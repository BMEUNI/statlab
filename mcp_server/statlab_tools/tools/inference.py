"""The eleven inference tools (`PLAN.md` §6.3's `inference` phase, this chunk's
`15_tools_inference.md`): the two matrix tools (`build_dummy_matrix`, `assemble_inference_matrix`)
and the nine statsmodels tools (`fit_ols`, `fit_logit`, `fit_glm`, `coefficient_table`,
`inferential_fit_summary`, `compute_vif`, `test_residuals`, `compute_influence`,
`plot_residuals`). This is the D7 second track: node 6 (`inferential_analysis`) runs it once per
run on train+valid (D31), and D35 (confirmatory only where pre-registered, Holm across goals)
becomes code in `coefficient_table`.

statsmodels is untyped here (`ignore_missing_imports`), imported lazily inside tool bodies so the
package import stays scipy-free (chunk 09's `test_s11`, chunk 12's lazy-import fact). A fitted
`results` object and every array it produces are implicit dynamic values; they are kept behind
`object`-typed fields (`FittedInference.result`) and narrowed at the boundary (`np.asarray(...)`,
`float(...)`), so no dynamic type leaks and no new `# type: ignore` is needed -- the same pattern
`training.py` and `evaluation.py` use for sklearn.

statsmodels facts this module is written against (verified under `-W error`, the spec's §1):

- P1: OLS reproduces the NIST StRD Longley certified coefficients to relative 1e-11.
- P3: **perfect separation does not raise in production** -- statsmodels installs its own
  `"always"` warning filter at import, so Logit emits `PerfectSeparationWarning` and returns
  `mle_retvals["converged"] == False` with meaningless coefficients. But pytest's per-test
  `filterwarnings=error` turns that warning into an exception. Both paths are handled: catch
  `PerfectSeparationWarning`/`ConvergenceWarning` raised as exceptions **and** check `converged`,
  both mapping to the same `ToolDataError`. `warnings.catch_warnings` is never used -- it is
  process-global and tool bodies run in worker threads (same as `training.py`).
- P6: **patsy formulas execute arbitrary Python**, so no formula strings anywhere (D-a). Fits take
  `(matrix, target, predictors)` and add the constant themselves.
- Gamma's canonical link (InversePower) raises a `DomainWarning` at construction, fatal under
  `-W error`; `fit_glm` uses the domain-safe **log link** for both GLM families (Poisson's
  canonical link is log anyway), which keeps coefficients multiplicative in original units (D31).
  `ponytail:` the log link is the standard domain-respecting Gamma choice; swapping to the
  canonical InversePower would need a `warnings` suppression this project forbids.
- The QQ plot uses stdlib `statistics.NormalDist().inv_cdf`, not scipy, and Durbin-Watson has no
  p-value.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Annotated, Final, Literal, cast

import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract, InferenceGoal
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import (
    load_dataframe,
    load_json,
    load_model,
    save_json,
    save_model,
    save_plot,
)
from statlab_tools.tool import ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import (
    as_str,
    as_str_scalar,
    is_numeric_column,
    json_scalar,
    observed_counts,
    require_columns,
    save_frame,
)

_INFERENCE_PHASES: Final = ("inferential_analysis",)

MISSING_INDICATOR_SHARE: Final = 0.05
"""A predictor missing on at least this share of the concatenated train+valid rows gets a
`f"{col}__missing"` 0/1 indicator before imputation (D-d step 3). Below it, the imputed value
carries no separate signal worth a column."""

VIF_WARN: Final = 10.0
"""A predictor whose variance-inflation factor exceeds this is `flagged` (§3.4). The usual
rule-of-thumb threshold for serious multicollinearity."""

INFLUENCE_TOP_K: Final = 10
"""`compute_influence` reports at most this many rows in its `top`, sorted by Cook's distance."""

_CI_ALPHA: Final = 0.05
"""The coefficient table reports a 95 % CI (§3.4): `conf_int(alpha=0.05)`."""


_Frame = Annotated[ArtifactRef, Field(description="A dataframe artifact.")]
_Train = Annotated[ArtifactRef, Field(description="The train split frame (node 6 uses train).")]
_Valid = Annotated[ArtifactRef, Field(description="The valid split frame (node 6 uses valid).")]
_Matrix = Annotated[
    ArtifactRef, Field(description="An inference matrix from a matrix tool; numeric/bool, no NaN.")
]
_Fit = Annotated[ArtifactRef, Field(description="A fitted inference model from a fit_* tool.")]
_Manifest = Annotated[ArtifactRef, Field(description="The matrix manifest table (D-c).")]
_Target = Annotated[str, Field(min_length=1, description="The target column.")]
_Columns = Annotated[
    tuple[str, ...], Field(min_length=1, description="Non-numeric columns to dummy-code.")
]
_Predictors = Annotated[
    tuple[str, ...], Field(min_length=1, description="The predictor source columns.")
]
_FitPredictors = Annotated[
    tuple[str, ...] | None,
    Field(description="Predictor columns; None means every non-target column."),
]
_ReferenceLevels = Annotated[
    dict[str, str],
    Field(description="Per-column reference level to drop; default is the most frequent."),
]
_Goals = Annotated[
    tuple[InferenceGoal, ...],
    Field(description="Pre-registered inference goals; a term is confirmatory iff in one (D35)."),
]

_NO_REFERENCES: dict[str, str] = {}
"""Shared empty default for `reference_levels`; never mutated (ruff B006)."""
_NO_GOALS: tuple[InferenceGoal, ...] = ()
"""Shared empty default for `goals`; never mutated."""


# --- fitted state --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedInference:
    """A fitted statsmodels results object plus everything the reporting tools need without
    reaching into statsmodels' untyped surface. `result` is typed `object` -- a statsmodels
    results object is a dynamic value (P7: it pickles and round-trips), narrowed only to call
    documented attributes. Frozen and plain so joblib round-trips it. `terms` is the design
    matrix's column order including `const`; `positive_class` is Logit's positive label (`None`
    for OLS/GLM)."""

    model: str
    target: str
    terms: tuple[str, ...]
    n_obs: int
    positive_class: str | None
    result: object
    classes: tuple[str, ...] = ()
    """For `mnlogit` only: the class labels, base first then the equation order (D-a, chunk 20r).
    Empty for every other model. The default keeps pre-20r pickles loading."""


# --- manifest ------------------------------------------------------------------------------------


def _empty_manifest() -> dict[str, JsonValue]:
    """A manifest with every section present and empty (D-c), accumulated by the builders."""
    return {
        "terms": {},
        "references": {},
        "imputed": [],
        "indicators": [],
        "dropped": [],
    }


def _str_level_counts(series: pd.Series) -> pd.Series:
    """`value_counts` over the **string form** of a column's observed levels (D-b). The string
    form must come from `as_str` (not `str(value)`): `as_str` routes an integral-valued numeric
    column through `Int64` so `1.0` becomes `"1"`, matching the dummy columns' comparison; a bare
    `str(1.0)` would give `"1.0"` and silently never match (the chunk 11/12 integer-string
    lesson). Unused categorical levels are dropped (`observed_counts`)."""
    return observed_counts(as_str(series))


