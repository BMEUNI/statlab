"""The candidate spec: PLAN.md §4.2, D26, D28. The object node 5 (experiment_design) produces.

A candidate is a fully specified modelling approach -- preprocessing + features + estimator +
params -- not just a model name. This is the last pure-schema chunk: no IO, no pandas, no
persistence, no candidate generation. Nothing here imports pandas, or anything beyond stdlib and
pydantic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from statlab_contracts.contract import MissingPolicy
from statlab_contracts.vocabulary import (
    DIMENSION_FOR_FIELD,
    RESERVED_ESTIMATOR_PARAMS,
    TUNABLE_PARAMS,
    CategoricalEncoding,
    ClassWeight,
    DatetimePart,
    FeatureSelectionMethod,
    OutlierPolicy,
    ParamValue,
    ScalingMethod,
    TextEncoding,
    VariationDimension,
    tunable_param_errors,
)

# --- FeatureSelection ------------------------------------------------------------------------


class FeatureSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    method: FeatureSelectionMethod = "none"
    k: int | None = Field(default=None, ge=1)
    threshold: float | None = Field(default=None, ge=0.0)

    @model_validator(mode="after")
    def _k_iff_mutual_info(self) -> FeatureSelection:
        """V1: `k` required iff `method == 'mutual_info'`."""
        if self.method == "mutual_info" and self.k is None:
            raise ValueError("k is mandatory when method == 'mutual_info'")
        if self.method != "mutual_info" and self.k is not None:
            raise ValueError(
                f"k is forbidden when method == {self.method!r} (only allowed for 'mutual_info')"
            )
        return self

    @model_validator(mode="after")
    def _threshold_iff_variance(self) -> FeatureSelection:
        """V2: `threshold` required iff `method == 'variance'`."""
        if self.method == "variance" and self.threshold is None:
            raise ValueError("threshold is mandatory when method == 'variance'")
        if self.method != "variance" and self.threshold is not None:
            raise ValueError(
                f"threshold is forbidden when method == {self.method!r} (only "
                f"allowed for 'variance')"
            )
        return self


# --- PreprocessingSpec -----------------------------------------------------------------------


class PreprocessingSpec(BaseModel):
    """What this candidate does about missing values and outliers.

    Reuses chunk 03's `MissingPolicy` unchanged -- a candidate's choice and the contract's
    default are the same shape, and duplicating it would be the drift risk chunk 02 warned
    about.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing: MissingPolicy = Field(default_factory=MissingPolicy)
    outliers: OutlierPolicy = "none"


# --- FeatureSpec ------------------------------------------------------------------------------


