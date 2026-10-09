"""Chunk 14 §4: the eleven evaluation tools.

The heart of the suite is metric correctness checked against sklearn as an *oracle* (every point
estimate equals sklearn's own function on the same inputs, binary and multiclass), and the three
tests the spec names as most likely to pass by construction:
  * the stratified-bootstrap test (5 positives / 43 negatives) scores every metric without a
    warning under `-W error`, and **fails under a plain bootstrap** (the mutation);
  * the alignment test shuffles the prediction order and asserts an identical score, so an
    implementation that ignores labels scores differently;
  * the positive-class test flips binary precision/recall by setting the contract's
    `positive_class`, so an implementation that always uses the last column disagrees.

An autouse fixture lowers `BOOTSTRAP_RESAMPLES` to 200 (P1: the real 1000 is 2.8 s on 48 rows);
exactly one test runs the real 1000.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactKind, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_json, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools import evaluation as ev
from statlab_tools.tools.evaluation import (
    compare_models,
    compute_calibration_curve,
    compute_confusion_matrix,
    compute_learning_curve,
    compute_pr_curve,
    compute_roc_curve,
    cross_validate_estimator,
    plot_curve,
    score_classification,
    score_regression,
    select_threshold,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]

_RESAMPLES_IN_TESTS = 50
"""P1: real sklearn resamples are ~2.8 ms each (9 metrics) -- 50 keeps the suite fast while still
giving a stable enough percentile CI for the correctness tests. Seed 1 is one where a plain
bootstrap still drops the rare class at 50 resamples (the mutation test relies on it)."""


@pytest.fixture(autouse=True)
def _fast_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1: 1000 real sklearn resamples on 48 rows is 2.8 s. Every test runs at 200 except the
    one that asserts the real 1000 (which sets it back explicitly)."""
    monkeypatch.setattr(ev, "BOOTSTRAP_RESAMPLES", _RESAMPLES_IN_TESTS)


# --- fixtures / helpers --------------------------------------------------------------------------


def _d(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return cast("dict[str, JsonValue]", value)


def _l(value: object) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _f(value: object) -> float:
    assert isinstance(value, (int, float))
    return float(value)


def _s(value: object) -> str:
    assert isinstance(value, str)
    return value


def _pd(value: JsonValue) -> dict[str, JsonValue]:
    """A loaded JSON payload narrowed to a dict (artifacts are always objects here)."""
    assert isinstance(value, dict)
    return value


def _pl(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _metric(res: ToolSuccess, name: str) -> dict[str, float | None]:
    """The `{value, ci_low, ci_high}` of one metric in a score summary, narrowed to floats."""
    m = _d(_d(res.summary["metrics"])[name])
    return {k: (None if v is None else _f(v)) for k, v in m.items()}


def _mv(res: ToolSuccess, name: str) -> float:
    """The point estimate of one metric (asserts it is not None)."""
    value = _metric(res, name)["value"]
    assert value is not None
    return value


def _ranking(res: ToolSuccess) -> list[dict[str, JsonValue]]:
    return [_d(row) for row in _l(res.summary["ranking"])]


def _save(paths: RunPaths, frame: pd.DataFrame, kind: ArtifactKind = "dataframe") -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, kind), frame, ref=ref, kind=kind, label="x")
    return ref


def _proba_frame(proba: np.ndarray, classes: list[object]) -> pd.DataFrame:
    return pd.DataFrame(proba, columns=[f"proba__{c}" for c in classes])


def _binary_fit(n: int = 200, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """`(y, proba, classes)` from a real logistic fit on separable-ish data."""
    from sklearn.linear_model import LogisticRegression

    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = ((f1 + f2 + rng.normal(scale=0.3, size=n)) > 0).astype(int)
    x = np.column_stack([f1, f2])
    clf = LogisticRegression(max_iter=1000).fit(x, y)
    return y, np.asarray(clf.predict_proba(x), dtype=float), np.asarray(clf.classes_)


def _multiclass_fit(n: int = 240, seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    from sklearn.ensemble import HistGradientBoostingClassifier

    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = np.asarray(pd.Series(pd.cut(f1, bins=3, labels=["low", "mid", "high"])).astype(str))
    x = np.column_stack([f1, f2])
    clf = HistGradientBoostingClassifier(random_state=seed).fit(x, y)
    return y, np.asarray(clf.predict_proba(x), dtype=float), np.asarray(clf.classes_)


def _binary_refs(
    paths: RunPaths, n: int = 200, seed: int = 0
) -> tuple[ArtifactRef, ArtifactRef, np.ndarray, np.ndarray, list[object]]:
    y, proba, classes = _binary_fit(n, seed)
    truth_ref = _save(paths, pd.DataFrame({"y": y}))
    proba_ref = _save(paths, _proba_frame(proba, list(classes)), "predictions")
    return truth_ref, proba_ref, y, proba, list(classes)


def _write_snapshot(paths: RunPaths, contract: DataContract) -> None:
    paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")


def _binary_contract(positive_class: object) -> DataContract:
    return DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": positive_class}}
    )


# --- metric correctness (oracle) -----------------------------------------------------------------


def test_binary_point_estimates_equal_sklearn(run_paths: RunPaths) -> None:
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        balanced_accuracy_score,
        brier_score_loss,
        f1_score,
        log_loss,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    truth_ref, proba_ref, y, proba, _classes = _binary_refs(run_paths)
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    pred = (proba[:, 1] >= 0.5).astype(int)
    assert _mv(res, "accuracy") == pytest.approx(accuracy_score(y, pred))
    assert _mv(res, "balanced_accuracy") == pytest.approx(balanced_accuracy_score(y, pred))
    assert _mv(res, "precision") == pytest.approx(
        precision_score(y, pred, pos_label=1, zero_division=0)
    )
    assert _mv(res, "recall") == pytest.approx(recall_score(y, pred, pos_label=1, zero_division=0))
    assert _mv(res, "f1") == pytest.approx(f1_score(y, pred, pos_label=1, zero_division=0))
    assert _mv(res, "roc_auc") == pytest.approx(roc_auc_score(y, proba[:, 1]))
    assert _mv(res, "pr_auc") == pytest.approx(average_precision_score(y, proba[:, 1]))
    assert _mv(res, "brier") == pytest.approx(brier_score_loss(y, proba[:, 1]))
    assert _mv(res, "log_loss") == pytest.approx(log_loss(y, proba, labels=[0, 1]))


def test_multiclass_point_estimates_equal_sklearn(run_paths: RunPaths) -> None:
    from sklearn.metrics import accuracy_score, log_loss, roc_auc_score

    y, proba, classes = _multiclass_fit()
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)), "predictions")
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    pred = np.asarray([classes[i] for i in np.argmax(proba, axis=1)])
    assert _mv(res, "accuracy") == pytest.approx(accuracy_score(y, pred))
    assert _mv(res, "roc_auc") == pytest.approx(
        roc_auc_score(y, proba, multi_class="ovr", average="macro")
    )
    assert _mv(res, "log_loss") == pytest.approx(log_loss(y, proba, labels=list(classes)))


