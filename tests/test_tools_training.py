"""Chunk 13 §4: the six training tools.

Frames are small and hand-built (150-300 rows); slow fits override `n_estimators` through
`params`. The heart of the suite is the three tests the spec names as most likely to pass by
construction: the budget stop monkeypatches `training.time.monotonic` to advance (not a real slow
grid) and asserts one point is evaluated while an un-advanced clock evaluates all; the shuffle
test uses a TARGET-SORTED train so an unshuffled splitter would change results; the calibration
test asserts probabilities DIFFER from the uncalibrated fit.
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
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools import training
from statlab_tools.tools.cleaning import apply_imputer, fit_imputer
from statlab_tools.tools.encoding import (
    apply_onehot_encoder,
    apply_scaler,
    fit_onehot_encoder,
    fit_scaler,
)
from statlab_tools.tools.splitting import split_stratified
from statlab_tools.tools.training import (
    get_model_params,
    list_estimators,
    predict,
    predict_proba,
    train_estimator,
    tune_estimator,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded(paths: RunPaths, res: ToolSuccess, index: int = 0) -> pd.DataFrame:
    return load_dataframe(paths.artifact(res.artifacts[index].ref, "predictions"))


def _separable_binary(n: int = 200, seed: int = 0) -> pd.DataFrame:
    """A linearly separable-ish binary frame: two informative features, label from their sum."""
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = ((f1 + f2 + rng.normal(scale=0.2, size=n)) > 0).astype(int)
    return pd.DataFrame({"f1": f1, "f2": f2, "y": y})


def _regression_frame(n: int = 200, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    y = 3.0 * f1 - 2.0 * f2 + rng.normal(scale=0.1, size=n)
    return pd.DataFrame({"f1": f1, "f2": f2, "y": y})


def _multiclass_frame(n: int = 240, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    y = pd.cut(f1, bins=3, labels=["low", "mid", "high"]).astype(str)
    return pd.DataFrame({"f1": f1, "f2": rng.normal(size=n), "y": y})


_FAST_RF = {"n_estimators": 10}
"""Override to keep forest fits quick in tests that do not need 200 trees."""


# --- list_estimators -----------------------------------------------------------------------------


def test_list_estimators_all_and_filtered() -> None:
    allo = _dict(list_estimators().summary["estimators"])
    assert len(allo) == 6
    clf = _dict(list_estimators(task="classification").summary["estimators"])
    assert set(clf) == {"logistic_regression", "random_forest_classifier", "hist_gb_classifier"}
    entry = _dict(clf["logistic_regression"])
    assert entry["supports_proba"] is True
    reg = _dict(list_estimators(task="regression").summary["estimators"])
    assert _dict(reg["linear_regression"])["supports_proba"] is False


# --- train & predict -----------------------------------------------------------------------------


def test_separable_binary_trains_and_predicts_above_0_9(run_paths: RunPaths) -> None:
    frame = _separable_binary()
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    out = _loaded(run_paths, predict(run_paths, res.artifacts[0].ref, ref))
    acc = float((out["prediction"].to_numpy() == frame["y"].to_numpy()).mean())
    assert acc > 0.9
    assert res.summary["calibrated"] is False
    assert res.summary["features"] == 2


def test_regression_gives_r2_above_0_9(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="linear_regression", seed=1)
    out = _loaded(run_paths, predict(run_paths, res.artifacts[0].ref, ref))
    pred = out["prediction"].to_numpy()
    truth = frame["y"].to_numpy()
    ss_res = float(((truth - pred) ** 2).sum())
    ss_tot = float(((truth - truth.mean()) ** 2).sum())
    assert 1 - ss_res / ss_tot > 0.9


def test_multiclass_and_string_class_targets_work(run_paths: RunPaths) -> None:
    frame = _multiclass_frame()
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="hist_gb_classifier", seed=1)
    classes = _list(res.summary["classes"])
    assert set(classes) == {"low", "mid", "high"}
    proba = predict_proba(run_paths, res.artifacts[0].ref, ref)
    cols = _list(proba.summary["columns"])
    assert cols == [f"proba__{c}" for c in classes]


# --- errors --------------------------------------------------------------------------------------


def test_unknown_name_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="unknown estimator"):
        train_estimator(run_paths, ref, target="y", name="nope", seed=1)


@pytest.mark.parametrize("bad", ["n_jobs", "random_state"])
def test_reserved_params_are_tool_param(run_paths: RunPaths, bad: str) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="fixed by the tool"):
        train_estimator(
            run_paths, ref, target="y", name="random_forest_classifier", seed=1, params={bad: 1}
        )


def test_unknown_param_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="not accepted"):
        train_estimator(
            run_paths, ref, target="y", name="logistic_regression", seed=1, params={"nope": 1}
        )


def test_class_weight_balanced_on_regressor_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _regression_frame())
    with pytest.raises(ToolParamError, match="class_weight"):
        train_estimator(
            run_paths, ref, target="y", name="linear_regression", seed=1, class_weight="balanced"
        )


def test_string_feature_column_is_tool_param_naming_it(run_paths: RunPaths) -> None:
    frame = _separable_binary()
    frame["note"] = "x"
    ref = _save(run_paths, frame)
    with pytest.raises(ToolParamError, match="note"):
        train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)


def test_nan_with_logistic_is_tool_param_but_histgb_trains(run_paths: RunPaths) -> None:
    frame = _separable_binary()
    frame.loc[0, "f1"] = np.nan
    ref = _save(run_paths, frame)
    with pytest.raises(ToolParamError, match="impute first"):
        train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    res = train_estimator(run_paths, ref, target="y", name="hist_gb_classifier", seed=1)
    assert res.summary["name"] == "hist_gb_classifier"


def test_target_not_in_train_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="not in train"):
        train_estimator(run_paths, ref, target="missing", name="logistic_regression", seed=1)


def test_classifier_on_continuous_target_is_data_error(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _regression_frame())
    with pytest.raises(ToolDataError, match="not class-shaped"):
        train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)


def test_regressor_on_string_target_is_data_error(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _multiclass_frame())
    with pytest.raises(ToolDataError, match="not numeric"):
        train_estimator(run_paths, ref, target="y", name="linear_regression", seed=1)


# --- calibration ---------------------------------------------------------------------------------


def test_calibration_produces_different_probabilities(run_paths: RunPaths) -> None:
    frame = _separable_binary()
    ref = _save(run_paths, frame)
    plain = train_estimator(
        run_paths, ref, target="y", name="random_forest_classifier", seed=1, params=_FAST_RF
    )
    calib = train_estimator(
        run_paths,
        ref,
        target="y",
        name="random_forest_classifier",
        seed=1,
        params=_FAST_RF,
        calibrate=True,
    )
    assert calib.summary["calibrated"] is True
    assert calib.summary["cv_folds"] == 5
    p0 = _loaded(run_paths, predict_proba(run_paths, plain.artifacts[0].ref, ref))
    p1 = _loaded(run_paths, predict_proba(run_paths, calib.artifacts[0].ref, ref))
    # not merely a CalibratedClassifierCV wrapper -- the numbers must actually differ.
    assert not np.allclose(p0["proba__1"].to_numpy(), p1["proba__1"].to_numpy())
    from sklearn.calibration import CalibratedClassifierCV

    loaded = _load_model(run_paths, calib.artifacts[0].ref)
    assert isinstance(loaded.estimator, CalibratedClassifierCV)


def test_calibration_on_regressor_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _regression_frame())
    with pytest.raises(ToolParamError, match="requires a classifier"):
        train_estimator(
            run_paths, ref, target="y", name="linear_regression", seed=1, calibrate=True
        )


def test_calibration_class_below_cv_folds_is_data_error(run_paths: RunPaths) -> None:
    # one class with 3 rows < cv_folds=5 (P1).
    frame = pd.DataFrame({"f1": list(range(23)), "f2": list(range(23)), "y": [0] * 20 + [1] * 3})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolDataError, match="cannot cross-validate or calibrate"):
        train_estimator(
            run_paths, ref, target="y", name="logistic_regression", seed=1, calibrate=True
        )


# --- seeds ---------------------------------------------------------------------------------------


def test_same_seed_identical_different_seed_differs(run_paths: RunPaths) -> None:
    # noisy, non-separable data so the forest's bootstrap/feature randomness actually moves
    # predictions between seeds (a separable frame predicts identically regardless of seed).
    rng = np.random.default_rng(0)
    n = 200
    frame = pd.DataFrame(
        {"f1": rng.normal(size=n), "f2": rng.normal(size=n), "y": rng.integers(0, 2, size=n)}
    )
    ref = _save(run_paths, frame)

    def preds(seed: int) -> np.ndarray:
        res = train_estimator(
            run_paths, ref, target="y", name="random_forest_classifier", seed=seed, params=_FAST_RF
        )
        return _loaded(run_paths, predict(run_paths, res.artifacts[0].ref, ref))[
            "prediction"
        ].to_numpy()

    assert np.array_equal(preds(1), preds(1))
    assert not np.array_equal(preds(1), preds(2))


def test_cv_splitter_shuffles_on_target_sorted_train(run_paths: RunPaths) -> None:
    # The CV splitter is the ONLY seed-dependent part here: logistic_regression's fit is
    # deterministic given the folds. On a TARGET-SORTED train, an unshuffled splitter makes
    # identical folds for every seed, so two seeds give identical results and the assertion
    # a != b fails -- which is exactly the shuffle=False mutation. Shuffling with the seed makes
    # the folds (and thus the CV means) differ between seeds.
    frame = _separable_binary(n=240).sort_values("y").reset_index(drop=True)
    ref = _save(run_paths, frame)
    grid = {"C": (0.1, 1.0, 10.0)}
    a = tune_estimator(
        run_paths,
        ref,
        target="y",
        name="logistic_regression",
        metric="roc_auc",
        seed=1,
        param_grid=grid,
    )
    b = tune_estimator(
        run_paths,
        ref,
        target="y",
        name="logistic_regression",
        metric="roc_auc",
        seed=99,
        param_grid=grid,
    )
    assert a.summary["results"] != b.summary["results"]


# --- tune ----------------------------------------------------------------------------------------


def test_tune_finds_best_c(run_paths: RunPaths) -> None:
    frame = _separable_binary()
    ref = _save(run_paths, frame)
    res = tune_estimator(
        run_paths, ref, target="y", name="logistic_regression", metric="roc_auc", seed=1
    )
    best = _dict(res.summary["best_params"])
    assert "C" in best
    assert res.summary["points_total"] == 3
    assert res.summary["greater_is_better"] is True


def test_tune_scores_sign_corrected_rmse_positive(run_paths: RunPaths) -> None:
    frame = _regression_frame()
    ref = _save(run_paths, frame)
    res = tune_estimator(
        run_paths,
        ref,
        target="y",
        name="random_forest_regressor",
        metric="rmse",
        seed=1,
        param_grid={"max_depth": (2, 3)},
    )
    assert res.summary["greater_is_better"] is False
    best = cast(float, res.summary["best_score"])
    assert best > 0  # RMSE sign-corrected to positive
    for row in _list(res.summary["results"]):
        assert cast(float, _dict(row)["mean"]) > 0


def test_tune_grid_over_50_points_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    big = {"C": tuple(float(i) for i in range(51))}
    with pytest.raises(ToolParamError, match="MAX_GRID_POINTS"):
        tune_estimator(
            run_paths,
            ref,
            target="y",
            name="logistic_regression",
            metric="roc_auc",
            seed=1,
            param_grid=big,
        )


def test_tune_empty_grid_is_tool_param(run_paths: RunPaths) -> None:
    # linear_regression's default_grid is {} -> nothing to tune.
    ref = _save(run_paths, _regression_frame())
    with pytest.raises(ToolParamError, match="nothing to tune"):
        tune_estimator(run_paths, ref, target="y", name="linear_regression", metric="rmse", seed=1)


def test_tune_empty_value_list_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="empty"):
        tune_estimator(
            run_paths,
            ref,
            target="y",
            name="logistic_regression",
            metric="roc_auc",
            seed=1,
            param_grid={"C": ()},
        )


def test_tune_brier_on_multiclass_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _multiclass_frame())
    with pytest.raises(ToolParamError, match="no multiclass scorer"):
        tune_estimator(
            run_paths,
            ref,
            target="y",
            name="hist_gb_classifier",
            metric="brier",
            seed=1,
            param_grid={"max_leaf_nodes": (7, 15)},
        )


def test_tune_multiclass_f1_does_not_fail_under_w_error(run_paths: RunPaths) -> None:
    # P4: zero_division=0 scorer means no UndefinedMetricWarning -> no fatal error.
    ref = _save(run_paths, _multiclass_frame())
    res = tune_estimator(
        run_paths,
        ref,
        target="y",
        name="hist_gb_classifier",
        metric="f1",
        seed=1,
        param_grid={"max_leaf_nodes": (7, 15)},
    )
    assert res.summary["metric"] == "f1"


def test_tune_metric_task_mismatch_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="not a classification metric"):
        tune_estimator(
            run_paths, ref, target="y", name="logistic_regression", metric="rmse", seed=1
        )


def test_tune_target_not_in_train_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _separable_binary())
    with pytest.raises(ToolParamError, match="not in train"):
        tune_estimator(
            run_paths, ref, target="missing", name="logistic_regression", metric="roc_auc", seed=1
        )


# --- budget (D36c) -------------------------------------------------------------------------------


def test_budget_stops_after_one_point_when_clock_advances(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _separable_binary()
    ref = _save(run_paths, frame)
    state = {"t": 0.0}

    def advancing() -> float:
        state["t"] += 10_000.0
        return state["t"]

    monkeypatch.setattr("statlab_tools.tools.training.time.monotonic", advancing)
    res = tune_estimator(
        run_paths, ref, target="y", name="logistic_regression", metric="roc_auc", seed=1
    )
    assert res.summary["points_evaluated"] == 1
    assert res.summary["points_total"] == 3
    assert res.summary["stopped_early"] is True
    assert len(res.warnings) == 1
    # the returned best_params is exactly the one point that was evaluated.
    first = _dict(_list(res.summary["results"])[0])
    assert _dict(res.summary["best_params"]) == _dict(first["params"])


def test_budget_evaluates_every_point_when_clock_frozen(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = _separable_binary()
    ref = _save(run_paths, frame)
    monkeypatch.setattr("statlab_tools.tools.training.time.monotonic", lambda: 100.0)
    res = tune_estimator(
        run_paths, ref, target="y", name="logistic_regression", metric="roc_auc", seed=1
    )
    assert res.summary["points_evaluated"] == 3
    assert res.summary["stopped_early"] is False
    assert res.warnings == ()


# --- the contract governs ------------------------------------------------------------------------


def test_cv_folds_from_snapshot_is_used(
    seeded_run: RunPaths,
    write_snapshot: Callable[[DataContract], None],
    minimal_contract: DataContract,
) -> None:
    # cv_folds=3 in the snapshot. A train whose minority class has exactly 4 rows passes the
    # class-count pre-check at 3 but would fail the default 5 -- so a successful calibrated fit
    # here proves the snapshot's 3 (not the fallback 5) is the fold count actually used. The
    # reported cv_folds confirms it too.
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
    frame = pd.DataFrame({"f1": list(range(24)), "f2": list(range(24)), "y": [0] * 20 + [1] * 4})
    ref = _save(seeded_run, frame)
    res = train_estimator(
        seeded_run,
        ref,
        target="y",
        name="random_forest_classifier",
        seed=1,
        params=_FAST_RF,
        calibrate=True,
    )
    assert res.summary["cv_folds"] == 3


# --- predict -------------------------------------------------------------------------------------


def test_predict_preserves_non_range_index(run_paths: RunPaths) -> None:
    frame = _separable_binary(n=60)
    frame.index = pd.Index([f"row_{i}" for i in range(len(frame))])
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    out = _loaded(run_paths, predict(run_paths, res.artifacts[0].ref, ref))
    assert list(out.index) == list(frame.index)


def test_predict_ignores_extra_columns_including_target(run_paths: RunPaths) -> None:
    frame = _separable_binary(n=80)
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    extra = frame.copy()
    extra["junk"] = 1.0  # an extra column plus the target are both ignored
    out = _loaded(run_paths, predict(run_paths, res.artifacts[0].ref, _save(run_paths, extra)))
    assert list(out.columns) == ["prediction"]
    assert len(out) == len(frame)


def test_predict_missing_feature_is_tool_param(run_paths: RunPaths) -> None:
    frame = _separable_binary(n=60)
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    short = frame.drop(columns=["f2"])
    with pytest.raises(ToolParamError, match="missing fitted feature"):
        predict(run_paths, res.artifacts[0].ref, _save(run_paths, short))


def test_predict_proba_sums_to_one_columns_follow_classes(run_paths: RunPaths) -> None:
    frame = _multiclass_frame()
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="hist_gb_classifier", seed=1)
    classes = _list(res.summary["classes"])
    out = _loaded(run_paths, predict_proba(run_paths, res.artifacts[0].ref, ref))
    assert list(out.columns) == [f"proba__{c}" for c in classes]
    assert np.allclose(out.to_numpy().sum(axis=1), 1.0)


def test_predict_proba_on_regressor_is_tool_param(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _regression_frame())
    res = train_estimator(run_paths, ref, target="y", name="linear_regression", seed=1)
    with pytest.raises(ToolParamError, match="needs a classifier"):
        predict_proba(run_paths, res.artifacts[0].ref, ref)


def test_predict_nan_with_non_handling_estimator_is_tool_param(run_paths: RunPaths) -> None:
    frame = _separable_binary(n=60)
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    nan_frame = frame.copy()
    nan_frame.loc[0, "f1"] = np.nan
    with pytest.raises(ToolParamError, match="impute first"):
        predict(run_paths, res.artifacts[0].ref, _save(run_paths, nan_frame))


# --- get_model_params --------------------------------------------------------------------------


def test_get_model_params_reports_metadata(run_paths: RunPaths) -> None:
    frame = _separable_binary(n=60)
    ref = _save(run_paths, frame)
    res = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=3)
    gm = get_model_params(run_paths, res.artifacts[0].ref)
    assert gm.summary["name"] == "logistic_regression"
    assert gm.summary["seed"] == 3
    assert gm.summary["task"] == "classification"
    assert "params" in gm.summary


def test_wrong_type_ref_is_tool_param(run_paths: RunPaths) -> None:
    # a model-kind joblib artifact that is not a FittedModel -> load_fitted raises tool_param,
    # not an AttributeError deep in the body.
    from statlab_tools.artifacts import save_model

    ref = new_artifact_ref()
    save_model(run_paths.artifact(ref, "model"), {"not": "a model"}, ref=ref, kind="model")
    with pytest.raises(ToolParamError, match="FittedModel"):
        get_model_params(run_paths, ref)


# --- strict JSON ---------------------------------------------------------------------------------


def test_every_summary_is_strict_json(run_paths: RunPaths) -> None:
    frame = _separable_binary(n=80)
    ref = _save(run_paths, frame)
    tr = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    tu = tune_estimator(
        run_paths, ref, target="y", name="logistic_regression", metric="roc_auc", seed=1
    )
    for res in (list_estimators(), tr, tu, get_model_params(run_paths, tr.artifacts[0].ref)):
        json.dumps(res.summary, allow_nan=False)


# --- end to end (B1 integration) -----------------------------------------------------------------

_CLASSIFIERS = ("logistic_regression", "random_forest_classifier", "hist_gb_classifier")
_REGRESSORS = ("linear_regression", "random_forest_regressor", "hist_gb_regressor")


def _roc_auc(truth: np.ndarray, proba: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(truth, proba))


def test_b1_end_to_end_classification_on_churn(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    # chunks 10 -> 11 -> 12 -> 13: split -> drop id/free text -> impute -> one-hot -> scale ->
    # train all three classifiers -> predict_proba on valid.
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

    truth = load_dataframe(run_paths.artifact(valid_s, "dataframe"))["churned"].to_numpy()
    aucs: dict[str, float] = {}
    for name in _CLASSIFIERS:
        model = train_estimator(
            run_paths, train_s, target="churned", name=name, seed=11, params=_fast_params(name)
        )
        pp = predict_proba(run_paths, model.artifacts[0].ref, valid_s)
        proba = load_dataframe(run_paths.artifact(pp.artifacts[0].ref, "predictions"))
        assert list(proba.columns) == ["proba__0", "proba__1"]
        assert len(proba) == len(truth)
        aucs[name] = _roc_auc(truth, proba["proba__1"].to_numpy())
    # near-random data: the point is the pipeline runs and produces a finite AUC per model.
    assert all(0.0 <= a <= 1.0 for a in aucs.values())


def test_b1_end_to_end_regression_on_numeric_target(run_paths: RunPaths) -> None:
    frame = _regression_frame(n=240)
    raw = _save(run_paths, frame)
    from statlab_tools.tools.splitting import split_random

    split = split_random(run_paths, raw, target="y", seed=7)
    refs = {m.split: m.ref for m in split.artifacts}
    train_ref, valid_ref = refs["train"], refs["valid"]
    for name in _REGRESSORS:
        model = train_estimator(
            run_paths, train_ref, target="y", name=name, seed=11, params=_fast_params(name)
        )
        out = _loaded(run_paths, predict(run_paths, model.artifacts[0].ref, valid_ref))
        assert len(out) == len(load_dataframe(run_paths.artifact(valid_ref, "dataframe")))


def _fast_params(name: str) -> dict[str, int]:
    """`n_estimators=10` for the random forests (which accept it), nothing for the other
    estimators -- LinearRegression/LogisticRegression/HistGB have no `n_estimators`."""
    return {"n_estimators": 10} if "random_forest" in name else {}


# --- one MCP round-trip --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_train_then_predict_round_trips(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """train_estimator (heavy, through the semaphore) then predict over a real session; the served
    meta carries the right phase/allow_in and train's seed is ORCHESTRATOR_SUPPLIED."""
    from mcp.types import CallToolResult
    from statlab_tools.settings import get_tool_settings
    from statlab_tools.tool import ORCHESTRATOR_SUPPLIED

    settings = get_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    frame = _separable_binary(n=60)
    ref = _save(paths, frame)

    async with mcp_session() as session:
        listed = await session.list_tools()
        meta = {t.name: t.meta for t in listed.tools if t.meta is not None}
        assert meta["train_estimator"]["phase"] == "training"
        assert meta["train_estimator"]["allow_in"] == ["model_training"]
        assert meta["train_estimator"]["heavy"] is True
        schema = next(t for t in listed.tools if t.name == "train_estimator").input_schema
        assert schema["properties"]["seed"]["description"] == ORCHESTRATOR_SUPPLIED
        assert "seed" in schema["required"]

        train_result = await session.call_tool(
            "train_estimator",
            {
                "project": "proj",
                "run": run_paths_over_mcp,
                "train": ref,
                "target": "y",
                "name": "logistic_regression",
                "seed": 1,
            },
        )
        assert isinstance(train_result, CallToolResult)
        assert train_result.structured_content is not None
        tv = TOOL_RESULT_ADAPTER.validate_python(train_result.structured_content["result"])
        assert tv.ok is True
        model_ref = tv.artifacts[0].ref

        predict_result = await session.call_tool(
            "predict",
            {"project": "proj", "run": run_paths_over_mcp, "model": model_ref, "frame": ref},
        )
        assert isinstance(predict_result, CallToolResult)
        assert predict_result.structured_content is not None
        pv = TOOL_RESULT_ADAPTER.validate_python(predict_result.structured_content["result"])
        assert pv.ok is True
        out = load_dataframe(paths.artifact(pv.artifacts[0].ref, "predictions"))
        assert list(out.columns) == ["prediction"]
        assert len(out) == len(frame)


