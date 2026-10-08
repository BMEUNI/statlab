"""The node-11 interpretation tools (`plan/18_tools_interpretation.md`): `permutation_importance`,
`coefficient_importance`, `partial_dependence` and `analyze_errors`. `phase="interpretation"`,
`allow_in=["model_interpretation"]`. Predictive interpretation of a fitted model; inferential
diagnostics stay in node 6 (chunk 15).

Same shape as the other tool modules (D22): sklearn is imported lazily inside the bodies (it pulls
scipy, and `tools/__init__.py` imports this module at package init -- chunk 09's scipy-free
invariant); `n_jobs=1` except permutation repeats, which run on threads (D17 addendum 42);
every summary number goes through `_frame.json_scalar`
and tables/curves are strict JSON (no NaN/inf); labels and row order are never reset. The model
knows its own `target` and `features` (D-a), so these tools take a frame without a separate target
parameter and read the target off the model.

sklearn/pandas facts this module is written against (verified under `-W error`, §1):
- P1: `sklearn.inspection.permutation_importance(est, X, y, scoring=scorer_for(metric),
  n_repeats, random_state=seed, n_jobs=parallel_jobs)` (threads, D17 addendum 42) works for every
  metric and every model (RF, HGB, calibrated), negating a `neg_*` scorer so "positive
  importance = relied on", one sign for all.
  The tool name shadows sklearn's function, so sklearn's is imported under an alias in the body.
- P3: `CalibratedClassifierCV` has no `coef_`; `coefficient_importance` refuses it.
- P4: binary `LogisticRegression.coef_` is `(1, p)` and refers to `classes_[1]`; when the
  contract's positive class is `classes_[0]`, the coefficients are negated so a positive
  coefficient always means "towards the positive class".
- P5: partial dependence is brute force -- set column j to v for every row, average the
  prediction -- which handles calibrated models and NaN exactly as `predict` does, with no method
  selection.
"""

from __future__ import annotations

from typing import Annotated, cast

import numpy as np
import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import Metric

from statlab_tools.artifacts import load_dataframe, save_json
from statlab_tools.registry import estimators, is_binary_only, parallel_jobs, scorer_for
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolParamError, tool
from statlab_tools.tools._frame import (
    as_str,
    as_str_scalar,
    is_numeric_column,
    json_scalar,
    load_fitted,
)
from statlab_tools.tools.encoding import FittedScaler
from statlab_tools.tools.evaluation import (
    _align,
    _positive_class,
    class_labels,
    hard_labels,
    snapshot,
)
from statlab_tools.tools.training import FittedModel, check_metric_task, predict_matrix

_INTERPRET_PHASES = ("model_interpretation",)

ERROR_SEGMENT_MAX_LEVELS = 10
"""A numeric `by` column with more than this many distinct values is cut into quartiles rather
than one segment per value (§3.4)."""

MIN_SEGMENT_ROWS = 10
"""A segment with fewer than this many rows is `small`: reported in the table but kept out of
`worst`, where a tiny-sample error rate would otherwise dominate (§3.4)."""

TOP_N = 10
"""Rows carried in a summary's top list (importance tables); `worst` carries 5 (§3.3/§3.4)."""

_Model = Annotated[ArtifactRef, Field(description="A fitted model from train_estimator.")]
_Frame = Annotated[ArtifactRef, Field(description="A frame to interpret the model on.")]
_Train = Annotated[
    ArtifactRef, Field(description="The train frame; the population sd is read here.")
]
_Seed = Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)]
_Metric = Annotated[Metric, Field(description="The scoring metric; must match the model's task.")]


