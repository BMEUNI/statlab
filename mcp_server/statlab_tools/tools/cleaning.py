"""The ten cleaning tools: `PLAN.md` §6.3's `cleaning` phase, this chunk's spec (`11_tools_
cleaning.md`). Three groups, by when they may run (D34d):

- Pre-split, structural: `drop_duplicate_rows`, `drop_high_missing_rows`, `cast_dtypes`.
- Decide on train: `drop_constant_columns`, `drop_high_missing_columns`, `fit_imputer`,
  `fit_outlier_clipper`.
- Apply to any frame: `drop_columns`, `apply_imputer`, `apply_outlier_clipper`.

No sklearn (D-d): a fill value per column and a pair of bounds per column are fully described by
a dict, which keeps fitted state readable in the summary (auditability) and trivial for chunk
20's `export_pipeline_script`. Fitted state is a frozen dataclass pickled as kind `transformer`;
`apply_*` type-checks the loaded object so a wrong ref fails as `tool_param`, not `AttributeError`.

After the split no tool drops a row (D-a, D26): every candidate is scored on the same valid and
test rows. `ImputationStrategy` `"drop"` drops the COLUMN. Output frames keep the input's index
labels and row order -- never `reset_index`: `verify_split` and chunk 10's fingerprint rely on
labels. The target is never imputed, clipped or dropped (D-e); `apply_*` works whether the target
is present or absent.

pandas 3.0 facts this module leans on (verified under `-W error`, this chunk's §1):
`Int64.fillna(2.5)` raises `TypeError` (P1), so mean/median columns are cast to `float64` first,
on every frame, so dtypes agree across splits; `Categorical.fillna(<new>)` raises (P2), so a
`constant` fill on a categorical is `tool_param`; integral floats via `Int64` give `"1"` not
`"1.0"` under `astype("str")` (P3); `to_datetime(format="ISO8601", errors="coerce")` still raises
`ValueError` on mixed UTC offsets (P4), caught and retried with `utc=True`. Every number entering
a summary goes through `_frame.json_scalar`; level counts use `_frame.observed_counts` so a
categorical's unused level is not counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Final, Literal, cast

import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import ColumnRole, ImputationStrategy

from statlab_tools.artifacts import load_dataframe, save_model
from statlab_tools.tool import ToolParamError, tool
from statlab_tools.tools._frame import (
    as_str,
    is_numeric_column,
    json_list,
    json_scalar,
    load_fitted,
    require_columns,
    save_frame,
)

IQR_FENCE: Final = 1.5
"""Tukey's fence multiplier for `clip_iqr`/`flag_only` bounds: Q1 - 1.5*IQR, Q3 + 1.5*IQR.
A constant, not a parameter -- 1.5 is the convention chunk 09's `detect_outliers` already uses,
and the agent has no basis to vary it."""

WINSORIZE_LOWER: Final = 0.01
WINSORIZE_UPPER: Final = 0.99
"""`winsorize` clips to the 1st and 99th percentiles -- the standard winsorisation cut."""

CONSTANT_NUMERIC_FILL: Final = 0.0
CONSTANT_STRING_FILL: Final = "__missing__"
"""`constant` imputation's two fills: `0.0` for numeric, a sentinel for `str`/object (P2: any
other dtype is `tool_param`, so these two cover every legal case)."""

_BOOLEAN_TRUE: Final = frozenset({"true", "t", "yes", "y", "1"})
_BOOLEAN_FALSE: Final = frozenset({"false", "f", "no", "n", "0"})
"""`cast_dtypes`'s case-insensitive boolean map; anything else becomes NaN."""

# Phase lists for the multi-phase tools, as named tuples rather than inline list literals: the
# `@tool` decorator takes a `Sequence[str]`, and a tuple constant reads as what it is (the nodes
# a tool may run in, D34d's three groups) while keeping each list in one obvious place.
_CAST_PHASES: Final = ("data_profiling", "data_splitting")
_APPLY_PHASES: Final = ("preprocessing", "champion_selection")
_DROP_COLUMNS_PHASES: Final = ("data_splitting", "preprocessing", "champion_selection")

