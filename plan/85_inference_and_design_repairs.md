---
status: in_progress
dependencies: [84]
completion_date: null
implementation_notes: ""
---

# Chunk 85 — the last two live-suite failures (S12, S09)

Live run `integration/out/20261019T185155Z-live` ($0.34): 9 of 11 re-run scenarios PASS. Two
remaining failures (main, read-only, from the harness DB):

| scenario | cause |
|---|---|
| S12 | `InferenceDecision` predictors `["x4","x1","x2","x3"]` (goal "Does x4 matter?"; the LLM added `x1..x3` as exploratory controls; they separate the species). `fit_mnlogit` refuses: "perfect separation or non-convergence: coefficients not identified" (data class) → `inferential_analysis` fails → failure gate → abort → cancelled. |
| S09 | Fork child `experiment_design`: the LLM nests `family_justifications` inside `candidate_set` three times → "candidate_set.family_justifications: Extra inputs are not permitted" → "LLM output remained invalid after two repairs". |

Decisions:
1. An inferential fit refused with a data-class error is not fatal. When the decision added
   exploratory controls, the node refits once with the goal predictors only (with a warning).
   When that also fails, or there were no controls, the node is `skipped` with the tool's
   reason. Champion selection already handles a non-completed inference (`champion.py` ~400:
   coefficients only when `status == "completed"`). Non-data errors and cancellation keep
   propagating as today.
2. `DesignDecision` accepts a `family_justifications` object nested in `candidate_set` by lifting
   it to the top level before validation (chunk 79 `_shorten_summary` pattern: repair a common
   LLM shape instead of failing).

Non-goals: changing the inference tools (their refusal is honest); `SearchDecision` (its
candidates are a tuple, no nested `candidate_set` was seen); recording the fitted predictor set
as a new summary field (the warning names it).

## Stage 85a (detailed spec)

Owned: `backend/statlab/domains/decisions.py`, `backend/statlab/domains/design_inference.py`,
`tests/test_nodes_design_inference.py`. Not alone: preserve others' edits.

### 1. `decisions.py` — `DesignDecision`

Add one method to `DesignDecision`, after its fields, decorated `@model_validator(mode="before")`
then `@classmethod` (same decorator order as `_shorten_summary`):
`def _lift_family_justifications(cls, data: object) -> object:` with docstring
`"""Accept family_justifications nested in candidate_set, a common LLM shape (live S09,
chunk 85), instead of failing the step."""`.

Control flow: bind `nested = data.get("candidate_set")` only when `data` is a dict. If `data` is
a dict, `nested` is a dict, `"family_justifications"` is a key of `nested`, and
`"family_justifications"` is not a key of `data`: return a new dict equal to `data` with
`"candidate_set"` replaced by a copy of `nested` without the `"family_justifications"` key and
with top-level `"family_justifications"` set to `nested["family_justifications"]`. Otherwise
return `data` unchanged. Never mutate the input dicts. Use one `if` with the four conditions
(a walrus for `nested` is fine) so there is a single branch pair.

### 2. `design_inference.py` — `_inference`

Replace the section from `matrix_output = await self._call(...)` (the
`assemble_inference_matrix` call) through `fit = _artifact(fit_output, "inferential_fit")` with:

1. `fixed` = set of every predictor in `problem.inference_goals` (same expression as in
   `_validate_inference`).
2. `attempts: list[InferenceDecision] = [approved]`. If any of `approved.predictors` is not in
   `fixed`, append `approved.model_copy(update={"predictors": <approved.predictors filtered to
   those in fixed, original order>, "reference_levels": <approved.reference_levels filtered to
   keys in fixed>})`. (`_validate_inference` already guarantees every fixed predictor is in
   `approved.predictors`, so the filtered tuple is non-empty.)
3. `reason = ""`. `for attempt in attempts:`
   - `matrix_output` = the existing `assemble_inference_matrix` call, but with
     `list(attempt.predictors)` and `attempt.model_dump(mode="json")["reference_levels"]`.
   - `matrix, manifest` and `params` built exactly as today (from `matrix_output`, `entry`).
   - `try: fit_output = await self._call(invocation, evidence, tool, params)`;
     `except ToolCallError as exc:` if `exc.failure.error_class != "data"`: bare `raise`;
     else `reason = exc.failure.message` and `continue`.
   - After the try: `if attempt is not approved:` append warning
     `f"Inferential fit with exploratory controls failed ({reason}); refitted with the goal
     predictors {', '.join(attempt.predictors)} only"`. Then `break`.
   - `else:` clause of the `for` (every attempt failed): return
     `NodeResult(status="skipped", summary={**summary, "tools": evidence.tools},
     warnings=tuple(evidence.warnings), skip_reason=f"The inferential model could not be fitted
     ({reason}), so no coefficients are reported.")`. No artifacts (a skip may carry none).
   Add a one-line comment above the loop: `# A refused fit is not fatal (chunk 85): refit
   without exploratory controls, else skip.`
4. `fit = _artifact(fit_output, "inferential_fit")` and everything after stays unchanged
   (it uses the last `matrix`, `manifest`, `matrix_output`, `fit_output`).