def _dummy_code(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    reference_levels: dict[str, str],
    manifest: dict[str, JsonValue],
) -> pd.DataFrame:
    """Dummy-code each column in `columns` with an explicit reference (D-b): the reference level is
    `reference_levels[col]` if given (and observed, else `tool_param`), else the most frequent
    observed level (ties by first string order). The reference is dropped and recorded; the
    remaining levels become `float64` 0/1 columns `f"{col}__{level}"`. Other columns pass through.

    `bool` columns become `float64` 0/1 directly (one term, no reference). Numeric columns pass
    through unscaled (D31: coefficients stay in original units)."""
    terms = cast("dict[str, JsonValue]", manifest["terms"])
    references = cast("dict[str, JsonValue]", manifest["references"])
    out: dict[str, pd.Series] = {}
    for col in frame.columns:
        series = frame[col]
        if col not in columns:
            if pd.api.types.is_bool_dtype(series):
                out[col] = series.astype("float64")
            else:
                out[col] = series
            terms[col] = col
            continue
        levels = sorted(str(level) for level in _str_level_counts(series).index)
        reference = _reference_level(col, series, reference_levels)
        references[col] = reference
        coded = as_str(series)
        for level in levels:
            if level == reference:
                continue
            term = f"{col}__{level}"
            out[term] = (coded == level).astype("float64")
            terms[term] = col
    return pd.DataFrame(out, index=frame.index)


def _reference_level(col: str, series: pd.Series, reference_levels: dict[str, str]) -> str:
    """The reference level for `col` (D-b): the explicit one if given, else the most frequent
    observed level with ties broken by first string order. An explicit level not observed in the
    column is `tool_param`."""
    observed = {str(level) for level in _str_level_counts(series).index}
    if col in reference_levels:
        wanted = reference_levels[col]
        if wanted not in observed:
            raise ToolParamError(
                f"reference level {wanted!r} for column {col!r} is not observed; "
                f"observed levels: {sorted(observed)!r}",
                field_errors={"reference_levels": f"{col!r}: {wanted!r} not observed"},
            )
        return wanted
    counts = _str_level_counts(series)
    top = int(counts.max())
    tied = sorted(str(level) for level, n in counts.items() if int(n) == top)
    return tied[0]


def _save_manifest(paths: RunPaths, manifest: dict[str, JsonValue]) -> ArtifactMeta:
    """Write the manifest (D-c) as a `table` artifact."""
    ref = new_artifact_ref()
    return save_json(
        paths.artifact(ref, "table"), manifest, ref=ref, kind="table", label="inference_manifest"
    )


def _load_manifest(paths: RunPaths, manifest: ArtifactRef) -> dict[str, JsonValue]:
    """Load a manifest `table` artifact, or `tool_param` if it is not one."""
    loaded = load_json(paths.artifact(manifest, "table"))
    if not isinstance(loaded, dict) or "terms" not in loaded or "references" not in loaded:
        raise ToolParamError(
            "manifest is not an inference matrix manifest",
            field_errors={"manifest": "not a build_* manifest (no terms/references)"},
        )
    return loaded


def _load_frame(paths: RunPaths, frame: ArtifactRef) -> pd.DataFrame:
    """Load a dataframe artifact the one way the fit and matrix tools read frames."""
    return load_dataframe(paths.artifact(frame, "dataframe"))


def _save_matrix_and_manifest(
    paths: RunPaths, coded: pd.DataFrame, manifest: dict[str, JsonValue]
) -> ToolSuccess:
    """Write the matrix and manifest artifacts and build the summary shared by both builders."""
    matrix_meta = save_frame(paths, coded, "inference_matrix")
    manifest_meta = _save_manifest(paths, manifest)
    summary: dict[str, JsonValue] = {
        "n_obs": json_scalar(len(coded)),
        "terms": list(cast("dict[str, JsonValue]", manifest["terms"])),
        "dropped": manifest["dropped"],
        "indicators": manifest["indicators"],
        "references": manifest["references"],
    }
    return ToolSuccess(summary=summary, artifacts=(matrix_meta, manifest_meta))


# --- 3.2 build_dummy_matrix ----------------------------------------------------------------------


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def build_dummy_matrix(
    paths: RunPaths,
    frame: _Frame,
    target: _Target,
    columns: _Columns,
    reference_levels: _ReferenceLevels = _NO_REFERENCES,
) -> ToolSuccess:
    """Dummy-code `columns` of `frame` with an explicit reference per column (D-b), step 4 of the
    canonical recipe applied to any frame.

    An unknown column, the target appearing in `columns`, or a reference level not observed is
    `tool_param`. A NaN in a column being coded is `tool_param` ("impute first") -- this tool does
    no imputation, unlike `assemble_inference_matrix`. The reference level is
    `reference_levels[col]` if given, else the most frequent observed level (ties by string
    order); it is dropped and recorded. `bool` columns become `float64` 0/1; numeric columns pass
    through unscaled (D31). Writes two artifacts: the matrix (label `inference_matrix`) and the
    manifest (D-c, label `inference_manifest`).
    """
    data = _load_frame(paths, frame)
    require_columns(data, (target, *columns), "columns")
    if target in columns:
        raise ToolParamError(
            f"target {target!r} must not be in columns",
            field_errors={"columns": f"the target {target!r} cannot be dummy-coded"},
        )
    for col in columns:
        if bool(data[col].isna().any()):
            raise ToolParamError(
                f"column {col!r} has missing values; impute first "
                f"(or use assemble_inference_matrix)",
                field_errors={"columns": f"{col!r} has NaN; coded columns must be complete"},
            )
    manifest = _empty_manifest()
    coded = _dummy_code(data, columns, reference_levels, manifest)
    return _save_matrix_and_manifest(paths, coded, manifest)


