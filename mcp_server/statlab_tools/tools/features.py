"""The node-8 feature tools (`plan/17_tools_features.md`): three row-wise constructors, a fitted
binner (fit/apply), and two train-side selectors. `phase="features"`.

Same shape as `cleaning.py`/`encoding.py` (D22): fitted state is a frozen dataclass pickled as
kind `transformer`, loaded through `_frame.load_fitted` so a wrong ref is `tool_param`; the target
is never transformed (present or absent); labels and row order are preserved (never `reset_index`);
every summary number goes through `_frame.json_scalar`, and an infinite bin edge is `null` in a
summary (strict JSON). Row-wise constructors are stateless -- calling one with the same arguments
on train, valid and test is the replay, so they carry no fitted state and are `transform_only`
(D-a/D-b). The selectors decide on train and write a reduced frame plus `dropped_columns`;
`drop_columns` replays the drop on the other splits (D-d).

pandas 3.0 / sklearn 1.9 facts this module leans on (verified under `-W error`, §1):
- P1: `.dt`-part on a `datetime64[us]` column with NaT comes back `float64` with NaN, and
  `(dayofweek >= 5)` is `False` on a NaT row, not NA -- so parts are cast to `Int64` and
  `is_weekend` is masked with `isna()`, or a missing date would read as a weekday.
- P3: `np.quantile` edges collapse on ties; the binner deduplicates inner edges and warns with the
  achieved count. Fewer than two bins is a data error.
- P4: `pd.cut(x, [-inf, *inner, inf], right=False, labels=False)` puts out-of-range values in the
  outer bins and keeps NaN as NaN, so apply never leaves a non-null value unbinned.
- P5: `a / b` with `b == 0` gives `+-inf` silently even under `-W error`; the ratio divides by
  `b.where(b != 0)` so a zero denominator becomes NaN, counted.
- P6: `Int64 * float64` is nullable `Float64`; inputs are converted with
  `to_numpy(dtype="float64", na_value=np.nan)` so the product is plain `float64`.
- P7: `mutual_info_*` raises on NaN in X (refused up front as `tool_param`); bool columns are
  discrete. sklearn is imported lazily inside the body (it pulls scipy) with `n_jobs=1`.
- P8: `select_by_variance` uses population variance (`ddof=0`), like sklearn's `VarianceThreshold`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

import numpy as np
import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import load_dataframe, save_model
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import (
    is_class_target,
    is_numeric_column,
    json_list,
    json_scalar,
    load_fitted,
    require_columns,
    save_frame,
)

_CONSTRUCT_PHASES = ("feature_engineering", "champion_selection")
"""Row-wise constructors and `apply_binner` are transform-only: node 12 replays them on test, so
they list `champion_selection` as well as `feature_engineering` (D-a/D-b)."""
_FIT_PHASES = ("feature_engineering",)
"""The fitted binner's `fit_*` half and both selectors decide on train only (D-c/D-d)."""

_Train = Annotated[ArtifactRef, Field(description="The train split frame; never test-derived.")]
_Frame = Annotated[ArtifactRef, Field(description="The dataframe to transform.")]
_Target = Annotated[str, Field(min_length=1, description="The target column; never transformed.")]

DatetimePart = Literal["year", "month", "day", "dayofweek", "dayofyear", "hour", "is_weekend"]
"""The datetime parts `add_datetime_parts` can extract (D-b). `is_weekend` is derived from
`dayofweek >= 5`, masked on NaT (P1); the rest are the matching `Series.dt` attribute."""


# --- fitted state --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedBinner:
    """Learned bin edges per column (D-c). `edges[col]` is the full edge vector, outer edges always
    `+-inf` (P4), so `apply_binner` never leaves a non-null value unbinned. Frozen and plain so
    joblib round-trips it and chunk 20 reads it without un-pickling bespoke classes."""

    target: str
    strategy: str
    edges: dict[str, tuple[float, ...]]


# --- shared --------------------------------------------------------------------------------------


def _feature_columns(frame: pd.DataFrame, target: str) -> list[str]:
    """Every column except `target` (the target is never transformed). The target need not be
    present."""
    return [col for col in frame.columns if col != target]


def _reject_target(names: tuple[str, ...], target: str, field: str) -> None:
    """Naming the target where a feature column is expected is `tool_param` (the target is never
    transformed)."""
    if target in names:
        raise ToolParamError(
            f"{field} names the target {target!r}, which is never transformed",
            field_errors={field: f"must not include the target {target!r}"},
        )


