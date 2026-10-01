"""Shared, pure helpers used by three or more tool modules: JSON conversion and the one numeric
predicate (D-a of `plan/09_tools_profiling.md`). Underscore-private -- these are plain functions
with nothing to say to the agent, not tools, and `tools/__init__.py` does not import this module.

`iso8601_parse_share` and `CLASS_TARGET_MAX_LEVELS` moved here from `intake.py` unchanged
(§3.1): both are needed again by this chunk's `summarize_target`/outlier-adjacent logic, and a
second, slightly different definition would let `infer_column_roles` and `summarize_target`
disagree about what "parses as a date" or "looks like a class target" means.

pandas/numpy facts this module exists because of (`00_index.md`, this chunk's own §1): `np.int64`
does not subclass `int`, so every count a pandas reduction produces (`Series.count()`, `.sum()`,
`value_counts()` values, `.iloc[i]` on an integer column) fails `ToolSuccess`'s `JsonValue`
validation unless converted; `np.bool_` does not subclass `bool`, so it needs its own branch
before any integer check runs -- and `bool` DOES subclass `int`, so the bool check must come
first regardless of which value is being converted. `NaN`/`inf` pass `JsonValue` validation but
`json.dumps(..., allow_nan=False)` (this chunk's `save_json` fix) then rejects them, so they must
become `None` explicitly rather than survive to the json-dump boundary. `is_numeric_dtype(bool)`
is `True`, but `select_dtypes("number")` excludes `bool` -- two different pandas-provided
"numeric" tests that disagree with each other, so this module provides the one predicate every
tool must use instead of either.
"""

from __future__ import annotations

import datetime
import math
from collections.abc import Iterable
from typing import Final

import numpy as np
import pandas as pd
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import load_model, save_dataframe
from statlab_tools.tool import ToolParamError

CLASS_TARGET_MAX_LEVELS: Final = 20
"""A target (or target-shaped column) with at most this many distinct values is treated as
class-shaped rather than continuous. Moved from `intake.py` unchanged -- see that module's
`detect_leaky_columns` and this chunk's `summarize_target`."""


def is_numeric_column(series: pd.Series) -> bool:
    """The one numeric predicate every tool in this chunk uses (P4). Numeric dtype and not
    bool -- `pd.api.types.is_numeric_dtype` alone says `True` for a bool column, which is not
    what any statistic in this chunk means by "numeric"."""
    return bool(pd.api.types.is_numeric_dtype(series)) and not pd.api.types.is_bool_dtype(series)


def is_class_target(series: pd.Series) -> bool:
    """A class target unless numeric with more than CLASS_TARGET_MAX_LEVELS distinct non-null
    values (summarize_target's rule, now shared). `nunique()` already excludes nulls, so this
    matches both of profiling.py's former inline copies exactly."""
    return not is_numeric_column(series) or series.nunique() <= CLASS_TARGET_MAX_LEVELS


def observed_counts(series: pd.Series) -> pd.Series:
    """`value_counts()` without the zero-count unused categories a categorical reports.

    A categorical's `value_counts()` lists every declared category, including ones no row uses,
    with a count of `0` (`nunique`/`unique` do not); counting levels off that bare result treats
    an unused category as a present level. Every level-counting site uses this instead -- the
    measured consequence of not doing so was a split refusing a categorical target over a class
    with 0 rows that no row ever had, and `summarize_class_target` reporting `minority_share`
    `0.0` for a 70/30 target with one unused category."""
    counts = series.value_counts()
    return counts[counts > 0]


def iso8601_parse_share(values: pd.Series) -> float:
    """Share of `values`' non-null entries that parse as ISO 8601. `0.0` when there are no
    non-null values. Moved from `intake.py` unchanged (§3.1) -- behaviour must not change, so
    chunk 08's tests keep passing without edits."""
    non_null = values.dropna()
    if non_null.empty:
        return 0.0
    parsed = pd.to_datetime(non_null, format="ISO8601", errors="coerce")
    return float(parsed.notna().mean())


