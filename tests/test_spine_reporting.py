"""Chunk 19 §4 end-to-end: the node-12/13 reporting flow over a real MCP session.

Reuses the skeleton's `_open_driver`/`_run_spine` (never copies them, spec §4): run the full
walking skeleton, then continue the **same** driver through the champion/report tools. The driver
still enforces chunk 21's allow-list and the D32/D-d lineage lock on every call, so this exercises
the reporting-phase test-read permit (D-d) over the wire, not only in the unit guard tests.

The classification fixture renders a validation ROC curve and includes the plot; the regression
fixture runs the same flow without a plot (`compute_roc_curve` is binary only). The report's test
value is asserted to be the champion's **recorded** test score (D-c: the number comes from the
artifact), and every required D33 sentence and both goal questions must be present.
"""

from __future__ import annotations

import json
from typing import cast

import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.layout import ProjectPaths, RunPaths
from statlab_tools.settings import ToolSettings

from tests.test_skeleton import (
    SessionFactory,
    SpineResult,
    TaskConfig,
    _houses_contract,
    _only_ref,
    _open_driver,
    _run_spine,
    _subscribers_contract,
    houses,
    subscribers,
)


@pytest.fixture
def _patch_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the bootstrap so the live uvicorn-thread server scores quickly (same device-reach as
    the skeleton's own fixture, chunk 16 D-f). Set before the session opens."""
    import statlab_tools.tools.evaluation as evaluation

    monkeypatch.setattr(evaluation, "BOOTSTRAP_RESAMPLES", 200)


def _report_text(settings: ToolSettings, run: str) -> str:
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run)
    return (paths.report / "report.md").read_text(encoding="utf-8")


def _recorded_test_value(settings: ToolSettings, run: str, result: SpineResult, metric: str) -> str:
    """The champion's test metric value with its CI, formatted per D-e, read straight off the
    champion test score artifact -- the golden the report line must contain."""
    from statlab_tools.tools.reporting import format_value_ci

    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run)
    text = paths.artifact(ArtifactRef(result.champion_test_table), "table").read_text()
    payload = cast("dict[str, JsonValue]", json.loads(text))
    metrics = cast("dict[str, dict[str, JsonValue]]", payload["metrics"])
    entry = metrics[metric]
    return format_value_ci(entry["value"], entry["ci_low"], entry["ci_high"])


async def _run_reporting(result: SpineResult, cfg: TaskConfig) -> tuple[str, bool]:
    """Continue the spine's driver through the reporting flow (§4). Returns `(report path,
    had_plot)`. Classification renders a validation ROC curve + plot; regression skips it."""
    driver = result.driver
    plots: tuple[ArtifactRef, ...] = ()
    had_plot = False
    if cfg.is_classification:
        # the champion's VALID probabilities (not test-derived): a model_evaluation call.
        champ_valid_proba = _only_ref(
            await driver.call(
                "model_evaluation",
                "predict_proba",
                model=result.models[result.champion],
                frame=result.valid_frame,
            )
        )
        curve = _only_ref(
            await driver.call(
                "model_evaluation",
                "compute_roc_curve",
                probabilities=champ_valid_proba,
                truth=result.valid_frame,
                target=cfg.target,
            )
        )
        plot = _only_ref(
            await driver.call("model_evaluation", "plot_curve", curve=curve, title="champion ROC")
        )
        plots = (ArtifactRef(plot),)
        had_plot = True

    # --- champion_selection: select, render the comparison, assemble the report ----------------
    champion_table = _only_ref(
        await driver.call(
            "champion_selection",
            "select_champion",
            ranking=result.ranking_ref,
            candidate=result.champion,
            rationale="the top eligible candidate by the primary metric",
        )
    )
    validation = _only_ref(
        await driver.call(
            "champion_selection",
            "render_metrics_table",
            scores=dict(result.score_tables),
        )
    )
    section = {"heading": "Narrative", "body": "An LLM-authored summary of the run."}
    report = await driver.call(
        "champion_selection",
        "assemble_report",
        title="Modelling report",
        champion=champion_table,
        test_score=result.champion_test_table,
        validation_table=validation,
        sections=[section],
        coefficient_table=result.coefficient_ref,
        plots=list(plots),
        prior_test_scorings=0,
        n_waves=1,
    )
    return str(report.summary["path"]), had_plot


