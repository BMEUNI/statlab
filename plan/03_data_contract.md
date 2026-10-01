---
status: completed
dependencies: [01, 02]
completion_date: 2026-09-09
implementation_notes: |
  Implemented as specified: `contracts/statlab_contracts/vocabulary.py` (9 closed vocabularies,
  `TASK_FOR_TARGET_TYPE` and `METRICS_FOR_TASK` derived via `get_args`, never hand-listed) and
  `contracts/statlab_contracts/contract.py` (10 models: DatasetOptions, TargetSpec,
  FeatureRules, InferenceGoal, Objective, SplitRules, MissingPolicy, PreprocessingRules, Tuning,
  ModellingRules, Constraints, DataContract — 12 classes total, matching the "10 models" count
  when Tuning and InferenceGoal are counted as the two small helper models nested inside
  ModellingRules/Objective rather than as top-level contract sections).

  Deviation from PLAN.md §3.2 (placement): the contract lives in `contracts/statlab_contracts/`,
  not `backend/statlab/`, because it crosses the process boundary exactly as chunk 02's
  `ToolResult` does — chunk 08's `validate_contract(df, contract)` tool receives it, chunk 27's
  intake node sends it. Recorded per the chunk's own instruction; PLAN.md §3.2's repo layout was
  not otherwise touched (it already showed `contracts/` as a directory, chunk 02 established the
  precedent).

  `MissingPolicy` restructure: PLAN.md §4.1's original illustrative JSON mixed a `default` key,
  per-column keys and a `drop_rows_above` key in one dict, requiring the implicit convention
  "any key that is not one of two reserved names is a column name". Replaced with three explicit
  fields (`default`, `per_column`, `drop_rows_above`) carrying the same information with no
  convention to remember. PLAN.md §4.1 updated to match (the DoD's own housekeeping item).

  All 28 rules (V1-V14, V14 enforced by field type `dict[str, ImputationStrategy]` rather than a
  custom validator; X1-X14) implemented as `model_validator(mode="after")` pure checks — no
  validator mutates or replaces `self`, per the frozen-model gotcha the assignment flagged.
  `_resolved_task` is the sole place "which task is this?" is answered; X2, X3, X10, X11 all
  call it. Verified pydantic's `model_validator(mode="after")` methods run in declaration order
  and the first raised `ValueError` short-circuits the rest for that model — this made the D6
  "one deviation, one error" guarantee hold even when a deviation could theoretically also
  trigger a later-declared rule (e.g. narrowing `features.include` to exclude an inference
  predictor also removes it from `features.exclude` in the same edit, which could have doubled
  as an X14 violation too, but X13 alone fires because it is declared first).

  Validator-ordering note beyond the two gotchas the assignment named: field validation
  (`min_length`, `ge`, `gt`, the `Literal` checks) always completes before any `mode="after"`
  model validator runs, confirmed empirically by relying on it throughout — e.g. `TargetSpec`'s
  V1/V2/V3 can safely assume `self.type` is already a valid `TargetType | None` and never a
  malformed string, so no defensive re-checking of field-level constraints was needed inside any
  cross-field validator.

  No rule was found to be unsatisfiable, and `full.json` (churn/binary-classification domain,
  matching PLAN.md's own illustration) satisfies all 28 rules simultaneously without needing any
  rule to be loosened. No STOP condition was triggered.

  Test suite: 180 new tests (vs. ~125 targeted) — 10 vocabulary (B1-B10), 102 model/defaults/
  bounds/enum tests, 67 consistency tests (V1-V14 and X1-X14 each pass+fail, several rules given
  an extra branch-coverage test beyond the minimum pass+fail pair to reach 100% branch coverage
  on `contract.py`), plus 1 additional boundary-isolation test (subprocess-isolated no-pandas
  check). Total 428 (248 existing + 180 new), 100% line+branch coverage project-wide, mypy
  --strict clean on 26 source files, ruff clean.

  Forward note for chunk 04: `vocabulary.py`'s `CategoricalEncoding`, `ImputationStrategy`,
  `ScalingMethod`, `OutlierPolicy`, `ColumnRole` and `SplitStrategy` are all directly reusable by
  `CandidateSpec`/`FeatureSpec`/`PreprocessingSpec` — a candidate's per-field choice is exactly
  one value from the same closed vocabulary the contract uses for its default. None of the nine
  vocabularies needed widening to support a candidate-level override; the contract states a
  *default*, a candidate states a *specific choice* from the same set, which is why the schema
  was already sufficient without new Literals.
---

# Chunk 03 — The data contract schema

**Goal.** Implement `PLAN.md` §4.1's contract as a validated Pydantic tree: ten models, one
closed vocabulary of enums, and roughly 35 consistency rules. This is the object the user
uploads, the setup wizard produces, node 1 re-validates, and every later node reads.

**Scope boundary that matters.** This chunk validates the contract **against itself**. Validating
it against an actual CSV — do these columns exist, is the target really binary — is the
`validate_contract` MCP tool in chunk 08, which is where pandas lives. Nothing here imports
pandas.

---

## 1. Prerequisites & Context

### State after chunk 02 (verified, committed)

```
contracts/statlab_contracts/artifacts.py   # ArtifactRef, ArtifactKind, ArtifactMeta,
                                           #   parse_artifact_ref, ARTIFACT_ID_PATTERN
contracts/statlab_contracts/envelope.py    # ErrorClass, RETRY_BUDGET, retry_budget,
                                           #   is_retryable, Failure, ToolSuccess,
                                           #   ToolFailure, ToolResult, TOOL_RESULT_ADAPTER
backend/statlab/domains/base.py            # NodeKind, GatePolicy, NodeStatus, NodeSpec, NodeResult
backend/statlab/errors.py                  # StatlabError, ConfigError, ToolCallError,
                                           #   ToolNotAllowedError, ArtifactError,
                                           #   ArtifactLockedError, EngineError
backend/statlab/config.py                  # Settings, load_settings, get_settings, ensure_writable
mcp_server/statlab_tools/settings.py       # ToolSettings, ToolSettingsError, load_tool_settings
tests/conftest.py                          # _clean_env, _clear_settings_cache, empty_env_file,
                                           #   valid_env, ref
```

248 tests, 100 % line + branch coverage, `mypy --strict` clean on 21 files, ruff clean.

### Conventions established by chunks 01–02 that bind this chunk

| Convention | Consequence here |
|---|---|
| `[tool.pydantic-mypy] init_typed = true` | Constructor kwargs are type-checked. **Negative tests use `Model.model_validate({...})`**, never kwargs with a deliberately wrong value. |
| `disallow_any_explicit` is off; `tests/test_no_explicit_any.py` scans source | Do not write `Any` anywhere. Use `pydantic.JsonValue` if a genuinely open payload is needed — it is not needed in this chunk. |
| `ConfigDict(frozen=True, extra="forbid")` on wire models | Same here, for the reason in §3.1. |
| `tuple`, not `list`, on frozen models | `frozen=True` does not freeze list contents. Every collection field in this chunk is a `tuple` or a `dict`. |
| Derive, don't duplicate-and-validate (the `retryable` lesson) | If a contract field is fully determined by another, do not carry both. |
| No implicit string conventions (the `mutual_info_k20` lesson) | Every structured value gets a field, never an encoded string. |
| Typed keyword-parameter test helpers, not `**overrides: object` | Applies to the contract fixtures you will write. |

### Where the contract lives, and why it is not in `backend/`

`PLAN.md` §3.2 places it at `backend/statlab/contract.py`. **Put it in
`contracts/statlab_contracts/contract.py` instead.**

Reason: the contract crosses the process boundary. Chunk 08's `validate_contract(df, contract)`
tool receives it, and chunk 27's intake node sends it. That makes it a wire type by exactly the
argument chunk 02 used for `ToolResult` — two definitions would drift, and a tool validating a
different shape than the backend parses fails as silent misparsing.

It stays consistent with chunk 02's rule that `statlab_contracts` holds "wire types only, no
logic": a Pydantic model with validators is types and validation, not logic. Still no IO, no
pandas, no HTTP in that package.

Record the deviation from §3.2 in your implementation notes.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `contracts/statlab_contracts/vocabulary.py` |
| create | `contracts/statlab_contracts/contract.py` |
| create | `tests/test_contract_vocabulary.py` |
| create | `tests/test_contract_models.py` |
| create | `tests/test_contract_consistency.py` |
| create | `tests/fixtures/contracts/minimal.json` |
| create | `tests/fixtures/contracts/full.json` |
| **modify** | `tests/conftest.py` — add two contract fixtures (§4.1) |

No changes to `pyproject.toml` (the `contracts` package is already registered). No changes to
`backend/`. If you find yourself editing `backend/`, stop and re-read the scope boundary.

---

## 3. Implementation Spec

### 3.1 Two decisions to apply throughout

**`extra="forbid"` on every contract model.** A user-supplied file with `"targert"` instead of
`"target"` must fail loudly. Silently ignoring an unknown key would mean the agent infers a target
while the user believes they specified one — the single worst failure mode available to this
schema, because the run then succeeds and reports a healthy metric for the wrong question.

**A validator fires only when every field it relates is present.** Every field is optional except
`target.column` (D8), and absent means *"the agent will infer this"*, not *"invalid"*. So
`_task_matches_target_type` must pass when `objective.task` is `None`, and only compare when both
sides are set. Getting this backwards makes the minimal contract unconstructible, which is the
one contract that must always work.

### 3.2 `contracts/statlab_contracts/vocabulary.py`

The closed vocabularies. Separated from `contract.py` because chunk 04's `CandidateSpec` reuses
several of them — a candidate chooses *an* encoding, the contract states the *default* encoding —
and chunk 14's scoring tools must conform to the metric names.

```python
"""Closed vocabularies shared by the contract (chunk 03) and candidate specs (chunk 04)."""

from typing import Literal, get_args

TargetType = Literal["binary", "multiclass", "continuous", "count"]
Task = Literal["classification", "regression"]
ColumnRole = Literal["numeric", "categorical", "ordinal", "datetime", "boolean", "text"]

SplitStrategy = Literal["random", "stratified", "temporal", "grouped"]

ImputationStrategy = Literal["mean", "median", "most_frequent", "constant", "drop"]
OutlierPolicy = Literal["none", "clip_iqr", "winsorize", "flag_only"]
ScalingMethod = Literal["none", "standard", "minmax", "robust"]
CategoricalEncoding = Literal["onehot", "ordinal", "target", "frequency"]

ClassificationMetric = Literal[
    "accuracy", "balanced_accuracy", "precision", "recall", "f1",
    "roc_auc", "pr_auc", "brier", "log_loss",
]
RegressionMetric = Literal["rmse", "mae", "r2", "mape", "medae"]
Metric = ClassificationMetric | RegressionMetric

TuningMethod = Literal["none", "grid", "random"]
ClassWeight = Literal["none", "balanced"]

TASK_FOR_TARGET_TYPE: dict[TargetType, Task] = {
    "binary": "classification",
    "multiclass": "classification",
    "continuous": "regression",
    "count": "regression",
}
"""Single source of truth for the target-type ↔ task relationship.

`task` is therefore *derivable* whenever `target.type` is known. It is still carried in the
contract because a user may state the task without stating the target type, and node 2 records
its own reasoning — but the two must never disagree, which is what `_task_matches_target_type`
enforces. This is the "derive, don't duplicate" lesson applied where the duplication cannot be
removed: the check exists precisely because both may legitimately be absent.
"""

METRICS_FOR_TASK: dict[Task, frozenset[str]] = {
    "classification": frozenset(get_args(ClassificationMetric)),
    "regression": frozenset(get_args(RegressionMetric)),
}
"""Derived from the Literals via `get_args`, never hand-listed — adding a metric to a Literal
without adding it here is impossible by construction."""
```

Required behaviour:

| Check | Expectation |
|---|---|
| `TASK_FOR_TARGET_TYPE` covers every `TargetType` | exhaustive, verified via `get_args` |
| `METRICS_FOR_TASK` covers every `Task` | exhaustive, verified via `get_args` |
| the two metric sets are disjoint | no metric name means different things per task |
| `Metric` equals the union of both sets | verified via `get_args` |

### 3.3 `contracts/statlab_contracts/contract.py` — the ten models

All models: `ConfigDict(frozen=True, extra="forbid")`. All collections are `tuple` or `dict`.

```python
class DatasetOptions(BaseModel):
    """CSV parsing options. Node 1 copies these into `load_csv` verbatim (PLAN.md §4.1)."""
    delimiter: str = Field(default=",", min_length=1, max_length=4)
    encoding: str = Field(default="utf-8", min_length=1)
    decimal: str = Field(default=".", min_length=1, max_length=1)
    na_values: tuple[str, ...] = ("", "NA", "N/A", "null", "NULL")
    header_row: int = Field(default=0, ge=0)


class TargetSpec(BaseModel):
    """The one place the target is declared. `column` is the only required field in the
    entire contract (D8)."""
    column: str = Field(min_length=1)
    type: TargetType | None = None
    positive_class: str | int | bool | None = None
    class_order: tuple[str, ...] | None = None


class FeatureRules(BaseModel):
    include: tuple[str, ...] | None = None      # None = everything not excluded
    exclude: tuple[str, ...] = ()
    id_columns: tuple[str, ...] = ()
    roles: dict[str, ColumnRole] = Field(default_factory=dict)
    ordinal_levels: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class InferenceGoal(BaseModel):
    """One question the D7/D31 inferential track must answer."""
    question: str = Field(min_length=1)
    predictors: tuple[str, ...] = Field(min_length=1)
    alpha: float = Field(default=0.05, gt=0.0, le=0.5)


class Objective(BaseModel):
    task: Task | None = None
    primary_metric: Metric | None = None
    secondary_metrics: tuple[Metric, ...] = ()
    success_threshold: float | None = None
    inference_goals: tuple[InferenceGoal, ...] = ()


class SplitRules(BaseModel):
    strategy: SplitStrategy | None = None
    test_size: float = Field(default=0.2, gt=0.0, lt=1.0)
    valid_size: float = Field(default=0.2, ge=0.0, lt=1.0)
    time_column: str | None = None
    group_column: str | None = None
    seed: int = Field(default=42, ge=0)


class MissingPolicy(BaseModel):
    """Restructured from PLAN.md §4.1's illustrative JSON, which used one dict mixing a
    `default` key, per-column keys and a `drop_rows_above` key. That shape requires the
    implicit convention "any key that is not one of two reserved names is a column name" —
    the same class of implicit string convention the plan rejected elsewhere. Three explicit
    fields carry the same information with no convention to remember."""
    default: ImputationStrategy = "median"
    per_column: dict[str, ImputationStrategy] = Field(default_factory=dict)
    drop_rows_above: float | None = Field(default=None, gt=0.0, le=1.0)


class PreprocessingRules(BaseModel):
    missing: MissingPolicy = Field(default_factory=MissingPolicy)
    outliers: OutlierPolicy = "none"
    scaling: ScalingMethod = "standard"
    categorical_encoding: CategoricalEncoding = "onehot"
    max_cardinality: int = Field(default=30, ge=2)
    drop_constant: bool = True
    drop_duplicates: bool = True


class Tuning(BaseModel):
    method: TuningMethod = "grid"
    cv_folds: int = Field(default=5, ge=2, le=20)
    max_fit_seconds: int = Field(default=300, gt=0)


class ModellingRules(BaseModel):
    candidates: tuple[str, ...] = ()
    """Estimator names. **Not validated here** — the registry lives in chunk 13, and the
    available estimators depend on the running MCP server, not on this schema. Chunk 27's
    intake node validates these against `list_estimators`. Carrying a Literal here would
    make the contract the source of truth for the model registry, which is wrong."""
    inferential_model: str | None = None    # same reasoning; chunk 15 owns the registry
    tuning: Tuning = Field(default_factory=Tuning)
    class_weight: ClassWeight = "none"
    calibrate: bool = False


class Constraints(BaseModel):
    max_runtime_seconds: int = Field(default=1800, gt=0)
    max_retune_loops: int = Field(default=2, ge=0)
    max_candidates: int = Field(default=8, ge=1, le=64)
    max_search_waves: int = Field(default=2, ge=1, le=10)
    max_total_fit_seconds: int = Field(default=1200, gt=0)
    random_seed: int = Field(default=42, ge=0)
    interpretability_required: bool = False


class DataContract(BaseModel):
    version: Literal[1] = 1
    target: TargetSpec                      # the only required member
    dataset: DatasetOptions = Field(default_factory=DatasetOptions)
    features: FeatureRules = Field(default_factory=FeatureRules)
    objective: Objective = Field(default_factory=Objective)
    split: SplitRules = Field(default_factory=SplitRules)
    preprocessing: PreprocessingRules = Field(default_factory=PreprocessingRules)
    modelling: ModellingRules = Field(default_factory=ModellingRules)
    constraints: Constraints = Field(default_factory=Constraints)
    notes: str = ""
```

**Naming note.** These are `*Rules`/`*Options`/`*Spec`, deliberately avoiding `FeatureSpec`,
`PreprocessingSpec` and `EstimatorSpec` — chunk 04 uses those names for a *candidate's* choices.
A contract states rules; a candidate states a spec. Do not rename either side to match.

### 3.4 Intra-model validators

| # | Model | Rule | Fires when |
|---|---|---|---|
| V1 | `TargetSpec` | `positive_class` requires `type == "binary"` | `positive_class` is set and `type` is set |
| V2 | `TargetSpec` | `class_order` requires `type == "multiclass"` | both set |
| V3 | `TargetSpec` | `class_order` entries unique and non-empty | `class_order` set |
| V4 | `FeatureRules` | `include` and `exclude` are disjoint | `include` set |
| V5 | `FeatureRules` | `id_columns` disjoint from `include` | `include` set |
| V6 | `FeatureRules` | every `ordinal_levels` key has `roles[key] == "ordinal"` | `ordinal_levels` non-empty **and** the key appears in `roles`; a key absent from `roles` is allowed (the role may be inferred) |
| V7 | `FeatureRules` | each `ordinal_levels` value has ≥2 unique entries | per key |
| V8 | `Objective` | `primary_metric` not repeated in `secondary_metrics` | both set |
| V9 | `Objective` | `secondary_metrics` entries unique | always |
| V10 | `SplitRules` | `test_size + valid_size < 0.9` | always |
| V11 | `SplitRules` | `time_column` set iff `strategy == "temporal"` | `strategy` set |
| V12 | `SplitRules` | `group_column` set iff `strategy == "grouped"` | `strategy` set |
| V13 | `ModellingRules` | `candidates` entries unique | `candidates` non-empty |
| V14 | `MissingPolicy` | `per_column` values are valid strategies | by field type |

V11/V12 are **iff**, not one-way: a `time_column` with `strategy="random"` is a contradiction the
user should hear about, not a harmlessly ignored field.

### 3.5 Cross-group validators on `DataContract`

These are the reason this chunk exists — the per-model rules are mostly field constraints.

| # | Rule | Message must name |
|---|---|---|
| X1 | `objective.task` agrees with `TASK_FOR_TARGET_TYPE[target.type]` | both values |
| X2 | `objective.primary_metric ∈ METRICS_FOR_TASK[task]`, where `task` is `objective.task` or derived from `target.type` | the metric and the task |
| X3 | every `objective.secondary_metrics` entry is in the same task's metric set | the offending metric |
| X4 | `split.strategy == "stratified"` requires `target.type ∈ {binary, multiclass}` | the target type, and that stratifying a continuous target is a type error not a strategy |
| X5 | `target.column` appears in none of `features.include`, `features.exclude`, `features.id_columns` | which one it appeared in |
| X6 | `target.column` is not a key in `features.roles` | that the target's nature is declared by `target.type` |
| X7 | `split.time_column != target.column` | — |
| X8 | `split.group_column != target.column` | — |
| X9 | `target.column` is not a key in `preprocessing.missing.per_column` | — |
| X10 | `modelling.class_weight == "balanced"` requires a classification task | the resolved task |
| X11 | `modelling.calibrate` requires a classification task | the resolved task |
| X12 | no `inference_goals[].predictors` entry equals `target.column` | the goal's question |
| X13 | every `inference_goals[].predictors` entry is not in `features.exclude` | the column and the goal |
| X14 | `features.include`, if set, contains every `inference_goals[].predictors` entry | the missing column |

Implement X2 with a single helper:

```python
def _resolved_task(contract: DataContract) -> Task | None:
    """`objective.task` if set, else derived from `target.type`, else None.

    Used by X2, X3, X10 and X11 so "what task is this?" is answered in one place.
    """
```

X14 is the subtle one: if the user restricted features to a list and then asked an inference
question about a column not in it, one of the two statements is wrong and the run cannot satisfy
both. Failing at contract load is far kinder than discovering it at node 6.

### 3.6 The minimum contract

This must construct, and it is worth an explicit named test:

```python
DataContract.model_validate({"target": {"column": "churned"}})
```

Everything else defaults. Assert the defaults are exactly the values in §3.3 — `split.test_size
== 0.2`, `preprocessing.scaling == "standard"`, `constraints.max_candidates == 8`, and so on. A
default silently changing is a behaviour change to every run that omits the field.

---

## 4. Test Suite Spec

Target: **~125 new tests**, total ≥ **373**.

### 4.1 `tests/conftest.py` additions

```python
@pytest.fixture
def minimal_contract() -> DataContract:
    """The D8 minimum: target.column and nothing else."""

@pytest.fixture
def full_contract() -> DataContract:
    """Every field populated with a mutually consistent set of values.

    Used as the base for cross-group tests: copy it with one field changed via
    `model_copy(update=...)` — or better, `model_validate(full_dict | {...})` — so a failure
    names exactly one deviation.
    """
```

Both load from `tests/fixtures/contracts/*.json` rather than being built in Python, so the same
JSON exercises the real parse path and doubles as an example file. `full.json` must satisfy every
rule in §3.4–§3.5; if it does not, the fixtures are wrong, not the validators.

### 4.2 `tests/test_contract_vocabulary.py` (~10)

| # | Test | Assertion |
|---|---|---|
| B1 | `TASK_FOR_TARGET_TYPE` exhaustive | keys == `set(get_args(TargetType))` |
| B2 | no extra keys | equality, not just containment |
| B3 | `METRICS_FOR_TASK` exhaustive over `Task` | via `get_args` |
| B4 | classification and regression metric sets are disjoint | `isdisjoint` |
| B5 | `Metric` == union of both | via `get_args` |
| B6 | every `ClassificationMetric` maps to classification | membership |
| B7 | every `RegressionMetric` maps to regression | membership |
| B8 | binary and multiclass → classification | explicit |
| B9 | continuous and count → regression | explicit |
| B10 | vocabularies contain no duplicates | each `get_args` tuple has unique members |

### 4.3 `tests/test_contract_models.py` (~55)

**Defaults and construction** — one test per model asserting every default from §3.3 (10 tests),
plus:

| # | Test | Expect |
|---|---|---|
| C1 | minimal contract constructs | §3.6 |
| C2 | `full.json` constructs | no error |
| C3 | round-trip | `model_validate_json(model_dump_json())` equals original, for both fixtures |
| C4 | `version` defaults to 1 | — |
| C5 | `version: 2` | `ValidationError` — a future contract must fail loudly, not be reinterpreted |
| C6 | missing `target` | `ValidationError` naming `target` |
| C7 | `target` without `column` | `ValidationError` naming `column` |
| C8 | `target.column = ""` | `ValidationError` |
| C9 | unknown top-level key | `ValidationError` (`extra_forbidden`) |
| C10 | unknown nested key in each of the 9 groups | parametrised, `ValidationError` each |
| C11 | every model frozen | parametrised over all 10, mutation raises |
| C12 | collections are tuples not lists | `na_values`, `exclude`, `id_columns`, `secondary_metrics`, `candidates`, `class_order`, `predictors` |
| C13 | list input coerces to tuple | via `model_validate` |

**Field bounds** — parametrised, each valid at the boundary and invalid just outside:

| Field | Valid | Invalid |
|---|---|---|
| `header_row` | 0, 1 | −1 |
| `decimal` | `"."`, `","` | `""`, `".."` |
| `delimiter` | `","`, `"\t"` | `""` |
| `test_size` | 0.01, 0.99 | 0.0, 1.0 |
| `valid_size` | 0.0, 0.99 | −0.1, 1.0 |
| `seed` / `random_seed` | 0 | −1 |
| `alpha` | 0.001, 0.5 | 0.0, 0.51 |
| `max_cardinality` | 2 | 1 |
| `cv_folds` | 2, 20 | 1, 21 |
| `max_fit_seconds` | 1 | 0 |
| `max_candidates` | 1, 64 | 0, 65 |
| `max_search_waves` | 1, 10 | 0, 11 |
| `max_retune_loops` | 0 | −1 |
| `drop_rows_above` | 0.01, 1.0 | 0.0, 1.01 |
| `predictors` | 1 entry | `()` |

**Invalid enum values** — parametrised over all nine vocabularies, one bad value each, via
`model_validate`. Assert the error message lists the permitted values, so a user can fix it.

### 4.4 `tests/test_contract_consistency.py` (~60)

One **pass** and one **fail** test per rule V1–V14 and X1–X14 (56 tests), plus:

| # | Test | Expect |
|---|---|---|
| D1 | absent fields do not trigger cross-group rules | the minimal contract passes all of X1–X14 |
| D2 | `objective.task` alone, no `target.type` | valid — X1 does not fire |
| D3 | `target.type` alone, no `objective.task` | valid, and X2 still constrains the metric via the derived task |
| D4 | `_resolved_task` prefers explicit over derived | when both present and equal |
| D5 | `_resolved_task` returns `None` when neither is set | — |
| D6 | one deviation → one error | change a single field in `full.json`; assert exactly one validation error, so messages stay diagnosable |

Every failure test asserts on the **message content**, not just the exception type. A rule whose
message does not name the offending field or value is not done — this schema's whole job is
telling a user what to fix. Use `pytest.raises(ValidationError, match=...)`.

Fail-test construction: start from `full_contract`'s dict, change exactly one thing, call
`DataContract.model_validate`. Do not hand-write a fresh minimal dict per test — a fail test that
fails for an unrelated reason is a false pass.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **373** (248 existing + ~125 new).
- [ ] Coverage **100 %** line and branch on `vocabulary.py` and `contract.py`; project stays 100 %.
- [ ] Every rule V1–V14 and X1–X14 has a passing case and a failing case, and each failing case
      asserts on message content.
- [ ] The minimal contract test exists and asserts every default value from §3.3.
- [ ] `full.json` and `minimal.json` both parse, both round-trip, and `full.json` satisfies all 28
      rules.

**Static analysis**
- [ ] `make typecheck` exits 0; `make lint` exits 0.
- [ ] No `Any`, no `# type: ignore[arg-type]`, no `pragma: no cover`.
- [ ] No negative test uses kwargs; all use `model_validate`.

**Boundary**
- [ ] `contracts/` still imports nothing but stdlib and `pydantic` — in particular **no pandas**.
      The existing `test_contracts_*` isolation tests must cover the two new modules.
- [ ] Nothing in `backend/` changed.

**Design checks**
- [ ] `TASK_FOR_TARGET_TYPE` and `METRICS_FOR_TASK` are derived via `get_args`, not hand-listed.
- [ ] `_resolved_task` is the only place the "which task is this" question is answered.
- [ ] Estimator and inferential-model names are **strings**, not Literals, with the deferral to
      chunk 27 documented in the field docstring.

**Housekeeping**
- [ ] Frontmatter → `completed`, with notes covering: the `contracts/` placement deviation from
      `PLAN.md` §3.2; the `MissingPolicy` restructure; anything the validator-ordering surprised
      you with (pydantic runs field validation before model validators, so a model validator can
      assume field types but not cross-field coherence).
- [ ] `plan/00_index.md` chunk 03 row → `completed`.
- [ ] Update `plan/PLAN.md` §4.1's `missing_policy` example to the three-field
      `MissingPolicy` shape, so the plan and the schema agree.

### Explicitly out of scope

No validation against a dataframe — that is `validate_contract` in chunk 08. No `CandidateSpec`
(chunk 04). No contract *elicitation* or proposal logic (chunks 27/33). No file loading helper:
callers use `DataContract.model_validate_json(path.read_text())`, and wrapping that in a function
adds a name without adding behaviour.

### Stop and ask if

- A rule in §3.4/§3.5 contradicts `PLAN.md` §4.1 — the plan wins, and I want to know.
- Two rules make some field combination unsatisfiable that a real user would reasonably write.
- `full.json` cannot be made to satisfy all 28 rules simultaneously — that would mean the rule set
  is over-constrained, which is a design problem, not a fixture problem.
