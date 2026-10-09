"""Chunk 19 §4 (reporting): `render_metrics_table` and `assemble_report`.

The report's numbers must be the artifacts' numbers (D-c). Every golden assertion here reads the
expected value and CI **from the input score/coefficient artifacts** and asserts the rendered
string contains them formatted per D-e, and each conditional D32c/D33 sentence is tested both ways
(present iff its condition holds). These tests call the tool bodies directly (not over MCP), the
same pattern `test_tools_evaluation.py` uses.
"""

from __future__ import annotations

from typing import cast, get_args

import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths
from statlab_contracts.reporting import ReportSection
from statlab_tools.artifacts import load_json, save_json, save_plot, save_text
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.reporting import (
    assemble_report,
    format_number,
    format_value_ci,
    heading_line,
    render_metrics_table,
)

# --- fixtures: artifacts shaped exactly as the producing tools write them --------------------


def _metric_entry(value: float, lo: float, hi: float) -> dict[str, JsonValue]:
    return {"value": value, "ci_low": lo, "ci_high": hi}


def _score_table(
    run_paths: RunPaths,
    *,
    task: str,
    metrics: dict[str, dict[str, JsonValue]],
    fingerprint: str = "fp",
) -> ArtifactRef:
    """A score_* table artifact (the shape `score_classification`/`score_regression` write)."""
    payload: dict[str, JsonValue] = {
        "task": task,
        "metrics": cast("dict[str, JsonValue]", metrics),
        "n_rows": 100,
        "truth_fingerprint": fingerprint,
    }
    ref = new_artifact_ref()
    save_json(run_paths.artifact(ref, "table"), payload, ref=ref, kind="table", label="scores")
    return ref


def _champion_table(
    run_paths: RunPaths,
    *,
    candidate: str = "logit",
    metric: str = "roc_auc",
    value: float = 0.82,
    ci_low: float = 0.75,
    ci_high: float = 0.89,
    overrides_top: bool = False,
    distinguishable: bool | None = False,
    n_candidates: int = 3,
    rationale: str = "best validation score",
) -> ArtifactRef:
    """A select_champion table artifact."""
    payload: dict[str, JsonValue] = {
        "candidate": candidate,
        "estimator": candidate,
        "rank": 1,
        "metric": metric,
        "value": value,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "top_eligible": "logit",
        "overrides_top": overrides_top,
        "top_two_eligible_distinguishable": distinguishable,
        "selection_note": "a selection score",
        "n_candidates": n_candidates,
        "rationale": rationale,
    }
    ref = new_artifact_ref()
    save_json(run_paths.artifact(ref, "table"), payload, ref=ref, kind="table", label="champion")
    return ref


def _validation_table(
    run_paths: RunPaths, markdown: str = "| candidate |\n| --- |\n"
) -> ArtifactRef:
    ref = new_artifact_ref()
    save_text(run_paths.artifact(ref, "report"), markdown, ref=ref, label="metrics_table")
    return ref


def _coefficient_table(run_paths: RunPaths, goals: list[dict[str, JsonValue]]) -> ArtifactRef:
    payload: dict[str, JsonValue] = {
        "model": "logit",
        "terms": [],
        "goals": cast("list[JsonValue]", goals),
        "n_terms": 3,
        "n_confirmatory": 2,
    }
    ref = new_artifact_ref()
    save_json(
        run_paths.artifact(ref, "table"), payload, ref=ref, kind="table", label="coefficient_table"
    )
    return ref


def _tested_goal(
    question: str,
    terms: list[str],
    df: int,
    p_raw: float,
    p_holm: float,
    alpha: float,
    reject: bool,
) -> dict[str, JsonValue]:
    return {
        "question": question,
        "predictors": [],
        "terms": cast("list[JsonValue]", terms),
        "stat": 12.3,
        "df": df,
        "p_raw": p_raw,
        "p_holm": p_holm,
        "alpha": alpha,
        "reject": reject,
        "status": "tested",
    }