@pytest.mark.asyncio
async def test_spine_reporting_classification(
    mcp_session: SessionFactory,
    tool_settings: ToolSettings,
    _patch_bootstrap: None,
) -> None:
    """The classification reporting flow end to end, over one live session."""
    frame = subscribers()
    contract = _subscribers_contract()
    run = "r_repcls000000000"
    async with mcp_session() as session:
        driver, cfg = await _open_driver(session, tool_settings, run, frame, contract)
        result = await _run_spine(driver, cfg)
        path, had_plot = await _run_reporting(result, cfg)
    assert had_plot
    assert path == "report/report.md"
    md = _report_text(tool_settings, run)
    # D-c: the test value in the report is the champion's RECORDED test score.
    expected = _recorded_test_value(tool_settings, run, result, cfg.primary_metric)
    assert f"test {cfg.primary_metric} is {expected}" in md
    assert "selection score" in md
    assert "not distinguishable" in md  # the subscribers top-two CIs overlap (chunk 16 §B3)
    for goal in cfg.goals:
        assert str(goal["question"]) in md
    # the plot link resolves relative to report.md.
    paths = RunPaths(data_dir=tool_settings.data_dir, slug="proj", run_id=run)
    assert "![" in md and "](plots/" in md
    plots_dir = paths.report / "plots"
    assert list(plots_dir.glob("*.png")), "no plot copied into report/plots/"
    # exactly one test scoring, enforced by the driver's lineage lock.
    assert driver.test_scores == 1


@pytest.mark.asyncio
async def test_spine_reporting_regression(
    mcp_session: SessionFactory,
    tool_settings: ToolSettings,
    _patch_bootstrap: None,
) -> None:
    """The regression reporting flow end to end -- same flow, no plot."""
    frame = houses()
    contract = _houses_contract()
    run = "r_repreg000000000"
    async with mcp_session() as session:
        driver, cfg = await _open_driver(session, tool_settings, run, frame, contract)
        result = await _run_spine(driver, cfg)
        path, had_plot = await _run_reporting(result, cfg)
    assert not had_plot
    assert path == "report/report.md"
    md = _report_text(tool_settings, run)
    expected = _recorded_test_value(tool_settings, run, result, cfg.primary_metric)
    assert f"test {cfg.primary_metric} is {expected}" in md
    assert "selection score" in md
    for goal in cfg.goals:
        assert str(goal["question"]) in md
    assert "## Figures" not in md
    assert driver.test_scores == 1


# --- M7b gate (chunk 20 §4): the exported pipeline.py reproduces the champion's test metric -----

import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

# The inferential-analysis and comparison/ranking branches are NOT on the champion's test path:
# the backward slice from the champion test score reaches load_csv -> split -> preprocessing
# transformer fits (fit_imputer/fit_onehot_encoder/fit_scaler ARE needed) -> train_estimator ->
# predict -> score. The §4 "no fit_*" means the inferential model fits (fit_logit/fit_ols), not
# the preprocessing transformer fits that reproducing the champion genuinely requires.
_FORBIDDEN_KEPT: frozenset[str] = frozenset(
    {
        "compare_models",
        "rank_models",
        "coefficient_table",
        "assemble_inference_matrix",
        "fit_logit",
        "fit_ols",
        # profiling tools
        "profile_schema",
        "summarize_numeric",
        "summarize_categorical",
        "report_missingness",
        "summarize_target",
    }
)


