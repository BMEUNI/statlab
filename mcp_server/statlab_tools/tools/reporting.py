"""The two reporting tools (chunk 19 §3.2-3.3, D-c/D-e): `render_metrics_table` and
`assemble_report`.

The report's **numbers come from artifacts, never from LLM text** (D-c): `assemble_report` reads
the champion pointer, the test score table, the validation markdown and the coefficient table off
disk and writes the Result, Validation comparison, Inference and Figures sections itself, including
every D32c/D33/D35 statement. The LLM contributes only the `sections` between Inference and
Figures, verbatim.

These are JSON-and-text tools: no compute libraries beyond what `artifacts.py` already uses. The
score-table loader is reused from `evaluation` (`load_score_table`) rather than re-parsing a score
table a second way; plots are copied **by reference only** (`shutil.copyfile` from the resolved
artifact path), never from a path an argument carries. The output markdown must be rendered by the
frontend with raw HTML disabled (D-e), because the `sections` bodies are LLM text.
"""

from __future__ import annotations

import hashlib
import shutil
from typing import Annotated, Final, cast, get_args

from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.reporting import ReportSection
from statlab_contracts.vocabulary import (
    METRICS_FOR_TASK,
    ClassificationMetric,
    Metric,
    RegressionMetric,
    Task,
)

from statlab_tools.artifacts import load_json, save_text
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolParamError, tool
from statlab_tools.tools.evaluation import ScoreTable, load_score_table

_REPORTING_PHASES: Final = ("champion_selection",)
_EM_DASH: Final = "\u2014"

# Metric vocabulary order (§3.2 `metrics=()`): the Literal arg order, not a frozenset's arbitrary
# iteration order. `get_args` via `__args__` is the single source so a new metric cannot drift.
_METRIC_ORDER: Final[dict[Task, tuple[Metric, ...]]] = {
    "classification": cast("tuple[Metric, ...]", get_args(ClassificationMetric)),
    "regression": cast("tuple[Metric, ...]", get_args(RegressionMetric)),
}

_NO_METRICS: tuple[Metric, ...] = ()
"""Shared empty default for `metrics`; never mutated (ruff B006)."""
_NO_SECTIONS: tuple[ReportSection, ...] = ()
"""Shared empty default for `sections`; never mutated."""
_NO_PLOTS: tuple[ArtifactRef, ...] = ()
"""Shared empty default for `plots`; never mutated."""


# --- D-e formatting --------------------------------------------------------------------------


def format_number(value: JsonValue) -> str:
    """A scalar formatted per D-e: `f"{v:.4g}"` for a real number, an em dash for `null`.

    `bool` is handled before the numeric branch -- a boolean is not a figure, and `f"{True:.4g}"`
    would print `1`, silently turning a flag into a measurement. Score and coefficient values are
    never bool, so a bool here is a caller slip, surfaced as `str()` rather than a fake number."""
    if value is None:
        return _EM_DASH
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:.4g}"
    return str(value)


def format_value_ci(value: JsonValue, ci_low: JsonValue, ci_high: JsonValue) -> str:
    """A value with its CI rendered `v [lo, hi]` (D-e); just `v` when either bound is null."""
    v = format_number(value)
    if ci_low is None or ci_high is None:
        return v
    return f"{v} [{format_number(ci_low)}, {format_number(ci_high)}]"


def cell(text: str) -> str:
    """A markdown table cell per D-e: newlines folded into one space, then every `|` escaped so a
    stray pipe in a label cannot split the row."""
    folded = " ".join(text.splitlines())
    return folded.replace("|", "\\|")


def heading_line(text: str) -> str:
    """A single-line heading (D-e): newlines dropped and leading `#` stripped, so an LLM-supplied
    heading cannot inject extra markdown structure or a second line."""
    first = " ".join(text.splitlines()).strip()
    return first.lstrip("#").strip()


def _table(header: list[str], rows: list[list[str]]) -> str:
    """A GitHub-flavoured markdown table from already-escaped cells."""
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


# --- 3.2 render_metrics_table ----------------------------------------------------------------


def _tasks_agree(tables: dict[str, ScoreTable], field: str) -> Task:
    """The single task shared by every score table, or `tool_param` on a mix."""
    tasks = {t.task for t in tables.values()}
    if len(tasks) > 1:
        raise ToolParamError(
            f"{field} mix tasks {sorted(tasks)!r}; a metrics table compares one task",
            field_errors={field: f"mixed tasks: {sorted(tasks)!r}"},
        )
    return cast(Task, next(iter(tasks)))


