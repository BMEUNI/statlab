---
status: completed
dependencies: [83]
completion_date: 2026-10-19
implementation_notes: "interpretation drops unusable choices; grid accepts fixed params; two LLM repairs; S10/S11 match current behaviour"
---

# Chunk 84 — fixes from the 2026-10-19 live suite

Live run `integration/out/20261019T152532Z-live` (user-approved, $3 cap, about $0.36 spent
before the user's network dropped): 9 PASS, 8 FAIL, S15/S16/S20 unfinished. Only one provider
timeout in `api.log`; the failures are product bugs or outdated scenarios (main, read-only, from
the harness DB):

| scenario | cause |
|---|---|
| S04, S07, S09, S19 | `model_interpretation` fails: the LLM asks `error_by` for raw columns (`plan`, `region`, `origin`) that are not in the encoded valid frame ("by columns [...] are not in truth"), or partial dependence for a raw column ("column 'plan' is not a model feature"). The candidate fails; champion selection then has < 2 eligible candidates → failure gate. |
| S18 | same, plus coefficients requested for a non-linear model ("coefficient importance requires supported uncalibrated linear estimator"), plus a grid `TrainingDecision` carrying approved `params` next to `param_grid` refused twice ("grid decision must put fixed parameters in param_grid") although training already merges `params` into the grid as single values. |
| S12 | `DesignDecision` repair 1 fixed a schema error but introduced an out-of-pool estimator; only one repair is allowed → "LLM output remained invalid after one repair". |
| S10 | outdated: temporal splits are supported since chunk 77; the run completed. |
| S11 | outdated wording: grouped refusal now says "grouped rows have no supported split yet". |

Decisions:
1. Interpretation keeps only usable choices (chunk 80/83 pattern): partial dependence and
   error segments only on the model's `feature_names`; coefficients only for supported
   uncalibrated linear estimators. Dropped choices give one warning; the step completes.
2. A grid decision may carry fixed `params`; they join the grid as single values (existing
   execution behaviour). The refusal goes.
3. The LLM client repairs an invalid answer up to twice (3 attempts at most).
4. S10 expects a completed temporal run; S11 expects the current refusal wording.

Non-goals: a single-survivor champion path (comparison needs two candidates; with 1 the run
still stops — separate decision if it recurs), error segments on raw columns (would need the
raw valid frame).

## Stage 84a (detailed spec)

Owned: `backend/statlab/domains/candidate_models.py`, `backend/statlab/llm/client.py`,
`backend/statlab/errors.py`, `tests/test_nodes_candidate_models.py`,
`tests/test_nodes_candidate_pipeline.py`, `tests/test_llm_client.py`,
`integration/scenarios.toml`, `integration/SCENARIOS.md`. Not alone: preserve others' edits.

### 1. `candidate_models.py`

a. Import `_strings` from `statlab.domains.foundation` (alphabetical in that import list).
b. Module constant after `SELECTION_CAVEAT`: `_COEFFICIENT_ESTIMATORS = frozenset({
   "linear_regression", "ridge", "lasso", "elastic_net", "logistic_regression"})`.
c. `_validate_training`: in the `if decision.method == "grid":` block delete the two lines
   `if decision.params: raise _RefusalError("grid decision must put fixed parameters in
   param_grid")`. Keep the `param_grid is None and estimator.params` refusal and the `elif`.
d. `_interpretation`: right after the multiclass-Brier refusal and before the
   `permutation_importance` call, insert:
   ```
   features = set(
       _strings(
           _object(trained.get("model_summary"), "model summary").get("feature_names"),
           "model features",
       )
   )
   estimator = branch.spec.estimator
   linear = estimator.name in _COEFFICIENT_ESTIMATORS and not estimator.calibrate
   usable = decision.model_copy(
       update={
           "coefficients": decision.coefficients and linear,
           "partial_dependence": tuple(c for c in decision.partial_dependence if c in features),
           "error_by": tuple(c for c in decision.error_by if c in features),
       }
   )
   dropped = (
       int(decision.coefficients and not linear)
       + len(decision.partial_dependence) - len(usable.partial_dependence)
       + len(decision.error_by) - len(usable.error_by)
   )
   if dropped:
       # Unusable choices are dropped, not fatal (chunk 84; chunk 80 pattern).
       evidence.warnings.append(f"ignored {dropped} interpretation choice(s) the model cannot use")
   decision = usable
   ```
   In the `if decision.coefficients:` block delete the estimator-name/calibrate refusal
   (`if (branch.spec.estimator.name not in (...) or branch.spec.estimator.calibrate): raise
   _RefusalError(...)`), keeping the `coefficient_importance` call and the diagnostics line.
   The LLM question text stays unchanged.

### 2. `client.py` and `errors.py`

- `client.py`: import `Final` from `typing` (add to the existing `from typing import
  Protocol`). Module constant near the top-level constants: `_REPAIRS: Final = 2` with
  docstring `"""An invalid answer is repaired up to twice (chunk 84): a repair often fixes one
  error and makes another."""`.
- `_complete`: `repaired = False` → `repairs = 0`; `if repaired` in `request_messages` →
  `if repairs`; the three `2 if repaired else 1` → `repairs + 1`; `if repaired:` before
  `raise LlmOutputError` → `if repairs == _REPAIRS:`; message → `"LLM output remained invalid
  after two repairs"`; `repaired = True` → `repairs += 1`.
- The `complete` docstring "Return a validated model, repairing one invalid response exactly
  once." → "Return a validated model, repairing an invalid response up to twice."
- `errors.py` `LlmOutputError` docstring → `"""A response remained invalid after the repair
  requests (two, chunk 84)."""`.

### 3. Tests

a. `tests/test_nodes_candidate_pipeline.py` `CandidateTools.call`: the `get_model_params` fake
   summary becomes `{"params": {"fit_intercept": False, "n_jobs": 1}, "feature_names": ["x"]}`.
b. `tests/test_nodes_candidate_models.py`:
   - `test_training_refusals`: bind the harness tools as `tools` if currently discarded. Replace
     the decision `TrainingDecision(rationale="Grid with fixed params", method="grid",
     params={"fit_intercept": True})` and its `_failed(...)` with:
     decision `TrainingDecision(rationale="Grid with fixed params", method="grid",
     params={"fit_intercept": True}, param_grid={"positive": (True, False)})`;
     `accepted = await _run(h, 8)`; assert `accepted.status == "completed"`; `tuned = [params
     for _, name, params in tools.calls if name == "tune_estimator"]`; assert
     `tuned[-1]["param_grid"] == {"fit_intercept": [True], "positive": [True, False]}`.
     If this changes later assertions in the same test (e.g. call counts), stop and report.
   - `test_interpretation_guided_skip_coefficients_and_unscaled_pdp`: replace the
     `_failed(await _run(h, 10), "coefficient importance requires supported uncalibrated
     linear estimator")` with `dropped = await _run(h, 10)`; assert `dropped.status ==
     "completed"`; `"ignored 1 interpretation choice(s) the model cannot use" in
     dropped.warnings`; `not any(name == "coefficient_importance" for _, name, _ in
     tools.calls)`.
   - New test after it, `test_interpretation_drops_columns_the_model_does_not_have`
     (`@pytest.mark.asyncio`, fixtures `migrated_db, project, tmp_path`): `h, tools = await
     candidate_harness(migrated_db, project, tmp_path)`; `await prepare_candidate(h)`; `await
     _run_save(h, 8)`; `await _run_save(h, 9)`; response `InterpretationDecision(rationale="Raw
     columns", partial_dependence=("x", "plan"), error_by=("region",))`; `result = await
     _run(h, 10)`; assert `result.status == "completed"`; `"ignored 2 interpretation choice(s)
     the model cannot use" in result.warnings`; `[params["column"] for _, name, params in
     tools.calls if name == "partial_dependence"] == ["x"]`; no `analyze_errors` call;
     `decision = result.summary["decision"]`; `isinstance(decision, dict) and
     decision["error_by"] == []`.
c. `tests/test_llm_client.py`:
   - `test_setup_completion_rejects_two_invalid_answers_without_audit`: `calls == 2` → `calls
     == 3`.
   - `test_semantic_check_rejected_twice_is_output_error`: `len(calls) == 2` → `len(calls) ==
     3`.
   - `test_second_invalid_answer_is_tool_param_without_foreign_text`: `len(_rows(conn)) == 2`
     → `== 3`.
   - `test_request_recovery_does_not_restart_output_repair`: `len(calls) == 3 and delays ==
     [1.0]` → `len(calls) == 4 and delays == [1.0]`; `len(_rows(conn)) == 3` → `== 4`; the two
     message-equality assertions stay.
   No other test edits; if another test fails because of the third attempt, stop and report.

### 4. Integration scenarios

- `scenarios.toml` S10: `title = "Temporal split runs end to end"`, `max_cost_usd = 0.30`,
  delete its `[[scenario.gates]]` block, and replace its `[scenario.expect]` table with
  `status = "completed"`, `nodes_completed = ["data_splitting", "champion_selection"]`,
  `champion = true`, `test_scored = true`, `pipeline_replay = true`.
- S11: `failure_contains = "no supported split"`; title `"Grouped data is refused cleanly"`.
- `SCENARIOS.md`: S10 heading → `### S10 — Temporal split runs end to end`; its Expect bullet →
  "**Expect:** the temporal split (chunk 77) orders train/valid/test by `date`; the run
  completes with a champion, one test score and a replayable `pipeline.py`." S11 heading →
  `### S11 — Grouped data is refused cleanly`; its Expect → "**Expect:** `data_splitting`
  refuses with a data-class error containing "no supported split"; failure gate, `abort` →
  `cancelled`; no candidate executions, no test scoring."

### Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_nodes_candidate_models.py tests/test_nodes_candidate_pipeline.py tests/test_llm_client.py tests/test_nodes_candidate_search.py tests/test_control_recovery.py"`
- Ruff format/`--check`/check and mypy on the six owned Python files.
- `.venv/bin/python -m integration.run_scenarios --llm offline --only S10 S11 S04` (free;
  report the summary rows). Never `--llm live`.
Stop and report on any failure or gap. Receipt: changed files, commands, exits, last log
lines, deviations.

### Addendum 84a-A (test ordering)

`test_training_refusals` later asserts no `train_estimator`/`tune_estimator` call happened, so
the accepted grid-with-fixed-params run moves to just after that assertion; the old refusal
case is deleted in place.

### Addendum 84a-B (second live batch)

Live `20261019T161206Z-live` (S15/S16/S20, $0.1284): S15 and S16 cancelled by the same
interpretation `error_by` failure (fixed above). S20 completed (text methods work live) but the
real LLM proposed 5 candidates, so S20's exact `candidate_count = 4` goes; `min_candidates_completed
= 4` stays.

### Addendum 84a-C (gate findings)

First `make check` exit 2: `test_llm_stream.py::test_two_invalid_streams_raise_output_error_after_one_repair`
and `test_api_assistant.py::test_draft_failure` assumed two attempts (assistant.py:331 then
uncovered). Correction: a third invalid stream / reply in each; counts 2 → 3.

## Stage 84a acceptance receipt (2026-10-19)

Implementer: focused selections exit 0 (candidate models/pipeline, LLM client/stream,
assistant API, search, recovery); Ruff/mypy on owned files exit 0; offline S04, S10, S11
PASS (`20261019T161733Z-offline`), offline S20 PASS 16/16 (`20261019T161846Z-offline`).
Main reviewed the source diff (matches recipe + addenda A-C). `make check` (`check84b.log`,
temporary) exit 0: coverage 100% (TOTAL 13944 stmts, 0 missed, 3940 branches), mypy 213 files,
Ruff clean, 222 files formatted. Live re-run of the failed scenarios follows.

## Live re-run (2026-10-19)

`integration/out/20261019T185155Z-live` ($0.3388): S04, S07, S10, S11, S15, S16, S18, S19, S20
PASS. S12 and S09 failed on new causes, handled in chunk 85.
