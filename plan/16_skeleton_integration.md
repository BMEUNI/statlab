---
status: completed
dependencies: [08, 09, 10, 11, 12, 13, 14, 15]
completion_date: 2026-10-02
implementation_notes: >
  M6 passed. The 33-tool walking skeleton runs end to end over a real MCP session, zero LLM, on
  both fixtures, and reproduces every §1 number to the printed precision (classification: valid
  roc_auc logistic 0.8125 [0.720, 0.888], RF 0.722, HGB 0.716; champion logistic test 0.730;
  n_obs 320; planted tenure -0.0547, plan premium -1.96, plan standard -0.705, all inside §1's
  CIs; plan goal df 2 reject, region goal df 3 p 0.510 not rejected. Regression: valid rmse
  linear 15.42 [13.01, 17.88], HGB 17.08, RF 17.52; champion linear test 13.64, r2 0.949;
  n_obs 240; planted centre +37.7 ∈ [33.32, 42.09], rural -25.9, rooms +8.24, all inside §1's
  CIs; neighbourhood goal df 2 reject, condition goal df 2 p 0.975 not rejected).
  Gates: 1913 passed, 1 skipped; 100% line and branch (4454 stmts, 1128 branches, 0 missed);
  mypy --strict clean on 91 files; ruff check + format clean; filterwarnings=error, zero new
  suppressions, zero Any, zero pragma: no cover. test_skeleton.py runs in ~8s (classification
  4.29s, regression 1.06s); suite ≈ 44 s. All six §5 DoD mutations caught.
  ONE DEFECT FOUND BY THE GATE, fixed at root cause: §3.3's `describe_artifact` on the comparison
  *table* was blocked -- describe_artifact only supported frame kinds (dataframe/predictions) and
  raised ToolDataError on `table`, pinned by `test_describe_artifact_table_kind_raises_not_yet_
  supported`. The tool's own docstring/test framed this as a temporary gap ("today", "does not
  yet support"), so it was fixed in mcp_server/ (not a D-decision, not backend/contracts): a
  minimal `_describe_json` summarises a JSON table/curve by top-level keys and list lengths. The
  pinned test was rewritten to assert the new behaviour (revert -> fail -> restore proven: the
  three new JSON tests fail on pre-fix production). No allow_in widened, no check_lineage relaxed,
  no tool body called directly, no step skipped.
---

# Chunk 16 — Walking-skeleton gate (M6)

**Goal.** Drive the 33 `[v1]` tools (`PLAN.md` §6.4) end to end over a **real MCP session**,
with zero LLM, on a classification fixture and a regression fixture, three estimators each. Each
run must produce real metrics with CIs, a ranking, a champion scored on test exactly once, and one
coefficient table that recovers planted effects.

This chunk adds **no production module**. It adds one test module that plays the orchestrator.
The gate's value is what it finds: if a tool's contract, `allow_in` or the D32 lineage rule makes
the spine impossible as written, this is where it surfaces. Treat a failure here as a finding,
not as something to work around in the test.

---

## 1. Prerequisites & Context

### State

Committed; see `git log -1`. 1898 tests (1897 passed, 1 skipped by design), 100 % line and
branch coverage, mypy clean on 90 files, suite ≈ 40 s.

Read first: `tests/test_server_mcp_session.py` (how a session calls a tool and validates the
envelope with `TOOL_RESULT_ADAPTER`), the `live_server`/`mcp_session`/`run_paths_over_mcp`/
`seeded_run` fixtures in `tests/conftest.py`, and the summaries of `split_*`,
`assemble_inference_matrix`, `coefficient_table`, `compare_models`, `rank_models` and
`list_estimators`.

### Findings from probing (direct tool-body calls, 2026-10-02)

The whole sequence in §3.3 was run by calling tool bodies directly on the two fixtures in §3.1,
with `BOOTSTRAP_RESAMPLES = 200`, split seed 42 and `random_seed` 42. It ran in about 6 s for
both tasks. The wire run must reproduce these numbers (same seeds, same code):

