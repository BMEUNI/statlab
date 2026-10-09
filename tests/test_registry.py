"""Chunk 13 §4 (Registry): the six-estimator `ESTIMATORS` registry (D25) and `SCORERS`.

These are structural assertions, not fits: the exact six names and tasks, that every
`default_grid` key is a real `get_params()` key of its estimator, that `build(seed, ...)` wires
the seed into every estimator that has `random_state`, and that every forest is `n_jobs=1` (D17).
"""

from __future__ import annotations

import os

import pytest
from statlab_contracts.vocabulary import TUNABLE_PARAMS, tunable_param_errors
from statlab_tools.registry import (
    estimators,
    fit_threads,
    greater_is_better,
    is_binary_only,
    parallel_jobs,
    scorer_for,
)


def _params(estimator: object) -> dict[str, object]:
    from typing import cast

    from sklearn.base import BaseEstimator

    return dict(cast(BaseEstimator, estimator).get_params())


def test_registry_names_tasks_and_families() -> None:
    reg = estimators()
    assert set(reg) == {
        "logistic_regression",
        "random_forest_classifier",
        "hist_gb_classifier",
        "gaussian_nb",
        "lda",
        "extra_trees_classifier",
        "decision_tree_classifier",
        "linear_regression",
        "ridge",
        "random_forest_regressor",
        "hist_gb_regressor",
        "lasso",
        "elastic_net",
        "extra_trees_regressor",
        "decision_tree_regressor",
    }
    tasks = {name: entry.task for name, entry in reg.items()}
    assert tasks == {
        "logistic_regression": "classification",
        "random_forest_classifier": "classification",
        "hist_gb_classifier": "classification",
        "gaussian_nb": "classification",
        "lda": "classification",
        "extra_trees_classifier": "classification",
        "decision_tree_classifier": "classification",
        "linear_regression": "regression",
        "ridge": "regression",
        "random_forest_regressor": "regression",
        "hist_gb_regressor": "regression",
        "lasso": "regression",
        "elastic_net": "regression",
        "extra_trees_regressor": "regression",
        "decision_tree_regressor": "regression",
    }
    assert {name: entry.family for name, entry in reg.items()} == {
        "logistic_regression": "linear",
        "linear_regression": "linear",
        "ridge": "linear",
        "lasso": "linear",
        "elastic_net": "linear",
        "random_forest_classifier": "random_forest",
        "random_forest_regressor": "random_forest",
        "hist_gb_classifier": "hist_gradient_boosting",
        "hist_gb_regressor": "hist_gradient_boosting",
        "gaussian_nb": "naive_bayes",
        "lda": "discriminant",
        "extra_trees_classifier": "extra_trees",
        "extra_trees_regressor": "extra_trees",
        "decision_tree_classifier": "decision_tree",
        "decision_tree_regressor": "decision_tree",
    }


def test_every_default_grid_key_is_a_real_param() -> None:
    for name, entry in estimators().items():
        valid = set(_params(entry.build(0, "none")))
        for key in entry.default_grid:
            assert key in valid, f"{name}: {key!r} not a real param"


def test_tunable_params_match_registry() -> None:
    assert set(TUNABLE_PARAMS) == set(estimators())
    for name, entry in estimators().items():
        keys = set(_params(entry.build(0, "none")))
        assert TUNABLE_PARAMS[name] <= keys
        assert set(entry.default_grid) <= TUNABLE_PARAMS[name]
        for key, values in entry.default_grid.items():
            for value in values:
                assert tunable_param_errors({key: value}, TUNABLE_PARAMS[name]) == []


def test_build_passes_seed_to_estimators_with_random_state() -> None:
    for name, entry in estimators().items():
        params = _params(entry.build(7, "none"))
        if "random_state" in params:
            assert params["random_state"] == 7, f"{name} ignored the seed"


def test_linear_regression_has_no_random_state() -> None:
    # P5: LinearRegression has no random_state; build accepts and ignores the seed.
    params = _params(estimators()["linear_regression"].build(7, "none"))
    assert "random_state" not in params


