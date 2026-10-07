"""Tests for statlab_contracts.candidate models: defaults, V1-V9, C1-C20 per
plan/04_candidate_spec.md §4.3.

Every negative test uses `Model.model_validate({...})`, never a deliberately-invalid kwarg.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from statlab_contracts.candidate import (
    CandidateSpec,
    EstimatorSpec,
    FeatureSelection,
    FeatureSpec,
    PreprocessingSpec,
)
from statlab_contracts.contract import MissingPolicy


def test_estimator_params_must_be_tunable() -> None:
    with pytest.raises(ValidationError, match="'penalty' is not a tunable parameter"):
        EstimatorSpec.model_validate({"name": "logistic_regression", "params": {"penalty": "l1"}})
    with pytest.raises(ValidationError, match="alpha=-1 is not a valid nonnegative number"):
        EstimatorSpec.model_validate({"name": "ridge", "params": {"alpha": -1}})
    spec = EstimatorSpec.model_validate({"name": "fixture", "params": {"choice": 1}})
    assert spec.params == {"choice": 1}


# --- Defaults, one test per model --------------------------------------------------------


def test_feature_selection_defaults() -> None:
    selection = FeatureSelection()
    assert selection.method == "none"
    assert selection.k is None
    assert selection.threshold is None


def test_preprocessing_spec_defaults() -> None:
    preprocessing = PreprocessingSpec()
    assert preprocessing.missing == MissingPolicy()
    assert preprocessing.outliers == "none"


def test_feature_spec_defaults() -> None:
    features = FeatureSpec()
    assert features.encoding == "onehot"
    assert features.scaling == "standard"
    assert features.max_cardinality == 30
    assert features.interactions == ()
    assert features.datetime_parts == {}
    assert features.binned == {}
    assert features.selection == FeatureSelection()


def test_estimator_spec_defaults() -> None:
    estimator = EstimatorSpec(name="logistic_regression")
    assert estimator.params == {}
    assert estimator.class_weight == "none"
    assert estimator.calibrate is False


# --- C1-C9 ----------------------------------------------------------------------------------


def test_c1_minimal_candidate_constructs() -> None:
    """C1: label, rationale, estimator -- constructs; all sub-specs default."""
    candidate = CandidateSpec(
        label="c",
        rationale="a rationale that is at least twenty characters long",
        estimator=EstimatorSpec(name="logistic_regression"),
    )
    assert candidate.preprocessing == PreprocessingSpec()
    assert candidate.features == FeatureSpec()
    assert candidate.is_baseline is False
    assert candidate.varies_from_baseline == ()


def test_c2_missing_estimator_rejected() -> None:
    """C2."""
    with pytest.raises(ValidationError):
        CandidateSpec.model_validate(
            {"label": "c", "rationale": "a rationale that is at least twenty characters long"}
        )


def test_c3_rationale_19_chars_rejected() -> None:
    """C3."""
    with pytest.raises(ValidationError):
        CandidateSpec.model_validate(
            {
                "label": "c",
                "rationale": "x" * 19,
                "estimator": {"name": "logistic_regression"},
            }
        )


def test_c4_rationale_20_chars_valid() -> None:
    """C4."""
    candidate = CandidateSpec(
        label="c", rationale="x" * 20, estimator=EstimatorSpec(name="logistic_regression")
    )
    assert len(candidate.rationale) == 20


def test_c5_label_121_chars_rejected() -> None:
    """C5."""
    with pytest.raises(ValidationError):
        CandidateSpec.model_validate(
            {
                "label": "x" * 121,
                "rationale": "a rationale that is at least twenty characters long",
                "estimator": {"name": "logistic_regression"},
            }
        )


_ALL_MODELS: tuple[type[BaseModel], ...] = (
    FeatureSelection,
    PreprocessingSpec,
    FeatureSpec,
    EstimatorSpec,
    CandidateSpec,
)


@pytest.mark.parametrize("model_cls", _ALL_MODELS)
def test_c6_every_model_frozen(model_cls: type[BaseModel]) -> None:
    """C6: mutation raises on every model."""
    instance: BaseModel
    attr: str
    if model_cls is FeatureSelection:
        instance = FeatureSelection()
        attr = "method"
    elif model_cls is PreprocessingSpec:
        instance = PreprocessingSpec()
        attr = "outliers"
    elif model_cls is FeatureSpec:
        instance = FeatureSpec()
        attr = "encoding"
    elif model_cls is EstimatorSpec:
        instance = EstimatorSpec(name="logistic_regression")
        attr = "name"
    else:
        instance = CandidateSpec(
            label="c",
            rationale="a rationale that is at least twenty characters long",
            estimator=EstimatorSpec(name="logistic_regression"),
        )
        attr = "label"
    with pytest.raises(ValidationError):
        setattr(instance, attr, getattr(instance, attr))


@pytest.mark.parametrize("model_cls", _ALL_MODELS)
def test_c7_extra_forbidden_on_every_model(model_cls: type[BaseModel]) -> None:
    """C7: extra='forbid' on all five, via model_validate."""
    base: dict[str, Any]
    if model_cls is EstimatorSpec:
        base = {"name": "logistic_regression"}
    elif model_cls is CandidateSpec:
        base = {
            "label": "c",
            "rationale": "a rationale that is at least twenty characters long",
            "estimator": {"name": "logistic_regression"},
        }
    else:
        base = {}
    with pytest.raises(ValidationError):
        model_cls.model_validate({**base, "bogus": 1})


def test_c8_json_round_trip() -> None:
    """C8: equality for a fully populated candidate."""
    candidate = CandidateSpec(
        label="c",
        rationale="a rationale that is at least twenty characters long",
        preprocessing=PreprocessingSpec(
            missing=MissingPolicy(default="mean", per_column={"x": "constant"}),
            outliers="clip_iqr",
        ),
        features=FeatureSpec(
            encoding="target",
            scaling="robust",
            interactions=(("age", "income"),),
            datetime_parts={"signup_date": ("year", "month")},
            binned={"age": 5},
            selection=FeatureSelection(method="variance", threshold=0.1),
        ),
        estimator=EstimatorSpec(
            name="random_forest_classifier",
            params={"n_estimators": 200, "max_depth": None},
            class_weight="balanced",
            calibrate=True,
        ),
        varies_from_baseline=("encoding", "estimator"),
    )
    restored = CandidateSpec.model_validate_json(candidate.model_dump_json())
    assert restored == candidate


def test_c9_list_to_tuple_coercion() -> None:
    """C9: interactions, varies_from_baseline, datetime_parts values."""
    candidate = CandidateSpec.model_validate(
        {
            "label": "c",
            "rationale": "a rationale that is at least twenty characters long",
            "features": {
                "interactions": [["age", "income"]],
                "datetime_parts": {"signup_date": ["year", "month"]},
            },
            "estimator": {"name": "logistic_regression"},
            "varies_from_baseline": ["encoding", "derived_features"],
        }
    )
    assert isinstance(candidate.features.interactions, tuple)
    assert candidate.features.interactions == (("age", "income"),)
    assert isinstance(candidate.features.interactions[0], tuple)
    assert isinstance(candidate.features.datetime_parts["signup_date"], tuple)
    assert candidate.features.datetime_parts["signup_date"] == ("year", "month")
    assert isinstance(candidate.varies_from_baseline, tuple)
    assert candidate.varies_from_baseline == ("encoding", "derived_features")


# --- V1-V3: FeatureSelection -----------------------------------------------------------------


def test_v1_k_with_mutual_info_passes() -> None:
    """V1 pass."""
    FeatureSelection.model_validate({"method": "mutual_info", "k": 20})


def test_v1_mutual_info_without_k_fails() -> None:
    """V1 fail."""
    with pytest.raises(ValidationError, match="k is mandatory"):
        FeatureSelection.model_validate({"method": "mutual_info"})


def test_v2_threshold_with_variance_passes() -> None:
    """V2 pass."""
    FeatureSelection.model_validate({"method": "variance", "threshold": 0.05})


def test_v2_variance_without_threshold_fails() -> None:
    """V2 fail."""
    with pytest.raises(ValidationError, match="threshold is mandatory"):
        FeatureSelection.model_validate({"method": "variance"})


def test_v2_threshold_with_non_variance_method_fails() -> None:
    """V2 fail (other direction): threshold set but method is not 'variance'."""
    with pytest.raises(ValidationError, match="threshold is forbidden"):
        FeatureSelection.model_validate({"method": "mutual_info", "k": 5, "threshold": 0.1})


def test_v3_none_without_k_or_threshold_passes() -> None:
    """V3 pass."""
    FeatureSelection.model_validate({"method": "none"})


def test_v3_none_with_k_fails() -> None:
    """V3 fail: method == 'none' forbids k."""
    with pytest.raises(ValidationError, match="k is forbidden"):
        FeatureSelection.model_validate({"method": "none", "k": 5})


# --- C10-C13: FeatureSelection edge cases ------------------------------------------------------


def test_c10_mutual_info_without_k_names_k() -> None:
    """C10."""
    with pytest.raises(ValidationError, match="k"):
        FeatureSelection.model_validate({"method": "mutual_info"})


def test_c11_variance_without_threshold_names_threshold() -> None:
    """C11."""
    with pytest.raises(ValidationError, match="threshold"):
        FeatureSelection.model_validate({"method": "variance"})


def test_c12_none_with_k_set_rejected() -> None:
    """C12: a stated value that will be ignored is a mistake, not a harmless extra."""
    with pytest.raises(ValidationError):
        FeatureSelection.model_validate({"method": "none", "k": 5})


def test_c13_k_zero_rejected() -> None:
    """C13."""
    with pytest.raises(ValidationError):
        FeatureSelection.model_validate({"method": "mutual_info", "k": 0})


# --- V4-V7: FeatureSpec ------------------------------------------------------------------------


def test_v4_distinct_interaction_columns_passes() -> None:
    """V4 pass."""
    FeatureSpec.model_validate({"interactions": [["age", "income"]]})


def test_v4_self_interaction_fails() -> None:
    """V4 fail: self-interaction."""
    with pytest.raises(ValidationError, match="two identical"):
        FeatureSpec.model_validate({"interactions": [["age", "age"]]})


def test_v5_unique_interactions_passes() -> None:
    """V5 pass."""
    FeatureSpec.model_validate({"interactions": [["age", "income"], ["age", "tenure"]]})


def test_v5_order_insensitive_duplicate_interaction_fails() -> None:
    """V5 fail: same interaction twice, order-insensitive."""
    with pytest.raises(ValidationError, match="duplicate interaction"):
        FeatureSpec.model_validate({"interactions": [["a", "b"], ["b", "a"]]})


def test_v6_non_empty_unique_datetime_parts_passes() -> None:
    """V6 pass."""
    FeatureSpec.model_validate({"datetime_parts": {"d": ["year", "month"]}})


def test_v6_empty_datetime_parts_list_fails() -> None:
    """V6 fail: empty part list."""
    with pytest.raises(ValidationError, match="must not be empty"):
        FeatureSpec.model_validate({"datetime_parts": {"d": []}})


def test_v7_binned_two_or_more_passes() -> None:
    """V7 pass."""
    FeatureSpec.model_validate({"binned": {"x": 2}})


def test_v7_binned_one_fails() -> None:
    """V7 fail."""
    with pytest.raises(ValidationError, match=">= 2"):
        FeatureSpec.model_validate({"binned": {"x": 1}})


# --- C14-C18: FeatureSpec edge cases ------------------------------------------------------------


def test_c14_self_interaction_rejected() -> None:
    """C14."""
    with pytest.raises(ValidationError):
        FeatureSpec.model_validate({"interactions": [["age", "age"]]})


def test_c15_order_insensitive_duplicate_rejected() -> None:
    """C15."""
    with pytest.raises(ValidationError):
        FeatureSpec.model_validate({"interactions": [["a", "b"], ["b", "a"]]})


def test_c16_empty_datetime_part_list_rejected() -> None:
    """C16."""
    with pytest.raises(ValidationError):
        FeatureSpec.model_validate({"datetime_parts": {"d": []}})


def test_c17_duplicate_datetime_parts_rejected() -> None:
    """C17."""
    with pytest.raises(ValidationError, match="entries must be unique"):
        FeatureSpec.model_validate({"datetime_parts": {"d": ["year", "year"]}})


def test_c18_binned_one_rejected() -> None:
    """C18."""
    with pytest.raises(ValidationError):
        FeatureSpec.model_validate({"binned": {"x": 1}})


# --- V8-V9: CandidateSpec -----------------------------------------------------------------------


def test_v8_unique_varies_from_baseline_passes() -> None:
    """V8 pass."""
    CandidateSpec.model_validate(
        {
            "label": "c",
            "rationale": "a rationale that is at least twenty characters long",
            "estimator": {"name": "logistic_regression"},
            "varies_from_baseline": ["encoding", "scaling"],
        }
    )


def test_v8_duplicate_varies_from_baseline_fails() -> None:
    """V8 fail."""
    with pytest.raises(ValidationError, match="unique"):
        CandidateSpec.model_validate(
            {
                "label": "c",
                "rationale": "a rationale that is at least twenty characters long",
                "estimator": {"name": "logistic_regression"},
                "varies_from_baseline": ["encoding", "encoding"],
            }
        )


def test_v9_baseline_with_empty_variation_passes() -> None:
    """V9 pass."""
    CandidateSpec.model_validate(
        {
            "label": "c",
            "rationale": "a rationale that is at least twenty characters long",
            "is_baseline": True,
            "estimator": {"name": "logistic_regression"},
        }
    )


def test_v9_baseline_with_nonempty_variation_fails() -> None:
    """V9 fail."""
    with pytest.raises(ValidationError, match="is_baseline=True forbids"):
        CandidateSpec.model_validate(
            {
                "label": "c",
                "rationale": "a rationale that is at least twenty characters long",
                "is_baseline": True,
                "estimator": {"name": "logistic_regression"},
                "varies_from_baseline": ["encoding"],
            }
        )


# --- C19-C20: CandidateSpec edge cases -----------------------------------------------------------


def test_c19_baseline_with_nonempty_variation_rejected() -> None:
    """C19."""
    with pytest.raises(ValidationError):
        CandidateSpec.model_validate(
            {
                "label": "c",
                "rationale": "a rationale that is at least twenty characters long",
                "is_baseline": True,
                "estimator": {"name": "logistic_regression"},
                "varies_from_baseline": ["encoding"],
            }
        )


def test_c20_duplicate_varies_from_baseline_rejected() -> None:
    """C20."""
    with pytest.raises(ValidationError):
        CandidateSpec.model_validate(
            {
                "label": "c",
                "rationale": "a rationale that is at least twenty characters long",
                "estimator": {"name": "logistic_regression"},
                "varies_from_baseline": ["encoding", "encoding"],
            }
        )


@pytest.mark.parametrize("key", ("n_jobs", "random_state"))
def test_estimator_params_reject_tool_fixed_keys(key: str) -> None:
    with pytest.raises(ValidationError, match="fixed by the tools"):
        EstimatorSpec.model_validate({"name": "ridge", "params": {key: 1}})