def _reject_collisions(frame: pd.DataFrame, new_names: tuple[str, ...], field: str) -> None:
    """A generated name colliding with an existing column is `tool_param` (chunk 12's rule)."""
    collisions = [name for name in new_names if name in frame.columns]
    if collisions:
        raise ToolParamError(
            f"generated names collide with existing columns: {collisions!r}",
            field_errors={field: f"name collision: {collisions!r}"},
        )


def _save_transformer(paths: RunPaths, obj: object, label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_model(
        paths.artifact(ref, "transformer"), obj, ref=ref, kind="transformer", label=label
    )


def _numeric_float(series: pd.Series) -> np.ndarray:
    """A column as plain `float64`, nulls as NaN (P6: an `Int64`/`Float64` input would otherwise
    stay nullable through arithmetic). A `bool` column becomes 0.0/1.0."""
    return series.to_numpy(dtype="float64", na_value=np.nan)


def _edge_json(edges: tuple[float, ...]) -> list[JsonValue]:
    """Bin edges for a summary, with the `+-inf` outer edges as `null` (strict JSON): `json_scalar`
    maps a non-finite float to `None`."""
    return [json_scalar(e) for e in edges]


# --- 3.1 row-wise constructors (transform-only) --------------------------------------------------


@tool(phase="features", allow_in=_CONSTRUCT_PHASES, transform_only=True)
def add_datetime_parts(
    paths: RunPaths,
    frame: _Frame,
    target: _Target,
    column: Annotated[str, Field(min_length=1, description="A datetime column to decompose.")],
    parts: Annotated[
        tuple[DatetimePart, ...],
        Field(min_length=1, description="Which parts to extract; no duplicates."),
    ],
) -> ToolSuccess:
    """Replace a datetime `column` with one `Int64` column per part, named `f"{column}__{part}"`,
    at the source column's position (D-b: training refuses a datetime feature, so the source goes).
    A part is NA wherever the date is NaT, `is_weekend` included (P1: the naive `dayofweek >= 5`
    reads NaT as a weekday). This is stateless -- the same call on train/valid/test is the replay
    (D-b), so it is `transform_only`.

    The column must be a datetime dtype (tz-aware included; parts come out in the column's own zone,
    P2); a `str` column is `tool_param` ("cast it with `cast_dtypes` in node 4"). A duplicated part
    or a generated name colliding with an existing column is `tool_param`.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    require_columns(data, (column,), "column")
    _reject_target((column,), target, "column")
    if len(set(parts)) != len(parts):
        raise ToolParamError(
            "parts contains duplicates", field_errors={"parts": "parts must be unique"}
        )
    source = data[column]
    if not pd.api.types.is_datetime64_any_dtype(source):
        raise ToolParamError(
            f"column {column!r} is not a datetime dtype; cast it with cast_dtypes in node 4",
            field_errors={"column": "must be a datetime dtype"},
        )

    new_names = tuple(f"{column}__{part}" for part in parts)
    _reject_collisions(data.drop(columns=[column]), new_names, "column")

    is_nat = source.isna()
    built: dict[str, pd.Series] = {}
    for part, name in zip(parts, new_names, strict=True):
        if part == "is_weekend":
            weekend = (source.dt.dayofweek >= 5).astype("Int64")
            weekend[is_nat] = pd.NA  # P1: NaT reads as a weekday otherwise
            built[name] = weekend
        else:
            built[name] = getattr(source.dt, part).astype("Int64")

    position = list(data.columns).index(column)
    out = data.drop(columns=[column])
    for offset, name in enumerate(new_names):
        out.insert(min(position + offset, len(out.columns)), name, built[name])

    summary: dict[str, JsonValue] = {"column": column, "parts": json_list(parts)}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, out, "datetime_parts"),))


@tool(phase="features", allow_in=_CONSTRUCT_PHASES, transform_only=True)
def add_interaction_terms(
    paths: RunPaths,
    frame: _Frame,
    target: _Target,
    pairs: Annotated[
        tuple[tuple[str, str], ...],
        Field(min_length=1, description="Column pairs; each adds the product a*b. (a, a) squares."),
    ],
) -> ToolSuccess:
    """Add `f"{a}__x__{b}"` = a*b as `float64` for each pair (D-b). `(a, a)` is allowed and adds a
    square. Both columns must be numeric or bool; a bool is cast to 0/1 (P6: inputs are taken as
    plain `float64` so the product is not nullable `Float64`). NaN propagates. The source columns
    stay (they are still useful), so this adds columns rather than replacing them. Stateless, so
    `transform_only`.

    A non-numeric input, a missing column, the target, or a generated name colliding with an
    existing column is `tool_param`.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    named = tuple({col for pair in pairs for col in pair})
    require_columns(data, named, "pairs")
    _reject_target(named, target, "pairs")
    bad = [
        col
        for col in named
        if not (is_numeric_column(data[col]) or pd.api.types.is_bool_dtype(data[col]))
    ]
    if bad:
        raise ToolParamError(
            f"interaction columns {bad!r} are not numeric or bool",
            field_errors={"pairs": f"must be numeric or bool: {bad!r}"},
        )

    new_names = tuple(f"{a}__x__{b}" for a, b in pairs)
    if len(set(new_names)) != len(new_names):
        raise ToolParamError(
            "pairs produce duplicate names", field_errors={"pairs": "duplicate interaction names"}
        )
    _reject_collisions(data, new_names, "pairs")

    out = data.copy()
    for (a, b), name in zip(pairs, new_names, strict=True):
        out[name] = _numeric_float(data[a]) * _numeric_float(data[b])

    summary: dict[str, JsonValue] = {"added": json_list(new_names)}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, out, "interaction_terms"),))