# --- 3.2 assemble_inference_matrix ---------------------------------------------------------------


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def assemble_inference_matrix(
    paths: RunPaths,
    train: _Train,
    valid: _Valid,
    target: _Target,
    predictors: _Predictors,
    reference_levels: _ReferenceLevels = _NO_REFERENCES,
) -> ToolSuccess:
    """Node 6's canonical inference recipe (D31, D-d): concatenate train+valid (never test), impute
    median/mode on the concatenation, add missing-indicators at a 5 % share, dummy-code with
    explicit references (D-b), then drop constant and perfectly collinear columns.

    `train`/`valid` are frames; overlapping labels across the two are `tool_param` (concatenation
    would double-count a row). The output carries only `predictors`' terms (plus any `__missing`
    indicators) and the target, with no NaN. A numeric predictor imputes its median; a non-numeric
    predictor imputes its mode. A predictor missing on at least `MISSING_INDICATOR_SHARE` of the
    concatenated rows gets a `f"{col}__missing"` 0/1 indicator. Columns are then added one at a
    time and any column that does not raise the matrix's rank (perfectly collinear, e.g. a dummy
    trap) is dropped with a warning; constant columns are dropped first. Writes the two artifacts;
    the summary carries `n_obs`, `terms`, `dropped`, `indicators`, `references`.
    """
    train_df = _load_frame(paths, train)
    valid_df = _load_frame(paths, valid)
    require_columns(train_df, (target, *predictors), "predictors")
    require_columns(valid_df, (target, *predictors), "predictors")
    overlap = sorted(str(x) for x in set(train_df.index) & set(valid_df.index))
    if overlap:
        raise ToolParamError(
            f"train and valid share row labels {overlap[:10]!r}; they would double-count",
            field_errors={"valid": f"{len(overlap)} labels overlap train"},
        )
    combined = pd.concat([train_df, valid_df], axis=0)
    selected = combined[[target, *predictors]].copy()

    manifest = _empty_manifest()
    indicators = cast("list[JsonValue]", manifest["indicators"])
    imputed = cast("list[JsonValue]", manifest["imputed"])
    n = len(selected)
    for col in predictors:
        missing = selected[col].isna()
        share = float(missing.mean()) if n else 0.0
        if share >= MISSING_INDICATOR_SHARE:
            selected[f"{col}__missing"] = missing.astype("float64")
            indicators.append(f"{col}__missing")
        if bool(missing.any()):
            fill, strategy = _impute_fill(selected[col])
            selected[col] = selected[col].fillna(fill)
            imputed.append({"column": col, "strategy": strategy, "fill": json_scalar(fill)})

    coded_targets = tuple(c for c in predictors if not is_numeric_column(selected[c]))
    coded = _dummy_code(selected, coded_targets, reference_levels, manifest)
    coded = _drop_constant_and_collinear(coded, target, manifest)
    dropped_entries = cast("list[dict[str, JsonValue]]", manifest["dropped"])
    warnings = tuple(f"dropped {d['term']!r}: {d['reason']}" for d in dropped_entries)
    result = _save_matrix_and_manifest(paths, coded, manifest)
    return ToolSuccess(summary=result.summary, artifacts=result.artifacts, warnings=warnings)


def _impute_fill(series: pd.Series) -> tuple[float | str, str]:
    """The imputation fill and its strategy name (D-d step 2): the median of a numeric column, else
    the most frequent observed level (ties by first string order)."""
    if is_numeric_column(series):
        return float(series.median()), "median"
    counts = observed_counts(series)
    top = int(counts.max())
    tied = sorted(str(level) for level, k in counts.items() if int(k) == top)
    return tied[0], "most_frequent"


def _drop_constant_and_collinear(
    coded: pd.DataFrame, target: str, manifest: dict[str, JsonValue]
) -> pd.DataFrame:
    """Drop constant columns, then perfectly collinear ones (D-d step 5). Non-target columns are
    added one at a time with an intercept already present; a column that does not raise the design
    matrix's rank is linearly dependent on those already kept and is dropped, each recorded in
    `dropped` with a reason. The target is never dropped. The `terms` manifest entries for dropped
    columns are removed so `coefficient_table` maps goals only to surviving terms."""
    dropped = cast("list[JsonValue]", manifest["dropped"])
    terms = cast("dict[str, JsonValue]", manifest["terms"])
    feature_cols = [c for c in coded.columns if c != target]

    kept: list[str] = []
    kept_matrix = np.ones((len(coded), 1), dtype="float64")  # the intercept the fits add
    for col in feature_cols:
        column = coded[col].to_numpy(dtype="float64")
        if float(column.std()) == 0.0:
            dropped.append({"term": col, "reason": "constant"})
            terms.pop(col, None)
            continue
        candidate = np.column_stack([kept_matrix, column])
        rank = int(np.linalg.matrix_rank(candidate))
        if rank < candidate.shape[1]:
            dropped.append({"term": col, "reason": "perfectly collinear"})
            terms.pop(col, None)
            continue
        kept.append(col)
        kept_matrix = candidate
    return coded[[target, *kept]]


# --- fits ----------------------------------------------------------------------------------------


def _fit_predictors(
    frame: pd.DataFrame, target: str, predictors: tuple[str, ...] | None
) -> list[str]:
    """The predictor columns for a fit: `predictors` if given, else every non-target column (D-a).
    Each named predictor must exist and not be the target, else `tool_param`."""
    if predictors is None:
        return [c for c in frame.columns if c != target]
    require_columns(frame, predictors, "predictors")
    if target in predictors:
        raise ToolParamError(
            f"target {target!r} must not be a predictor",
            field_errors={"predictors": f"{target!r} is the target"},
        )
    return list(predictors)


def _design(
    paths: RunPaths, matrix: ArtifactRef, target: str, predictors: tuple[str, ...] | None
) -> tuple[pd.DataFrame, np.ndarray, pd.Series, list[str]]:
    """Load the matrix and build the statsmodels design: `(frame, X_with_const, y, terms)`. Every
    predictor must be numeric or bool with no NaN (`tool_param`: use `assemble_inference_matrix`);
    `n_obs <= n_params + 1` is a `ToolDataError`. The constant is column 0, named `const`."""
    frame = _load_frame(paths, matrix)
    if target not in frame.columns:
        raise ToolParamError(
            f"target {target!r} is not in the matrix",
            field_errors={"target": f"not a column: {target!r}"},
        )
    predictor_cols = _fit_predictors(frame, target, predictors)
    bad = [
        c
        for c in predictor_cols
        if not (is_numeric_column(frame[c]) or pd.api.types.is_bool_dtype(frame[c]))
    ]
    if bad:
        raise ToolParamError(
            f"predictor columns {bad!r} are not numeric or bool; use assemble_inference_matrix",
            field_errors={"predictors": f"non-numeric/bool columns: {bad!r}"},
        )
    design = frame[predictor_cols].to_numpy(dtype="float64", na_value=np.nan)
    if bool(np.isnan(design).any()):
        raise ToolParamError(
            "the matrix has missing values; use assemble_inference_matrix to impute first",
            field_errors={"matrix": "missing values; impute first"},
        )
    n_params = design.shape[1] + 1  # + the constant
    if frame.shape[0] <= n_params + 1:
        raise ToolDataError(
            f"too few observations: {frame.shape[0]} rows for {n_params} parameters"
        )
    x = np.column_stack([np.ones(len(frame), dtype="float64"), design])
    terms = ["const", *predictor_cols]
    return frame, x, frame[target], terms


def _save_fit(paths: RunPaths, fitted: FittedInference) -> ArtifactMeta:
    """Persist a `FittedInference` as kind `inferential_fit`."""
    ref = new_artifact_ref()
    return save_model(
        paths.artifact(ref, "inferential_fit"),
        fitted,
        ref=ref,
        kind="inferential_fit",
        label=fitted.model,
    )


def _fit_summary(fitted: FittedInference, result: object) -> dict[str, JsonValue]:
    """The shared fit summary (§3.3): `{model, n_obs, n_params, converged, aic}`."""
    from statsmodels.base.model import Results

    res = cast(Results, result)
    return {
        "model": fitted.model,
        "n_obs": json_scalar(fitted.n_obs),
        "n_params": json_scalar(len(fitted.terms)),
        "converged": _converged(result),
        "aic": json_scalar(float(res.aic)),
    }