| | Classification (`subscribers`) | Regression (`houses`) |
|---|---|---|
| Valid primary metric | `roc_auc`: logistic 0.812 [0.720, 0.888], RF 0.722, HGB 0.716 | `rmse`: linear 15.42 [13.01, 17.88], HGB 17.08, RF 17.52 |
| `top_two_distinguishable` | false | false |
| Champion, test score | logistic_regression, 0.730 | linear_regression, 13.64 |
| Inference `n_obs` | 320 (= train + valid) | 240 (= train + valid) |
| Reference levels | `plan`: basic, `region`: east | `neighbourhood`: suburb, `condition`: good |
| Missing indicators | `age__missing`, `region__missing` | `rooms__missing` |
| Planted goal | plan: df 2, p ≈ 0, reject | neighbourhood: df 2, p ≈ 0, reject |
| Null goal | region: df 3, p 0.510, not rejected | condition: df 2, p 0.975, not rejected |
| Planted coefficients, 95 % CI | tenure −0.05 ∈ [−0.070, −0.040]; plan premium −1.9 ∈ [−2.772, −1.150]; plan standard −0.9 ∈ [−1.307, −0.102] | centre +40 ∈ [33.32, 42.09]; rural −25 ∈ [−30.65, −21.21]; rooms +8 ∈ [7.12, 9.35] |

`area_m2`'s CI is [1.120, 1.200] against a true 1.2: on the edge, so it is **not** asserted.

### Decisions this chunk makes

**D-a. Over the wire, not by calling bodies.** Every call goes through `mcp_session()`, and every
result goes through `TOOL_RESULT_ADAPTER`, which is the parse point chunk 21 will use. This
proves the JSON shapes: a goal list of objects, the `candidates` and `estimators` dicts, tuples
as lists, an integer `positive_class`, refs as strings, the heavy semaphore, and run resolution
by `project`/`run`. A direct call would skip pydantic coercion; the probe above hit exactly that
when it passed plain dicts as goals.

**D-b. The test plays the orchestrator, minimally.** It does what the backend will do at nodes 1
and 2: write the CSV to `ProjectPaths.datasets/<sha256>.csv` and write `contract.snapshot.json`.
Then it calls tools node by node.
- It **injects** what chunk 21 will inject, keyed on each tool's input schema from `tools/list`:
  `project` and `run` when present, `dataset` for `load_csv`, and `seed`. Split tools get
  `split.seed`; every other tool gets `constraints.random_seed`.
- Every other argument comes from the contract (`features.id_columns`,
  `preprocessing.missing.default`, `preprocessing.max_cardinality`, `preprocessing.scaling`,
  `objective.primary_metric`, `objective.inference_goals`, `modelling.candidates`,
  `modelling.inferential_model`) or from an earlier result (refs, `top_eligible`).
- The inferential fit tool is looked up through `list_estimators`' `inferential_models[name].tool`,
  not hard-coded.
- It is **test scaffolding**, not a client library. Chunk 21 builds the real client, and its
  acceptance re-drives this skeleton through it (§2 housekeeping note).

**D-c. The driver enforces chunk 21's two rules on itself, before each call.** Both rules are
pure functions in the test module, so they can be unit-tested without a server.
- `check_allowed(node, tool, allow_in)`: the node must be in the tool's `meta.allow_in` from
  `tools/list`, or `allow_in` must be `["*"]`.
- `check_lineage(node, tool, tainted_params)`: D32 as written in `PLAN.md`. A ref is
  **test-derived** if it is a split output with `meta.split == "test"`, or the output of a call
  that received a test-derived input. The rules for a call that receives one:
  - a `fit_*` tool, or a parameter named `train`, is a violation in any node;
  - before `champion_selection`, only `verify_split` may receive one;
  - in `champion_selection`, only `apply_*`, `predict`, `predict_proba` and `score_*` may, and
    the driver counts `score_*` calls with a test-derived input. The count must end at exactly 1.

  Input refs are found by walking the argument values (including dict values such as
  `candidates`) for strings that are refs this run produced.

**D-d. Two seeded fixtures with planted effects, defined in the test module (§3.1).** Each has
an identifier column, an integer column with nulls (it loads as `float64`: the integral-float
class of bug from B2's review), a categorical with nulls (most-frequent fallback in the imputer,
and a missing indicator in the inference matrix), a planted categorical effect, and a pure-noise
categorical. The fixtures live in the test module, not `conftest.py`, until a second module
needs them.

**D-e. One predictive matrix per task.** Impute, then one-hot, then scale with the contract's
`standard` method. All three estimators train on it. Scaling is harmless for trees, and D7's
other matrix is the unscaled inference matrix built at node 6. The champion's pipeline is
therefore always imputer → encoder → scaler, which node 12 replays on test.