_NO_ROLES: Final[dict[str, ColumnRole]] = {}
_NO_PER_COLUMN: Final[dict[str, ImputationStrategy]] = {}
"""Shared empty defaults for the two dict parameters. Never mutated -- read-only, so sharing one
instance across concurrent tool bodies is safe (the handoff's no-module-level-mutable-state rule
is about state a body writes). A named constant, not a `{}` literal default (ruff B006)."""


# --- fitted state (D-d) --------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedImputer:
    """A learned fill per feature column, plus the columns that imputation drops.

    `strategies[col]` is the effective strategy (after any fallback); `fills[col]` the value used
    for a non-`drop` column. `target` is recorded so `apply_imputer` can accept a frame with the
    target present or absent (D-e) by comparing on `columns` alone. Frozen and plain so joblib
    round-trips it (P7) and chunk 20 can read it without un-pickling bespoke classes.
    """

    target: str
    columns: tuple[str, ...]
    strategies: dict[str, str]
    fills: dict[str, object]
    dropped: tuple[str, ...]


@dataclass(frozen=True)
class FittedClipper:
    """A pair of bounds per numeric feature column, and the method they were computed for.

    `bounds[col]` is `(lower, upper)`. A column whose bounds would be equal is never fitted (the
    clipper skips it with a warning), so every entry here has `lower < upper`.
    """

    target: str
    method: str
    bounds: dict[str, tuple[float, float]]


# --- shared helpers ------------------------------------------------------------------------------


def _is_datetime_column(series: pd.Series) -> bool:
    return bool(pd.api.types.is_datetime64_any_dtype(series))


def _is_str_column(series: pd.Series) -> bool:
    """`str` dtype or plain object dtype -- the two things `cast_dtypes`' categorical/text branch
    and `constant` imputation treat as string-like. A `Categorical` is excluded even though
    pandas' `is_string_dtype` returns True for it: `Categorical.fillna(<new value>)` raises (P2),
    so `constant` on a categorical must be `tool_param`, not a silent string fill."""
    if isinstance(series.dtype, pd.CategoricalDtype):
        return False
    return bool(pd.api.types.is_string_dtype(series)) or series.dtype == object


# --- 3.1 pre-split -------------------------------------------------------------------------------

_Frame = Annotated[ArtifactRef, Field(description="The dataframe to transform.")]
_Target = Annotated[str, Field(min_length=1, description="The target column; never transformed.")]


@tool(phase="cleaning", allow_in=["data_splitting"])
def drop_duplicate_rows(
    paths: RunPaths,
    frame: _Frame,
    subset: Annotated[
        tuple[str, ...] | None,
        Field(description="Columns to compare; None uses all. Exclude ID columns."),
    ] = None,
) -> ToolSuccess:
    """Drop duplicate rows, keeping the first occurrence. Pre-split only (D34d): a duplicate
    split across train and test is a leak `verify_split` exists to catch.

    Pass a `subset` that excludes ID columns -- an ID column makes every row unique, so a
    whole-row check finds no duplicates. NaN compares equal to NaN (P5), so a row null in the
    same places as another is a duplicate.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    if subset is not None:
        require_columns(data, subset, "subset")
    rows_before = len(data)
    reduced = data.drop_duplicates(
        subset=list(subset) if subset is not None else None, keep="first"
    )
    summary: dict[str, JsonValue] = {
        "rows_before": json_scalar(rows_before),
        "rows_dropped": json_scalar(rows_before - len(reduced)),
        "subset": json_list(subset) if subset is not None else None,
    }
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, reduced, "deduplicated"),))


@tool(phase="cleaning", allow_in=["data_splitting"])
def drop_high_missing_rows(
    paths: RunPaths,
    frame: _Frame,
    threshold: Annotated[
        float,
        Field(gt=0, le=1, description="Drop rows whose null share is strictly above this."),
    ],
) -> ToolSuccess:
    """Drop rows whose share of null cells is strictly greater than `threshold`. Pre-split only
    (D34d): after the split no tool drops a row (D26). Matches the contract's `drop_rows_above`.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    rows_before = len(data)
    null_share = data.isna().mean(axis=1)
    reduced = data[null_share <= threshold]
    summary: dict[str, JsonValue] = {
        "rows_before": json_scalar(rows_before),
        "rows_dropped": json_scalar(rows_before - len(reduced)),
        "threshold": json_scalar(threshold),
    }
    return ToolSuccess(
        summary=summary, artifacts=(save_frame(paths, reduced, "sparse_rows_dropped"),)
    )