def _recorded_test_point(
    settings: ToolSettings, run: str, result: SpineResult, metric: str
) -> float:
    """The champion's recorded test primary-metric point value (never a CI) -- the golden the
    replayed script must match within +/-0.005 (§14.1)."""
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run)
    text = paths.artifact(ArtifactRef(result.champion_test_table), "table").read_text()
    payload = cast("dict[str, JsonValue]", json.loads(text))
    metrics = cast("dict[str, dict[str, JsonValue]]", payload["metrics"])
    value = metrics[metric]["value"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return float(value)


def _run_pipeline_subprocess(pipeline_py: Path, csv: Path, cwd: Path) -> str:
    """Run `pipeline.py CSV` in a fresh process with a minimal env (PATH, HOME only) and a fresh
    empty cwd, so a script that only works because of the test process's env/cwd/imports fails
    here (§4). Returns the last stdout line, which must be the champion summary as JSON."""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    completed = subprocess.run(  # noqa: S603 -- sys.executable on a generated, compiled script
        [sys.executable, str(pipeline_py), str(csv)],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, (
        f"pipeline.py exited {completed.returncode}\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    # tool log lines go to stderr; the LAST stdout line is the champion summary JSON.
    lines = [ln for ln in completed.stdout.splitlines() if ln.strip()]
    assert lines, f"no stdout from pipeline.py; stderr:\n{completed.stderr}"
    return lines[-1]


async def _export_and_snapshot(result: SpineResult) -> tuple[dict[str, JsonValue], list[str]]:
    """Call `export_pipeline_script` and `snapshot_run` over MCP in `artifact_persistence`, from
    the same driver that ran the spine (its `log` is the tool call record, D-b). Returns the
    export summary and the kept tool names."""
    driver = result.driver
    log_payload = [call.model_dump(mode="json") for call in driver.log]
    export = await driver.call(
        "artifact_persistence",
        "export_pipeline_script",
        calls=log_payload,
        target=result.champion_test_table,
    )
    await driver.call("artifact_persistence", "snapshot_run", calls=log_payload)
    tools = [str(t) for t in cast("list[JsonValue]", export.summary["tools"])]
    return dict(export.summary), tools


async def _run_m7b(
    mcp_session: SessionFactory,
    settings: ToolSettings,
    run: str,
    frame: object,
    contract: object,
) -> None:
    """One fixture's M7b gate: run the spine + reporting, export the script, run it in a fresh
    process and assert it reproduces the champion's test metric within +/-0.005 (§14.1), then
    assert a one-byte CSV change makes it exit non-zero with the sha message."""
    from pandas import DataFrame
    from statlab_contracts.contract import DataContract

    assert isinstance(frame, DataFrame)
    assert isinstance(contract, DataContract)
    async with mcp_session() as session:
        driver, cfg = await _open_driver(session, settings, run, frame, contract)
        result = await _run_spine(driver, cfg)
        await _run_reporting(result, cfg)
        summary, kept = await _export_and_snapshot(result)

    # exactly one train_estimator; none of the comparison/ranking/inference tools.
    assert kept.count("train_estimator") == 1, f"kept train_estimator count != 1: {kept!r}"
    assert not (set(kept) & _FORBIDDEN_KEPT), (
        f"forbidden tool kept: {sorted(set(kept) & _FORBIDDEN_KEPT)!r}"
    )

    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run)
    pipeline_py = paths.pipeline_py
    assert pipeline_py.exists()
    project = ProjectPaths(data_dir=settings.data_dir, slug="proj")
    csv = project.dataset_csv(str(summary["dataset_sha256"]))

    # run the script in a fresh, empty cwd with a minimal env.
    import tempfile

    cwd = Path(tempfile.mkdtemp(prefix="m7b_cwd_"))
    last = _run_pipeline_subprocess(pipeline_py, csv, cwd)
    printed = cast("dict[str, JsonValue]", json.loads(last))
    metrics = cast("dict[str, dict[str, JsonValue]]", printed["metrics"])
    reproduced = metrics[cfg.primary_metric]["value"]
    assert isinstance(reproduced, (int, float)) and not isinstance(reproduced, bool)
    recorded = _recorded_test_point(settings, run, result, cfg.primary_metric)
    diff = abs(float(reproduced) - recorded)
    assert diff <= 0.005, (
        f"reproduced {cfg.primary_metric} {reproduced} differs from recorded {recorded} "
        f"by {diff} > 0.005"
    )

    # a one-byte CSV change makes the script exit non-zero with the sha message.
    tampered = cwd / "tampered.csv"
    data = bytearray(csv.read_bytes())
    data[0] = data[0] ^ 0x01
    tampered.write_bytes(bytes(data))
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
    bad = subprocess.run(  # noqa: S603 -- sys.executable on a generated, compiled script
        [sys.executable, str(pipeline_py), str(tampered)],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert bad.returncode != 0
    assert "sha256" in bad.stderr.lower()


@pytest.mark.asyncio
async def test_m7b_pipeline_reproduces_classification(
    mcp_session: SessionFactory,
    tool_settings: ToolSettings,
    _patch_bootstrap: None,
) -> None:
    """M7b, classification: pipeline.py reproduces the champion's test roc_auc within +/-0.005
    (§14.1), run in a fresh process on the CSV."""
    await _run_m7b(
        mcp_session, tool_settings, "r_m7bcls000000000", subscribers(), _subscribers_contract()
    )


@pytest.mark.asyncio
async def test_m7b_pipeline_reproduces_regression(
    mcp_session: SessionFactory,
    tool_settings: ToolSettings,
    _patch_bootstrap: None,
) -> None:
    """M7b, regression: pipeline.py reproduces the champion's test rmse within +/-0.005 (§14.1),
    run in a fresh process on the CSV."""
    await _run_m7b(mcp_session, tool_settings, "r_m7breg000000000", houses(), _houses_contract())