def _save_table(paths: RunPaths, payload: dict[str, JsonValue], label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_json(paths.artifact(ref, "table"), payload, ref=ref, kind="table", label=label)


def _save_curve(paths: RunPaths, payload: dict[str, JsonValue], label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_json(paths.artifact(ref, "curve"), payload, ref=ref, kind="curve", label=label)


def _model_y(frame: pd.DataFrame, fitted: FittedModel) -> np.ndarray:
    """The target `y` exactly as `train_estimator` takes it (hazard): a classifier keeps the
    labels' own dtype, a regressor goes `float64`. The frame must carry the model's target."""
    if fitted.target not in frame.columns:
        raise ToolParamError(
            f"frame is missing the model's target column {fitted.target!r}",
            field_errors={"frame": f"missing target column {fitted.target!r}"},
        )
    y = frame[fitted.target]
    if fitted.task == "classification":
        return y.to_numpy()
    return y.to_numpy(dtype="float64")


# --- 3.1 permutation_importance ------------------------------------------------------------------


@tool(phase="interpretation", allow_in=_INTERPRET_PHASES, heavy=True)
def permutation_importance(
    paths: RunPaths,
    model: _Model,
    frame: _Frame,
    metric: _Metric,
    seed: _Seed,
    n_repeats: Annotated[
        int, Field(default=5, ge=1, le=50, description="Permutation repeats per feature.")
    ] = 5,
) -> ToolSuccess:
    """Permutation importance per model column: the drop in `metric` when that column is shuffled
    (sklearn as the oracle, P1). One sign for every metric -- a `neg_*` scorer is negated inside
    sklearn, so a positive importance always means "the model relies on this column".

    The frame must carry the model's features and target; a missing feature is `tool_param`. `y`
    is read exactly as `train_estimator` takes it. `metric` must match the model's task, and a
    multiclass `brier` is `tool_param` (it has no multiclass scorer). The table has one
    `{feature, importance_mean, importance_std}` row per feature, sorted by mean descending with
    ties broken by name. The summary carries `metric`, `baseline` (the scorer on the unpermuted
    data in natural direction), `n_repeats`, `n_features` and the top 10 rows.

    ponytail: importance is per model column (D-b), so a one-hot source split across dummies is
    measured dummy by dummy, not as one group. Grouped permutation (a `groups` parameter fed from
    the encoder's levels) is the upgrade when per-source importance is wanted.
    """
    # the tool name shadows sklearn's; alias it so the body can call the real one.
    from joblib import parallel_config
    from sklearn.inspection import permutation_importance as sk_permutation_importance

    fitted = load_fitted(paths, model, FittedModel, "model")
    check_metric_task(metric, fitted.task)
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    x = predict_matrix(data, fitted)
    y = _model_y(data, fitted)

    multiclass = fitted.task == "classification" and len(fitted.classes) > 2
    if multiclass and is_binary_only(metric):
        raise ToolParamError(
            f"metric {metric!r} has no multiclass scorer",
            field_errors={"metric": f"{metric!r} is binary-only"},
        )
    scorer = scorer_for(metric, multiclass=multiclass)

    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, fitted.estimator)
    jobs = parallel_jobs(fitted.name)
    with parallel_config(backend="threading"):
        result = sk_permutation_importance(
            est, x, y, scoring=scorer, n_repeats=n_repeats, random_state=seed, n_jobs=jobs
        )
    means = np.asarray(result.importances_mean, dtype="float64")
    stds = np.asarray(result.importances_std, dtype="float64")
    baseline = _baseline(est, x, y, scorer)

    rows = [
        {
            "feature": feat,
            "importance_mean": float(means[i]),
            "importance_std": float(stds[i]),
        }
        for i, feat in enumerate(fitted.features)
    ]
    rows.sort(key=lambda r: (-cast(float, r["importance_mean"]), cast(str, r["feature"])))
    table: list[JsonValue] = [
        {
            "feature": cast(str, r["feature"]),
            "importance_mean": json_scalar(r["importance_mean"]),
            "importance_std": json_scalar(r["importance_std"]),
        }
        for r in rows
    ]
    payload: dict[str, JsonValue] = {
        "kind": "permutation_importance",
        "metric": metric,
        "features": table,
    }
    meta = _save_table(paths, payload, "permutation_importance")
    summary: dict[str, JsonValue] = {
        "metric": metric,
        "baseline": json_scalar(baseline),
        "n_repeats": json_scalar(n_repeats),
        "n_features": json_scalar(len(fitted.features)),
        "top": table[:TOP_N],
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _baseline(est: object, x: np.ndarray, y: np.ndarray, scorer: str | object) -> float:
    """The scorer on the unpermuted data, in the metric's natural direction. `scorer` is a sklearn
    scorer (a built-in name string or a `make_scorer` object); `check_scoring` resolves either to
    a callable `(estimator, X, y) -> float` whose sign is sklearn's (greater is better), which is
    already the natural direction for every importance reading (a `neg_*` scorer's own value, not
    the metric's, so the baseline is reported as sklearn scores it)."""
    from sklearn.metrics import check_scoring

    scorer_fn = check_scoring(est, scoring=scorer)
    return float(scorer_fn(est, x, y))


# --- 3.2 coefficient_importance ------------------------------------------------------------------


@tool(phase="interpretation", allow_in=_INTERPRET_PHASES)
def coefficient_importance(paths: RunPaths, model: _Model, train: _Train) -> ToolSuccess:
    """Standardised coefficients of a linear model: `std_coef = coef x population sd of the train
    column` (D-c), making scaled and unscaled matrices comparable (a standard-scaled column has
    sd 1, a 0/1 dummy sd sqrt(p(1-p))).

    Only an `interpretable` estimator (`logistic_regression`, `linear_regression`) qualifies; any
    other is `tool_param` ("use permutation_importance"), and a calibrated model is `tool_param`
    (it has no `coef_`, P3). The `train` frame supplies the population sd per feature (`ddof=0`,
    NaN skipped; a constant column has sd 0, so `std_coef` is 0). Binary coefficients are
    sign-aligned to the contract's positive class (P4: `coef_` refers to `classes_[1]`, so when
    the positive class is `classes_[0]` every coefficient is negated). Multiclass gives one row
    per (class, feature); regression one row per feature. The intercept is reported separately.
    The summary carries `positive_class` (binary), `intercept` and the top 10 by `|std_coef|`.
    """
    fitted = load_fitted(paths, model, FittedModel, "model")
    entry = estimators()[fitted.name]
    if not entry.interpretable:
        raise ToolParamError(
            f"estimator {fitted.name!r} is not interpretable; use permutation_importance",
            field_errors={"model": "not a linear model; use permutation_importance"},
        )
    if fitted.calibrated:
        raise ToolParamError(
            "a calibrated model has no coefficients (one per calibration fold); use "
            "permutation_importance",
            field_errors={"model": "calibrated model has no coef_"},
        )

    data = load_dataframe(paths.artifact(train, "dataframe"))
    missing = [col for col in fitted.features if col not in data.columns]
    if missing:
        raise ToolParamError(
            f"train is missing fitted feature columns {missing!r}",
            field_errors={"train": f"missing fitted features: {missing!r}"},
        )
    sd = np.asarray(
        [float(data[col].astype("float64").std(ddof=0)) for col in fitted.features], dtype="float64"
    )

    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, fitted.estimator)
    coef = np.asarray(est.coef_, dtype="float64")
    # LinearRegression.intercept_ is a 0-d scalar; LogisticRegression's is 1-d. atleast_1d unifies.
    intercept = np.atleast_1d(np.asarray(est.intercept_, dtype="float64"))

    if fitted.task == "regression":
        # LinearRegression.coef_ is 1-d (p,), not (1, p); use it directly.
        return _coef_regression(paths, fitted, coef, intercept, sd)
    if len(fitted.classes) > 2:
        return _coef_multiclass(paths, fitted, coef, intercept, sd)
    return _coef_binary(paths, fitted, coef, intercept, sd)


def _coef_rows(
    features: tuple[str, ...], coef: np.ndarray, sd: np.ndarray
) -> list[dict[str, object]]:
    """Per-feature `{feature, coef, std_coef}` for one coefficient vector (`coef[i] x sd[i]`)."""
    return [
        {"feature": feat, "coef": float(coef[i]), "std_coef": float(coef[i] * sd[i])}
        for i, feat in enumerate(features)
    ]


def _coef_table_rows(rows: list[dict[str, object]]) -> list[JsonValue]:
    """Strict-JSON `{feature, coef, std_coef}` rows."""
    return [
        {
            "feature": cast(str, r["feature"]),
            "coef": json_scalar(cast(float, r["coef"])),
            "std_coef": json_scalar(cast(float, r["std_coef"])),
        }
        for r in rows
    ]


def _top_by_abs(rows: list[JsonValue], key: str = "std_coef") -> list[JsonValue]:
    """The top `TOP_N` rows by `|key|`, descending."""
    return sorted(
        rows, key=lambda r: -abs(cast(float, cast("dict[str, JsonValue]", r)[key] or 0.0))
    )[:TOP_N]


def _coef_regression(
    paths: RunPaths, fitted: FittedModel, coef: np.ndarray, intercept: np.ndarray, sd: np.ndarray
) -> ToolSuccess:
    rows = _coef_table_rows(_coef_rows(fitted.features, coef, sd))
    payload: dict[str, JsonValue] = {
        "kind": "coefficient_importance",
        "task": "regression",
        "coefficients": rows,
        "intercept": json_scalar(float(intercept[0])),
    }
    meta = _save_table(paths, payload, "coefficient_importance")
    summary: dict[str, JsonValue] = {
        "intercept": json_scalar(float(intercept[0])),
        "top": _top_by_abs(rows),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _coef_binary(
    paths: RunPaths, fitted: FittedModel, coef: np.ndarray, intercept: np.ndarray, sd: np.ndarray
) -> ToolSuccess:
    classes = [as_str_scalar(c) for c in fitted.classes]
    positive, _ = _positive_class(snapshot(paths), classes)
    # coef_ is (1, p) and refers to classes_[1]; negate when the positive class is classes_[0].
    sign = -1.0 if positive == classes[0] else 1.0
    vec = sign * coef[0]
    rows = _coef_table_rows(_coef_rows(fitted.features, vec, sd))
    payload: dict[str, JsonValue] = {
        "kind": "coefficient_importance",
        "task": "classification",
        "positive_class": positive,
        "coefficients": rows,
        "intercept": json_scalar(float(sign * intercept[0])),
    }
    meta = _save_table(paths, payload, "coefficient_importance")
    summary: dict[str, JsonValue] = {
        "positive_class": positive,
        "intercept": json_scalar(float(sign * intercept[0])),
        "top": _top_by_abs(rows),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _coef_multiclass(
    paths: RunPaths, fitted: FittedModel, coef: np.ndarray, intercept: np.ndarray, sd: np.ndarray
) -> ToolSuccess:
    classes = [as_str_scalar(c) for c in fitted.classes]
    rows: list[JsonValue] = []
    intercepts: dict[str, JsonValue] = {}
    for k, cls in enumerate(classes):
        for row in _coef_table_rows(_coef_rows(fitted.features, coef[k], sd)):
            entry: dict[str, JsonValue] = {"class": cls, **cast("dict[str, JsonValue]", row)}
            rows.append(entry)
        intercepts[cls] = json_scalar(float(intercept[k]))
    payload: dict[str, JsonValue] = {
        "kind": "coefficient_importance",
        "task": "classification",
        "coefficients": rows,
        "intercept": intercepts,
    }
    meta = _save_table(paths, payload, "coefficient_importance")
    summary: dict[str, JsonValue] = {
        "intercept": intercepts,
        "top": _top_by_abs(rows),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


# --- 3.3 partial_dependence ----------------------------------------------------------------------


@tool(phase="interpretation", allow_in=_INTERPRET_PHASES, heavy=True)
def partial_dependence(
    paths: RunPaths,
    model: _Model,
    frame: _Frame,
    column: Annotated[str, Field(min_length=1, description="A model feature to vary.")],
    grid_resolution: Annotated[
        int, Field(default=20, ge=2, le=100, description="Grid points along the column.")
    ] = 20,
    scaler: Annotated[
        ArtifactRef | None,
        Field(default=None, description="A FittedScaler, to read the grid in original units."),
    ] = None,
) -> ToolSuccess:
    """Brute-force partial dependence of the model on one feature (P5): over a grid of the
    column's values, set the whole column to each value and average the prediction.

    `column` must be a model feature. The grid is the column's distinct non-null values (sorted)
    when there are at most `grid_resolution` of them, else the unique quantiles over
    `linspace(0.05, 0.95, grid_resolution)`; a bool column's grid is `[0.0, 1.0]`. A regression
    model gives one `prediction` series; binary gives one `P(positive)` series; multiclass one
    series per class. With a `scaler` (the candidate's `FittedScaler`), a scaled column's grid is
    mapped back to original units (`x . scale + center`, D-e); a column the scaler did not touch
    gets `x_original = x`; without a scaler `x_original` is null. Writes a `partial_dependence`
    curve; `plot_curve` renders it.
    """
    fitted = load_fitted(paths, model, FittedModel, "model")
    if column not in fitted.features:
        raise ToolParamError(
            f"column {column!r} is not a model feature; one of {list(fitted.features)!r}",
            field_errors={"column": f"not a model feature: {column!r}"},
        )
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    x = predict_matrix(data, fitted)
    col_idx = fitted.features.index(column)

    grid = _pd_grid(data[column], grid_resolution)
    series = _pd_series(fitted, x, col_idx, grid)
    x_original = _pd_x_original(paths, scaler, column, grid)

    payload: dict[str, JsonValue] = {
        "kind": "partial_dependence",
        "column": column,
        "x": [json_scalar(float(v)) for v in grid],
        "x_original": (None if x_original is None else [json_scalar(float(v)) for v in x_original]),
        "series": {name: [json_scalar(float(v)) for v in vals] for name, vals in series.items()},
        "task": fitted.task,
        "n_rows": json_scalar(len(data)),
    }
    meta = _save_curve(paths, payload, "partial_dependence")
    summary: dict[str, JsonValue] = {
        "column": column,
        "task": fitted.task,
        "grid_points": json_scalar(len(grid)),
        "series": list(series),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _pd_grid(column: pd.Series, grid_resolution: int) -> np.ndarray:
    """The grid for a column (§3.3). A bool column is `[0, 1]`; a low-cardinality column its sorted
    distinct non-null values; otherwise the unique quantiles over `linspace(0.05, 0.95, res)`."""
    if pd.api.types.is_bool_dtype(column):
        return np.array([0.0, 1.0], dtype="float64")
    values = column.dropna().to_numpy(dtype="float64")
    distinct = np.unique(values)
    if distinct.size <= grid_resolution:
        return distinct
    quantiles = np.quantile(values, np.linspace(0.05, 0.95, grid_resolution))
    return np.unique(quantiles)


def _pd_series(
    fitted: FittedModel, x: np.ndarray, col_idx: int, grid: np.ndarray
) -> dict[str, list[float]]:
    """Brute-force PD (P5): for each grid value, set column `col_idx` to it on a copy of the
    matrix and average the model's prediction. Regression averages `predict`; classification
    averages each class's `predict_proba` column (binary keeps only the positive class)."""
    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, fitted.estimator)
    if fitted.task == "regression":
        means = [float(np.mean(np.asarray(est.predict(_set_col(x, col_idx, v))))) for v in grid]
        return {"prediction": means}
    classes = [as_str_scalar(c) for c in fitted.classes]
    averaged = np.asarray(
        [
            np.asarray(est.predict_proba(_set_col(x, col_idx, v)), dtype="float64").mean(axis=0)
            for v in grid
        ],
        dtype="float64",
    )
    if len(classes) == 2:
        # binary: one series, the mean P(positive) column.
        return {classes[1]: [float(row[1]) for row in averaged]}
    return {cls: [float(averaged[g, k]) for g in range(len(grid))] for k, cls in enumerate(classes)}


def _set_col(x: np.ndarray, col_idx: int, value: float) -> np.ndarray:
    """A copy of `x` with column `col_idx` set to `value` on every row (never mutates `x`)."""
    out = x.copy()
    out[:, col_idx] = value
    return out


def _pd_x_original(
    paths: RunPaths, scaler: ArtifactRef | None, column: str, grid: np.ndarray
) -> np.ndarray | None:
    """The grid in original units (D-e): with no scaler, `None`; with a scaler, `x . scale +
    center` for a column it scaled, else `x` for a column it did not touch (a dummy). A ref that
    is not a `FittedScaler` is `tool_param`."""
    if scaler is None:
        return None
    fitted_scaler = load_fitted(paths, scaler, FittedScaler, "scaler")
    params = fitted_scaler.params.get(column)
    if params is None:
        return grid
    center, scale = params
    return grid * scale + center


# --- 3.4 analyze_errors --------------------------------------------------------------------------


_Predictions = Annotated[
    ArtifactRef, Field(description="A predict or predict_proba predictions artifact.")
]
_Truth = Annotated[ArtifactRef, Field(description="A frame carrying the target and `by` columns.")]
_Target = Annotated[str, Field(min_length=1, description="The target column in `truth`.")]
_By = Annotated[
    tuple[str, ...], Field(min_length=1, description="Columns to segment errors by (in `truth`).")
]


@tool(phase="interpretation", allow_in=_INTERPRET_PHASES)
def analyze_errors(
    paths: RunPaths,
    predictions: _Predictions,
    truth: _Truth,
    target: _Target,
    by: _By,
    threshold: Annotated[
        float | None,
        Field(default=None, gt=0, lt=1, description="Binary p(positive)>=threshold; binary only."),
    ] = None,
) -> ToolSuccess:
    """Error rate (classification) or MAE (regression) by segment of the readable `truth` frame.

    Predictions are aligned to `truth` by label (D-f; an exact label-set match, else `tool_param`,
    which is what makes a shuffled prediction order score identically). `proba__` columns mean
    classification (hard labels via argmax, or `p(positive) >= threshold` for binary); a
    `prediction` column means regression; neither is `tool_param`. Each `by` column must be in
    `truth` and must not be the target. A numeric `by` with more than
    `ERROR_SEGMENT_MAX_LEVELS` distinct values is cut into quartiles (`qcut`); otherwise one
    segment per level; nulls form a `"<missing>"` segment. Per segment: `n`, `error`, `bias`
    (regression only) and `small` (`n < MIN_SEGMENT_ROWS`). The table holds every
    `(column, segment)` row sorted by error then `n` descending; the summary's `worst` is the top
    5 non-`small` rows.
    """
    preds = load_dataframe(paths.artifact(predictions, "predictions"))
    truth_frame = load_dataframe(paths.artifact(truth, "dataframe"))
    if target not in truth_frame.columns:
        raise ToolParamError(
            f"target {target!r} is not a column in truth",
            field_errors={"target": f"not a column in truth: {target!r}"},
        )
    bad_by = [c for c in by if c not in truth_frame.columns]
    if bad_by:
        raise ToolParamError(
            f"by columns {bad_by!r} are not in truth",
            field_errors={"by": f"not columns in truth: {bad_by!r}"},
        )
    if target in by:
        raise ToolParamError(
            f"by names the target {target!r}, which cannot segment its own error",
            field_errors={"by": f"must not include the target {target!r}"},
        )

    task, error, extra = _errors_per_row(preds, truth_frame[target], threshold, snapshot(paths))
    # error/extra are already indexed by the predictions' labels (aligned in _errors_per_row).
    rows = _segment_rows(truth_frame, by, preds.index, error, extra, task)
    rows.sort(key=lambda r: (-cast(float, r["error"]), -cast(int, r["n"])))
    table = _error_table_rows(rows, task)
    overall = float(error.mean())
    worst = [r for r in table if not cast("dict[str, JsonValue]", r)["small"]][:5]
    metric = "error_rate" if task == "classification" else "mae"
    payload: dict[str, JsonValue] = {
        "kind": "error_analysis",
        "task": task,
        "metric": metric,
        "segments": table,
    }
    meta = _save_table(paths, payload, "error_analysis")
    summary: dict[str, JsonValue] = {
        "task": task,
        "metric": metric,
        "overall_error": json_scalar(overall),
        "n_segments": json_scalar(len(table)),
        "worst": worst,
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _errors_per_row(
    preds: pd.DataFrame,
    truth_target: pd.Series,
    threshold: float | None,
    snap: object,
) -> tuple[str, pd.Series, pd.Series | None]:
    """`(task, per_row_error, extra)` indexed by the predictions' labels. For classification the
    error is `1.0` on a misclassified row (`extra` is None); for regression the error is the
    absolute residual and `extra` is the signed residual (prediction - truth) for `bias`. The
    predictions are aligned to the truth target by label (D-f) before anything else."""
    proba_cols = [c for c in preds.columns if str(c).startswith("proba__")]
    if proba_cols:
        classes = class_labels([str(c) for c in proba_cols])
        aligned_truth = _align(truth_target, preds).truth
        if threshold is not None and len(classes) != 2:
            raise ToolParamError(
                "threshold is binary only; this target has more than two classes",
                field_errors={"threshold": "not applicable to a multiclass target"},
            )
        positive, _ = _positive_class(cast("DataContract | None", snap), classes)
        proba = preds[proba_cols].to_numpy(dtype="float64")
        y_pred = hard_labels(proba, classes, positive, threshold)
        y_true = as_str(aligned_truth).to_numpy()
        wrong = (y_pred != y_true).astype("float64")
        return "classification", pd.Series(wrong, index=preds.index), None
    if "prediction" in preds.columns:
        aligned_truth = _align(truth_target, preds).truth
        y_true = aligned_truth.to_numpy(dtype="float64")
        y_pred = preds["prediction"].to_numpy(dtype="float64")
        resid = pd.Series(y_pred - y_true, index=preds.index)
        return "regression", resid.abs(), resid
    raise ToolParamError(
        "predictions have neither proba__ columns nor a `prediction` column",
        field_errors={"predictions": "not a predict or predict_proba artifact"},
    )


def _segment_rows(
    truth_frame: pd.DataFrame,
    by: tuple[str, ...],
    index: pd.Index,
    error: pd.Series,
    extra: pd.Series | None,
    task: str,
) -> list[dict[str, object]]:
    """One `{column, segment, n, error, bias, small}` dict per `(by column, level)`, over the
    truth frame reordered to the predictions' label order. A numeric column with more than
    `ERROR_SEGMENT_MAX_LEVELS` distinct values is cut into quartiles; otherwise `as_str` levels;
    nulls form a `"<missing>"` segment."""
    rows: list[dict[str, object]] = []
    for col in by:
        labels = _segment_labels(truth_frame[col].reindex(index))
        for seg in _ordered_segments(labels):
            mask = (labels == seg).to_numpy()
            seg_error = error[mask]
            n = int(mask.sum())
            bias = float(extra[mask].mean()) if (extra is not None and n > 0) else None
            rows.append(
                {
                    "column": col,
                    "segment": seg,
                    "n": n,
                    "error": float(seg_error.mean()),
                    "bias": bias,
                    "small": n < MIN_SEGMENT_ROWS,
                }
            )
    return rows


def _segment_labels(column: pd.Series) -> pd.Series:
    """String segment labels for one `by` column: quartiles for a high-cardinality numeric column
    (`qcut(..., duplicates="drop")`), else `as_str` levels; nulls become `"<missing>"`."""
    if is_numeric_column(column) and column.nunique() > ERROR_SEGMENT_MAX_LEVELS:
        binned = pd.qcut(column, 4, duplicates="drop")
        labels = binned.astype("object").map(lambda v: v if v is np.nan else str(v))
        return pd.Series(labels, index=column.index).fillna("<missing>")
    return as_str(column).fillna("<missing>")


def _ordered_segments(labels: pd.Series) -> list[str]:
    """Distinct segment labels in first-seen order (never `reset_index`; preserves determinism)."""
    seen: list[str] = []
    for value in labels.tolist():
        if value not in seen:
            seen.append(str(value))
    return seen


def _error_table_rows(rows: list[dict[str, object]], task: str) -> list[JsonValue]:
    """Strict-JSON `{column, segment, n, error, bias?, small}` rows; `bias` only for regression."""
    out: list[JsonValue] = []
    for r in rows:
        row: dict[str, JsonValue] = {
            "column": cast(str, r["column"]),
            "segment": cast(str, r["segment"]),
            "n": json_scalar(cast(int, r["n"])),
            "error": json_scalar(cast(float, r["error"])),
            "small": cast(bool, r["small"]),
        }
        if task == "regression":
            row["bias"] = json_scalar(r["bias"])
        out.append(row)
    return out