def test_regression_point_estimates_equal_sklearn(run_paths: RunPaths) -> None:
    from sklearn.metrics import (
        mean_absolute_error,
        mean_absolute_percentage_error,
        median_absolute_error,
        r2_score,
        root_mean_squared_error,
    )

    rng = np.random.default_rng(0)
    truth = rng.normal(size=150) + 5.0
    pred = truth + rng.normal(scale=0.5, size=150)
    truth_ref = _save(run_paths, pd.DataFrame({"y": truth}))
    pred_ref = _save(run_paths, pd.DataFrame({"prediction": pred}), "predictions")
    res = score_regression(run_paths, pred_ref, truth_ref, target="y", seed=1)
    assert _mv(res, "rmse") == pytest.approx(root_mean_squared_error(truth, pred))
    assert _mv(res, "mae") == pytest.approx(mean_absolute_error(truth, pred))
    assert _mv(res, "r2") == pytest.approx(r2_score(truth, pred))
    assert _mv(res, "medae") == pytest.approx(median_absolute_error(truth, pred))
    assert _mv(res, "mape") == pytest.approx(mean_absolute_percentage_error(truth, pred))


# --- CIs ----------------------------------------------------------------------------------------


def test_ci_brackets_value(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    for name in _d(res.summary["metrics"]):
        m = _metric(res, name)
        lo, value, hi = m["ci_low"], m["value"], m["ci_high"]
        assert lo is not None and value is not None and hi is not None
        assert lo <= value <= hi


def test_same_seed_identical_cis_different_seed_differs(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    a = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    b = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    c = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=2)
    assert _metric(a, "roc_auc") == _metric(b, "roc_auc")
    assert _metric(a, "roc_auc") != _metric(c, "roc_auc")


def test_ci_narrows_as_n_grows(run_paths: RunPaths) -> None:
    def width(n: int) -> float:
        truth_ref, proba_ref, *_ = _binary_refs(run_paths, n=n, seed=5)
        res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
        m = _metric(res, "roc_auc")
        hi, lo = m["ci_high"], m["ci_low"]
        assert hi is not None and lo is not None
        return hi - lo

    assert width(1000) < width(100)


def test_point_estimate_is_full_data_not_bootstrap_mean(run_paths: RunPaths) -> None:
    """D-e: the point estimate is on the full data. Replacing it with the bootstrap mean would
    move it and break the oracle equality -- the mutation. Here we only assert the value is the
    full-data roc_auc, which the bootstrap mean generally is not."""
    from sklearn.metrics import roc_auc_score

    truth_ref, proba_ref, y, proba, _ = _binary_refs(run_paths)
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert _mv(res, "roc_auc") == pytest.approx(roc_auc_score(y, proba[:, 1]))


def test_stratified_bootstrap_scores_rare_class_without_warning(run_paths: RunPaths) -> None:
    """5 positives / 43 negatives: a stratified resample keeps the class counts fixed, so every
    metric is defined in every resample and no warning fires under `-W error` (P2). Seed 1 is one
    where a *plain* bootstrap drops the positive class at least once -- see
    `test_mutation_plain_bootstrap_fails`."""
    y = np.array([1] * 5 + [0] * 43)
    rng = np.random.default_rng(0)
    p1 = np.where(y == 1, rng.uniform(0.5, 0.9, size=48), rng.uniform(0.1, 0.5, size=48))
    proba = np.column_stack([1 - p1, p1])
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, [0, 1]), "predictions")
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert set(_d(res.summary["metrics"])) == {
        "accuracy",
        "balanced_accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc",
        "brier",
        "log_loss",
    }


