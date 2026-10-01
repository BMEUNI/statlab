"""Node 3 profiling tools: `PLAN.md` §4 node 3, §6.3's `profiling` phase.

Nine tools, all read-only: a `fit_*`/`apply_*` split (§6.1 rule 3) does not apply here because
nothing here is learned-and-reused, only reported (D-d). Every tool dispatches on dtype, not on
`infer_column_roles`' inferred role (D-b): date strings are strings until chunk 11's
`cast_dtypes` casts them, so they profile as categorical here. That is honest about what the
data *is* right now, even if it is less informative than it will be once dates are typed;
calling `infer_column_roles` internally would risk a second, silently different inference from
the one the agent already saw and decided on.

Constant columns (one distinct non-null value, or none) are the one hazard every spread-based
statistic in this module shares (P6): a `std` of `0` makes a z-score divide by zero and gives
`NaN`, so every outlier comparison is `False` and the tool would report "0 outliers" -- a
confident wrong answer, not an error. Every such statistic checks for this first and lists the
column under the result's `skipped`, with a reason, rather than computing a number that means
nothing.

Every value entering a summary or JSON artifact goes through `_frame.json_scalar`/`json_list`
(P1-P3): `np.int64` fails `ToolSuccess` validation outright, and `NaN`/`inf` pass validation but
would be written as invalid JSON by `save_json`'s now-strict `allow_nan=False` (chunk 09 §3.2).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Final, Literal

import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import load_dataframe, save_json, save_plot
from statlab_tools.tool import ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import (
    CLASS_TARGET_MAX_LEVELS,
    is_numeric_column,
    json_list,
    json_scalar,
)

CORRELATION_TOP_PAIRS: Final = 10
"""`compute_correlations`: the summary's `strongest` list holds at most this many pairs,
ranked by |r| -- the matrix itself, with every pair, is the artifact (D-d)."""

CO_MISSING_JACCARD: Final = 0.8
"""`report_missingness`: a pair of columns is reported in `co_missing` when the Jaccard
overlap of their null masks is at or above this -- the usual signature of a structural cause
such as an optional form section, rather than coincidental missingness."""

IMBALANCE_WARNING: Final = 0.1
"""`summarize_target`: a class target warns when its minority share is below this. This is the
number that later justifies class weighting (`PLAN.md` §4.2's worked example)."""

OUTLIER_ROWS_CAP: Final = 100
"""`detect_outliers`: at most this many row labels are listed per column in the summary; the
`count` is always exact, and `truncated` says whether the list was cut."""

IQR_K: Final = 1.5
"""`detect_outliers`'s `iqr` method: values outside `[q1 - k*IQR, q3 + k*IQR]` are flagged."""

ZSCORE_THRESHOLD: Final = 3.0
"""`detect_outliers`'s `zscore` method: values with `|z| >` this are flagged."""

HIST_MAX_BINS: Final = 30
"""`plot_distribution`'s numeric branch: `bins = min(HIST_MAX_BINS, n_unique)`."""

PLOT_MAX_CATEGORIES: Final = 50
"""D15's plot cap: a bar/box/grouped-bar plot shows at most this many categorical levels, by
frequency, with a warning naming how many were omitted."""

TOP_K_DEFAULT: Final = 10
"""`summarize_categorical`'s default `top_k`."""

TOP_K_MIN: Final = 1
TOP_K_MAX: Final = 50

_FIGSIZE: Final = (8, 5)
_DPI: Final = 100


# --- shared ----------------------------------------------------------------------------------


def _resolve_columns(
    data: pd.DataFrame, columns: tuple[str, ...] | None, *, predicate: Callable[[pd.Series], bool]
) -> list[str]:
    """Shared `columns` handling for every summary tool (§3.3's "shared validation").

    `columns=None` means every column `predicate` accepts -- never an error, even if that is
    zero columns (D-c). `columns` naming a column the frame does not have is `tool_param`,
    listing every unknown name in one message, not just the first. A named column the
    predicate rejects is also `tool_param`: the agent asked for a specific column by name, so a
    silent skip would hide a real mistake that `columns=None`'s silent selection does not.
    """
    unknown = [c for c in (columns or ()) if c not in data.columns]
    if unknown:
        raise ToolParamError(
            f"unknown column(s): {sorted(unknown)}",
            field_errors={"columns": f"not present in the frame: {sorted(unknown)}"},
        )
    if columns is None:
        return [str(c) for c in data.columns if predicate(data[c])]
    wrong_dtype = [c for c in columns if not predicate(data[c])]
    if wrong_dtype:
        raise ToolParamError(
            f"column(s) not applicable: {sorted(wrong_dtype)}",
            field_errors={"columns": f"wrong dtype for this tool: {sorted(wrong_dtype)}"},
        )
    return list(columns)


def _is_constant(series: pd.Series) -> bool:
    """One distinct non-null value, or none at all (P6)."""
    return series.dropna().nunique() <= 1


# --- profile_schema ----------------------------------------------------------------------------


@tool(phase="profiling", allow_in=["data_profiling"])
def profile_schema(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
) -> ToolSuccess:
    """Report the frame's shape, memory footprint, and per-column dtype/cardinality/nullness.

    The first look at a frame: nothing here is a statistic about values, only about shape --
    everything downstream (`summarize_numeric`, `summarize_categorical`, ...) builds on what
    this reports.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    columns: dict[str, JsonValue] = {
        str(column): {
            "dtype": str(data[column].dtype),
            "n_unique": json_scalar(data[column].nunique()),
            "n_null": json_scalar(data[column].isna().sum()),
        }
        for column in data.columns
    }
    return ToolSuccess(
        summary={
            "rows": data.shape[0],
            "cols": data.shape[1],
            "memory_bytes": json_scalar(data.memory_usage(deep=True).sum()),
            "columns": columns,
        }
    )


# --- summarize_numeric --------------------------------------------------------------------------


def _numeric_stats(series: pd.Series) -> dict[str, JsonValue]:
    non_null = series.dropna()
    return {
        "count": json_scalar(non_null.count()),
        "mean": json_scalar(non_null.mean()),
        "std": json_scalar(non_null.std()),
        "min": json_scalar(non_null.min()),
        "q25": json_scalar(non_null.quantile(0.25)),
        "median": json_scalar(non_null.median()),
        "q75": json_scalar(non_null.quantile(0.75)),
        "max": json_scalar(non_null.max()),
        "skew": json_scalar(non_null.skew()),
    }


@tool(phase="profiling", allow_in=["data_profiling"])
def summarize_numeric(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Columns to summarize. Omit to summarize every numeric column."),
    ] = None,
) -> ToolSuccess:
    """Count/mean/std/quantiles/skew for numeric columns. Never computed on a constant column.

    `columns=None` selects every column `_frame.is_numeric_column` accepts, so a frame with no
    numeric column at all returns an empty result rather than an error (D-c) -- the agent is
    not required to already know whether any numeric column exists before asking.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    selected = _resolve_columns(data, columns, predicate=is_numeric_column)
    stats: dict[str, JsonValue] = {}
    skipped: dict[str, JsonValue] = {}
    for column in selected:
        series = data[column]
        if _is_constant(series):
            skipped[column] = "constant column: mean/std/quantiles would be degenerate"
            continue
        stats[column] = _numeric_stats(series)
    return ToolSuccess(summary={"columns": stats, "skipped": skipped})


# --- summarize_categorical -----------------------------------------------------------------------


def _categorical_top(series: pd.Series, *, top_k: int) -> tuple[list[JsonValue], bool]:
    non_null = series.dropna()
    counts = non_null.value_counts()
    total = len(non_null)
    truncated = len(counts) > top_k
    top: list[JsonValue] = [
        {
            "value": json_scalar(value),
            "count": json_scalar(count),
            "share": json_scalar(count / total),
        }
        for value, count in counts.head(top_k).items()
    ]
    return top, truncated


@tool(phase="profiling", allow_in=["data_profiling"])
def summarize_categorical(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Columns to summarize. Omit to summarize every non-numeric column."),
    ] = None,
    top_k: Annotated[
        int, Field(ge=TOP_K_MIN, le=TOP_K_MAX, description="Maximum number of top values kept.")
    ] = TOP_K_DEFAULT,
) -> ToolSuccess:
    """Cardinality and top-k value frequencies for string, category and bool columns.

    Applies to everything `_frame.is_numeric_column` rejects (P4) -- string, category and bool
    columns, so a bool column shows up here and nowhere in `summarize_numeric`'s default
    selection. Shares are of non-null rows, not of all rows.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    selected = _resolve_columns(data, columns, predicate=lambda s: not is_numeric_column(s))
    stats: dict[str, JsonValue] = {}
    skipped: dict[str, JsonValue] = {}
    for column in selected:
        series = data[column]
        non_null = series.dropna()
        if non_null.empty:
            skipped[column] = "all values are null"
            continue
        top, truncated = _categorical_top(series, top_k=top_k)
        stats[column] = {
            "n_unique": json_scalar(non_null.nunique()),
            "top": top,
            "truncated": truncated,
        }
    return ToolSuccess(summary={"columns": stats, "skipped": skipped})


# --- report_missingness -------------------------------------------------------------------------


@tool(phase="profiling", allow_in=["data_profiling"])
def report_missingness(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
) -> ToolSuccess:
    """Per-column null counts, overall complete-row count, and pairs missing together.

    `co_missing` lists every pair of columns that both have at least one null, whose null
    masks' Jaccard overlap is at or above `CO_MISSING_JACCARD` -- the usual sign of a
    structural cause (an optional form section, a since-deprecated field) rather than
    coincidental missingness, and worth surfacing precisely because it suggests one cause with
    several symptoms rather than several independent problems.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    null_counts = data.isna().sum()
    rows = len(data)
    columns_with_nulls = [str(c) for c in data.columns if null_counts[c] > 0]

    per_column: dict[str, JsonValue] = {
        column: {
            "n_null": json_scalar(null_counts[column]),
            "share": json_scalar(null_counts[column] / rows) if rows else None,
        }
        for column in columns_with_nulls
    }

    co_missing: list[tuple[float, JsonValue]] = []
    masks = {column: data[column].isna() for column in columns_with_nulls}
    for i, a in enumerate(columns_with_nulls):
        for b in columns_with_nulls[i + 1 :]:
            # `union` can never be 0 here: both `a` and `b` are drawn from `columns_with_nulls`,
            # so each already has at least one null of its own, which alone makes the union
            # non-empty -- no separate zero-union guard is needed or reachable.
            union = (masks[a] | masks[b]).sum()
            intersection = (masks[a] & masks[b]).sum()
            jaccard = float(intersection / union)
            if jaccard >= CO_MISSING_JACCARD:
                co_missing.append((jaccard, {"a": a, "b": b, "jaccard": json_scalar(jaccard)}))
    co_missing.sort(key=lambda pair: -pair[0])

    complete_rows = int((data.notna().all(axis=1)).sum())
    rows_with_any_missing = rows - complete_rows

    return ToolSuccess(
        summary={
            "columns": per_column,
            "complete_rows": complete_rows,
            "rows_with_any_missing": rows_with_any_missing,
            "co_missing": [entry for _, entry in co_missing],
        }
    )