def _converged(result: object) -> bool:
    """Whether an MLE fit converged. OLS has no `mle_retvals` and is always "converged"; a Logit or
    GLM reports it in `mle_retvals["converged"]` (P3)."""
    retvals = getattr(result, "mle_retvals", None)
    if retvals is None:
        return True
    return bool(retvals.get("converged", True))


@tool(phase="inference", allow_in=_INFERENCE_PHASES, heavy=True)
def fit_ols(
    paths: RunPaths, matrix: _Matrix, target: _Target, predictors: _FitPredictors = None
) -> ToolSuccess:
    """Fit an ordinary least squares regression (`sm.OLS`) on `matrix` (§3.3). No formulas (D-a,
    P6): the design is `matrix`'s predictor columns plus an added constant.

    The target must be numeric. Every predictor must be numeric or bool with no NaN
    (`tool_param`: use `assemble_inference_matrix`); `n_obs <= n_params + 1` is a `ToolDataError`.
    Saves a `FittedInference` as kind `inferential_fit`; the summary carries `model`, `n_obs`,
    `n_params`, `converged` and `aic`.
    """
    frame, x, y, terms = _design(paths, matrix, target, predictors)
    if not is_numeric_column(y):
        raise ToolParamError(
            f"target {target!r} is not numeric; OLS needs a numeric target",
            field_errors={"target": "not numeric"},
        )
    import statsmodels.api as sm

    result = sm.OLS(y.to_numpy(dtype="float64"), x).fit()
    fitted = FittedInference(
        model="ols",
        target=target,
        terms=tuple(terms),
        n_obs=len(frame),
        positive_class=None,
        result=result,
    )
    meta = _save_fit(paths, fitted)
    return ToolSuccess(summary=_fit_summary(fitted, result), artifacts=(meta,))


@tool(phase="inference", allow_in=_INFERENCE_PHASES, heavy=True)
def fit_logit(
    paths: RunPaths, matrix: _Matrix, target: _Target, predictors: _FitPredictors = None
) -> ToolSuccess:
    """Fit a binary logistic regression (`sm.Logit(...).fit(disp=0)`) on `matrix` (§3.3). No
    formulas (D-a). The target must have exactly two levels; the positive class is the snapshot's
    `target.positive_class` by string form, else the second level in string order (D-f), and the
    summary records which.

    Perfect separation is **not** a crash in production -- statsmodels warns and returns
    `converged=False` (P3) -- so this catches that warning raised as an exception (pytest's
    `filterwarnings=error`) **and** checks `converged`, both becoming a `ToolDataError` naming
    non-identification. Every predictor must be numeric or bool with no NaN. Saves a
    `FittedInference`.
    """
    frame, x, y, terms = _design(paths, matrix, target, predictors)
    positive, source, y_bin = _binary_target(y, _snapshot(paths))
    import statsmodels.api as sm
    from statsmodels.tools.sm_exceptions import ConvergenceWarning, PerfectSeparationWarning

    try:
        result = sm.Logit(y_bin, x).fit(disp=0)
    except (PerfectSeparationWarning, ConvergenceWarning) as exc:
        raise ToolDataError(
            "perfect separation or non-convergence: coefficients not identified"
        ) from exc
    if not _converged(result):
        raise ToolDataError("perfect separation or non-convergence: coefficients not identified")
    fitted = FittedInference(
        model="logit",
        target=target,
        terms=tuple(terms),
        n_obs=len(frame),
        positive_class=positive,
        result=result,
    )
    meta = _save_fit(paths, fitted)
    summary = _fit_summary(fitted, result)
    summary["positive_class"] = positive
    summary["positive_class_source"] = source
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _binary_target(
    y: pd.Series, snapshot: DataContract | None
) -> tuple[str, Literal["contract", "default"], np.ndarray]:
    """The positive class, its source, and the 0/1 target for Logit (D-f). The target must have
    exactly two observed levels (`tool_param`). The positive class is the snapshot's
    `target.positive_class` by string form (`as_str_scalar`, the same rule the levels are
    stringified by) if it is one of the two; a contract `positive_class` matching neither level is
    `tool_data`, never a silent fallback. With no contract `positive_class`, the second level in
    string order, source `"default"`."""
    levels = sorted(str(level) for level in _str_level_counts(y).index)
    if len(levels) != 2:
        raise ToolParamError(
            f"logit needs a target with exactly two levels, got {len(levels)}: {levels!r}",
            field_errors={"target": f"{len(levels)} levels, expected 2"},
        )
    positive = levels[1]
    source: Literal["contract", "default"] = "default"
    if snapshot is not None and snapshot.target.positive_class is not None:
        wanted = as_str_scalar(snapshot.target.positive_class)
        if wanted not in levels:
            raise ToolDataError(
                f"contract positive_class {snapshot.target.positive_class!r} is not one of the "
                f"observed target levels {levels!r}"
            )
        positive = wanted
        source = "contract"
    y_bin = (as_str(y) == positive).astype("float64").to_numpy()
    return positive, source, y_bin