def _untestable_goal(question: str, alpha: float = 0.05) -> dict[str, JsonValue]:
    return {
        "question": question,
        "predictors": [],
        "terms": [],
        "stat": None,
        "df": None,
        "p_raw": None,
        "p_holm": None,
        "alpha": alpha,
        "reject": False,
        "status": "untestable",
    }


def _plot(run_paths: RunPaths) -> ArtifactRef:
    from matplotlib.figure import Figure

    fig = Figure()
    fig.add_subplot().plot([0, 1], [0, 1])
    ref = new_artifact_ref()
    save_plot(run_paths.artifact(ref, "plot"), fig, ref=ref, label="roc")
    return ref


def _read(run_paths: RunPaths) -> str:
    return (run_paths.report / "report.md").read_text(encoding="utf-8")


def _d(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


# --- D-e formatting -------------------------------------------------------------------------


def test_format_number_4g_and_null() -> None:
    assert format_number(0.123456) == "0.1235"
    assert format_number(1234.5) == "1234"
    assert format_number(None) == "\u2014"


def test_format_value_ci() -> None:
    assert format_value_ci(0.82, 0.75, 0.89) == "0.82 [0.75, 0.89]"
    assert format_value_ci(0.82, None, 0.89) == "0.82"


def test_format_number_non_numeric_branches() -> None:
    # a bool is not rendered as a figure (would read as 0/1); a bare string passes through.
    assert format_number(True) == "True"
    assert format_number("n/a") == "n/a"


def test_render_absent_metric_renders_em_dash(run_paths: RunPaths) -> None:
    # a candidate whose table lacks a requested metric entirely renders an em dash for that cell.
    a = _score_table(
        run_paths, task="classification", metrics={"accuracy": _metric_entry(0.9, 0.85, 0.95)}
    )
    res = render_metrics_table(run_paths, {"a": a}, metrics=("accuracy", "roc_auc"))
    md = str(res.summary["markdown"])
    assert "\u2014" in md  # the missing roc_auc cell


def test_report_test_score_not_a_table_is_tool_param(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths, metric="roc_auc")
    bad = new_artifact_ref()
    save_json(run_paths.artifact(bad, "table"), {"not": "a score"}, ref=bad, kind="table")
    valid = _validation_table(run_paths)
    with pytest.raises(ToolParamError, match="not a score table"):
        assemble_report(
            run_paths,
            title="t",
            champion=champ,
            test_score=bad,
            validation_table=valid,
            prior_test_scorings=0,
            n_waves=1,
        )


def test_report_coefficient_table_without_goals_is_tool_param(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    bad = new_artifact_ref()
    save_json(run_paths.artifact(bad, "table"), {"terms": []}, ref=bad, kind="table")
    with pytest.raises(ToolParamError, match="no goals"):
        assemble_report(
            run_paths,
            title="t",
            champion=champ,
            test_score=test,
            validation_table=valid,
            coefficient_table=bad,
            prior_test_scorings=0,
            n_waves=1,
        )


def test_heading_line_strips_hash_and_newlines() -> None:
    assert heading_line("# Big\nheading") == "Big heading"
    assert heading_line("## Title") == "Title"


# --- render_metrics_table -------------------------------------------------------------------


def test_render_known_markdown_two_candidates(run_paths: RunPaths) -> None:
    a = _score_table(
        run_paths,
        task="classification",
        metrics={"roc_auc": _metric_entry(0.9, 0.85, 0.95)},
    )
    b = _score_table(
        run_paths,
        task="classification",
        metrics={"roc_auc": _metric_entry(0.8, 0.75, 0.85)},
    )
    res = render_metrics_table(run_paths, {"a": a, "b": b}, metrics=("roc_auc",))
    md = str(res.summary["markdown"])
    assert md == (
        "| candidate | roc_auc |\n| --- | --- |\n| a | 0.9 [0.85, 0.95] |\n| b | 0.8 [0.75, 0.85] |"
    )
    assert res.summary["n_rows"] == 2
    assert res.artifacts[0].kind == "report"
    # the written artifact holds the same markdown.
    assert (run_paths.artifact(res.artifacts[0].ref, "report")).read_text() == md


def test_render_escapes_pipe_in_label(run_paths: RunPaths) -> None:
    a = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.9, 0.85, 0.95)}
    )
    res = render_metrics_table(run_paths, {"a|b": a}, metrics=("roc_auc",))
    assert "a\\|b" in str(res.summary["markdown"])