@tool(phase="features", allow_in=_CONSTRUCT_PHASES, transform_only=True)
def add_ratio_feature(
    paths: RunPaths,
    frame: _Frame,
    target: _Target,
    numerator: Annotated[str, Field(min_length=1, description="The ratio's numerator column.")],
    denominator: Annotated[str, Field(min_length=1, description="The ratio's denominator column.")],
) -> ToolSuccess:
    """Add `f"{numerator}__per__{denominator}"` as `float64` (D-b). A zero denominator (including
    `-0.0`) gives NaN, never `+-inf` (P5: the raw `a / b` would give an infinity silently). The
    count of zero-denominator rows is in the summary, with a warning when it is above 0. The source
    columns stay. Stateless, so `transform_only`.

    Both columns must be numeric or bool, present, and not the target; a generated name colliding
    with an existing column is `tool_param`.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    named = (numerator, denominator)
    require_columns(data, named, "numerator")
    _reject_target(named, target, "numerator")
    bad = [
        col
        for col in named
        if not (is_numeric_column(data[col]) or pd.api.types.is_bool_dtype(data[col]))
    ]
    if bad:
        raise ToolParamError(
            f"ratio columns {bad!r} are not numeric or bool",
            field_errors={"numerator": f"must be numeric or bool: {bad!r}"},
        )

    name = f"{numerator}__per__{denominator}"
    _reject_collisions(data, (name,), "numerator")

    num = _numeric_float(data[numerator])
    den = _numeric_float(data[denominator])
    zero_rows = int(np.count_nonzero(den == 0.0))  # catches -0.0 too (-0.0 == 0.0)
    safe_den = np.where(den == 0.0, np.nan, den)  # P5: zero -> NaN, not inf
    out = data.copy()
    out[name] = num / safe_den

    warnings_out = (
        (f"{zero_rows} rows have a zero {denominator!r}; their ratio is NaN",)
        if zero_rows > 0
        else ()
    )
    summary: dict[str, JsonValue] = {
        "added": name,
        "zero_denominator_rows": json_scalar(zero_rows),
    }
    return ToolSuccess(
        summary=summary,
        artifacts=(save_frame(paths, out, "ratio_feature"),),
        warnings=warnings_out,
    )


# --- 3.2 binning ---------------------------------------------------------------------------------


@tool(phase="features", allow_in=_FIT_PHASES)
def fit_binner(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    columns: Annotated[tuple[str, ...], Field(min_length=1, description="Numeric columns to bin.")],
    n_bins: Annotated[
        int, Field(ge=2, le=20, description="Requested bins; ties may yield fewer.")
    ] = 5,
    strategy: Annotated[
        Literal["quantile", "uniform"], Field(description="How inner edges are placed.")
    ] = "quantile",
) -> ToolSuccess:
    """Learn bin edges per numeric column from train's non-null values (D-c). `quantile` places
    inner edges at `linspace(0, 1, n_bins + 1)` quantiles; `uniform` at `linspace(min, max,
    n_bins + 1)`. Inner edges are deduplicated (P3: `np.quantile` edges collapse on ties), outer
    edges are always `+-inf` (P4). An achieved bin count below `n_bins` warns, naming the column;
    fewer than two bins is a `ToolDataError` (a constant column cannot be binned). Edges go in the
    fitted state and the summary (infinities as `null`), so a report can render them.

    Numeric columns only (`is_numeric_column`); a non-numeric column, a missing column, or the
    target is `tool_param`. `apply_binner` replays these edges -- they are never recomputed.
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    require_columns(data, columns, "columns")
    _reject_target(columns, target, "columns")
    non_numeric = [col for col in columns if not is_numeric_column(data[col])]
    if non_numeric:
        raise ToolParamError(
            f"columns {non_numeric!r} are not numeric; the binner needs numeric columns",
            field_errors={"columns": f"must be numeric: {non_numeric!r}"},
        )

    warnings_out: list[str] = []
    edges: dict[str, tuple[float, ...]] = {}
    for col in columns:
        values = data[col].dropna().to_numpy(dtype="float64")
        if strategy == "quantile":
            inner = np.quantile(values, np.linspace(0.0, 1.0, n_bins + 1))
        else:
            inner = np.linspace(float(values.min()), float(values.max()), n_bins + 1)
        unique_inner = np.unique(inner)  # P3: deduplicate collapsed edges
        # Drop the data min/max from the inner edges; the outer edges are always +-inf (P4).
        interior = tuple(float(e) for e in unique_inner[1:-1])
        full = (-np.inf, *interior, np.inf)
        achieved = len(full) - 1
        if achieved < 2:
            raise ToolDataError(
                f"column {col!r} yields fewer than 2 bins under {strategy!r} "
                f"(n_bins={n_bins}); it has too few distinct values to bin"
            )
        if achieved < n_bins:
            warnings_out.append(
                f"column {col!r} yielded {achieved} bins, fewer than the requested {n_bins} "
                f"(ties collapsed edges)"
            )
        edges[col] = full

    binner = FittedBinner(target=target, strategy=strategy, edges=edges)
    meta = _save_transformer(paths, binner, "binner")
    summary: dict[str, JsonValue] = {
        "strategy": strategy,
        "edges": {col: _edge_json(edges[col]) for col in columns},
        "n_bins": {col: json_scalar(len(edges[col]) - 1) for col in columns},
    }
    return ToolSuccess(summary=summary, artifacts=(meta,), warnings=tuple(warnings_out))