No other edits; the outer `except ToolCallError` in `__call__` stays for the other tools.

### 3. Tests (`tests/test_nodes_design_inference.py`)

a. `DesignTools.__init__`: add `self.fail_once = False`. In `call`, in the
   `if tool == self.failure_tool:` branch, before raising: `if self.fail_once:
   self.failure_tool = None` (the raise still happens on that call).
b. `test_inference_gate_refusal_cancellation_and_nonconvergence`: the `refused` block becomes:
   `refused = ...` (same call); assert `refused.status == "skipped"`, `refused.skip_reason ==
   "The inferential model could not be fitted (fit refused), so no coefficients are
   reported."`, `refused.artifacts == ()`. Rename the variable `skipped` if you like; nothing
   else in the test changes.
c. New test after it, `test_inference_refits_without_controls_then_skips`
   (`@pytest.mark.asyncio`, fixtures `migrated_db, project, tmp_path`):
   - `h, tools = await prepared(migrated_db, project, tmp_path)`; `await design(h)`.
   - `h.llm.responses[InferenceDecision] = InferenceDecision(model="ols", predictors=("x",
     "excluded"), reference_levels={"excluded": "a"}, rationale="Goal plus a control.")`.
   - `tools.failure_tool, tools.fail_once = "fit_ols", True`;
     `refit = await DesignInferenceExecutor(h.runtime)(h.invocation(5))`.
     Assert `refit.status == "completed"`; `"Inferential fit with exploratory controls failed
     (fit refused); refitted with the goal predictors x only" in refit.warnings`;
     `[(params["predictors"], params["reference_levels"]) for _, name, params in tools.calls if
     name == "assemble_inference_matrix"][-2:] == [(["x", "excluded"], {"excluded": "a"}),
     (["x"], {})]`.
   - `tools.calls.clear()`; `tools.failure_tool, tools.fail_once = "fit_ols", False`;
     `skipped = await DesignInferenceExecutor(h.runtime)(h.invocation(5))`. Assert
     `skipped.status == "skipped"`; `skipped.skip_reason == "The inferential model could not be
     fitted (fit refused), so no coefficients are reported."`; `sum(name == "fit_ols" for _,
     name, _ in tools.calls) == 2`; no `coefficient_table` call.
d. New test `test_design_decision_lifts_nested_family_justifications` (plain `def`, no fixtures):
   `payload = shortlist("linear_regression").model_dump(mode="json")`;
   `del payload["family_justifications"]`;
   `payload["candidate_set"]["family_justifications"] = {"linear": "Why three."}`;
   assert `DesignDecision.model_validate(payload).family_justifications == {"linear": "Why
   three."}`. Then `both = shortlist("linear_regression").model_dump(mode="json")`;
   `both["candidate_set"]["family_justifications"] = {"linear": "Nested."}`; `with
   pytest.raises(ValidationError): DesignDecision.model_validate(both)` (a top-level key already
   exists, so nothing is lifted and the nested extra key is still refused).
If the `excluded` predictor is refused by `_validate_inference` in this harness, or any other
existing test fails, stop and report (do not change it).

### Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_nodes_design_inference.py tests/test_domain_decisions.py tests/test_nodes_candidate_search.py"`
- `.venv/bin/python -m ruff format` then `--check`, `ruff check`, and `mypy` on the three owned
  files.
- `.venv/bin/python -m integration.run_scenarios --llm offline --only S12 S09` (free; report the
  summary rows). Never `--llm live`.
Stop and report on any failure or gap. Receipt: changed files, commands, exits, last log lines,
deviations.

### Addendum 85a-A (gate coverage)

First `make check` exit 2: coverage 99.98%, `design_inference.py` lines 78 (outer
`except ToolCallError` return in `__call__`) and 383 (non-data re-raise in the fit loop)
uncovered; the only fit-failure test now skips. Correction (tests only):
- `DesignTools.__init__`: add `self.failure_class: ErrorClass = "data"` (import `ErrorClass`
  from `statlab_contracts.envelope` next to `Failure, ToolSuccess`); the failure raise uses
  `error_class=self.failure_class`.
- At the end of `test_inference_refits_without_controls_then_skips`: `tools.failure_tool,
  tools.fail_once, tools.failure_class = "fit_ols", False, "bug"`; `failed = await
  DesignInferenceExecutor(h.runtime)(h.invocation(5))`; assert `failed.status == "failed"`,
  `failed.error is not None and failed.error.error_class == "bug"`, `failed.error.message ==
  "fit refused"`.
Checks: same focused pytest selection and Ruff/mypy on the test file.

## Stage 85a acceptance receipt (2026-10-19)

Implementer: focused design/inference, decisions and search selections exit 0; Ruff/mypy on
owned files exit 0; offline S12 PASS 26/26, S09 PASS 22/22 (`20261019T191238Z-offline`). Main
reviewed the source diff (matches the recipe + addendum A). First `make check` exit 2 (99.98%,
addendum A); second `make check` (`check85b.log`, temporary) exit 0: coverage 100% (TOTAL 13966
stmts, 0 missed, 3950 branches), mypy 213 files, Ruff clean, 222 files formatted. Live re-run
of S12/S09 follows.