class FeatureSpec(BaseModel):
    """How this candidate builds the predictive matrix.

    Note this is the PREDICTIVE matrix only. The inferential matrix is built once per run in
    node 6 with a canonical, unscaled, dummy-coded specification (D31) and is not a candidate
    choice -- which is why nothing here mentions it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    encoding: CategoricalEncoding = "onehot"
    scaling: ScalingMethod = "standard"
    max_cardinality: int = Field(default=30, ge=2)
    text_encoding: TextEncoding = Field(
        default="categorical",
        description="How text-role columns become numbers: 'categorical' encodes them like "
        "categories (default), 'drop' removes them, 'lexicon' counts class keywords learned on "
        "train (classification only), 'tfidf' weights words and word pairs, 'hashed_chars' "
        "hashes character 3-5-grams.",
    )
    interactions: tuple[tuple[str, str], ...] = ()
    datetime_parts: dict[str, tuple[DatetimePart, ...]] = Field(default_factory=dict)
    binned: dict[str, int] = Field(default_factory=dict)  # column -> n_bins
    selection: FeatureSelection = Field(default_factory=FeatureSelection)

    @model_validator(mode="after")
    def _interaction_pairs_distinct_columns(self) -> FeatureSpec:
        """V4: interaction pairs have two distinct columns."""
        for pair in self.interactions:
            if pair[0] == pair[1]:
                raise ValueError(
                    f"features.interactions pair {pair!r} has two identical "
                    f"columns; an interaction requires two distinct columns"
                )
        return self

    @model_validator(mode="after")
    def _interaction_pairs_unique_order_insensitive(self) -> FeatureSpec:
        """V5: interaction pairs unique, order-insensitive -- (a,b) and (b,a) are the same
        interaction."""
        seen: set[frozenset[str]] = set()
        for pair in self.interactions:
            key = frozenset(pair)
            if key in seen:
                raise ValueError(
                    f"features.interactions has duplicate interaction {pair!r} "
                    f"(order-insensitive: (a,b) and (b,a) are the same pair)"
                )
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _datetime_parts_non_empty_and_unique(self) -> FeatureSpec:
        """V6: `datetime_parts` values non-empty and unique per column."""
        for column, parts in self.datetime_parts.items():
            if not parts:
                raise ValueError(f"features.datetime_parts[{column!r}] must not be empty")
            if len(set(parts)) != len(parts):
                raise ValueError(
                    f"features.datetime_parts[{column!r}] entries must be unique, got {parts!r}"
                )
        return self

    @model_validator(mode="after")
    def _binned_values_at_least_two(self) -> FeatureSpec:
        """V7: `binned` values >= 2."""
        for column, n_bins in self.binned.items():
            if n_bins < 2:
                raise ValueError(f"features.binned[{column!r}] must be >= 2, got {n_bins!r}")
        return self


# --- EstimatorSpec --------------------------------------------------------------------------


class EstimatorSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    """Validated against the registry by chunk 27, not here (chunk 04 §1)."""
    params: dict[str, ParamValue] = Field(default_factory=dict)
    class_weight: ClassWeight = "none"
    calibrate: bool = False

    @model_validator(mode="after")
    def _no_tool_fixed_params(self) -> EstimatorSpec:
        reserved = sorted(set(self.params) & RESERVED_ESTIMATOR_PARAMS)
        if reserved:
            raise ValueError(
                f"estimator params {reserved!r} are fixed by the tools "
                "(n_jobs by D17, random_state by D34e); omit them"
            )
        return self

    @model_validator(mode="after")
    def _tunable_params(self) -> EstimatorSpec:
        allowed = TUNABLE_PARAMS.get(self.name)
        if allowed is None:
            return self
        errors = tunable_param_errors(self.params, allowed)
        if errors:
            raise ValueError(f"estimator {self.name!r}: " + "; ".join(errors))
        return self


# --- CandidateSpec --------------------------------------------------------------------------

_UNHASHED_FIELDS: Final[frozenset[str]] = frozenset(
    {"label", "rationale", "is_baseline", "varies_from_baseline"}
)
"""Commentary and bookkeeping, not identity.