**D-f. Bootstrap resamples patched to 200.** `monkeypatch` sets
`statlab_tools.tools.evaluation.BOOTSTRAP_RESAMPLES = 200`. The live server is a uvicorn thread
in the test process, so the patch reaches it. Point estimates do not depend on it, and chunk 14's
`test_real_1000_resamples_on_48_rows` already covers the production constant. Budget: the module
runs in under 25 s; the suite stays under 70 s.

**D-g. The `[v1]` set is corrected.** `assemble_inference_matrix` replaces `build_dummy_matrix`
in the 33. It is node 6's matrix tool (D31: train + valid, imputation, indicators); it writes the
manifest `coefficient_table` needs; and `build_dummy_matrix` is not on the spine. This was
applied to `PLAN.md` §6.3 with this spec. The test module holds the 33 names as
`SKELETON_TOOLS`.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `tests/test_skeleton.py`: fixtures, driver, rule checks, the two gate runs, guard tests |
| modify | production code **only** to fix a defect the gate finds. Each fix comes with a regression test in that tool's own test file, proven by revert → fail → restore |
| modify | this file's frontmatter; `00_index.md` row 16 → completed, B3 and M6 ✅; `WHATS_NEXT.md` state table (measured), "Next" → batch B4 (chunks 17 + 18), a "Batch B3 — completed (summary)" section, and the gate's finding count. Add to `00_index.md` row 21: "acceptance: `tests/test_skeleton.py` re-driven through `mcp_client`, with the driver's injection and rule checks deleted in favour of the client's" |

No `backend/` or `contracts/` change.

---

## 3. Implementation Spec

### 3.1 Fixtures (verbatim; the numbers in §1 depend on the exact call order)

```python
def subscribers() -> pd.DataFrame:
    rng = np.random.default_rng(20261002); n = 400
    age = rng.integers(18, 80, n).astype(float); tenure = rng.integers(1, 72, n)
    plan = rng.choice(["basic", "standard", "premium"], n, p=[.5, .3, .2])
    region = rng.choice(["north", "south", "east", "west"], n).astype(object)
    effect = pd.Series(plan).map({"basic": 0.9, "standard": 0.0, "premium": -1.0}).to_numpy()
    lin = -0.6 - 0.05 * (tenure - 36) + effect + 0.02 * (age - 48)
    y = (rng.random(n) < 1 / (1 + np.exp(-lin))).astype(int)
    age[rng.random(n) < 0.10] = np.nan; region[rng.random(n) < 0.06] = None
    return pd.DataFrame({"subscriber_id": np.arange(1, n + 1), "age": age,
                         "tenure_months": tenure, "plan": plan, "region": region, "churned": y})

def houses() -> pd.DataFrame:
    rng = np.random.default_rng(20261003); n = 300
    area = rng.uniform(40, 200, n).round(1); rooms = rng.integers(1, 7, n).astype(float)
    nb = rng.choice(["centre", "suburb", "rural"], n, p=[.3, .5, .2])
    cond = rng.choice(["good", "fair", "poor"], n)
    effect = pd.Series(nb).map({"centre": 40, "suburb": 0, "rural": -25}).to_numpy()
    price = 50 + 1.2 * area + 8 * rooms + effect + rng.normal(0, 15, n)
    rooms[rng.random(n) < 0.08] = np.nan
    return pd.DataFrame({"house_id": np.arange(1, n + 1), "area_m2": area, "rooms": rooms,
                         "neighbourhood": nb, "condition": cond, "price": price.round(2)})
```

The CSV bytes are `frame.to_csv(index=False).encode()`. Format these to the project's style;
do not change the arithmetic or the order of `rng` calls.

**Contracts:**
- `subscribers`:
  - target `churned`, `binary`, `positive_class: 1`;
  - `features.id_columns: ["subscriber_id"]`;
  - objective: task `classification`, primary metric `roc_auc`, goals "Does plan affect churn?"
    `[plan]` and "Does region affect churn?" `[region]`;
  - `split.strategy: stratified`;
  - `modelling.candidates`: the three D25 classifiers; `modelling.inferential_model: logit`.
- `houses`: the same shape, with target `price` (`continuous`), id `house_id`, task
  `regression`, primary metric `rmse`, goals `[neighbourhood]` and `[condition]`, strategy
  `random`, the three D25 regressors, and `ols`.

