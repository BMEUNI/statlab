---
status: completed
dependencies: [58]
completion_date: 2026-10-09
implementation_notes: "digest for per-model and Q&A prompts; STATLAB_LLM_REASONING off in harness; live harness 1 wave, 0 retunes; make check 100%"
---

# Chunk 59 — cheaper live integration runs (and leaner prompts)

User request 2026-10-09: make the live integration tests as cheap as possible (cap input and
output, shorter prompts); they only prove the backend runs without flaws, not output quality.

Evidence (read-only):
- Every live harness run predates the prompt digest (`c0cc6de`). The user's newest real run
  `r_edd756461dd61f32`: model_interpretation avg 28k prompt tokens, model_evaluation 18k,
  model_training 12k; input is about three quarters of the cost.
- Offline S01 prompt (`integration/out/20261009T111722Z-offline/api.log`): in a per-model
  step the user message is ~26k characters, of which the `candidate` state summary is ~18k:
  `runtime.py` `decide` dumps the whole `CandidateProgress`, including every earlier step's
  raw `tools` results. Shared steps already get `boundary_digest(..., keep_tools=False)`.
- Chat Q&A (`graph/control.py` `answer_question`) dumps full `shared_results` and full
  branches (~151k tokens per question seen earlier).
- `problem_definition`, `experiment_design`, `inferential_analysis`, `model_interpretation`
  and `champion_selection` run with DeepSeek thinking at `reasoning_effort="high"`
  (`domains/routing.py`, `llm/client.py` ~320), which adds hidden completion tokens.
- Scenarios use the default constraints (8 candidates, 2 search waves, 2 retunes per model),
  so live runs pay for extra waves and retunes no scenario asserts on.

Decision:
1. Product, all runs: per-model prompts and chat Q&A get the compact digest. A model's
   earlier steps appear as `boundary_digest(branch.results, keep_tools=False)`; Q&A uses the
   digest for shared steps too. The digest helpers move to a new `graph/digest.py` (graph
   must not import domains); `domains/runtime.py` re-imports them.
2. New setting `STATLAB_LLM_REASONING` (default true). When false, every DeepSeek request is
   sent with thinking disabled and no `reasoning_effort`. The harness sets it false.
3. Live harness only: when a scenario's contract does not set them, `max_search_waves = 1`
   and `max_retune_loops = 0`. Offline modes keep the defaults (offline-rich exercises waves
   and retunes on purpose).
Non-goals: an output-token cap (a truncated JSON answer costs a repair call, which is more
than it saves), changing scenario candidate lists or checks.

## Stage 59a

Owned: new `backend/statlab/graph/digest.py`, `backend/statlab/domains/runtime.py`,
`backend/statlab/graph/control.py`, `backend/statlab/config.py`,
`backend/statlab/llm/client.py`, `integration/services.py`, `integration/driver.py`,
`integration/run_scenarios.py`, `tests/test_domain_runtime_digest.py`,
`tests/test_llm_client.py`. Preserve other edits.