`is_baseline` is excluded deliberately: if the baseline is logreg+onehot and another candidate
is also logreg+onehot, that is a duplicate regardless of which one carries the flag. Including
the flag in identity would let the same approach be proposed twice and both get trained.
"""

_LEGACY_TEXT: Final[dict[str, set[str]]] = {"features": {"text_encoding"}}
"""Left out of the hash at its default `categorical` (chunk 82): that value is the behaviour
from before the field existed, so every earlier spec keeps its hash (H17).
"""


class CandidateSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=20)
    """A floor against empty gestures, not a check that it cites an EDA finding -- that is the
    node-5 gate's judgement and a human's (D28). Twenty characters is roughly
    "plan has 47 levels"."""
    is_baseline: bool = False
    preprocessing: PreprocessingSpec = Field(default_factory=PreprocessingSpec)
    features: FeatureSpec = Field(default_factory=FeatureSpec)
    estimator: EstimatorSpec
    varies_from_baseline: tuple[VariationDimension, ...] = ()

    @model_validator(mode="after")
    def _varies_from_baseline_unique(self) -> CandidateSpec:
        """V8: `varies_from_baseline` entries unique."""
        if len(set(self.varies_from_baseline)) != len(self.varies_from_baseline):
            raise ValueError(
                f"varies_from_baseline entries must be unique, got {self.varies_from_baseline!r}"
            )
        return self

    @model_validator(mode="after")
    def _is_baseline_implies_no_variation(self) -> CandidateSpec:
        """V9: `is_baseline` implies `varies_from_baseline == ()`."""
        if self.is_baseline and self.varies_from_baseline:
            raise ValueError(
                f"is_baseline=True forbids a non-empty varies_from_baseline, got "
                f"{self.varies_from_baseline!r}"
            )
        return self

    @property
    def spec_hash(self) -> str:
        """The canonical identity hash: sha256 of the candidate's methodology, excluding
        commentary and bookkeeping fields (`_UNHASHED_FIELDS`).

        Numeric-identity ceiling, deliberate: `mode="json"` preserves numeric type, so
        `params={"C": 1}` and `params={"C": 1.0}` hash DIFFERENTLY and count as distinct
        candidates. Coercing integral floats would be wrong in the other direction --
        `n_estimators=100` must not become `100.0`. In practice the agent emits one type
        consistently; two candidates differing only in int-vs-float would waste one fit, not
        produce a wrong result.
        ponytail: numeric-identity ceiling; upgrade path is a per-param type table from the
        registry (chunk 13) if it ever bites.
        """
        payload: dict[str, JsonValue] = self.model_dump(
            mode="json",
            exclude=_LEGACY_TEXT if self.features.text_encoding == "categorical" else None,
        )
        for field in _UNHASHED_FIELDS:
            payload.pop(field)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- changed_dimensions -----------------------------------------------------------------------


def changed_dimensions(
    baseline: CandidateSpec, candidate: CandidateSpec
) -> frozenset[VariationDimension]:
    """The dimensions in which `candidate` actually differs from `baseline`.

    Compares the hashed subset field by field at the paths in `DIMENSION_FOR_FIELD`, mapping
    each differing path to its dimension. Nested models (e.g. `MissingPolicy`) compare by
    equality -- a `MissingPolicy` that differs in any sub-field counts as one `imputation`
    change, because that is the granularity a reader of the comparison table cares about.
    """
    changed: set[VariationDimension] = set()
    field_values: dict[str, tuple[object, object]] = {
        "preprocessing.missing": (baseline.preprocessing.missing, candidate.preprocessing.missing),
        "preprocessing.outliers": (
            baseline.preprocessing.outliers,
            candidate.preprocessing.outliers,
        ),
        "features.encoding": (baseline.features.encoding, candidate.features.encoding),
        "features.scaling": (baseline.features.scaling, candidate.features.scaling),
        "features.max_cardinality": (
            baseline.features.max_cardinality,
            candidate.features.max_cardinality,
        ),
        "features.text_encoding": (
            baseline.features.text_encoding,
            candidate.features.text_encoding,
        ),
        "features.interactions": (baseline.features.interactions, candidate.features.interactions),
        "features.datetime_parts": (
            baseline.features.datetime_parts,
            candidate.features.datetime_parts,
        ),
        "features.binned": (baseline.features.binned, candidate.features.binned),
        "features.selection": (baseline.features.selection, candidate.features.selection),
        "estimator.name": (baseline.estimator.name, candidate.estimator.name),
        "estimator.params": (baseline.estimator.params, candidate.estimator.params),
        "estimator.class_weight": (
            baseline.estimator.class_weight,
            candidate.estimator.class_weight,
        ),
        "estimator.calibrate": (baseline.estimator.calibrate, candidate.estimator.calibrate),
    }
    for path, dimension in DIMENSION_FOR_FIELD.items():
        before, after = field_values[path]
        if before != after:
            changed.add(dimension)
    return frozenset(changed)


# --- CandidateSet ----------------------------------------------------------------------------


class CandidateSet(BaseModel):
    """The shortlist node 5 produces (D28). Validated as a whole, because every interesting
    rule is about the relationship between candidates rather than any one of them.

    NOT checked here -- deferred to chunk 27's node 5, which has the registry and the contract
    this schema does not:
    - estimator names against the registry (`list_estimators`, chunk 13);
    - the model-family cap (at most two candidates per family without justification, D28) --
      needs the registry's family mapping;
    - `len(candidates) <= contract.constraints.max_candidates` -- needs the contract, and a
      model validator cannot take an external argument;
    - whether a rationale genuinely cites an EDA finding -- not mechanically checkable; this
      chunk enforces only a length floor (`CandidateSpec.rationale`, min_length=20).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[CandidateSpec, ...] = Field(min_length=1)

    @property
    def baseline(self) -> CandidateSpec:
        """The candidate with `is_baseline`. Total, because S2 guarantees exactly one."""
        return next(c for c in self.candidates if c.is_baseline)

    @model_validator(mode="after")
    def _exactly_one_baseline(self) -> CandidateSet:
        """S2: exactly one `is_baseline`."""
        count = sum(1 for c in self.candidates if c.is_baseline)
        if count != 1:
            raise ValueError(f"exactly one candidate must have is_baseline=True, found {count}")
        return self

    @model_validator(mode="after")
    def _spec_hashes_unique(self) -> CandidateSet:
        """S3: all `spec_hash` values unique. Declared before S6 (declaration order is
        load-bearing, chunk 04 §1): when two candidates are identical, "claims to vary in
        encoding but nothing differs" is a confusing way to learn that the same approach was
        proposed twice -- the duplicate-hash message should surface first."""
        seen: dict[str, str] = {}
        for candidate in self.candidates:
            digest = candidate.spec_hash
            if digest in seen:
                raise ValueError(
                    f"candidates {seen[digest]!r} and {candidate.label!r} have identical "
                    f"spec_hash ({digest}); they are the same methodology and one is a "
                    f"duplicate"
                )
            seen[digest] = candidate.label
        return self

    @model_validator(mode="after")
    def _labels_unique_after_stripping(self) -> CandidateSet:
        """S4: all `label` values unique after stripping."""
        seen: set[str] = set()
        for candidate in self.candidates:
            stripped = candidate.label.strip()
            if stripped in seen:
                raise ValueError(
                    f"duplicate label {candidate.label!r} (compared after stripping whitespace)"
                )
            seen.add(stripped)
        return self

    @model_validator(mode="after")
    def _non_baseline_has_variation(self) -> CandidateSet:
        """S5: every non-baseline has non-empty `varies_from_baseline`."""
        for candidate in self.candidates:
            if not candidate.is_baseline and not candidate.varies_from_baseline:
                raise ValueError(
                    f"candidate {candidate.label!r} is not the baseline but has "
                    f"an empty varies_from_baseline"
                )
        return self

    @model_validator(mode="after")
    def _varies_from_baseline_matches_actual_diff(self) -> CandidateSet:
        """S6: `varies_from_baseline` equals `changed_dimensions(baseline, c)` for each
        non-baseline candidate. Both directions matter: claiming a dimension that did not
        change is a false explanation in the report; omitting one that did change hides the
        real cause of a score difference."""
        baseline = self.baseline
        for candidate in self.candidates:
            if candidate.is_baseline:
                continue
            declared = frozenset(candidate.varies_from_baseline)
            actual = changed_dimensions(baseline, candidate)
            if declared != actual:
                raise ValueError(
                    f"candidate {candidate.label!r} declares varies_from_baseline="
                    f"{sorted(declared)!r} but the actual diff from the baseline is "
                    f"{sorted(actual)!r}"
                )
        return self
