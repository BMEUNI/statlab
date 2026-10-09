"""The six training tools (`PLAN.md` §6.3's `training` phase, this chunk's `13_tools_training.md`):
`list_estimators`, `train_estimator`, `tune_estimator`, `predict`, `predict_proba`,
`get_model_params`. This is where D34a (tune by CV within train), D34b (calibrate on train), D34e
(explicit seeds), D17 (`n_jobs=1` estimators, the fit semaphore via `heavy=True`; addendum 50
threads an uncalibrated forest's final fit) and D36c (tuning
stops at its budget) become code. It runs after chunk 12 in batch B1 and consumes its matrices.

sklearn is untyped here (`ignore_missing_imports`), so its estimators, scorers and arrays are
implicit dynamic values. Three rules keep that dynamic type out of this module's typed surface
(P6, D-note in the spec): the registry's `build` returns `object` and `FittedModel.estimator` is
an `object` field (an estimator is only ever stored and re-loaded, never statically typed as a
sklearn class); every array crossing from sklearn into typed code is `np.asarray(..., dtype=...)`
assigned to a typed name; and a `predict`/`predict_proba`/`get_params` call result is immediately
converted (to a numpy array or a plain dict) rather than flowing on as a dynamic value. No dynamic
leak, no new `# type: ignore`.

sklearn facts this module is written against (verified under `-W error`, the spec's §1):
`CalibratedClassifierCV` on a class with fewer than `cv_folds` train rows raises `ValueError`
(P1), so the class-count pre-check runs first and raises `ToolDataError`; `LogisticRegression`
rejects NaN while the forests and HistGB accept it (P2), gated by the registry's `handles_missing`;
`DataFrame.to_numpy(dtype="float64", na_value=np.nan)` converts `bool`, nullable `boolean` and
`Int64`-with-NA correctly (P3); a multiclass precision/recall/f1 scorer must use
`make_scorer(..., zero_division=0)` or a zero-division `UndefinedMetricWarning` fails scoring under
`-W error` (P4); fitting on a `DataFrame` then predicting on numpy emits a feature-name
`UserWarning`, so every fit and predict is on a `float64` numpy matrix (D-note). `tune_estimator`
reads `_monotonic` through this module so a test can advance it (D-b); `warnings.catch_warnings`
is never used -- it is process-global and bodies run in threads (D-e).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Annotated, cast

import numpy as np
import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import (
    RESERVED_ESTIMATOR_PARAMS,
    ClassWeight,
    Metric,
    ParamValue,
    Task,
)

from statlab_tools.artifacts import load_dataframe, save_dataframe, save_model
from statlab_tools.registry import (
    SPECS,
    EstimatorEntry,
    estimators,
    fit_threads,
    greater_is_better,
    is_binary_only,
    is_negated,
    parallel_jobs,
    scorer_for,
)
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import (
    as_str_scalar,
    is_class_target,
    is_numeric_column,
    json_scalar,
    load_fitted,
)

_TRAIN_PHASES = ("model_training",)
_PREDICT_PHASES = ("model_evaluation", "model_interpretation", "champion_selection")
_PARAMS_PHASES = ("model_training", "model_evaluation", "champion_selection")
_DESIGN_PHASES = ("experiment_design", "model_training")

MAX_GRID_POINTS = 50
"""A tuning grid over more than this many points is `tool_param`: the budget is for stopping a
slow fit, not for an unbounded search, and `results` is capped here so it needs no truncation flag.
"""

_FALLBACK_CV_FOLDS = 5
"""Fold count when no contract snapshot is present (tests that call tool bodies without writing
one). The real value comes from the snapshot's `modelling.tuning.cv_folds` (D-a)."""

_FALLBACK_BUDGET_SECONDS = 300
"""Likewise for `max_fit_seconds`."""

# `n_jobs` and `random_state` are owned by D17 and D34e; naming either in `params` is `tool_param`.

_Train = Annotated[ArtifactRef, Field(description="The train split frame; fits happen here only.")]
_Model = Annotated[ArtifactRef, Field(description="A fitted model from train_estimator.")]
_Frame = Annotated[ArtifactRef, Field(description="The dataframe to predict on.")]
_Target = Annotated[str, Field(min_length=1, description="The target column.")]
_Name = Annotated[str, Field(min_length=1, description="An estimator name from list_estimators.")]
_Seed = Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)]
_Params = Annotated[
    dict[str, ParamValue], Field(description="Hyperparameter overrides; n_jobs/random_state fixed.")
]
_ClassWeight = Annotated[
    ClassWeight, Field(description="Class weighting; 'balanced' needs a supporting classifier.")
]