def test_mutation_plain_bootstrap_fails(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: a plain (non-stratified) classification bootstrap drops the rare class in
    some resample and AUC raises under `-W error`. This is the test that would silently pass a
    by-construction CI, so it must fail when stratification is removed."""
    y = np.array([1] * 5 + [0] * 43)
    rng = np.random.default_rng(0)
    p1 = np.where(y == 1, rng.uniform(0.5, 0.9, size=48), rng.uniform(0.1, 0.5, size=48))
    proba = np.column_stack([1 - p1, p1])
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, [0, 1]), "predictions")
    monkeypatch.setattr(
        ev,
        "_stratified_indices",
        lambda strata, rng: ev._plain_indices(sum(int(m.size) for m in strata), rng),
    )
    with pytest.raises(ToolDataError):
        score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)


def _sklearn_reference(
    y_true: np.ndarray,
    proba: np.ndarray,
    y_pred: np.ndarray,
    classes: list[str],
    positive: str,
    average: str,
) -> dict[str, object]:
    """The previous sklearn `_classification_metrics`, kept as the oracle for the numpy version."""
    from sklearn.metrics import (
        accuracy_score,
        average_precision_score,
        balanced_accuracy_score,
        brier_score_loss,
        f1_score,
        log_loss,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    binary = len(classes) == 2
    pos_idx = classes.index(positive)
    out: dict[str, object] = {
        "accuracy": accuracy_score(y_true, y_pred),
        "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
        "log_loss": log_loss(y_true, proba, labels=classes),
    }
    if binary:
        out["precision"] = precision_score(y_true, y_pred, pos_label=positive, zero_division=0)
        out["recall"] = recall_score(y_true, y_pred, pos_label=positive, zero_division=0)
        out["f1"] = f1_score(y_true, y_pred, pos_label=positive, zero_division=0)
        out["roc_auc"] = roc_auc_score((y_true == positive).astype(int), proba[:, pos_idx])
        out["pr_auc"] = average_precision_score((y_true == positive).astype(int), proba[:, pos_idx])
        out["brier"] = brier_score_loss((y_true == positive).astype(int), proba[:, pos_idx])
    else:
        out["precision"] = precision_score(
            y_true, y_pred, average=average, labels=classes, zero_division=0
        )
        out["recall"] = recall_score(
            y_true, y_pred, average=average, labels=classes, zero_division=0
        )
        out["f1"] = f1_score(y_true, y_pred, average=average, labels=classes, zero_division=0)
        one_hot = np.asarray([[1 if str(t) == c else 0 for c in classes] for t in y_true])
        out["roc_auc"] = roc_auc_score(y_true, proba, multi_class="ovr", average="macro")
        out["pr_auc"] = average_precision_score(one_hot, proba, average="macro")
        out["brier"] = float(np.mean(np.sum((proba - one_hot) ** 2, axis=1)))
    return out


def _assert_fast_matches_reference(
    y: np.ndarray,
    proba: np.ndarray,
    classes: list[str],
    positive: str,
    threshold: float | None,
    average: str,
    reference_labels: dict[str, str] | None = None,
) -> None:
    codes = ev._class_codes(y, classes)
    pos_idx = classes.index(positive)
    pred = ev._hard_codes(proba, pos_idx, threshold)
    rng = np.random.default_rng(1)
    strata = ev._strata(y)
    indexes = [np.arange(y.size)] + [ev._stratified_indices(strata, rng) for _ in range(5)]
    ref_y = y
    ref_classes = classes
    ref_positive = positive
    if reference_labels is not None:
        ref_y = np.asarray([reference_labels[v] for v in y.tolist()])
        ref_classes = [reference_labels[c] for c in classes]
        ref_positive = reference_labels[positive]
    for idx in indexes:
        fast = ev._classification_metrics(codes[idx], proba[idx], pred[idx], pos_idx, average)
        ref_pred = np.asarray([ref_classes[i] for i in pred[idx]])
        ref = _sklearn_reference(
            ref_y[idx], proba[idx], ref_pred, ref_classes, ref_positive, average
        )
        assert set(fast) == set(ref)
        for name, value in fast.items():
            assert value == pytest.approx(ref[name], rel=1e-9, abs=1e-12), name


def test_fast_metrics_match_sklearn_binary() -> None:
    rng = np.random.default_rng(0)
    n = 400
    y = np.where(rng.uniform(size=n) < 0.15, "pos", "neg")
    p = np.round(
        np.where(y == "pos", rng.uniform(0.2, 0.9, size=n), rng.uniform(0.0, 0.6, size=n)), 2
    )
    proba = np.column_stack([1 - p, p])
    for threshold in (None, 0.3):
        _assert_fast_matches_reference(y, proba, ["neg", "pos"], "pos", threshold, "macro")


def test_fast_metrics_match_sklearn_multiclass() -> None:
    rng = np.random.default_rng(0)
    n = 300
    classes = ["a", "b", "c"]
    y = rng.choice(classes, size=n)
    raw = np.round(rng.dirichlet([1.0, 1.0, 1.0], size=n), 3) + 1e-3
    proba = raw / raw.sum(axis=1, keepdims=True)
    for average in ("macro", "weighted"):
        _assert_fast_matches_reference(y, proba, classes, "c", None, average)


def test_fast_metrics_use_positional_class_order() -> None:
    rng = np.random.default_rng(0)
    n = 300
    classes = ["2", "10", "30"]
    y = rng.choice(classes, size=n)
    raw = np.round(rng.dirichlet([1.0, 1.0, 1.0], size=n), 3) + 1e-3
    proba = raw / raw.sum(axis=1, keepdims=True)
    _assert_fast_matches_reference(
        y, proba, classes, "30", None, "macro", reference_labels={"2": "a", "10": "b", "30": "c"}
    )


def test_auc_and_ap_undefined_raise_tool_data_error() -> None:
    scores = np.array([0.1, 0.5, 0.9])
    with pytest.raises(ToolDataError):
        ev._binary_auc(scores, np.array([True, True, True]))
    with pytest.raises(ToolDataError):
        ev._binary_auc(scores, np.array([False, False, False]))
    with pytest.raises(ToolDataError):
        ev._binary_ap(scores, np.array([False, False, False]))


def test_real_1000_resamples_on_48_rows(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one test that runs the real 1000 resamples (P1). Asserts the resample count is reported
    and every metric is still defined (stratified)."""
    monkeypatch.setattr(ev, "BOOTSTRAP_RESAMPLES", 1000)
    y = np.array([1] * 5 + [0] * 43)
    rng = np.random.default_rng(0)
    p1 = np.where(y == 1, rng.uniform(0.5, 0.9, size=48), rng.uniform(0.1, 0.5, size=48))
    proba = np.column_stack([1 - p1, p1])
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, [0, 1]), "predictions")
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert res.summary["resamples"] == 1000
    assert len(_d(res.summary["metrics"])) == 9


# --- alignment (D-b) -----------------------------------------------------------------------------


def test_shuffled_prediction_order_scores_identically(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, _y, proba, classes = _binary_refs(run_paths)
    shuffled = _proba_frame(proba, classes).sample(frac=1.0, random_state=7)
    shuf_ref = _save(run_paths, shuffled, "predictions")
    a = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    b = score_classification(run_paths, shuf_ref, truth_ref, target="y", seed=1)
    assert _mv(a, "roc_auc") == _mv(b, "roc_auc")


def test_mutation_truth_not_reordered_changes_score(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: not reordering truth to the predictions' order. On shuffled predictions the
    score then differs from the aligned one."""
    truth_ref, _proba_ref, _y, proba, classes = _binary_refs(run_paths)
    shuffled = _proba_frame(proba, classes).sample(frac=1.0, random_state=7)
    shuf_ref = _save(run_paths, shuffled, "predictions")

    def no_reorder(truth: pd.Series, preds: pd.DataFrame) -> ev._Aligned:
        return ev._Aligned(truth=truth, frame=preds)

    aligned = score_classification(run_paths, shuf_ref, truth_ref, target="y", seed=1)
    monkeypatch.setattr(ev, "_align", no_reorder)
    misaligned = score_classification(run_paths, shuf_ref, truth_ref, target="y", seed=1)
    assert _mv(aligned, "accuracy") != pytest.approx(_mv(misaligned, "accuracy"))


def test_missing_label_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref, _proba_ref, _y, proba, classes = _binary_refs(run_paths, n=60)
    short = _proba_frame(proba, classes).iloc[:-1]
    short_ref = _save(run_paths, short, "predictions")
    with pytest.raises(ToolParamError, match="do not cover"):
        score_classification(run_paths, short_ref, truth_ref, target="y", seed=1)


def test_truth_class_without_proba_column_is_tool_param(run_paths: RunPaths) -> None:
    y = np.array([0, 1, 2, 0, 1, 2] * 10)
    proba = np.full((60, 2), 0.5)
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, [0, 1]), "predictions")
    with pytest.raises(ToolParamError, match="no proba__ column"):
        score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)


def test_non_range_index_works(run_paths: RunPaths) -> None:
    y, proba, classes = _binary_fit(n=60)
    idx = pd.Index([f"row_{i}" for i in range(60)])
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}, index=idx))
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)).set_index(idx), "predictions")
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert res.summary["n_rows"] == 60


def test_predict_artifact_without_proba_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref = _save(run_paths, pd.DataFrame({"y": [0, 1] * 30}))
    hard = _save(run_paths, pd.DataFrame({"prediction": [0, 1] * 30}), "predictions")
    with pytest.raises(ToolParamError, match="no proba__ columns"):
        score_classification(run_paths, hard, truth_ref, target="y", seed=1)


def test_target_not_in_truth_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths, n=60)
    with pytest.raises(ToolParamError, match="not a column in truth"):
        score_classification(run_paths, proba_ref, truth_ref, target="missing", seed=1)


# --- positive class (D-c) ------------------------------------------------------------------------


def test_contract_positive_class_flips_precision(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, _y, _proba, _classes = _binary_refs(run_paths)
    default = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert default.summary["positive_class_source"] == "default"
    _write_snapshot(run_paths, _binary_contract(0))
    flipped = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert flipped.summary["positive_class"] == "0"
    assert flipped.summary["positive_class_source"] == "contract"
    assert _mv(flipped, "precision") != pytest.approx(_mv(default, "precision"))


def test_mutation_always_last_column_ignores_contract(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: the positive class is always the last column. With the contract setting
    `positive_class=0`, that disagrees with the honoured value."""
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    _write_snapshot(run_paths, _binary_contract(0))
    honoured = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    monkeypatch.setattr(ev, "_positive_class", lambda snapshot, classes: (classes[-1], "default"))
    mutated = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert _mv(honoured, "precision") != pytest.approx(_mv(mutated, "precision"))


def test_string_class_target_works(run_paths: RunPaths) -> None:
    y, proba, classes = _multiclass_fit()
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)), "predictions")
    res = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    assert res.summary["positive_class"] in {str(c) for c in classes}