def _cast_numeric(series: pd.Series) -> pd.Series:
    if is_numeric_column(series):
        return series
    return pd.to_numeric(series, errors="coerce")


def _cast_string(series: pd.Series) -> pd.Series:
    """To `str` dtype. A numeric column whose non-null values are all integral routes through
    `Int64` first (P3), so `2.0` becomes `"2"`, not `"2.0"`, and NaN stays NaN."""
    if _is_str_column(series):
        return series
    return as_str(series)


def _cast_ordinal(
    series: pd.Series, levels: tuple[str, ...], warnings_out: list[str], column: str
) -> pd.Series:
    """Ordered `Categorical` over `levels`; values outside become NaN. No levels: unchanged,
    with a warning."""
    if not levels:
        warnings_out.append(
            f"{column!r} has role ordinal but no ordinal_levels declared; unchanged"
        )
        return series
    # as_str(series) gives the string form each value compares as (integral floats via Int64,
    # P3, so a float64-coded 1.0 reads as "1", matching a declared level "1" instead of "1.0").
    # A present value whose string form is a declared level is kept; a null or an out-of-level
    # value becomes None. pandas 3.0 raises a Pandas4Warning (-> error under -W error) when a
    # Categorical is built with a dtype whose categories do not cover a non-null value -- the
    # old silent out-of-category -> NaN coercion is deprecated -- so the mapping to None must
    # happen here, before construction, not be left to the constructor. Not in P1-P7.
    allowed = set(levels)
    stringified = as_str(series)
    mapped = [s if (not pd.isna(s) and s in allowed) else None for s in stringified]
    return pd.Series(
        pd.Categorical(mapped, categories=list(levels), ordered=True), index=series.index
    )


def _cast_datetime(series: pd.Series, warnings_out: list[str], column: str) -> pd.Series:
    if _is_datetime_column(series):
        return series
    try:
        return pd.to_datetime(series, format="ISO8601", errors="coerce")
    except ValueError:
        warnings_out.append(f"{column!r} has mixed UTC offsets; parsed with utc=True")
        return pd.to_datetime(series, format="ISO8601", errors="coerce", utc=True)