def test_render_null_renders_em_dash(run_paths: RunPaths) -> None:
    # a regression table missing mape (null entry) renders an em dash.
    a = _score_table(
        run_paths,
        task="regression",
        metrics={
            "rmse": _metric_entry(1.0, 0.9, 1.1),
            "mape": {"value": None, "ci_low": None, "ci_high": None},
        },
    )
    res = render_metrics_table(run_paths, {"a": a}, metrics=("rmse", "mape"))
    md = str(res.summary["markdown"])
    assert "\u2014" in md


def test_render_differing_fingerprints_is_tool_param(run_paths: RunPaths) -> None:
    a = _score_table(
        run_paths,
        task="classification",
        metrics={"roc_auc": _metric_entry(0.9, 0.8, 0.95)},
        fingerprint="fp1",
    )
    b = _score_table(
        run_paths,
        task="classification",
        metrics={"roc_auc": _metric_entry(0.8, 0.7, 0.85)},
        fingerprint="fp2",
    )
    with pytest.raises(ToolParamError, match="truth fingerprints"):
        render_metrics_table(run_paths, {"a": a, "b": b}, metrics=("roc_auc",))


def test_render_mixed_tasks_is_tool_param(run_paths: RunPaths) -> None:
    a = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.9, 0.8, 0.95)}
    )
    b = _score_table(run_paths, task="regression", metrics={"rmse": _metric_entry(1.0, 0.9, 1.1)})
    with pytest.raises(ToolParamError, match="mix tasks"):
        render_metrics_table(run_paths, {"a": a, "b": b})


def test_render_regression_metric_on_classification_is_tool_param(run_paths: RunPaths) -> None:
    a = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.9, 0.8, 0.95)}
    )
    with pytest.raises(ToolParamError, match="not classification metrics"):
        render_metrics_table(run_paths, {"a": a}, metrics=("rmse",))


def test_render_empty_metrics_lists_all_task_metrics_in_order(run_paths: RunPaths) -> None:
    from statlab_contracts.vocabulary import RegressionMetric

    metrics = {m: _metric_entry(1.0, 0.9, 1.1) for m in get_args(RegressionMetric)}
    a = _score_table(run_paths, task="regression", metrics=metrics)
    res = render_metrics_table(run_paths, {"a": a})
    assert list(res.summary["metrics"]) == list(get_args(RegressionMetric))  # type: ignore[arg-type]


# --- assemble_report: golden Result assertions read from the artifacts ----------------------