@tool(phase="inference", allow_in=_INFERENCE_PHASES, heavy=True)
def fit_mnlogit(
    paths: RunPaths, matrix: _Matrix, target: _Target, predictors: _FitPredictors = None
) -> ToolSuccess:
    """Fit a multinomial logistic regression (`sm.MNLogit`) on `matrix` (§3.3, chunk 20r). No
    formulas (D-a). The target must have at least three observed levels; exactly two is
    `tool_param` ("use fit_logit").

    The base (reference) class is chosen, not left to statsmodels' alphabetical default (D-a):
    the first observed label of the snapshot's `target.class_order` when set, else the most
    frequent observed class (ties by string order), matching chunk 15's reference-level rule.
    `y` is encoded as integer codes with the base coded 0 so statsmodels keeps that base; the
    equations are then the remaining classes in observed-count order (ties by string order).

    Separation does **not** warn as `PerfectSeparationWarning` here -- `exp` overflows, a
    `RuntimeWarning` fatal under `-W error`, and silent otherwise (P3). The fit runs inside
    `np.errstate(over="raise", invalid="raise", divide="raise")` (thread-local, unlike
    `warnings.catch_warnings`, so it is allowed), and a `FloatingPointError`, a
    `ConvergenceWarning`/`PerfectSeparationWarning` raised as an exception, or `converged == False`
    each become the same `ToolDataError`. Saves a `FittedInference` with `classes` (base first).
    """
    frame, x, y, terms = _design(paths, matrix, target, predictors)
    base, classes, codes = _mnlogit_codes(y, _snapshot(paths))
    import statsmodels.api as sm
    from statsmodels.tools.sm_exceptions import ConvergenceWarning, PerfectSeparationWarning

    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            result = sm.MNLogit(codes, x).fit(disp=0)
    except (FloatingPointError, PerfectSeparationWarning, ConvergenceWarning) as exc:
        raise ToolDataError(
            "perfect separation or non-convergence: coefficients not identified"
        ) from exc
    if not _converged(result):
        raise ToolDataError("perfect separation or non-convergence: coefficients not identified")
    fitted = FittedInference(
        model="mnlogit",
        target=target,
        terms=tuple(terms),
        n_obs=len(frame),
        positive_class=None,
        result=result,
        classes=classes,
    )
    meta = _save_fit(paths, fitted)
    summary = _fit_summary(fitted, result)
    summary["base_class"] = base
    summary["classes"] = list(classes)
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _mnlogit_codes(
    y: pd.Series, snapshot: DataContract | None
) -> tuple[str, tuple[str, ...], np.ndarray]:
    """The base class, the class tuple (base first then equation order), and the integer codes for
    MNLogit (D-a). The target must have >= 3 observed levels (`tool_param`; exactly 2 says use
    `fit_logit`). Levels are compared by `as_str`/`as_str_scalar` (B2). The base is the first
    observed label of the snapshot's `target.class_order` when set and observed, else the most
    frequent observed class (ties by first string order). The equations are the remaining classes
    in descending observed-count order (ties by string order). The base is coded 0 so statsmodels
    keeps it as the reference, and each equation class is coded 1..K-1 in that order."""
    counts = _str_level_counts(y)
    observed = sorted(str(level) for level in counts.index)
    if len(observed) < 3:
        raise ToolParamError(
            f"mnlogit needs a target with at least three levels, got {len(observed)}: "
            f"{observed!r}; use fit_logit for two",
            field_errors={"target": f"{len(observed)} levels, expected >= 3"},
        )

    def freq_order(labels: list[str]) -> list[str]:
        return sorted(labels, key=lambda lbl: (-int(counts[lbl]), lbl))

    base = freq_order(observed)[0]
    if snapshot is not None and snapshot.target.class_order is not None:
        wanted = as_str_scalar(snapshot.target.class_order[0])
        if wanted not in observed:
            raise ToolDataError(
                f"contract class_order[0] {snapshot.target.class_order[0]!r} is not one of the "
                f"observed target levels {observed!r}"
            )
        base = wanted
    equations = freq_order([lbl for lbl in observed if lbl != base])
    classes = (base, *equations)
    index = {label: code for code, label in enumerate(classes)}
    coded = as_str(y).map(index)
    codes = coded.to_numpy(dtype="int64")
    return base, classes, codes


@tool(phase="inference", allow_in=_INFERENCE_PHASES, heavy=True)
def fit_glm(
    paths: RunPaths,
    matrix: _Matrix,
    target: _Target,
    family: Annotated[
        Literal["poisson", "gamma"], Field(description="The GLM family: poisson or gamma.")
    ],
    predictors: _FitPredictors = None,
) -> ToolSuccess:
    """Fit a generalised linear model (`sm.GLM`) with a **log link** on `matrix` (§3.3). No
    formulas (D-a).

    `poisson` needs a non-negative integer target; `gamma` needs a strictly positive target --
    otherwise `tool_param`. The log link is used for both: Poisson's canonical link is log, and
    Gamma's canonical InversePower link raises a `DomainWarning` that is fatal under this project's
    `-W error`, so the domain-safe log link is used instead (coefficients stay multiplicative in
    original units, D31). Every predictor must be numeric or bool with no NaN. Saves a
    `FittedInference`.
    """
    frame, x, y, terms = _design(paths, matrix, target, predictors)
    if not is_numeric_column(y):
        raise ToolParamError(
            f"target {target!r} is not numeric; a GLM needs a numeric target",
            field_errors={"target": "not numeric"},
        )
    y_arr = y.to_numpy(dtype="float64")
    _check_glm_target(family, y_arr)
    import statsmodels.api as sm

    link = sm.families.links.Log()
    sm_family = (
        sm.families.Poisson(link=link) if family == "poisson" else sm.families.Gamma(link=link)
    )
    result = sm.GLM(y_arr, x, family=sm_family).fit()
    fitted = FittedInference(
        model=f"glm_{family}",
        target=target,
        terms=tuple(terms),
        n_obs=len(frame),
        positive_class=None,
        result=result,
    )
    meta = _save_fit(paths, fitted)
    return ToolSuccess(summary=_fit_summary(fitted, result), artifacts=(meta,))


def _check_glm_target(family: str, y: np.ndarray) -> None:
    """A Poisson target must be non-negative integers; a Gamma target strictly positive (§3.3).
    Otherwise `tool_param`."""
    if family == "poisson":
        if bool(np.any(y < 0)) or not bool(np.all(y == np.round(y))):
            raise ToolParamError(
                "poisson GLM needs a non-negative integer target",
                field_errors={"target": "not a non-negative integer count"},
            )
    elif bool(np.any(y <= 0)):
        raise ToolParamError(
            "gamma GLM needs a strictly positive target",
            field_errors={"target": "not strictly positive"},
        )


# --- loading a fit -------------------------------------------------------------------------------


def _load_fit(paths: RunPaths, fit: ArtifactRef) -> FittedInference:
    """Load a `FittedInference`, or `tool_param` if the artifact is not one."""
    obj = load_model(paths.artifact(fit, "inferential_fit"))
    if not isinstance(obj, FittedInference):
        raise ToolParamError(
            f"fit is a {type(obj).__name__}, not a fitted inference model",
            field_errors={"fit": "not an inferential_fit artifact"},
        )
    return obj


def _refuse_mnlogit_residuals(fitted: FittedInference, tool_name: str) -> None:
    """Residual-based diagnostics refuse an mnlogit fit (D-b): residuals have no single scale
    across K-1 equations, so Cook's distance, leverage and a residuals-vs-fitted plot are not
    well-defined on one axis.
    ponytail: the upgrade is per-equation (one-vs-base) response residuals -- one diagnostic set
    per non-base class -- which no caller needs in v1; deferred until a node asks for it."""
    if fitted.model == "mnlogit":
        raise ToolParamError(
            f"{tool_name} does not support mnlogit: residuals have no single scale across "
            f"equations",
            field_errors={"fit": "mnlogit residual diagnostics are not supported"},
        )


def _snapshot(paths: RunPaths) -> DataContract | None:
    """The contract snapshot, or `None` (tests calling bodies without one). Same read as
    `training.tuning_from_snapshot` / `evaluation._snapshot`."""
    if paths.contract_snapshot_json.exists():
        return DataContract.model_validate_json(paths.contract_snapshot_json.read_text())
    return None