# --- threshold -----------------------------------------------------------------------------------


def test_threshold_changes_recall_in_right_direction(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    low = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1, threshold=0.1)
    high = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1, threshold=0.9)
    # a lower threshold predicts positive more often -> higher recall for the positive class.
    assert _mv(low, "recall") >= _mv(high, "recall")


def test_threshold_on_multiclass_is_tool_param(run_paths: RunPaths) -> None:
    y, proba, classes = _multiclass_fit()
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)), "predictions")
    with pytest.raises(ToolParamError, match="binary only"):
        score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1, threshold=0.5)


# --- regression ----------------------------------------------------------------------------------


def test_regression_mape_null_with_zero_truth(run_paths: RunPaths) -> None:
    truth = np.array([0.0, 1.0, 2.0, 3.0] * 10)
    pred = truth + 0.1
    truth_ref = _save(run_paths, pd.DataFrame({"y": truth}))
    pred_ref = _save(run_paths, pd.DataFrame({"prediction": pred}), "predictions")
    res = score_regression(run_paths, pred_ref, truth_ref, target="y", seed=1)
    assert _metric(res, "mape")["value"] is None
    assert any("mape" in w for w in res.warnings)


def test_regression_on_non_numeric_target_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref = _save(run_paths, pd.DataFrame({"y": ["a", "b"] * 30}))
    pred_ref = _save(run_paths, pd.DataFrame({"prediction": [1.0, 2.0] * 30}), "predictions")
    with pytest.raises(ToolParamError, match="not numeric"):
        score_regression(run_paths, pred_ref, truth_ref, target="y", seed=1)


def test_regression_without_prediction_column_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref = _save(run_paths, pd.DataFrame({"y": [1.0, 2.0] * 30}))
    bad = _save(run_paths, _proba_frame(np.full((60, 2), 0.5), [0, 1]), "predictions")
    with pytest.raises(ToolParamError, match="no `prediction` column"):
        score_regression(run_paths, bad, truth_ref, target="y", seed=1)


# --- confusion matrix ----------------------------------------------------------------------------


def test_confusion_matrix_matches_sklearn(run_paths: RunPaths) -> None:
    from sklearn.metrics import confusion_matrix

    truth_ref, proba_ref, y, proba, _classes = _binary_refs(run_paths)
    res = compute_confusion_matrix(run_paths, proba_ref, truth_ref, target="y")
    pred = (proba[:, 1] >= 0.5).astype(int)
    expected = confusion_matrix(y.astype(str), pred.astype(str), labels=["0", "1"]).tolist()
    assert res.summary["matrix"] == expected
    assert res.summary["labels"] == ["0", "1"]
    assert _d(res.summary["support"])["1"] == int((y == 1).sum())


# --- curves --------------------------------------------------------------------------------------


def test_roc_curve_auc_equals_sklearn_and_is_strict_json(run_paths: RunPaths) -> None:
    from sklearn.metrics import roc_auc_score

    truth_ref, proba_ref, y, proba, _ = _binary_refs(run_paths)
    res = compute_roc_curve(run_paths, proba_ref, truth_ref, target="y")
    assert res.summary["auc"] == pytest.approx(roc_auc_score(y, proba[:, 1]))
    payload = load_json(run_paths.artifact(res.artifacts[0].ref, "curve"))
    json.dumps(payload, allow_nan=False)  # strict (P4)
    assert _pl(_pd(payload)["thresholds"])[0] is None  # P4: inf -> null


def test_pr_curve_auc_equals_sklearn_and_is_strict_json(run_paths: RunPaths) -> None:
    from sklearn.metrics import average_precision_score

    truth_ref, proba_ref, y, proba, _ = _binary_refs(run_paths)
    res = compute_pr_curve(run_paths, proba_ref, truth_ref, target="y")
    assert res.summary["auc"] == pytest.approx(average_precision_score(y, proba[:, 1]))
    payload = load_json(run_paths.artifact(res.artifacts[0].ref, "curve"))
    json.dumps(payload, allow_nan=False)
    assert len(_pl(_pd(payload)["x"])) == len(_pl(_pd(payload)["thresholds"]))


def test_calibration_curve_has_n_bins_or_fewer_points(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    res = compute_calibration_curve(run_paths, proba_ref, truth_ref, target="y", n_bins=10)
    payload = load_json(run_paths.artifact(res.artifacts[0].ref, "curve"))
    assert 0 < len(_pl(_pd(payload)["x"])) <= 10
    json.dumps(payload, allow_nan=False)


def test_roc_curve_multiclass_is_tool_param(run_paths: RunPaths) -> None:
    y, proba, classes = _multiclass_fit()
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)), "predictions")
    with pytest.raises(ToolParamError, match="binary only"):
        compute_roc_curve(run_paths, proba_ref, truth_ref, target="y")


# --- select_threshold ----------------------------------------------------------------------------


def _hand_roc_curve(run_paths: RunPaths) -> ArtifactRef:
    """A hand-built ROC curve artifact: a saved curve with known fpr/tpr/thresholds and
    10 positives / 10 negatives, so each objective's answer can be hand-computed."""
    payload: dict[str, JsonValue] = {
        "kind": "roc",
        "x": [0.0, 0.1, 0.5, 1.0],  # fpr
        "y": [0.0, 0.8, 0.9, 1.0],  # tpr
        "thresholds": [None, 0.9, 0.5, 0.1],
        "auc": 0.9,
        "n_pos": 10,
        "n_neg": 10,
        "positive_class": "1",
    }
    from statlab_tools.artifacts import save_json

    ref = new_artifact_ref()
    save_json(run_paths.artifact(ref, "curve"), payload, ref=ref, kind="curve", label="roc")
    return ref