def test_report_test_line_matches_the_score_table(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths, metric="roc_auc", value=0.82, ci_low=0.75, ci_high=0.89)
    test_payload = _d(load_json(run_paths.artifact(champ, "table")))  # noqa: F841 -- shape check
    test = _score_table(
        run_paths,
        task="classification",
        metrics={"roc_auc": _metric_entry(0.731, 0.66, 0.80)},
    )
    valid = _validation_table(run_paths)
    assemble_report(
        run_paths,
        title="Final report",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    # golden: the test value and CI exactly as in the score table, formatted per D-e.
    entry = _d(_d(_d(load_json(run_paths.artifact(test, "table")))["metrics"])["roc_auc"])
    expected = format_value_ci(entry["value"], entry["ci_low"], entry["ci_high"])
    assert expected == "0.731 [0.66, 0.8]"
    assert f"test roc_auc is {expected}" in md
    assert "only unbiased performance estimate" in md
    # validation line names the selection score, N and W.
    champ_entry = _d(load_json(run_paths.artifact(champ, "table")))
    valid_expected = format_value_ci(
        champ_entry["value"], champ_entry["ci_low"], champ_entry["ci_high"]
    )
    assert f"validation roc_auc is {valid_expected}" in md
    assert "selection score over 3 candidate(s) and 1 wave(s)" in md


def test_report_not_distinguishable_sentence_both_ways(run_paths: RunPaths) -> None:
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    sentence = "not distinguishable on this validation set"

    champ_false = _champion_table(run_paths, distinguishable=False)
    assemble_report(
        run_paths,
        title="t",
        champion=champ_false,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    assert sentence in _read(run_paths)

    champ_true = _champion_table(run_paths, distinguishable=True)
    assemble_report(
        run_paths,
        title="t",
        champion=champ_true,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    assert sentence not in _read(run_paths)


def test_report_exposure_sentence_both_ways(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)

    assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=2,
        n_waves=1,
    )
    md = _read(run_paths)
    assert "scored 2 time(s) in prior runs" in md

    assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    assert "prior runs" not in _read(run_paths)


def test_report_override_sentence_both_ways(run_paths: RunPaths) -> None:
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)

    champ_override = _champion_table(
        run_paths, overrides_top=True, rationale="simpler model preferred"
    )
    assemble_report(
        run_paths,
        title="t",
        champion=champ_override,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    assert "chosen over the top-ranked eligible one" in md
    assert "simpler model preferred" in md

    champ_top = _champion_table(run_paths, overrides_top=False)
    assemble_report(
        run_paths,
        title="t",
        champion=champ_top,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    assert "chosen over the top-ranked eligible one" not in _read(run_paths)


def test_report_inference_table_holm_and_decisions(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    goals = [
        _tested_goal(
            "Does plan affect churn?", ["plan__a", "plan__b"], 2, 0.001, 0.002, 0.05, True
        ),
        _tested_goal("Does region affect churn?", ["region__x"], 1, 0.40, 0.50, 0.05, False),
        _untestable_goal("Does untested affect churn?"),
    ]
    coef = _coefficient_table(run_paths, goals)
    assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        coefficient_table=coef,
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    assert "## Inference" in md
    assert "p (Holm)" in md
    assert "reject H0" in md
    assert "not rejected" in md
    assert "untestable" in md
    # golden: the Holm p of the first goal, formatted per D-e.
    assert format_number(0.002) in md
    assert "limited to the pre-registered goals" in md


def test_report_section_order_and_heading_sanitised(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    coef = _coefficient_table(run_paths, [_tested_goal("Q?", ["t"], 1, 0.01, 0.01, 0.05, True)])
    section = ReportSection(heading="# My\nsection", body="narrative body")
    plot = _plot(run_paths)
    assemble_report(
        run_paths,
        title="Title",
        champion=champ,
        test_score=test,
        validation_table=valid,
        sections=(section,),
        coefficient_table=coef,
        plots=(plot,),
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    order = [
        md.index("# Title"),
        md.index("## Result"),
        md.index("## Validation comparison"),
        md.index("## Inference"),
        md.index("## My section"),
        md.index("## Figures"),
    ]
    assert order == sorted(order), f"sections out of order: {order!r}"
    # heading sanitised to a single line with no leading '#'.
    assert "## My section" in md
    assert "narrative body" in md


def test_report_plots_copied_and_linked(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    plot = _plot(run_paths)
    plot_without_chart = _plot(run_paths)
    charts = run_paths.artifacts / "charts"
    charts.mkdir(exist_ok=True)
    (charts / f"{plot}.json").write_bytes(b"{}")
    res = assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        plots=(plot, plot_without_chart),
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    assert f"![{plot}](plots/{plot}.png)" in md
    # the link resolves relative to report.md.
    copied = run_paths.report / "plots" / f"{plot}.png"
    assert copied.exists()
    assert (run_paths.report / "plots" / f"{plot}.chart.json").read_bytes() == b"{}"
    assert not (run_paths.report / "plots" / f"{plot_without_chart}.chart.json").exists()
    assert res.summary["n_plots"] == 2


def test_report_no_figures_section_without_plots(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    assert "## Figures" not in _read(run_paths)


def test_report_no_inference_without_coefficient_table(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    assert "## Inference" not in _read(run_paths)


def test_report_wrong_task_test_score_is_tool_param(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths, metric="roc_auc")
    test = _score_table(
        run_paths, task="regression", metrics={"rmse": _metric_entry(1.0, 0.9, 1.1)}
    )
    valid = _validation_table(run_paths)
    with pytest.raises(ToolParamError, match="is classification"):
        assemble_report(
            run_paths,
            title="t",
            champion=champ,
            test_score=test,
            validation_table=valid,
            prior_test_scorings=0,
            n_waves=1,
        )


def test_report_test_score_missing_metric_is_tool_param(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths, metric="roc_auc")
    # a classification table but without roc_auc.
    test = _score_table(
        run_paths, task="classification", metrics={"accuracy": _metric_entry(0.9, 0.8, 0.95)}
    )
    valid = _validation_table(run_paths)
    with pytest.raises(ToolParamError, match="no metric"):
        assemble_report(
            run_paths,
            title="t",
            champion=champ,
            test_score=test,
            validation_table=valid,
            prior_test_scorings=0,
            n_waves=1,
        )


def test_report_missing_plot_is_tool_param(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    with pytest.raises(ToolParamError, match="not an existing plot"):
        assemble_report(
            run_paths,
            title="t",
            champion=champ,
            test_score=test,
            validation_table=valid,
            plots=(ArtifactRef("a_deadbeefdeadbeef"),),
            prior_test_scorings=0,
            n_waves=1,
        )


def test_report_champion_not_select_table_is_tool_param(run_paths: RunPaths) -> None:
    ref = new_artifact_ref()
    save_json(run_paths.artifact(ref, "table"), {"not": "champion"}, ref=ref, kind="table")
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    with pytest.raises(ToolParamError, match="not a select_champion table"):
        assemble_report(
            run_paths,
            title="t",
            champion=ref,
            test_score=test,
            validation_table=valid,
            prior_test_scorings=0,
            n_waves=1,
        )


def test_report_rerun_overwrites(run_paths: RunPaths) -> None:
    champ = _champion_table(run_paths)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.73, 0.6, 0.8)}
    )
    valid = _validation_table(run_paths)
    r1 = assemble_report(
        run_paths,
        title="first",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    r2 = assemble_report(
        run_paths,
        title="second",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    assert "# second" in md
    assert "# first" not in md
    assert r1.summary["sha256"] != r2.summary["sha256"]


# --- DoD mutations as direct checks ----------------------------------------------------------


def test_mutation_report_takes_test_value_from_validation_row(run_paths: RunPaths) -> None:
    """DoD mutation: the report takes the test value from the validation (champion) row instead of
    the test score table. The two differ, so the golden test-line assertion catches it."""
    champ = _champion_table(run_paths, value=0.82, ci_low=0.75, ci_high=0.89)
    test = _score_table(
        run_paths, task="classification", metrics={"roc_auc": _metric_entry(0.731, 0.66, 0.80)}
    )
    valid = _validation_table(run_paths)
    assemble_report(
        run_paths,
        title="t",
        champion=champ,
        test_score=test,
        validation_table=valid,
        prior_test_scorings=0,
        n_waves=1,
    )
    md = _read(run_paths)
    # the test figure (0.731) must appear, NOT the champion/validation figure (0.82).
    assert "test roc_auc is 0.731 [0.66, 0.8]" in md
    assert "test roc_auc is 0.82" not in md
