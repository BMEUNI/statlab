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
    ClassificationMetric,
    DatetimePart,
    FeatureSelectionMethod,
    Metric,
    RegressionMetric,
    TargetType,
    Task,
    VariationDimension,
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
    spec = EstimatorSpec.model_validate({"name": "logistic_regression", "params": {"x": value}})
    assert spec.params["x"] == value


def test_param_value_accepts_flat_tuple() -> None:
    """B6."""
    spec = EstimatorSpec.model_validate({"name": "logistic_regression", "params": {"x": (1, 2, 3)}})
    assert spec.params["x"] == (1, 2, 3)


def test_param_value_rejects_nesting() -> None:
    """B7: pin the non-recursive limit."""
    with pytest.raises(ValidationError):
        EstimatorSpec.model_validate({"name": "logistic_regression", "params": {"x": ((1, 2),)}})


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