def _fingerprints_agree(tables: dict[str, ScoreTable], field: str) -> None:
    """Every score table must share a `truth_fingerprint` (chunk 14 D-d): rows scored on
    different data are not comparable."""
    fingerprints = {label: t.fingerprint for label, t in tables.items()}
    if len(set(fingerprints.values())) > 1:
        raise ToolParamError(
            f"{field} were computed on different rows (truth fingerprints differ): "
            f"{fingerprints!r}",
            field_errors={field: f"differing truth fingerprints: {fingerprints!r}"},
        )


def _resolve_metrics(task: Task, metrics: tuple[Metric, ...]) -> list[Metric]:
    """The metrics to show (§3.2): every metric of `task` in vocabulary order when `metrics=()`,
    else the given metrics, each of which must belong to the task (`tool_param`)."""
    if not metrics:
        return list(_METRIC_ORDER[task])
    allowed = METRICS_FOR_TASK[task]
    bad = [m for m in metrics if m not in allowed]
    if bad:
        raise ToolParamError(
            f"metrics {bad!r} are not {task} metrics",
            field_errors={"metrics": f"not {task} metrics: {bad!r}"},
        )
    return list(metrics)


def render_metrics_markdown(tables: dict[str, ScoreTable], columns: list[Metric]) -> str:
    """The markdown comparison table: one row per candidate in insertion order, one column per
    metric, each cell the value with its CI (D-e). A metric absent from a table renders an em
    dash."""
    header = ["candidate", *list(columns)]
    rows: list[list[str]] = []
    for label, table in tables.items():
        row = [cell(label)]
        for metric in columns:
            entry = table.metrics.get(metric)
            if entry is None:
                row.append(cell(format_number(None)))
            else:
                row.append(cell(format_value_ci(entry["value"], entry["ci_low"], entry["ci_high"])))
        rows.append(row)
    return _table(header, rows)