def test_select_threshold_f1_matches_hand_computation(run_paths: RunPaths) -> None:
    # at fpr=0.1,tpr=0.8: tp=8,fp=1,fn=2 -> precision=8/9=0.889, recall=0.8, f1=0.842
    # at fpr=0.5,tpr=0.9: tp=9,fp=5,fn=1 -> precision=9/14=0.643, recall=0.9, f1=0.75
    curve = _hand_roc_curve(run_paths)
    res = select_threshold(run_paths, curve, objective="f1")
    assert res.summary["threshold"] == pytest.approx(0.9)
    assert res.summary["recall"] == pytest.approx(0.8)
    assert res.summary["objective_value"] == pytest.approx(0.8421, abs=1e-3)


def test_select_threshold_target_recall_picks_highest_threshold(run_paths: RunPaths) -> None:
    # recall >= 0.85 is met at tpr 0.9 (thr 0.5) and 1.0 (thr 0.1); highest thr is 0.5.
    curve = _hand_roc_curve(run_paths)
    res = select_threshold(run_paths, curve, objective="target_recall", target_recall=0.85)
    assert res.summary["threshold"] == pytest.approx(0.5)
    assert res.summary["recall"] == pytest.approx(0.9)


def test_select_threshold_min_cost_matches_hand_computation(run_paths: RunPaths) -> None:
    # cost = FP + 2*FN at each point: 20, 5, 7, 10 -> min is 5 at thr 0.9.
    curve = _hand_roc_curve(run_paths)
    res = select_threshold(run_paths, curve, objective="min_cost", cost_fn_to_fp=2.0)
    assert res.summary["threshold"] == pytest.approx(0.9)
    assert res.summary["objective_value"] == pytest.approx(5.0)


def test_select_threshold_pr_curve_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    pr = compute_pr_curve(run_paths, proba_ref, truth_ref, target="y")
    with pytest.raises(ToolParamError, match="not 'roc'"):
        select_threshold(run_paths, pr.artifacts[0].ref, objective="f1")


# --- plot_curve ----------------------------------------------------------------------------------


def test_plot_curve_renders_each_kind(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    roc = compute_roc_curve(run_paths, proba_ref, truth_ref, target="y")
    pr = compute_pr_curve(run_paths, proba_ref, truth_ref, target="y")
    cal = compute_calibration_curve(run_paths, proba_ref, truth_ref, target="y")
    lc = compute_learning_curve(
        run_paths,
        _train_frame(run_paths),
        target="y",
        name="logistic_regression",
        metric="roc_auc",
        seed=1,
    )
    for res in (roc, pr, cal, lc):
        out = plot_curve(run_paths, res.artifacts[0].ref, title="t $x$ y")
        assert out.artifacts[0].kind == "plot"
        sidecar = run_paths.artifacts / "charts" / f"{out.artifacts[0].ref}.json"
        spec = cast("dict[str, JsonValue]", json.loads(sidecar.read_text(encoding="utf-8")))
        panel = cast("list[dict[str, JsonValue]]", spec["panels"])[0]
        assert panel["kind"] == "line"
        if res is lc:
            series = cast("list[dict[str, JsonValue]]", panel["series"])
            assert [s["name"] for s in series] == ["train", "valid"]


def test_plot_curve_unknown_kind_is_tool_param(run_paths: RunPaths) -> None:
    from statlab_tools.artifacts import save_json

    ref = new_artifact_ref()
    save_json(
        run_paths.artifact(ref, "curve"),
        {"kind": "nope", "x": [1.0], "y": [1.0]},
        ref=ref,
        kind="curve",
        label="bad",
    )
    with pytest.raises(ToolParamError, match="unknown curve kind"):
        plot_curve(run_paths, ref, title="t")


# --- CV / learning curve -------------------------------------------------------------------------


def _train_frame(run_paths: RunPaths) -> ArtifactRef:
    rng = np.random.default_rng(0)
    n = 150
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = ((f1 + f2) > 0).astype(int)
    return _save(run_paths, pd.DataFrame({"f1": f1, "f2": f2, "y": y}))


def test_cross_validate_fold_count_from_snapshot(
    seeded_run: RunPaths,
    write_snapshot: Callable[[DataContract], None],
    minimal_contract: DataContract,
) -> None:
    contract = minimal_contract.model_copy(
        update={
            "modelling": minimal_contract.modelling.model_copy(
                update={
                    "tuning": minimal_contract.modelling.tuning.model_copy(update={"cv_folds": 3})
                }
            )
        }
    )
    write_snapshot(contract)
    rng = np.random.default_rng(0)
    n = 120
    f1 = rng.normal(size=n)
    train = _save(
        seeded_run, pd.DataFrame({"f1": f1, "f2": rng.normal(size=n), "y": (f1 > 0).astype(int)})
    )
    res = cross_validate_estimator(
        seeded_run, train, target="y", name="logistic_regression", metric="roc_auc", seed=1
    )
    assert len(_l(res.summary["fold_scores"])) == 3


def test_learning_curve_has_five_sizes(run_paths: RunPaths) -> None:
    res = compute_learning_curve(
        run_paths,
        _train_frame(run_paths),
        target="y",
        name="logistic_regression",
        metric="roc_auc",
        seed=1,
    )
    payload = load_json(run_paths.artifact(res.artifacts[0].ref, "curve"))
    assert len(_pl(_pd(payload)["sizes"])) == 5
    assert len(_pl(_pd(payload)["train_mean"])) == 5


def test_cv_is_single_threaded(run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    """D17: nothing in a tool body may spawn workers. A spy on `cross_val_score` asserts
    `n_jobs=1`, as in chunk 13's tuning spy."""
    import sklearn.model_selection as ms

    real = ms.cross_val_score
    seen: list[object] = []

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs.get("n_jobs"))
        return real(*args, **kwargs)

    monkeypatch.setattr(ms, "cross_val_score", spy)
    cross_validate_estimator(
        run_paths,
        _train_frame(run_paths),
        target="y",
        name="logistic_regression",
        metric="roc_auc",
        seed=1,
    )
    assert seen and set(seen) == {1}


def test_cross_validate_rmse_sign_corrected_positive(run_paths: RunPaths) -> None:
    rng = np.random.default_rng(0)
    n = 150
    f1 = rng.normal(size=n)
    train = _save(run_paths, pd.DataFrame({"f1": f1, "f2": rng.normal(size=n), "y": 3 * f1}))
    res = cross_validate_estimator(
        run_paths, train, target="y", name="linear_regression", metric="rmse", seed=1
    )
    assert res.summary["greater_is_better"] is False
    assert all(_f(s) > 0 for s in _l(res.summary["fold_scores"]))


# --- compare_models ------------------------------------------------------------------------------


def _two_score_tables(
    run_paths: RunPaths, *, same_truth: bool = True
) -> tuple[ArtifactRef, ArtifactRef]:
    truth_ref, proba_ref, y, proba, classes = _binary_refs(run_paths, seed=0)
    rng = np.random.default_rng(1)
    weak = np.clip(proba + rng.normal(scale=0.25, size=proba.shape), 1e-6, 1 - 1e-6)
    weak = weak / weak.sum(axis=1, keepdims=True)
    weak_ref = _save(run_paths, _proba_frame(weak, list(classes)), "predictions")
    truth_b = truth_ref if same_truth else _save(run_paths, pd.DataFrame({"y": y[::-1]}))
    a = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=5)
    b = score_classification(run_paths, weak_ref, truth_b, target="y", seed=5)
    return a.artifacts[0].ref, b.artifacts[0].ref


