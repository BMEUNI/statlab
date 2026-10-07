"""Tests for statlab_contracts.vocabulary's chunk 04 additions: B1-B14 per
plan/04_candidate_spec.md §4.2.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError
from statlab_contracts.candidate import EstimatorSpec, FeatureSpec, PreprocessingSpec
from statlab_contracts.vocabulary import (
    DIMENSION_FOR_FIELD,
    METRICS_FOR_TASK,
    TASK_FOR_TARGET_TYPE,
    TUNABLE_PARAM_KINDS,
    TUNABLE_PARAMS,
    ClassificationMetric,
    DatetimePart,
    FeatureSelectionMethod,
    Metric,
    ParamValue,
    RegressionMetric,
    TargetType,
    Task,
    VariationDimension,
    tunable_grid_errors,
    tunable_param_errors,
)

# --- DIMENSION_FOR_FIELD ------------------------------------------------------------------


def test_dimension_for_field_values_are_valid_variation_dimension() -> None:
    """B1."""
    valid = set(get_args(VariationDimension))
    for path, dimension in DIMENSION_FOR_FIELD.items():
        assert dimension in valid, f"{path!r} maps to invalid dimension {dimension!r}"


def _hashed_leaf_paths() -> set[str]:
    """Derive every leaf path under `preprocessing`, `features`, `estimator` from
    `model_fields`, rather than hand-listing them -- B2/B3 must be exhaustiveness proofs, not
    a second copy of the same list."""
    paths: set[str] = set()
    for group_name, group_model in (
        ("preprocessing", PreprocessingSpec),
        ("features", FeatureSpec),
        ("estimator", EstimatorSpec),
    ):
        for field_name in group_model.model_fields:
            paths.add(f"{group_name}.{field_name}")
    return paths


def test_dimension_for_field_exhaustive_over_hashed_leaves() -> None:
    """B2: every leaf path under preprocessing, features, estimator appears as a key."""
    assert _hashed_leaf_paths() == set(DIMENSION_FOR_FIELD.keys())


def test_dimension_for_field_no_extra_keys() -> None:
    """B3: every key corresponds to a real field path (equality, not just containment)."""
    hashed_paths = _hashed_leaf_paths()
    for path in DIMENSION_FOR_FIELD:
        assert path in hashed_paths, f"{path!r} does not correspond to a real field path"


def test_every_variation_dimension_is_reachable() -> None:
    """B4: each dimension is the value of at least one key, so no dimension is unusable."""
    reachable = set(DIMENSION_FOR_FIELD.values())
    for dimension in get_args(VariationDimension):
        assert dimension in reachable, f"{dimension!r} is not the value of any key"


# --- ParamValue ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value",),
    [("a string",), (1,), (1.5,), (True,), (None,)],
)
def test_param_value_accepts_each_scalar_type(value: object) -> None:
    """B5."""
    spec = EstimatorSpec.model_validate({"name": "fixture", "params": {"x": value}})
    assert spec.params["x"] == value


def test_param_value_accepts_flat_tuple() -> None:
    """B6."""
    spec = EstimatorSpec.model_validate({"name": "fixture", "params": {"x": (1, 2, 3)}})
    assert spec.params["x"] == (1, 2, 3)


def test_param_value_rejects_nesting() -> None:
    """B7: pin the non-recursive limit."""
    with pytest.raises(ValidationError):
        EstimatorSpec.model_validate({"name": "fixture", "params": {"x": ((1, 2),)}})


# --- No duplicates in the two new Literals ----------------------------------------------------


def test_datetime_part_and_feature_selection_method_no_duplicates() -> None:
    """B8."""
    for vocabulary in (DatetimePart, FeatureSelectionMethod):
        values = get_args(vocabulary)
        assert len(values) == len(set(values)), f"{vocabulary!r} has duplicate members"


# --- Chunk 03 assertions re-run, to prove nothing broke by appending --------------------------


def test_task_for_target_type_exhaustive() -> None:
    """B9."""
    assert set(TASK_FOR_TARGET_TYPE.keys()) == set(get_args(TargetType))


def test_task_for_target_type_no_extra_keys() -> None:
    """B10."""
    assert TASK_FOR_TARGET_TYPE.keys() == set(get_args(TargetType))
    assert len(TASK_FOR_TARGET_TYPE) == len(get_args(TargetType))


def test_metrics_for_task_exhaustive() -> None:
    """B11."""
    assert set(METRICS_FOR_TASK.keys()) == set(get_args(Task))


def test_classification_and_regression_metric_sets_disjoint() -> None:
    """B12."""
    assert METRICS_FOR_TASK["classification"].isdisjoint(METRICS_FOR_TASK["regression"])


def test_metric_equals_union_of_both_sets() -> None:
    """B13."""
    flattened = {value for branch in get_args(Metric) for value in get_args(branch)}
    assert flattened == METRICS_FOR_TASK["classification"] | METRICS_FOR_TASK["regression"]


def test_classification_and_regression_metric_vocab_no_duplicates() -> None:
    """B14."""
    for vocabulary in (ClassificationMetric, RegressionMetric):
        values = get_args(vocabulary)
        assert len(values) == len(set(values)), f"{vocabulary!r} has duplicate members"


# --- Tunable hyperparameters -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("C", 0.5),
        ("C", 2),
        ("alpha", 0.0),
        ("max_iter", 100),
        ("max_depth", None),
        ("max_depth", 3),
        ("min_samples_split", 2),
        ("max_leaf_nodes", None),
        ("max_leaf_nodes", 15),
        ("fit_intercept", False),
        ("max_features", "sqrt"),
        ("max_features", None),
        ("max_features", 0.5),
        ("max_features", 3),
    ],
)
def test_tunable_kinds_accept(key: str, value: ParamValue) -> None:
    assert tunable_param_errors({key: value}, None) == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("C", 0),
        ("C", -1.0),
        ("C", True),
        ("C", float("inf")),
        ("C", "1"),
        ("alpha", -0.1),
        ("max_iter", 0),
        ("max_iter", 1.5),
        ("max_iter", True),
        ("max_depth", 0),
        ("min_samples_split", 1),
        ("max_leaf_nodes", 1),
        ("fit_intercept", 1),
        ("max_features", "auto"),
        ("max_features", 1.5),
        ("max_features", 0.0),
        ("max_features", (1, 2)),
    ],
)
def test_tunable_kinds_reject(key: str, value: ParamValue) -> None:
    kind = TUNABLE_PARAM_KINDS[key].replace("_", " ")
    assert tunable_param_errors({key: value}, None) == [f"{key}={value!r} is not a valid {kind}"]


def test_tunable_unknown_and_disallowed_keys() -> None:
    errors = tunable_param_errors({"penalty": "l1"}, None)
    assert len(errors) == 1 and "'penalty' is not a tunable parameter" in errors[0]
    errors = tunable_param_errors({"alpha": 1.0}, TUNABLE_PARAMS["logistic_regression"])
    assert len(errors) == 1
    assert "'alpha' is not a tunable parameter" in errors[0]
    assert "allowed: ['C', 'fit_intercept', 'max_iter', 'tol']" in errors[0]


def test_tunable_grid_errors() -> None:
    assert tunable_grid_errors({"C": (0.5, 1.0)}) == []
    assert tunable_grid_errors({"C": (0.5, -1.0, -1.0)}) == [
        "C=-1.0 is not a valid positive number"
    ]