# --- summarize_target ----------------------------------------------------------------------------


def summarize_class_target(series: pd.Series) -> dict[str, JsonValue]:
    """The class-target branch of `summarize_target`, factored out as its own function so it
    can be reused wherever "class proportions on one series" is the question -- `verify_split`
    (chunk 10) compares exactly this shape across three frames rather than one.

    Per-class `count` and `share`, `minority_share`, `imbalance_ratio` (majority / minority).
    Raises nothing: an empty or single-class series still has a well-defined (if degenerate)
    answer, left to the caller to decide whether that is itself worth flagging.
    """
    non_null = series.dropna()
    counts = non_null.value_counts()
    total = len(non_null)
    classes: dict[str, JsonValue] = {
        str(value): {"count": json_scalar(count), "share": json_scalar(count / total)}
        for value, count in counts.items()
    }
    shares = [count / total for count in counts.to_numpy()]
    minority_share = min(shares) if shares else 0.0
    majority_share = max(shares) if shares else 0.0
    imbalance_ratio = (majority_share / minority_share) if minority_share > 0 else None
    return {
        "kind": "class",
        "classes": classes,
        "minority_share": json_scalar(minority_share),
        "imbalance_ratio": json_scalar(imbalance_ratio),
        "n_null": json_scalar(series.isna().sum()),
    }