# --- 3.4 coefficient_table -----------------------------------------------------------------------


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def coefficient_table(
    paths: RunPaths, fit: _Fit, manifest: _Manifest, goals: _Goals = _NO_GOALS
) -> ToolSuccess:
    """The coefficient table with per-term statistics and per-goal joint tests (§3.4, D-e, D35).

    A term's role is `confirmatory` if its source column (via the `manifest`) is in some goal's
    `predictors`, `intercept` for the constant, else `exploratory` (`p_note: "not pre-specified"`).
    Each goal gets **one joint Wald test** over all its terms (a three-level predictor is two
    dummies -- one question, one test). With more than one goal, Holm-adjusted p is added and each
    goal's `alpha` is applied to the **adjusted** p (D35). A goal whose predictors have no
    surviving terms is `untestable` (dropped as constant/collinear, or never a predictor) and
    excluded from Holm -- not an error. Writes a `table` artifact; the summary carries the goal
    rows plus `n_terms` and `n_confirmatory`.
    """
    fitted = _load_fit(paths, fit)
    manifest_data = _load_manifest(paths, manifest)
    term_to_source = cast("dict[str, JsonValue]", manifest_data["terms"])
    goal_columns = {col for goal in goals for col in goal.predictors}

    if fitted.model == "mnlogit":
        rows, n_confirmatory = _mnlogit_term_rows(fitted, term_to_source, goal_columns)
    else:
        rows, n_confirmatory = _single_equation_term_rows(fitted, term_to_source, goal_columns)

    goal_rows = _goal_rows(goals, fitted, term_to_source)
    payload: dict[str, JsonValue] = {
        "model": fitted.model,
        "terms": rows,
        "goals": goal_rows,
        "n_terms": json_scalar(len(fitted.terms)),
        "n_confirmatory": json_scalar(n_confirmatory),
    }
    if fitted.model == "mnlogit":
        payload["base_class"] = fitted.classes[0] if fitted.classes else None
        payload["classes"] = list(fitted.classes)
    meta = _save_table(paths, payload, "coefficient_table")
    summary: dict[str, JsonValue] = {
        "model": fitted.model,
        "goals": goal_rows,
        "n_terms": json_scalar(len(fitted.terms)),
        "n_confirmatory": json_scalar(n_confirmatory),
    }
    if fitted.model == "mnlogit":
        summary["base_class"] = fitted.classes[0] if fitted.classes else None
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _single_equation_term_rows(
    fitted: FittedInference, term_to_source: dict[str, JsonValue], goal_columns: set[str]
) -> tuple[list[JsonValue], int]:
    """Per-term rows for OLS/Logit/GLM: `params`/`bse`/`tvalues`/`pvalues` are 1-d over `terms`,
    `conf_int()` is `(p, 2)`."""
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    params = np.asarray(res.params, dtype="float64")
    bse = np.asarray(res.bse, dtype="float64")
    stats = np.asarray(res.tvalues, dtype="float64")
    pvals = np.asarray(res.pvalues, dtype="float64")
    conf = np.asarray(res.conf_int(alpha=_CI_ALPHA), dtype="float64")

    rows: list[JsonValue] = []
    n_confirmatory = 0
    for i, term in enumerate(fitted.terms):
        role, p_note = _term_role(term, term_to_source, goal_columns)
        if role == "confirmatory":
            n_confirmatory += 1
        rows.append(
            {
                "term": term,
                "source_column": _source_of(term, term_to_source),
                "role": role,
                "coef": json_scalar(float(params[i])),
                "se": json_scalar(float(bse[i])),
                "stat": json_scalar(float(stats[i])),
                "p": json_scalar(float(pvals[i])),
                "ci_low": json_scalar(float(conf[i, 0])),
                "ci_high": json_scalar(float(conf[i, 1])),
                "p_note": p_note,
            }
        )
    return rows, n_confirmatory


def _mnlogit_term_rows(
    fitted: FittedInference, term_to_source: dict[str, JsonValue], goal_columns: set[str]
) -> tuple[list[JsonValue], int]:
    """Per-(equation, term) rows for MNLogit (P1). `params`/`bse`/`tvalues`/`pvalues` are
    `(p, K-1)`; `conf_int()` is `(K-1, p, 2)`, so a term's CI in equation `j` is
    `conf_int()[j, t, :]` (NOT `[t, j]`). Each row carries its `equation` (the non-base class).
    `n_terms` counts distinct terms, so a term is `confirmatory` once per equation it appears in,
    counted once per row here only to keep parity with the single-equation branch; the caller's
    `n_terms` is `len(fitted.terms)` regardless."""
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    params = np.asarray(res.params, dtype="float64")  # (p, K-1)
    bse = np.asarray(res.bse, dtype="float64")
    stats = np.asarray(res.tvalues, dtype="float64")
    pvals = np.asarray(res.pvalues, dtype="float64")
    conf = np.asarray(res.conf_int(alpha=_CI_ALPHA), dtype="float64")  # (K-1, p, 2)
    equations = fitted.classes[1:]  # base is classes[0]

    rows: list[JsonValue] = []
    n_confirmatory = 0
    for j, equation in enumerate(equations):
        for t, term in enumerate(fitted.terms):
            role, p_note = _term_role(term, term_to_source, goal_columns)
            if role == "confirmatory":
                n_confirmatory += 1
            rows.append(
                {
                    "equation": equation,
                    "term": term,
                    "source_column": _source_of(term, term_to_source),
                    "role": role,
                    "coef": json_scalar(float(params[t, j])),
                    "se": json_scalar(float(bse[t, j])),
                    "stat": json_scalar(float(stats[t, j])),
                    "p": json_scalar(float(pvals[t, j])),
                    "ci_low": json_scalar(float(conf[j, t, 0])),
                    "ci_high": json_scalar(float(conf[j, t, 1])),
                    "p_note": p_note,
                }
            )
    return rows, n_confirmatory


def _source_of(term: str, term_to_source: dict[str, JsonValue]) -> str | None:
    """The source column of a term from the manifest, or `None` for the intercept."""
    if term == "const":
        return None
    source = term_to_source.get(term)
    return str(source) if source is not None else term


def _term_role(
    term: str, term_to_source: dict[str, JsonValue], goal_columns: set[str]
) -> tuple[str, str | None]:
    """A term's role (D-e): `intercept` for `const`; `confirmatory` if its source column is in a
    goal; else `exploratory` with `p_note: "not pre-specified"`."""
    if term == "const":
        return "intercept", None
    source = _source_of(term, term_to_source)
    if source is not None and source in goal_columns:
        return "confirmatory", None
    return "exploratory", "not pre-specified"


def _terms_for_goal(
    goal: InferenceGoal, fitted: FittedInference, term_to_source: dict[str, JsonValue]
) -> list[int]:
    """The design-matrix indices of the terms whose source column is one of the goal's predictors
    (D-e). Empty when every such column was dropped or was never a predictor."""
    wanted = set(goal.predictors)
    out: list[int] = []
    for i, term in enumerate(fitted.terms):
        source = _source_of(term, term_to_source)
        if source is not None and source in wanted:
            out.append(i)
    return out


def _goal_restriction(idx: list[int], n_terms: int, model: str, n_equations: int) -> np.ndarray:
    """The joint-Wald restriction matrix `R` for a goal's term indices.

    For a single-equation model (OLS/Logit/GLM) `R` is `(len(idx), n_terms)`, one row per term.

    For `mnlogit` the parameter vector `wald_test` sees is `params.ravel(order="F")` -- equation
    major, so the flat index of (term `t`, equation `j`) is `t + j*n_terms` (P2). A goal covers
    its terms in **every** equation, so `R` is `(len(idx) * n_equations, n_terms * n_equations)`
    with df = terms x (K-1). Building `R` on C-order (`j + t*n_equations`) or on the first equation
    only is wrong -- both are mutation targets in the DoD."""
    width = n_terms * n_equations
    rows: list[int] = []
    for j in range(n_equations):
        offset = j * n_terms if model == "mnlogit" else 0
        rows.extend(t + offset for t in idx)
    restriction = np.zeros((len(rows), width), dtype="float64")
    for row, col in enumerate(rows):
        restriction[row, col] = 1.0
    return restriction