_NO_PARAMS: dict[str, ParamValue] = {}
"""Shared empty default for `params`; never mutated (ruff B006)."""


# --- fitted state --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedModel:
    """A fitted estimator and everything `predict`/`predict_proba`/`get_model_params` need without
    reaching into sklearn. `estimator` is typed `object`, not a sklearn class: sklearn is untyped
    here, and storing it as `object` keeps that dynamic type off this module's surface -- it is
    narrowed only to call documented methods, never widened. Frozen and plain so
    joblib round-trips it. `features` is the fitted feature order; `predict` selects exactly those
    columns. `classes` is empty for a regressor."""

    name: str
    task: Task
    target: str
    features: tuple[str, ...]
    classes: tuple[object, ...]
    params: dict[str, ParamValue]
    class_weight: ClassWeight
    calibrated: bool
    seed: int
    estimator: object


# --- shared --------------------------------------------------------------------------------------


def _entry(name: str) -> EstimatorEntry:
    """Look an estimator up by name, or `tool_param` listing the valid names."""
    reg = estimators()
    entry = reg.get(name)
    if entry is None:
        raise ToolParamError(
            f"unknown estimator {name!r}; valid names: {sorted(reg)!r}",
            field_errors={"name": f"unknown estimator; one of {sorted(reg)!r}"},
        )
    return entry


def tuning_from_snapshot(paths: RunPaths) -> tuple[int, int]:
    """`(cv_folds, max_fit_seconds)` from the contract snapshot's `modelling.tuning` (D-a: the
    user's budget, which the agent cannot weaken). Falls back to the sklearn defaults when no
    snapshot is present -- the same read `cleaning.cast_dtypes` does."""
    if paths.contract_snapshot_json.exists():
        contract = DataContract.model_validate_json(paths.contract_snapshot_json.read_text())
        return contract.modelling.tuning.cv_folds, contract.modelling.tuning.max_fit_seconds
    return _FALLBACK_CV_FOLDS, _FALLBACK_BUDGET_SECONDS


def _reject_class_weight(entry: EstimatorEntry, class_weight: ClassWeight) -> None:
    """`class_weight != "none"` on an entry without support is `tool_param` (§3.1)."""
    if class_weight != "none" and not entry.supports_class_weight:
        raise ToolParamError(
            f"estimator does not support class_weight={class_weight!r}",
            field_errors={"class_weight": "this estimator has no class_weight support"},
        )


def _validate_params(entry: EstimatorEntry, keys: tuple[str, ...]) -> None:
    """Every `params` key must be a real `get_params()` key of the estimator, and neither
    `n_jobs` nor `random_state` (D17/D34e own those). Otherwise `tool_param` (§3.1)."""
    reserved = [k for k in keys if k in RESERVED_ESTIMATOR_PARAMS]
    if reserved:
        raise ToolParamError(
            f"params {reserved!r} are fixed by the tool (n_jobs by D17, random_state by D34e)",
            field_errors={"params": f"not overridable: {reserved!r}"},
        )
    valid = set(_get_params(entry.build(0, "none")))
    unknown = [k for k in keys if k not in valid]
    if unknown:
        raise ToolParamError(
            f"params {unknown!r} are not accepted by {entry.task} estimator; "
            f"valid keys include {sorted(valid)!r}",
            field_errors={"params": f"unknown keys: {unknown!r}"},
        )


def _get_params(estimator: object) -> dict[str, object]:
    """`estimator.get_params()` as a plain `dict[str, object]`. sklearn is untyped here
    (`ignore_missing_imports`), so `BaseEstimator` is a dynamic value and `cast`ing to it lets the
    method call need no `# type: ignore` -- the call is on a dynamic value, not bare `object`. The
    result is converted to a plain dict at the boundary, so the dynamic type does not escape (P6).
    This is the
    narrow-at-the-boundary pattern `encoding.py` uses for `TargetEncoder`, generalised."""
    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, estimator)
    return {str(k): v for k, v in dict(est.get_params()).items()}


def feature_columns(frame: pd.DataFrame, target: str) -> list[str]:
    """Every column except `target` (D-d). The node has already dropped unwanted columns."""
    return [col for col in frame.columns if col != target]