1. `graph/digest.py` (docstring "Compact run history for prompt context only; code never
   reads it back (chunks 47, 59)."): move `_DIGEST_DROP`, `_SENTENCE`, `_brief`, `_prune` and
   `boundary_digest` from `domains/runtime.py` unchanged (with their imports). Add
   `def candidate_digest(branch: CandidateProgress) -> dict[str, JsonValue]:` returning
   `{**branch.model_dump(mode="json", exclude={"results", "pending_result"}), "results":
   boundary_digest(branch.results, keep_tools=False)}` (docstring "A candidate's progress
   with its earlier steps digested, for prompt context only."). Import `CandidateProgress`
   and `BoundaryResult` from `statlab.graph.state`.
2. `domains/runtime.py`: delete the moved definitions and import `boundary_digest`,
   `candidate_digest` from `statlab.graph.digest` (keep `boundary_digest` importable from
   `statlab.domains.runtime`, the existing test imports it there). In `decide`,
   `"candidate": candidate_digest(invocation.candidate) if invocation.candidate is not None
   else None`. Remove imports Ruff reports unused.
3. `graph/control.py` `answer_question`: `"boundaries": boundary_digest(
   context.state.shared_results, keep_tools=False)` and `"candidates":
   [candidate_digest(item) for item in context.state.branches]`, importing both from
   `statlab.graph.digest`.
4. `config.py`: `llm_reasoning: bool = Field(default=True, alias="STATLAB_LLM_REASONING")`
   next to `max_concurrent_llm`.
5. `llm/client.py`: in `__init__` store `self._reasoning = settings.llm_reasoning`; in the
   request builder the DeepSeek branch uses `thinking = reasoning and self._reasoning` for
   both the `thinking` type and the `reasoning_effort` condition.
6. `integration/services.py` `build_settings`: pass `llm_reasoning=False` to `Settings`
   (comment: "# The harness proves the backend runs; thinking tokens buy nothing (chunk 59).").
7. `integration/driver.py`:
   - `Driver.__init__` gains keyword `cheap: bool = False` stored as `self._cheap`.
   - Module function `def _cheap_constraints(contract: dict[str, JsonValue]) ->
     dict[str, JsonValue]:` copies `contract`; `constraints = dict(_obj(contract["constraints"]))
     if isinstance(contract.get("constraints"), dict) else {}`; `setdefault("max_search_waves",
     1)`, `setdefault("max_retune_loops", 0)`; returns `{**contract, "constraints":
     constraints}`. (Use the module's existing `_obj` helper if its signature fits; otherwise
     `dict(...)` with a cast as the module does elsewhere, and report which.)
   - In `setup`, right before `DataContract.model_validate(contract)`: `if self._cheap:
     contract = _cheap_constraints(contract)`.
8. `integration/run_scenarios.py`: `Driver(base_url=base_url, workspace=workspace,
   restart=restart, cheap=args.llm == "live")`.
9. Tests:
   - `tests/test_domain_runtime_digest.py`: `test_candidate_digest_drops_tools_and_pending`:
     build a `CandidateProgress` (use the minimal `CandidateSpec` construction another test in
     `tests/` already uses; name the file you copied it from in the receipt) with `results=(
     _b("preprocessing", NodeResult(status="completed", summary={"decision": {"x": 1},
     "tools": [{"tool": "t"}]})),)` and a `pending_result=_b("model_training",
     NodeResult(status="completed"))`; assert the digest has no `"pending_result"` key,
     `digest["results"] == [{"node": "preprocessing", "status": "completed", "summary":
     {"decision": {"x": 1}}}]`, and `digest["public_id"] == branch.public_id`.
   - `tests/test_llm_client.py`: next to the test asserting `thinking == {"type":
     "enabled"}` (~591), add the same call with `_settings(tmp_path).model_copy(update=
     {"llm_reasoning": False})` and `reasoning=True`; assert `calls[0]["thinking"] ==
     {"type": "disabled"}` and `"reasoning_effort" not in calls[0]`.
   - If any existing test in `tests/test_control_qa_status.py` or elsewhere pins the full Q&A
     or decision prompt and fails because of the digest, stop and report the assertion.

Checks: `make test-fast TESTS="tests/test_domain_runtime_digest.py tests/test_llm_client.py tests/test_control_qa_status.py tests/test_candidate_fanout.py tests/test_nodes_candidate_models.py tests/test_config.py"`
(skip a missing file and say so); Ruff check, Ruff format --check and mypy on every owned
Python file; then `.venv/bin/python -m integration.run_scenarios --llm offline --only S01 S07`
(offline, no cost) with exit code and the summary table from its output.

Main then: `make check`, measure offline prompt sizes against
`integration/out/20261009T111722Z-offline`, update `integration/SCENARIOS.md`, commit. A
live run needs the user's explicit go-ahead.

### Addendum 59a-A (separate test)

Put the reasoning-disabled case in its own test,
`test_reasoning_setting_off_disables_thinking(tmp_path, llm_context)`, a copy of the setup of
`test_legacy_flash_peak_cost_and_explicit_reasoning` (same `fake`, model and `Result`
output) with `_settings(tmp_path).model_copy(update={"llm_reasoning": False})` and
`reasoning=True`; assert only `calls[0]["thinking"] == {"type": "disabled"}` and
`"reasoning_effort" not in calls[0]`. Restore the original test to its committed form. Then
run `ruff format` on the owned Python files and the 59a Checks.

### Addendum 59a-B (import home)

`boundary_digest` now lives in `statlab.graph.digest`; `tests/test_domain_runtime_digest.py`
imports `boundary_digest` (and `candidate_digest`) from there. No re-export from
`domains/runtime.py`. Then rerun the mypy check and the remaining 59a Checks.

## Acceptance receipt (2026-10-09)

Implementer (after addenda A, B): focused selectors exit 0 (221 tests), Ruff check/format and
mypy on ten files exit 0, offline `run_scenarios --only S01 S07` exit 0, both PASS (24/24,
11/11), folder `integration/out/20261009T112326Z-offline`. Main reviewed the diff and ran
`make check` exit 0: coverage 100 % (TOTAL 12210 stmts, 0 missed), mypy 198 files, Ruff
clean (log `check59.log`, temporary). Offline S01 average prompt size per call, before
(`20261009T111722Z-offline`) and after: TrainingDecision 19.3k to 17.2k characters,
EvaluationActions 28.3k to 23.4k, ValidationConclusion 27.3k to 22.4k,
InterpretationDecision 35.7k to 28.3k; shared steps unchanged. Thinking off and no extra
waves/retunes act only on live runs; no live run made (needs the user's go-ahead).
`integration/SCENARIOS.md` documents the controls and the chunk 58 failure behaviour.