@tool(phase="features", allow_in=_CONSTRUCT_PHASES, transform_only=True)
def apply_binner(
    paths: RunPaths,
    binner: Annotated[ArtifactRef, Field(description="A fitted binner from fit_binner.")],
    frame: _Frame,
) -> ToolSuccess:
    """Replace each fitted column with `f"{col}__bin"`, a `str` label `b{i}` per bin, at the
    source's position (D-c). Binning uses `pd.cut(..., right=False)` with the fitted edges, whose
    outer edges are `+-inf`, so an out-of-range value lands in `b0` or `b{k-1}` and NaN stays NaN
    (P4). The edges are **never** recomputed from `frame` -- they come only from the binner, which
    is what keeps the fit leak-free. The resulting `str` column is picked up by
    `fit_onehot_encoder` by default. `transform_only`: node 12 replays it on test.
    """
    fitted = load_fitted(paths, binner, FittedBinner, "binner")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    missing = [col for col in fitted.edges if col not in data.columns and col != fitted.target]
    if missing:
        raise ToolParamError(
            f"frame is missing fitted columns {missing!r}",
            field_errors={"frame": f"missing fitted columns: {missing!r}"},
        )

    out = data.copy()
    for col, edges in fitted.edges.items():
        codes = pd.cut(data[col], list(edges), right=False, labels=False)  # P4
        labels = codes.map(lambda c: f"b{int(c)}" if pd.notna(c) else pd.NA).astype("str")
        name = f"{col}__bin"
        position = list(out.columns).index(col)
        out = out.drop(columns=[col])
        out.insert(min(position, len(out.columns)), name, labels.to_numpy())

    summary: dict[str, JsonValue] = {
        "binned": {
            f"{col}__bin": json_scalar(len(edges) - 1) for col, edges in fitted.edges.items()
        }
    }
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, out, "binned"),))


# --- 3.3 selection (decide on train) -------------------------------------------------------------