def _cast_boolean(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series
    lowered = as_str(series).str.strip().str.lower()
    mapped = pd.Series(pd.NA, index=series.index, dtype="boolean")
    mapped[lowered.isin(_BOOLEAN_TRUE)] = True
    mapped[lowered.isin(_BOOLEAN_FALSE)] = False
    return mapped


def _effective_roles(
    frame_columns: set[str],
    roles: dict[str, ColumnRole],
    contract: DataContract | None,
    target: str,
    warnings_out: list[str],
) -> dict[str, tuple[ColumnRole, str]]:
    """`roles` (source `agent`) overlaid by the snapshot's `features.roles` (source `contract`).

    A role for a column not in the frame is `tool_param` on `roles` when it came from the agent;
    it is ignored with a warning when it came from the contract (`validate_contract` already
    reports it). An agent role for the `target` is ignored with a warning (D-e: the target is
    never transformed; its dtype is governed by contract `target.type`, not roles). The contract
    can never name the target in `features.roles` -- its own validator (V-roles) forbids it -- so
    only the agent side needs the guard. Returns `{col: (role, source)}` for feature columns in
    the frame.
    """
    unknown_agent = [col for col in roles if col != target and col not in frame_columns]
    if unknown_agent:
        raise ToolParamError(
            f"roles name columns not in the frame: {unknown_agent!r}",
            field_errors={"roles": f"not columns in the frame: {unknown_agent!r}"},
        )
    if target in roles:
        warnings_out.append(
            f"role for target {target!r} ignored: the target's dtype is governed by contract "
            f"target.type, not roles"
        )
    effective: dict[str, tuple[ColumnRole, str]] = {
        col: (role, "agent") for col, role in roles.items() if col != target
    }
    if contract is not None:
        for col, role in contract.features.roles.items():
            if col not in frame_columns:
                warnings_out.append(f"contract role for {col!r} ignored: not a column in the frame")
                continue
            effective[col] = (role, "contract")
    return effective


@tool(phase="cleaning", allow_in=_CAST_PHASES)
def cast_dtypes(
    paths: RunPaths,
    frame: _Frame,
    target: _Target,
    roles: Annotated[
        dict[str, ColumnRole],
        Field(description="Per-column roles, normally infer_column_roles' output."),
    ] = _NO_ROLES,
) -> ToolSuccess:
    """Cast columns to the dtype their role implies. Pre-split and stateless (D-b): a cast learns
    nothing from the data, so doing it once before the split is leak-free and test never needs it
    replayed.

    The effective roles are `roles` overlaid by the contract snapshot's `features.roles` -- every
    role the contract declares overrides the agent's, and the summary says which role came from
    where. Columns with no role are unchanged. A role for a column not in the frame is an error
    when it came from the agent; a contract role for a missing column is ignored with a warning.
    The `target` is never cast (D-e): an agent role for it is ignored with a warning, since the
    target's dtype is governed by contract `target.type`, not roles (the contract's own validator
    forbids naming the target in `features.roles`).
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    contract: DataContract | None = None
    if paths.contract_snapshot_json.exists():
        contract = DataContract.model_validate_json(paths.contract_snapshot_json.read_text())

    warnings_out: list[str] = []
    effective = _effective_roles(set(data.columns), roles, contract, target, warnings_out)

    columns_summary: dict[str, JsonValue] = {}
    for column, (role, source) in effective.items():
        before = data[column]
        from_dtype = str(before.dtype)
        if role == "numeric":
            after = _cast_numeric(before)
        elif role in ("categorical", "text"):
            after = _cast_string(before)
        elif role == "ordinal":
            levels = (
                contract.features.ordinal_levels.get(column, ()) if contract is not None else ()
            )
            after = _cast_ordinal(before, levels, warnings_out, column)
        elif role == "datetime":
            after = _cast_datetime(before, warnings_out, column)
        else:  # boolean
            after = _cast_boolean(before)
        if str(after.dtype) == from_dtype and after.equals(before):
            continue
        data[column] = after
        newly_null = int((before.notna() & after.isna()).sum())
        columns_summary[str(column)] = {
            "role": role,
            "source": source,
            "from": from_dtype,
            "to": str(after.dtype),
            "newly_null": json_scalar(newly_null),
        }
        if newly_null > 0:
            warnings_out.append(f"{column!r}: {newly_null} value(s) became null after the cast")

    summary: dict[str, JsonValue] = {"columns": columns_summary}
    return ToolSuccess(
        summary=summary,
        artifacts=(save_frame(paths, data, "typed"),),
        warnings=tuple(warnings_out),
    )


# --- 3.2 decide on train -------------------------------------------------------------------------

_Train = Annotated[ArtifactRef, Field(description="The train split frame; never test-derived.")]


def _feature_columns(train: pd.DataFrame, target: str) -> list[str]:
    """Every column except `target` (D-e: the target is never transformed). The target need not
    be present -- a decider on a frame without it just excludes nothing."""
    return [col for col in train.columns if col != target]


@tool(phase="cleaning", allow_in=["preprocessing"])
def drop_constant_columns(paths: RunPaths, train: _Train, target: _Target) -> ToolSuccess:
    """Drop feature columns with at most one distinct non-null value (an all-null column is
    constant). Decides on `train` (D-c); apply `dropped_columns` to valid and test with
    `drop_columns`, so every split keeps the same columns. The target is never dropped (D-e).
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    dropped = [col for col in _feature_columns(data, target) if data[col].nunique(dropna=True) <= 1]
    reduced = data.drop(columns=dropped)
    summary: dict[str, JsonValue] = {"dropped_columns": json_list(dropped)}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, reduced, "columns_dropped"),))


