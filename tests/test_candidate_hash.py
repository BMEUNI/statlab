"""Tests for CandidateSpec.spec_hash: H1-H18 per plan/04_candidate_spec.md §4.4.

H17 pins the exact known-answer digest of shortlist.json's baseline. If a later chunk changes
the hashing algorithm, THIS TEST MUST FAIL -- that is the point, not a bug to silence. A failure
here means every previously stored candidate's identity has shifted, which is a migration
question (how do we re-key existing rows), not a test to quietly update.
"""

from __future__ import annotations

import re

import pytest
from statlab_contracts.candidate import (
    _UNHASHED_FIELDS,
    CandidateSpec,
    EstimatorSpec,
    FeatureSelection,
    FeatureSpec,
    PreprocessingSpec,
)
from statlab_contracts.contract import MissingPolicy

_HEX64 = re.compile(r"^[0-9a-f]{64}\Z")


def _candidate(**overrides: object) -> CandidateSpec:
    """Local helper, distinct from conftest's `make_candidate`: this file needs to construct
    with arbitrary sub-model overrides for hash-sensitivity tests, which typed kwargs alone
    cannot express without duplicating every nested field as its own parameter. Kept
    module-private and used only in this file."""
    defaults: dict[str, object] = {
        "label": "c",
        "rationale": "a rationale that is at least twenty characters long",
        "estimator": EstimatorSpec(name="logistic_regression"),
    }
    defaults.update(overrides)
    return CandidateSpec.model_validate(defaults)


def test_h1_stable_across_calls() -> None:
    """H1."""
    candidate = _candidate()
    assert candidate.spec_hash == candidate.spec_hash


def test_h2_key_order_irrelevant() -> None:
    """H2: build the same candidate from two dicts with different key order via
    model_validate; hashes equal."""
    a = CandidateSpec.model_validate(
        {
            "label": "c",
            "rationale": "a rationale that is at least twenty characters long",
            "estimator": {"name": "logistic_regression", "params": {"C": 1, "max_iter": 2}},
        }
    )
    b = CandidateSpec.model_validate(
        {
            "estimator": {"params": {"max_iter": 2, "C": 1}, "name": "logistic_regression"},
            "rationale": "a rationale that is at least twenty characters long",
            "label": "c",
        }
    )
    assert a.spec_hash == b.spec_hash


def test_h3_label_does_not_affect_hash() -> None:
    """H3."""
    a = _candidate(label="alpha")
    b = _candidate(label="beta")
    assert a.spec_hash == b.spec_hash


def test_h4_rationale_does_not_affect_hash() -> None:
    """H4."""
    a = _candidate(rationale="a rationale that is at least twenty characters long")
    b = _candidate(rationale="a completely different rationale of sufficient length")
    assert a.spec_hash == b.spec_hash


def test_h5_is_baseline_does_not_affect_hash() -> None:
    """H5: the §3.3 rationale -- a duplicate is a duplicate regardless of which one is
    flagged baseline."""
    a = _candidate(is_baseline=True)
    b = _candidate(is_baseline=False)
    assert a.spec_hash == b.spec_hash


def test_h6_varies_from_baseline_does_not_affect_hash() -> None:
    """H6."""
    a = _candidate(varies_from_baseline=())
    b = _candidate(varies_from_baseline=("encoding", "scaling"))
    assert a.spec_hash == b.spec_hash


def test_h7_estimator_name_affects_hash() -> None:
    """H7."""
    a = _candidate(estimator=EstimatorSpec(name="logistic_regression"))
    b = _candidate(estimator=EstimatorSpec(name="random_forest_classifier"))
    assert a.spec_hash != b.spec_hash


def test_h8_estimator_params_affects_hash() -> None:
    """H8."""
    a = _candidate(estimator=EstimatorSpec(name="logistic_regression", params={}))
    b = _candidate(estimator=EstimatorSpec(name="logistic_regression", params={"C": 1}))
    assert a.spec_hash != b.spec_hash


def test_h9_class_weight_affects_hash() -> None:
    """H9."""
    a = _candidate(estimator=EstimatorSpec(name="logistic_regression", class_weight="none"))
    b = _candidate(estimator=EstimatorSpec(name="logistic_regression", class_weight="balanced"))
    assert a.spec_hash != b.spec_hash


def test_h10_calibrate_affects_hash() -> None:
    """H10."""
    a = _candidate(estimator=EstimatorSpec(name="logistic_regression", calibrate=False))
    b = _candidate(estimator=EstimatorSpec(name="logistic_regression", calibrate=True))
    assert a.spec_hash != b.spec_hash


# --- H11: each FeatureSpec field affects the hash, parametrised over all 8 --------------------

_FEATURE_SPEC_VARIANTS: tuple[tuple[str, FeatureSpec], ...] = (
    ("encoding", FeatureSpec(encoding="target")),
    ("scaling", FeatureSpec(scaling="robust")),
    ("max_cardinality", FeatureSpec(max_cardinality=50)),
    ("text_encoding", FeatureSpec(text_encoding="tfidf")),
    ("interactions", FeatureSpec(interactions=(("a", "b"),))),
    ("datetime_parts", FeatureSpec(datetime_parts={"d": ("year",)})),
    ("binned", FeatureSpec(binned={"x": 3})),
    ("selection", FeatureSpec(selection=FeatureSelection(method="variance", threshold=0.1))),
)