def check_features_usable(frame: pd.DataFrame, features: list[str]) -> None:
    """Every feature must be numeric or bool, nullable included (D-d). A string, datetime or
    categorical column is `tool_param` naming them ("encode first")."""
    bad = [
        col
        for col in features
        if not (is_numeric_column(frame[col]) or pd.api.types.is_bool_dtype(frame[col]))
    ]
    if bad:
        raise ToolParamError(
            f"feature columns {bad!r} are not numeric or bool; encode them first",
            field_errors={"train": f"non-numeric feature columns: {bad!r}"},
        )


def build_matrix(frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    """The `float64` design matrix for `features` (P3: one conversion for every allowed dtype).
    Always numpy, never a DataFrame -- fitting or predicting on a frame then crossing to numpy
    emits a feature-name `UserWarning` that `-W error` turns fatal (D-note)."""
    return frame[features].to_numpy(dtype="float64", na_value=np.nan)


def check_no_missing(matrix: np.ndarray, entry: EstimatorEntry) -> None:
    """NaN in the matrix with an estimator that cannot handle it is `tool_param` (P2)."""
    if not entry.handles_missing and bool(np.isnan(matrix).any()):
        raise ToolParamError(
            "the feature matrix has missing values and this estimator cannot handle them; "
            "impute first",
            field_errors={"train": "missing values; impute first or use a tree estimator"},
        )


def check_task(frame: pd.DataFrame, target: str, task: Task) -> None:
    """Task compatibility by shape (D-f): a classifier needs `is_class_target`, a regressor needs
    `is_numeric_column`."""
    y = frame[target]
    if task == "classification" and not is_class_target(y):
        raise ToolDataError(f"estimator is a classifier but target {target!r} is not class-shaped")
    if task == "regression" and not is_numeric_column(y):
        raise ToolDataError(f"estimator is a regressor but target {target!r} is not numeric")


def cv_splitter(cv_folds: int, task: Task, seed: int) -> object:
    """The CV splitter (D-c): `StratifiedKFold` for a classifier, `KFold` for a regressor, both
    shuffled with the injected seed. Chunk 10 keeps the input's row order, so an unshuffled split
    of target-sorted or time-sorted data would make biased folds. Returned as `object` -- a
    sklearn splitter is a dynamic value (P6)."""
    from sklearn.model_selection import KFold, StratifiedKFold

    if task == "classification":
        return StratifiedKFold(cv_folds, shuffle=True, random_state=seed)
    return KFold(cv_folds, shuffle=True, random_state=seed)


def check_class_counts(y: pd.Series, cv_folds: int) -> None:
    """Every class needs at least `cv_folds` train rows, else `CalibratedClassifierCV` /
    `StratifiedKFold` raise (P1); surfaced as `ToolDataError` so it is not a `bug`."""
    smallest = int(y.value_counts().min())
    if smallest < cv_folds:
        raise ToolDataError(
            f"the smallest target class has {smallest} rows, fewer than cv_folds={cv_folds}; "
            f"cannot cross-validate or calibrate"
        )


def _effective_params(estimator: object) -> dict[str, JsonValue]:
    """The estimator's own `get_params()`, JSON-scalared or `repr`'d for a non-JSON value (§3.1's
    summary). Keeps sklearn's dynamic param values off the typed surface."""
    out: dict[str, JsonValue] = {}
    for key, value in _get_params(estimator).items():
        out[key] = _param_json(value)
    return out


def _param_json(value: object) -> JsonValue:
    """A param value as strict JSON: `json_scalar` for a scalar it recognises, else `repr`
    (sklearn params like `None`, nested estimators, or callables)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def _classes(estimator: object) -> tuple[object, ...]:
    """A fitted classifier's `classes_` as a plain tuple (converted at the boundary, P6). The
    labels are kept as their own type so `predict`'s output matches the training target's."""
    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, estimator)
    return tuple(np.asarray(est.classes_).tolist())


def _save_model_artifact(paths: RunPaths, model: FittedModel) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_model(paths.artifact(ref, "model"), model, ref=ref, kind="model", label=model.name)


def _save_predictions(paths: RunPaths, frame: pd.DataFrame, label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_dataframe(
        paths.artifact(ref, "predictions"), frame, ref=ref, kind="predictions", label=label
    )


# --- 3.2 list_estimators ([v1]) ------------------------------------------------------------------


@tool(phase="training", allow_in=_DESIGN_PHASES, needs_run=False)
def list_estimators(
    task: Annotated[
        Task | None, Field(description="Filter to one task; None lists every estimator.")
    ] = None,
) -> ToolSuccess:
    """List the registry's estimators (D25), optionally filtered to one `task`. Per entry:
    `task`, `default_grid`, `handles_missing`, `supports_class_weight`, and `supports_proba`
    (true for classifiers). The agent reads this to pick an estimator and propose a grid.

    Also reports `inferential_models`: the `SPECS` registry (chunk 15 §3.1) of classical-inference
    models (OLS, Logit, Poisson/Gamma GLM) node 1 may validate `modelling.inferential_model`
    against. `task` does not filter these -- an inference model is chosen by `target.type`, not a
    predictive task."""
    out: dict[str, JsonValue] = {}
    for name, entry in estimators().items():
        if task is not None and entry.task != task:
            continue
        out[name] = {
            "task": entry.task,
            "family": entry.family,
            "default_grid": {
                k: [_param_json(v) for v in values] for k, values in entry.default_grid.items()
            },
            "handles_missing": entry.handles_missing,
            "supports_class_weight": entry.supports_class_weight,
            "supports_proba": entry.task == "classification",
            "interpretable": entry.interpretable,
        }
    inferential: dict[str, JsonValue] = {
        name: {
            "tool": spec.tool,
            "target_types": list(spec.target_types),
            "family": spec.family,
        }
        for name, spec in SPECS.items()
    }
    return ToolSuccess(summary={"estimators": out, "inferential_models": inferential})


# --- 3.2 train_estimator (H, [v1]) ---------------------------------------------------------------


@tool(phase="training", allow_in=_TRAIN_PHASES, heavy=True)
def train_estimator(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    name: _Name,
    seed: _Seed,
    params: _Params = _NO_PARAMS,
    class_weight: _ClassWeight = "none",
    calibrate: Annotated[
        bool, Field(description="Wrap a classifier in CalibratedClassifierCV (train-only, D34b).")
    ] = False,
) -> ToolSuccess:
    """Fit one estimator on train (D34a/b own tuning and calibration; this owns the single fit).

    Every non-target column is a feature (D-d); a string/datetime/categorical feature is
    `tool_param` ("encode first"). An unknown `name`, a `params` key the estimator does not accept,
    and naming `n_jobs`/`random_state` are each `tool_param` (D17/D34e own the latter two).
    `class_weight="balanced"` on an estimator without support is `tool_param`. Task compatibility
    is checked by shape (D-f). With `calibrate`, the estimator must be a classifier, every class
    needs at least `cv_folds` train rows (P1), and the fit is wrapped in
    `CalibratedClassifierCV(cv=<shuffled splitter>, method="sigmoid")` on train.

    The fit is on a `float64` numpy matrix, never a DataFrame -- a feature-name `UserWarning` would
    be fatal under `-W error`. Saves a `FittedModel` as kind `model`.
    """
    entry = _entry(name)
    _reject_class_weight(entry, class_weight)
    _validate_params(entry, tuple(params))

    data = load_dataframe(paths.artifact(train, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not in train",
            field_errors={"target": f"not a column in train: {target!r}"},
        )
    features = feature_columns(data, target)
    check_features_usable(data, features)
    check_task(data, target, entry.task)

    x = build_matrix(data, features)
    check_no_missing(x, entry)
    y = data[target]

    cv_folds, _ = tuning_from_snapshot(paths)
    estimator = entry.build(seed, class_weight)
    estimator = _set_params(estimator, params)

    calibrated = False
    if calibrate:
        if entry.task != "classification":
            raise ToolParamError(
                "calibrate requires a classifier",
                field_errors={"calibrate": "only a classification estimator can be calibrated"},
            )
        check_class_counts(y, cv_folds)
        estimator = _wrap_calibrated(estimator, cv_folds, seed)
        calibrated = True

    # D17 addendum 50: forest and extra-trees trees fit on threads; predictions stay
    # single-threaded because a threaded forest sums tree outputs in completion order
    # (last-bit nondeterminism).
    threaded = entry.family in ("random_forest", "extra_trees") and not calibrated
    if threaded:
        from joblib import parallel_config

        _set_params(estimator, {"n_jobs": fit_threads()})
        with parallel_config(backend="threading"):
            fitted = _fit(estimator, x, y, entry.task)
        _set_params(fitted, {"n_jobs": 1})
    else:
        fitted = _fit(estimator, x, y, entry.task)
    classes = _classes(fitted) if entry.task == "classification" else ()

    model = FittedModel(
        name=name,
        task=entry.task,
        target=target,
        features=tuple(features),
        classes=classes,
        params=dict(params),
        class_weight=class_weight,
        calibrated=calibrated,
        seed=seed,
        estimator=fitted,
    )
    meta = _save_model_artifact(paths, model)
    summary: dict[str, JsonValue] = {
        "name": name,
        "task": entry.task,
        "features": json_scalar(len(features)),
        "train_rows": json_scalar(len(data)),
        "classes": [json_scalar(c) for c in classes],
        "params": _effective_params(fitted),
        "calibrated": calibrated,
    }
    if calibrated:
        summary["cv_folds"] = json_scalar(cv_folds)
    return ToolSuccess(summary=summary, artifacts=(meta,))


def _set_params(estimator: object, params: dict[str, ParamValue]) -> object:
    """`estimator.set_params(**params)` returning the estimator as `object` (sklearn's return is a
    dynamic value, P6). Empty `params` is a no-op."""
    if not params:
        return estimator
    from sklearn.base import BaseEstimator

    cast(BaseEstimator, estimator).set_params(**params)
    return estimator


def _wrap_calibrated(estimator: object, cv_folds: int, seed: int) -> object:
    """`CalibratedClassifierCV(estimator, cv=<shuffled StratifiedKFold>, method="sigmoid")` (D34b,
    D-c), returned as `object`."""
    from sklearn.calibration import CalibratedClassifierCV

    splitter = cv_splitter(cv_folds, "classification", seed)
    return CalibratedClassifierCV(estimator, cv=splitter, method="sigmoid")


def _fit(estimator: object, x: np.ndarray, y: pd.Series, task: Task) -> object:
    """Fit on the `float64` numpy matrix (never a DataFrame, D-note). The target crosses as a
    numpy array too: a classifier keeps the labels' own dtype (object for strings), a regressor
    goes `float64`. Returns the fitted estimator as `object`."""
    from sklearn.base import BaseEstimator

    y_arr = y.to_numpy() if task == "classification" else y.to_numpy(dtype="float64")
    cast(BaseEstimator, estimator).fit(x, y_arr)
    return estimator


# --- 3.2 tune_estimator (H) ----------------------------------------------------------------------


def _monotonic() -> float:
    """The tuning budget clock; tests patch this function, never the global `time.monotonic`,
    which joblib's threaded waits also read."""
    return time.monotonic()


@tool(phase="training", allow_in=_TRAIN_PHASES, heavy=True)
def tune_estimator(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    name: _Name,
    metric: Annotated[Metric, Field(description="The metric to maximise (natural direction).")],
    seed: _Seed,
    param_grid: Annotated[
        dict[str, tuple[ParamValue, ...]] | None,
        Field(description="Grid to search; None uses the estimator's default_grid."),
    ] = None,
    class_weight: _ClassWeight = "none",
) -> ToolSuccess:
    """Tune hyperparameters by CV within train only (D34a). Returns the best params, not a model:
    the node passes them to `train_estimator`, which owns calibration and class weights (D-b).

    The loop is a manual `ParameterGrid` iteration with `cross_val_score`, because a `GridSearchCV`
    call cannot stop partway (D-b). After each grid point the elapsed time (via this module's
    `_monotonic`, so a test can advance it) is checked against the snapshot's `max_fit_seconds`;
    when the budget is exceeded the loop stops and returns the best point so far with
    `points_evaluated < points_total` and a warning (D36c). At least one point is always evaluated.
    The splitter shuffles with the seed (D-c), and `cv_folds` comes from the snapshot (D-a).

    `param_grid=None` uses the entry's `default_grid`; an empty grid is `tool_param` (call
    `train_estimator`). Each key is validated as in `train_estimator`, each value is non-empty, and
    the grid has at most `MAX_GRID_POINTS` points. A multiclass `brier` is `tool_param` (no
    multiclass scorer). CV scores are sign-corrected to the metric's natural direction (an RMSE is
    positive). CV folds run on `jobs` threads (D17 addendum 42); results are identical to the
    sequential loop. No artifact.
    """
    from joblib import parallel_config
    from sklearn.model_selection import ParameterGrid, cross_val_score

    entry = _entry(name)
    _reject_class_weight(entry, class_weight)
    check_metric_task(metric, entry.task)

    grid = entry.default_grid if param_grid is None else param_grid
    _validate_grid(entry, grid)

    data = load_dataframe(paths.artifact(train, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not in train",
            field_errors={"target": f"not a column in train: {target!r}"},
        )
    features = feature_columns(data, target)
    check_features_usable(data, features)
    check_task(data, target, entry.task)

    x = build_matrix(data, features)
    check_no_missing(x, entry)
    y = data[target]

    cv_folds, budget_seconds = tuning_from_snapshot(paths)
    multiclass = False
    if entry.task == "classification":
        check_class_counts(y, cv_folds)
        multiclass = int(y.nunique()) > 2
        if multiclass and is_binary_only(metric):
            raise ToolParamError(
                f"metric {metric!r} has no multiclass scorer",
                field_errors={"metric": f"{metric!r} is binary-only"},
            )

    scorer = scorer_for(metric, multiclass=multiclass)
    splitter = cv_splitter(cv_folds, entry.task, seed)
    y_arr = y.to_numpy() if entry.task == "classification" else y.to_numpy(dtype="float64")

    jobs = min(cv_folds, parallel_jobs(name))
    points = list(ParameterGrid(dict(grid)))
    points_total = len(points)
    results: list[JsonValue] = []
    best_params: dict[str, ParamValue] = {}
    best_mean = -np.inf
    started = _monotonic()
    evaluated = 0
    stopped_early = False
    for point in points:
        estimator = _set_params(entry.build(seed, class_weight), dict(point))
        with parallel_config(backend="threading"):
            scores = cross_val_score(estimator, x, y_arr, scoring=scorer, cv=splitter, n_jobs=jobs)
        raw = np.asarray(scores, dtype="float64")
        evaluated += 1
        mean = float(raw.mean())
        std = float(raw.std())
        natural_mean = -mean if is_negated(metric) else mean
        results.append(
            {
                "params": {k: _param_json(v) for k, v in point.items()},
                "mean": json_scalar(natural_mean),
                "std": json_scalar(std),
            }
        )
        if mean > best_mean:
            best_mean = mean
            best_params = {str(k): cast("ParamValue", v) for k, v in point.items()}
        if _monotonic() - started > budget_seconds and evaluated < points_total:
            stopped_early = True
            break

    best_natural = -best_mean if is_negated(metric) else best_mean
    warnings_out: list[str] = []
    if stopped_early:
        warnings_out.append(
            f"tuning stopped after {evaluated}/{points_total} points at the "
            f"{budget_seconds}s budget (D36c); returning the best so far"
        )
    summary: dict[str, JsonValue] = {
        "best_params": {k: _param_json(v) for k, v in best_params.items()},
        "best_score": json_scalar(best_natural),
        "metric": metric,
        "greater_is_better": greater_is_better(metric),
        "points_evaluated": json_scalar(evaluated),
        "points_total": json_scalar(points_total),
        "budget_seconds": json_scalar(budget_seconds),
        "stopped_early": stopped_early,
        "results": results,
    }
    return ToolSuccess(summary=summary, warnings=tuple(warnings_out))


def check_metric_task(metric: Metric, task: Task) -> None:
    """Metric and estimator task must agree (D-f)."""
    from statlab_contracts.vocabulary import METRICS_FOR_TASK

    if metric not in METRICS_FOR_TASK[task]:
        raise ToolParamError(
            f"metric {metric!r} is not a {task} metric",
            field_errors={"metric": f"{metric!r} does not match the estimator's task {task!r}"},
        )


def _validate_grid(entry: EstimatorEntry, grid: dict[str, tuple[ParamValue, ...]]) -> None:
    """An empty grid is `tool_param` (nothing to tune); each key must be a real param (as in
    `train_estimator`); each value must be non-empty; the point count is at most
    `MAX_GRID_POINTS`."""
    if not grid:
        raise ToolParamError(
            "param_grid is empty; there is nothing to tune -- use train_estimator",
            field_errors={"param_grid": "must have at least one key with values"},
        )
    _validate_params(entry, tuple(grid))
    empty = [k for k, v in grid.items() if len(v) == 0]
    if empty:
        raise ToolParamError(
            f"param_grid values for {empty!r} are empty",
            field_errors={"param_grid": f"empty value lists: {empty!r}"},
        )
    total = 1
    for values in grid.values():
        total *= len(values)
    if total > MAX_GRID_POINTS:
        raise ToolParamError(
            f"param_grid has {total} points, more than MAX_GRID_POINTS={MAX_GRID_POINTS}",
            field_errors={"param_grid": f"{total} points exceeds {MAX_GRID_POINTS}"},
        )


# --- 3.2 predict / predict_proba ([v1]) ----------------------------------------------------------


@tool(phase="training", allow_in=_PREDICT_PHASES, transform_only=True)
def predict(paths: RunPaths, model: _Model, frame: _Frame) -> ToolSuccess:
    """Predict on `frame`, selecting exactly the fitted feature columns (D-d), ignoring extras such
    as the target. A missing fitted column is `tool_param`; NaN with an estimator that cannot
    handle it is `tool_param` (P2). The output is kind `predictions`, indexed by the frame's
    labels (chunk 14 aligns to truth by label), with one column `prediction`."""
    fitted = load_fitted(paths, model, FittedModel, "model")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    x = predict_matrix(data, fitted)
    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, fitted.estimator)
    preds = np.asarray(est.predict(x))
    out = pd.DataFrame({"prediction": preds}, index=data.index)
    meta = _save_predictions(paths, out, f"{fitted.name}_prediction")
    summary: dict[str, JsonValue] = {"rows": json_scalar(len(out)), "columns": ["prediction"]}
    return ToolSuccess(summary=summary, artifacts=(meta,))


@tool(phase="training", allow_in=_PREDICT_PHASES, transform_only=True)
def predict_proba(paths: RunPaths, model: _Model, frame: _Frame) -> ToolSuccess:
    """Class probabilities on `frame`, selecting the fitted features (D-d). A regressor is
    `tool_param`. The output is kind `predictions`, indexed by the frame's labels, one column
    `proba__{class}` per class in `classes` order (chunk 14 aligns to truth by label)."""
    fitted = load_fitted(paths, model, FittedModel, "model")
    if fitted.task != "classification":
        raise ToolParamError(
            "predict_proba needs a classifier",
            field_errors={"model": "a regressor has no class probabilities"},
        )
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    x = predict_matrix(data, fitted)
    from sklearn.base import BaseEstimator

    est = cast(BaseEstimator, fitted.estimator)
    proba = np.asarray(est.predict_proba(x), dtype="float64")
    columns = [f"proba__{as_str_scalar(cls)}" for cls in fitted.classes]
    out = pd.DataFrame(proba, columns=columns, index=data.index)
    meta = _save_predictions(paths, out, f"{fitted.name}_proba")
    summary: dict[str, JsonValue] = {
        "rows": json_scalar(len(out)),
        "columns": [str(c) for c in columns],
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


def predict_matrix(frame: pd.DataFrame, fitted: FittedModel) -> np.ndarray:
    """The `float64` matrix for a fitted model's features, in the fitted order (D-d). A missing
    fitted column is `tool_param`; NaN with a non-`handles_missing` estimator is `tool_param`."""
    missing = [col for col in fitted.features if col not in frame.columns]
    if missing:
        raise ToolParamError(
            f"frame is missing fitted feature columns {missing!r}",
            field_errors={"frame": f"missing fitted features: {missing!r}"},
        )
    x = frame[list(fitted.features)].to_numpy(dtype="float64", na_value=np.nan)
    entry = estimators()[fitted.name]
    check_no_missing(x, entry)
    return x


# --- 3.2 get_model_params ------------------------------------------------------------------------


@tool(phase="training", allow_in=_PARAMS_PHASES)
def get_model_params(paths: RunPaths, model: _Model) -> ToolSuccess:
    """Summarise a `FittedModel`'s metadata and effective params (§3.2). No artifact."""
    fitted = load_fitted(paths, model, FittedModel, "model")
    summary: dict[str, JsonValue] = {
        "name": fitted.name,
        "task": fitted.task,
        "target": fitted.target,
        "features": json_scalar(len(fitted.features)),
        "feature_names": [json_scalar(f) for f in fitted.features],
        "classes": [json_scalar(c) for c in fitted.classes],
        "class_weight": fitted.class_weight,
        "calibrated": fitted.calibrated,
        "seed": json_scalar(fitted.seed),
        "params": _effective_params(fitted.estimator),
    }
    return ToolSuccess(summary=summary)