# --- helpers for snapshot/model loading ----------------------------------------------------------


def _load_model(paths: RunPaths, ref: ArtifactRef) -> training.FittedModel:
    from statlab_tools.artifacts import load_model

    obj = load_model(paths.artifact(ref, "model"))
    assert isinstance(obj, training.FittedModel)
    return obj


# --- review additions (B1) -----------------------------------------------------------------------


def test_predict_is_independent_of_frame_column_order(run_paths: RunPaths) -> None:
    """`predict` selects the fitted features in fitted order. A frame with the same columns in a
    different order must give identical predictions; a body that took `frame.columns` order would
    feed the model f2 where it learned f1."""
    frame = _separable_binary(n=120)
    ref = _save(run_paths, frame)
    model = train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)
    model_ref = model.artifacts[0].ref
    reordered = frame[list(reversed(frame.columns))]
    assert list(reordered.columns) != list(frame.columns)
    straight = _loaded(run_paths, predict_proba(run_paths, model_ref, ref))
    swapped = _loaded(run_paths, predict_proba(run_paths, model_ref, _save(run_paths, reordered)))
    pd.testing.assert_frame_equal(straight, swapped)


def test_tuning_cv_is_single_threaded(run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    """D17: nothing inside a tool body may spawn workers. `n_jobs=-1` in the tuning CV deadlocked
    the test run when this chunk was first implemented (loky workers started from a tool-body
    thread). The body imports `cross_val_score` at call time, so patching the module attribute
    intercepts every call."""
    import sklearn.model_selection as ms

    real = ms.cross_val_score
    seen: list[object] = []

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs.get("n_jobs"))
        return real(*args, **kwargs)

    monkeypatch.setattr(ms, "cross_val_score", spy)
    ref = _save(run_paths, _separable_binary())
    tune_estimator(run_paths, ref, target="y", name="logistic_regression", metric="roc_auc", seed=1)
    assert seen
    assert set(seen) == {1}


def test_tune_best_params_is_the_argmax_of_results(run_paths: RunPaths) -> None:
    """`best_params`/`best_score` must be the best row of `results` in the metric's natural
    direction -- for both a greater-is-better and a lower-is-better metric."""
    for frame, name, metric in (
        (_separable_binary(), "logistic_regression", "roc_auc"),
        (_regression_frame(), "random_forest_regressor", "rmse"),
    ):
        res = tune_estimator(
            run_paths, _save(run_paths, frame), target="y", name=name, metric=metric, seed=1
        )
        rows = [_dict(r) for r in _list(res.summary["results"])]
        means = [float(cast(float, r["mean"])) for r in rows]
        best = max(means) if res.summary["greater_is_better"] else min(means)
        assert res.summary["best_score"] == best
        assert res.summary["best_params"] == rows[means.index(best)]["params"]