Everything else is left at contract defaults.

### 3.2 The driver

A small dataclass holding:
- the session;
- `run`;
- `schemas` and `allow_in` per tool (read once from `tools/list`);
- `produced: set[str]` and `test_derived: set[str]`;
- `called: list[tuple[str, str]]` of `(node, tool)` pairs;
- `test_scores: int`.

It has one method, `async call(node, tool, **arguments) -> ToolSuccess`, which:
1. runs `check_allowed`;
2. injects the parameters from D-b;
3. finds the test-derived input params and runs `check_lineage`;
4. calls the tool and validates the envelope, asserting `ok` and including the failure message
   in the assertion text;
5. records the produced refs, and marks them test-derived when the inputs were test-derived or
   when `meta.split == "test"`.

Split outputs are told apart by `ArtifactMeta.split`, never by position. Inference outputs are
told apart by `label`.

No `Any`, mypy `--strict`. Narrow `JsonValue` summaries with `isinstance` or `cast` at the point
of use.

### 3.3 The spine, per task

| Node | Calls |
|---|---|
| `intake` | `load_csv` → `infer_column_roles`, `validate_contract` on `raw` |
| `data_profiling` | `profile_schema`, `summarize_numeric`, `summarize_categorical`, `report_missingness`, `summarize_target` on `raw` |
| `data_splitting` | `drop_columns(raw, id_columns)` → `split_stratified` / `split_random` → `verify_split(train, valid, test)` |
| `experiment_design` | `list_estimators(task)`: every `modelling.candidates` name is listed; the inferential tool comes from `inferential_models` |
| `inferential_analysis` | `assemble_inference_matrix(train, valid, predictors = all non-id features)` → `fit_logit` / `fit_ols` → `coefficient_table(fit, manifest, goals)` |
| `preprocessing` | `fit_imputer(train)` → `apply_imputer` on train and valid |
| `feature_engineering` | `fit_onehot_encoder(train)` → apply on train and valid; `fit_scaler(train)` → apply on train and valid |
| `model_training` | `train_estimator` × 3 |
| `model_evaluation` | per candidate: `predict_proba` / `predict` on valid → `score_*`; plus `compute_confusion_matrix` (classification). Then `compare_models(candidates, primary_metric)` |
| `champion_selection` | `rank_models(comparison, estimators)` → for `top_eligible`: `apply_imputer`, `apply_onehot_encoder`, `apply_scaler` on test → `predict_proba` / `predict` → **one** `score_*` |
| `artifact_persistence` | `list_artifacts`; `describe_artifact` on the comparison table and on the transformed test frame |

`problem_definition` makes no tool call: the contract is the problem definition.

### 3.4 Gate assertions (each task)

- **G1.** Every call is `ok`, and every envelope validates.
- **G2.** No `check_allowed` or `check_lineage` violation, and `test_scores == 1` at the end.
- **G3.** The set of tools called equals `SKELETON_TOOLS` minus the other task's task-specific
  tools:
  - classification only: `split_stratified`, `predict_proba`, `score_classification`,
    `compute_confusion_matrix`, `fit_logit`;
  - regression only: `split_random`, `predict`, `score_regression`, `fit_ols`.

  Every name in `SKELETON_TOOLS` appears in `tools/list`, and the two runs together cover all 33.
- **G4. Real metrics.**
  - Every candidate's valid primary metric is finite and lies inside its own CI.
  - Classification: every candidate's valid `roc_auc` ≥ 0.65, and the champion's test `roc_auc`
    ≥ 0.65.
  - Regression: every candidate's valid `r2` ≥ 0.85, and the champion's test `r2` ≥ 0.85.
- **G5. Ranking.**
  - `compare_models` returns three rows, best first in the metric's own direction. The test
    states the direction itself (`roc_auc` higher is better, `rmse` lower) and does not read it
    from the tool.
  - `rank_models`' `top_eligible` is the rank-1 candidate, and a `selection_note` is present.
- **G6. Inference.**
  - `n_obs` equals the train row count plus the valid row count, read from the split summary or
    `ArtifactMeta.rows`. It never includes test.
  - The planted goal has df = number of levels − 1, and is rejected.
  - The null goal is not rejected.
  - With two goals, `p_holm` ≥ `p_raw` for each.
  - Each planted coefficient in §1's table lies inside its reported 95 % CI. Read the terms from
    the coefficient-table artifact via `describe_artifact`, or from its JSON file.