def _goal_rows(
    goals: tuple[InferenceGoal, ...],
    fitted: FittedInference,
    term_to_source: dict[str, JsonValue],
) -> list[JsonValue]:
    """One row per goal (D-e): a joint Wald test over the goal's terms, Holm-adjusted across the
    testable goals when there is more than one, each goal's `alpha` applied to the adjusted p. An
    untestable goal (no surviving terms) is reported with `status="untestable"` and excluded from
    Holm."""
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    n_terms = len(fitted.terms)
    n_equations = max(len(fitted.classes) - 1, 1) if fitted.model == "mnlogit" else 1

    prepared: list[tuple[InferenceGoal, list[int]]] = [
        (goal, _terms_for_goal(goal, fitted, term_to_source)) for goal in goals
    ]
    testable = [(goal, idx) for goal, idx in prepared if idx]
    raw_p: list[float] = []
    stats: list[float] = []
    dfs: list[int] = []
    for _goal, idx in testable:
        restriction = _goal_restriction(idx, n_terms, fitted.model, n_equations)
        wald = res.wald_test(restriction, scalar=True)
        stats.append(float(wald.statistic))
        raw_p.append(float(wald.pvalue))
        dfs.append(restriction.shape[0])

    holm: list[float | None] = [None] * len(testable)
    if len(testable) > 1:
        from statsmodels.stats.multitest import multipletests

        _, adjusted, _, _ = multipletests(raw_p, method="holm")
        holm = [float(p) for p in np.asarray(adjusted, dtype="float64")]

    rows: list[JsonValue] = []
    testable_iter = iter(range(len(testable)))
    for _goal, idx in prepared:
        if not idx:
            rows.append(_untestable_row(_goal))
            continue
        j = next(testable_iter)
        p_holm = holm[j]
        decisive_p = p_holm if p_holm is not None else raw_p[j]
        rows.append(
            {
                "question": _goal.question,
                "predictors": list(_goal.predictors),
                "terms": [fitted.terms[k] for k in idx],
                "stat": json_scalar(stats[j]),
                "df": json_scalar(dfs[j]),
                "p_raw": json_scalar(raw_p[j]),
                "p_holm": json_scalar(p_holm),
                "alpha": json_scalar(_goal.alpha),
                "reject": bool(decisive_p < _goal.alpha),
                "status": "tested",
            }
        )
    return rows


def _untestable_row(goal: InferenceGoal) -> dict[str, JsonValue]:
    """A goal with no surviving terms (D-e): reported, excluded from Holm, not an error."""
    empty_terms: list[JsonValue] = []
    return {
        "question": goal.question,
        "predictors": list(goal.predictors),
        "terms": empty_terms,
        "stat": None,
        "df": None,
        "p_raw": None,
        "p_holm": None,
        "alpha": json_scalar(goal.alpha),
        "reject": False,
        "status": "untestable",
    }


# --- 3.4 inferential_fit_summary -----------------------------------------------------------------


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def inferential_fit_summary(paths: RunPaths, fit: _Fit) -> ToolSuccess:
    """Model-appropriate goodness-of-fit (§3.4). OLS: R², adjusted R², F and its p. Logit:
    McFadden pseudo-R², LLR and its p. GLM: `pseudo_rsquared()` and deviance. Every model: AIC,
    BIC, log-likelihood, `n_obs`, `df_model`, `converged`. No artifact."""
    fitted = _load_fit(paths, fit)
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    summary: dict[str, JsonValue] = {
        "model": fitted.model,
        "aic": json_scalar(float(res.aic)),
        "bic": json_scalar(float(res.bic)),
        "log_likelihood": json_scalar(float(res.llf)),
        "n_obs": json_scalar(fitted.n_obs),
        "df_model": json_scalar(float(res.df_model)),
        "converged": _converged(fitted.result),
    }
    if fitted.model == "ols":
        summary["rsquared"] = json_scalar(float(res.rsquared))
        summary["rsquared_adj"] = json_scalar(float(res.rsquared_adj))
        summary["f_statistic"] = json_scalar(float(res.fvalue))
        summary["f_pvalue"] = json_scalar(float(res.f_pvalue))
    elif fitted.model in ("logit", "mnlogit"):
        summary["pseudo_rsquared"] = json_scalar(float(res.prsquared))
        summary["llr"] = json_scalar(float(res.llr))
        summary["llr_pvalue"] = json_scalar(float(res.llr_pvalue))
        if fitted.model == "mnlogit":
            summary["base_class"] = fitted.classes[0] if fitted.classes else None
    else:
        summary["pseudo_rsquared"] = json_scalar(float(res.pseudo_rsquared()))
        summary["deviance"] = json_scalar(float(res.deviance))
    return ToolSuccess(summary=summary)


# --- 3.4 compute_vif -----------------------------------------------------------------------------


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def compute_vif(
    paths: RunPaths, matrix: _Matrix, target: _Target, predictors: _FitPredictors = None
) -> ToolSuccess:
    """Variance-inflation factor per predictor (§3.4), with the constant added for the computation
    and excluded from the output. A predictor is `flagged` when its VIF exceeds `VIF_WARN`. Writes
    a `table` artifact; the summary carries the per-predictor VIFs and the `flagged` list.
    """
    _frame, x, _y, terms = _design(paths, matrix, target, predictors)
    from statsmodels.stats.outliers_influence import variance_inflation_factor

    vifs: dict[str, JsonValue] = {}
    flagged: list[JsonValue] = []
    for i, term in enumerate(terms):
        if term == "const":
            continue
        value = float(variance_inflation_factor(x, i))
        vifs[term] = json_scalar(value)
        if value > VIF_WARN:
            flagged.append(term)
    payload: dict[str, JsonValue] = {"vif": vifs, "flagged": flagged, "threshold": VIF_WARN}
    meta = _save_table(paths, payload, "vif")
    return ToolSuccess(summary=payload, artifacts=(meta,))


# --- 3.4 test_residuals --------------------------------------------------------------------------