def test_every_forest_is_single_threaded() -> None:
    # D17: n_jobs=1 so nothing oversubscribes the fit semaphore.
    for name in (
        "random_forest_classifier",
        "random_forest_regressor",
        "extra_trees_classifier",
        "extra_trees_regressor",
    ):
        params = _params(estimators()[name].build(0, "none"))
        assert params["n_jobs"] == 1


def test_class_weight_wired_into_supporting_classifiers() -> None:
    params = _params(estimators()["logistic_regression"].build(0, "balanced"))
    assert params["class_weight"] == "balanced"
    none_params = _params(estimators()["logistic_regression"].build(0, "none"))
    assert none_params["class_weight"] is None


def test_logistic_regression_converges_with_max_iter_1000() -> None:
    # D-e/P4: max_iter=1000 so no ConvergenceWarning under -W error.
    assert _params(estimators()["logistic_regression"].build(0, "none"))["max_iter"] == 1000


def test_handles_missing_and_class_weight_flags() -> None:
    reg = estimators()
    assert reg["logistic_regression"].handles_missing is False
    assert reg["random_forest_classifier"].handles_missing is True
    assert reg["hist_gb_classifier"].handles_missing is True
    assert reg["logistic_regression"].supports_class_weight is True
    assert reg["linear_regression"].supports_class_weight is False
    assert reg["random_forest_regressor"].supports_class_weight is False
    for name in (
        "gaussian_nb",
        "lda",
        "extra_trees_classifier",
        "decision_tree_classifier",
        "lasso",
        "elastic_net",
        "extra_trees_regressor",
        "decision_tree_regressor",
    ):
        assert reg[name].handles_missing is False, name
    assert reg["extra_trees_classifier"].supports_class_weight is True
    assert reg["decision_tree_classifier"].supports_class_weight is True
    assert reg["gaussian_nb"].supports_class_weight is False
    assert reg["lda"].supports_class_weight is False


def test_new_linear_models_are_interpretable_and_lda_uses_lsqr() -> None:
    reg = estimators()
    assert reg["lasso"].interpretable is True
    assert reg["elastic_net"].interpretable is True
    for name in (
        "gaussian_nb",
        "lda",
        "extra_trees_classifier",
        "decision_tree_classifier",
        "extra_trees_regressor",
        "decision_tree_regressor",
    ):
        assert reg[name].interpretable is False, name
    assert _params(reg["lda"].build(0, "none"))["solver"] == "lsqr"


def test_scorer_roc_auc_binary_vs_multiclass() -> None:
    assert scorer_for("roc_auc", multiclass=False) == "roc_auc"
    assert scorer_for("roc_auc", multiclass=True) == "roc_auc_ovr"


def test_scorer_regression_names() -> None:
    assert scorer_for("rmse", multiclass=False) == "neg_root_mean_squared_error"
    assert scorer_for("r2", multiclass=False) == "r2"
    assert scorer_for("mape", multiclass=False) == "neg_mean_absolute_percentage_error"


def test_scorer_precision_uses_make_scorer_object() -> None:
    # precision/recall/f1 are make_scorer objects (zero_division=0), not plain strings (P4).
    assert not isinstance(scorer_for("precision", multiclass=False), str)
    assert not isinstance(scorer_for("f1", multiclass=True), str)


def test_greater_is_better_direction() -> None:
    assert greater_is_better("roc_auc") is True
    assert greater_is_better("rmse") is False
    assert greater_is_better("log_loss") is False
    assert greater_is_better("r2") is True


def test_brier_is_binary_only() -> None:
    assert is_binary_only("brier") is True
    assert is_binary_only("roc_auc") is False


@pytest.mark.parametrize(("cores", "expected"), [(16, 5), (2, 1), (None, 1)])
def test_fit_threads_is_a_third_of_the_cores(
    monkeypatch: pytest.MonkeyPatch, cores: int | None, expected: int
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: cores)
    assert fit_threads() == expected


def test_parallel_jobs_keeps_hist_gb_single_and_threads_the_rest() -> None:
    assert parallel_jobs("hist_gb_classifier") == 1
    assert parallel_jobs("hist_gb_regressor") == 1
    assert parallel_jobs("random_forest_classifier") == fit_threads()