- **G7.** Every ref produced by any call appears in `list_artifacts`.

### 3.5 Guard tests (no server)

- `check_allowed` rejects `fit_scaler` in `preprocessing`, and accepts `list_artifacts` in any
  node.
- `check_lineage` rejects each of:
  - a test-derived input to `apply_imputer` in `preprocessing`;
  - a test-derived input to any `fit_*` in `champion_selection`;
  - a test-derived `train` parameter;
  - a test-derived input to `train_estimator` in `champion_selection`;
  - a second test-derived `score_*`.

  It accepts `verify_split` in `data_splitting`, and `apply_scaler`, `predict_proba` and one
  `score_classification` in `champion_selection`.

---

## 4. Test Suite Spec (~10)

- `test_skeleton_classification_end_to_end` and `test_skeleton_regression_end_to_end`: async,
  one live session each, G1–G7.
- `test_skeleton_tools_cover_the_33`: the union of the two task sets is `SKELETON_TOOLS`, and
  the task-specific sets are disjoint. This one is pure and needs no server.
- The §3.5 guard tests, parametrized where natural.

---

## 5. Definition of Done

- [ ] `make test` exits 0, `tests/test_skeleton.py` runs in under 25 s, and the suite stays
      under 70 s. Coverage stays at **100 %** line and branch.
- [ ] The wire run reproduces §1's numbers, to the printed precision.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - in the test's spine, apply the imputer to test in `preprocessing` instead of
    `champion_selection` (lineage guard);
  - score the champion on test twice (exactly-once);
  - call `fit_scaler` in `preprocessing` (allow-list);
  - production: `predict_proba` writes the columns in reversed class order (real-metrics floor);
  - production: `assemble_inference_matrix` ignores `valid` (`n_obs`);
  - production: `compare_models` sorts `roc_auc` ascending (ranking direction).
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

- `allow_in` or the D32 rule makes a step of §3.3 impossible. Report the step and the rule; do
  not widen `allow_in` or relax the check.
- A fix needs `backend/`, `contracts/` or a change to a D-numbered decision.
- The wire numbers differ from §1.

### Report back with

1. Gate numbers, suite time, and the module's time.
2. For each task: a metric table (valid metric and CI per candidate, plus the champion's test
   score) and the planted-coefficient table, both read from the wire run.
3. MCP calls per task.
4. The mutation table.
5. Every defect the gate found: symptom, root cause, fix, and the regression test.
6. For chunk 21: any argument shape or injection that was awkward over the wire.

---

## 6. Addendum — review corrections (2026-10-02)

The independent review re-ran the gates and all six §5 mutations, then mutated each
`check_lineage` clause. It found the driver weaker than this spec in five places. None changed a
result: the contract values happened to equal the hard-coded ones.

| Gap | Correction |
|---|---|
| `SPINE_NODES` omitted `model_interpretation`, so a test-derived input in node 11 would raise `ValueError` instead of a lineage violation | All 13 node tokens; a guard case for `predict` in `model_interpretation` |
| The one-score count was checked only inside `champion_selection`, so a second `score_*` in a later node passed | The count, like the `fit_*`/`train` clause, holds in every node (it is `runs.test_scored_at` in production); a guard case in `artifact_persistence`. Recorded in `00_index.md` row 21 |
| The `train` and `fit_*` clauses survived their own deletion, because every guard case was also caught by another clause | One case only each can reject: `apply_imputer` with a test-derived `train` in node 12, and `fit_ols` in `artifact_persistence` |
| One `seed` was injected into every tool ("both are 42") | `split.seed` for `split_*` and `constraints.random_seed` otherwise, both read from the contract. The gate still cannot observe the difference, since both are 42; chunk 21 tests the rule |
| The split tool was picked by task, and the imputer, encoder and scaler arguments were hard-coded | Taken from `split.strategy`, `preprocessing.missing.default`, `preprocessing.max_cardinality` and `preprocessing.scaling` |

`describe_artifact` on a JSON table reports its shape (keys and list lengths), not its values.
That is enough for the gate. If an LLM node later needs to read coefficient terms, extending it
to a bounded head of each list is the natural step; it is not done now.