@tool(phase="profiling", allow_in=["data_profiling"])
def summarize_target(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    target: Annotated[str, Field(min_length=1, description="The target column.")],
) -> ToolSuccess:
    """Distribution and balance of the target column.

    A numeric target with more than `_frame.CLASS_TARGET_MAX_LEVELS` distinct values is treated
    as continuous and gets `summarize_numeric`'s statistics plus `n_null` (D-b: dispatch on
    dtype and cardinality, never on `infer_column_roles`' output). Anything else is a class
    target: per-class count/share, `minority_share`, `imbalance_ratio`, and a warning when
    `minority_share < IMBALANCE_WARNING` -- the number that later justifies class weighting.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not a column in the data", field_errors={"target": "unknown"}
        )
    series = data[target]
    is_class_target = (
        not is_numeric_column(series) or series.dropna().nunique() <= CLASS_TARGET_MAX_LEVELS
    )
    warnings: list[str] = []
    if is_class_target:
        result = summarize_class_target(series)
        minority_share = result["minority_share"]
        if isinstance(minority_share, float) and minority_share < IMBALANCE_WARNING:
            warnings.append(
                f"minority class share {minority_share:.1%} is below {IMBALANCE_WARNING:.0%}"
            )
    else:
        result = {
            **_numeric_stats(series),
            "kind": "continuous",
            "n_null": json_scalar(series.isna().sum()),
        }
    return ToolSuccess(summary=result, warnings=tuple(warnings))


# --- compute_correlations -----------------------------------------------------------------------


@tool(phase="profiling", allow_in=["data_profiling"])
def compute_correlations(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Columns to correlate. Omit to use every numeric column."),
    ] = None,
    method: Annotated[
        Literal["pearson", "spearman"],
        Field(description="Correlation method. 'spearman' is rank correlation, no scipy."),
    ] = "pearson",
) -> ToolSuccess:
    """Pairwise correlation among numeric columns. The matrix is the artifact; the summary
    holds only the strongest pairs (D-d, §6.1 rule 5).

    `spearman` is computed as `df.rank().corr()`, never `.corr(method="spearman")` -- the
    latter imports scipy, which is not a pinned dependency and would make this tool's behaviour
    depend on an unpinned transitive package (same reasoning as chunk 08's leak-detection rank
    correlation). Constant columns are excluded from the matrix entirely (their row/column
    would be all-`NaN`, P6) and listed in `skipped`.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    selected = _resolve_columns(data, columns, predicate=is_numeric_column)
    skipped: dict[str, JsonValue] = {
        column: "constant column: correlation is undefined"
        for column in selected
        if _is_constant(data[column])
    }
    usable = [c for c in selected if c not in skipped]
    if len(usable) < 2:
        raise ToolDataError(
            f"at least two non-constant numeric columns are needed for correlation, found "
            f"{len(usable)}"
        )
    subset = data[usable]
    corr = subset.rank().corr() if method == "spearman" else subset.corr()

    matrix: list[JsonValue] = [[json_scalar(value) for value in row] for row in corr.to_numpy()]
    strongest: list[tuple[float, JsonValue]] = []
    for i, a in enumerate(usable):
        for b in usable[i + 1 :]:
            r = float(np.asarray(corr.loc[a, b]).item())
            if not np.isnan(r):
                strongest.append((abs(r), {"a": a, "b": b, "r": json_scalar(r)}))
    strongest.sort(key=lambda pair: -pair[0])
    top_pairs = [entry for _, entry in strongest[:CORRELATION_TOP_PAIRS]]

    ref = new_artifact_ref()
    table_payload: dict[str, JsonValue] = {
        "method": method,
        "columns": list(usable),
        "matrix": matrix,
    }
    meta = save_json(
        paths.artifact(ref, "table"),
        table_payload,
        ref=ref,
        kind="table",
        label="correlations",
    )
    return ToolSuccess(summary={"strongest": top_pairs, "skipped": skipped}, artifacts=(meta,))