@tool(phase="cleaning", allow_in=["preprocessing"])
def drop_high_missing_columns(
    paths: RunPaths,
    train: _Train,
    threshold: Annotated[
        float,
        Field(gt=0, le=1, description="Drop columns whose null share is strictly above this."),
    ],
    target: _Target,
) -> ToolSuccess:
    """Drop feature columns whose null share in `train` is strictly greater than `threshold`.
    Decides on `train` (D-c); apply `dropped_columns` to valid and test with `drop_columns`.
    The target is never dropped (D-e).
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    null_shares = {col: float(data[col].isna().mean()) for col in _feature_columns(data, target)}
    dropped = [col for col, share in null_shares.items() if share > threshold]
    reduced = data.drop(columns=dropped)
    summary: dict[str, JsonValue] = {
        "dropped_columns": json_list(dropped),
        "null_share": {str(col): json_scalar(null_shares[col]) for col in dropped},
    }
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, reduced, "columns_dropped"),))


def _imputable_by_statistic(series: pd.Series) -> bool:
    """`mean`/`median` need a numeric or datetime column."""
    return is_numeric_column(series) or _is_datetime_column(series)


def _resolve_strategy(
    column: str,
    series: pd.Series,
    default: ImputationStrategy,
    per_column: dict[str, ImputationStrategy],
    warnings_out: list[str],
) -> ImputationStrategy:
    """The effective strategy for one column, applying the fallback rules (D-spec 3.2).

    A column all-null in train becomes `drop` (it cannot be imputed), with a warning. An explicit
    `per_column` mean/median on a non-numeric, non-datetime column is `tool_param`. A mean/median
    default on such a column falls back to `most_frequent`.
    """
    if series.isna().all():
        warnings_out.append(f"{column!r} is entirely null in train; imputation strategy is drop")
        return "drop"
    if column in per_column:
        strategy = per_column[column]
        if strategy in ("mean", "median") and not _imputable_by_statistic(series):
            raise ToolParamError(
                f"per_column[{column!r}] = {strategy!r} needs a numeric or datetime column",
                field_errors={"per_column": f"{column!r}: {strategy!r} needs numeric/datetime"},
            )
        return strategy
    if default in ("mean", "median") and not _imputable_by_statistic(series):
        warnings_out.append(
            f"{column!r} is not numeric/datetime; default {default!r} falls back to most_frequent"
        )
        return "most_frequent"
    return default


def _fill_value(column: str, series: pd.Series, strategy: ImputationStrategy) -> object:
    """The fill learned from `train` for a non-`drop` strategy. `constant` on a dtype other than
    numeric or string is `tool_param` (P2: `Categorical`/`bool`/`datetime` cannot take a new
    value)."""
    if strategy == "mean":
        return (
            float(series.astype("float64").mean()) if is_numeric_column(series) else series.mean()
        )
    if strategy == "median":
        return (
            float(series.astype("float64").median())
            if is_numeric_column(series)
            else series.median()
        )
    if strategy == "most_frequent":
        return series.mode().iloc[0]
    # constant
    if is_numeric_column(series):
        return CONSTANT_NUMERIC_FILL
    if _is_str_column(series):
        return CONSTANT_STRING_FILL
    raise ToolParamError(
        f"constant imputation on {column!r} (dtype {series.dtype}) is unsupported",
        field_errors={"per_column": f"{column!r}: constant needs numeric or string dtype"},
    )


@tool(phase="cleaning", allow_in=["preprocessing"])
def fit_imputer(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    default: Annotated[
        ImputationStrategy, Field(description="Strategy for columns without a per_column entry.")
    ] = "median",
    per_column: Annotated[
        dict[str, ImputationStrategy], Field(description="Per-column strategy overrides.")
    ] = _NO_PER_COLUMN,
) -> ToolSuccess:
    """Learn a fill for every feature column from `train` (D-c), saved as an imputer artifact.

    Learns a fill for every column except the target (D-e), not only those with nulls in train:
    valid or test can have nulls where train has none. A mean/median default on a non-numeric,
    non-datetime column falls back to `most_frequent` (recorded); an explicit `per_column`
    mean/median there is an error. A column entirely null in train becomes `drop`, with a warning.
    `apply_imputer` replays the learned fills on any frame; the fills are never recomputed there.
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    columns = _feature_columns(data, target)
    warnings_out: list[str] = []
    strategies: dict[str, str] = {}
    fills: dict[str, object] = {}
    dropped: list[str] = []
    for column in columns:
        strategy = _resolve_strategy(column, data[column], default, per_column, warnings_out)
        strategies[column] = strategy
        if strategy == "drop":
            dropped.append(column)
        else:
            fills[column] = _fill_value(column, data[column], strategy)

    imputer = FittedImputer(
        target=target,
        columns=tuple(columns),
        strategies=strategies,
        fills=fills,
        dropped=tuple(dropped),
    )
    ref = new_artifact_ref()
    meta = save_model(
        paths.artifact(ref, "transformer"), imputer, ref=ref, kind="transformer", label="imputer"
    )
    summary: dict[str, JsonValue] = {
        "strategies": {str(col): strategies[col] for col in columns},
        "fills": {str(col): json_scalar(fills[col]) for col in fills},
        "dropped": json_list(dropped),
        "fitted_rows": json_scalar(len(data)),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,), warnings=tuple(warnings_out))