@pytest.mark.parametrize(("field_name", "variant"), _FEATURE_SPEC_VARIANTS)
def test_h11_each_feature_spec_field_affects_hash(field_name: str, variant: FeatureSpec) -> None:
    """H11."""
    a = _candidate(features=FeatureSpec())
    b = _candidate(features=variant)
    assert a.spec_hash != b.spec_hash, f"changing {field_name!r} did not affect the hash"


# --- H12: each PreprocessingSpec field affects the hash, parametrised over both ----------------

_PREPROCESSING_VARIANTS: tuple[tuple[str, PreprocessingSpec], ...] = (
    ("missing", PreprocessingSpec(missing=MissingPolicy(default="mean"))),
    ("outliers", PreprocessingSpec(outliers="clip_iqr")),
)


@pytest.mark.parametrize(("field_name", "variant"), _PREPROCESSING_VARIANTS)
def test_h12_each_preprocessing_spec_field_affects_hash(
    field_name: str, variant: PreprocessingSpec
) -> None:
    """H12."""
    a = _candidate(preprocessing=PreprocessingSpec())
    b = _candidate(preprocessing=variant)
    assert a.spec_hash != b.spec_hash, f"changing {field_name!r} did not affect the hash"


def test_h13_nested_missing_policy_change_affects_hash() -> None:
    """H13."""
    a = _candidate(preprocessing=PreprocessingSpec(missing=MissingPolicy()))
    b = _candidate(
        preprocessing=PreprocessingSpec(missing=MissingPolicy(per_column={"x": "constant"}))
    )
    assert a.spec_hash != b.spec_hash


def test_h14_tuple_vs_list_input_hashes_equal() -> None:
    """H14."""
    from_list = CandidateSpec.model_validate(
        {
            "label": "c",
            "rationale": "a rationale that is at least twenty characters long",
            "features": {"interactions": [["a", "b"]]},
            "estimator": {"name": "logistic_regression"},
        }
    )
    from_tuple = CandidateSpec(
        label="c",
        rationale="a rationale that is at least twenty characters long",
        features=FeatureSpec(interactions=(("a", "b"),)),
        estimator=EstimatorSpec(name="logistic_regression"),
    )
    assert from_list.spec_hash == from_tuple.spec_hash


def test_h15_drift_guard() -> None:
    """H15: an exclusion list alone would silently hash a new field; a positive list alone
    would silently ignore one. The list plus this test makes either mistake loud."""
    assert set(CandidateSpec.model_fields) - _UNHASHED_FIELDS == {
        "preprocessing",
        "features",
        "estimator",
    }


def test_h16_hash_is_64_lowercase_hex_chars() -> None:
    """H16."""
    candidate = _candidate()
    assert _HEX64.match(candidate.spec_hash)


def test_h17_known_answer_digest_of_shortlist_baseline(shortlist: object) -> None:
    """H17: the exact sha256 digest of shortlist.json's baseline candidate, pinned as a
    literal. Produced by running spec_hash once on the fixture and pasting the result.

    If this test fails after a later change to the hashing algorithm (field additions,
    canonicalisation changes, etc.), DO NOT update the literal to make it pass. Every
    previously persisted candidate's spec_hash was computed with the old algorithm, so a
    silent algorithm change re-partitions every stored candidate's identity -- existing rows
    would need an explicit migration (chunk 05), not a quiet test update.
    """
    from statlab_contracts.candidate import CandidateSet

    assert isinstance(shortlist, CandidateSet)
    baseline = shortlist.baseline
    assert baseline.spec_hash == "9c550c0bc4ce6433985ef623c5cc896502e1799cd18459cd51bc28034b2820bd"


def test_h18_numeric_type_ceiling_is_real() -> None:
    """H18: params={"C": 1} and {"C": 1.0} hash DIFFERENTLY -- pin the documented behaviour,
    do not fix it. Coercing integral floats would turn n_estimators=100 into 100.0, which is
    wrong in the other direction.

    ponytail: numeric-identity ceiling; upgrade path is a per-param type table from the
    registry (chunk 13) if it ever bites.
    """
    int_variant = _candidate(estimator=EstimatorSpec(name="logistic_regression", params={"C": 1}))
    float_variant = _candidate(
        estimator=EstimatorSpec(name="logistic_regression", params={"C": 1.0})
    )
    assert int_variant.spec_hash != float_variant.spec_hash


def test_h19_default_text_encoding_is_left_out_of_the_hash() -> None:
    a = _candidate(features=FeatureSpec())
    b = _candidate(features=FeatureSpec(text_encoding="categorical"))
    c = _candidate(features=FeatureSpec(text_encoding="drop"))
    assert a.spec_hash == b.spec_hash
    assert c.spec_hash != a.spec_hash