def test_compare_ranks_roc_auc_descending(run_paths: RunPaths) -> None:
    a, b = _two_score_tables(run_paths)
    res = compare_models(run_paths, {"strong": a, "weak": b}, metric="roc_auc")
    ranking = _ranking(res)
    assert ranking[0]["candidate"] == "strong"
    assert _f(ranking[0]["value"]) >= _f(ranking[1]["value"])


def test_compare_ranks_rmse_ascending(run_paths: RunPaths) -> None:
    truth = np.random.default_rng(0).normal(size=120) + 5
    truth_ref = _save(run_paths, pd.DataFrame({"y": truth}))
    good = _save(run_paths, pd.DataFrame({"prediction": truth + 0.1}), "predictions")
    bad = _save(run_paths, pd.DataFrame({"prediction": truth + 2.0}), "predictions")
    sa = score_regression(run_paths, good, truth_ref, target="y", seed=1)
    sb = score_regression(run_paths, bad, truth_ref, target="y", seed=1)
    res = compare_models(
        run_paths, {"good": sa.artifacts[0].ref, "bad": sb.artifacts[0].ref}, metric="rmse"
    )
    assert _ranking(res)[0]["candidate"] == "good"


def test_compare_overlapping_cis_not_distinguishable(run_paths: RunPaths) -> None:
    # two scorings of the same predictions -> identical CIs -> overlap -> not distinguishable.
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    a = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=5)
    b = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=5)
    res = compare_models(
        run_paths, {"a": a.artifacts[0].ref, "b": b.artifacts[0].ref}, metric="roc_auc"
    )
    assert res.summary["top_two_distinguishable"] is False


def test_compare_separated_cis_distinguishable(run_paths: RunPaths) -> None:
    # a near-perfect classifier vs a near-random one on 300 rows: well-separated CIs.
    rng = np.random.default_rng(0)
    n = 300
    y = rng.integers(0, 2, size=n)
    strong = np.where(y == 1, 0.95, 0.05)
    weak = np.full(n, 0.5) + rng.normal(scale=0.01, size=n)
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    sref = _save(
        run_paths, _proba_frame(np.column_stack([1 - strong, strong]), [0, 1]), "predictions"
    )
    wref = _save(run_paths, _proba_frame(np.column_stack([1 - weak, weak]), [0, 1]), "predictions")
    sa = score_classification(run_paths, sref, truth_ref, target="y", seed=5)
    sb = score_classification(run_paths, wref, truth_ref, target="y", seed=5)
    res = compare_models(
        run_paths, {"strong": sa.artifacts[0].ref, "weak": sb.artifacts[0].ref}, metric="roc_auc"
    )
    assert res.summary["top_two_distinguishable"] is True


def test_mutation_ci_overlap_against_third_not_second(run_paths: RunPaths) -> None:
    """DoD mutation: distinguishability computed against the third-ranked candidate instead of the
    second. With a three-candidate set where the top two overlap but the top and third do not,
    the mutated flag would be True while the correct flag is False."""
    rng = np.random.default_rng(0)
    n = 300
    y = rng.integers(0, 2, size=n)
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))

    def p(signal: float) -> ArtifactRef:
        # a noisy latent score: larger `signal` separates the classes more, so AUC rises
        # smoothly from ~0.5 (signal 0) upward. Noise scale 1.0 keeps AUC well below 1.0 so the
        # CI has real width, unlike a clean p=0.55/0.45 split which is rank-perfect (AUC 1.0).
        latent = signal * (2 * y - 1) + rng.normal(scale=1.0, size=n)
        pos = 1.0 / (1.0 + np.exp(-latent))
        pos = np.clip(pos, 1e-6, 1 - 1e-6)
        return _save(
            run_paths, _proba_frame(np.column_stack([1 - pos, pos]), [0, 1]), "predictions"
        )

    top = score_classification(run_paths, p(1.4), truth_ref, target="y", seed=5)
    second = score_classification(run_paths, p(1.3), truth_ref, target="y", seed=5)
    third = score_classification(run_paths, p(0.05), truth_ref, target="y", seed=5)
    res = compare_models(
        run_paths,
        {
            "top": top.artifacts[0].ref,
            "second": second.artifacts[0].ref,
            "third": third.artifacts[0].ref,
        },
        metric="roc_auc",
    )
    ranking = _ranking(res)
    # the two strong candidates (signal ~1.3-1.4) fill the top two ranks in some order; the weak
    # one (signal 0.05) is last. We do not assert which strong one leads -- their CIs overlap.
    assert {ranking[0]["candidate"], ranking[1]["candidate"]} == {"top", "second"}
    assert ranking[2]["candidate"] == "third"
    # correct: the top two overlap -> not distinguishable.
    assert res.summary["top_two_distinguishable"] is False
    # the mutation would compare the leader against the third-ranked (well-separated) -> True.
    r = _ranking(res)

    def ci(row: dict[str, JsonValue]) -> dict[str, float | None]:
        return {"ci_low": _f(row["ci_low"]), "ci_high": _f(row["ci_high"])}

    assert ev._ci_overlap(ci(r[0]), ci(r[1])) is True
    assert ev._ci_overlap(ci(r[0]), ci(r[2])) is False


def test_compare_mismatched_fingerprints_names_candidates(run_paths: RunPaths) -> None:
    a, b = _two_score_tables(run_paths, same_truth=False)
    with pytest.raises(ToolParamError, match="fingerprint"):
        compare_models(run_paths, {"a": a, "b": b}, metric="roc_auc")