@tool(phase="reporting", allow_in=_REPORTING_PHASES)
def render_metrics_table(
    paths: RunPaths,
    scores: Annotated[
        dict[str, ArtifactRef],
        Field(min_length=1, description="Candidate label -> its score_* table artifact."),
    ],
    metrics: Annotated[
        tuple[Metric, ...],
        Field(description="Metrics to show; empty means every metric of the task, in order."),
    ] = _NO_METRICS,
) -> ToolSuccess:
    """Render a markdown comparison table of candidate scores (§3.2, D-e).

    Each `scores` value is a `score_*` table. Mixed tasks, and differing `truth_fingerprint`s
    (candidates scored on different data, chunk 14 D-d), are each `tool_param`. `metrics=()` shows
    every metric of the task in vocabulary order; a metric not of the task is `tool_param`. Rows
    are the candidates in the given order, columns are `| candidate | m1 | m2 | ... |`, each cell
    the value with its CI formatted per D-e. Writes a `report` artifact holding the table only; the
    summary carries `markdown`, `metrics` and `n_rows`.
    """
    tables = {label: load_score_table(paths, ref, label) for label, ref in scores.items()}
    task = _tasks_agree(tables, "scores")
    _fingerprints_agree(tables, "scores")
    columns = _resolve_metrics(task, metrics)
    markdown = render_metrics_markdown(tables, columns)
    ref = new_artifact_ref()
    meta = save_text(paths.artifact(ref, "report"), markdown, ref=ref, label="metrics_table")
    summary: dict[str, JsonValue] = {
        "markdown": markdown,
        "metrics": list(columns),
        "n_rows": len(tables),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


# --- 3.3 assemble_report ---------------------------------------------------------------------


def _load_champion(paths: RunPaths, champion: ArtifactRef) -> dict[str, JsonValue]:
    """A `select_champion` table, or `tool_param`. It has `candidate`, `metric` and
    `overrides_top` -- the shape only `select_champion` writes."""
    loaded = load_json(paths.artifact(champion, "table"))
    if (
        not isinstance(loaded, dict)
        or "candidate" not in loaded
        or "overrides_top" not in loaded
        or "metric" not in loaded
    ):
        raise ToolParamError(
            "champion is not a select_champion table",
            field_errors={"champion": "not a select_champion table"},
        )
    return loaded


def _load_test_score(
    paths: RunPaths, test_score: ArtifactRef, champion: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """The champion's test score table, or `tool_param`. It must be a score table of the
    champion's task holding the champion's metric (so the Result line reads a real figure)."""
    loaded = load_json(paths.artifact(test_score, "table"))
    if not isinstance(loaded, dict) or "metrics" not in loaded or "task" not in loaded:
        raise ToolParamError(
            "test_score is not a score table",
            field_errors={"test_score": "not a score_* table"},
        )
    metric = str(champion["metric"])
    task = loaded["task"]
    expected_task = _task_of_metric(metric)
    if task != expected_task:
        raise ToolParamError(
            f"test_score is a {task!r} table but the champion metric {metric!r} is {expected_task}",
            field_errors={"test_score": f"wrong task for metric {metric!r}: {task!r}"},
        )
    metrics = cast("dict[str, JsonValue]", loaded["metrics"])
    if metric not in metrics:
        raise ToolParamError(
            f"test_score has no metric {metric!r}",
            field_errors={"test_score": f"missing the champion metric {metric!r}"},
        )
    return loaded


def _task_of_metric(metric: str) -> Task:
    """The task a metric belongs to, read from the closed vocabularies."""
    if metric in METRICS_FOR_TASK["classification"]:
        return "classification"
    return "regression"


def _load_validation_markdown(paths: RunPaths, validation_table: ArtifactRef) -> str:
    """The `report` artifact `render_metrics_table` wrote; its text is spliced in verbatim. A
    missing file is a `transient` artifact error, not a `tool_param`."""
    return paths.artifact(validation_table, "report").read_text(encoding="utf-8")


def _load_coefficient_table(
    paths: RunPaths, coefficient_table: ArtifactRef
) -> dict[str, JsonValue]:
    """A `coefficient_table` artifact (it has `goals`), or `tool_param`."""
    loaded = load_json(paths.artifact(coefficient_table, "table"))
    if not isinstance(loaded, dict) or "goals" not in loaded:
        raise ToolParamError(
            "coefficient_table is not a coefficient table (no goals)",
            field_errors={"coefficient_table": "not a coefficient_table artifact"},
        )
    return loaded


def _result_section(
    champion: dict[str, JsonValue],
    test_score: dict[str, JsonValue],
    *,
    prior_test_scorings: int,
    n_waves: int,
) -> str:
    """The `## Result` section (D-c). The test line is always present; the other sentences each
    appear only when their condition holds. Every number comes from the artifacts, formatted per
    D-e -- never from LLM text."""
    metric = str(champion["metric"])
    candidate = str(champion["candidate"])
    metrics = cast("dict[str, dict[str, JsonValue]]", test_score["metrics"])
    test_entry = metrics[metric]
    test_str = format_value_ci(test_entry["value"], test_entry["ci_low"], test_entry["ci_high"])
    valid_str = format_value_ci(champion["value"], champion["ci_low"], champion["ci_high"])
    n = int(cast(int, champion["n_candidates"]))

    lines = [
        "## Result",
        "",
        f"The champion is **{candidate}**.",
        (f"Its test {metric} is {test_str}, the only unbiased performance estimate."),
        (
            f"Its validation {metric} is {valid_str}, a selection score over {n} "
            f"candidate(s) and {n_waves} wave(s): optimistic by construction (D33a)."
        ),
    ]
    if champion["top_two_eligible_distinguishable"] is False:
        lines.append(
            "The top two eligible candidates are not distinguishable on this validation set "
            "(their confidence intervals overlap, D33b)."
        )
    if prior_test_scorings > 0:
        lines.append(
            f"The test set has been scored {prior_test_scorings} time(s) in prior runs; "
            f"this cross-run exposure further inflates the estimate (D32c)."
        )
    if champion["overrides_top"] is True:
        rationale = str(champion["rationale"])
        lines.append(
            f"This candidate was chosen over the top-ranked eligible one; rationale: {rationale}"
        )
    return "\n".join(lines)


_DECISION: Final[dict[str, str]] = {
    "reject": "reject H0",
    "not_rejected": "not rejected",
    "untestable": "untestable",
}


def _inference_section(coefficient_table: dict[str, JsonValue]) -> str:
    """The `## Inference` section (§3.3): one row per pre-registered goal, then the D35 note that
    confirmatory claims are limited to those goals."""
    goals = cast("list[dict[str, JsonValue]]", coefficient_table["goals"])
    # the alpha column header is spec §3.3's exact wording; the Greek letter is intentional.
    header = ["question", "terms (df)", "statistic", "p", "p (Holm)", "α", "decision"]  # noqa: RUF001
    rows: list[list[str]] = []
    for goal in goals:
        terms = cast("list[JsonValue]", goal["terms"])
        term_text = ", ".join(str(t) for t in terms) if terms else _EM_DASH
        df = goal["df"]
        terms_df = f"{term_text} ({format_number(df)})"
        rows.append(
            [
                cell(str(goal["question"])),
                cell(terms_df),
                cell(format_number(goal["stat"])),
                cell(format_number(goal["p_raw"])),
                cell(format_number(goal["p_holm"])),
                cell(format_number(goal["alpha"])),
                cell(_decision(goal)),
            ]
        )
    table = _table(header, rows)
    note = (
        "Confirmatory claims are limited to the pre-registered goals above; every other term is "
        "exploratory (D35)."
    )
    return f"## Inference\n\n{table}\n\n{note}"


def _decision(goal: dict[str, JsonValue]) -> str:
    """The decision cell for a goal row: `untestable` when the goal has no surviving terms, else
    `reject H0` / `not rejected` from its `reject` flag (§3.3)."""
    if goal["status"] == "untestable":
        return _DECISION["untestable"]
    return _DECISION["reject"] if goal["reject"] is True else _DECISION["not_rejected"]


def _check_plots(paths: RunPaths, plots: tuple[ArtifactRef, ...]) -> None:
    """Every plot must be an existing `plot` artifact, else `tool_param` (§3.3)."""
    for plot in plots:
        if not paths.artifact(plot, "plot").exists():
            raise ToolParamError(
                f"plot {plot!r} is not an existing plot artifact",
                field_errors={"plots": f"missing plot artifact: {plot!r}"},
            )


def _copy_plots(paths: RunPaths, plots: tuple[ArtifactRef, ...]) -> str:
    """Copy each plot to `report/plots/<ref>.png` **by reference** (never a path an argument
    carries) and return the `## Figures` section. The link is relative to `report.md`."""
    plots_dir = paths.report / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    lines = ["## Figures", ""]
    for plot in plots:
        source = paths.artifact(plot, "plot")
        shutil.copyfile(source, plots_dir / f"{plot}.png")
        sidecar = paths.artifacts / "charts" / f"{plot}.json"
        if sidecar.is_file():
            shutil.copyfile(sidecar, plots_dir / f"{plot}.chart.json")
        lines.append(f"![{plot}](plots/{plot}.png)")
    return "\n".join(lines)


@tool(phase="reporting", allow_in=_REPORTING_PHASES)
def assemble_report(
    paths: RunPaths,
    title: Annotated[str, Field(min_length=1, max_length=200, description="The report title.")],
    champion: Annotated[ArtifactRef, Field(description="The select_champion table artifact.")],
    test_score: Annotated[
        ArtifactRef, Field(description="The champion's test score table artifact.")
    ],
    validation_table: Annotated[
        ArtifactRef, Field(description="The render_metrics_table report artifact.")
    ],
    prior_test_scorings: Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)],
    n_waves: Annotated[int, Field(ge=1, description=ORCHESTRATOR_SUPPLIED)],
    sections: Annotated[
        tuple[ReportSection, ...],
        Field(
            description="LLM narrative sections, spliced in verbatim between Inference and Figures."
        ),
    ] = _NO_SECTIONS,
    coefficient_table: Annotated[
        ArtifactRef | None,
        Field(description="An optional coefficient_table artifact; adds the Inference section."),
    ] = None,
    plots: Annotated[
        tuple[ArtifactRef, ...],
        Field(description="Plot artifacts copied to report/plots/ and linked in Figures."),
    ] = _NO_PLOTS,
) -> ToolSuccess:
    """Assemble the final markdown report, stating every fact from artifacts (D-c, D-e).

    `champion` must be a `select_champion` table; `test_score` a score table of the champion's task
    holding its metric; `validation_table` a `report` artifact; `coefficient_table` (if given) must
    have `goals`; each plot must be an existing `plot` artifact -- each otherwise `tool_param`.

    The layout is exactly: `# {title}`; `## Result` (D-c's sentences, each present only when its
    condition holds, the test line always); `## Validation comparison` (the markdown table);
    `## Inference` (only with a coefficient table, the per-goal table plus the D35 note); the
    LLM `sections` verbatim; `## Figures` (only with plots). `prior_test_scorings` and `n_waves`
    are orchestrator-supplied -- the tool cannot see the DB. Plots are copied to
    `report/plots/<ref>.png`
    by reference. Overwrites `report/report.md`; returns no artifact. The summary carries the
    relative `path`, `n_sections`, `n_plots` and the file's `sha256`.
    """
    champ = _load_champion(paths, champion)
    test = _load_test_score(paths, test_score, champ)
    validation_md = _load_validation_markdown(paths, validation_table)
    coef = (
        _load_coefficient_table(paths, coefficient_table) if coefficient_table is not None else None
    )
    _check_plots(paths, plots)

    parts: list[str] = [
        f"# {heading_line(title)}",
        _result_section(champ, test, prior_test_scorings=prior_test_scorings, n_waves=n_waves),
        f"## Validation comparison\n\n{validation_md}",
    ]
    if coef is not None:
        parts.append(_inference_section(coef))
    for section in sections:
        parts.append(f"## {heading_line(section.heading)}\n\n{section.body}")
    if plots:
        parts.append(_copy_plots(paths, plots))

    document = "\n\n".join(parts) + "\n"
    report_path = paths.report / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(document, encoding="utf-8")

    sha256 = hashlib.sha256(document.encode("utf-8")).hexdigest()
    summary: dict[str, JsonValue] = {
        "path": str(report_path.relative_to(paths.root)),
        "n_sections": len(sections),
        "n_plots": len(plots),
        "sha256": sha256,
    }
    return ToolSuccess(summary=summary)