@tool(phase="cleaning", allow_in=["preprocessing"])
def fit_outlier_clipper(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    method: Annotated[
        Literal["clip_iqr", "winsorize", "flag_only"], Field(description="How bounds are computed.")
    ],
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Numeric feature columns to fit; None uses every numeric feature."),
    ] = None,
) -> ToolSuccess:
    """Learn outlier bounds per numeric feature column from `train` (D-c), saved as a clipper
    artifact. `clip_iqr`/`flag_only` use Tukey's 1.5*IQR fence; `winsorize` the 1st/99th
    percentiles. A column whose bounds would be equal (constant, or IQR 0) is skipped with a
    warning (chunk 09's P6: a zero-width fence flags everything or nothing). The target is never
    clipped (D-e). `apply_outlier_clipper` replays the learned bounds; they are never recomputed.
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    if columns is not None:
        require_columns(data, columns, "columns")
        bad = [col for col in columns if col == target or not is_numeric_column(data[col])]
        if bad:
            raise ToolParamError(
                f"columns {bad!r} are the target or non-numeric",
                field_errors={"columns": f"must be numeric features, not: {bad!r}"},
            )
        candidates = list(columns)
    else:
        candidates = [col for col in _feature_columns(data, target) if is_numeric_column(data[col])]

    warnings_out: list[str] = []
    bounds: dict[str, tuple[float, float]] = {}
    skipped: list[str] = []
    for column in candidates:
        values = data[column].dropna()
        if method == "winsorize":
            lower = float(values.quantile(WINSORIZE_LOWER))
            upper = float(values.quantile(WINSORIZE_UPPER))
        else:
            q1 = float(values.quantile(0.25))
            q3 = float(values.quantile(0.75))
            iqr = q3 - q1
            lower = q1 - IQR_FENCE * iqr
            upper = q3 + IQR_FENCE * iqr
        if lower >= upper:
            skipped.append(column)
            warnings_out.append(f"{column!r} has zero-width bounds; skipped")
            continue
        bounds[column] = (lower, upper)

    clipper = FittedClipper(target=target, method=method, bounds=bounds)
    ref = new_artifact_ref()
    meta = save_model(
        paths.artifact(ref, "transformer"),
        clipper,
        ref=ref,
        kind="transformer",
        label="outlier_clipper",
    )
    summary: dict[str, JsonValue] = {
        "method": method,
        "bounds": {
            str(col): [json_scalar(lo), json_scalar(hi)] for col, (lo, hi) in bounds.items()
        },
        "skipped": json_list(skipped),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,), warnings=tuple(warnings_out))


# --- 3.3 apply -----------------------------------------------------------------------------------


@tool(phase="cleaning", allow_in=_DROP_COLUMNS_PHASES)
def drop_columns(
    paths: RunPaths,
    frame: _Frame,
    columns: Annotated[
        tuple[str, ...], Field(min_length=1, description="Columns to drop; each must exist.")
    ],
) -> ToolSuccess:
    """Drop named columns from any frame. The apply half of the two column deciders: pass their
    `dropped_columns` here on valid and test. An unknown column is an error.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    require_columns(data, columns, "columns")
    reduced = data.drop(columns=list(columns))
    summary: dict[str, JsonValue] = {"dropped_columns": json_list(columns)}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, reduced, "columns_dropped"),))