# --- detect_outliers ------------------------------------------------------------------------------


def _iqr_outlier_mask(series: pd.Series) -> pd.Series:
    non_null = series.dropna()
    q1, q3 = non_null.quantile(0.25), non_null.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - IQR_K * iqr, q3 + IQR_K * iqr
    return (non_null < lo) | (non_null > hi)


def _zscore_outlier_mask(series: pd.Series) -> pd.Series:
    non_null = series.dropna()
    z = (non_null - non_null.mean()) / non_null.std()
    return z.abs() > ZSCORE_THRESHOLD


@tool(phase="profiling", allow_in=["data_profiling"])
def detect_outliers(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Columns to check. Omit to check every numeric column."),
    ] = None,
    method: Annotated[
        Literal["iqr", "zscore"],
        Field(description="'iqr': outside [q1-1.5*IQR, q3+1.5*IQR]. 'zscore': |z| > 3."),
    ] = "iqr",
) -> ToolSuccess:
    """Count and report (never remove) numeric outliers per column.

    Reports only -- `fit_outlier_clipper` (chunk 11) is what acts on this. A constant column's
    IQR has zero width and its z-score divides by `std == 0`, both giving `NaN` that compares
    `False` against anything, so a naive implementation would confidently report "0 outliers"
    on a column with no spread at all (P6); this tool skips such columns instead.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    selected = _resolve_columns(data, columns, predicate=is_numeric_column)
    stats: dict[str, JsonValue] = {}
    skipped: dict[str, JsonValue] = {}
    for column in selected:
        series = data[column]
        if _is_constant(series):
            skipped[column] = "constant column: spread-based outlier detection is undefined"
            continue
        mask = _iqr_outlier_mask(series) if method == "iqr" else _zscore_outlier_mask(series)
        flagged_index = mask[mask].index
        count = int(mask.sum())
        rows = json_list(flagged_index[:OUTLIER_ROWS_CAP].tolist())
        stats[column] = {
            "count": count,
            "share_of_non_null": json_scalar(count / mask.size) if mask.size else 0.0,
            "rows": rows,
            "truncated": count > OUTLIER_ROWS_CAP,
        }
    return ToolSuccess(summary={"columns": stats, "skipped": skipped})


# --- plotting --------------------------------------------------------------------------------


def _new_figure() -> tuple[Figure, Axes]:
    """A fresh `Figure` with one subplot, built without `pyplot` (P5): tool bodies run
    concurrently in worker threads since `c5f23d3`, and pyplot's figure registry is
    process-global and not thread-safe."""
    fig = Figure(figsize=_FIGSIZE, dpi=_DPI)
    ax = fig.add_subplot()
    return fig, ax


def _save_plot_artifact(paths: RunPaths, fig: Figure, *, label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_plot(paths.artifact(ref, "plot"), fig, ref=ref, label=label)


@tool(phase="profiling", allow_in=["data_profiling"])
def plot_distribution(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    column: Annotated[str, Field(min_length=1, description="The column to plot.")],
) -> ToolSuccess:
    """Histogram for a numeric column, bar chart of value counts otherwise.

    Column names and category values are user data, not code -- every text call
    (`set_xlabel`/`set_ylabel`/`set_title`/tick labels) passes `parse_math=False` (§3.3), since
    matplotlib otherwise reads paired `$` as mathtext: a label like `cost $ in $ usd` renders
    silently mangled, and a category value like `$\\frac$` raises mid-render. Neither is
    hypothetical -- both come directly from user-supplied column names and values.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    if column not in data.columns:
        raise ToolParamError(
            f"column {column!r} is not in the data", field_errors={"column": "unknown"}
        )
    series = data[column]
    non_null = series.dropna()
    if non_null.empty:
        raise ToolDataError(f"column {column!r} is entirely null; nothing to plot")

    fig, ax = _new_figure()
    warnings: list[str] = []
    if is_numeric_column(series):
        kind = "histogram"
        bins = min(HIST_MAX_BINS, non_null.nunique())
        ax.hist(non_null.to_numpy(), bins=max(bins, 1))
        shown_levels = int(non_null.nunique())
        total_levels = shown_levels
        ax.set_ylabel("count", parse_math=False)
    else:
        kind = "bar"
        counts = non_null.value_counts()
        total_levels = len(counts)
        shown_levels = min(PLOT_MAX_CATEGORIES, total_levels)
        top = counts.head(PLOT_MAX_CATEGORIES)
        positions = list(range(len(top)))
        ax.bar(positions, top.to_numpy())
        ax.set_xticks(positions, [str(v) for v in top.index], parse_math=False)
        ax.set_ylabel("count", parse_math=False)
        if total_levels > PLOT_MAX_CATEGORIES:
            warnings.append(f"showing {PLOT_MAX_CATEGORIES} of {total_levels} levels")
    ax.set_xlabel(str(column), parse_math=False)
    ax.set_title(f"distribution of {column}", parse_math=False)
    fig.tight_layout()
    meta = _save_plot_artifact(paths, fig, label=f"distribution of {column}")
    return ToolSuccess(
        summary={"kind": kind, "shown_levels": shown_levels, "total_levels": total_levels},
        artifacts=(meta,),
        warnings=tuple(warnings),
    )


