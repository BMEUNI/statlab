"""The eleven evaluation tools (`PLAN.md` §6.3's `evaluation` phase, this chunk's
`14_tools_evaluation.md`): `score_classification`, `score_regression`,
`compute_confusion_matrix`, `compute_roc_curve`, `compute_pr_curve`,
`compute_calibration_curve`, `select_threshold`, `plot_curve`, `cross_validate_estimator`,
`compute_learning_curve`, `compare_models`. This is where D33 becomes code: every score carries a
seeded bootstrap 95 % CI (D33b), and `compare_models` says when the top two overlap (D33b/D-f).
`rank_models` (the champion phase) lives in `champion.py`.

sklearn is untyped here (`ignore_missing_imports`), imported lazily inside tool bodies so the
package import stays scipy-free (chunk 09's `test_s11`, chunk 12's lazy-import fact). Its arrays
and metric results are implicit dynamic values; every one is narrowed at the boundary
(`np.asarray(..., dtype=float)`, `float(...)`) so the dynamic type does not leak onto this
module's typed surface and no new `# type: ignore` is needed -- the same pattern `training.py`
uses.

sklearn facts this module is written against (verified under `-W error`, the spec's §1):
a classification resample that drops a class makes AUC raise and `balanced_accuracy` warn (P2), so
classification bootstraps resample **within each true class** (stratified) and every metric is
defined in every resample; regression uses a plain bootstrap. `roc_curve`'s first threshold is
`inf` and strict JSON rejects it (P4), so that first point is stored with a `null` threshold.
precision/recall/f1 use `zero_division=0` or a zero-division `UndefinedMetricWarning` is fatal.
`learning_curve(..., n_jobs=1, shuffle=True, random_state=seed)` with a seeded splitter works
(P5). `n_jobs=1` everywhere (D17's deadlock).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Final, Literal, cast

import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import (
    METRICS_FOR_TASK,
    ClassWeight,
    Metric,
    ParamValue,
    Task,
)

from statlab_tools.artifacts import load_dataframe, load_json, save_json, save_plot
from statlab_tools.registry import greater_is_better, is_binary_only, is_negated, scorer_for
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import as_str, as_str_scalar, is_numeric_column, json_scalar
from statlab_tools.tools.training import (
    build_matrix,
    check_class_counts,
    check_features_usable,
    check_no_missing,
    check_task,
    cv_splitter,
    feature_columns,
    tuning_from_snapshot,
)

BOOTSTRAP_RESAMPLES: Final = 1000
"""Bootstrap resamples per score (D-e). A percentile 95 % interval is read off these. Tests set
this lower with `monkeypatch`; exactly one test runs the real 1000 (P1)."""

_CI_LOW_PCT: Final = 2.5
_CI_HIGH_PCT: Final = 97.5

_SCORE_PHASES = ("model_evaluation", "champion_selection")
_CURVE_PHASES = ("model_evaluation", "model_interpretation", "champion_selection")
_CV_PHASES = ("model_evaluation",)

_Truth = Annotated[
    ArtifactRef, Field(description="A frame holding the true `target` column; aligned by label.")
]
_Probabilities = Annotated[
    ArtifactRef, Field(description="A predict_proba predictions artifact (proba__{class} columns).")
]
_Predictions = Annotated[
    ArtifactRef, Field(description="A predict predictions artifact with a `prediction` column.")
]
_Target = Annotated[str, Field(min_length=1, description="The target column in `truth`.")]
_Seed = Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)]
_Curve = Annotated[ArtifactRef, Field(description="A curve artifact from a compute_*_curve tool.")]
_CvTrain = Annotated[ArtifactRef, Field(description="The train split frame; CV happens here.")]
_Name = Annotated[str, Field(min_length=1, description="An estimator name from list_estimators.")]
_CvMetric = Annotated[Metric, Field(description="The metric to score, in its natural direction.")]
_Params = Annotated[dict[str, ParamValue], Field(description="Hyperparameter overrides.")]
_CvClassWeight = Annotated[
    ClassWeight, Field(description="Class weighting; 'balanced' needs a supporting classifier.")
]

_NO_PARAMS: dict[str, ParamValue] = {}
"""Shared empty default for `params`; never mutated (ruff B006)."""


# --- alignment & truth -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Aligned:
    """Truth and predictions reordered to the predictions' label order (D-b). `truth` is the
    target series; `frame` is the aligned prediction frame (both share the predictions' index)."""

    truth: pd.Series
    frame: pd.DataFrame


def load_truth_target(paths: RunPaths, truth: ArtifactRef, target: str) -> pd.Series:
    """The `target` column of the truth frame, or `tool_param` naming it."""
    data = load_dataframe(paths.artifact(truth, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not a column in truth",
            field_errors={"target": f"not a column in truth: {target!r}"},
        )
    return data[target]


def _align(truth: pd.Series, preds: pd.DataFrame) -> _Aligned:
    """Reorder `truth` to `preds`' label order (D-b). The two indices must be equal as sets --
    same labels, same count -- else `tool_param` naming the counts. This never aligns by
    position: a shuffled prediction order must score identically to an aligned one, which is
    only true if truth is looked up by label."""
    truth_labels = set(truth.index)
    pred_labels = set(preds.index)
    if truth_labels != pred_labels or len(truth) != len(preds):
        missing = sorted(str(x) for x in truth_labels - pred_labels)
        extra = sorted(str(x) for x in pred_labels - truth_labels)
        raise ToolParamError(
            f"truth and predictions do not cover the same labels: "
            f"{len(truth)} truth rows, {len(preds)} prediction rows; "
            f"missing from predictions={missing!r}, extra in predictions={extra!r}",
            field_errors={
                "truth": f"label mismatch (truth {len(truth)}, predictions {len(preds)})"
            },
        )
    return _Aligned(truth=truth.reindex(preds.index), frame=preds)


def snapshot(paths: RunPaths) -> DataContract | None:
    """The contract snapshot, or `None` when none is present (tests calling tool bodies without
    writing one). The same read `training.tuning_from_snapshot` does."""
    if paths.contract_snapshot_json.exists():
        return DataContract.model_validate_json(paths.contract_snapshot_json.read_text())
    return None


def proba_columns(frame: pd.DataFrame) -> list[str]:
    """The `proba__{class}` columns of a predict_proba artifact, in stored order. A predictions
    artifact without any is `tool_param` (D-a: classification tools take probabilities)."""
    cols = [c for c in frame.columns if str(c).startswith("proba__")]
    if not cols:
        raise ToolParamError(
            "the predictions artifact has no proba__ columns; score_classification needs "
            "predict_proba output, not predict",
            field_errors={"probabilities": "no proba__ columns (use predict_proba)"},
        )
    return [str(c) for c in cols]


def class_labels(proba_columns: list[str]) -> list[str]:
    """The class suffixes of `proba__{class}` columns (the classes, by string form, D-b)."""
    return [c[len("proba__") :] for c in proba_columns]


def check_truth_classes_have_columns(truth: pd.Series, classes: list[str]) -> None:
    """Every truth class (by string form) must have a probability column, else `tool_param`
    naming it (D-b)."""
    truth_classes = set(as_str(truth).dropna().unique())
    missing = sorted(truth_classes - set(classes))
    if missing:
        raise ToolParamError(
            f"truth classes {missing!r} have no proba__ column",
            field_errors={"truth": f"classes without a probability column: {missing!r}"},
        )


def _positive_class(
    snapshot: DataContract | None, classes: list[str]
) -> tuple[str, Literal["contract", "default"]]:
    """The positive class and its source (D-c). The snapshot's `target.positive_class` matched by
    string form (`as_str_scalar`, the same rule the proba columns and truth are stringified by) if
    present among `classes`; a contract `positive_class` matching no class is `tool_data`, never a
    silent fallback (a float-coded `positive_class=0` must not quietly score the last column). With
    no contract `positive_class`, the last column, sklearn's convention, with source `"default"`."""
    if snapshot is not None and snapshot.target.positive_class is not None:
        wanted = as_str_scalar(snapshot.target.positive_class)
        if wanted not in classes:
            raise ToolDataError(
                f"contract positive_class {snapshot.target.positive_class!r} is not one of the "
                f"observed classes {classes!r}"
            )
        return wanted, "contract"
    return classes[-1], "default"


def _truth_fingerprint(truth: pd.Series) -> str:
    """A sha256 of the sorted truth labels paired with their target values (D-d). Two score
    tables are comparable only when this matches: same rows, same truth. Sorting by label makes
    it order-invariant, so a shuffled prediction order yields the same fingerprint."""
    ordered = truth.sort_index()
    payload = "\n".join(f"{label!r}={value!r}" for label, value in ordered.items())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- bootstrap -------------------------------------------------------------------------------


def _strata(y: np.ndarray) -> list[np.ndarray]:
    """Row indices of each true class, in `np.unique` order."""
    return [np.flatnonzero(y == cls) for cls in np.unique(y)]


def _stratified_indices(strata: list[np.ndarray], rng: np.random.Generator) -> np.ndarray:
    """Row indices for one classification resample, drawn **within each true class** (P2). Class
    counts are fixed, so every metric (AUC, balanced accuracy, ...) is defined in every resample
    -- a plain bootstrap can drop a rare class entirely and make AUC raise under `-W error`."""
    parts = [rng.choice(members, size=members.size, replace=True) for members in strata]
    return np.concatenate(parts)


def _plain_indices(n: int, rng: np.random.Generator) -> np.ndarray:
    """Row indices for one regression resample: a plain bootstrap over all `n` rows."""
    return rng.choice(n, size=n, replace=True)


def _bootstrap_cis(
    metric_values: dict[str, object],
    compute: Callable[[np.ndarray], dict[str, object]],
    *,
    y: np.ndarray,
    resamples: int,
    seed: int,
    stratified: bool,
) -> dict[str, JsonValue]:
    """Point estimate (on the full data, never the bootstrap mean, D-e) plus a percentile 95 % CI
    per metric. All metrics share the same resample indices, so their CIs are coherent (D-e).

    `metric_values` maps a metric name to its full-data value; `compute` is a callable mapping a
    resample's row indices to a `{name: value}` dict. A classification resample is stratified
    within the true class (P2); regression is a plain bootstrap.
    """
    names = list(metric_values)
    draws: dict[str, list[float]] = {name: [] for name in names}
    rng = np.random.default_rng(seed)
    n = int(y.shape[0])
    strata = _strata(y) if stratified else None
    for _ in range(resamples):
        idx = _stratified_indices(strata, rng) if strata is not None else _plain_indices(n, rng)
        resampled = compute(idx)
        for name in names:
            draws[name].append(float(cast(float, resampled[name])))
    out: dict[str, JsonValue] = {}
    for name in names:
        arr = np.asarray(draws[name], dtype=float)
        lo = float(np.percentile(arr, _CI_LOW_PCT))
        hi = float(np.percentile(arr, _CI_HIGH_PCT))
        out[name] = {
            "value": json_scalar(float(cast(float, metric_values[name]))),
            "ci_low": json_scalar(lo),
            "ci_high": json_scalar(hi),
        }
    return out


def _save_table(paths: RunPaths, payload: dict[str, JsonValue], label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_json(paths.artifact(ref, "table"), payload, ref=ref, kind="table", label=label)


def save_curve(paths: RunPaths, payload: dict[str, JsonValue], label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_json(paths.artifact(ref, "curve"), payload, ref=ref, kind="curve", label=label)


# --- 3.1 score_classification ----------------------------------------------------------------


def hard_labels(
    proba: np.ndarray, classes: list[str], positive: str, threshold: float | None
) -> np.ndarray:
    """Hard predicted labels from probabilities: argmax over classes, or for binary a
    `p(positive) >= threshold` rule when `threshold` is set (D-a)."""
    return np.asarray(np.asarray(classes)[_hard_codes(proba, classes.index(positive), threshold)])


# --- fast classification metrics ---


def _class_codes(values: np.ndarray, classes: list[str]) -> np.ndarray:
    """Position of each value in `classes` (every value is present: the caller has checked)."""
    lookup = {c: i for i, c in enumerate(classes)}
    return np.asarray([lookup[v] for v in values.tolist()], dtype=np.intp)


def _hard_codes(proba: np.ndarray, pos_idx: int, threshold: float | None) -> np.ndarray:
    """Hard class codes: argmax, or for binary `p(positive) >= threshold`."""
    if threshold is None:
        return np.argmax(proba, axis=1)
    return np.where(proba[:, pos_idx] >= threshold, pos_idx, 1 - pos_idx)


def _binary_auc(scores: np.ndarray, is_pos: np.ndarray) -> float:
    """ROC AUC by the rank-sum (Mann-Whitney) identity; average ranks for ties."""
    n_pos = int(is_pos.sum())
    n_neg = is_pos.size - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ToolDataError("roc_auc is undefined: the truth has only one class")
    order = np.argsort(scores, kind="mergesort")
    s = scores[order]
    starts = np.r_[0, np.flatnonzero(np.diff(s)) + 1]
    ends = np.r_[starts[1:], s.size]
    ranks = np.empty(s.size, dtype=np.float64)
    ranks[order] = np.repeat((starts + ends + 1) / 2.0, ends - starts)
    return float((ranks[is_pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _binary_ap(scores: np.ndarray, is_pos: np.ndarray) -> float:
    """Step-wise average precision (sklearn's definition)."""
    if int(is_pos.sum()) == 0:
        raise ToolDataError("pr_auc is undefined: the truth has no positive rows")
    order = np.argsort(scores, kind="mergesort")[::-1]
    s = scores[order]
    y = is_pos[order].astype(np.float64)
    threshold_idx = np.r_[np.flatnonzero(np.diff(s)), y.size - 1]
    tps = np.cumsum(y)[threshold_idx]
    fps = threshold_idx + 1 - tps
    precision = tps / (tps + fps)
    recall = tps / tps[-1]
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def _classification_metrics(
    codes: np.ndarray,
    proba: np.ndarray,
    pred: np.ndarray,
    pos_idx: int,
    average: str,
) -> dict[str, object]:
    """Every `ClassificationMetric`, numpy implementation (sklearn is the test oracle). Same
    definitions as before: zero_division=0, macro one-vs-rest, clipped log loss. Class columns
    are positional (the `proba__` order)."""
    k = proba.shape[1]
    n = codes.size
    support = np.bincount(codes, minlength=k).astype(np.float64)
    predicted = np.bincount(pred, minlength=k).astype(np.float64)
    tp = np.bincount(codes[codes == pred], minlength=k).astype(np.float64)
    recall_c = np.divide(tp, support, out=np.zeros(k), where=support > 0)
    precision_c = np.divide(tp, predicted, out=np.zeros(k), where=predicted > 0)
    den = support + predicted
    f1_c = np.divide(2 * tp, den, out=np.zeros(k), where=den > 0)
    eps = np.finfo(np.float64).eps
    out: dict[str, object] = {
        "accuracy": float(np.mean(codes == pred)),
        "balanced_accuracy": float(np.mean(recall_c[support > 0])),
        "log_loss": float(-np.mean(np.log(np.clip(proba[np.arange(n), codes], eps, 1 - eps)))),
    }
    if k == 2:
        is_pos = codes == pos_idx
        s = proba[:, pos_idx]
        out["precision"] = float(precision_c[pos_idx])
        out["recall"] = float(recall_c[pos_idx])
        out["f1"] = float(f1_c[pos_idx])
        out["roc_auc"] = _binary_auc(s, is_pos)
        out["pr_auc"] = _binary_ap(s, is_pos)
        out["brier"] = float(np.mean((is_pos - s) ** 2))
    else:
        if average == "macro":
            out["precision"] = float(np.mean(precision_c))
            out["recall"] = float(np.mean(recall_c))
            out["f1"] = float(np.mean(f1_c))
        else:
            total = support.sum()
            out["precision"] = float(np.sum(support * precision_c) / total)
            out["recall"] = float(np.sum(support * recall_c) / total)
            out["f1"] = float(np.sum(support * f1_c) / total)
        out["roc_auc"] = float(np.mean([_binary_auc(proba[:, c], codes == c) for c in range(k)]))
        out["pr_auc"] = float(np.mean([_binary_ap(proba[:, c], codes == c) for c in range(k)]))
        one_hot = codes[:, None] == np.arange(k)[None, :]
        out["brier"] = float(np.mean(np.sum((proba - one_hot) ** 2, axis=1)))
    return out


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def score_classification(
    paths: RunPaths,
    probabilities: _Probabilities,
    truth: _Truth,
    target: _Target,
    seed: _Seed,
    threshold: Annotated[
        float | None,
        Field(default=None, gt=0, lt=1, description="Binary p(positive)>=threshold; binary only."),
    ] = None,
    average: Annotated[
        Literal["macro", "weighted"],
        Field(description="Averaging for multiclass precision/recall/f1."),
    ] = "macro",
) -> ToolSuccess:
    """Score a classifier's probabilities against truth, every metric with a seeded bootstrap CI.

    Classification tools take probabilities (D-a); a `predict` artifact without `proba__` columns
    is `tool_param`. Hard labels come from argmax, or for binary from `p(positive) >= threshold`.
    Truth is a frame plus `target`, aligned to the predictions by label (D-b) -- a label mismatch,
    or a truth class with no probability column, is `tool_param`. The positive class is the
    snapshot's `target.positive_class` by string form, else the last column (D-c).

    All nine `ClassificationMetric` values are computed with numpy (sklearn is the test
    oracle). The point estimate is on the full data; the 95 % CI is a percentile interval over a
    **stratified** bootstrap (P2: resampling within each true class keeps every metric
    defined). `threshold` is binary only -- a value on a multiclass target is `tool_param`.
    Written as a `table` artifact
    (D-d) carrying the metrics, `n_rows`, `task`, `positive_class` and a `truth_fingerprint`.
    """
    preds = load_dataframe(paths.artifact(probabilities, "predictions"))
    proba_cols = proba_columns(preds)
    classes = class_labels(proba_cols)
    truth_series = load_truth_target(paths, truth, target)
    aligned = _align(truth_series, preds)
    check_truth_classes_have_columns(aligned.truth, classes)
    if threshold is not None and len(classes) != 2:
        raise ToolParamError(
            "threshold is binary only; this target has more than two classes",
            field_errors={"threshold": "not applicable to a multiclass target"},
        )

    positive, source = _positive_class(snapshot(paths), classes)
    y_true = as_str(aligned.truth).to_numpy()
    proba = aligned.frame[proba_cols].to_numpy(dtype="float64")
    pos_idx = classes.index(positive)
    codes = _class_codes(y_true, classes)
    pred = _hard_codes(proba, pos_idx, threshold)

    full = _classification_metrics(codes, proba, pred, pos_idx, average)

    def compute(idx: np.ndarray) -> dict[str, object]:
        return _classification_metrics(codes[idx], proba[idx], pred[idx], pos_idx, average)

    resamples = BOOTSTRAP_RESAMPLES
    metrics = _bootstrap_cis(
        full, compute, y=y_true, resamples=resamples, seed=seed, stratified=True
    )
    payload: dict[str, JsonValue] = {
        "task": "classification",
        "metrics": metrics,
        "n_rows": json_scalar(len(y_true)),
        "classes": list(classes),
        "positive_class": positive,
        "positive_class_source": source,
        "threshold": json_scalar(threshold),
        "resamples": json_scalar(resamples),
        "truth_fingerprint": _truth_fingerprint(aligned.truth),
    }
    meta = _save_table(paths, payload, "scores")
    return ToolSuccess(summary=payload, artifacts=(meta,))


# --- 3.1 score_regression --------------------------------------------------------------------


def _regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[dict[str, object], bool]:
    """The five `RegressionMetric` values with sklearn as the oracle. `mape` is `None` (reported
    as `null` with a warning) when any truth value is 0 -- its denominator is `|y_true|`. Returns
    `(metrics, mape_undefined)`."""
    from sklearn.metrics import (
        mean_absolute_error,
        mean_absolute_percentage_error,
        median_absolute_error,
        r2_score,
        root_mean_squared_error,
    )

    out: dict[str, object] = {
        "rmse": root_mean_squared_error(y_true, y_pred),
        "mae": mean_absolute_error(y_true, y_pred),
        "r2": r2_score(y_true, y_pred),
        "medae": median_absolute_error(y_true, y_pred),
    }
    mape_undefined = bool(np.any(y_true == 0.0))
    if not mape_undefined:
        out["mape"] = mean_absolute_percentage_error(y_true, y_pred)
    return out, mape_undefined


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def score_regression(
    paths: RunPaths,
    predictions: _Predictions,
    truth: _Truth,
    target: _Target,
    seed: _Seed,
) -> ToolSuccess:
    """Score a regressor's predictions against truth, every metric with a seeded bootstrap CI.

    Regression takes `predictions` (one `prediction` column), aligned to truth by label (D-b). The
    five `RegressionMetric` values are computed with sklearn as the oracle; the point estimate is
    on the full data and the 95 % CI is a percentile interval over a plain bootstrap (D-e). `mape`
    is reported as `null` with a warning when any truth value is 0 (its denominator). Written as a
    `table` artifact (D-d) with `n_rows`, `task` and a `truth_fingerprint`.
    """
    preds = load_dataframe(paths.artifact(predictions, "predictions"))
    if "prediction" not in preds.columns:
        raise ToolParamError(
            "the predictions artifact has no `prediction` column; score_regression needs "
            "predict output",
            field_errors={"predictions": "no `prediction` column"},
        )
    truth_series = load_truth_target(paths, truth, target)
    if not is_numeric_column(truth_series):
        raise ToolParamError(
            f"target {target!r} is not numeric; score_regression needs a numeric target",
            field_errors={"target": "not a numeric column"},
        )
    aligned = _align(truth_series, preds)
    y_true = aligned.truth.to_numpy(dtype="float64")
    y_pred = aligned.frame["prediction"].to_numpy(dtype="float64")

    full, mape_undefined = _regression_metrics(y_true, y_pred)

    def compute(idx: np.ndarray) -> dict[str, object]:
        metrics, _ = _regression_metrics(y_true[idx], y_pred[idx])
        return metrics

    resamples = BOOTSTRAP_RESAMPLES
    metrics = _bootstrap_cis(
        full, compute, y=y_true, resamples=resamples, seed=seed, stratified=False
    )
    if mape_undefined:
        metrics["mape"] = {"value": None, "ci_low": None, "ci_high": None}
    warnings: list[str] = []
    if mape_undefined:
        warnings.append("mape is undefined because at least one truth value is 0; reported as null")
    payload: dict[str, JsonValue] = {
        "task": "regression",
        "metrics": metrics,
        "n_rows": json_scalar(len(y_true)),
        "truth_fingerprint": _truth_fingerprint(aligned.truth),
    }
    meta = _save_table(paths, payload, "scores")
    return ToolSuccess(summary=payload, artifacts=(meta,), warnings=tuple(warnings))


# --- 3.2 compute_confusion_matrix ------------------------------------------------------------


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def compute_confusion_matrix(
    paths: RunPaths,
    probabilities: _Probabilities,
    truth: _Truth,
    target: _Target,
    threshold: Annotated[
        float | None,
        Field(default=None, gt=0, lt=1, description="Binary p(positive)>=threshold; binary only."),
    ] = None,
) -> ToolSuccess:
    """The confusion matrix of a classifier's hard predictions against truth. No bootstrap.

    Rows are truth, columns are predicted, both in class order (D-a hard-label rule applies). Per
    class `support` (truth count). Written as a `table` artifact; the summary carries `labels`,
    `matrix` and `support`.
    """
    preds = load_dataframe(paths.artifact(probabilities, "predictions"))
    proba_cols = proba_columns(preds)
    classes = class_labels(proba_cols)
    truth_series = load_truth_target(paths, truth, target)
    aligned = _align(truth_series, preds)
    check_truth_classes_have_columns(aligned.truth, classes)
    if threshold is not None and len(classes) != 2:
        raise ToolParamError(
            "threshold is binary only; this target has more than two classes",
            field_errors={"threshold": "not applicable to a multiclass target"},
        )
    positive, _ = _positive_class(snapshot(paths), classes)
    y_true = as_str(aligned.truth).to_numpy()
    proba = aligned.frame[proba_cols].to_numpy(dtype="float64")
    y_pred = hard_labels(proba, classes, positive, threshold)

    from sklearn.metrics import confusion_matrix

    cm = np.asarray(confusion_matrix(y_true, y_pred, labels=classes), dtype=int)
    matrix: list[JsonValue] = [[int(v) for v in row] for row in cm]
    support: dict[str, JsonValue] = {
        cls: json_scalar(int((y_true == cls).sum())) for cls in classes
    }
    payload: dict[str, JsonValue] = {
        "labels": list(classes),
        "matrix": matrix,
        "support": support,
    }
    meta = _save_table(paths, payload, "confusion_matrix")
    return ToolSuccess(summary=payload, artifacts=(meta,))


# --- 3.2 ROC / PR curves ---------------------------------------------------------------------


def _binary_setup(
    paths: RunPaths, probabilities: ArtifactRef, truth: ArtifactRef, target: str
) -> tuple[np.ndarray, np.ndarray, str, int, int]:
    """Shared binary-only loading for the curves: `(y_binary, p_positive, positive, n_pos,
    n_neg)`. A multiclass target is `tool_param` (the three curves are binary only)."""
    preds = load_dataframe(paths.artifact(probabilities, "predictions"))
    proba_cols = proba_columns(preds)
    classes = class_labels(proba_cols)
    truth_series = load_truth_target(paths, truth, target)
    aligned = _align(truth_series, preds)
    check_truth_classes_have_columns(aligned.truth, classes)
    if len(classes) != 2:
        raise ToolParamError(
            "this curve is binary only; the target has more than two classes",
            field_errors={"probabilities": "multiclass target not supported"},
        )
    positive, _ = _positive_class(snapshot(paths), classes)
    pos_idx = classes.index(positive)
    y_str = as_str(aligned.truth).to_numpy()
    y_bin = (y_str == positive).astype(int)
    p_pos = aligned.frame[proba_cols[pos_idx]].to_numpy(dtype="float64")
    return y_bin, p_pos, positive, int(y_bin.sum()), int((y_bin == 0).sum())


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def compute_roc_curve(
    paths: RunPaths, probabilities: _Probabilities, truth: _Truth, target: _Target
) -> ToolSuccess:
    """The ROC curve (false-positive rate vs true-positive rate) of a binary classifier.

    Binary only; a multiclass target is `tool_param`. Writes a `curve` artifact with `x` (fpr),
    `y` (tpr), `thresholds`, `auc`, `n_pos`, `n_neg` and `positive_class`. `roc_curve`'s first
    threshold is `inf`, which strict JSON rejects (P4), so it is stored as `null`.
    """
    y_bin, p_pos, positive, n_pos, n_neg = _binary_setup(paths, probabilities, truth, target)
    from sklearn.metrics import roc_auc_score, roc_curve

    fpr, tpr, thr = roc_curve(y_bin, p_pos)
    fpr_a = np.asarray(fpr, dtype="float64")
    tpr_a = np.asarray(tpr, dtype="float64")
    thr_a = np.asarray(thr, dtype="float64")
    thresholds: list[JsonValue] = [
        None if not np.isfinite(t) else json_scalar(float(t)) for t in thr_a
    ]
    auc = float(roc_auc_score(y_bin, p_pos))
    payload: dict[str, JsonValue] = {
        "kind": "roc",
        "x": [json_scalar(float(v)) for v in fpr_a],
        "y": [json_scalar(float(v)) for v in tpr_a],
        "thresholds": thresholds,
        "auc": json_scalar(auc),
        "n_pos": json_scalar(n_pos),
        "n_neg": json_scalar(n_neg),
        "positive_class": positive,
    }
    meta = save_curve(paths, payload, "roc")
    return ToolSuccess(
        summary={"kind": "roc", "auc": json_scalar(auc), "points": json_scalar(len(fpr_a))},
        artifacts=(meta,),
    )


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def compute_pr_curve(
    paths: RunPaths, probabilities: _Probabilities, truth: _Truth, target: _Target
) -> ToolSuccess:
    """The precision-recall curve of a binary classifier.

    Binary only; a multiclass target is `tool_param`. Writes a `curve` artifact with `x` (recall),
    `y` (precision), `thresholds`, `auc` (average precision), `n_pos`, `n_neg` and
    `positive_class`. `precision_recall_curve` returns one more precision/recall point than
    thresholds (the (recall=0, precision=1) endpoint), so that trailing point's threshold is
    `null`.
    """
    y_bin, p_pos, positive, n_pos, n_neg = _binary_setup(paths, probabilities, truth, target)
    from sklearn.metrics import average_precision_score, precision_recall_curve

    precision, recall, thr = precision_recall_curve(y_bin, p_pos)
    prec_a = np.asarray(precision, dtype="float64")
    rec_a = np.asarray(recall, dtype="float64")
    thr_a = np.asarray(thr, dtype="float64")
    # precision/recall have one more point than thresholds: pad the trailing endpoint with null.
    thresholds: list[JsonValue] = [json_scalar(float(t)) for t in thr_a]
    thresholds.append(None)
    auc = float(average_precision_score(y_bin, p_pos))
    payload: dict[str, JsonValue] = {
        "kind": "pr",
        "x": [json_scalar(float(v)) for v in rec_a],
        "y": [json_scalar(float(v)) for v in prec_a],
        "thresholds": thresholds,
        "auc": json_scalar(auc),
        "n_pos": json_scalar(n_pos),
        "n_neg": json_scalar(n_neg),
        "positive_class": positive,
    }
    meta = save_curve(paths, payload, "pr")
    return ToolSuccess(
        summary={"kind": "pr", "auc": json_scalar(auc), "points": json_scalar(len(rec_a))},
        artifacts=(meta,),
    )


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def compute_calibration_curve(
    paths: RunPaths,
    probabilities: _Probabilities,
    truth: _Truth,
    target: _Target,
    n_bins: Annotated[
        int, Field(default=10, ge=2, le=50, description="Number of probability bins.")
    ] = 10,
) -> ToolSuccess:
    """The calibration (reliability) curve of a binary classifier.

    Binary only; a multiclass target is `tool_param`. Writes a `curve` artifact with `x`
    (mean predicted probability per bin), `y` (fraction of positives per bin), `n_bins`, `n_pos`,
    `n_neg` and `positive_class`. Empty bins are dropped, so the curve has `n_bins` or fewer
    points.
    """
    y_bin, p_pos, positive, n_pos, n_neg = _binary_setup(paths, probabilities, truth, target)
    from sklearn.calibration import calibration_curve

    frac_pos, mean_pred = calibration_curve(y_bin, p_pos, n_bins=n_bins)
    mean_pred_a = np.asarray(mean_pred, dtype="float64")
    frac_pos_a = np.asarray(frac_pos, dtype="float64")
    payload: dict[str, JsonValue] = {
        "kind": "calibration",
        "x": [json_scalar(float(v)) for v in mean_pred_a],
        "y": [json_scalar(float(v)) for v in frac_pos_a],
        "n_bins": json_scalar(n_bins),
        "n_pos": json_scalar(n_pos),
        "n_neg": json_scalar(n_neg),
        "positive_class": positive,
    }
    meta = save_curve(paths, payload, "calibration")
    return ToolSuccess(
        summary={
            "kind": "calibration",
            "auc": None,
            "points": json_scalar(len(mean_pred_a)),
        },
        artifacts=(meta,),
    )


# --- 3.2 select_threshold --------------------------------------------------------------------


def _require_curve(
    paths: RunPaths, curve: ArtifactRef, kind: str, field: str
) -> dict[str, JsonValue]:
    """Load a `curve` artifact and check its `kind`, else `tool_param`."""
    loaded = load_json(paths.artifact(curve, "curve"))
    if not isinstance(loaded, dict):
        raise ToolParamError(
            f"{field} is not a curve object",
            field_errors={field: "not a curve artifact"},
        )
    actual = loaded.get("kind")
    if actual != kind:
        raise ToolParamError(
            f"{field} is a {actual!r} curve, not {kind!r}",
            field_errors={field: f"expected a {kind!r} curve, got {actual!r}"},
        )
    return loaded


@tool(phase="evaluation", allow_in=_CURVE_PHASES)
def select_threshold(
    paths: RunPaths,
    curve: _Curve,
    objective: Annotated[
        Literal["f1", "target_recall", "min_cost"],
        Field(
            description="f1: maximise F1. target_recall: highest thr with recall>=target_recall."
            " min_cost: minimise FP + cost_fn_to_fp*FN."
        ),
    ],
    target_recall: Annotated[
        float, Field(default=0.8, gt=0, le=1, description="Used by objective='target_recall' only.")
    ] = 0.8,
    cost_fn_to_fp: Annotated[
        float, Field(default=1.0, gt=0, description="Used by objective='min_cost' only.")
    ] = 1.0,
) -> ToolSuccess:
    """Pick a decision threshold on a ROC curve for a stated objective.

    Requires a `roc` curve artifact; any other kind is `tool_param`. Counts come from the curve's
    `fpr` (`x`), `tpr` (`y`), `n_pos` and `n_neg`. `f1` maximises F1; `target_recall` takes the
    highest threshold with recall >= `target_recall`; `min_cost` minimises
    `FP + cost_fn_to_fp * FN`. A threshold chosen on a validation curve is itself a selection
    decision (D33a), not an unbiased operating point. The summary carries `threshold`,
    `precision`, `recall` and `objective_value`.
    """
    loaded = _require_curve(paths, curve, "roc", "curve")
    fpr = np.asarray(cast("list[float]", loaded["x"]), dtype="float64")
    tpr = np.asarray(cast("list[float]", loaded["y"]), dtype="float64")
    raw_thr = cast("list[float | None]", loaded["thresholds"])
    n_pos = int(cast(int, loaded["n_pos"]))
    n_neg = int(cast(int, loaded["n_neg"]))

    tp = tpr * n_pos
    fn = n_pos - tp
    fp = fpr * n_neg
    with np.errstate(invalid="ignore", divide="ignore"):
        precision = np.where((tp + fp) > 0, tp / (tp + fp), 0.0)
    recall = tpr

    if objective == "f1":
        denom = precision + recall
        with np.errstate(invalid="ignore", divide="ignore"):
            f1 = np.where(denom > 0, 2 * precision * recall / denom, 0.0)
        best = int(np.argmax(f1))
        objective_value = float(f1[best])
    elif objective == "target_recall":
        eligible = np.flatnonzero(recall >= target_recall)
        if eligible.size == 0:
            raise ToolParamError(
                f"no threshold reaches recall >= {target_recall}",
                field_errors={"target_recall": f"unreachable on this curve: {target_recall}"},
            )
        # the highest threshold with recall >= the floor: among eligible points, the one with the
        # smallest recall (a higher threshold admits fewer positives, so recall only decreases).
        best = int(eligible[int(np.argmin(recall[eligible]))])
        objective_value = float(recall[best])
    else:
        cost = fp + cost_fn_to_fp * fn
        best = int(np.argmin(cost))
        objective_value = float(cost[best])

    thr_value = raw_thr[best]
    summary: dict[str, JsonValue] = {
        "threshold": json_scalar(thr_value),
        "precision": json_scalar(float(precision[best])),
        "recall": json_scalar(float(recall[best])),
        "objective_value": json_scalar(objective_value),
    }
    return ToolSuccess(summary=summary)


# --- 3.2 plot_curve --------------------------------------------------------------------------


def new_figure() -> tuple[Figure, Axes]:
    """A fresh `Figure` without `pyplot` (chunk 09 P5): pyplot's registry is process-global and
    not thread-safe, and tool bodies run concurrently in worker threads."""
    fig = Figure(figsize=(8, 5), dpi=100)
    ax = fig.add_subplot()
    return fig, ax


_CURVE_LABELS: Final[dict[str, tuple[str, str]]] = {
    "roc": ("false positive rate", "true positive rate"),
    "pr": ("recall", "precision"),
    "calibration": ("mean predicted probability", "fraction of positives"),
    "learning": ("training set size", "score"),
}


def _plot_partial_dependence(ax: Axes, loaded: dict[str, JsonValue]) -> tuple[str, str]:
    """Draw a `partial_dependence` curve: one line per series over `x_original` (else `x`), and
    return `(xlabel, ylabel)`. The x axis is the column name, with "(scaled)" when the grid is
    scaled but no scaler mapped it back; the y axis is "average prediction" for regression or
    "average P(class)" for classification (chunk 18 §3.3)."""
    column = str(loaded["column"])
    x = np.asarray(cast("list[float]", loaded["x"]), dtype="float64")
    x_original_raw = loaded["x_original"]
    if x_original_raw is None:
        x_axis = x
    else:
        x_axis = np.asarray(cast("list[float]", x_original_raw), dtype="float64")
    series = cast("dict[str, JsonValue]", loaded["series"])
    for name, values in series.items():
        y = np.asarray(cast("list[float]", values), dtype="float64")
        ax.plot(x_axis, y, marker="o", label=str(name))
    if len(series) > 1:
        ax.legend()
    # x_original is null only when no scaler was supplied, so the grid is still in model units,
    # which for a scaled feature are standardised -- flag that uncertainty with "(scaled)".
    xlabel = column if x_original_raw is not None else f"{column} (scaled)"
    ylabel = "average prediction" if str(loaded["task"]) == "regression" else "average P(class)"
    return xlabel, ylabel


@tool(phase="evaluation", allow_in=_CURVE_PHASES)
def plot_curve(
    paths: RunPaths,
    curve: _Curve,
    title: Annotated[str, Field(default="", max_length=120, description="Plot title.")] = "",
) -> ToolSuccess:
    """Render a curve artifact as a PNG. Handles `roc`, `pr`, `calibration`, `learning` and
    `partial_dependence`.

    An unknown curve kind is `tool_param`. A learning curve plots train and validation means
    against size; a partial-dependence curve plots one line per series over the column's grid
    (its original units when a scaler mapped them back, chunk 18 §3.3); the other three plot `y`
    against `x`. Every text call passes `parse_math=False` (chunk 09): a user-supplied `title`
    with paired `$` would otherwise be read as mathtext and render mangled or raise mid-render.
    """
    loaded = load_json(paths.artifact(curve, "curve"))
    if not isinstance(loaded, dict):
        raise ToolParamError("curve is not a curve object", field_errors={"curve": "not a curve"})
    kind = loaded.get("kind")
    if kind != "partial_dependence" and kind not in _CURVE_LABELS:
        raise ToolParamError(
            f"unknown curve kind {kind!r}",
            field_errors={"curve": f"unknown kind: {kind!r}"},
        )
    kind_str = str(kind)
    fig, ax = new_figure()
    if kind_str == "partial_dependence":
        xlabel, ylabel = _plot_partial_dependence(ax, loaded)
    elif kind_str == "learning":
        xlabel, ylabel = _CURVE_LABELS[kind_str]
        sizes = np.asarray(cast("list[float]", loaded["sizes"]), dtype="float64")
        train_mean = np.asarray(cast("list[float]", loaded["train_mean"]), dtype="float64")
        valid_mean = np.asarray(cast("list[float]", loaded["valid_mean"]), dtype="float64")
        ax.plot(sizes, train_mean, marker="o", label="train")
        ax.plot(sizes, valid_mean, marker="o", label="valid")
        ax.legend()
    else:
        xlabel, ylabel = _CURVE_LABELS[kind_str]
        x = np.asarray(cast("list[float]", loaded["x"]), dtype="float64")
        y = np.asarray(cast("list[float]", loaded["y"]), dtype="float64")
        ax.plot(x, y, marker="o")
    ax.set_xlabel(xlabel, parse_math=False)
    ax.set_ylabel(ylabel, parse_math=False)
    ax.set_title(title or f"{kind_str} curve", parse_math=False)
    fig.tight_layout()
    ref = new_artifact_ref()
    meta = save_plot(paths.artifact(ref, "plot"), fig, ref=ref, label=f"{kind_str} curve")
    return ToolSuccess(summary={"kind": kind_str}, artifacts=(meta,))


# --- 3.3 cross_validate_estimator / compute_learning_curve ----------------------------------


def _cv_prepare(
    paths: RunPaths, train: ArtifactRef, target: str, name: str, metric: Metric, seed: int
) -> tuple[np.ndarray, np.ndarray, Task, object, object, bool, int]:
    """Shared CV preparation for both CV tools: load, validate, build the matrix, splitter and
    scorer. Reuses chunk 13's helpers verbatim (made public there). Returns
    `(x, y_arr, task, splitter, scorer, greater, cv_folds)`."""
    from statlab_tools.registry import estimators

    reg = estimators()
    entry = reg.get(name)
    if entry is None:
        raise ToolParamError(
            f"unknown estimator {name!r}; valid names: {sorted(reg)!r}",
            field_errors={"name": f"unknown estimator; one of {sorted(reg)!r}"},
        )
    if metric not in METRICS_FOR_TASK[entry.task]:
        raise ToolParamError(
            f"metric {metric!r} is not a {entry.task} metric",
            field_errors={"metric": f"{metric!r} does not match the estimator's task"},
        )
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
    multiclass = False
    if entry.task == "classification":
        check_class_counts(y, cv_folds)
        multiclass = int(y.nunique()) > 2
        if multiclass and is_binary_only(metric):
            raise ToolParamError(
                f"metric {metric!r} has no multiclass scorer",
                field_errors={"metric": f"{metric!r} is binary-only"},
            )
    y_arr = y.to_numpy() if entry.task == "classification" else y.to_numpy(dtype="float64")
    splitter = cv_splitter(cv_folds, entry.task, seed)
    scorer = scorer_for(metric, multiclass=multiclass)
    return x, y_arr, entry.task, splitter, scorer, greater_is_better(metric), cv_folds


def _set_params(estimator: object, params: dict[str, ParamValue]) -> object:
    """`estimator.set_params(**params)` returning `object` (sklearn's return is dynamic)."""
    if not params:
        return estimator
    from sklearn.base import BaseEstimator

    cast(BaseEstimator, estimator).set_params(**params)
    return estimator


@tool(phase="evaluation", allow_in=_CV_PHASES, heavy=True)
def cross_validate_estimator(
    paths: RunPaths,
    train: _CvTrain,
    target: _Target,
    name: _Name,
    metric: _CvMetric,
    seed: _Seed,
    params: _Params = _NO_PARAMS,
    class_weight: _CvClassWeight = "none",
) -> ToolSuccess:
    """Cross-validate one estimator on train, reporting the per-fold scores (D34a: CV on train).

    Reuses chunk 13's matrix builder, CV splitter (contract `cv_folds`, shuffled with the seed)
    and scorer mapping. The scores are sign-corrected to the metric's natural direction (an RMSE
    is positive). `n_jobs=1` (D17). No artifact. The summary carries `metric`, `fold_scores`,
    `mean`, `std` and `greater_is_better`.
    """
    from sklearn.model_selection import cross_val_score

    x, y_arr, _task, splitter, scorer, greater, _ = _cv_prepare(
        paths, train, target, name, metric, seed
    )
    from statlab_tools.registry import estimators

    estimator = _set_params(estimators()[name].build(seed, class_weight), dict(params))
    raw = np.asarray(
        cross_val_score(estimator, x, y_arr, scoring=scorer, cv=splitter, n_jobs=1),
        dtype="float64",
    )
    natural = -raw if is_negated(metric) else raw
    summary: dict[str, JsonValue] = {
        "metric": metric,
        "fold_scores": [json_scalar(float(v)) for v in natural],
        "mean": json_scalar(float(natural.mean())),
        "std": json_scalar(float(natural.std())),
        "greater_is_better": greater,
    }
    return ToolSuccess(summary=summary)


@tool(phase="evaluation", allow_in=_CV_PHASES, heavy=True)
def compute_learning_curve(
    paths: RunPaths,
    train: _CvTrain,
    target: _Target,
    name: _Name,
    metric: _CvMetric,
    seed: _Seed,
    params: _Params = _NO_PARAMS,
    class_weight: _CvClassWeight = "none",
) -> ToolSuccess:
    """A learning curve: train and validation scores at five training-set sizes (D34a on train).

    `train_sizes=np.linspace(0.2, 1.0, 5)`, the seeded splitter, `shuffle=True`, `n_jobs=1` (P5,
    D17). Scores are sign-corrected to the metric's natural direction. Writes a `curve` artifact
    of kind `learning` with `sizes`, `train_mean`, `train_std`, `valid_mean`, `valid_std`.
    """
    from sklearn.model_selection import learning_curve

    x, y_arr, _task, splitter, scorer, _, _ = _cv_prepare(paths, train, target, name, metric, seed)
    from statlab_tools.registry import estimators

    estimator = _set_params(estimators()[name].build(seed, class_weight), dict(params))
    sizes, train_scores, valid_scores = learning_curve(
        estimator,
        x,
        y_arr,
        train_sizes=np.linspace(0.2, 1.0, 5),
        cv=splitter,
        scoring=scorer,
        n_jobs=1,
        shuffle=True,
        random_state=seed,
    )
    sizes_a = np.asarray(sizes, dtype="float64")
    train_a = np.asarray(train_scores, dtype="float64")
    valid_a = np.asarray(valid_scores, dtype="float64")
    sign = -1.0 if is_negated(metric) else 1.0
    train_mean = sign * train_a.mean(axis=1)
    valid_mean = sign * valid_a.mean(axis=1)
    payload: dict[str, JsonValue] = {
        "kind": "learning",
        "sizes": [json_scalar(int(v)) for v in sizes_a],
        "train_mean": [json_scalar(float(v)) for v in train_mean],
        "train_std": [json_scalar(float(v)) for v in train_a.std(axis=1)],
        "valid_mean": [json_scalar(float(v)) for v in valid_mean],
        "valid_std": [json_scalar(float(v)) for v in valid_a.std(axis=1)],
    }
    meta = save_curve(paths, payload, "learning")
    return ToolSuccess(
        summary={"metric": metric, "sizes": json_scalar(len(sizes_a))}, artifacts=(meta,)
    )


# --- 3.4 compare_models ----------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreTable:
    """A loaded score table: the slice `compare_models`/`rank_models` need."""

    task: str
    fingerprint: str
    metrics: dict[str, dict[str, float | None]]


def load_score_table(paths: RunPaths, ref: ArtifactRef, candidate: str) -> ScoreTable:
    """Load a score table written by `score_*`, or `tool_param` if the artifact is not one."""
    loaded = load_json(paths.artifact(ref, "table"))
    if not isinstance(loaded, dict) or "metrics" not in loaded or "truth_fingerprint" not in loaded:
        raise ToolParamError(
            f"candidate {candidate!r} is not a score table",
            field_errors={"candidates": f"{candidate!r} is not a score_* table"},
        )
    metrics_raw = cast("dict[str, dict[str, float | None]]", loaded["metrics"])
    return ScoreTable(
        task=str(loaded["task"]),
        fingerprint=str(loaded["truth_fingerprint"]),
        metrics=metrics_raw,
    )


def _ci_overlap(a: dict[str, float | None], b: dict[str, float | None]) -> bool:
    """Whether two metric CIs overlap (D-f): `a.low <= b.high and b.low <= a.high`."""
    a_lo, a_hi = a["ci_low"], a["ci_high"]
    b_lo, b_hi = b["ci_low"], b["ci_high"]
    if a_lo is None or a_hi is None or b_lo is None or b_hi is None:
        return True
    return a_lo <= b_hi and b_lo <= a_hi


def _rank_candidates(tables: dict[str, ScoreTable], metric: Metric) -> list[tuple[str, ScoreTable]]:
    """Candidates ranked by the metric's value in its natural direction; ties keep label order."""
    greater = greater_is_better(metric)
    items = list(tables.items())
    # stable sort on the negated-or-not value; Python's sort is stable so ties keep input order.
    return sorted(
        items,
        key=lambda kv: (
            -cast(float, kv[1].metrics[metric]["value"])
            if greater
            else cast(float, kv[1].metrics[metric]["value"])
        ),
    )


@tool(phase="evaluation", allow_in=_SCORE_PHASES)
def compare_models(
    paths: RunPaths,
    candidates: Annotated[
        dict[str, ArtifactRef],
        Field(min_length=2, description="Candidate label -> its score_* table artifact."),
    ],
    metric: Annotated[Metric, Field(description="The metric to rank by.")],
) -> ToolSuccess:
    """Rank candidate score tables by one metric, flagging whether the top two are distinguishable.

    `candidates` maps a label to a `score_*` table (at least two). Tables with mixed `task`, a
    metric that does not belong to the task, or differing `truth_fingerprint` (candidates scored
    on different rows, D26/D-d) are each `tool_param` -- the fingerprint error names which
    candidates differ. Ranks by the metric's natural direction; ties keep label order. Writes a
    `table` artifact of `{rank, candidate, value, ci_low, ci_high}` rows. `top_two_distinguishable`
    is false when the leader's CI overlaps the runner-up's (D-f): a conservative "not
    distinguishable on this validation set", not a significance test.
    """
    tables = {label: load_score_table(paths, ref, label) for label, ref in candidates.items()}
    tasks = {t.task for t in tables.values()}
    if len(tasks) > 1:
        raise ToolParamError(
            f"candidates mix tasks {sorted(tasks)!r}; they are not comparable",
            field_errors={"candidates": f"mixed tasks: {sorted(tasks)!r}"},
        )
    task = cast(Task, next(iter(tasks)))
    if metric not in METRICS_FOR_TASK[task]:
        raise ToolParamError(
            f"metric {metric!r} is not a {task} metric",
            field_errors={"metric": f"{metric!r} does not belong to task {task!r}"},
        )
    fingerprints = {label: t.fingerprint for label, t in tables.items()}
    distinct = set(fingerprints.values())
    if len(distinct) > 1:
        raise ToolParamError(
            f"candidates were scored on different rows (truth fingerprints differ): "
            f"{fingerprints!r}",
            field_errors={"candidates": f"differing truth fingerprints: {fingerprints!r}"},
        )

    ranked = _rank_candidates(tables, metric)
    ranking: list[JsonValue] = []
    for rank, (label, tbl) in enumerate(ranked, start=1):
        m = tbl.metrics[metric]
        ranking.append(
            {
                "rank": json_scalar(rank),
                "candidate": label,
                "value": json_scalar(m["value"]),
                "ci_low": json_scalar(m["ci_low"]),
                "ci_high": json_scalar(m["ci_high"]),
            }
        )
    top_two_distinguishable = not _ci_overlap(
        ranked[0][1].metrics[metric], ranked[1][1].metrics[metric]
    )
    payload: dict[str, JsonValue] = {"metric": metric, "ranking": ranking}
    meta = _save_table(paths, payload, "comparison")
    summary: dict[str, JsonValue] = {
        "metric": metric,
        "ranking": ranking,
        "top_two_distinguishable": top_two_distinguishable,
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))