def test_mutation_remove_fingerprint_check_allows_incomparable(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD mutation: removing the fingerprint check lets candidates scored on different rows be
    compared. Here the mismatched-fingerprint case must raise; without the check it would not."""
    a, b = _two_score_tables(run_paths, same_truth=False)
    with pytest.raises(ToolParamError, match="fingerprint"):
        compare_models(run_paths, {"a": a, "b": b}, metric="roc_auc")


def test_compare_mixed_tasks_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, y, _proba, _classes = _binary_refs(run_paths)
    clf = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=5)
    rtruth = _save(run_paths, pd.DataFrame({"z": y.astype(float)}))
    rpred = _save(run_paths, pd.DataFrame({"prediction": y.astype(float)}), "predictions")
    reg = score_regression(run_paths, rpred, rtruth, target="z", seed=5)
    with pytest.raises(ToolParamError, match="mix tasks"):
        compare_models(
            run_paths, {"clf": clf.artifacts[0].ref, "reg": reg.artifacts[0].ref}, metric="roc_auc"
        )


def test_compare_metric_not_in_task_is_tool_param(run_paths: RunPaths) -> None:
    a, b = _two_score_tables(run_paths)
    with pytest.raises(ToolParamError, match="is not a classification metric"):
        compare_models(run_paths, {"a": a, "b": b}, metric="rmse")


# --- strict JSON ---------------------------------------------------------------------------------


def test_every_summary_and_artifact_is_strict_json(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    sc = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)
    cm = compute_confusion_matrix(run_paths, proba_ref, truth_ref, target="y")
    roc = compute_roc_curve(run_paths, proba_ref, truth_ref, target="y")
    for res in (sc, cm, roc):
        json.dumps(res.summary, allow_nan=False)
        for art in res.artifacts:
            kind: ArtifactKind = "table" if art.kind == "table" else "curve"
            json.dumps(load_json(run_paths.artifact(art.ref, kind)), allow_nan=False)


# --- extra label (D-b) ---------------------------------------------------------------------------


def test_extra_prediction_label_is_tool_param(run_paths: RunPaths) -> None:
    y, proba, classes = _binary_fit(n=60)
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}).iloc[:-1])  # one fewer truth row
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)), "predictions")
    with pytest.raises(ToolParamError, match="do not cover"):
        score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)


# --- end to end (B1 integration) -----------------------------------------------------------------

_CLASSIFIERS = ("logistic_regression", "random_forest_classifier", "hist_gb_classifier")


def test_end_to_end_rank_models_on_churn(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """B1 pipeline -> predict_proba -> score_classification for the three classifiers ->
    compare_models -> rank_models, on `churn_frame`. The whole evaluation path runs end to end and
    produces a ranking with a selection note naming N=3."""
    from statlab_tools.artifacts import load_dataframe
    from statlab_tools.tools.cleaning import apply_imputer, fit_imputer
    from statlab_tools.tools.encoding import (
        apply_onehot_encoder,
        apply_scaler,
        fit_onehot_encoder,
        fit_scaler,
    )
    from statlab_tools.tools.splitting import split_stratified
    from statlab_tools.tools.training import predict_proba, train_estimator

    frame = churn_frame_fixture.drop(columns=["customer_id", "notes_free_text", "signup_date"])
    raw = _save(run_paths, frame)
    split = split_stratified(run_paths, raw, target="churned", seed=7)
    refs = {m.split: m.ref for m in split.artifacts}
    train_ref, valid_ref = refs["train"], refs["valid"]

    imp = fit_imputer(run_paths, train_ref, target="churned")
    train_i = apply_imputer(run_paths, imp.artifacts[0].ref, train_ref).artifacts[0].ref
    valid_i = apply_imputer(run_paths, imp.artifacts[0].ref, valid_ref).artifacts[0].ref
    oh = fit_onehot_encoder(run_paths, train_i, target="churned")
    train_o = apply_onehot_encoder(run_paths, oh.artifacts[0].ref, train_i).artifacts[0].ref
    valid_o = apply_onehot_encoder(run_paths, oh.artifacts[0].ref, valid_i).artifacts[0].ref
    sc = fit_scaler(run_paths, train_o, target="churned", method="standard")
    train_s = apply_scaler(run_paths, sc.artifacts[0].ref, train_o).artifacts[0].ref
    valid_s = apply_scaler(run_paths, sc.artifacts[0].ref, valid_o).artifacts[0].ref

    # the valid frame is the truth; score each classifier's probabilities against it.
    _ = load_dataframe(run_paths.artifact(valid_s, "dataframe"))
    tables: dict[str, ArtifactRef] = {}
    estimators: dict[str, str] = {}
    for name in _CLASSIFIERS:
        model = train_estimator(
            run_paths, train_s, target="churned", name=name, seed=11, params=_fast(name)
        )
        pp = predict_proba(run_paths, model.artifacts[0].ref, valid_s)
        scored = score_classification(
            run_paths, pp.artifacts[0].ref, valid_s, target="churned", seed=13
        )
        tables[name] = scored.artifacts[0].ref
        estimators[name] = name

    cmp = compare_models(run_paths, tables, metric="roc_auc")
    assert len(_ranking(cmp)) == 3

    from statlab_tools.tools.champion import rank_models

    rk = rank_models(run_paths, cmp.artifacts[0].ref, estimators=estimators)
    assert rk.summary["n_candidates"] == 3
    assert "over 3 candidates" in _s(rk.summary["selection_note"])
    assert rk.summary["top_eligible"] in _CLASSIFIERS
    # emit the verbatim rank_models summary for the report.
    print("RANK_MODELS_SUMMARY=" + json.dumps(rk.summary))


def test_float_coded_binary_target_scores_and_honours_contract(run_paths: RunPaths) -> None:
    """Review correction a: a 0.0/1.0 float-coded binary target (an int-coded column with a null
    loads as float64, the common case). predict_proba must name columns proba__0/proba__1 (not
    proba__0.0/proba__1.0), score_classification must succeed, and flipping the contract
    positive_class between 1 and 0 must change the honoured positive class and the recall."""
    from statlab_tools.artifacts import load_dataframe
    from statlab_tools.tools.training import predict_proba, train_estimator

    rng = np.random.default_rng(0)
    f1 = rng.normal(size=200)
    f2 = rng.normal(size=200)
    y = ((f1 + f2 + rng.normal(scale=0.2, size=200)) > 0).astype(float)  # {0.0, 1.0} float64
    frame = pd.DataFrame({"f1": f1, "f2": f2, "y": y})
    ref = _save(run_paths, frame)

    model = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    pp = predict_proba(run_paths, model.artifacts[0].ref, ref)
    proba = load_dataframe(run_paths.artifact(pp.artifacts[0].ref, "predictions"))
    assert list(proba.columns) == ["proba__0", "proba__1"]

    _write_snapshot(run_paths, _binary_contract(1))
    pos1 = score_classification(run_paths, pp.artifacts[0].ref, ref, target="y", seed=13)
    assert pos1.summary["positive_class"] == "1"
    assert pos1.summary["positive_class_source"] == "contract"

    _write_snapshot(run_paths, _binary_contract(0))
    pos0 = score_classification(run_paths, pp.artifacts[0].ref, ref, target="y", seed=13)
    assert pos0.summary["positive_class"] == "0"
    assert _mv(pos0, "recall") != pytest.approx(_mv(pos1, "recall"))


def _fast(name: str) -> dict[str, int]:
    return {"n_estimators": 10} if "random_forest" in name else {}


# --- one MCP round-trip --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_score_classification_round_trips(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """score_classification over a real MCP session: the served meta carries the evaluation phase
    and allow_in, and the result round-trips as a valid envelope with a score table artifact."""
    from mcp.types import CallToolResult
    from statlab_contracts.envelope import TOOL_RESULT_ADAPTER
    from statlab_tools.settings import get_tool_settings

    settings = get_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    y, proba, classes = _binary_fit(n=80)
    truth_ref = _save(paths, pd.DataFrame({"y": y}))
    proba_ref = _save(paths, _proba_frame(proba, list(classes)), "predictions")

    async with mcp_session() as session:
        listed = await session.list_tools()
        meta = {t.name: t.meta for t in listed.tools if t.meta is not None}
        assert meta["score_classification"]["phase"] == "evaluation"
        assert meta["score_classification"]["allow_in"] == [
            "model_evaluation",
            "champion_selection",
        ]
        result = await session.call_tool(
            "score_classification",
            {
                "project": "proj",
                "run": run_paths_over_mcp,
                "probabilities": proba_ref,
                "truth": truth_ref,
                "target": "y",
                "seed": 1,
            },
        )
        assert isinstance(result, CallToolResult)
        assert result.structured_content is not None
        tv = TOOL_RESULT_ADAPTER.validate_python(result.structured_content["result"])
        assert tv.ok is True
        assert tv.artifacts[0].kind == "table"


# --- coverage: edge paths ------------------------------------------------------------------------


def test_contract_positive_class_unmatched_is_tool_data(run_paths: RunPaths) -> None:
    """D-c (review correction c): a contract positive_class that is not one of the predicted
    classes is a data error naming the value and the observed classes -- never a silent fallback
    to the last column, which would score the wrong class."""
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    _write_snapshot(run_paths, _binary_contract("not_a_class"))
    with pytest.raises(ToolDataError, match="not_a_class"):
        score_classification(run_paths, proba_ref, truth_ref, target="y", seed=1)


def test_confusion_matrix_threshold_on_multiclass_is_tool_param(run_paths: RunPaths) -> None:
    y, proba, classes = _multiclass_fit()
    truth_ref = _save(run_paths, pd.DataFrame({"y": y}))
    proba_ref = _save(run_paths, _proba_frame(proba, list(classes)), "predictions")
    with pytest.raises(ToolParamError, match="binary only"):
        compute_confusion_matrix(run_paths, proba_ref, truth_ref, target="y", threshold=0.5)


def test_select_threshold_non_dict_curve_is_tool_param(run_paths: RunPaths) -> None:
    from statlab_tools.artifacts import save_json

    ref = new_artifact_ref()
    payload: JsonValue = [1, 2, 3]
    save_json(run_paths.artifact(ref, "curve"), payload, ref=ref, kind="curve", label="bad")
    with pytest.raises(ToolParamError, match="not a curve"):
        select_threshold(run_paths, ref, objective="f1")


def test_select_threshold_target_recall_unreachable_is_tool_param(run_paths: RunPaths) -> None:
    """A curve whose maximum recall is below the floor makes target_recall unreachable."""
    from statlab_tools.artifacts import save_json

    ref = new_artifact_ref()
    low: JsonValue = {
        "kind": "roc",
        "x": [0.0, 0.5],
        "y": [0.0, 0.6],  # max recall 0.6
        "thresholds": [None, 0.5],
        "auc": 0.5,
        "n_pos": 10,
        "n_neg": 10,
        "positive_class": "1",
    }
    save_json(run_paths.artifact(ref, "curve"), low, ref=ref, kind="curve", label="roc")
    with pytest.raises(ToolParamError, match="no threshold reaches recall"):
        select_threshold(run_paths, ref, objective="target_recall", target_recall=0.9)


def test_plot_curve_non_dict_is_tool_param(run_paths: RunPaths) -> None:
    from statlab_tools.artifacts import save_json

    ref = new_artifact_ref()
    payload: JsonValue = [1, 2]
    save_json(run_paths.artifact(ref, "curve"), payload, ref=ref, kind="curve", label="bad")
    with pytest.raises(ToolParamError, match="not a curve"):
        plot_curve(run_paths, ref, title="t")


def test_cv_unknown_estimator_is_tool_param(run_paths: RunPaths) -> None:
    with pytest.raises(ToolParamError, match="unknown estimator"):
        cross_validate_estimator(
            run_paths, _train_frame(run_paths), target="y", name="nope", metric="roc_auc", seed=1
        )


def test_cv_metric_task_mismatch_is_tool_param(run_paths: RunPaths) -> None:
    with pytest.raises(ToolParamError, match="not a classification metric"):
        cross_validate_estimator(
            run_paths,
            _train_frame(run_paths),
            target="y",
            name="logistic_regression",
            metric="rmse",
            seed=1,
        )


def test_cv_target_not_in_train_is_tool_param(run_paths: RunPaths) -> None:
    with pytest.raises(ToolParamError, match="not in train"):
        cross_validate_estimator(
            run_paths,
            _train_frame(run_paths),
            target="missing",
            name="logistic_regression",
            metric="roc_auc",
            seed=1,
        )


def test_cv_multiclass_brier_is_tool_param(run_paths: RunPaths) -> None:
    y, _proba, _classes = _multiclass_fit()
    rng = np.random.default_rng(0)
    train = _save(
        run_paths,
        pd.DataFrame({"f1": rng.normal(size=len(y)), "f2": rng.normal(size=len(y)), "y": y}),
    )
    with pytest.raises(ToolParamError, match="no multiclass scorer"):
        cross_validate_estimator(
            run_paths, train, target="y", name="hist_gb_classifier", metric="brier", seed=1
        )


def test_cv_with_params_sets_them(run_paths: RunPaths) -> None:
    """Exercises `_set_params` with a non-empty params dict (the branch skipped when params is
    empty). A valid `C` override on logistic_regression runs without error."""
    res = cross_validate_estimator(
        run_paths,
        _train_frame(run_paths),
        target="y",
        name="logistic_regression",
        metric="roc_auc",
        seed=1,
        params={"C": 0.5},
    )
    assert len(_l(res.summary["fold_scores"])) == 5


def test_compare_non_table_candidate_is_tool_param(run_paths: RunPaths) -> None:
    truth_ref, proba_ref, *_ = _binary_refs(run_paths)
    good = score_classification(run_paths, proba_ref, truth_ref, target="y", seed=5)
    from statlab_tools.artifacts import save_json

    bad = new_artifact_ref()
    payload: JsonValue = {"not": "a score table"}
    save_json(run_paths.artifact(bad, "table"), payload, ref=bad, kind="table", label="bad")
    with pytest.raises(ToolParamError, match="not a score table"):
        compare_models(run_paths, {"a": good.artifacts[0].ref, "b": bad}, metric="roc_auc")


def test_ci_overlap_treats_missing_bound_as_overlapping() -> None:
    """`_ci_overlap` returns True (not distinguishable) when a bound is None -- the conservative
    reading. Covers the None branch directly."""
    assert ev._ci_overlap({"ci_low": None, "ci_high": None}, {"ci_low": 0.1, "ci_high": 0.2})
    assert ev._ci_overlap({"ci_low": 0.1, "ci_high": 0.2}, {"ci_low": 0.15, "ci_high": 0.25})
    assert not ev._ci_overlap({"ci_low": 0.1, "ci_high": 0.2}, {"ci_low": 0.3, "ci_high": 0.4})