def json_scalar(value: object) -> JsonValue:
    """Convert one pandas/numpy cell or reduction result into a strict-JSON value.

    Order matters: `np.bool_` is checked before any integer branch, because `bool` (the
    builtin) is itself an `int` subclass but `np.bool_` is not a `bool` subclass -- swapping
    the order would let a `np.bool_` fall through to the integer branch and come back as `0`
    or `1` instead of `False`/`True`. `np.floating` (covers `np.float64`, `np.float32`, ...) is
    checked for non-finite values explicitly, because `float("nan")`/`inf` pass `JsonValue`
    validation but are not valid strict JSON (P2/P3) -- they become `None` here rather than
    surviving to `save_json`'s `allow_nan=False` boundary, where they would raise instead of
    silently becoming `null`.
    """
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        as_float = float(value)
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        return as_float
    if isinstance(value, int):
        return value
    if isinstance(value, (pd.Timestamp, datetime.date)):
        # `datetime.date` alone is sufficient: `datetime.datetime` subclasses `date`, and
        # `pd.Timestamp` subclasses `datetime.datetime` (verified against the installed
        # pandas) -- but `pd.Timestamp` is named explicitly anyway, since relying on a
        # third-party class's inheritance from a stdlib one to stay true is a fact worth
        # stating rather than leaving implicit.
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise TypeError(f"json_scalar: unsupported type {type(value).__name__!r} for value {value!r}")


def json_list(values: Iterable[object]) -> list[JsonValue]:
    """`json_scalar`, mapped over an iterable. For a pandas index or series' `.tolist()`."""
    return [json_scalar(v) for v in values]


def as_str(series: pd.Series) -> pd.Series:
    """Stringify, routing an all-integral numeric column through `Int64` first, so `2.0` becomes
    `"2"`, not `"2.0"`; NaN stays NaN. Shared by every cast that compares string forms (chunk 11's
    categorical/text, ordinal levels, boolean tokens; chunk 12's ordinal encoder): integer-coded
    columns with a null load as `float64`, so without the Int64 route every such value stringifies
    as `"1.0"` and silently falls outside the levels/token map (verified chunk 11, P3)."""
    if is_numeric_column(series):
        non_null = series.dropna()
        if len(non_null) > 0 and bool((non_null == non_null.round()).all()):
            return series.astype("Int64").astype("str")
    return series.astype("str")


def as_str_scalar(value: object) -> str:
    """The scalar twin of `as_str`: the string form of one class label or category level, agreeing
    with `as_str` element-for-element (`as_str(pd.Series([value])).iloc[0]` semantics). An integral
    float becomes `"1"` not `"1.0"`, a `bool` becomes `"True"`/`"False"`, a `str` passes through.

    Needed wherever a single label/level is turned into a column name or compared against an
    `as_str` column but no `Series` is in hand (chunk 14/15 class names, one-hot/target-encoder
    output names): a bare `str(1.0)` gives `"1.0"` while `as_str` gives `"1"`, so float-coded
    targets and levels (any integer-coded column with a null loads as `float64`) silently fall
    outside every comparison -- the same two-stringifications defect `as_str` was built to end."""
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if isinstance(value, (float, np.floating)):
        as_float = float(value)
        if math.isfinite(as_float) and as_float == int(as_float):
            return str(int(as_float))
        return str(as_float)
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    # Every other type (`str`, a datetime category level, ...) goes through the one-element
    # `as_str` path, which is the definition this helper agrees with -- a single source of truth.
    return str(as_str(pd.Series([value])).iloc[0])


def require_columns(frame: pd.DataFrame, names: tuple[str, ...], field: str) -> None:
    """A column name not in `frame` is `tool_param`, naming the missing ones under `field`."""
    missing = [name for name in names if name not in frame.columns]
    if missing:
        raise ToolParamError(
            f"columns {missing!r} are not in the frame",
            field_errors={field: f"not columns in the frame: {missing!r}"},
        )


def save_frame(paths: RunPaths, frame: pd.DataFrame, label: str) -> ArtifactMeta:
    """Mint a ref and save `frame` as kind `dataframe` with `label`."""
    ref = new_artifact_ref()
    return save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label=label)


def load_fitted[T](paths: RunPaths, ref: ArtifactRef, expected: type[T], field: str) -> T:
    """Load a fitted transformer and check its type. A wrong ref fails as `tool_param`, not an
    `AttributeError` deep in apply (chunk 11 D-d). Generic over any type via an unconstrained PEP
    695 `[T]`, so chunks 12-15 add their own fitted-state classes without editing a constraint
    tuple here (chunk 12 §3.1)."""
    obj = load_model(paths.artifact(ref, "transformer"))
    if not isinstance(obj, expected):
        raise ToolParamError(
            f"{field} is a {type(obj).__name__}, not a {expected.__name__}",
            field_errors={field: f"expected a {expected.__name__}"},
        )
    return obj