def _check_fitted_columns(
    frame: pd.DataFrame, fitted_columns: tuple[str, ...], target: str
) -> None:
    """The frame's columns minus the fitted target must equal `fitted_columns` (D-e: the target
    may be present or absent). A missing or unseen column is `tool_param` on `frame`."""
    present = {col for col in frame.columns if col != target}
    missing = [col for col in fitted_columns if col not in present]
    unseen = [col for col in present if col not in fitted_columns]
    if missing or unseen:
        raise ToolParamError(
            f"frame columns do not match the fitted columns (missing={missing!r}, "
            f"unseen={unseen!r})",
            field_errors={"frame": f"missing={missing!r}, unseen={unseen!r}"},
        )


@tool(phase="cleaning", allow_in=_APPLY_PHASES)
def apply_imputer(
    paths: RunPaths,
    imputer: Annotated[ArtifactRef, Field(description="A fitted imputer from fit_imputer.")],
    frame: _Frame,
) -> ToolSuccess:
    """Fill a frame's nulls with the imputer's learned values, and drop its `drop` columns. The
    fills come only from the imputer (learned on train) -- nothing is recomputed from this frame,
    which is what keeps the fit leak-free. Works whether the target is present or absent (D-e).
    """
    fitted = load_fitted(paths, imputer, FittedImputer, "imputer")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    _check_fitted_columns(data, fitted.columns, fitted.target)

    filled: dict[str, JsonValue] = {}
    for column in fitted.columns:
        strategy = fitted.strategies[column]
        if strategy == "drop":
            continue
        before_null = int(data[column].isna().sum())
        if strategy in ("mean", "median") and is_numeric_column(data[column]):
            data[column] = data[column].astype("float64")  # P1
        data[column] = data[column].fillna(cast("float | str | pd.Timestamp", fitted.fills[column]))
        if before_null > 0:
            filled[str(column)] = json_scalar(before_null)
    if fitted.dropped:
        data = data.drop(columns=[col for col in fitted.dropped if col in data.columns])

    summary: dict[str, JsonValue] = {"filled": filled}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, data, "imputed"),))


@tool(phase="cleaning", allow_in=_APPLY_PHASES)
def apply_outlier_clipper(
    paths: RunPaths,
    clipper: Annotated[
        ArtifactRef, Field(description="A fitted clipper from fit_outlier_clipper.")
    ],
    frame: _Frame,
) -> ToolSuccess:
    """Clip (or flag) a frame's outliers using the clipper's learned bounds. The bounds come only
    from the clipper (learned on train); nothing is recomputed here. `clip_iqr`/`winsorize` cast
    the column to `float64` before clipping (P1: clipping an Int64 column to a fractional bound
    raises; done on every frame so dtypes agree across splits) and NaN survives (P7); `flag_only`
    adds a plain-`bool` `{col}__outlier` column (a null is flagged False) and leaves values
    unchanged. Works whether the target is present or absent (D-e).
    """
    fitted = load_fitted(paths, clipper, FittedClipper, "clipper")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    require_columns(data, tuple(fitted.bounds), "frame")

    if fitted.method == "flag_only":
        collisions = [
            f"{col}__outlier" for col in fitted.bounds if f"{col}__outlier" in data.columns
        ]
        if collisions:
            raise ToolParamError(
                f"flag columns already exist: {collisions!r}",
                field_errors={"frame": f"columns already present: {collisions!r}"},
            )

    affected: dict[str, JsonValue] = {}
    for column, (lower, upper) in fitted.bounds.items():
        series = data[column]
        outside = int(((series < lower) | (series > upper)).sum())
        if fitted.method == "flag_only":
            # A plain bool column, never a nullable BooleanDtype: a null value is outside no
            # bound, so flag it False (D-spec 3.3 says a bool column).
            data[f"{column}__outlier"] = (
                ((series < lower) | (series > upper)).fillna(False).astype(bool)
            )
        else:
            # Cast to float64 before clipping (P1 again): clipping an Int64 column to a
            # fractional bound raises TypeError. On every frame, so dtypes agree across splits
            # (same rule as apply_imputer's mean/median fills).
            data[column] = series.astype("float64").clip(lower=lower, upper=upper)
        if outside > 0:
            affected[str(column)] = json_scalar(outside)

    label = "outliers_flagged" if fitted.method == "flag_only" else "clipped"
    summary: dict[str, JsonValue] = {"affected": affected}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, data, label),))