@tool(phase="profiling", allow_in=["data_profiling"])
def plot_target_relation(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
    column: Annotated[str, Field(min_length=1, description="The feature column to plot.")],
    target: Annotated[str, Field(min_length=1, description="The target column.")],
) -> ToolSuccess:
    """Plot `column` against `target`, choosing the right shape for the pair of dtypes.

    Class target + numeric feature: box plot per class. Class target + non-numeric feature:
    grouped bars of the class share per feature level (top 50, by frequency). Continuous
    target + numeric feature: scatter. Continuous target + non-numeric feature: box plot per
    level (top 50). `column == target` is a `tool_param` -- there is no relation to plot
    between a column and itself.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    for name in (column, target):
        if name not in data.columns:
            raise ToolParamError(
                f"column {name!r} is not in the data", field_errors={"column": "unknown"}
            )
    if column == target:
        raise ToolParamError(
            "column and target must differ", field_errors={"column": "same as target"}
        )

    pair = data[[column, target]].dropna()
    if pair.empty:
        raise ToolDataError(f"no rows with both {column!r} and {target!r} present")

    target_series = pair[target]
    feature_series = pair[column]
    is_class_target = (
        not is_numeric_column(target_series) or target_series.nunique() <= CLASS_TARGET_MAX_LEVELS
    )
    feature_is_numeric = is_numeric_column(feature_series)

    fig, ax = _new_figure()
    warnings: list[str] = []

    if is_class_target and feature_is_numeric:
        kind = "box"
        classes = sorted(target_series.unique(), key=str)
        groups = [feature_series[target_series == cls].to_numpy() for cls in classes]
        ax.boxplot(groups, tick_labels=[str(v) for v in classes])
        ax.set_xlabel(str(target), parse_math=False)
        ax.set_ylabel(str(column), parse_math=False)
    elif is_class_target and not feature_is_numeric:
        kind = "bars"
        level_counts = feature_series.value_counts()
        levels = list(level_counts.head(PLOT_MAX_CATEGORIES).index)
        total_levels = len(level_counts)
        if total_levels > PLOT_MAX_CATEGORIES:
            warnings.append(f"showing {PLOT_MAX_CATEGORIES} of {total_levels} levels")
        classes = sorted(target_series.unique(), key=str)
        width = 0.8 / max(len(classes), 1)
        for i, cls in enumerate(classes):
            shares = [
                float(
                    ((feature_series == level) & (target_series == cls)).sum()
                    / max((feature_series == level).sum(), 1)
                )
                for level in levels
            ]
            positions = [p + i * width for p in range(len(levels))]
            ax.bar(positions, shares, width=width, label=str(cls))
        ax.set_xticks(
            [p + width * (len(classes) - 1) / 2 for p in range(len(levels))],
            [str(v) for v in levels],
            parse_math=False,
        )
        ax.set_xlabel(str(column), parse_math=False)
        ax.set_ylabel(f"share of {target}", parse_math=False)
        ax.legend()
    elif not is_class_target and feature_is_numeric:
        kind = "scatter"
        ax.scatter(feature_series.to_numpy(), target_series.to_numpy())
        ax.set_xlabel(str(column), parse_math=False)
        ax.set_ylabel(str(target), parse_math=False)
    else:
        kind = "box"
        counts = feature_series.value_counts()
        total_levels = len(counts)
        top_levels = counts.head(PLOT_MAX_CATEGORIES).index
        if total_levels > PLOT_MAX_CATEGORIES:
            warnings.append(f"showing {PLOT_MAX_CATEGORIES} of {total_levels} levels")
        subset = pair[feature_series.isin(top_levels)]
        levels_sorted = sorted(subset[column].unique(), key=str)
        groups = [subset[target][subset[column] == level].to_numpy() for level in levels_sorted]
        ax.boxplot(groups, tick_labels=[str(v) for v in levels_sorted])
        ax.set_xlabel(str(column), parse_math=False)
        ax.set_ylabel(str(target), parse_math=False)

    ax.set_title(f"{column} vs {target}", parse_math=False)
    fig.tight_layout()
    meta = _save_plot_artifact(paths, fig, label=f"{column} vs {target}")
    return ToolSuccess(summary={"kind": kind}, artifacts=(meta,), warnings=tuple(warnings))