@tool(phase="features", allow_in=_FIT_PHASES)
def select_by_variance(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    threshold: Annotated[
        float, Field(ge=0.0, description="Drop considered columns with variance <= this.")
    ] = 0.0,
) -> ToolSuccess:
    """Drop every numeric or bool feature whose population variance (`ddof=0`, P8, like sklearn's
    `VarianceThreshold`) is `<= threshold`, deciding on train (D-d). Non-numeric, non-bool features
    are left alone and counted in `not_considered`. An all-null column has undefined variance and
    counts as 0, so it is dropped at any threshold (chunk 11's "an all-null column is constant").
    The target is never considered or dropped. The summary carries `dropped_columns` and their
    variances; replay the drop on valid/test with `drop_columns`.
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    features = _feature_columns(data, target)

    not_considered: list[str] = []
    variances: dict[str, float] = {}
    for col in features:
        series = data[col]
        if not (is_numeric_column(series) or pd.api.types.is_bool_dtype(series)):
            not_considered.append(col)
            continue
        values = series.dropna().to_numpy(dtype="float64")
        variances[col] = float(np.var(values, ddof=0)) if values.size else 0.0

    dropped = [col for col, var in variances.items() if var <= threshold]
    reduced = data.drop(columns=dropped)
    summary: dict[str, JsonValue] = {
        "dropped_columns": json_list(dropped),
        "variances": {col: json_scalar(var) for col, var in variances.items()},
        "not_considered": json_list(not_considered),
    }
    return ToolSuccess(
        summary=summary, artifacts=(save_frame(paths, reduced, "variance_selected"),)
    )


@tool(phase="features", allow_in=_FIT_PHASES, heavy=True)
def select_by_mutual_info(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    k: Annotated[int, Field(ge=1, description="Keep the top-k features by mutual information.")],
    seed: Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)],
) -> ToolSuccess:
    """Keep the top `k` features by mutual information with the target, deciding on train (D-d).
    `mutual_info_classif` for a class target, else `mutual_info_regression`, with bool columns
    marked discrete, `random_state=seed`, `n_jobs=1` (P7). Ties are broken by column name; the rest
    are dropped. `k` at or above the feature count drops nothing, with a warning. The summary has
    every feature's score (sorted) and `dropped_columns`; replay the drop with `drop_columns`.

    Every feature must be numeric or bool ("encode first") and the matrix must have no NaN ("impute
    first", P7: `mutual_info_*` raises on NaN); either is `tool_param`. `heavy=True`, sklearn
    imported lazily inside the body.
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not in train; mutual information needs it",
            field_errors={"target": f"not a column in train: {target!r}"},
        )
    features = _feature_columns(data, target)
    bad = [
        col
        for col in features
        if not (is_numeric_column(data[col]) or pd.api.types.is_bool_dtype(data[col]))
    ]
    if bad:
        raise ToolParamError(
            f"feature columns {bad!r} are not numeric or bool; encode them first",
            field_errors={"train": f"non-numeric feature columns: {bad!r}"},
        )
    matrix = data[features].to_numpy(dtype="float64", na_value=np.nan)
    if np.isnan(matrix).any():
        raise ToolParamError(
            "the feature matrix has NaN; impute first (mutual information cannot score NaN)",
            field_errors={"train": "feature matrix contains NaN"},
        )

    from sklearn.feature_selection import mutual_info_classif, mutual_info_regression

    discrete = np.array([bool(pd.api.types.is_bool_dtype(data[col])) for col in features])
    y = data[target]
    if is_class_target(y):
        raw = mutual_info_classif(
            matrix, y, discrete_features=discrete, random_state=seed, n_jobs=1
        )
    else:
        raw = mutual_info_regression(
            matrix, y, discrete_features=discrete, random_state=seed, n_jobs=1
        )
    scores = {col: float(score) for col, score in zip(features, raw, strict=True)}

    warnings_out: list[str] = []
    if k >= len(features):
        warnings_out.append(
            f"k={k} is at or above the {len(features)} feature count; nothing is dropped"
        )
    # Rank by score desc, column name asc as the tie-break; keep the top k.
    ranked = sorted(features, key=lambda c: (-scores[c], c))
    kept = set(ranked[:k])
    dropped = [col for col in features if col not in kept]
    reduced = data.drop(columns=dropped)
    summary: dict[str, JsonValue] = {
        "scores": {col: json_scalar(scores[col]) for col in ranked},
        "dropped_columns": json_list(dropped),
    }
    return ToolSuccess(
        summary=summary,
        artifacts=(save_frame(paths, reduced, "mutual_info_selected"),),
        warnings=tuple(warnings_out),
    )
