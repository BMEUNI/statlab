---
status: completed
dependencies: [01, 02, 03]
completion_date: 2026-09-10
implementation_notes: |
  Implemented as specified. `vocabulary.py` gained `FeatureSelectionMethod`, `DatetimePart`,
  `ParamValue`, `VariationDimension`, `DIMENSION_FOR_FIELD` (13 keys, exhaustiveness proven via
  `model_fields` walks in B2/B3/B4, never hand-listed against a literal copy). `candidate.py`
  implements `FeatureSelection`, `PreprocessingSpec` (reusing chunk 03's `MissingPolicy`
  unchanged), `FeatureSpec`, `EstimatorSpec`, `CandidateSpec` (V1-V9, `spec_hash` property),
  `changed_dimensions()`, and `CandidateSet` (S1-S7, `baseline` property).

  Known-answer digest (H17): `9c550c0bc4ce6433985ef623c5cc896502e1799cd18459cd51bc28034b2820bd`,
  the sha256 of `shortlist.json`'s baseline candidate (logistic_regression, onehot, defaults
  everywhere). Canonical JSON it was computed from:
  `{"estimator":{"calibrate":false,"class_weight":"none","name":"logistic_regression","params":{}},"features":{"binned":{},"datetime_parts":{},"encoding":"onehot","interactions":[],"max_cardinality":30,"scaling":"standard","selection":{"k":null,"method":"none","threshold":null}},"preprocessing":{"missing":{"default":"median","drop_rows_above":null,"per_column":{}},"outliers":"none"}}`
  Reproducible independently: `CandidateSet.model_validate_json(...).baseline.spec_hash`.

  `changed_dimensions` needed nothing beyond field-path comparison by pydantic's default
  `BaseModel.__eq__` -- no field required special-casing. Nested-model equality (e.g.
  `MissingPolicy`) already gives exactly the granularity the spec wants: any sub-field
  difference collapses to one dimension change, which is what a comparison-table reader cares
  about, not five.

  S6 never rejected a candidate considered legitimate while building `shortlist.json`; all five
  candidates (mirroring PLAN.md §4.2's worked example) satisfied every rule on the first
  correctly-specified attempt. No chunk 03 vocabulary needed changing -- `OutlierPolicy`,
  `CategoricalEncoding`, `ScalingMethod`, `ClassWeight` transferred to candidate-level use
  without widening, confirming the chunk 03 report's prediction.

  S7 ("no non-baseline candidate is identical to the baseline") is genuinely unreachable through
  `CandidateSet.model_validate`: S3 (declared earlier, per the "declaration order is
  load-bearing" instruction) always catches any hash collision first, including a
  baseline-collision, because S3 scans all pairs and the baseline is one of the scanned
  candidates. Confirmed empirically before writing the test. Tested S7's own logic directly via
  `CandidateSet.model_construct(...)` (which bypasses all validators) to call
  `_no_non_baseline_identical_to_baseline()` in isolation, with a justified
  `# type: ignore[operator]` on the two call sites (pydantic's `model_validator` decorator
  confuses mypy's static call-checking even though the method is a plain callable at runtime;
  confirmed via `type()` at a REPL). This is not an `[arg-type]` ignore, so it does not violate
  the DoD's "no type: ignore[arg-type]" constraint. The same `model_construct` bypass was also
  used to exercise the `baseline` property's defensive `AssertionError` fallback, which S2
  likewise makes unreachable via normal validated construction -- needed for 100% branch
  coverage without a forbidden `pragma: no cover`.

  No STOP condition was triggered. `is_baseline`'s exclusion from the hash does not conflict
  with PLAN.md §4.2: PLAN.md's own illustrative hash algorithm excludes only `label`,
  `rationale` and `varies_from_baseline`, and simply does not discuss `is_baseline` either way
  (the field appears in PLAN.md's `CandidateSpec` snippet but the surrounding hash description
  never mentions it) -- chunk 04's addition is a documented refinement filling a gap, not a
  contradiction of anything PLAN.md states.

  Test suite: 138 new tests (18 vocabulary B1-B14, 51 model/defaults/V1-V9/C1-C20 tests, 25 hash
  H1-H18, 44 set changed_dimensions/S1-S7/E1-E12, incl. two extra branch-coverage tests beyond
  the spec's minimum pass/fail pairs: V2's threshold-forbidden-when-not-variance direction, and
  the baseline property's unreachable fallback). Total 566 (428 existing + 138 new), 100%
  line+branch coverage project-wide, mypy --strict clean on 31 source files, ruff clean.

  Forward note for chunk 05: `CandidateSpec`, `CandidateSet` and `DataContract` will all need to
  round-trip through SQLite as `spec_json` (candidates table). `model_dump(mode="json")` +
  `model_validate_json` round-trips exactly for all three -- verified via explicit round-trip
  tests (C8, E2, and chunk 03's C3). The one property to watch: `model_dump(mode="json")`
  serialises tuples as JSON arrays, and `model_validate`/`model_validate_json` coerce arrays
  back to tuples on load (confirmed via C9, H14), so tuple-ness survives a round trip through a
  TEXT column storing the JSON string. `sort_keys=True` in `spec_hash`'s own canonicalisation
  means key order in the stored JSON is irrelevant to identity, but the DB should still store
  `model_dump_json()`'s output verbatim (not re-serialise it through some other path) so that
  reloading and re-hashing a stored candidate reproduces the same `spec_hash` byte-for-byte.
---

# Chunk 04 — Candidate specs, canonical hashing, and shortlist rules

**Goal.** Implement the object node 5 produces: a shortlist of fully specified modelling
approaches (`PLAN.md` §4.2, D28). Three things make it more than a data holder — a canonical
hash so duplicates cannot slip through, a verified `varies_from_baseline` so the comparison table
is trustworthy, and set-level rules enforcing the shortlist discipline.

Last pure-schema chunk. Chunk 05 introduces IO.

---

## 1. Prerequisites & Context

### State after chunk 03 (verified, committed)

```
contracts/statlab_contracts/vocabulary.py  # 9 vocabularies, TASK_FOR_TARGET_TYPE,
                                           #   METRICS_FOR_TASK   ← YOU EXTEND THIS
contracts/statlab_contracts/contract.py    # 10 models incl. MissingPolicy, 27 validators,
                                           #   _resolved_task
contracts/statlab_contracts/artifacts.py   # ArtifactRef, ArtifactKind, ArtifactMeta
contracts/statlab_contracts/envelope.py    # ErrorClass, Failure, ToolResult, RETRY_BUDGET
backend/statlab/domains/base.py            # NodeSpec, NodeResult, NodeKind, GatePolicy
backend/statlab/errors.py                  # 7 error classes
tests/conftest.py                          # ref, minimal_contract, full_contract fixtures
tests/fixtures/contracts/{minimal,full}.json
```

428 tests, 100 % coverage, mypy clean on 26 files, ruff clean.

### Conventions that bind this chunk

Everything from chunk 03's table still applies. Two additions from that chunk's report:

- **The `MissingPolicy` pattern** — three explicit fields rather than one dict overloading its keys
  with two meanings — is the template whenever a config shape is tempting to flatten into a dict.
- **Validator declaration order matters**: `model_validator(mode="after")` methods run in
  declaration order and the first raised error short-circuits the rest. Declare the cheapest and
  most specific rules first so messages stay diagnosable.

### What this chunk does NOT decide

| Not here | Where | Why |
|---|---|---|
| Candidate `id` (`c_01`) | chunk 05/22 mints it | Same discipline as artifact IDs: identity is assigned by the store, not claimed by the payload |
| `wave` number | chunk 24 assigns it | Engine state — which wave a candidate arrived in is not something the agent states |
| Estimator name validity | chunk 27, against `list_estimators` | The registry is chunk 13 and depends on the running MCP server, not on this schema (same deferral as chunk 03's `modelling.candidates`) |
| Model-family cap (≤2 per family, D28) | chunk 27 | Requires the registry's family mapping |
| `len(candidates) <= constraints.max_candidates` | chunk 27 | Needs the contract; a model validator cannot take an external argument |
| Whether a rationale genuinely cites an EDA finding | the node-5 gate, and a human | Not mechanically checkable. This chunk enforces only a length floor |

Document these in the `CandidateSet` docstring. Do **not** write stub functions for them — an
unimplemented signature is speculative work, and chunk 27 will define its own.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| **modify** | `contracts/statlab_contracts/vocabulary.py` — append four vocabularies + one mapping |
| create | `contracts/statlab_contracts/candidate.py` |
| create | `tests/test_candidate_vocabulary.py` |
| create | `tests/test_candidate_models.py` |
| create | `tests/test_candidate_hash.py` |
| create | `tests/test_candidate_set.py` |
| **modify** | `tests/conftest.py` — add three fixtures (§4.1) |
| create | `tests/fixtures/candidates/shortlist.json` |

No `pyproject.toml` change. No `backend/` change. Still no pandas in `contracts/`.

---

## 3. Implementation Spec

### 3.1 `vocabulary.py` additions

```python
FeatureSelectionMethod = Literal["none", "variance", "mutual_info"]

DatetimePart = Literal[
    "year", "month", "day", "dayofweek", "hour", "quarter", "weekofyear", "is_weekend",
]
"""Parts `add_datetime_parts` (chunk 17) can extract. Closed so a typo is a schema error."""

ParamValue = str | int | float | bool | None | tuple[str | int | float | bool | None, ...]
"""A single hyperparameter value. One level of nesting, deliberately non-recursive: sklearn
params are scalars or flat sequences, and an unbounded recursive type buys nothing while
defeating static typing (chunk 02's JsonValue lesson)."""

VariationDimension = Literal[
    "imputation", "outliers", "encoding", "scaling", "cardinality_handling",
    "derived_features", "feature_selection", "estimator", "hyperparameters",
    "class_weight", "calibration",
]
"""The axes along which one candidate may differ from the baseline (D26's
`varies_from_baseline`). A closed vocabulary rather than free strings, so the comparison table
can group by dimension and a typo cannot invent an axis."""

DIMENSION_FOR_FIELD: dict[str, VariationDimension] = {
    "preprocessing.missing":        "imputation",
    "preprocessing.outliers":       "outliers",
    "features.encoding":            "encoding",
    "features.scaling":             "scaling",
    "features.max_cardinality":     "cardinality_handling",
    "features.interactions":        "derived_features",
    "features.datetime_parts":      "derived_features",
    "features.binned":              "derived_features",
    "features.selection":           "feature_selection",
    "estimator.name":               "estimator",
    "estimator.params":             "hyperparameters",
    "estimator.class_weight":       "class_weight",
    "estimator.calibrate":          "calibration",
}
"""Maps every hashed leaf field to the dimension it varies. Used to verify a candidate's
declared `varies_from_baseline` against the actual diff (§3.4). Three fields map to
`derived_features` because interactions, datetime parts and binning are one axis from a
reader's point of view — "this candidate engineered features" — even though they are three
mechanisms."""
```

Exhaustiveness is a **tested requirement**: every leaf path under the hashed fields must appear
in `DIMENSION_FOR_FIELD`, and every value must be a valid `VariationDimension`. Adding a field to
`FeatureSpec` without mapping it must fail the suite.

### 3.2 `candidate.py` — the four models

All `ConfigDict(frozen=True, extra="forbid")`. All collections `tuple` or `dict`.

```python
class FeatureSelection(BaseModel):
    method: FeatureSelectionMethod = "none"
    k: int | None = Field(default=None, ge=1)
    threshold: float | None = Field(default=None, ge=0.0)

    # V1: k required iff method == "mutual_info"
    # V2: threshold required iff method == "variance"
    # V3: method == "none" forbids both


class PreprocessingSpec(BaseModel):
    """What this candidate does about missing values and outliers.

    Reuses chunk 03's `MissingPolicy` unchanged — a candidate's choice and the contract's
    default are the same shape, and duplicating it would be the drift risk chunk 02 warned
    about.
    """
    missing: MissingPolicy = Field(default_factory=MissingPolicy)
    outliers: OutlierPolicy = "none"


class FeatureSpec(BaseModel):
    """How this candidate builds the predictive matrix.

    Note this is the PREDICTIVE matrix only. The inferential matrix is built once per run in
    node 6 with a canonical, unscaled, dummy-coded specification (D31) and is not a candidate
    choice — which is why nothing here mentions it.
    """
    encoding: CategoricalEncoding = "onehot"
    scaling: ScalingMethod = "standard"
    max_cardinality: int = Field(default=30, ge=2)
    interactions: tuple[tuple[str, str], ...] = ()
    datetime_parts: dict[str, tuple[DatetimePart, ...]] = Field(default_factory=dict)
    binned: dict[str, int] = Field(default_factory=dict)       # column -> n_bins
    selection: FeatureSelection = Field(default_factory=FeatureSelection)

    # V4: interaction pairs have two distinct columns
    # V5: interaction pairs unique, order-insensitive — (a,b) and (b,a) are the same interaction
    # V6: datetime_parts values non-empty and unique per column
    # V7: binned values >= 2


class EstimatorSpec(BaseModel):
    name: str = Field(min_length=1)
    """Validated against the registry by chunk 27, not here (§1)."""
    params: dict[str, ParamValue] = Field(default_factory=dict)
    class_weight: ClassWeight = "none"
    calibrate: bool = False


class CandidateSpec(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=20)
    """A floor against empty gestures, not a check that it cites an EDA finding — that is the
    node-5 gate's judgement and a human's (D28). Twenty characters is roughly
    "plan has 47 levels"."""
    is_baseline: bool = False
    preprocessing: PreprocessingSpec = Field(default_factory=PreprocessingSpec)
    features: FeatureSpec = Field(default_factory=FeatureSpec)
    estimator: EstimatorSpec
    varies_from_baseline: tuple[VariationDimension, ...] = ()

    # V8: varies_from_baseline entries unique
    # V9: is_baseline implies varies_from_baseline == ()

    @property
    def spec_hash(self) -> str: ...     # §3.3
```

`estimator` is the only required member — a candidate with no model is not a candidate. Everything
else defaults, mirroring the contract's optional-by-default posture.

### 3.3 The canonical spec hash

```python
_UNHASHED_FIELDS: Final[frozenset[str]] = frozenset(
    {"label", "rationale", "is_baseline", "varies_from_baseline"}
)
"""Commentary and bookkeeping, not identity.

`is_baseline` is excluded deliberately: if the baseline is logreg+onehot and another candidate
is also logreg+onehot, that is a duplicate regardless of which one carries the flag. Including
the flag would let the same approach be proposed twice.
"""
```

Algorithm, exactly:

1. `payload = self.model_dump(mode="json")`
2. `for field in _UNHASHED_FIELDS: payload.pop(field)`
3. `canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
4. `return hashlib.sha256(canonical.encode("utf-8")).hexdigest()`

`sort_keys=True` is what makes key order irrelevant; `separators` removes whitespace variance;
`mode="json"` normalises tuples to lists so a tuple/list distinction cannot change identity.

**Known ceiling, document it in the docstring.** Numeric type is preserved, so `params={"C": 1}`
and `params={"C": 1.0}` hash differently and count as distinct candidates. Coercing integral
floats would be wrong in the other direction — `n_estimators=100` must not become `100.0` —
so the distinction stays. In practice the agent emits one or the other consistently; two
candidates differing only in int-versus-float would waste one fit, not produce a wrong result.
`ponytail: numeric-identity ceiling; upgrade path is a per-param type table from the registry
(chunk 13) if it ever bites.`

**Drift guard, a required test.** Assert
`set(CandidateSpec.model_fields) - _UNHASHED_FIELDS == {"preprocessing", "features", "estimator"}`.
Adding a field to `CandidateSpec` then fails the suite, forcing an explicit decision about whether
it belongs to identity. An exclusion list alone would silently hash a new field; a positive list
alone would silently ignore one. The list plus this test is what makes either mistake loud.

### 3.4 Verifying `varies_from_baseline`

D26 says `varies_from_baseline` "is what makes the final comparison interpretable". Unverified, it
is a claim; verified, it is evidence. Implement:

```python
def changed_dimensions(baseline: CandidateSpec, candidate: CandidateSpec) -> frozenset[VariationDimension]:
    """The dimensions in which `candidate` actually differs from `baseline`.

    Compares the hashed subset field by field at the paths in DIMENSION_FOR_FIELD, mapping each
    differing path to its dimension. Nested models compare by equality — a MissingPolicy that
    differs in any sub-field counts as one `imputation` change, because that is the granularity a
    reader of the comparison table cares about.
    """
```

Then `CandidateSet` enforces, per non-baseline candidate:

`set(candidate.varies_from_baseline) == changed_dimensions(baseline, candidate)`

Both directions matter. Claiming a dimension that did not change is a false explanation in the
report; omitting one that did change hides the real cause of a score difference.

### 3.5 `CandidateSet` and the shortlist rules

```python
class CandidateSet(BaseModel):
    """The shortlist node 5 produces (D28). Validated as a whole, because every interesting
    rule is about the relationship between candidates rather than any one of them.

    NOT checked here — see chunk 04 §1: estimator names against the registry, the ≤2-per-family
    cap, `len() <= constraints.max_candidates`, and whether a rationale genuinely cites an EDA
    finding. All four need either the registry or the contract, and belong to chunk 27's node 5.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[CandidateSpec, ...] = Field(min_length=1)

    @property
    def baseline(self) -> CandidateSpec:
        """The candidate with `is_baseline`. Total, because S2 guarantees exactly one."""
        return next(c for c in self.candidates if c.is_baseline)
```

Set-level rules, declared in this order:

| # | Rule | Message must name |
|---|---|---|
| S1 | at least one candidate | — (field constraint) |
| S2 | exactly one `is_baseline` | how many were found |
| S3 | all `spec_hash` values unique | the two colliding labels |
| S4 | all `label` values unique after stripping | the duplicate label |
| S5 | every non-baseline has non-empty `varies_from_baseline` | the offending label |
| S6 | `varies_from_baseline` equals `changed_dimensions(baseline, c)` for each non-baseline | the label, what was claimed, what actually differs |

S3 before S6: a duplicate is a cheaper and more obvious failure, and reporting "claims to vary in
encoding but nothing differs" is confusing when the real problem is that two candidates are the
same. Declaration order is load-bearing here (see §1).

**There is deliberately no "non-baseline identical to the baseline" rule.** An earlier draft had
one, justified as giving a clearer message than S3's duplicate-hash error. It was unreachable: S3
compares all pairs *including* the baseline, so a baseline-identical candidate always trips S3
first. The cost of keeping it was two validator-bypassing tests and two `type: ignore` comments to
cover code that cannot execute — the same mistake as chunk 02's `retryable` field, a check that can
only ever be satisfied trivially. Its message quality was not worth dead code in a module 36 later
chunks read.

For the same reason `baseline` uses `next(...)` rather than a loop with a trailing
`raise AssertionError`. Verified empirically: the loop-and-raise form leaves the raise permanently
uncovered, while `next(...)` reaches 100 % line and branch coverage with no unreachable statement.

---

## 4. Test Suite Spec

Target: **~130 new tests**, total ≥ **558**.

### 4.1 `tests/conftest.py` additions

```python
@pytest.fixture
def baseline_candidate() -> CandidateSpec:
    """A cheap interpretable baseline: logistic_regression, one-hot, no engineering."""

@pytest.fixture
def shortlist() -> CandidateSet:
    """Loaded from tests/fixtures/candidates/shortlist.json.

    Five candidates mirroring PLAN.md §4.2's worked example on an imbalanced binary target:
    baseline logreg; logreg + balanced class weight; random forest; hist GB + target encoding;
    logreg + an age×income interaction. Must satisfy all six set rules, and each
    `varies_from_baseline` must match its actual diff — if it does not, the fixture is wrong.
    """

def make_candidate(*, label: str = ..., estimator_name: str = ..., ...) -> CandidateSpec:
    """Typed keyword-parameter helper. NOT `**overrides: object` — see the index note; that
    shape forces a `**dict` construction and an arg-type suppression under init_typed."""
```

### 4.2 `tests/test_candidate_vocabulary.py` (~14)

| # | Test | Assertion |
|---|---|---|
| B1 | `DIMENSION_FOR_FIELD` values are all valid `VariationDimension` | via `get_args` |
| B2 | **exhaustive over hashed leaves** | every leaf path under `preprocessing`, `features`, `estimator` appears as a key. Derive the paths from `model_fields`, do not hand-list them |
| B3 | no extra keys | every key corresponds to a real field path |
| B4 | every `VariationDimension` is reachable | each dimension is the value of at least one key, so no dimension is unusable |
| B5 | `ParamValue` accepts each scalar type | parametrised: `str`, `int`, `float`, `bool`, `None` |
| B6 | `ParamValue` accepts a flat tuple | `(1, 2, 3)` |
| B7 | `ParamValue` rejects nesting | `((1, 2),)` fails — pin the non-recursive limit |
| B8 | `DatetimePart` and `FeatureSelectionMethod` have no duplicates | via `get_args` |
| B9–B14 | chunk 03's vocabularies still exhaustive | re-run the chunk 03 assertions to prove nothing was broken by appending |

### 4.3 `tests/test_candidate_models.py` (~45)

Defaults per model (4 tests), then:

| # | Test | Expect |
|---|---|---|
| C1 | minimal candidate: `label`, `rationale`, `estimator` | constructs; all sub-specs default |
| C2 | missing `estimator` | `ValidationError` |
| C3 | `rationale` of 19 chars | `ValidationError` |
| C4 | `rationale` of 20 chars | valid |
| C5 | `label` of 121 chars | `ValidationError` |
| C6 | all four models frozen | parametrised, mutation raises |
| C7 | `extra="forbid"` on all four | parametrised via `model_validate` |
| C8 | JSON round-trip | equality for a fully populated candidate |
| C9 | list→tuple coercion | `interactions`, `varies_from_baseline`, `datetime_parts` values |

Rules V1–V9, one pass and one fail each (18 tests), plus:

| # | Test | Expect |
|---|---|---|
| C10 | `FeatureSelection(method="mutual_info")` without `k` | `ValidationError` naming `k` |
| C11 | `method="variance"` without `threshold` | `ValidationError` naming `threshold` |
| C12 | `method="none"` with `k` set | `ValidationError` — a stated value that will be ignored is a mistake, not a harmless extra |
| C13 | `k=0` | `ValidationError` |
| C14 | interaction `("age", "age")` | `ValidationError` — self-interaction |
| C15 | `(("a","b"), ("b","a"))` | `ValidationError` — same interaction twice, order-insensitive |
| C16 | `datetime_parts={"d": ()}` | `ValidationError` — empty part list |
| C17 | `datetime_parts={"d": ("year","year")}` | `ValidationError` |
| C18 | `binned={"x": 1}` | `ValidationError` |
| C19 | `is_baseline=True` with non-empty `varies_from_baseline` | `ValidationError` |
| C20 | `varies_from_baseline=("encoding","encoding")` | `ValidationError` |

### 4.4 `tests/test_candidate_hash.py` (~30)

| # | Test | Assertion |
|---|---|---|
| H1 | stable across calls | same object, twice, equal |
| H2 | key order irrelevant | build the same candidate from two dicts with different key order via `model_validate`; hashes equal |
| H3 | `label` does not affect it | two candidates differing only in label collide |
| H4 | `rationale` does not affect it | collide |
| H5 | `is_baseline` does not affect it | collide — the §3.3 rationale |
| H6 | `varies_from_baseline` does not affect it | collide |
| H7 | `estimator.name` affects it | differ |
| H8 | `estimator.params` affects it | differ |
| H9 | `class_weight` affects it | differ |
| H10 | `calibrate` affects it | differ |
| H11 | each `FeatureSpec` field affects it | parametrised over all 7 |
| H12 | each `PreprocessingSpec` field affects it | parametrised over both |
| H13 | nested `MissingPolicy` change affects it | differ |
| H14 | tuple vs list input | `model_validate` with a list, then with a tuple; hashes equal |
| H15 | **drift guard** | `set(model_fields) - _UNHASHED_FIELDS == {"preprocessing","features","estimator"}` |
| H16 | 64 lowercase hex chars | format |
| H17 | known-answer | pin the exact digest of `shortlist.json`'s baseline. If the algorithm changes, this fails — which is the point, because a silent hash change re-partitions every stored candidate |
| H18 | numeric-type ceiling is real | `params={"C": 1}` and `{"C": 1.0}` hash **differently**; assert the documented behaviour rather than leaving it unpinned |

H17 is the one that matters most. Write the digest as a literal, produced by running the code once
and pasting it — and if a later chunk changes the algorithm, treat the failure as a migration
question, not a test to update.

### 4.5 `tests/test_candidate_set.py` (~40)

`changed_dimensions` (~14):

| # | Test | Expect |
|---|---|---|
| G1 | identical candidates | `frozenset()` |
| G2 | each single-field change → its dimension | parametrised over all 13 `DIMENSION_FOR_FIELD` keys |
| G3 | three `derived_features` mechanisms collapse to one dimension | changing `interactions` and `binned` yields `{"derived_features"}`, not two entries |
| G4 | multiple dimensions | changing encoding and estimator yields both |
| G5 | nested change counts once | any `MissingPolicy` sub-field change → `{"imputation"}` |

Set rules S1–S6, one pass and one fail each (12 tests), plus:

| # | Test | Expect |
|---|---|---|
| E1 | `shortlist.json` validates | all seven rules satisfied |
| E2 | `shortlist.json` round-trips | JSON equality |
| E3 | empty `candidates` | `ValidationError` |
| E4 | zero baselines | `ValidationError` naming the count |
| E5 | two baselines | `ValidationError` naming the count |
| E6 | duplicate hashes | `ValidationError` naming both labels |
| E7 | duplicate labels differing only in whitespace | `ValidationError` |
| E8 | non-baseline with empty `varies_from_baseline` | `ValidationError` |
| E9 | over-claimed dimension | candidate claims `("encoding",)` but changed only the estimator → `ValidationError` naming claimed vs actual |
| E10 | under-claimed dimension | changed encoding *and* estimator, claims only `("encoding",)` → `ValidationError` |
| E11 | `baseline` property returns the flagged one | identity |
| E12 | single-candidate set | a set of just the baseline is valid — a run comparing nothing is legal, if pointless, and forbidding it would be a policy decision belonging to node 5 |

E9 and E10 are the tests that make `varies_from_baseline` evidence rather than a claim. If they
pass, the comparison table in chunk 39 can be trusted.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **558** (428 existing + ~130 new).
- [ ] Coverage **100 %** line and branch on `candidate.py` and the appended part of
      `vocabulary.py`; project stays 100 %.
- [ ] Rules V1–V9 and S1–S6 each have a passing and a failing case; every failing case asserts on
      message content.
- [ ] `DIMENSION_FOR_FIELD` exhaustiveness (B2/B3) is derived from `model_fields`, not hand-listed.
- [ ] The hash drift guard (H15) and known-answer digest (H17) both exist.
- [ ] `shortlist.json` satisfies all six set rules with every `varies_from_baseline` matching its
      actual diff.

**Static analysis**
- [ ] `make typecheck` and `make lint` exit 0.
- [ ] No `Any`, no `# type: ignore[arg-type]`, no `pragma: no cover`.
- [ ] No negative test constructs with kwargs; all use `model_validate`.
- [ ] The `make_candidate` helper takes typed keyword parameters, not `**overrides: object`.

**Boundary**
- [ ] `contracts/` still imports only stdlib and `pydantic` — now also `hashlib` and `json`, both
      stdlib. Still no pandas.
- [ ] Nothing in `backend/` changed.

**Design checks**
- [ ] `MissingPolicy` is reused from chunk 03, not redefined.
- [ ] `is_baseline` is excluded from the hash, with the §3.3 reason in the docstring.
- [ ] The numeric-identity ceiling is documented with a `ponytail:` comment and pinned by H18.
- [ ] `CandidateSet`'s docstring lists the four deferred checks and names chunk 27.
- [ ] No stub functions for the deferred checks.

**Housekeeping**
- [ ] Frontmatter → `completed`, notes covering: the known-answer digest value and how it was
      produced; whether `changed_dimensions` needed anything beyond field-path comparison; and
      whether any chunk 03 vocabulary needed changing (it should not have).
- [ ] `plan/00_index.md` chunk 04 row → `completed`.

### Explicitly out of scope

No candidate *generation* (node 5, chunk 27). No persistence (chunk 05). No registry lookup, no
family mapping, no contract-relative caps. No `wave` or `id` fields.

### Stop and ask if

- `changed_dimensions` cannot be made total over `DIMENSION_FOR_FIELD` without special-casing a
  field — that would mean the field's dimension is genuinely ambiguous and needs a decision.
- S6 turns out to reject a candidate a real shortlist would contain, which would mean the
  dimension granularity is wrong rather than the rule.
- Excluding `is_baseline` from the hash conflicts with something in `PLAN.md` §4.2 I have missed.