_ResidualTest = Literal["normality", "heteroskedasticity", "autocorrelation"]
_ALL_RESIDUAL_TESTS: Final[tuple[_ResidualTest, ...]] = (
    "normality",
    "heteroskedasticity",
    "autocorrelation",
)


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def test_residuals(
    paths: RunPaths,
    fit: _Fit,
    tests: Annotated[
        tuple[_ResidualTest, ...],
        Field(description="Which residual assumption tests to run; default is all three."),
    ] = _ALL_RESIDUAL_TESTS,
) -> ToolSuccess:
    """Residual assumption tests for an OLS fit (§3.4). **OLS only** -- another model is
    `tool_param`. `normality` is Jarque-Bera (statistic, p); `heteroskedasticity` is Breusch-Pagan
    (LM, p); `autocorrelation` is Durbin-Watson (statistic only -- it has no p-value and is
    meaningful only for ordered data, so the summary says so). No artifact.
    """
    fitted = _load_fit(paths, fit)
    if fitted.model != "ols":
        raise ToolParamError(
            f"test_residuals is OLS only; this fit is {fitted.model!r}",
            field_errors={"fit": f"not an OLS fit: {fitted.model!r}"},
        )
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    resid = np.asarray(res.resid, dtype="float64")
    results: dict[str, JsonValue] = {}
    if "normality" in tests:
        from statsmodels.stats.stattools import jarque_bera

        jb_stat, jb_p, _skew, _kurt = jarque_bera(resid)
        results["normality"] = {
            "test": "jarque_bera",
            "statistic": json_scalar(float(jb_stat)),
            "p": json_scalar(float(jb_p)),
        }
    if "heteroskedasticity" in tests:
        from statsmodels.stats.diagnostic import het_breuschpagan

        exog = np.asarray(res.model.exog, dtype="float64")
        lm, lm_p, _f, _f_p = het_breuschpagan(resid, exog)
        results["heteroskedasticity"] = {
            "test": "breusch_pagan",
            "lm": json_scalar(float(lm)),
            "p": json_scalar(float(lm_p)),
        }
    if "autocorrelation" in tests:
        from statsmodels.stats.stattools import durbin_watson

        results["autocorrelation"] = {
            "test": "durbin_watson",
            "statistic": json_scalar(float(durbin_watson(resid))),
            "note": "meaningful only for ordered data; no p-value",
        }
    return ToolSuccess(summary={"model": fitted.model, "tests": results})


# --- 3.4 compute_influence -----------------------------------------------------------------------


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def compute_influence(paths: RunPaths, fit: _Fit) -> ToolSuccess:
    """Cook's distance and leverage for every model, plus DFFITS for OLS (§3.4, P5). Labels are the
    design-matrix row positions. Writes a `table` artifact holding every row; the summary carries
    `n_above_cooks_4_over_n` and `top` (at most `INFLUENCE_TOP_K` rows sorted by Cook's distance),
    each `{label, cooks, leverage}`.
    """
    fitted = _load_fit(paths, fit)
    _refuse_mnlogit_residuals(fitted, "compute_influence")
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    influence = res.get_influence()
    cooks = np.asarray(influence.cooks_distance[0], dtype="float64")
    leverage = np.asarray(influence.hat_matrix_diag, dtype="float64")
    labels = list(range(fitted.n_obs))

    rows: list[JsonValue] = []
    has_dffits = fitted.model == "ols"
    dffits = np.asarray(influence.dffits[0], dtype="float64") if has_dffits else None
    for i, label in enumerate(labels):
        row: dict[str, JsonValue] = {
            "label": json_scalar(label),
            "cooks": json_scalar(float(cooks[i])),
            "leverage": json_scalar(float(leverage[i])),
        }
        if dffits is not None:
            row["dffits"] = json_scalar(float(dffits[i]))
        rows.append(row)

    n = fitted.n_obs
    threshold = 4.0 / n
    n_above = int(np.sum(cooks > threshold))
    order = np.argsort(-cooks)[:INFLUENCE_TOP_K]
    top: list[JsonValue] = [
        {
            "label": json_scalar(labels[i]),
            "cooks": json_scalar(float(cooks[i])),
            "leverage": json_scalar(float(leverage[i])),
        }
        for i in order
    ]
    payload: dict[str, JsonValue] = {"model": fitted.model, "rows": rows}
    meta = _save_table(paths, payload, "influence")
    summary: dict[str, JsonValue] = {
        "model": fitted.model,
        "n_above_cooks_4_over_n": json_scalar(n_above),
        "top": top,
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


# --- 3.4 plot_residuals --------------------------------------------------------------------------


def _new_figure() -> tuple[Figure, tuple[Axes, Axes]]:
    """Two side-by-side axes on a pyplot-free `Figure` (chunk 09 P5: pyplot's registry is
    process-global and not thread-safe, and tool bodies run in worker threads)."""
    fig = Figure(figsize=(12, 5), dpi=100)
    ax1 = fig.add_subplot(1, 2, 1)
    ax2 = fig.add_subplot(1, 2, 2)
    return fig, (ax1, ax2)


@tool(phase="inference", allow_in=_INFERENCE_PHASES)
def plot_residuals(paths: RunPaths, fit: _Fit) -> ToolSuccess:
    """Residuals-vs-fitted and a normal QQ plot (§3.4). OLS uses `resid`; GLM and Logit use
    deviance residuals. The QQ plot's theoretical quantiles come from stdlib
    `statistics.NormalDist().inv_cdf`, not scipy. PNG, pyplot-free, every text call passes
    `parse_math=False` (chunk 09).
    """
    fitted = _load_fit(paths, fit)
    _refuse_mnlogit_residuals(fitted, "plot_residuals")
    from statsmodels.base.model import Results

    res = cast(Results, fitted.result)
    if fitted.model == "ols":
        resid = np.asarray(res.resid, dtype="float64")
    elif fitted.model == "logit":
        # Logit exposes deviance residuals as `resid_dev`; GLM as `resid_deviance`.
        resid = np.asarray(res.resid_dev, dtype="float64")
    else:
        resid = np.asarray(res.resid_deviance, dtype="float64")
    fitted_vals = np.asarray(res.fittedvalues, dtype="float64")

    fig, (ax_rvf, ax_qq) = _new_figure()
    ax_rvf.scatter(fitted_vals, resid, s=12)
    ax_rvf.axhline(0.0, color="grey", linewidth=1)
    ax_rvf.set_xlabel("fitted values", parse_math=False)
    ax_rvf.set_ylabel("residuals", parse_math=False)
    ax_rvf.set_title("residuals vs fitted", parse_math=False)

    sample = np.sort(resid)
    n = len(sample)
    normal = statistics.NormalDist()
    theoretical = np.asarray([normal.inv_cdf((i + 0.5) / n) for i in range(n)], dtype="float64")
    ax_qq.scatter(theoretical, sample, s=12)
    ax_qq.set_xlabel("theoretical quantiles", parse_math=False)
    ax_qq.set_ylabel("ordered residuals", parse_math=False)
    ax_qq.set_title("normal QQ plot", parse_math=False)

    fig.tight_layout()
    ref = new_artifact_ref()
    meta = save_plot(paths.artifact(ref, "plot"), fig, ref=ref, label=f"{fitted.model} residuals")
    return ToolSuccess(summary={"model": fitted.model, "n_obs": json_scalar(n)}, artifacts=(meta,))


# --- shared table helper -------------------------------------------------------------------------


def _save_table(paths: RunPaths, payload: dict[str, JsonValue], label: str) -> ArtifactMeta:
    """Mint a ref and save `payload` as a `table` artifact (§3.4), the one way the diagnostics
    write their tables -- the same shape `evaluation._save_table` uses."""
    ref = new_artifact_ref()
    return save_json(paths.artifact(ref, "table"), payload, ref=ref, kind="table", label=label)
