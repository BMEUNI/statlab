"""Tests for changed_dimensions and CandidateSet: G1-G5, S1-S7, E1-E12 per
plan/04_candidate_spec.md §4.5.

Every failing case is built by taking `shortlist_dict` and changing exactly one thing, then
calling `CandidateSet.model_validate` -- never a fresh hand-written minimal dict. Every failing
case asserts on MESSAGE CONTENT via `pytest.raises(ValidationError, match=...)`.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from statlab_contracts.candidate import (
    CandidateSet,
    CandidateSpec,
    FeatureSelection,
    FeatureSpec,
    PreprocessingSpec,
    changed_dimensions,
)
from statlab_contracts.contract import MissingPolicy
from statlab_contracts.vocabulary import DIMENSION_FOR_FIELD, VariationDimension

from tests.conftest import make_candidate

# --- changed_dimensions: G1-G5 -----------------------------------------------------------------


def test_g1_identical_candidates_yield_empty_set(baseline_candidate: CandidateSpec) -> None:
    """G1."""
    other = make_candidate(label="other", is_baseline=False, varies_from_baseline=())
    baseline_no_flag = make_candidate(label="baseline-shape", is_baseline=False)
    assert changed_dimensions(baseline_no_flag, other) == frozenset()


_DIMENSION_VARIANTS: dict[str, CandidateSpec] = {
    "preprocessing.missing": make_candidate(
        preprocessing=PreprocessingSpec(missing=MissingPolicy(default="mean"))
    ),
    "preprocessing.outliers": make_candidate(preprocessing=PreprocessingSpec(outliers="clip_iqr")),
    "features.encoding": make_candidate(features=FeatureSpec(encoding="target")),
    "features.scaling": make_candidate(features=FeatureSpec(scaling="robust")),
    "features.max_cardinality": make_candidate(features=FeatureSpec(max_cardinality=50)),
    "features.interactions": make_candidate(features=FeatureSpec(interactions=(("a", "b"),))),
    "features.datetime_parts": make_candidate(
        features=FeatureSpec(datetime_parts={"d": ("year",)})
    ),
    "features.binned": make_candidate(features=FeatureSpec(binned={"x": 3})),
    "features.selection": make_candidate(
        features=FeatureSpec(selection=FeatureSelection(method="variance", threshold=0.1))
    ),
    "estimator.name": make_candidate(estimator_name="random_forest_classifier"),
    "estimator.params": make_candidate(estimator_params={"C": 1}),
    "estimator.class_weight": make_candidate(class_weight="balanced"),
    "estimator.calibrate": make_candidate(calibrate=True),
}


@pytest.mark.parametrize(("path", "dimension"), list(DIMENSION_FOR_FIELD.items()))
def test_g2_each_single_field_change_maps_to_its_dimension(
    path: str, dimension: VariationDimension
) -> None:
    """G2: parametrised over all 13 DIMENSION_FOR_FIELD keys."""
    baseline = make_candidate(label="baseline")
    candidate = _DIMENSION_VARIANTS[path]
    assert changed_dimensions(baseline, candidate) == frozenset({dimension})


def test_g3_derived_features_mechanisms_collapse_to_one_dimension() -> None:
    """G3: changing interactions and binned yields {"derived_features"}, not two entries."""
    baseline = make_candidate(label="baseline")
    candidate = make_candidate(features=FeatureSpec(interactions=(("a", "b"),), binned={"x": 3}))
    assert changed_dimensions(baseline, candidate) == frozenset({"derived_features"})


def test_g4_multiple_dimensions_detected() -> None:
    """G4: changing encoding and estimator yields both."""
    baseline = make_candidate(label="baseline")
    candidate = make_candidate(
        features=FeatureSpec(encoding="target"), estimator_name="hist_gb_classifier"
    )
    assert changed_dimensions(baseline, candidate) == frozenset({"encoding", "estimator"})


def test_g5_nested_change_counts_once() -> None:
    """G5: any MissingPolicy sub-field change -> {"imputation"}."""
    baseline = make_candidate(label="baseline")
    candidate = make_candidate(
        preprocessing=PreprocessingSpec(
            missing=MissingPolicy(default="median", per_column={"x": "constant"})
        )
    )
    assert changed_dimensions(baseline, candidate) == frozenset({"imputation"})


# --- S1-S7: one pass, one fail each -------------------------------------------------------------


def test_s1_at_least_one_candidate_passes(baseline_candidate: CandidateSpec) -> None:
    """S1 pass: a single-candidate set (just the baseline) is valid."""
    CandidateSet(candidates=(baseline_candidate,))


def test_s1_empty_candidates_fails() -> None:
    """S1 fail: field constraint (min_length=1)."""
    with pytest.raises(ValidationError):
        CandidateSet.model_validate({"candidates": []})


def test_s2_exactly_one_baseline_passes(shortlist_dict: dict[str, Any]) -> None:
    """S2 pass."""
    CandidateSet.model_validate(shortlist_dict)


def test_s2_zero_baselines_fails(shortlist_dict: dict[str, Any]) -> None:
    """S2 fail (also E4)."""
    candidates = [{**c, "is_baseline": False} for c in shortlist_dict["candidates"]]
    # dropping is_baseline entirely leaves varies_from_baseline empty on the former baseline,
    # which would also trip S5; give it a variation so S2's message is the one that surfaces
    candidates[0] = {**candidates[0], "varies_from_baseline": ["encoding"]}
    with pytest.raises(ValidationError, match="found 0"):
        CandidateSet.model_validate({"candidates": candidates})


def test_s2_two_baselines_fails(shortlist_dict: dict[str, Any]) -> None:
    """S2 fail (also E5)."""
    candidates = list(shortlist_dict["candidates"])
    candidates[1] = {**candidates[1], "is_baseline": True, "varies_from_baseline": []}
    with pytest.raises(ValidationError, match="found 2"):
        CandidateSet.model_validate({"candidates": candidates})


def test_s3_unique_hashes_passes(shortlist_dict: dict[str, Any]) -> None:
    """S3 pass."""
    CandidateSet.model_validate(shortlist_dict)


def test_s3_duplicate_hashes_fails(shortlist_dict: dict[str, Any]) -> None:
    """S3 fail (also E6): a candidate identical to the baseline in methodology."""
    candidates = list(shortlist_dict["candidates"])
    duplicate = {**candidates[0], "label": "duplicate of baseline", "is_baseline": False}
    with pytest.raises(ValidationError, match="identical spec_hash"):
        CandidateSet.model_validate({"candidates": [*candidates, duplicate]})


def test_s4_unique_labels_after_stripping_passes(shortlist_dict: dict[str, Any]) -> None:
    """S4 pass."""
    CandidateSet.model_validate(shortlist_dict)


def test_s4_duplicate_label_whitespace_only_fails(shortlist_dict: dict[str, Any]) -> None:
    """S4 fail (also E7): duplicate labels differing only in whitespace."""
    candidates = list(shortlist_dict["candidates"])
    padded = {
        **candidates[1],
        "label": f"  {candidates[0]['label']}  ",
        "estimator": {**candidates[1]["estimator"], "params": {"distinct": 1}},
    }
    with pytest.raises(ValidationError, match="duplicate label"):
        CandidateSet.model_validate({"candidates": [*candidates, padded]})


def test_s5_non_baseline_has_variation_passes(shortlist_dict: dict[str, Any]) -> None:
    """S5 pass."""
    CandidateSet.model_validate(shortlist_dict)


def test_s5_non_baseline_empty_variation_fails(shortlist_dict: dict[str, Any]) -> None:
    """S5 fail (also E8)."""
    candidates = list(shortlist_dict["candidates"])
    candidates[1] = {**candidates[1], "varies_from_baseline": []}
    with pytest.raises(ValidationError, match="empty varies_from_baseline"):
        CandidateSet.model_validate({"candidates": candidates})


def test_s6_declared_matches_actual_diff_passes(shortlist_dict: dict[str, Any]) -> None:
    """S6 pass."""
    CandidateSet.model_validate(shortlist_dict)


def test_s6_over_claimed_dimension_fails(shortlist_dict: dict[str, Any]) -> None:
    """S6 fail (also E9): candidate claims an extra dimension beyond the actual diff."""
    candidates = list(shortlist_dict["candidates"])
    candidates[1] = {**candidates[1], "varies_from_baseline": ["class_weight", "scaling"]}
    with pytest.raises(ValidationError, match="actual diff"):
        CandidateSet.model_validate({"candidates": candidates})


# --- E1-E12 --------------------------------------------------------------------------------------


def test_e1_shortlist_validates(shortlist_dict: dict[str, Any]) -> None:
    """E1: all seven rules satisfied."""
    CandidateSet.model_validate(shortlist_dict)


def test_e2_shortlist_round_trips(shortlist_dict: dict[str, Any]) -> None:
    """E2."""
    original = CandidateSet.model_validate(shortlist_dict)
    restored = CandidateSet.model_validate_json(original.model_dump_json())
    assert restored == original


def test_e3_empty_candidates_rejected() -> None:
    """E3."""
    with pytest.raises(ValidationError):
        CandidateSet.model_validate({"candidates": []})


def test_e4_zero_baselines_names_count(shortlist_dict: dict[str, Any]) -> None:
    """E4."""
    candidates = [{**c, "is_baseline": False} for c in shortlist_dict["candidates"]]
    candidates[0] = {**candidates[0], "varies_from_baseline": ["encoding"]}
    with pytest.raises(ValidationError, match="found 0"):
        CandidateSet.model_validate({"candidates": candidates})


def test_e5_two_baselines_names_count(shortlist_dict: dict[str, Any]) -> None:
    """E5."""
    candidates = list(shortlist_dict["candidates"])
    candidates[1] = {**candidates[1], "is_baseline": True, "varies_from_baseline": []}
    with pytest.raises(ValidationError, match="found 2"):
        CandidateSet.model_validate({"candidates": candidates})


def test_e6_duplicate_hashes_names_both_labels(shortlist_dict: dict[str, Any]) -> None:
    """E6."""
    candidates = list(shortlist_dict["candidates"])
    duplicate = {**candidates[0], "label": "duplicate of baseline", "is_baseline": False}
    with pytest.raises(
        ValidationError,
        match=r"candidates '.*' and 'duplicate of baseline' have identical spec_hash",
    ):
        CandidateSet.model_validate({"candidates": [*candidates, duplicate]})


def test_e7_duplicate_label_whitespace_only_rejected(shortlist_dict: dict[str, Any]) -> None:
    """E7."""
    candidates = list(shortlist_dict["candidates"])
    padded = {
        **candidates[1],
        "label": f"  {candidates[0]['label']}  ",
        "estimator": {**candidates[1]["estimator"], "params": {"distinct": 1}},
    }
    with pytest.raises(ValidationError, match="duplicate label"):
        CandidateSet.model_validate({"candidates": [*candidates, padded]})


def test_e8_non_baseline_empty_variation_rejected(shortlist_dict: dict[str, Any]) -> None:
    """E8."""
    candidates = list(shortlist_dict["candidates"])
    candidates[1] = {**candidates[1], "varies_from_baseline": []}
    with pytest.raises(ValidationError):
        CandidateSet.model_validate({"candidates": candidates})


def test_e9_over_claimed_dimension_names_claimed_and_actual(
    shortlist_dict: dict[str, Any],
) -> None:
    """E9: candidate claims ("encoding",) but changed only the estimator ->
    ValidationError naming claimed vs actual. This is what makes varies_from_baseline
    evidence rather than a claim."""
    candidates = list(shortlist_dict["candidates"])
    # candidates[2] (c_03, random forest) actually changed estimator+hyperparameters;
    # claim only "encoding" instead, which did not change at all.
    candidates[2] = {**candidates[2], "varies_from_baseline": ["encoding"]}
    with pytest.raises(
        ValidationError, match=r"declares varies_from_baseline=\['encoding'\] but the actual"
    ):
        CandidateSet.model_validate({"candidates": candidates})


def test_e10_under_claimed_dimension_names_claimed_and_actual(
    shortlist_dict: dict[str, Any],
) -> None:
    """E10: changed encoding and estimator, claims only ("encoding",) ->
    ValidationError naming both."""
    candidates = list(shortlist_dict["candidates"])
    # candidates[3] (c_04, hist GB + target encoding) actually changed encoding+estimator;
    # under-claim only "encoding".
    candidates[3] = {**candidates[3], "varies_from_baseline": ["encoding"]}
    with pytest.raises(ValidationError, match=r"actual diff .* is \['encoding', 'estimator'\]"):
        CandidateSet.model_validate({"candidates": candidates})


def test_e11_baseline_property_returns_flagged_candidate(shortlist_dict: dict[str, Any]) -> None:
    """E11."""
    shortlist_set = CandidateSet.model_validate(shortlist_dict)
    assert shortlist_set.baseline.is_baseline is True
    assert shortlist_set.baseline is shortlist_set.candidates[0]


def test_e12_single_candidate_set_is_valid(baseline_candidate: CandidateSpec) -> None:
    """E12: a set of just the baseline is valid -- a run comparing nothing is legal, if
    pointless, and forbidding it would be a policy decision belonging to node 5."""
    candidate_set = CandidateSet(candidates=(baseline_candidate,))
    assert len(candidate_set.candidates) == 1
    assert candidate_set.baseline is baseline_candidate
