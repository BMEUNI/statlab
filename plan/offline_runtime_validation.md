---
status: in_progress
dependencies: [27a, 27b, 28, 29, 30, 31, 32, 33, 34, 35, 36]
completion_date: null
implementation_notes: "Offline runtime validation authorized 2026-10-05; focused correction loop in progress."
---

# Offline runtime validation

## Goal and scope

Verify the implemented B9–B14 behavior against the existing reviewed tests, then
perform one stable full Python acceptance. Candidate starts at `edf6ad7` with a
clean working tree. Docs-only descendants preserve B14 static evidence. Reuse
B8 behavioral evidence and B9–B14 static receipts rather than rerunning startup
baselines. This pass targets the previously unexecuted domain, control, API,
stream, frontend client and launcher behavior.

No B15/B16 implementation, dependency changes, paid calls, credential validation
against remote services, manual service launch or browser smoke runs. Existing
tests may use temporary files, fake providers, in-process ASGI and test-owned
loopback MCP fixtures; these remain offline checks. No real project data is used.

Main investigates, runs validation and reviews only. Product/test/probe edits,
including mutations, remain implementer-owned. The available implementer role
is pinned to the required Luna/low configuration. No writer is needed unless a
check exposes a failure requiring a fully resolved correction recipe.

## Accepted contracts and evidence

Preserve PLAN D9 replay and sequence identity, D11 forks, D12 allow-lists, D13
loopback/authentication, D18 provider isolation, D27 domain isolation and D32
selection/test-lineage locks. Preserve per-run bindings, durable gate/fork
idempotence, restart reconciliation before admission and API task/shutdown
ownership described in WHATS_NEXT and the B12–B14 receipts.

Inspection of the active stage validation sections found no new mandatory
mutation recipe in B9–B14. Unchanged accepted B6 mutations/replays are reused.
Normal suite replay coverage counts where specified; do not invent a mutation
quota or repeat old probes. Inspect any newly generated required report and
pipeline artifacts fully and retain their replay evidence.

## Ordered checks

All pytest commands use `make test-fast` (the Makefile's explicit 900-second
subprocess timeout), without coverage, and stop at the first failure via `-x`.
Keep unique complete logs in `/private/tmp/statlab-offline-20261005-*`. Main
records genuine exits and reads the complete failure/summary before advancing.

1. Domain behavior and changed shared callers:
   `make test-fast TESTS='-x tests/test_nodes_foundation.py tests/test_domains_base.py tests/test_nodes_design_inference.py tests/test_candidate_fanout.py tests/test_registry.py tests/test_tools_training.py tests/test_nodes_candidate_pipeline.py tests/test_nodes_candidate_models.py tests/test_nodes_candidate_search.py tests/test_tools_cleaning_fit.py tests/test_nodes_champion.py tests/test_nodes_persistence.py tests/test_domain_executor.py'`.
2. Control/recovery and affected persistence/client boundaries:
   `make test-fast TESTS='-x tests/test_control_router.py tests/test_control_qa_status.py tests/test_control_recovery.py tests/test_control_lifecycle.py tests/test_control_gates.py tests/test_control_rewind.py tests/test_restart_reconciliation.py tests/test_repository.py tests/test_storage.py tests/test_mcp_client.py tests/test_llm_client.py'`.
3. API and setup integration:
   `make test-fast TESTS='-x tests/test_domain_binding.py tests/test_api_projects.py tests/test_api_runs.py tests/test_tools_setup.py tests/test_tools_intake_load.py tests/test_tools_intake_roles.py tests/test_tools_intake_validate.py'`.
4. Streaming and launcher behavior:
   `make test-fast TESTS='-x tests/test_api_stream.py tests/test_llm_stream.py tests/test_dev.py'`.
5. Frontend offline clients: `npm test` from `frontend/`; use the same explicit
   900-second subprocess wrapper as pytest, passing npm and test as arguments.
6. After focused checks/corrections are stable, main runs `make check` once,
   retaining full tests/coverage, mypy, Ruff and format exits. Require 100% line
   and branch coverage and warnings-as-errors without weakening validators.
   Accepted frontend typecheck/build remain valid unless their inputs change.

## Failure and acceptance rules

Stop affected work on failure. Main reads the failing test, fixture, source,
callers and relevant decision, then appends a precise correction plan before
delegating to one Luna/low implementer with `fork_turns="none"`. Worker may
change only explicitly owned files and run exact focused checks. Missing
decisions return to main; no independent fixes or scope expansion. Main never
edits code. Reuse the same writer for that correction stage.

A timeout, collection failure, sandbox denial or orchestration failure is not
a behavioral pass. A failed final gate is not accepted; only changed inputs
justify another final execution. Do not broaden tests without a changed shared
boundary or a concrete uncovered reachable branch.

Terminal receipt records candidate commit/product hashes, exact commands,
exits/counts/coverage, full logs, corrections, requirements proved and remaining
online/browser/process limitations. Update live status and affected index rows
only after acceptance; historical receipts retain their original scope. Commit
finished work locally using exact paths, never push. Stop once offline proof is
complete; online validation remains a separate authorization.

## Correction C1 — foundation tool fixture contract

Domain command 1 exited2 (`make`; pytest failed) after5 passes at
`tests/test_nodes_foundation.py::test_first_four_flow_preserves_evidence_and_test_lock_boundary`.
Full log: `/private/tmp/statlab-offline-20261005-domain-1.log`.
`DomainRuntime.decide` now calls `tools.control.check()` and supplies recovery
using `tools.sleep`. Production `ToolBoundary.__init__` always initializes both;
the offline subclass overrides that initializer without doing so. This is a
fixture contract defect, not a missing production guard.

Only owned source file: `tests/test_nodes_foundation.py`. Only changed symbol:
`OfflineTools.__init__(self, conn: sqlite3.Connection) -> None`, plus its import
from `statlab.graph.candidates`. Reuse `RunControl` at
`backend/statlab/graph/candidates.py:80`; do not invent a fake/no-op control.
In the existing initializer, initialize `self.control` to a fresh `RunControl`
and `self.sleep` to `None` before the existing fixture fields. Preserve all
other attributes and the existing `call` signature/behavior. No super-call:
this fake intentionally has no real MCP client or fit budget. Every fake
instance owns a distinct unpaused/uncancelled control; `None` selects the
existing default recovery sleep. Do not modify DomainRuntime or other source.

All descendants (`DesignTools`, `CandidateTools`, `ChampionTools`) already call
the parent initializer and therefore inherit these values. Their existing
test factories and domain callers were inspected. No new test is required:
the failing flow exercises real DomainRuntime decision admission, while the
full planned domain group exercises all descendant consumers, cancellation,
gate policy, frozen transformations and champion score boundaries. No validator
bypass or assertion weakening. Cancellation remains the original exception;
this fixture correction adds no dispatch/retry side effect.

Run sequentially and stop on the first failure: scoped Ruff check and format
verification of the owned file; scoped mypy of the owned file; then the exact
domain command 1 above. The worker may apply mechanical import/format changes
only within this file. Use unique `...-c1-*` logs. No globals, no other commands,
no commits, no plan edits, no subagents. Report exact changed paths/hash,
commands/exits/pass count/logs and any next failure; do not fix it independently.
Main reviews the terminal diff and receipt before continuing dependent checks.

## Correction C2 — validate fan-out before committing input

C1 fixture statics passed; the domain group exited2 after124 passes at
`test_initial_cap_and_invalid_traversal_have_no_side_effects`. Full log:
`/private/tmp/statlab-offline-20261005-c1-domain-1.log`. `_run` saves the input
before `_fanout` validates declarations/cap/CandidateSet. The first rejected
attempt leaves a valid-looking checkpoint, so later attempts load its branches
instead of their supplied candidates. This violates the existing no-side-effect
test and admission policy. Do not change the expected baseline error.

Owned files/symbols: `backend/statlab/graph/engine.py`, only `RunLoop._run`
and `RunLoop._fanout`; `tests/test_candidate_fanout.py`, only
`test_initial_cap_and_invalid_traversal_have_no_side_effects`. Preserve C1 and
all other edits. Public signatures/defaults/types remain unchanged:
`_run(self, initial: RunState) -> RunState` and
`_fanout(self, state: RunState) -> RunState` are async. No new helper/import.
Reuse `load_state`, Checkpointer.save, TraversalPlan.region and CandidateSet
unchanged. Main inspected their implementations, production binding, graph/fanout
factories, lifecycle/reconciliation load callers and relevant B10/B12 contracts.

Ordered engine recipe:
1. Remove the unconditional checkpoint save immediately following load_state.
   Leave the SQL status refusal first; on refusal do not alter persistence.
2. In the existing non-pending/non-running state return branch, save the loaded
   state immediately before returning. This preserves terminal/dormant handling.
3. Leave the requested pause/cancel branch unchanged: it saves the transitioned
   state, writes SQL/event status, then returns; no fan-out validation is added
   to lifecycle stop admission. Cancellation exception propagation is unchanged.
4. Leave the fan-out selection condition/call unchanged. For the sequential
   path, insert the original input save immediately after this condition and
   before candidate registration. Preserve all subsequent operation ordering.
5. In `_fanout`, keep every existing validation and its order/error exactly:
   traversal mismatch, region declaration, planner/design, retune/training,
   max_candidates, multi-branch CandidateSet, missing candidate. Insert a save
   of the incoming state immediately after the final missing-candidate guard
   and before the branch-registration loop. Thus accepted input is durable
   before SQL candidates or running state, exactly as previously, while all
   rejected declarations leave no checkpoint/SQL/event dispatch effects.
   Keep the running `_state` assignment, budget snapshot and status writes intact.

No new validation, error wrapping, async task, transaction or concurrency owner.
CandidateSet's existing Pydantic validator produces ValidationError for zero
baselines in a multi-candidate shortlist; single-candidate legacy compatibility
remains unchanged. Invalid cap2 with3 candidates -> EngineError/max_candidates;
overlap=compare after fit -> EngineError/overlap; specs(3,10) with no baseline ->
ValidationError/baseline. All three attempts on the same fresh pending run must
leave state.json absent, candidate/node tables empty and event replay empty.
Valid fan-out still saves its input before first SQL candidate, and valid
sequential execution before its candidate/attempt; restart reuses saved inputs.

Test recipe: in the existing regression, immediately after each of its three
raises blocks assert no state.json, no node executions, zero candidate rows via
the existing world.conn count query pattern, and empty awaited runner.events
replay. Replace the final duplicate checkpoint/execution-only assertions with
the complete third set. Keep setup, no-dispatch executor, inputs, raises and
matches unchanged. Existing graph tests cover terminal returns, cancellation,
checkpoint failures and SQL control ahead; existing fan-out tests cover invalid
declarations, valid input persistence, restart and join/cancellation ownership.

Run sequentially, first failure stops: Ruff check/format verification and mypy
scoped to both owned files; `make test-fast TESTS='-x tests/test_graph_engine.py
tests/test_candidate_fanout.py'`; then domain command1 above. Unique c2 logs.
No globals, mutation/probe, other source, commits, plan edits or subagents.
Receipt includes exact diff/files/hashes, commands/exits/counts/logs, deviations
and next failure. Main reviews terminal handoff; no independent workaround.

## Environment recovery E1 — bounded CPU detection

C2 reviewed: both scoped static commands plus mypy exit0; graph/fanout88 pass.
Domain command1 then exited2 after178 passes because joblib's macOS physical-core
query returned empty output under sandbox and its fallback emitted UserWarning,
correctly rejected by warnings-as-errors. No product defect was shown there.
Installed joblib `externals/loky/backend/context.py` documents and implements
`LOKY_MAX_CPU_COUNT`; a user limit below logical cores bypasses physical probing.
All remaining Python runtime commands use `LOKY_MAX_CPU_COUNT=1` as an explicit
process resource limit, including final `make check`. No source/config mutation,
warning filtering or validator weakening. This honors the single-worker compute
policy; it does not prove behavior under multi-core OpenMP. Main reruns domain1
with that prefix, then proceeds through ordered checks if green. Keep E1 logs.

## Environment recovery E2 — test-owned loopback socket

E1 reached the MCP round-trip fixture but sandbox denied binding127.0.0.1:0
(`PermissionError`, command exit2). No behavioral assertion failed there.
Run Python runtime checks outside sandbox through reviewed escalation when
their offline fixtures require local sockets. Scope stays unchanged: temporary
loopback MCP with fixture token/fake providers only, no paid or remote calls.
Retain CPU limit1 and warnings-as-errors. Keep unique E2 logs and genuine exits.

## Correction C3 — unique fixture execution attempts

E2 domain command1 exited2 after279 passes at
`test_guided_unknown_and_ineligible_choice_stop_before_selection`; its second
direct fixture invocation inserts attempt1 for the same run/node/candidate,
violating ux_node_executions_identity. Production RunLoop already derives
`next_node_attempt` for every dispatch. Do not weaken the SQL unique index.
Log: `/private/tmp/statlab-offline-20261005-e2-domain-1.log`.

Only owned file: `tests/test_nodes_champion.py`. Symbols: `tail_invocation`
and `test_latest_retune_model_threshold_and_repository_exposure_are_reported`,
plus repository import. Main read the entire file and all persistence-test
callers, and production engine/champion audit recovery. Preserve other edits.
Signatures remain `tail_invocation(h: Harness, index: int = 11) -> NodeInvocation`
and existing async test signature. Import existing `next_node_attempt` from
`statlab.persistence.repository` (line531): `(conn, *, run_id: str,
node_name: str, candidate_id: str | None) -> int`, max existing matching attempt
plus1, with NULL candidate matched via IS. No new helper or fallback.

Ordered recipe: `tail_invocation` continues selecting TABULAR_SPINE[index],
then creates the same same-run shared execution, but supplies `attempt` from
next_node_attempt using h.runtime.conn, h.state.run_id, spec.name and
candidate_id=None. Return unchanged NodeInvocation. In the retune test's
existing model_training create_node_execution call, supply attempt derived
with migrated_db, same run, node_name=model_training and the identical first
candidate database_id. This sibling fixture also repeats a prior training row;
correct both callers of this fixture defect, preserving all setup/assertions.

First invocation remains1; repeated shared champion/persistence calls become
2,3; retune candidate training follows the existing attempt. Distinct run/node/
candidate keys remain independent. No state/cursor/tool/audit restoration,
champion rescoring, result finishing, retry authorization or production changes.
Existing guided/unknown/ineligible and interruption tests are the regression
checks: they must reach their original failed/completed assertions while exactly
one select_champion/test score remains enforced. Existing retune test must prove
the latest frozen model, threshold and prior-exposure report; persistence
callers must still prove interrupted artifact replay and immutable manifests.

Run scoped Ruff check, format --check and mypy for owned file; then
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='-x tests/test_nodes_champion.py
tests/test_nodes_persistence.py tests/test_domain_executor.py'`, using reviewed
escalation only if socket fixtures require it. No need to repeat178+ accepted
domain predecessors: the remaining domain modules are this exact suffix.
Unique c3 logs; stop first failure; receipt hashes/commands/exits/counts/logs.
No additional code, globals, mutations/probes, plans, commits or subagents.

## Correction C4 — truthful persisted fixture ownership and producer audit

C3 statics exit0; all9 champion cases pass. Suffix exits2 at first persistence
case: checkpoint execution ownership mismatch. Log `...-c3-suffix.log`.
The checkpoint's shared IDs are synthetic; c02 copies c01 execution IDs, and
pre-champion fake outputs have no complete producer audit. Production validates
these correctly. This correction changes unit fixtures only, never validators.
Main inspected every caller of the three helpers, all fixture tool dispatches,
storage ownership validation, fork-aware logged_calls, champion provenance and
repository creation/finishing APIs. These fixtures are control-flow stubs, not
scientific pipeline/replay evidence.

Owned files/symbols only:
* `tests/test_nodes_candidate_pipeline.py`: `candidate_harness`.
* `tests/test_nodes_champion.py`: `champion_harness`, new private helper
  `_persist_fixture_call(h: Harness, execution_id: str, tool: str,
  arguments: dict[str, JsonValue], output: ToolSuccess) -> None`, imports.
* `tests/test_domain_executor.py`: `test_dispatches_all_thirteen_declared_nodes`,
  repository import.
All existing signatures/defaults remain unchanged. Preserve C1–C3 and others'
edits. No application changes or new fixture modes/classes/dependencies.

Ordered recipe:
1. In candidate_harness retain the DesignTools returned by prepared, instead
   of discarding it. Immediately after successful design, append its captured
   calls to h.tools.calls. This list now contains actual foundation then design
   calls in dispatch order. Do not change any sent argument or tool output;
   CandidateTools continues with its own empty calls/outputs lists.
2. Add the private persistence helper to champion tests. For each output meta,
   write bytes `offline artifact` to h.runtime.paths.artifact(meta.ref, meta.kind),
   except model outputs write `frozen fitted model`. Register using existing
   create_artifact: same ref/run/kind/path/full meta, produced_by=tool,
   split=meta.split, derived_from_test exactly meta.split == test. Then record
   one tool call using existing record_tool_call, actual arguments JSON, exact
   output.model_dump_json(), given execution ID, same run, duration_ms=1.
   No duplicate skipping, altered arguments, invented outputs or catch/fallback.
   Repository transactions remain helper-owned; exceptions fail the fixture.
3. In champion_harness retain CandidateTools returned by candidate_harness.
   Immediately after that call, materialize each shared boundary in order:
   create_node_execution for its node, same run, candidate None, actual spine
   index+1, next_node_attempt matching that key; finish with its exact result;
   build a replacement BoundaryResult with the new ID and unchanged result.
   Keep mapping old synthetic execution ID to newly created ID. Build a typed
   list of (tool name, ToolSuccess) from each boundary's summary tools in order:
   absent tools means empty; require each present value list, each entry dict,
   tool string, result dict via assertions, then ToolSuccess.model_validate on
   the stored evidence result. This is deserialization of existing evidence.
   Zip h.tools.calls with these outputs using strict=True, assert matching tool
   names, and call the helper with mapped ctx.node_execution_id and the captured
   arguments. Replace state shared_results with the materialized tuple through
   the existing model_copy update style; retain all other state fields.
   The skipped inference boundary receives a genuine finished skipped row.
   Default foundation fixture here has exactly one raw output, one plot, one
   duplicate-removal frame and three split outputs; no reused output producer.
4. Run existing c01 preparation/models unchanged. Zip retained CandidateTools
   calls and outputs strict=True after nodes6–10, assert equal tool names, then
   helper-persist each with its actual ctx.node_execution_id and arguments.
   This preserves dispatch order before champion and records all frozen
   transformers, train/valid frames, model and validation outputs, not only the
   old synthetic train_estimator call. All c01 execution rows already exist and
   have been finished by save_result.
5. Keep the c02 comparison stub's summaries/methodology/results construction.
   Ensure its SQL candidate before constructing its result boundaries, using
   public_id c_02 database identity and the existing second spec. For each copied
   boundary, first build its existing typed NodeResult unchanged, then create
   and finish a distinct node execution owned by c02, same run, node name,
   spine index+1 and next_node_attempt for c02. Use that new ID in its boundary.
   This remains an explicitly declared comparison stub; it does not claim a
   second compute run. Its results retain existing no-artifact behavior.
   Remove the now-redundant later ensure_candidate.
6. Remove the old manual c01 trained-artifact and empty-arguments tool log block,
   and TEST fallback artifact block. The captured real fake-call producers now
   provide both. Preserve ChampionTools setup, counter1000, skill, choice and all
   test expectations. Remove imports only if now unused; _ref remains used by
   later tests, create_artifact/record_tool_call remain used elsewhere/helper.
7. Dispatcher regression: enumerate the existing strict zipped spine/types,
   starting1; use enumerated seq instead of1. Derive candidate_id once from
   selected branch (None for shared). Supply next_node_attempt on that identical
   run/node/candidate key. Preserve all dispatcher assertions and monkeypatches.

Expected invariants: shared rows own exact node/seq/status and None candidate;
c01 and c02 never share execution IDs; every retained artifact exists in same
run. Every tool argument artifact has exactly one earlier actual producer.
Split TEST metadata remains test/derived; candidate fake calls still reject TEST
use. No paid compute, test fitting, rescoring, cancellation conversion or guard
bypass. Fixture setup calls/outputs cardinality disagreement must fail, never
be silently truncated. Genuine empty tools evidence on problem/inference yields
no audit calls. Real scientific artifact/replay tests remain separate.

Add assertions in persistence_harness after its complete champion checkpoint
construction: existing fork-aware logged_calls with that checkpoint must equal
ordinary logged_calls; assert c01/c02 execution ID sets disjoint and all shared
IDs exist with expected node/seq/status/NULL candidate using repository rows.
This adds ownership evidence to the existing successful preservation cases;
owned file therefore also includes `tests/test_nodes_persistence.py`, only
persistence_harness and necessary imports. Do not weaken any existing assertion.
Use existing list_node_executions(conn: sqlite3.Connection, run_id: str) ->
tuple[NodeExecutionRow, ...] from persistence.repository, build an ID-to-row
mapping once, and compare each shared boundary against its spine index+1,
node_name, result.status, and candidate_id None. Import TABULAR_SPINE from
domains.tabular for this expected ordering. For the two branches compare sets
of their result.execution_id values via isdisjoint; no manufactured rows.

Checks sequential, stop on first unexpected failure: scoped Ruff check, format
--check, mypy on all four owned test files; then `LOKY_MAX_CPU_COUNT=1 make
test-fast TESTS='-x tests/test_nodes_candidate_pipeline.py tests/test_nodes_champion.py
tests/test_nodes_persistence.py tests/test_domain_executor.py'`. Use unique c4
logs and reviewed socket escalation if required. Receipt exact paths/hashes,
commands/exits/counts/logs, deviations/hazards. No global gates, extra checks,
mutations, commits, plan edits, subagents or independent fixes.

### C4 recovery R1 — bounded resume after worker usage-limit error

Main inspected the partial edit on 2026-10-06. Only C4 step1 exists in
`tests/test_nodes_candidate_pipeline.py` (SHA256
`fb233d5b25786f26e80586ae39ccca3d7fcde1114721fd4663678bd82ea173c2`):
`candidate_harness` binds `design_tools` from `prepared` and, after the
completed-design assertion, extends `h.tools.calls` with `design_tools.calls`.
This matches step1 exactly; keep it unchanged. It is unaccepted until C4 checks pass.
C1–C3 file hashes match the handoff; do not touch or redo them.

Fresh implementer assignment: apply C4 steps 2–7 and the persistence_harness
assertions exactly as written above. Owned files: `tests/test_nodes_champion.py`,
`tests/test_domain_executor.py`, `tests/test_nodes_persistence.py`; touch
`tests/test_nodes_candidate_pipeline.py` only if a C4 check reports a defect in
step1, and then stop and report instead of fixing. Then run the C4 checks in
order: scoped Ruff check, Ruff format --check, scoped mypy on all four owned
test files, then the C4 runtime selector with `LOKY_MAX_CPU_COUNT=1`. Use
`/private/tmp/statlab-offline-20261005-c4-*.log`. Stop conditions, prohibitions
and receipt fields are unchanged from C4.

Runtime note: a Claude Code session on HEAD `96da7a8` (adds only
`.claude/settings.json`; no product input change) cannot spawn the pinned
`implementer` role (`gpt-6-luna`/low, `.codex/agents/implementer.toml`).
Per AGENTS.md it reported the mismatch and did not substitute. AGENTS.md now
authorizes a pinned Claude Code role (`.claude/agents/implementer.md`, `sonnet`/low)
beside Codex; resume delegation from either runtime with its own pinned role.

#### R1 clarifications (main, verified against source before delegation)

* Step 2 `create_artifact` (`backend/statlab/storage.py:51`): pass `split=meta.split`
  and `derived_from_test=(meta.split == "test")` explicitly; other kwargs as listed.
* Step 3 row creation: `create_node_execution(conn, run_id=..., node_name=boundary.node_name,
  seq=..., candidate_id=None, attempt=next_node_attempt(conn, run_id=..., node_name=...,
  candidate_id=None))` (repository.py:349/531), then `finish_node_execution(conn, new_id,
  boundary.result)` (repository.py:716). `seq` = enumerate index of
  `h.state.shared_results` starting at 1; first assert each shared boundary's node_name
  equals `TABULAR_SPINE[index].name` for that index (shared results are the spine prefix).
* Step 3 evidence shape (verified `domains/foundation.py:222`): `summary["tools"]` is a
  list of `{"tool": str, "result": ToolSuccess JSON dump}`.
* Step 5 c02 database ID before results exist: build
  `CandidateProgress(public_id="c_02", spec=spec)` (all other fields default-valid),
  take its `database_id(h.state.run_id)`, call the existing `ensure_candidate` with that
  ID/run/spec before the copy loop, and use the same ID as candidate_id for each c02
  `next_node_attempt`/`create_node_execution`. `seq` for each copied boundary = 1 + index
  of its node name in `TABULAR_SPINE`. Then build the final `second` exactly as today.
* Loopback: if the runtime selector fails only on loopback socket permission, rerun the
  identical command once with the sandbox disabled (test-owned 127.0.0.1 MCP fixtures
  only) and report both attempts. Any other failure: stop and report.

#### C4a — persistence shared-row seq rule (main correction)

Worker stopped correctly: persistence_harness shared_results are the six-node
spine prefix plus the appended champion_selection boundary (spine index 11,
created by `tail_invocation` with seq 12), so enumerate index is wrong at item 6.
Spec defect, not product defect. Log `...-c4-runtime.log` (exit 2, `-x`).
Statics on all four files exit 0. Reviewed diff otherwise matches C4 steps 2–7;
removing the unused `_ref`/`get_artifact`/`TEST` imports is an accepted mechanical
deviation (Ruff F401).

Recipe, `tests/test_nodes_persistence.py` persistence_harness only: in the
per-shared-boundary loop drop `enumerate`; for each shared boundary compute
expected seq = 1 + index of `shared.node_name` in the tuple of `TABULAR_SPINE`
names; assert `row.node_name == shared.node_name`, `row.seq == expected seq`,
`row.status == shared.result.status`, `row.candidate_id is None`. Keep all other
assertions unchanged. Rechecks: Ruff check, Ruff format --check and mypy on
`tests/test_nodes_persistence.py`, then the unchanged C4 runtime selector
(log `...-c4a-runtime.log`).

#### C4 receipt (main-reviewed, focused)

Implementer (Claude Code `implementer`, sonnet/low) applied C4 steps 2–7, persistence
assertions and C4a. Main reviewed complete diffs of all four files against C4/R1/C4a.
SHA256: candidate_pipeline `fb233d5b…a173c2`, champion `6e1dffa9…77f5cf`,
persistence `0fbffc19…9c7428e`, domain_executor `3820c6ab…595646`.
Ruff check/format --check/mypy: four files exit 0 (`...-c4-{ruff,format,mypy}.log`);
C4a recheck on persistence exit 0 (`...-c4a-*.log`). Runtime selector
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='-x <four files>'` exit 0: 26 passed, 0
failed/skipped (counted `-q` markers; config prints no summary line), no loopback
escalation needed. Log `/private/tmp/statlab-offline-20261005-c4a-runtime.log`.
Accepted deviations: unused-import removal, line-length reshaping, loop-variable
renames. C4 focused evidence accepted; global gate still outstanding.

## Correction C5 — Q&A unavailable-reference failure must satisfy Failure contract

Ordered check 2 exit 2 after 33 passes (`...-control-2.log`):
`tests/test_control_qa_status.py::test_qa_invented_well_formed_reference_rejected_without_repair_layer`.
`answer_question` (`backend/statlab/graph/control.py:354-357`) raises
`LlmOutputError(Failure(error_class="tool_param", message=...))` without
`field_errors`; `Failure._field_errors_iff_tool_param`
(`contracts/statlab_contracts/envelope.py:66-79`) rejects that, so Pydantic
`ValidationError` escapes instead of the intended domain `LlmOutputError`.
Product defect, reachable whenever the model cites a well-formed but unavailable ref.
Only offending site: every other backend `tool_param` construction supplies
`field_errors`. Precedent: `llm/client.py:218-225` keys LLM output field errors by
the field path (`_validation_errors`, client.py:565). Contract stays unchanged.

Owned: `backend/statlab/graph/control.py` (`answer_question` raise only) and
`tests/test_control_qa_status.py`
(`test_qa_invented_well_formed_reference_rejected_without_repair_layer` only).

Recipe:
1. control.py: in that `Failure(...)` keep `error_class="tool_param"` and the
   message `"Q&A cited unavailable artifacts"` unchanged; add
   `field_errors={"references": "cites artifacts unavailable to this question"}`.
   No other change (no new helpers, no computed lists of offending refs).
2. Test: bind the `pytest.raises(LlmOutputError, match="unavailable artifacts")`
   context as `excinfo`; after the block add
   `assert excinfo.value.failure == Failure(error_class="tool_param",
   message="Q&A cited unavailable artifacts",
   field_errors={"references": "cites artifacts unavailable to this question"})`
   (typed kwargs; `Failure` already imported). Keep the existing
   `len(client.calls) == 1` assertion.

Checks, sequential: Ruff check, Ruff format --check, mypy on both owned files;
then `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='-x tests/test_control_qa_status.py'`
(log `...-c5-qa.log`). Main then reruns ordered check 2.

#### C5 receipt (main-reviewed, focused)

Diff matches recipe exactly. SHA256 control.py `ba99891b…9bfde8`,
test_control_qa_status.py `719b8435…6c713b8b`. Ruff/format/mypy exit 0
(`...-c5-{ruff,format,mypy}.log`; worker's first static run passed both paths as one
quoted argument, exits 1/2/2, rerun correctly — invocation error, not product).
`-x tests/test_control_qa_status.py` exit 0, 22 passed (`...-c5-qa.log`). Accepted.

## Correction C6 — gate options empty for PEP 695 `GateAction` alias

Ordered check 2 rerun (`...-c5-control-2.log`) exit 2 after 72 passes:
`tests/test_control_gates.py::test_pre_action_override_keeps_actual_mro_type_and_avoids_new_proposal[approve]`
raised `EngineError("gate action has no safe continuation")` (gates.py:292-293).
This file was authored but never executed in B12; first runtime exposure.
Root cause: `GateService.approval` (`backend/statlab/graph/gates.py:201`) sets
`options=get_args(GateAction)`. `GateAction` is a PEP 695 `type` alias
(`contracts/statlab_contracts/gates.py:9`), a `TypeAliasType`; verified
`get_args(GateAction) == ()` in the project venv. Every approval gate therefore
offers no actions and rejects every answer. Product defect. Only affected
`get_args` site: every other backend/contract `get_args` target is a plain alias.
Contract stays unchanged (no alias-form churn across the contract package).

Owned: `backend/statlab/graph/gates.py` (line 201 only) and
`tests/test_control_gates.py`
(`test_pre_action_override_keeps_actual_mro_type_and_avoids_new_proposal` only).

Recipe:
1. gates.py:201: change the argument to `get_args(GateAction.__value__)`. Nothing else.
2. Test: immediately after `assert question.decision_type.endswith(".Proposal")`
   add `assert question.options == ("approve", "approve_with_edits", "instruct",
   "approve_all_remaining", "skip_node", "rewind", "abort")` (declaration order).
   No other test change.

Checks, sequential: Ruff check, Ruff format --check, mypy on both owned files
(separate path arguments); then
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_control_gates.py'` without `-x`
(log `...-c6-gates.log`) so all never-executed gate cases report at once. If mypy
rejects `GateAction.__value__`, or any gate case fails, stop and report the full
failure list; do not fix. Main then reruns ordered check 2.

#### C6a — after-action gate fixture must not create pre-start history

C6 statics exit 0 (mypy accepts `GateAction.__value__`); target gate case passes.
Full `tests/test_control_gates.py` (no `-x`): 15 passed, 1 failed (`...-c6-gates.log`):
`test_after_action_acknowledgment_retains_artifacts_without_executor_replay` fails on
its FIRST `lifecycle.start()` (`load_state`, engine.py:160-161) with
`PersistenceError("missing checkpoint for run with durable history")`.
Cause: the test calls `artifact(engine_world)` (tests/test_graph_engine.py:106,
registers an `artifacts` row) before the run starts; `has_run_history`
(repository.py:545) correctly treats any artifact row as durable history, so a
checkpoint-less run must not initialize. Fixture defect; production guard stays.
Real artifacts are produced during node execution, so create it there.

Owned: `tests/test_control_gates.py`, that test only, plus the import below.
Recipe:
1. Import `ArtifactMeta` from `statlab_contracts.artifacts` (sorted with the
   existing `statlab_contracts` imports).
2. Replace `output = artifact(engine_world)` with
   `outputs: list[ArtifactMeta] = []`.
3. In `execute`, after `calls += 1`: `output = artifact(engine_world)`, then
   `outputs.append(output)`, then the existing `log_outputs(...)`, approval and
   return, unchanged (they keep using local `output`).
4. After `state = await lifecycle.start()` (second start), add
   `(output,) = outputs` before the existing assertions; keep all assertions.
Checks: Ruff check, format --check, mypy on `tests/test_control_gates.py`; then
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_control_gates.py'` without
`-x` (log `...-c6a-gates.log`). Any failure: stop, report full FAILED list.

#### C6/C6a receipt (main-reviewed, focused)

gates.py `2076dd46…f7b07`, test_control_gates.py `7536db5f…3359e`. Statics exit 0
(`...-c6-*.log`, `...-c6a-*.log`; worker re-ran statics once after a quoting error).
`tests/test_control_gates.py` (no `-x`) exit 0, 16 passed (`...-c6a-gates.log`). Accepted.

## Correction C7 — awaiting recovery regression and pre-start fixture history

Check 2 rerun (`...-c6-control-2.log`) stopped at rewind; main then ran the remaining
check-2 files without `-x` (`...-control-2-rest.log`): 308 passed, 15 failed, three causes.

A. Product regression, `backend/statlab/graph/reconciliation.py`
`reconcile_projects` projection (`if row.status in ("paused", "cancelled", "superseded"):`).
Chunk 23 (B7, runtime-accepted) requires SQL `awaiting_user` dormant even when the
checkpoint says running, and awaiting requires its retained pending result. B7/B8 projected
`("paused", "awaiting_user")`; B12 `ccdb476` replaced the tuple and dropped `awaiting_user`.
Failures: `test_restart_reconciliation.py::test_awaiting_republishes_actual_pending_gates_only`
(task scheduled instead of dormant) and `::test_awaiting_without_pending_result_fails_closed`
(projection without pending result must raise RunState ValidationError, a ValueError caught
into `PersistenceError("restart preparation failed")`). Recipe: change that tuple to
`("paused", "awaiting_user", "cancelled", "superseded")`. Nothing else. Gate reconciler
re-reads the row before this point, so an answered gate already moved off awaiting.

B. Fixture defect, `tests/test_control_rewind.py` (four tests; never executed in B12).
Each writes durable history (artifact rows, test fingerprint, test-scoring claim) before
the first `lifecycle.start()`; `has_run_history` (repository.py:545) correctly refuses a
checkpoint-less start. Move those writes into the producing node's execution, the C6a
pattern. `ArtifactMeta` is already imported.
1. `test_completed_parent_fork_hardlinks_exact_refs_and_never_rewrites_ancestor`:
   replace `output = artifact(engine_world)` with `outputs: list[ArtifactMeta] = []`;
   in `producing`'s prefix branch, first `output = artifact(engine_world)` then
   `outputs.append(output)`, then the existing log/return; after
   `source = await lifecycle.start()` add `(output,) = outputs`. Keep the comment and
   all assertions.
2. `test_missing_retained_file_rejects_before_child_creation`: same pattern in
   `execute`'s prefix branch; add `(output,) = outputs` right after
   `await lifecycle.start()`.
3. `test_retained_split_inherits_fingerprint_without_inheriting_claim`: keep
   `metas`, `roles` and `fingerprint` declarations before `execute`. Move the whole
   `for role in roles:` artifact-creation loop, then `set_test_fingerprint(...)`, then
   `assert claim_test_scoring(...)` (unchanged statements, same order) into the
   `split` branch before its `log_outputs`. Keep all later assertions.
4. `test_fork_after_champion_keeps_score_provenance_and_independent_report`: replace
   `fitted = artifact(engine_world, "model")` with `fitted_outputs: list[ArtifactMeta] = []`.
   In the `train` branch, first `fitted = artifact(engine_world, "model")` and
   `fitted_outputs.append(fitted)`, then the existing `record_tool_call`/return. At the
   start of the `champion_selection` branch add `(fitted,) = fitted_outputs`. Keep
   `score`, `meta` and the report-file write before `execute` (pure values/filesystem,
   not SQL history). Move `score_path` write, the score `create_artifact(...)`, and
   `set_test_fingerprint(...)` into the `champion_selection` branch, in that order,
   immediately before `assert claim_test_scoring(...)`. Statements unchanged.

C. Fixture defect, `tests/test_mcp_client.py` (B13 tests; accepted B6 discovery
contract requires `properties`). Replace every `served_tool(` schema literal
`{"type": "object"}` (six sites: in `test_setup_dataset_requires_exact_metadata`,
`test_setup_dataset_schema_failure_and_tool_failure_are_preserved` second session,
`test_setup_dataset_preserves_cancellation`,
`test_setup_dataset_rejects_malformed_results_and_artifacts`,
`test_setup_dataset_maps_foreign_dispatch_failures_without_audit`,
`test_setup_dataset_missing_tool_fails_before_dispatch`) with
`{"type": "object", "properties": {}}`, the passing sibling's form. No other change.
Assert exactly six replacements.

Checks, sequential: Ruff check, Ruff format --check, mypy on the four owned files
(separate path arguments); then without `-x`:
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_restart_reconciliation.py tests/test_control_rewind.py tests/test_mcp_client.py'`
(log `...-c7-runtime.log`). Any failure: stop, report full FAILED list. Main then reruns
complete ordered check 2.
C7-C clarification: the six sites are exactly lines 209, 241, 258, 273, 322 and 339 in
the six named functions. Do NOT touch `test_invalid_discovery_metadata_and_schema_fail_context_entry`
(lines ~802/811): its bare schemas intentionally prove discovery rejection.

#### C7 receipt (main-reviewed) and check 2 acceptance

Diffs match C7 A/B/C exactly; `ruff format` only rewrapped the six edited `served_tool`
calls (exactly six `"properties": {}` additions; lines ~802/811 untouched). SHA256
reconciliation.py `38a8231a…f79f864`, test_control_rewind.py `71643127…ee566a`,
test_mcp_client.py `f681ca23…a918c`. Statics exit 0; focused three files exit 0, 141 passed.
**Ordered check 2 accepted:** full selector without `-x` exit 0, 405 passed, 0 failed/skipped
(`...-c7-control-2.log`). Domain check 1 accepted earlier: prefix 279 + champion 9 (C3) +
C4 26 + downstream 12 (`...-c4-downstream.log`, exit 0).

## Check 3 findings and probe P1 (diagnostic only)

Check 3 without `-x` hung (killed by main after 10 min; `...-api-3.log`): projects 18 and
api_runs 25 collected; `test_api_projects.py::test_create_list_detail_and_csv_upload`,
`test_api_runs.py::test_gate_action_outcomes[abort]`,
`test_api_runs.py::test_continuation_capacity_preserves_answered_gate` fail
(`...-api-3-fail.log`); `test_message_decision_and_change[decision]` hangs (event loop idle;
faulthandler `...-api-3-hang.log`; `asyncio pstree` needs root; SIGINT output swallowed).
Static diagnosis so far:
* projects: test GETs `/api/projects/missing/`; FastAPI default slash redirect gives 307.
  Spec route is `/api/projects/{project_id}` with missing → 404. Test-path defect (fix later).
* continuation: 409 at line 840 (`resume`). Chunk 34 requires capacity-denied accepted gate
  to leave SQL `pending` so `_resume` schedules start; `_resume` refuses `running` without a
  task. `GateService._prepare` (gates.py:463-470) sets `running`. Decision pending P1 facts.
* abort, decision hang: not explained statically; need runtime facts.

Probe P1 (implementer-owned, temporary, `tests/test_api_runs.py` only, restored exactly):
record the file's SHA256 first (HEAD blob, unmodified). Temporarily:
1. `test_message_decision_and_change`: bound the messages POST and, in the decision
   branch, the `executor.next_entered.wait()` each with `asyncio.wait_for` timeout 20 s.
   On `TimeoutError` print (flush) one line prefixed `PROBE-<label>` containing: parent
   SQL run status; list of (gate status, gate id) for parent; whether
   `api.registry.execution_tasks` has parent and that task's `done()`; if done: cancelled
   flag, else `repr(task.exception())`, else result's status and cursor;
   `parent in api.registry.failed_tasks`; `executor.body_count`; `first_entered`/`next_entered`
   `is_set()`; binding lifecycle `_running`; lifecycle state status/cursor and whether
   `pending_result is None`; `len(api.records.requests)`; then re-raise.
2. `test_gate_action_outcomes` abort branch: before `assert run_id not in
   api.registry.execution_tasks`, print `PROBE-abort` with that task's name, `done()`,
   and if done and not cancelled and no exception: result status/cursor; else the exception repr.
3. `test_continuation_capacity_preserves_answered_gate`: before `assert resumed.status_code
   == 200`, print `PROBE-cont` with resumed status code, response JSON, first run SQL status,
   gate statuses, and whether an execution task exists/done for first id.
Run once: `PYTHONUNBUFFERED=1 LOKY_MAX_CPU_COUNT=1` with the 300 s subprocess wrapper,
`.venv/bin/python -m pytest -s --no-cov -p no:cacheprovider` the three node IDs
(`test_message_decision_and_change[decision]`, `test_gate_action_outcomes[abort]`,
`test_continuation_capacity_preserves_answered_gate`), log `...-p1-probe.log`. Timeout of the
wrapper is acceptable (teardown may hang) — probe lines are the goal. In `finally`, restore with
`git checkout -- tests/test_api_runs.py` and assert SHA256 equals the recorded value.
Report all PROBE lines verbatim, exit code and restoration hash. No fixes.

#### Probe P1 result and probe P2

P1 (restored; SHA256 back to `4bae32da…26231`) printed no PROBE line before the 300 s wrapper
timeout (`...-p1-probe.log`). faulthandler shows only the main thread, idle in `select`, so
`wait_for` at the probed awaits would have fired: the decision case blocks at an unprobed
step — `_pause_release(api, parent)` or `_open_run_api` teardown (lifespan drain).

Probe P2 (implementer-owned, temporary, `tests/test_api_runs.py` only, same SHA256 guard and
`git checkout` restoration as P1). In `test_message_decision_and_change` only:
1. Print (flush) `PROBE-step <name>` immediately after each of: `_create_run`, `_gate`,
   the messages POST, decision-branch `next_entered.wait()`, the decision-branch audit
   assertions (just before `_pause_release`), `_pause_release`, and as the last statement
   inside the `async with _open_run_api(...)` block.
2. Wrap the decision-branch `_pause_release(api, parent)` call in `asyncio.wait_for` with
   timeout 20 s; on `TimeoutError` print `PROBE-pause` with: parent SQL status; executor
   `runtime.tools.control.status`; `next_entered`/`next_release` `is_set()`; parent execution
   task present/`done()`; binding lifecycle `_running` and `_stopping`; lifecycle state status
   and cursor; then re-raise.
Run only `"tests/test_api_runs.py::test_message_decision_and_change[decision]"` with the same
P1 command shape (`-s`, `PYTHONUNBUFFERED=1`, 300 s wrapper), log `...-p2-probe.log`. Restore
in `finally`, verify SHA256. Report all PROBE lines verbatim, exit, restoration hash. No fixes.

#### Probe P2 result and probe P3

P2 (restored to `4bae32da…26231`) printed `create_run`, `gate`, `post`, `next_entered`, then
nothing (`...-p2-probe.log`). So one decision-branch assertion between `next_entered` and the
audit block (test lines ~1260-1276) fails; the raised AssertionError exits `_open_run_api`
whose lifespan drain then waits forever on the still-blocked fixture executor
(`next_release` never set). The "hang" is a failed assertion plus non-released fixture latch.

Probe P3 (same guard/restoration rules, decision test only): immediately after the decision
branch's `await executor.next_entered.wait()`, print (flush) `PROBE-values` with:
`executor.body_count`; every parent gate as (id, status) and the target `gate.id`; the exact
audit query's rows (all three columns, as list); `len(api.records.requests)`; and for each
recorded request its `response_format`/output signature if present else the sorted kwargs keys.
Then set `executor.next_release` (so teardown can drain) before the existing assertions run.
Command as P2 but wrapper timeout 120 s, log `...-p3-probe.log`. Report PROBE lines verbatim,
the pytest failure line/assertion text if printed, exit and restoration hash. No fixes.

#### Probe P3 result and probe P4

P3 (restored `4bae32da…26231`; `...-p3-probe.log`, 1 failed in 2.22 s): after approval via
message the node re-executed once (`body_count=1`), next node entered, audit rows correct
(IntakeDecision/InterruptClass/GateAnswer, all parent-owned, 3 requests), but the gate is
still `prepared`; test line 1280 expects `applied` (chunk 34 §5 and abort case say applied).
No code transitions `prepared → applied` after the override is consumed (gates.py:156-174
`override`, only `_external`/skip/after paths write `applied`). Product gap; design below.

P4 = P1 items 2 and 3 only (abort and continuation probes), same guards/restoration, command
with the two node IDs `"tests/test_api_runs.py::test_gate_action_outcomes[abort]"` and
`tests/test_api_runs.py::test_continuation_capacity_preserves_answered_gate`, wrapper 120 s,
log `...-p4-probe.log`. Additionally in the abort probe print the task's `get_coro()`
qualname, `id(task)`, all `execution_tasks` keys, and `parent in failed_tasks`; if done
with exception print its repr.

#### Probe P4 result

P4 (restored `4bae32da…26231`; `...-p4-probe.log`, 2 failed in 1.78 s):
* `PROBE-abort`: the lingering entry is the ORIGINAL start task (`RunLifecycle.start`,
  done, result `awaiting_user` cursor 0, not failed). Only `_adopt` inserts; `_completed`
  (done callback, `call_soon`) deletes. The abort request path completes without ever
  suspending on the loop, so the already-scheduled callback has not run when the test
  asserts. Registry semantics everywhere use `not task.done()`; chunk 34 requires only "no
  unfinished task" (and uses `is None or done` elsewhere). Test assertion is stricter than
  the contract: test defect.
* `PROBE-cont`: after capacity-denied approve, SQL `running`, gate `prepared`, no task;
  `_resume` correctly refuses `running` without an owned task (chunk 34). Chunk 34 requires the
  accepted continuation to be SQL `pending`; `GateService._prepare` writes `running`. Product.

## Correction C8 — gate continuation status, applied transition, API test defects

Owned: `backend/statlab/graph/gates.py` (`_prepare` status expression; `capture`),
`tests/test_api_projects.py` (`test_create_list_detail_and_csv_upload`, one line),
`tests/test_api_runs.py` (`test_gate_action_outcomes` abort branch, one assertion).

A. gates.py `_prepare` (~line 463-470): in the final `status = (...)` conditional expression,
   change the last fallback literal from `"running"` to `"pending"` (line ~469 only; NOT the
   candidate `"running"` at ~447). Result: accepted continuation is SQL/checkpoint `pending`
   (paused stays paused, awaiting stays awaiting). `_continue` already schedules pending;
   `RunLifecycle.start` and `RunLoop._run` accept pending and move to running; restart treats
   pending as runnable. Satisfies chunk 34 §5 continuation capacity.
B. gates.py `capture`: replace the leading early return with this order:
   if `result.status != "awaiting_user"`: iterate `list_gates(self.conn, self.run_id)`; for each
   gate, ONE combined `if` (single branch pair, `and`-chained, short-circuit order exactly):
   `gate.status == "prepared"`, `gate.answer_json is not None`,
   `(question := self._validate(gate)).phase == "before"`,
   `question.node == invocation.spec.name`,
   `question.candidate_id == invocation.execution.candidate_id`,
   `question.context == self._context(invocation)`; when true call existing
   `transition_gate(self.conn, gate, status="applied", answer_json=gate.answer_json)`.
   After the loop `return`. The existing awaiting-user body below stays unchanged.
   Meaning: once the node attempt that consumed the accepted pre-action proposal returns a
   non-awaiting result, the gate is `applied` (failure gates offer no same-context retry, so
   failed also consumes it). Hook receives the same snapshot invocation as the executor, so
   `_context` equals the override match. Known ceiling (deliberate, not fixed): a crash after
   this write but before the boundary commit re-gates that node on restart; no work replays.
C. test_api_projects.py line ~381: replace `client.get("/api/projects/{}/".format("missing"))`
   with `client.get("/api/projects/missing")`; keep `== 404`.
D. test_api_runs.py abort branch: replace `assert run_id not in api.registry.execution_tasks`
   with binding `task = api.registry.execution_tasks.get(run_id)` then
   `assert task is None or task.done()`.

Checks: Ruff check, format --check, mypy on the three owned files (separate args); then
without `-x`: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_control_gates.py
tests/test_api_projects.py tests/test_api_runs.py'` (log `...-c8-runtime.log`). The worker's
make wrapper is 900 s; if it times out, report it (a hang means an assertion failed in an API
test whose latch stayed blocked). Any failure: stop, report FAILED list. Main then reruns
complete ordered check 3.

#### C8a — later API tests first reached (restart/shutdown fixture defects)

C8 diff matches A–D (gates.py `d7c6bf78…af54`, test_api_projects.py `039d326c…966707`,
test_api_runs.py `74e298d7…c7f059`); statics exit 0. Runtime (`...-c8-runtime.log`):
56 passed, 3 failed — all three tests were never reached before (earlier hang):
* `test_memory_snapshot_restart_and_shutdown_pause` (line 1376) and
  `test_autonomous_paused_restart` (line 1430) assert `run_id not in records.executors` after
  restart. Chunk 34 composition (spec lines 175-185) constructs `TabularExecutor(runtime)`
  inside every binding, and restart must resolve bindings for dormant awaiting/paused runs
  (tests read `registry.bindings[run_id]`). The fixture factory records construction, not
  entry; spec "NO tasks/executor entry" means the executor body never ran. Test proxy
  defect. Truthful proof: no execution task (kept) and no LLM request (`runtime.decide` is
  the first thing the fixture body does on the intake node; fresh FixtureRecords are empty).
* `test_shutdown_drains_setup_upload_and_retrieves_failed_task` (line 1592): `_open_run_api`
  enters `app.router.lifespan_context(app)` then `httpx.AsyncClient(...)`; exit order closes the
  client before lifespan shutdown drains, so the in-shutdown 503 probe cannot be sent. Fixture
  ordering defect.

Recipe, `tests/test_api_runs.py` only:
1. `_open_run_api`: in the combined `async with (...)`, put the `httpx.AsyncClient(...) as
   client` item first and `app.router.lifespan_context(app)` second. Nothing else.
2. `test_memory_snapshot_restart_and_shutdown_pause` restart block: delete the two
   `... not in api.records.executors` assertions; after the remaining
   `second_id not in api.registry.execution_tasks` assertion add
   `assert api.records.requests == []`. Keep all other assertions.
3. `test_autonomous_paused_restart` restart block: replace
   `assert run_id not in restarted.records.executors` with
   `assert restarted.records.requests == []`.
Checks: Ruff check, format --check, mypy `tests/test_api_runs.py`; then without `-x`
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_api_projects.py tests/test_api_runs.py'`
(log `...-c8a-runtime.log`). Any failure: stop and report FAILED list with `E` lines.

#### C8/C8a receipt and check 3 acceptance

C8a diff matches recipe (client/lifespan order swapped; two executor-dict assertions replaced
by `requests == []`). test_api_runs.py `6ff0c491…ab6396`; statics exit 0 (`...-c8a-*.log`).
**Ordered check 3 accepted:** full selector without `-x` exit 0, 183 passed, 0 failed/skipped
(`...-c8a-api-3.log`). Probes P1-P4 all restored to the recorded HEAD hash before C8.

## Correction C9 — check 4 first-run test defects

Check 4 without `-x` (`...-stream-4.log`): 68 passed, 7 failed; tests never executed before
(B14 static-only). Product code matches its spec in all three causes; fixes are test-only.
1. `test_api_stream.py` lines 225/256, 288/314, 455/486, 504/570 snapshot
   `dict(api.registry.execution_tasks)` and require equality afterwards. The snapshot can hold
   a finished start task whose `_completed` done-callback (call_soon) has not yet run; it is
   removed later (same artifact as C8-D). Intent: streaming starts/stops no execution task.
2. Two signal-hook tests: `_open_run_api` monkeypatches `statlab.main.RunRegistry` to a capture
   function; `_RunServer.handle_exit` (main.py:182) correctly does
   `isinstance(registry, RunRegistry)` against the real class in production, but the active
   patch makes `isinstance` raise TypeError. Fixture interference.
3. `test_llm_stream.py`: (a) `_partial_text('{"text":"\\u00')` returns `''` — the decoded prefix
   before an incomplete escape; chunk 35 line 213 requires incomplete Unicode fragments to
   "emit only decoded text", and `_consume_prose` emits nothing for `''`. Expectation `None`
   is over-specified. (b) malformed-repair case `('{"text": invalid}',)` has choices but no
   usage chunk, so per chunk 35 line 329 the SDK builder runs and estimates (3, 5); the `(0,0)`
   expectation applies only to choice-less streams. Realistic provider fixture (include_usage)
   ends with a usage chunk.

Owned: `tests/test_api_stream.py`, `tests/test_llm_stream.py` only.
Recipe:
A. test_api_stream.py: import `RunAPI` (from the existing `tests.test_api_runs` import block)
   and `RunRegistry` (add to the existing `from statlab.api.runs import RunConfig`). Add
   module-level helper after the imports' existing helpers, before `_drive_request`:
   `def _active_tasks(api: RunAPI) -> frozenset[str]` returning the frozenset of run IDs in
   `api.registry.execution_tasks` whose task is not `done()`. Replace each of the four
   `tasks_before = dict(api.registry.execution_tasks)` with `tasks_before = _active_tasks(api)`
   and each `assert api.registry.execution_tasks == tasks_before` with
   `assert _active_tasks(api) == tasks_before`. Assert exactly four of each.
B. In `test_server_signal_hook_signals_registry_before_uvicorn_exit` and
   `test_server_signal_hook_before_registry_publication`, immediately after the
   `monkeypatch.setattr(uvicorn.Server, "handle_exit", parent_exit)` line add
   `monkeypatch.setattr(main_module, "RunRegistry", RunRegistry)` (restores the real class
   for the hook under test only).
C. test_llm_stream.py `test_partial_text_waits_for_incomplete_or_non_text_values`: change the
   parametrization to `("content", "expected")` with cases `('{"text":"\\u00', "")` and
   `('{"other":"value"', None)`; signature adds `expected: str | None`; assertion
   `assert _partial_text(content) == expected`. Keep the test name.
D. test_llm_stream.py `test_empty_usage_only_and_malformed_streams_repair`: change the third
   case from `(('{"text": invalid}',), (0, 0))` to `(('{"text": invalid}', None), (3, 2))`
   (invalid text followed by the provider usage chunk). Nothing else.
Checks: Ruff check, format --check, mypy on both files; then without `-x`
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_api_stream.py tests/test_llm_stream.py tests/test_dev.py'`
(log `...-c9-runtime.log`). Any failure: stop, report FAILED list with `E` lines.

#### C9 receipt; checks 4 and 5 accepted

C9 diff matches A–D (worker ran `ruff format` on test_api_stream.py only). SHA256
test_api_stream.py `a146eeb3…a498e412`, test_llm_stream.py `f471bb9a…f8da0c`. Statics exit 0.
**Ordered check 4 accepted:** without `-x` exit 0, 75 passed, 0 failed (`...-c9-runtime.log`).
**Ordered check 5 accepted:** `npm test` (frontend, 900 s wrapper) exit 0: tests 11, pass 11,
fail/cancelled/skipped/todo 0 (`...-frontend-5.log`). Frontend sources unchanged since the
accepted B14 typecheck/build.

## Final gate attempt 1 (main) — failed; findings

`LOKY_MAX_CPU_COUNT=1 make -k check` on HEAD `e3d9315` + dirty C1–C9 tree
(`...-final-make-check.log`, candidate list `...-final-candidate.txt`): make exit 2.
mypy `Success: no issues found in 179 source files`; Ruff `All checks passed!`; format
`187 files already formatted`. Pytest+coverage hit the 900 s wrapper (TimeoutExpired) at ~41%
of 2818 collected, with failures at collection indexes 137/138/144/1038; no coverage TOTAL.
Diagnosis (main, read-only runs):
* 137/138 `test_candidate_fanout_tools.py::test_tool_boundary_drains_and_prevents_second_call`
  (B8 test) expects the stopped execution `running`; chunk 31 §3 (B12) deliberately requires a
  stopped attempt not stay falsely running (`RunLoop._stop_attempt` marks it failed). Stale test.
* Hang: `tests/test_main.py::test_main_exits_2_on_tool_settings_error` (MCP server `main()`)
  hangs alone too: the developer's gitignored `.env` (keys only inspected) supplies
  `STATLAB_MCP_TOKEN`; `ToolSettings` reads `.env` from cwd, so `main()` starts real uvicorn.
  Non-hermetic test (all five `main()` tests in that file read cwd `.env`).
* 144 `test_three_candidates_shared_splits_locked_test_and_one_champion_score` and 1038
  `test_graph_engine_tools.py::test_one_candidate_real_tools`: pass alone; deterministic failure
  (2/2) when `tests/test_api_stream.py::test_server_repeated_signals_preserve_lifespan_drain`
  runs earlier in the same process (per-test bisection over all 17 stream tests; also outside
  repo cwd, so not `.env`). Live MCP server: first `initialize` → "Closing SSE writer",
  "Request stream 1 not found", middleware `RuntimeError: No response returned.`
  Mechanism not yet identified → probe P5.

Probe P5 (implementer-owned, temporary new file `tests/test_zz_probe_p5.py`, deleted in
`finally`; no other file touched). Four sync test functions, each self-contained:
v1 constructs only `Settings(mcp_token=SecretStr("fixture-token"), api_host="127.0.0.1",
api_port=8111)`; v2 = v1 then `statlab.main._RunServer(FastAPI(), settings)`; v3 = v2 then one
`server.handle_exit(signal.SIGINT, None)`; v4 = v2 then exactly the polluter's three calls
(SIGINT, SIGINT, SIGTERM). For each variant run in a fresh process:
`LOKY_MAX_CPU_COUNT=1 .venv/bin/python -m pytest -q --no-cov -p no:cacheprovider
tests/test_zz_probe_p5.py::<variant> tests/test_candidate_fanout_tools.py::test_three_candidates_shared_splits_locked_test_and_one_champion_score`
with the 300 s wrapper, log `...-p5-<variant>.log`. Report per variant exit and victim
PASS/FAIL. Then delete the probe file and confirm `git status --short` shows it absent.

#### Probe P5 result

P5 (temporary file deleted; no other file touched): v1 Settings only PASS, v2 + `_RunServer`
construction PASS, v3 + one real `handle_exit(SIGINT)` FAIL, v4 polluter sequence FAIL
(`...-p5-v{1..4}.log`). Mechanism verified in installed `sse_starlette/sse.py` (imported by the
MCP stack): at import it replaces `uvicorn.Server.handle_exit` with `AppStatus.handle_exit`,
which sets process-global `AppStatus.should_exit = True` and never resets it; every later
in-process `EventSourceResponse` (the live loopback MCP server's SSE writer) then closes at
once. Test-isolation defect only: production signals mean process exit; the backend's own SSE
route does not use `sse_starlette`.

## Correction C10 — final-gate test isolation and stale B8 expectation

Owned: `tests/test_candidate_fanout_tools.py`, `tests/test_main.py`, `tests/test_api_stream.py`.
A. `test_candidate_fanout_tools.py::test_tool_boundary_drains_and_prevents_second_call`: change the
   expected node execution status in the assertion at line ~107 from `"running"` to `"failed"`
   (chunk 31 §3: a stopped attempt must not stay falsely running; `_stop_attempt`). Nothing else.
B. `test_main.py`: in each of the five tests that call `main()`
   (`test_main_exits_2_on_tool_settings_error`, `test_main_runs_uvicorn_and_returns_0`,
   `test_main_uses_the_configured_port`, `test_main_passes_the_built_app_to_uvicorn`,
   `test_main_missing_data_dir_permission_also_exits_2`) make `monkeypatch.chdir(tmp_path)` the
   first statement of the body (after the docstring when present), so `ToolSettings` cannot read a
   developer `.env`. Assert exactly five insertions. No other change.
C. `test_api_stream.py::test_server_repeated_signals_preserve_lifespan_drain`: add parameter
   `monkeypatch: pytest.MonkeyPatch`; first statement
   `monkeypatch.setattr(AppStatus, "should_exit", False)` (pytest restores the pre-test value at
   teardown, undoing the global flag the real `handle_exit` sets). Import
   `from sse_starlette.sse import AppStatus` in the third-party import group (sorted by Ruff).
   Keep all existing assertions.
Checks: Ruff check, format --check, mypy on the three files (separate args); then
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_api_stream.py tests/test_candidate_fanout_tools.py tests/test_graph_engine_tools.py tests/test_main.py'`
without `-x` (order places the former polluter before both victims; log `...-c10-runtime.log`).
Any failure: stop, report FAILED list with `E` lines. Main then reruns the final gate once.

#### C10 receipt and final gate attempt 2 (main) — NOT accepted (coverage)

C10 diff matches A–C (fanout_tools `1c81cb94…6656c`, test_main `23b79d18…a6783`, test_api_stream
`6edfe85a…f112f`); statics exit 0; focused 46 passed (`...-c10-runtime.log`).
`LOKY_MAX_CPU_COUNT=1 make -k check` (`...-final2-make-check.log`, candidate `...-final2-candidate.txt`):
make exit 2. Pytest: 2817 passed, 1 skipped (designed skip), 0 failed, no timeout. mypy 0/179,
Ruff check 0, format 0/187. **Coverage TOTAL 95.46%** (11361 stmts, 365 miss, 3318 branches,
275 partial) < required 100%. Gaps concentrate in B9–B14 modules never runtime-covered before
(api/projects 90%, api/runs 85%, api/stream 94%, domains/binding, candidate_models 83%,
candidate_pipeline 83%, candidate_search, champion 86%, decisions, design_inference, …; full
per-file missing lines in the log). Next: main reads every missing line/branch, classifies as
(a) reachable → exact test recipe or (b) unreachable defensive code → removal per AGENTS
(never manufacture unreachable paths); delegate in bounded stages; rerun final gate once.

## Coverage closure plan (after final gate attempt 2)

Classification rule (main, read-only): every missing line/branch in `...-final2-make-check.log`
was read. A guard is **reachable** when a concrete input to its public function/class (including a
corrupted durable row or checkpoint) reaches it; reachable guards get a negative test with the exact
message. Behavior never exercised (feature options, restart recovery, lifecycle callbacks) gets a
positive test asserting exact tool arguments/state. Code that a prior check provably makes
unreachable is removed (named per stage), never covered by bypassing validators. No coverage
suppression. Stages S1… run one at a time; each owns only the named test/source files.
Implementer checks never use coverage; main measures stage coverage read-only afterwards.

### Stage S1 — candidate preparation paths (`tests/test_nodes_candidate_pipeline.py` only)

Targets `backend/statlab/domains/candidate_pipeline.py` misses 56->50, 61-62, 64, 93, 104, 108,
113, 184, 241, 280->282, 283, 299, 323, 351, 354, 356, 358, 370, 381, 407->416, 409->408,
434->453, 437, 472, 485. All reachable; no source change.

Imports to add: `PipelineState` from `statlab.domains.decisions`; `FeatureSelection`,
`PreprocessingSpec` from `statlab_contracts.candidate` (also remove the function-local
`from statlab_contracts.candidate import FeatureSelection` inside
`test_train_decided_selection_replays_exact_drop_and_exception_waits`, using the module import);
`PreprocessingRules` from `statlab_contracts.contract`; `TABULAR_SPINE` already imported.

0. `candidate_harness`: add keyword parameter `preprocessing: PreprocessingSpec | None = None`
   after `feature`. Replace the condition `if feature is not None:` with
   `if feature is not None or preprocessing is not None:`; in the rebuilt `CandidateSpec` pass
   `preprocessing=candidate.preprocessing if preprocessing is None else preprocessing` and
   `features=candidate.features if feature is None else feature` (other fields unchanged).

Add these tests (all `@pytest.mark.asyncio`, args `migrated_db, project, tmp_path` like peers;
"run node i" means `await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, i))`;
"run+save i" means run then `save_result(h, invocation, output)` as `prepare_candidate` does):

T1 `test_pipeline_from_state_skips_unsuccessful_and_rejects_malformed_boundaries`:
   `h, _ = await candidate_harness(...)`; `await prepare_candidate(h)`;
   `branch = h.state.branches[0]`; `expected = pipeline_from_state(h.state, branch)`.
   Local helper (closure) `with_result(result: NodeResult, execution_id: str) -> CandidateProgress`
   returning `CandidateProgress(public_id=branch.public_id, spec=branch.spec,
   position=branch.position, status=branch.status, results=(*branch.results,
   BoundaryResult(node_name="feature_engineering", execution_id=execution_id, result=result)))`.
   a) `NodeResult(status="failed", error=Failure(error_class="data", message="refused"))`,
      id `"failed-feature"` → `pipeline_from_state(h.state, <that>) == expected`.
   b) `NodeResult(status="completed", summary={"pipeline": {"train": "not-a-ref"}})`, id
      `"malformed-feature"` → `pytest.raises(EngineError, match="malformed candidate pipeline")`.
   c) `foreign = PipelineState(train=expected.train, valid=expected.valid, replay=expected.replay,
      feature_manifest=expected.feature_manifest, methodology=CandidateSpec(label="Ridge branch",
      rationale="Validation regularization comparison", estimator=EstimatorSpec(name="ridge"),
      varies_from_baseline=("estimator", "hyperparameters")))`; result
      `NodeResult(status="completed", summary={"pipeline": foreign.model_dump(mode="json")})`, id
      `"foreign-feature"` → raises `EngineError`, match `"disagrees with approved candidate"`.

T2 `test_executor_rejects_unbound_invocations`: `h, _ = await candidate_harness(...)`;
   `shared = TABULAR_SPINE[0]`; `execution = create_node_execution(h.runtime.conn,
   run_id=h.state.run_id, node_name=shared.name, seq=1, attempt=next_node_attempt(h.runtime.conn,
   run_id=h.state.run_id, node_name=shared.name, candidate_id=None))`;
   `pytest.raises(EngineError, match="pipeline requires a candidate")` around
   `await CandidatePipelineExecutor(h.runtime)(NodeInvocation(h.state, shared, execution, None))`.
   Then `pytest.raises(EngineError, match="pipeline executor cannot execute this node")` around
   run node 8.

T3 `test_preprocessing_contract_conflict_threshold_drops_and_outliers`:
   `h, tools = await candidate_harness(..., preprocessing=PreprocessingSpec(outliers="clip_iqr"))`.
   `contract = h.runtime.contract`; set `h.runtime = replace(h.runtime, contract=DataContract(
   target=contract.target, objective=contract.objective, features=contract.features,
   preprocessing=PreprocessingRules(drop_constant=False)))`.
   a) run node 6 (default decision `drop_constant=True`) → `status == "failed"`,
      `error.message == "constant drop conflicts with explicit contract"`, `tools.calls == []`.
   b) `h.llm.responses[PreprocessingDecision] = PreprocessingDecision(rationale="Contract keeps
      constants; drop sparse columns", drop_constant=False, high_missing_threshold=0.5)`;
      `tools.drops = ("x",)`; run+save 6 (assert completed). Assert:
      `"drop_constant_columns" not in [name for _, name, _ in tools.calls]`;
      the single `drop_high_missing_columns` params have keys exactly
      `{"threshold", "train", "target"}`, `threshold == 0.5`, `target == "y"`;
      `pipeline = pipeline_from_state(h.state, h.state.branches[0])`;
      `[s.tool for s in pipeline.replay] == ["drop_columns", "drop_columns", "apply_imputer",
      "apply_outlier_clipper"]`; `pipeline.replay[1].parameters == {"columns": ["x"]}`;
      exactly one recorded `drop_columns` call has `params["columns"] == ["x"]` (train reused);
      the single `fit_outlier_clipper` params have `method == "clip_iqr"`;
      `pipeline.feature_manifest["transforms"]` is a list whose entries' `"tool"` values are
      `["fit_imputer", "fit_outlier_clipper"]` (narrow with `isinstance` for mypy).
   c) `tools.drops = ()`; run+save 7 → completed (imputer-drop scan sees the clipper entry).

T4 `test_feature_stage_waits_and_refuses_skip`: harness; run+save 6. Set
   `h.state = h.state.model_copy(update={"mode": "guided"})`;
   `h.llm.responses[FeatureDecision] = FeatureDecision(rationale="Exception needs review",
   material_exceptions=("Rare level collapse",))`; `offset = len(tools.calls)`; run node 7 →
   `status == "awaiting_user"`, `summary["decision"]` is a dict with
   `["material_exceptions"] == ["Rare level collapse"]`, `len(tools.calls) == offset`.
   Then mode back to `"autonomous"` (same `model_copy`), response
   `FeatureDecision(rationale="Skip requested", skip_reason="No engineered features needed")`;
   run node 7 → failed, message `"approved encoding must execute; feature stage cannot skip"`.

T5 `test_frequency_encoding_has_no_supported_tool`: harness with
   `feature=FeatureSpec(encoding="frequency")`; run+save 6; run node 7 → failed, message
   `"frequency encoding has no supported tool"`.

T6 `test_feature_stage_replays_datetime_interactions_bins_and_ordinal_levels`: harness with
   `feature=FeatureSpec(encoding="ordinal", scaling="none", datetime_parts={"id": ("year",
   "month")}, interactions=(("x", "excluded"),), binned={"x": 3})`; then replace the contract
   (target/objective from current) with `features=FeatureRules(include=("x", "excluded"),
   id_columns=("id",), roles={"excluded": "ordinal"}, ordinal_levels={"excluded": ("lo", "hi"),
   "absent": ("a", "b")})`. `await prepare_candidate(h)`. With `calls = [(name, params) for _, name,
   params in tools.calls]` and "params minus frame" = dict without key `"frame"`:
   both `add_datetime_parts` calls (exactly 2) minus frame equal
   `{"target": "y", "column": "id", "parts": ["year", "month"]}`; both `add_interaction_terms`
   calls (exactly 2) minus frame equal `{"target": "y", "pairs": [["x", "excluded"]]}`; the single
   `fit_binner` params equal `{"columns": ["x"], "n_bins": 3, "train": <any>, "target": "y"}`
   (assert keys and the three fixed values); the single `fit_ordinal_encoder` params have
   `columns == ["excluded", "x__bin"]`, `level_order == {"excluded": ["lo", "hi"]}` and no
   `"seed"`; no `fit_scaler` call; replay tools of
   `pipeline_from_state(h.state, h.state.branches[0])` equal `["drop_columns", "apply_imputer",
   "add_datetime_parts", "add_interaction_terms", "apply_binner", "apply_ordinal_encoder"]`.

T7 `test_ordinal_without_categoricals_and_mutual_info_keep_selected_train`: harness with
   `feature=FeatureSpec(encoding="ordinal", scaling="none",
   selection=FeatureSelection(method="mutual_info", k=1))`; `await prepare_candidate(h)`. Assert no
   call named `fit_ordinal_encoder`; the single `select_by_mutual_info` params have `k == 1`,
   `seed == 42`, `target == "y"` and no `"threshold"`;
   `selected = next(out for name, out in tools.outputs if name == "select_by_mutual_info")`;
   `pipeline_from_state(h.state, h.state.branches[0]).train == selected.artifacts[0].ref`; replay
   tools equal `["drop_columns", "apply_imputer"]`.

T8 `test_after_action_acknowledgment_cannot_change_completed_decision`: harness; mode guided via
   `model_copy`; `tools.warning = "Imputer filled a sparse column"`; local
   `async def approve(inv: NodeInvocation, decision: BaseModel, evidence: tuple[JsonValue, ...])
   -> BaseModel` that asserts `inv.after_action` and returns
   `PreprocessingDecision(rationale="Changed after the actions ran")`;
   `h.runtime = replace(h.runtime, approval=approve)`; run node 6 → failed, message
   `"completed actions cannot be changed by exception acknowledgment"`, `artifacts == ()`.

Checks (exact, separate arguments):
`.venv/bin/python -m ruff check tests/test_nodes_candidate_pipeline.py`,
`.venv/bin/python -m ruff format --check tests/test_nodes_candidate_pipeline.py`,
`.venv/bin/python -m mypy tests/test_nodes_candidate_pipeline.py`,
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS=tests/test_nodes_candidate_pipeline.py` → log
`/private/tmp/statlab-offline-20261007-s1.log`. Any failure or recipe gap (e.g. an assertion's
premise false): stop and report exact `E` lines; do not change product code or weaken assertions.

#### S1 receipt and correction S1a

S1 accepted (`ff684b7c…c4ea1`; checks exit 0; 14 passed `...-20261007-s1.log`). Main focused
coverage: candidate_pipeline misses only 35-38 (covered by model tests) plus 184 and 407->416,
omitted from the S1 recipe. S1a adds T9
`test_feature_stage_rejects_malformed_checkpointed_transform_manifest`: harness; run+save 6;
`branch = h.state.branches[0]`; `old = branch.results[-1]`;
`pipeline = pipeline_from_state(h.state, branch)`; `bad = PipelineState(train=pipeline.train,
valid=pipeline.valid, replay=pipeline.replay, methodology=pipeline.methodology,
feature_manifest={"input_features": ["x"], "transforms": "malformed"})`; rebuilt
`CandidateProgress(public_id, spec, position, status from branch, results=(*branch.results[:-1],
BoundaryResult(node_name="preprocessing", execution_id=old.execution_id,
result=NodeResult(status="completed", summary={**old.result.summary,
"pipeline": bad.model_dump(mode="json")}, artifacts=old.result.artifacts))))`; set `h.state =
RunState.model_validate({**h.state.model_dump(mode="json"), "branches": [<rebuilt>.model_dump(
mode="json")]})`; `pytest.raises(EngineError, match="malformed feature transform manifest")`
around run node 7. Same four checks, log `...-20261007-s1a.log`.

S1a accepted (`bb1dfecb…b3089`; checks exit 0; 15 passed `...-s1a.log`).

### Stage S2 — candidate model paths

Owned: `backend/statlab/domains/foundation.py` (helper only), `backend/statlab/domains/
candidate_models.py` (two lines), `tests/test_nodes_candidate_pipeline.py` (harness only),
`tests/test_nodes_candidate_models.py`. Targets candidate_models misses 53-54, 59, 70, 78, 148,
152, 165, 177, 181-182, 196, 258, 265, 306, 355, 375, 384, 461, 487, 490, 496, 515, 531->535,
532->531.

Unreachable: candidate_models 354-355 (`problem.primary_metric is None`) — `problem_from_state`
already raises when the metric is unresolved; the guard exists only for mypy narrowing.
Source recipe:
- foundation.py: add, directly above `problem_from_state`,
  `def resolved_metric(problem: ProblemSpec) -> Metric:` — if `problem.primary_metric is None`
  raise `EngineError("automatic metric has not been resolved")`; else return it. In
  `problem_from_state` replace the inner `if problem.primary_metric is None: raise
  EngineError("automatic metric has not been resolved")` (after the profile re-validation) with
  the bare statement `resolved_metric(problem)`. (`Metric` already imported.) Its raise is
  covered by the foundation stage (unresolved profile metric), not here.
- candidate_models.py `_evaluation`: delete the two lines `if problem.primary_metric is None:` /
  `raise EngineError("missing resolved primary metric")`; insert `metric = resolved_metric(problem)`
  in their place; change `metrics.get(problem.primary_metric)` to `metrics.get(metric)` and the
  final `{problem.primary_metric: float(value)}` to `{metric: float(value)}`. Import
  `resolved_metric` from `statlab.domains.foundation` (existing import block). No other change.

Harness recipe (`tests/test_nodes_candidate_pipeline.py`):
- `CandidateTools.__init__`: add `self.threshold: JsonValue = 0.4`; the `select_threshold`
  branch returns `{"threshold": self.threshold}`.
- `candidate_harness`: add keyword `estimator: EstimatorSpec | None = None` after
  `preprocessing`; condition becomes `feature is not None or preprocessing is not None or
  estimator is not None`; rebuilt spec uses `estimator=candidate.estimator if estimator is None
  else estimator`.

Tests in `tests/test_nodes_candidate_models.py` (asyncio; peer fixture args; "run i"/"run+save i"
as in S1; "guided"/"autonomous" = `h.state = h.state.model_copy(update={"mode": ...})`; "failed
'msg'" = `status == "failed"`, `error is not None`, `error.message == msg`):

M1 `test_durable_decision_and_executor_binding_guards`: harness; `prepare_candidate`; run+save 8,
   run+save 9. `branch = h.state.branches[0]`; `last = branch.results[-1]`; `bad =
   CandidateProgress(public_id=branch.public_id, spec=branch.spec, position=branch.position,
   status=branch.status, results=(*branch.results[:-1], BoundaryResult(node_name=
   "model_evaluation", execution_id=last.execution_id, result=NodeResult(status="completed",
   summary={**last.result.summary, "decision": {"rationale": ""}}))))`.
   `pytest.raises(EngineError, match="malformed durable evaluation decision")`:
   `evaluation_decision(bad)`. `pytest.raises(EngineError, match="retune branch is not
   checkpointed")`: `await candidate_retune_decider(h.state, bad)`. Shared invocation exactly as
   S1 T2 (`TABULAR_SPINE[0]`, `create_node_execution`/`next_node_attempt`, candidate `None`) →
   `match="model executor requires a candidate"`; `candidate_invocation(h, 6)` →
   `match="model executor cannot execute this node"`.

M2 `test_training_refusals_and_exception_wait`: harness; `prepare_candidate`. Premise assert
   `h.state.branches[0].spec.estimator.params == {"choice": 0}`. `contract = h.runtime.contract`.
   a) guided; `TrainingDecision(rationale="Needs review", material_exceptions=("Unusual
      grid",))`; `offset = len(tools.calls)`; run 8 → `awaiting_user`, `len(tools.calls) ==
      offset`; autonomous.
   b) contract → `DataContract(target=contract.target, objective=contract.objective,
      features=contract.features, modelling=ModellingRules(tuning=Tuning(method="random")))`;
      default `TrainingDecision(rationale="Direct fit of approved estimator")`; run 8 → failed
      "random search has no supported tool"; restore `h.runtime = replace(h.runtime,
      contract=contract)`.
   c) `TrainingDecision(rationale="Change params directly", params={"choice": 1})` → run 8 failed
      "initial direct parameters differ from approved candidate".
   d) `TrainingDecision(rationale="Grid with fixed params", method="grid", params={"choice": 0})`
      → failed "grid decision must put fixed parameters in param_grid".
   e) `TrainingDecision(rationale="Grid without explicit grid", method="grid")` → failed
      "approved parameters require an explicit grid".
   f) `TrainingDecision(rationale="Direct with grid", param_grid={"choice": (0, 1)})` → failed
      "direct fitting cannot accept a parameter grid".
   Assert no `train_estimator` or `tune_estimator` call was recorded in the whole test.

M3 `test_multiclass_threshold_and_brier_refusals`: harness `target_type="multiclass"`;
   `prepare_candidate`; run+save 8. `EvaluationActions(rationale="Threshold on multiclass",
   threshold_objective="f1")` → run 9 failed "threshold/calibration curve requires binary
   classification". Then `EvaluationActions(rationale="Compatible validation")`; run+save 9.
   Replace problem: `index = next(i for i, b in enumerate(h.state.shared_results) if b.node_name
   == "problem_definition")`; `old = h.state.shared_results[index]`; `problem =
   problem_from_state(h.state)`; `brier = ProblemSpec.model_validate({**problem.model_dump(
   mode="json"), "primary_metric": "brier", "metric_rule": "explicit"})`; new boundary
   `BoundaryResult(node_name=old.node_name, execution_id=old.execution_id,
   result=NodeResult(status="completed", summary={**old.result.summary, "problem":
   brier.model_dump(mode="json")}, artifacts=old.result.artifacts))`; `h.state =
   RunState.model_validate({**h.state.model_dump(mode="json"), "shared_results":
   [<new>.model_dump(mode="json") if i == index else b.model_dump(mode="json") for i, b in
   enumerate(h.state.shared_results)]})`. Run 10 → failed "multiclass Brier permutation scorer is
   unsupported"; no `permutation_importance` call recorded.

M4 `test_binary_threshold_range_and_calibrated_diagnostic_refusals`: harness
   `target_type="binary", estimator=EstimatorSpec(name="logistic_regression", calibrate=True)`;
   `prepare_candidate`; run+save 8. `tools.threshold = 1.0`; `EvaluationActions(rationale=
   "Choose threshold", threshold_objective="f1")` → run 9 failed "selected binary threshold is
   unavailable or outside (0,1)". `tools.threshold = 0.4`; `EvaluationActions(rationale="CV
   requested", cross_validation=True)` → run 9 failed "train diagnostic tool cannot reproduce
   calibrated estimator"; no `cross_validate_estimator` call recorded.

M5 `test_evaluation_exception_wait_and_out_of_range_after_action_wait`: harness (continuous);
   `prepare_candidate`; run+save 8; guided. a) `EvaluationActions(rationale="Review actions",
   material_exceptions=("Skip CV",))`; `offset`; run 9 → `awaiting_user`, no new calls.
   b) `EvaluationActions(rationale="Score validation")`; `tools.metric = -1.0`; run 9 →
   `status == "awaiting_user"`, `metrics == {}`, `"Primary metric is outside its mathematical
   range" in output.warnings`, and a `score_regression` call was recorded.

M6 `test_interpretation_wait_skip_coefficients_and_unscaled_pdp`: harness
   `feature=FeatureSpec(scaling="none"), estimator=EstimatorSpec(name="decision_tree")`;
   `prepare_candidate`; run+save 8; run+save 9 (default actions). a) guided;
   `InterpretationDecision(rationale="Review", material_exceptions=("Leakage suspicion",))` →
   run 10 `awaiting_user`; autonomous. b) `InterpretationDecision(rationale="Skip",
   skip_reason="Not needed for this candidate")` → `status == "skipped"`, `skip_reason` equal,
   `summary["decision"]` dict whose `"skip_reason"` equals it. c)
   `InterpretationDecision(rationale="Coefficients", coefficients=True)` → failed "coefficient
   importance requires supported uncalibrated linear estimator". d)
   `InterpretationDecision(rationale="PDP", partial_dependence=("x",))` → `completed`; the single
   `partial_dependence` call params have keys exactly `{"model", "frame", "column"}`.

Checks (separate args): Ruff check and format --check on the four owned files; mypy on the four
owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_nodes_candidate_models.py
tests/test_nodes_candidate_pipeline.py tests/test_nodes_foundation.py tests/test_nodes_champion.py
tests/test_nodes_persistence.py'` → log `/private/tmp/statlab-offline-20261007-s2.log`. Stop on
any failure or false premise with exact `E` lines.

#### S2 receipt and correction S2a

S2 accepted: foundation `7057eb4a…9b386`, candidate_models `176371b9…29676`, pipeline tests
`0b3814b4…6c9`, model tests `f28e9025…98792`; checks exit 0 (`...-s2.log`). Main focused
coverage: candidate_pipeline 100%; candidate_models only 182->184 (grid, no `param_grid`, no
approved params → `tune_estimator` with `param_grid=None` = registry `default_grid`; valid path).
S2a adds M7 `test_grid_without_explicit_grid_uses_registry_default`: harness with
`estimator=EstimatorSpec(name="linear_regression")`; `prepare_candidate`;
`TrainingDecision(rationale="Registry default grid", method="grid")`; run 8 → `completed`; the
single `tune_estimator` call has `param_grid is None` and `metric == "rmse"`; the single
`train_estimator` call has `params == {"fit_intercept": False}`; `summary["cv_caveat"] ==
CV_CAVEAT` (import from `statlab.domains.candidate_models`). Owned: test_nodes_candidate_models
only. Checks: Ruff check/format --check/mypy on it; `make test-fast
TESTS=tests/test_nodes_candidate_models.py` → `...-s2a.log`.

S2a accepted (`be0951ac…f31d50`; checks exit 0; `...-s2a.log`).

### Stage S3 — gate restart recovery and guards

Owned: `backend/statlab/graph/gates.py`, `tests/test_control_gates.py`. Targets gates.py misses
83, 113, 115, 117, 162, 164->160, 182, 190, 215, 253->252, 312, 314, 349->351, 354, 358->362,
371, 376, 404, 413->499, 447-461, 492, 495-498, 521->520, 532-534, 539-540, 543->518, 547,
556-623, 638; contracts/gates.py 58. Never-executed behavior here: candidate-owned gate
continuation (447-461/492/495-498) and restart restoration of an unanswered gate missing from the
checkpoint (`_restore_pending`, all phases).

Unreachable, removed (source recipe, gates.py):
- `_prepare` signature becomes `_prepare(self, gate: GateRow, state: RunState, answer:
  GateAnswer) -> RunState`; delete its `if gate.answer_json is None: raise
  PersistenceError("accepted gate has no answer")` and the
  `answer = GateAnswer.model_validate_json(gate.answer_json)` line (both callers already parsed
  `answer`: `answer()` validated it; `reconcile` parsed it at its `answered` branch, where a null
  answer already fails validation). Keep `question = self._validate(gate)` first.
- Delete `if answer.action in ("abort", "rewind"): return state` in `_prepare` (both callers
  route abort/rewind to `_external` and never call `_prepare` for them).
- Call sites: in `answer()` `await self._prepare(gate, state, answer)`; in `reconcile`
  `state = await self._prepare(gate, state, answer)`. `PersistenceError` stays imported (used
  elsewhere). No other source change.

Tests (`tests/test_control_gates.py`; imports from `tests.test_candidate_fanout`
(`initial as fanout_initial`, `loop as fanout_loop`), `tests.test_control_router` (`FakeClient`,
`context`), `statlab.graph.router.InterruptClass`, `statlab.graph.state.RunState`,
`statlab.graph.candidates.TraversalPlan` (or `statlab.graph`), `statlab.errors.PersistenceError`;
fixtures `engine_world`, `minimal_contract`). Module-level:
`class Unrelated(BaseModel)`: frozen, extra forbid, `rationale: str`.

A. `test_seven_action_contract_rejects_missing_or_extraneous_fields`: add parametrize case
   `{"action": "rewind", "target": " "}`.
B. `test_pre_action_override_keeps_actual_mro_type_and_avoids_new_proposal`: directly after
   `await service.answer(answer)` add `assert await service.override(replace(seen[0],
   spec=NodeSpec("other", "SHARED", True, Composite, (), "always")), Proposal) is None`; directly
   after the `lifecycle.start()` completed assertion add
   `assert await service.override(seen[0], Proposal) is None`.
C. Helper `async def shared_gate(world: World, *, after: bool = False, rewind: GateRewind |
   None = None) -> tuple[RunLifecycle, GateService, list[NodeInvocation]]` (`GateRewind` from
   `statlab.graph.gates`): `nodes = declared()`; `update_run_mode(world.conn,
   world.paths.run_id, "guided")`; `seen: list[NodeInvocation] = []`; executor appends `inv`,
   then `accepted = await service.override(inv, Proposal)`; if not None return
   `NodeResult(status="completed")`; else `assert await service.approval(replace(inv,
   after_action=after), Composite(rationale="Proposal", count=1, outcome="done") if after else
   Proposal(rationale="Proposal", count=1), ()) is None` and return
   `NodeResult(status="awaiting_user", summary={"waiting": True})`. Build
   `lifecycle = RunLifecycle(world.loop(nodes, execute), world.initial(nodes).model_copy(
   update={"mode": "guided"}))`, `service = GateService(lifecycle, rewind=rewind)`; assert
   `(await lifecycle.start()).status == "awaiting_user"`; return `(lifecycle, service, seen)`.
D. Helper `def strip(state: RunState, **changes: JsonValue) -> RunState` is NOT allowed (no
   untyped override helpers): write each stripped state inline as
   `RunState.model_validate({**state.model_dump(), "pending_result": None, ...})`.

G1 `test_candidate_gate_continuation_updates_owner_branch` parametrize `action` in
   `("approve", "skip_node")`: `nodes = (node("foundation"), NodeSpec("prepare",
   "PER_CANDIDATE", True, Composite, (), "always"), node("compare"))` (`node` from
   `tests.test_graph_state`); `update_run_mode(...guided)`; `calls: list[str] = []`; executor:
   for `inv.spec.name == "prepare"` and `inv.candidate is not None` and
   `inv.candidate.public_id == "c_01"`: append `"c_01"`; `accepted = await service.override(inv,
   Proposal)`; if None: `assert await service.approval(inv, Proposal(rationale="Candidate
   proposal", count=1), ()) is None`, return `NodeResult(status="awaiting_user")`; else return
   `NodeResult(status="completed", summary={"count": Proposal.model_validate(
   accepted.model_dump()).count})`. Every other invocation returns
   `NodeResult(status="completed", summary={"node": inv.spec.name})`.
   `lifecycle = RunLifecycle(fanout_loop(engine_world, nodes, execute, traversal=TraversalPlan()),
   fanout_initial(engine_world, nodes, count=2).model_copy(update={"mode": "guided"}))`;
   `service = GateService(lifecycle)`; first start → `awaiting_user`; `gate =
   list_gates(...)[0]`; assert `GateQuestion.model_validate(gate.question()).candidate_id ==
   first.branches[0].database_id(first.run_id)` where `first` is the start result and
   `first.branches[0].public_id == "c_01"`.
   Answer `GateAnswer(gate_id=gate.id, action="approve")` or `GateAnswer(gate_id=gate.id,
   action="skip_node", reason="Operator excluded candidate step")`. Second start → `completed`.
   approve: `calls == ["c_01", "c_01"]`; the c_01 branch's `prepare` boundary result summary is
   `{"count": 1}`. skip_node: `calls == ["c_01"]`; that boundary has `status == "skipped"` and
   the reason; some replayed event has `type == "node.skipped"` and `payload["candidate_id"] ==
   "c_01"`. Both: final c_01 branch `status == "completed"`; `get_gate(...).status == "applied"`.
G2 `test_restart_restores_candidate_gate_missing_from_checkpoint`: same setup as G1 through the
   first start (copy, no shared helper required); `state = await lifecycle.state()`;
   `stripped = RunState.model_validate({**state.model_dump(), "branches": [{**b.model_dump(),
   "pending_result": None, "status": "running"} if b.public_id == "c_01" else b.model_dump() for
   b in state.branches]})`; `lifecycle.loop.checkpointer.save(stripped)`;
   `restored = await service.reconcile(stripped)`: c_01 branch `status == "awaiting_user"` and
   `pending_result == state.branches[0].pending_result` (match by public_id),
   `restored.status == "awaiting_user"`. Then approve answer and start → `completed`.
G3 `test_restart_restores_unanswered_shared_gate_missing_from_checkpoint` parametrize `case` in
   `("captured", "uncaptured_before", "uncaptured_after")`: `lifecycle, service, seen = await
   shared_gate(engine_world, after=case == "uncaptured_after")`; `gate = list_gates(...)[0]`;
   if captured: assert `gate.question()["awaiting_result"] is not None`; else
   `gate = transition_gate(conn, gate, status="pending", answer_json=None,
   question={**gate.question(), "awaiting_result": None})`. `state = await lifecycle.state()`;
   `stripped = RunState.model_validate({**state.model_dump(), "pending_result": None})`; save it;
   `restored = await service.reconcile(stripped)`; `restored.status == "awaiting_user"`;
   `restored.pending_result` not None with `execution_id == gate.node_execution_id`.
   captured: `restored.pending_result == state.pending_result`; then approve (revision 0), start
   → `completed`, `len(seen) == 2`; exactly 1 `gate.opened` event.
   uncaptured_before: `restored.pending_result.result.summary == {"decision":
   GateQuestion.model_validate(gate.question()).proposal, "gate_id": gate.id}`; approve, start
   → `completed`, `len(seen) == 2`; exactly 1 `gate.opened`.
   uncaptured_after: re-read gate: question `phase == "failure"`, `revision == 1`,
   `options == ("abort", "rewind", "skip_node")`, `default == "abort"`, `failure.error_class ==
   "bug"`, `failure.message == "Completed action outcome was not checkpointed"`; answer
   `GateAnswer(gate_id=gate.id, revision=1, action="skip_node", reason="Outcome lost")`; start →
   `completed`, `shared_results[0].result.status == "skipped"`, `len(seen) == 1`; exactly 2
   `gate.opened` events.
G4 `test_restart_rejects_unanswered_gate_beyond_checkpoint_frontier`: `shared_gate`; state;
   stripped with `"pending_result": None, "cursor": 1`; save;
   `pytest.raises(PersistenceError, match="unanswered gate disagrees with checkpoint frontier")`
   around `service.reconcile(stripped)`.
G5 `test_restart_prepares_answered_gate_after_checkpoint_advanced`: `shared_gate`; gate;
   `transition_gate(conn, gate, status="answered", answer_json=GateAnswer(gate_id=gate.id,
   action="approve").model_dump_json())`; stripped with `"pending_result": None, "status":
   "pending"`; save; `ready = await service.reconcile(stripped)`; `ready.pending_result is None`;
   gate status `"prepared"`; start → `completed`, `len(seen) == 2`, gate status `"applied"`.
G6 `test_restart_external_answers` parametrize `action` in `("abort", "rewind", "instruct")`:
   `shared_gate` (no rewind/revise); gate; answer = abort: `GateAnswer(gate_id=gate.id,
   action="abort")`; rewind: `...action="rewind", target="proposal"`; instruct:
   `...action="instruct", instruction="Use two"`; transition to `"answered"` with its JSON;
   `state = await lifecycle.state()`. abort: `await service.reconcile(state)`;
   `(await lifecycle.state()).status == "cancelled"`; gate status `"applied"`; call
   `await service.reconcile(await lifecycle.state())` again (applied abort replay; must not
   raise); exactly 1 `gate.closed` event. rewind: `pytest.raises(EngineError, match="rewind
   service is not bound")`. instruct: `pytest.raises(EngineError, match="revision callback is
   required for accepted instruction")`.
G7 `test_gate_service_rejects_corrupted_questions_and_bindings(engine_world, minimal_contract)`:
   `lifecycle, service, seen = await shared_gate(engine_world)`; `original = list_gates(...)[0]`;
   `inv = seen[0]`; `approve = GateAnswer(gate_id=original.id, action="approve")`.
   For each `(change, error, match)` in order: `({"decision_type": "tests.Unknown"}, EngineError,
   "outside declared schema inheritance")`, `({"node": "other"}, PersistenceError, "gate execution
   ownership mismatch")`, `({"decision_schema": {}}, EngineError, "gate decision schema
   changed")`: `mutated = transition_gate(conn, current, status="pending", answer_json=None,
   question={**original.question(), **change})`; `pytest.raises(error, match=match)` around
   `service.answer(approve)`; restore `current = transition_gate(conn, mutated,
   status="pending", answer_json=None, question=original.question())` (`current` starts as
   `original`). Then:
   - history: mutate `{"history": ["noise"]}` the same way; `await
     service.reconcile(await lifecycle.state())` does not raise; no replayed event has type
     `"gate.answered"`; restore.
   - `pytest.raises(EngineError, match="rewind requires a declared target and bound change
     service")`: `service.answer(GateAnswer(gate_id=original.id, action="rewind",
     target="proposal"))`; `match="gate revision callback is not configured"`:
     `GateAnswer(gate_id=original.id, action="instruct", instruction="Use two")`.
   - `match="gate invocation binding mismatch"`: `service.approval(replace(inv,
     state=inv.state.model_copy(update={"run_id": "r_other"})), Proposal(rationale="x",
     count=1), ())`.
   - `match="gate proposal type disagrees with declared decision"`: `service.approval(inv,
     Unrelated(rationale="Not declared"), ())`.
   - `match="failure escalation requires structured failure"`: `service.failure(inv,
     NodeResult(status="completed"))`.
   - `match="gate control binding mismatch"`: `service.handle("approve",
     InterruptClass(category="qa"), context(engine_world.conn, minimal_contract, FakeClient()))`.
   - last: `execution = create_node_execution(conn, run_id=original.run_id, node_name="ghost",
     seq=2)`; `ghost = create_gate(conn, run_id=original.run_id, execution_id=execution.id,
     question={**original.question(), "node": "ghost"})`; `match="gate node is undeclared"`:
     `service.answer(GateAnswer(gate_id=ghost.id, action="approve"))`.

Checks: Ruff check/format --check, mypy on `backend/statlab/graph/gates.py` and
`tests/test_control_gates.py` (separate args); `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_control_gates.py tests/test_api_runs.py tests/test_domain_binding.py'` → log
`/private/tmp/statlab-offline-20261007-s3.log`. Any failure: stop with exact `E` lines — a
failure here may be a real recovery defect; never adapt assertions to observed behavior.

### Stage S4 — champion recovery, audit and refusal paths

Owned: `backend/statlab/persistence/repository.py` (helper + two call sites),
`backend/statlab/domains/champion.py` (one call site), `tests/test_nodes_champion.py`,
`tests/test_repository.py`. Targets champion.py misses 64->61, 67-68, 75, 80-81, 114, 116, 147,
152, 157, 159, 168, 180, 190, 266, 322, 457, 476, 480, 489; repository 955, 1001.

Unreachable, removed: champion 146-147 (`run is None`): `self.runtime.context(invocation)` runs
first and, for `champion_selection`, calls `get_champion_model`, which raises when the run row is
missing; nothing awaits in between. Repository 954-955 (`_check_model_identity`) and 1000-1001
(`save_champion_model`): the candidate row was just proven to belong to `run_id` and candidates
cascade with runs. Source recipe:
- repository.py: add directly below `get_run`:
  `def require_run(conn: sqlite3.Connection, run_id: str) -> RunRow:` — `row = get_run(conn,
  run_id)`; if None raise `PersistenceError("run does not exist")`; return row. In
  `_check_model_identity` replace `current = get_run(conn, run_id)` + its None guard with
  `current = require_run(conn, run_id)`; in `save_champion_model` replace `run = get_run(conn,
  run_id)` + its None guard with `run = require_run(conn, run_id)`.
- champion.py: replace `run = get_run(self.runtime.conn, state.run_id)` + its None guard with
  `run = require_run(self.runtime.conn, state.run_id)`; import `require_run` in the existing
  repository import block (`get_run` stays: still used at the fingerprint read).

Test harness edit (`ChampionTools.call`): directly after `self.calls.append(...)` add
`if tool == self.failure_tool: raise ToolCallError(tool, Failure(error_class="data",
message="champion tool refused"))` (`failure_tool` is inherited, default None).

Shared definitions for the tests below: `CHAMPION_ROWS = "node_execution_id IN (SELECT id FROM
node_executions WHERE node_name = 'champion_selection')"`; "run champion" =
`await ChampionExecutor(h.runtime)(tail_invocation(h))`; "failed 'msg'" as in S2; `record(h,
inv, tool, args, result_json)` = `record_tool_call(h.runtime.conn, run_id=h.state.run_id,
node_execution_id=inv.execution.id, tool_name=tool, args_json=json.dumps(args),
result_json=result_json, duration_ms=1)` written inline (no helper required).

H1 `test_champion_audit_skips_failures_and_malformed_rows_fail_closed`: harness; `inv =
   tail_invocation(h)`; record `compare_models` with args `{}` and
   `ToolFailure(error_class="data", message="refused").model_dump_json()`;
   `champion_audits(h.runtime) == ()`. Record `compare_models` with args `{}` and result_json
   `"not json"`; `pytest.raises(PersistenceError, match="cannot recover champion audit")`:
   `champion_audits(h.runtime)`; `pytest.raises(EngineError, match="champion persistence
   failed")`: run champion.
H2 `test_eligibility_requires_join_and_excludes_unsuccessful_branches`: harness; `second =
   h.state.branches[1]`; for status `"running"`: state with second rebuilt as
   `CandidateProgress(public_id=second.public_id, spec=second.spec, results=second.results,
   position=second.position, status=status)` (via `RunState.model_validate` on dumps, as
   `test_latest_model_alignment...`) → `pytest.raises(EngineError, match="champion selection
   requires joined candidates")`: `eligible_candidates(state)`. For `"failed"`: survivors ==
   `(h.state.branches[0],)` and `excluded == {"c_02": "failed"}`.
H3 `test_champion_tool_failure_is_structured`: harness; `tools.failure_tool = "compare_models"`;
   run champion → failed "champion tool refused"; `artifacts == ()`.
H4 `test_interrupted_selection_audits_must_be_unique_and_consistent` parametrize `case` in
   `("duplicate", "malformed", "disagree", "ranking", "comparison")`: harness;
   `tools.interrupt_select = True`; `pytest.raises(asyncio.CancelledError)`: run champion;
   `tools.interrupt_select = False`; `inv = tail_invocation(h)`; `row =
   conn.execute("SELECT * FROM tool_calls WHERE tool_name = 'select_champion'").fetchone()`;
   `args = json.loads(row["args_json"])`.
   - duplicate: record `select_champion` with `row["args_json"]` text and `row["result_json"]`
     (pass the strings directly) → run champion (fresh `tail_invocation`) failed "interrupted
     selection has multiple successful audits".
   - malformed / disagree / ranking: first `conn.execute("DELETE FROM tool_calls WHERE id = ?",
     (row["id"],))`; then record `select_champion` with args respectively
     `{**args, "candidate": 1}`, `args`, `{**args, "ranking": "a_00000000000000aa"}` and result
     respectively `row["result_json"]`, `ToolSuccess(summary={"candidate": "c_02"},
     artifacts=ToolSuccess.model_validate_json(row["result_json"]).artifacts).model_dump_json()`,
     `row["result_json"]`. malformed → `pytest.raises(EngineError, match="malformed successful
     selection audit")`; disagree → `pytest.raises(EngineError, match="disagrees with its
     choice")`; ranking → failed "interrupted selection lacks matching validation ranking audit".
   - comparison: `conn.execute(f"DELETE FROM tool_calls WHERE tool_name = 'compare_models' AND
     {CHAMPION_ROWS}")` → failed "interrupted selection disagrees with latest validation
     evidence".
   (Validate `row["result_json"]` with `TOOL_RESULT_ADAPTER` or `ToolSuccess.model_validate_json`
   as needed; the select result is a success.)
H5 `test_claimed_or_pointed_run_without_selection_audit_refuses`: harness;
   `claim_test_scoring(conn, h.state.run_id)`; run champion → failed "interrupted champion has no
   successful selection audit"; no `compare_models` call recorded.
H6 `test_durable_pointer_must_match_frozen_choice`: harness; interrupt at select as H4;
   `second = h.state.branches[1]`; `save_champion_model(conn, run_id=h.state.run_id,
   candidate_id=second.database_id(h.state.run_id), artifact_id=str(candidate_result(second,
   "model_training").summary["model"]), name=second.spec.estimator.name,
   parameters={"fit_intercept": False})`; run champion → failed "durable champion disagrees with
   frozen latest model".
H7 `test_missing_test_fingerprint_fails_after_score`: harness; `conn.execute("UPDATE runs SET
   test_fingerprint = NULL WHERE id = ?", (h.state.run_id,))`; `pytest.raises(EngineError,
   match="champion test fingerprint is missing")`: run champion.
H8 `test_recovered_score_requires_frozen_audit_chain` parametrize `(statement, message)`:
   `("DELETE FROM tool_calls WHERE tool_name = 'predict' AND " + CHAMPION_ROWS, "interrupted score
   lacks frozen prediction audit")`, `("DELETE FROM tool_calls WHERE tool_name = 'apply_scaler'
   AND " + CHAMPION_ROWS, "interrupted score lacks frozen replay audit")`, `("UPDATE tool_calls
   SET args_json = json_set(args_json, '$.frame', 'a_00000000000000bb') WHERE tool_name =
   'drop_columns' AND " + CHAMPION_ROWS, "interrupted score replay does not start at shared
   test")`, `("UPDATE tool_calls SET args_json = json_set(args_json, '$.target', 'not_target')
   WHERE tool_name = 'score_regression' AND " + CHAMPION_ROWS, "test scoring interrupted:
   successful audit does not match frozen champion")`: harness; premise: `"apply_scaler"` and
   `"drop_columns"` are in the first branch's replay tools; `tools.interrupt_report = True`;
   `pytest.raises(asyncio.CancelledError)`: run champion; `tools.interrupt_report = False`;
   `conn.execute(statement)` and assert `rowcount >= 1`; run champion → failed message.
H9 (`tests/test_repository.py`) `test_require_run_rejects_missing_run(migrated_db)`:
   `pytest.raises(PersistenceError, match="run does not exist")`: `require_run(migrated_db,
   "r_missing")`.

Checks: Ruff check/format --check and mypy on the four owned files (separate args);
`LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_nodes_champion.py tests/test_repository.py
tests/test_nodes_persistence.py tests/test_control_rewind.py tests/test_spine_reporting.py'` →
`/private/tmp/statlab-offline-20261007-s4.log`. Stop on any failure/false premise with `E` lines.

#### S3 stop report and correction S3a (product defect found)

S3 run (`...-s3.log`): 51 passed, 6 failed. Diagnosis (main):
1. **Product defect** — `GateService._prepare` (owner branch) and `_restore_pending` use raw
   `owner.position`; the engine (`engine.py` 220/241/626) and the `RunState` validator use the
   effective position `max(region_start, branch.position)`. New branches start at position 0,
   below the candidate region. Consequence: in guided mode, an after-action acknowledgment or
   `skip_node` on the FIRST candidate node (tabular `preprocessing`, exception gate) builds
   `position=1` with one boundary → `RunState` ValidationError ("candidate boundaries disagree
   with position"); the operator cannot continue. On restart, an unanswered gate on that node is
   rejected as off-frontier. G1[skip_node] reproduces it.
2. G2–G4 spec defect: a crash between gate creation and checkpoint leaves the pre-node checkpoint,
   whose status is `running` (no pending boundary); `awaiting_user` without pending is invalid.

S3a source recipe (gates.py):
- `_prepare`, owner branch (`else:` after `if owner is None`): replace the `position = ...` and
  `end = (...)` statements with: if `state.traversal is not None`: `start, end =
  state.traversal.region(state.spine)`; `position = max(start, owner.position)`; else
  `position, end = state.cursor, len(state.spine)`. Everything after is unchanged.
- `_restore_pending`: replace `position = (owner.position if owner is not None and
  state.traversal is not None else state.cursor)` with: `position = (max(state.traversal.region(
  state.spine)[0], owner.position) if owner is not None and state.traversal is not None else
  state.cursor)`.
S3a test recipe (test_control_gates.py):
- G2: add `"status": "running"` to the top-level `stripped` dict.
- G3: `stripped` dict becomes `{**state.model_dump(), "pending_result": None, "status": "running"}`.
- G4: `stripped` dict becomes `{**state.model_dump(), "pending_result": None, "status":
  "running", "cursor": 1, "shared_results": [BoundaryResult(node_name="proposal",
  execution_id="ne_other", result=NodeResult(status="completed")).model_dump()]}`.
- G1: no change (skip_node is now the regression test for defect 1). Additionally in G1
  skip_node, assert the answer returns without error and, before the second start,
  `(await lifecycle.state())` has the c_01 branch with `position == 2`, `status == "completed"`
  and one `prepare` boundary.
Checks: same as S3, log `...-s3a.log`. Stop on any failure.

#### S3a receipt and correction S3b

S3a: checks exit 0, 57 passed (`...-s3a.log`); gates `7ecf65ed…68769`, tests `6f2d2e07…6309d`.
Main coverage (gates suites): gates.py misses 400-401, 448, 539->514; contracts/gates.py 58.
- 400-401: the S3 recipe's deletion of `if answer.action in ("abort", "rewind"): return state`
  inside `_prepare` was not applied — delete it now.
- 539->514: in `reconcile`, the final `elif gate.status in ("prepared", "applied") and
  gate.answer_json is not None:` false arc is unreachable (statuses are only pending/answered/
  prepared/applied; prepared/applied always carry an answer). Replace that `elif ...:` line with
  `else:` and its first statement with `answer = GateAnswer.model_validate_json(gate.answer_json
  or "null")` (same idiom as the `answered` branch). Body otherwise unchanged.
- 448: single-candidate runs have `traversal=None` (`RunState.initial` sets a traversal only for
  >1 branch) — a production path. Test: parametrize G1
  (`test_candidate_gate_continuation_updates_owner_branch`) additionally over `count` in `(1, 2)`
  (ids `single`/`fanout`): pass `count` to `fanout_initial` and use
  `traversal=TraversalPlan() if count == 2 else None` in `fanout_loop`. The c_01 assertions are
  unchanged; for `count == 1` the skip_node pre-start check asserts the state `cursor == 2`
  instead of branch `position == 2` (branch status `completed` and one `prepare` boundary still
  asserted) — the `node.skipped` payload check applies only to `count == 2` (no candidate_id
  without traversal). Apply the same `count` parametrization to G2 only for `count == 2` (leave G2
  as is).
- contracts/gates.py 58 is `RewindIntent._nonblank`: add test
  `test_rewind_intent_rejects_blank_target`: `pytest.raises(ValidationError)`:
  `RewindIntent.model_validate({"target": " "})` (import from `statlab_contracts.gates`). Keep
  test A's added case.
Checks as S3, log `...-s3b.log`.

### Stage S5 — preservation and retained-champion provenance (after S4)

Owned: `backend/statlab/domains/persistence.py`, `tests/test_nodes_persistence.py`,
`tests/test_nodes_champion.py` (ChampionTools knob only). Targets persistence.py 50->exit, 70, 82,
84, 107, 134, 165-166, 168; domains/rewind.py 25, 30, 47, 59, 66-68.

Unreachable, removed (persistence.py `_execute`): `if run is None: raise _RefusalError(...)` —
`champion_provenance` fails first when the run/executions are gone (cascade); and
`if model.id != champion.get("model_id") or model.artifact_id != champion.get("model"): raise
_RefusalError("completed champion disagrees with durable model")` — `champion_provenance` checks the
same latest completed `champion_selection` boundary's `model_id`/`model` (`shared_result` and
`champion_provenance` both pick it newest-first). Source recipe: replace
`run = get_run(runtime.conn, runtime.paths.run_id)` with
`run = require_run(runtime.conn, runtime.paths.run_id)` (S4 helper; import it, drop `get_run` if
unused); delete the two guards. No other change.

ChampionTools knob: `self.manifest_text: str | None = None` in `__init__`; in the `snapshot_run`
branch write `self.manifest_text` when not None, else the existing JSON.

Tests (`tests/test_nodes_persistence.py`; "run 12" = `await PersistenceExecutor(h.runtime)(
tail_invocation(h, 12))`; harness = `persistence_harness`):
P1 `test_atomic_manifest_creation_failure_leaves_no_temporary(tmp_path)`:
   `pytest.raises(FileNotFoundError)`: `_atomic_manifest(tmp_path / "missing" / "manifest.json",
   {})`; `not (tmp_path / "missing").exists()`.
P2 `test_export_tool_failure_is_structured`: harness; `tools.failure_tool =
   "export_pipeline_script"`; run 12 → failed "champion tool refused".
P3 `test_score_absent_from_real_audit_refuses`: harness; `meta = ArtifactMeta(ref=
   new_artifact_ref(), kind="table")`; `path = h.runtime.paths.artifact(meta.ref, "table")`;
   write `b"orphan score"`; `create_artifact(conn, ref=meta.ref, run_id=h.state.run_id,
   kind="table", path=path, meta=meta, produced_by="fixture", derived_from_test=True)`; replace
   the last shared boundary (champion) by `BoundaryResult(node_name=last.node_name,
   execution_id=last.execution_id, result=NodeResult(status="completed",
   summary={**last.result.summary, "test_score": str(meta.ref)},
   artifacts=last.result.artifacts))` via `RunState.model_validate` on dumps; run 12 → failed
   "champion test score is absent from real tool audit".
P4 `test_lockfile_changed_during_approval_refuses_before_export`: harness;
   `h.state = with_mode(h.state, "guided")`; approval callback writes
   `b"changed during approval\n"` to `h.runtime.paths.root / "uv.lock"` and returns `decision`;
   run 12 → failed "run lockfile already differs from supplied bytes"; `tools.calls == []`.
P5 `test_snapshot_manifest_must_parse_and_match` parametrize `(text, message)`:
   `("[]", "snapshot manifest is malformed")`, `('{"run_hash": "other"}', "snapshot manifest
   disagrees with returned run hash")`: harness; `tools.manifest_text = text`;
   `pytest.raises(EngineError, match="artifact preservation failed") as raised`: run 12;
   `isinstance(raised.value.__cause__, PersistenceError)` and its `.message == message`.
R1 `test_champion_provenance_rejects_missing_unknown_and_unclaimed`: harness; `conn =
   h.runtime.conn`; `last = h.state.shared_results[-1]`.
   a) `RunState.model_validate({**h.state.model_dump(mode="json"), "cursor": 11,
      "shared_results": [b.model_dump(mode="json") for b in h.state.shared_results[:-1]]})` →
      `pytest.raises(PersistenceError, match="retained champion boundary is unavailable")`.
   b) last replaced with `execution_id="ne_missing"` (same node/result) → match
      `"champion execution is unavailable"`.
   c) `conn.execute("UPDATE runs SET test_scored_at = NULL WHERE id = ?", (h.state.run_id,))`;
      `champion_provenance(conn, h.state)` → match `"retained champion score provenance is
      invalid"`.
R2 `test_publish_retained_report_copies_plots_and_rejects_changed_report`: harness; `parent =
   h.runtime.paths`; `child = RunPaths(parent.data_dir, parent.slug, new_id("r"))`;
   `ensure_run_layout(child)`; `ref = sorted(h.state.retained_refs)[0]`;
   `(parent.artifacts / f"{ref}.png").write_bytes(b"png bytes")`;
   `await publish_retained_report(parent, child, h.state)`; child `report/report.md` bytes equal
   parent's; `(child.report / "plots" / f"{ref}.png").read_bytes() == b"png bytes"`. Then write
   `b"# changed\n"` to `parent.report / "report.md"` → `pytest.raises(PersistenceError,
   match="retained report hash disagrees with committed champion")`.
(Imports: `_atomic_manifest` from `statlab.domains.persistence`; `champion_provenance`,
`publish_retained_report` from `statlab.domains.rewind`; `new_id` from `statlab.persistence.ids`;
`ensure_run_layout`, `create_artifact` from `statlab.storage`; `RunPaths` from
`statlab_contracts.layout`; `ArtifactMeta`, `new_artifact_ref` from `statlab_contracts.artifacts`;
`PersistenceError` from `statlab.errors`; `NodeResult` from `statlab.domains.base`.)
Checks: Ruff/format/mypy on the three owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_nodes_persistence.py tests/test_nodes_champion.py tests/test_control_rewind.py
tests/test_spine_reporting.py'` → `...-s5.log`.

#### S3b stop and correction S3c

S3b: 58 passed, 2 failed (`[approve-single]`, `[skip_node-single]`: final c_01 branch status
`running`). Main diagnosis: not a product defect. Production always runs with a traversal
(`RunLoop._fanout` writes `traversal` into the state; tabular binding passes
`TABULAR_TRAVERSAL`). `traversal=None` with a branch is the B7 legacy single-candidate path,
where the engine (`engine.py` ~418) never maintains branch status. Spec premise wrong.
S3c (test only): in G1 the final-state assertion `branch.status == "completed"` applies only
when `count == 2`; for `count == 1` assert instead that the c_01 branch has exactly one `prepare`
boundary with the expected status (`completed` for approve, `skipped` for skip_node) and
`final.cursor == 3`. Same checks, log `...-s3c.log`.

### Stage S6 — shared foundation and decision validators (after S4)

Owned: `backend/statlab/domains/foundation.py` (split run read only),
`tests/test_nodes_foundation.py`. Targets foundation 49, 56, 74(now in `resolved_metric`), 76-77,
87, 102, 194, 196, 200, 203, 211, 241, 311->313, 313->315, 340, 382-383, 401->400, 405, 419,
423, 488, 519, 561, 573->589, 637, 653, 657; decisions 88, 97, 100, 102, 106, 189.

Unreachable, removed: foundation `_split` `if run is None: raise EngineError("split run is
missing")` — `set_test_fingerprint` (called just before) raises `PersistenceError` for an unknown
run. Source: replace `run = get_run(...)` + guard with `run = require_run(self.runtime.conn,
invocation.state.run_id)` (import from repository; drop `get_run` import if unused).

OfflineTools knobs (`__init__`): `self.id_candidates: list[JsonValue] = [{"column": "id"}]`,
`self.leak_suspects: list[JsonValue] = [{"column": "x"}]`, `self.split_count = 3`,
`self.omit_fingerprint = False`. `detect_id_columns` returns `{"candidates": self.id_candidates}`;
`detect_leaky_columns` returns `{"suspects": self.leak_suspects}`; the split branch calls
`set_test_fingerprint` and includes `"test_fingerprint"` only when `not self.omit_fingerprint`,
and returns `artifacts[: self.split_count]` of the existing three.

Tests (names as given; "failed 'msg'" as before; "advance(n)" = `await h.advance(n)`):
F1 `test_foundation_helpers_reject_malformed_evidence` (sync): `pytest.raises(EngineError,
   match=...)` for `_strings("x", "label")` → "missing or malformed label"; `_ref({}, "raw")` →
   "missing or malformed raw reference"; `_tool_summary(NodeResult(status="completed",
   summary={"tools": []}), "load_csv")` → "missing tool evidence: load_csv"; `_frame(ToolSuccess())`
   → "tool did not return exactly one shared dataframe"; `resolved_metric(ProblemSpec(
   rationale="r", target="y", target_type="binary", task="classification", primary_metric=None,
   metric_rule="classification_auto"))` → "automatic metric has not been resolved".
F2 `test_resolve_metric_refuses_contradictory_class_evidence` parametrize `(target, message)`
   with `problem` = the F1 ProblemSpec: `({"kind": "class", "classes": {"0": {"share": 1.0}},
   "minority_share": 1.0}, "classification needs at least two observed classes")`;
   `({"kind": "class", "classes": {"0": {"share": 0.4}, "1": {"share": 0.3}, "2": {"share":
   0.3}}, "minority_share": 0.3}, "binary target summary has a contradictory class count")`;
   `({"kind": "class", "classes": {"0": 1, "1": 2}, "minority_share": 0.5}, "class-share evidence
   is malformed")`; `({"kind": "class", "classes": {"0": {"share": 0}, "1": {"share": 1}},
   "minority_share": 0}, "class-share evidence is unavailable")`; `({"kind": "class", "classes":
   {"0": {"share": 0.5}, "1": {"share": 0.5}}, "minority_share": 0.4}, "class-share evidence is
   contradictory")` → `pytest.raises(_RefusalError, match=message)`.
F3 `test_malformed_problem_boundary_fails_closed`: `h = harness(...)`; advance(2);
   `bad = RunState.model_validate({**h.state.model_dump(mode="json"), "shared_results":
   [h.state.shared_results[0].model_dump(mode="json"), BoundaryResult(node_name=
   "problem_definition", execution_id=h.state.shared_results[1].execution_id,
   result=NodeResult(status="completed", summary={"problem": {"target": ""}})).model_dump(
   mode="json")]})`; `pytest.raises(EngineError, match="malformed shared problem output")`:
   `problem_from_state(bad)`; `h.state = bad`; `pytest.raises(EngineError, match="malformed
   problem boundary")`: `await h.step()`.
F4 `test_untyped_boolean_target_is_binary`: harness with
   `contract=DataContract(target=TargetSpec(column="y"))`; advance(2);
   `problem_from_state(h.state)` — NOTE metric may be unresolved before profiling: instead read
   `ProblemSpec.model_validate(h.state.shared_results[1].result.summary["problem"])` and assert
   `target_type == "binary"` and `task == "classification"`.
F5 `test_intake_without_flags_and_escalation`: harness; `h.tools.id_candidates = []`;
   `h.tools.leak_suspects = []`; `out = await h.step()` → completed; neither "ID candidates
   require review; no columns were removed" nor "suspected leakage requires review; flags are not
   proof" in `out.warnings`. Second harness in a separate test
   `test_intake_escalation_fails_for_review`: `h.llm.responses[IntakeDecision] =
   IntakeDecision(rationale="Escalate", disposition="escalate")`; step → failed "intake requires
   data/contract review".
F6 `test_profile_roles_override_dtype_and_skip_absent_columns`: harness with contract
   `DataContract(target=TargetSpec(column="y", type="binary"), features=FeatureRules(roles={"x":
   "categorical", "ghost": "numeric"}))`; `h.llm.responses[ProfileDecision] =
   ProfileDecision(rationale="Categorical x", categorical=("x",), plots=(ProfilePlot(
   kind="target_relation", column="x"),))`; advance(3); the single `summarize_categorical` call
   has `columns == ["x"]`; the single `plot_target_relation` call has `target == "y"` and
   `column == "x"`.
F7 `test_profile_refuses_unavailable_tool_and_unknown_plot_column`: (a) harness; replace skill
   with `SkillPolicy("fixed preamble", tuple(NodeSkill(spec.name, "fixed section", "purpose",
   "heuristics", "tools", "outputs", "pitfalls", policy().node(spec.name).allowed -
   {"summarize_numeric"}) for spec in TABULAR_SPINE), b"fixed policy")` via
   `h.runtime = replace(h.runtime, skill=...)`; advance(2); step → failed "summarize_numeric is
   unavailable in this node skill". (b) separate test
   `test_profile_refuses_unknown_plot_column`: `ProfileDecision(rationale="Ghost plot",
   plots=(ProfilePlot(kind="distribution", column="ghost"),))`; advance(2); step → failed "plot
   names an unknown column or unavailable tool".
F8 `test_regression_refuses_stratified_split_proposal`: harness with contract
   `DataContract(target=TargetSpec(column="y", type="continuous"))`; `h.tools.roles["y"] =
   "numeric"`; `h.llm.responses[SplitDecision] = SplitDecision(rationale="Stratify",
   strategy="stratified")`; advance(3); step → failed "regression cannot use a stratified split".
   (If the profile step does not complete for this contract, stop and report — do not adapt.)
F9 `test_split_after_skipped_profile_casts_and_respects_no_duplicate_drop`: harness with contract
   `DataContract(target=TargetSpec(column="y", type="binary"), objective=Objective(
   primary_metric="roc_auc"), features=FeatureRules(roles={"x": "numeric"}),
   preprocessing=PreprocessingRules(drop_duplicates=False))`; advance(2); then
   `h.state = RunState.model_validate({**h.state.model_dump(mode="json"), "cursor": 3,
   "shared_results": [*(b.model_dump(mode="json") for b in h.state.shared_results),
   BoundaryResult(node_name="data_profiling", execution_id="skipped-profile",
   result=NodeResult(status="skipped", skip_reason="Profiling skipped")).model_dump(
   mode="json")]})`; `offset = len(h.tools.calls)`; step → completed; the split-node calls
   (`h.tools.calls[offset:]`) start with `cast_dtypes` whose params are `{"frame": str(RAW),
   "target": "y", "roles": {"x": "numeric"}}`; no `drop_duplicate_rows` call among them.
F10 `test_split_rejects_malformed_artifacts_and_missing_fingerprint` parametrize `knob` in
   `("count", "fingerprint")`: harness; advance(3); count: `h.tools.split_count = 2` →
   `pytest.raises(EngineError, match="split tool returned malformed split artifacts")`: step;
   fingerprint: `h.tools.omit_fingerprint = True` → match "split tool omitted test fingerprint".
F11 `test_problem_and_conclusion_validators` (sync) parametrize `(payload, message)` over
   `ProblemSpec.model_validate` with base `{"rationale": "r", "target": "y"}` plus:
   `{"target_type": "continuous", "task": "regression", "primary_metric": "roc_auc",
   "metric_rule": "explicit"}` → "primary metric is incompatible with task";
   `{"target_type": "continuous", "task": "regression", "primary_metric": "rmse", "metric_rule":
   "explicit", "secondary_metrics": ["roc_auc"]}` → "secondary metric is incompatible with task";
   `{"target_type": "continuous", "task": "regression", "primary_metric": "rmse", "metric_rule":
   "classification_auto"}` → "classification rule requires classification";
   `{"target_type": "binary", "task": "classification", "primary_metric": None, "metric_rule":
   "explicit"}` → "resolved metric is required"; `{"target_type": "binary", "task":
   "classification", "primary_metric": "f1", "metric_rule": "override",
   "metric_override_rationale": " "}` → "metric override needs a nonblank rationale"
   → `pytest.raises(ValidationError, match=message)`. Separately
   `pytest.raises(ValidationError, match="retune requires a nonblank reason")`:
   `ValidationConclusion.model_validate({"rationale": "r", "retune": True})`.
Checks: Ruff/format/mypy on the two owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_nodes_foundation.py tests/test_nodes_design_inference.py
tests/test_nodes_candidate_pipeline.py'` → `...-s6.log`.

#### S3 accepted

S3c pre-start check also assumed legacy branch status; S3d (test-only) limited it to the fanout
case. Final: gates.py `0a99c7f1…200e1`, test_control_gates `62132bc6…e41be`; checks exit 0,
60 passed (`...-s3d.log`). Main focused coverage: graph/gates.py and contracts/gates.py 100%.
Product fix retained: effective candidate position in `_prepare`/`_restore_pending` (guided
first-candidate-node acknowledgment/skip and restart restoration). Removed unreachable:
`_prepare` null-answer guard and abort/rewind return; `reconcile` impossible elif arc.

### Stage S7 — design/inference, search, binding and runtime recovery (after S6)

Owned: `backend/statlab/domains/design_inference.py`, `tests/test_nodes_design_inference.py`,
`tests/test_nodes_candidate_search.py`, `tests/test_domain_binding.py`,
`tests/test_nodes_candidate_pipeline.py` (two new tests only). Targets design_inference 47, 118,
220, 222, 228, 300, 334; candidate_search 35, 37, 44; binding 113, 134-135; runtime 56, 96-97, 101.

Unreachable, removed (design_inference.py):
- 298-300 `tool = entry["tool"]; if not isinstance(tool, str): raise EngineError("malformed
  registry fit tool")`: `_validate_inference` already raised unless `entry["tool"]` is a str in
  the skill allow-list. Change `_validate_inference` to return `tuple[dict[str, JsonValue], str]`
  (`return entry, tool` — `tool` is the local it already checked); the first call site ignores
  the result as now; the second becomes `entry, tool = self._validate_inference(...)`; delete
  the two lines `tool = entry["tool"]` / guard + raise.
- 331-334 (comment + `split_rules = ...` + `if split_rules.get("strategy") == "temporal":
  tests.append("autocorrelation")`): `_split_rules` refuses temporal/grouped strategies, and forks
  only retain parent splits, so a temporal split never exists. Delete those four lines; keep
  `tests: list[JsonValue] = ["normality", "heteroskedasticity"]`.

Tests:
D1 (`test_nodes_design_inference.py`) `test_artifact_helper_requires_exactly_one_kind` (sync):
   `pytest.raises(EngineError, match="tool did not return exactly one table artifact")`:
   `_artifact(ToolSuccess(), "table")`.
D2 `test_registry_without_family_fails_closed`: `h, tools = await prepared(...)`;
   `entries = tools.registry["estimators"]`; assert dict; `entry = entries["linear_regression"]`;
   assert dict; `del entry["family"]`; `pytest.raises(EngineError, match="estimator registry
   omitted family")`: `await design(h)`.
D3 `test_inference_registry_refusals` parametrize `(change, error, message)`; each case: `h, tools
   = await prepared(...)` (continuous, goals); mutate `models = tools.registry[
   "inferential_models"]` (assert dict) BEFORE `design(h)`: `"missing"` → `del models["ols"]`,
   failed "inference model is not registered"; `"target"` → `models["ols"] = {"tool": "fit_ols",
   "target_types": ["binary"], "family": None}`, failed "inference model is incompatible with
   target type"; `"tool"` → `models["ols"] = {"tool": "fit_unknown", "target_types":
   ["continuous"], "family": None}`, `pytest.raises(EngineError, match="registry inference fit
   tool is unavailable")`. Then `assert (await design(h)).status == "completed"` and run
   `await DesignInferenceExecutor(h.runtime)(h.invocation(5))`; for the two refusal cases assert
   `status == "failed"` and `error.message == message`; for `"tool"` wrap the call in the raises.
   (If a refusal surfaces as an exception instead of a failed result, stop and report.)
S1 (`test_nodes_candidate_search.py`) `test_search_wave_order_budget_and_no_completed_branch`:
   `h, _ = await candidate_harness(...)`; `await joined(h)`; `runtime = replace(h.runtime,
   contract=DataContract(target=..., objective=..., features=..., constraints=Constraints(
   max_search_waves=3)))` (copy fields from `h.runtime.contract`); `planner =
   CandidateSearchPlanner(runtime)`; `calls = len(h.llm.calls)`.
   a) `pytest.raises(EngineError, match="search wave must follow the completed checkpoint
      wave")`: `await planner(h.state, 3)`.
   b) `exhausted = RunState.model_validate({**h.state.model_dump(mode="json"), "budget":
      {"total_fit_ms": 10, "limit_ms": 10}})`; `await planner(exhausted, 2) == ()`.
   c) `failed = RunState.model_validate({**h.state.model_dump(mode="json"), "branches":
      [{**h.state.branches[0].model_dump(mode="json"), "status": "failed"}]})`;
      `await planner(failed, 2) == ()`.
   `len(h.llm.calls) == calls` at the end.
B1 (`test_domain_binding.py`, inside `test_gate_revision_uses_captured_memory_and_exact_anchor`):
   - before `bind_tabular`, replace `policy` with `replace(policy, nodes=tuple(replace(item,
     model="fixture/explicit-model") if item.node == "intake" else item for item in
     policy.nodes))` (`dataclasses.replace`; keep `snapshot_skill(policy, ...)` where it is, i.e.
     the snapshot stays from the loaded policy — apply the replacement after `snapshot_skill`).
   - in the non-cancel branch add: `{row["model"] for row in migrated_db.execute("SELECT model
     FROM llm_calls WHERE run_id = ?", (run.id,))} == {"fixture/explicit-model"}`.
   - at the very end (both params): `current = get_gate(migrated_db, gate.id)`; assert not None;
     `transition_gate(migrated_db, current, status="applied", answer_json=current.answer_json)`;
     `other = create_node_execution(migrated_db, run_id=run.id, node_name="problem_definition",
     seq=2)`; `create_gate(migrated_db, run_id=run.id, execution_id=other.id,
     question=current.question())`; `pytest.raises(PersistenceError, match="revision gate
     execution ownership mismatch")`: `await binding.gates.revise(GateQuestion.model_validate(
     current.question()), "Revise", IntakeDecision)`.
R1 (`test_nodes_candidate_pipeline.py`) `test_runtime_rejects_shared_identity_for_candidate_node`:
   harness; `execution = create_node_execution(h.runtime.conn, run_id=h.state.run_id,
   node_name="preprocessing", seq=7, attempt=next_node_attempt(h.runtime.conn,
   run_id=h.state.run_id, node_name="preprocessing", candidate_id=None))`;
   `pytest.raises(EngineError, match="shared invocation identity mismatch")`:
   `h.runtime.context(NodeInvocation(h.state, TABULAR_SPINE[6], execution, None))`.
R2 (`test_nodes_candidate_pipeline.py`) `test_decision_recovery_retries_transient_llm_failure`
   parametrize `sleeping` in `(False, True)`: harness; local class `FlakyLlm(OfflineLlm)` (from
   `tests.test_nodes_foundation`) overriding `complete` with the same signature: it records
   `self.attempts += 1` inside an inner `async def call() -> T` that raises
   `LlmCallError(Failure(error_class="transient", message="provider hiccup"))` on the first
   attempt and otherwise returns `output.model_validate(PreprocessingDecision(rationale=
   "Recovered").model_dump(mode="json"))`; `assert recovery is not None`; returns
   `await recovery(call)`. `attempts` initialised to 0 in `__init__` (call super). Set
   `h.runtime = replace(h.runtime, llm=FlakyLlm())`; if `sleeping`: `slept: list[float] = []`,
   `async def sleep(delay: float) -> None: slept.append(delay)`, and set `tools.sleep = sleep`
   (the CandidateTools instance from the harness). `decision = await
   h.runtime.decide(candidate_invocation(h, 6), PreprocessingDecision, evidence=(),
   question="q")` → `decision.rationale == "Recovered"`, `llm.attempts == 2`; if sleeping
   `slept == [1.0]`.
Checks: Ruff/format/mypy on the five owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_nodes_design_inference.py tests/test_nodes_candidate_search.py
tests/test_domain_binding.py tests/test_nodes_candidate_pipeline.py'` → `...-s7.log`.

#### S4 stop report and correction S4a

S4 (`...-s4.log`): 2 failures + Ruff S608/E501 — all spec defects, no product defect.
- H2: a non-terminal branch with the cursor already at the shared tail (11) is invalid
  ("shared tail precedes candidate join"). Correction: the `"running"` case also sets
  `"cursor": 6` in the `RunState.model_validate` dict; the `"failed"` case keeps the harness
  cursor. If the `"failed"` case is also rejected by `RunState`, stop and report which message.
- H6: `save_champion_model` correctly refuses a pointer for `c_02`, whose fit audit the fixture
  never recorded (repository guard working). Correction: before interrupting, set
  `h.llm.responses[ChampionChoice] = ChampionChoice(candidate_id="c_02", rationale="Second
  branch")`; interrupt at select; then save the pointer for the FIRST branch
  (`first = h.state.branches[0]`, its database id, its `model_training` model, its estimator
  name, `parameters={"fit_intercept": False}`); resume → failed "durable champion disagrees with
  frozen latest model" (the resumed audit chooses c_02; the pointer names c_01).
- Ruff S608: do not build SQL by concatenation or f-strings. Delete the `CHAMPION_ROWS` constant;
  write every statement as one string literal (adjacent-literal wrapping inside parentheses is
  allowed for E501), with the subquery spelled out:
  `"... AND node_execution_id IN (SELECT id FROM node_executions WHERE node_name = "
  "'champion_selection')"`. Then run `ruff format` on the file before the checks.
Same checks, log `...-s4a.log`.

S4b (H2 `"failed"` case only): `RunState` requires a halted branch to carry its failing boundary
at its own frontier. Build the failed second branch as `CandidateProgress(public_id=
second.public_id, spec=second.spec, results=second.results[:4], position=10, status="failed",
pending_result=BoundaryResult(node_name="model_interpretation",
execution_id="failed-interpretation", result=NodeResult(status="failed",
error=Failure(error_class="data", message="interpretation refused"))))` (premise: the first four
results are preprocessing, feature_engineering, model_training, model_evaluation — assert their
node names), keep the harness cursor. Expected unchanged: survivors `(h.state.branches[0],)`,
`excluded == {"c_02": "failed"}`. Log `...-s4b.log`.

#### S4 accepted

Final: repository `5aa965ed…70cc2`, champion `ac82b688…1da8`, test_nodes_champion
`1a992c7c…a5ba66`, test_repository `edd70f71…cfbe`; checks exit 0 (`...-s4b.log`). Main focused
coverage: domains/champion.py 100%. No product defect in S4 (all stops were spec premises:
RunState topology for synthetic states; repository fit-audit guard correctly refusing an
unaudited pointer).

### Stage S8 — engine control anchor, bootstrap stops, lifecycle callbacks, status facts

Owned source: `backend/statlab/graph/candidates.py`, `backend/statlab/graph/reconciliation.py`,
`backend/statlab/graph/control.py`. Owned tests: `tests/test_control_lifecycle.py`,
`tests/test_control_gates.py` (one test), `tests/test_candidate_fanout.py` (one addition),
`tests/test_candidate_fanout_tools.py` (one test), `tests/test_control_qa_status.py` (one test +
expectation updates named below). Targets engine 167, 168->170, 177, 183, 199, 221->242, 234,
243-245, 250, 260, 273, 373, 828-830, 832, 840; lifecycle 34, 73, 85, 132, 154, 158-163;
candidates 93, 215; reconciliation 194; control 394.

Source recipe:
- candidates.py `ToolBoundary.call.can_retry`: replace `run = get_run(...)` + `if run is None:
  raise PersistenceError("run does not exist")` with `run = require_run(self.client.conn,
  ctx.paths.run_id)` (same error; unreachable branch removed — a dispatch for a missing run has
  already failed). Adjust imports (drop `get_run`/`PersistenceError` only if now unused).
- reconciliation.py `_repair_boundaries` loop: `ensure_candidate` already raises
  `PersistenceError("candidate identity mismatch")` for any differing row and guarantees the row
  exists. Delete the `candidate = self.conn.execute("SELECT run_id, spec_json, wave ...")`
  statement and the following mismatch `if ...: raise`; replace
  `if candidate is not None and state.traversal is not None:` with
  `if state.traversal is not None:` (body unchanged).
- control.py `status_facts`: `position = (max(state.traversal.region(state.spine)[0],
  branch.position) if state.traversal is not None else state.cursor)` — the effective
  position (same rule as engine/RunState); a new branch at raw position 0 currently reports
  "position 0" in operator status text. If existing qa_status tests assert a raw position, update
  only that expected number to the effective one and list each change in the receipt.

Tests:
E1 (`test_control_lifecycle.py`) `test_missing_run_and_unexecuted_terminal_run_reject_anchor`:
   `nodes = (node(),)`; `service = RunLifecycle(engine_world.loop(nodes, complete),
   engine_world.initial(nodes))`; `missing = service.initial.model_copy(update={"run_id":
   "r_missing"})`; `pytest.raises(PersistenceError, match="run does not exist")`:
   `service.loop.ensure_audit_anchor(missing)` and (separately)
   `RunLifecycle(service.loop, missing).state()`. Then `await service.cancel()`;
   `pytest.raises(EngineError, match="run cannot start from this status")`: `service.start()`;
   `pytest.raises(EngineError, match="run has no declared execution for control audit")`:
   `service.audit_anchor()`.
E2 `test_anchor_after_joined_candidates_uses_last_execution`: `nodes = (node("fit",
   candidate=True),)`; `loop = fanout_loop(engine_world, nodes, complete,
   traversal=TraversalPlan())`; `service = RunLifecycle(loop, fanout_initial(engine_world, nodes,
   count=2))`; `done = await service.start()` (completed); save
   `RunState.model_validate({**done.model_dump(), "status": "paused"})` via
   `loop.checkpointer.save`; `anchor = await service.audit_anchor()`;
   `anchor.id == list_node_executions(conn, done.run_id)[-1].id`. Then
   `engine_world.conn.execute("DELETE FROM node_executions WHERE run_id = ?", (done.run_id,))`;
   `pytest.raises(EngineError, match="run has no declared execution for control audit")`:
   `service.audit_anchor()`.
E3 `test_candidate_node_anchor_requires_a_branch`: `nodes = (node("fit", candidate=True),)`;
   `fanout_loop(..., traversal=TraversalPlan())` with `fanout_initial(engine_world, nodes,
   count=0)`; `pytest.raises(EngineError, match="control anchor requires current candidate")`:
   `service.audit_anchor()`.
E4 `test_anchor_rejects_duplicate_and_foreign_pending_executions` parametrize `case` in
   `("duplicate", "foreign")`: `nodes = (node(),)`; `loop = engine_world.loop(nodes, complete)`;
   duplicate: `loop.checkpointer.save(engine_world.initial(nodes))`; two
   `create_node_execution(conn, run_id=engine_world.paths.run_id, node_name="alpha", seq=1,
   status="pending")`; → `pytest.raises(PersistenceError, match="duplicate active execution
   anchors")`. foreign: save `RunState.model_validate({**engine_world.initial(nodes).model_dump(),
   "status": "awaiting_user", "pending_result": BoundaryResult(node_name="alpha",
   execution_id="ne_foreign", result=NodeResult(status="awaiting_user")).model_dump()})` →
   `pytest.raises(PersistenceError, match="control audit requires a same-run pending
   execution")`. Both via `RunLifecycle(loop, engine_world.initial(nodes)).audit_anchor()`.
E5 `test_shared_budget_stop_becomes_failed_boundary`: executor raises
   `BoundaryStopError("budget_exhausted")`; `state = await RunLifecycle(engine_world.loop((node(),),
   work), engine_world.initial((node(),))).start()` → `status == "failed"`,
   `pending_result.result.error == Failure(error_class="data", message="Fit budget exhausted")`.
E6 `test_lifecycle_callback_actions_and_binding` parametrize `action` in `("cancel", "resume",
   "none", "binding")` with `minimal_contract` fixture: `nodes = (node(),)`; `service =
   RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))`; `ctx =
   dataclasses.replace(context(engine_world.conn, minimal_contract, FakeClient()),
   state=service.initial)` (except `"binding"`: unreplaced context). cancel:
   `InterruptClass(category="lifecycle", lifecycle_action="cancel")` → response `text == "Run
   cancelled."`. resume: first `await service.pause()` (status paused), then action resume →
   `text == "Run completed."`. none: `InterruptClass(category="qa")` → `pytest.raises(EngineError,
   match="lifecycle callback requires an action")`. binding: lifecycle cancel class →
   `match="lifecycle control binding mismatch"`. (`context`, `FakeClient` from
   `tests.test_control_router`; `InterruptClass` from `statlab.graph.router`.)
E7 `test_resume_rejects_running_and_pause_rejects_vanished_request`: executor blocks on
   `release` after setting `entered` (like `test_cancel_dominates...`); `running =
   create_task(service.start())`; `await entered.wait()`; `pytest.raises(EngineError,
   match="run is already executing")`: `service.resume()`; `pausing = create_task(
   service.pause())`; `await asyncio.sleep(0)`; `service.loop.control.resume()`;
   `release.set()`; `pytest.raises(EngineError, match="lifecycle request disappeared before
   drain")`: `await pausing`; `(await running).status == "completed"`. Also (sync part)
   `control = RunControl()`; `control.request_cancel()`; `pytest.raises(EngineError,
   match="cancelled control cannot resume")`: `control.resume()`.
E8 (`test_control_gates.py`) `test_restart_repairs_mode_written_before_gate_preparation`:
   `lifecycle, service, seen = await shared_gate(engine_world)`; gate; `transition_gate(...,
   status="answered", answer_json=GateAnswer(gate_id=gate.id,
   action="approve_all_remaining").model_dump_json())`; `update_run_mode(conn, run_id,
   "autonomous")`; `lifecycle.initial = lifecycle.initial.model_copy(update={"mode":
   "autonomous"})`; `final = await lifecycle.start()` → `completed`, `final.mode ==
   "autonomous"`, `len(seen) == 2`, gate status `"applied"`.
E9 (`test_candidate_fanout.py`, end of `test_repeated_design_halt_reload_and_reconcile`):
   `anchor = await recovering.ensure_audit_anchor(start)`; if `outcome == "awaiting_user"`:
   `anchor.id == state.pending_result.execution_id`; else `anchor.id == executions[-1].id`.
E10 (`test_candidate_fanout_tools.py`) `test_initial_bootstrap_stops_on_budget_or_control`
   parametrize `case` in `("budget_before", "budget_after", "pause")`, fixtures as
   `test_shared_heavy_budget_refusal`: `nodes = (node("design"), node("fit", candidate=True))`;
   `session = FakeSession((heavy_tool(),))`; inside `async with McpClient(...) as client`:
   `executions: list[str] = []`; executor (design only) appends `inv.execution.id`; for
   `budget_before` records a `fixture_fit` tool call with `duration_ms=1000` on it (as the
   existing test); returns completed. `planner(state, wave)`: for `budget_after` records the same
   heavy call on `executions[0]`; returns `specs(1)`; for other cases `pytest.fail("planner must
   not run")` only when `case == "budget_before"`; for `pause` the planner is never reached.
   `control = RunControl()`; for `pause` call `control.request_pause()` inside the design
   executor before returning. `runner = loop(world, nodes, execute,
   traversal=TraversalPlan(design_node="design"), constraints=Constraints(
   max_total_fit_seconds=1), control=control)`; `runner.candidate_planner = planner`;
   `ToolBoundary(client, runner.control, runner.budget)` (discovers the heavy tool).
   budget cases: `pytest.raises(EngineError, match="initial candidate bootstrap has no remaining
   fit budget")`: `runner.run(initial(world, nodes, 0))`. pause: result `status == "paused"`,
   `result.branches == ()`. If the pause case stops before bootstrap (planner never called and
   engine 828-830 still uncovered) main will re-plan; just report.
E11 (`test_control_qa_status.py`) `test_polished_status_streams_through_prose_sink`: local
   `class ProseClient(FakeClient)` overriding `complete_prose` (same signature as
   `LlmClient.complete_prose`) that appends `model` to `self.calls`-compatible list, awaits
   `sink("Brief extra context", False)` and returns `output.model_validate({"text": "Brief extra
   context"})`; `chunks: list[str] = []`; `async def sink(text: str, reset: bool) -> None:
   chunks.append(text)`; `ctx = replace(context(migrated_db, minimal_contract, ProseClient()),
   prose_sink=sink)`; `polished = await explain_status("status", ctx, polish=True)` →
   `polished.text == "Brief extra context"`, `chunks == ["Brief extra context"]`.
Checks: Ruff/format/mypy on the three source + six test files; `LOKY_MAX_CPU_COUNT=1 make
test-fast TESTS='tests/test_control_lifecycle.py tests/test_control_gates.py
tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py tests/test_control_qa_status.py
tests/test_restart_reconciliation.py tests/test_graph_engine.py'` → `...-s8.log`.

### Stage S9 — rewind projection and fork service

Owned: `backend/statlab/graph/rewind.py`, `tests/test_control_rewind.py`. Targets rewind.py 45, 75,
107, 158, 164->159, 176, 190, 215, 223, 246->244, 250, 309, 338.

Unreachable, removed (source):
- `_existing` (156-158), `fork` (213-215) and `_finish_parent` (307-309): each runs after
  `self.parent.state()`/`halt_for_fork`, which already raises `PersistenceError("run does not
  exist")` for a missing run. Replace each `x = get_run(self.conn, <id>)` + `if x is None: raise
  PersistenceError("fork parent does not exist")` with `x = require_run(self.conn, <id>)`
  (import from repository; keep `get_run` only if still used).
- `fork`: `fork` checks every retained row is unpruned and its file exists immediately before
  `fork_artifacts`, which links exactly those rows; `validate_checkpoint_ownership` returns the
  projection's retained refs. Replace `linked = fork_artifacts(self.paths, paths, retained)` +
  the `if frozenset(linked) != child_state.retained_refs: raise ...` with the bare call
  `fork_artifacts(self.paths, paths, retained)`.

Tests (`tests/test_control_rewind.py`; fork-service tests share this inline setup:
`nodes = (node("prefix"), node("target"), node("tail"))`; `lifecycle =
RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))`; `source = await
lifecycle.start()`; `immutable_inputs(engine_world)`; `parent = get_run(conn, source.run_id)`):
W1 `test_projection_rejects_unreached_missing_shared_and_unapproved_frontiers` (sync):
   a) `state = RunState.initial(run_id="r_frontier", spine_id="synthetic", nodes=(node("alpha"),
      node("beta")), mode="autonomous")` → `pytest.raises(EngineError, match="rewind frontier
      has not been reached")`: `project_frontier(state, "beta")`.
   b) `source = wave_state()`; `running = CandidateProgress(public_id="c_01",
      spec=source.branches[0].spec, wave=1, position=3, status="running",
      results=(boundary("prepare", "p1"),))`; `state = RunState.model_validate({**source.model_dump(),
      "status": "running", "cursor": 1, "wave": 1, "shared_results": (boundary("design",
      "design1"),), "branches": (running,)})` → `match="required preceding shared boundary is
      unavailable"`: `project_frontier(state, "train")`.
   c) `state = RunState.model_validate({**source.model_dump(), "wave": 1, "branches": (),
      "shared_results": (boundary("design", "design1"), boundary("overlap", "overlap1"),
      boundary("tail", "tail1"))})` → `match="candidate frontier has no approved
      specifications"`: `project_frontier(state, "prepare")`.
   (If `RunState` rejects b) or c), stop and report the message.)
W2 `test_fork_skips_unpublished_request_and_rejects_unloadable_published_fork`: setup;
   `unpublished = create_run(conn, project_id=parent.project_id, contract_id=parent.contract_id,
   mode="autonomous", config_json=json.dumps({"fork": {"request_id": "g_retry", "target":
   "target", "published": False}}), forked_from_run_id=parent.id, status="cancelled")`;
   `result = await service_for(engine_world, lifecycle, nodes).fork("target",
   request_id="g_retry")`; `result.new_run_id != unpublished.id`. Then `create_run(...)` the same
   way with `{"request_id": "g_lost", "target": "target", "published": True}` (no checkpoint);
   `pytest.raises(PersistenceError, match="published fork checkpoint is unavailable")`:
   `service_for(...).fork("target", request_id="g_lost")`.
W3 `test_fork_rejects_misbound_resolver_and_missing_immutable_inputs`: setup;
   `pytest.raises(EngineError, match="fork resolver must return a fresh correctly bound
   lifecycle")`: `ForkService(lifecycle, resolver=lambda paths, state: lifecycle).fork("target")`
   (use a typed local `def` instead of a lambda if mypy requires). Then
   `engine_world.paths.contract_snapshot_json.unlink()`; `pytest.raises(PersistenceError,
   match="fork requires immutable contract and skill inputs")`: `service_for(...).fork("target")`.
W4 `test_fork_without_lockfile_copies_only_present_inputs`: setup;
   `(engine_world.paths.root / "uv.lock").unlink()`; `fork = await service_for(...).fork(
   "target")`; `paths = fork.lifecycle.loop.checkpointer.paths`;
   `paths.contract_snapshot_json.is_file()`, `paths.skill_snapshot_md.is_file()`,
   `not (paths.root / "uv.lock").exists()`.
W5 `test_fork_control_binding_mismatch(engine_world, minimal_contract)`: setup;
   `pytest.raises(EngineError, match="fork control binding mismatch")`:
   `service_for(...).handle("rewind", InterruptClass(category="qa"), context(engine_world.conn,
   minimal_contract, FakeClient()))`.
Checks: Ruff/format/mypy on both owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_control_rewind.py tests/test_storage_fork.py tests/test_api_runs.py'` →
`...-s9.log`.

#### S5 receipt and correction S5a

S5: persistence `b31bf198…651b8`, tests `9bbb53c7…f1f9e`, champion tests `61b4afae…91f191`;
checks exit 0 (`/tmp/offline-s5.log`). Main coverage: domains/rewind 100% (53 covered by rewind
suites), persistence only `50->exit`: the `temporary is None` false arc on a normal exit is
impossible (the `with` either fails before assignment, which leaves by exception, or assigns).
S5a (persistence.py `_atomic_manifest`, branch-free, same semantics): body becomes
`fd, name = tempfile.mkstemp(dir=path.parent)`; `temporary = Path(name)`; `try:` → `with
os.fdopen(fd, "w", encoding="utf-8") as stream:` (json.dump with the same arguments, `flush`,
`os.fsync(stream.fileno())`), then `temporary.replace(path)`; `finally:`
`temporary.unlink(missing_ok=True)`. Remove the `temporary: Path | None = None` declaration and
the `if temporary is not None` guard. P1 unchanged. Checks: Ruff/format/mypy on persistence.py;
`make test-fast TESTS=tests/test_nodes_persistence.py` → `...-s5a.log`.

S5a accepted: persistence `e37f7026…1d7a2`; checks exit 0 (`...-s5a.log`). S5 complete.

### Stage S10 — project API corruption/limit paths and application error handlers

Owned: `backend/statlab/api/projects.py`, `tests/test_api_projects.py`. Targets projects 130-131,
133, 135, 186, 188, 197, 208-209, 233, 241-242, 256, 336, 340, 354, 360, 366-369, 378-379,
383->385, 438, 451, 465, 480; main 48, 53, 156-157, 162-163.

Unreachable, removed (projects.py):
- `persist_contract`: `if not paths.contracts.resolve().is_relative_to(paths.root.resolve()):
  raise HTTPException(500, "contracts directory escapes project root")` — `self.paths(project)`
  (`_project_paths`) already refuses any child resolving outside the root, with no await in
  between; and `except FileExistsError: raise HTTPException(500, "contract destination already
  exists")` — the destination name is a fresh random `new_id("ct")`; any OS failure still reaches
  the app's `OSError` handler (500). Delete both (keep `try/finally` cleanup).
- `create`: the loop `for directory in (paths.root, paths.datasets, paths.contracts,
  paths.runs): if not directory.resolve().is_relative_to(...data_dir...): raise ...` —
  `_project_paths` (two lines above) already guarantees root ⊂ data_dir and children ⊂ root.
  Delete the loop. Also delete the `try/except PersistenceError` around `create_project`
  (the slug was checked absent in the same synchronous section; no await interleaves) — call
  `row = create_project(...)` directly; drop the `PersistenceError` import if unused.
- `create` metadata write: replace the `temporary: Path | None` + `NamedTemporaryFile` block with:
  `try: fd, name = tempfile.mkstemp(dir=paths.root)` / `except OSError: raise
  HTTPException(500, "project was created but metadata could not be written") from None`;
  `temporary = Path(name)`; `try:` `with os.fdopen(fd, "wb") as handle:
  handle.write(row.model_dump_json().encode("utf-8"))`; `os.link(temporary,
  paths.project_json)`; `except FileExistsError: raise HTTPException(409, "project metadata
  already exists") from None`; `except OSError: raise HTTPException(500, "project was created but
  metadata could not be written") from None`; `finally: temporary.unlink(missing_ok=True)`;
  then `return row`. (The previous `temporary is None` arc on normal flow was impossible.)

Tests (`tests/test_api_projects.py`; `_open_project_api` as existing; DB tampering through a
typed helper `def _write(db_path: Path, sql: str, params: tuple[str, ...]) -> None` that opens
`sqlite3.connect(db_path)`, executes, commits and closes in `finally`; filesystem permission
changes are always restored in `finally`):
A1 `test_bounded_file_rejects_bad_content_length` parametrize `(value, status)`:
   `("abc", 422)`, `("-1", 422)`, `(str(1024 + 65537), 413)`: `Request` scope like the existing
   oversize case with headers `[(b"content-type", b"multipart/form-data; boundary=x"),
   (b"content-length", value.encode())]` and a `receive` returning one empty final body;
   `pytest.raises(HTTPException)` around `async with bounded_file(request, limit=1024)`;
   `status_code == status`.
A2 `test_project_creation_conflicts_and_metadata_write_failure`: open api; duplicate
   `POST /api/projects {"slug": "demo", "name": "Again"}` → 409. Pre-create
   `ProjectPaths(settings.data_dir, "locked")` root plus its `datasets`, `contracts`, `runs`
   directories, then `chmod(root, 0o500)`; `POST {"slug": "locked", "name": "Locked"}` → 500 with
   detail `"project was created but metadata could not be written"`; restore `0o700`.
A3 `test_stored_project_path_corruption_returns_500` parametrize `case` in `("slug", "escape")`:
   open api; slug: `_write(db, "UPDATE projects SET slug = ? WHERE id = ?", ("Bad Slug!",
   project["id"]))`; escape: `root = ProjectPaths(settings.data_dir, "demo").root`;
   `outside = tmp_path / "outside"`; `root.rename(outside)`; `root.symlink_to(outside,
   target_is_directory=True)`; `GET /api/projects/{id}` → 500 with detail `"stored project path
   is invalid"` / `"project path escapes data directory"` respectively.
A4 `test_stored_dataset_corruption_and_missing_file_return_500` parametrize `case` in
   `("path", "missing")`: open api; upload `data.csv` (`b"a,b\n1,2\n3,4\n"`); path:
   `_write(db, "UPDATE datasets SET path = ? WHERE id = ?", (str(tmp_path / "elsewhere.csv"),
   row["id"]))`; missing: `Path(row["path"]).unlink()`; `POST
   /api/projects/{id}/contracts/validate` with `{"dataset_id": row["id"], "contract":
   _contract().model_dump(mode="json")}` → 500, detail `"stored dataset path is invalid"` /
   `"stored dataset file is missing"`.
A5 `test_malformed_setup_summaries_are_internal_errors` parametrize `endpoint` in `("validate",
   "propose")`: open api; upload as A4; queue `session.responses.append(ToolSuccess(summary=
   {"valid": True, "mismatches": ["not-an-object"]}))` for validate, or
   `ToolSuccess(summary={"mismatches": "not-a-list"})` for propose; POST
   `.../contracts/validate` (body as A4) or `.../contracts/propose` (body: inspect the existing
   proposal tests and use the same request shape with `_contract()`; if the shape needs a
   decision, stop and ask) → 500 with `{"detail": "internal error"}`.
A6 `test_upload_filesystem_corruption_and_bad_dimensions` parametrize `case` in `("symlink",
   "corrupt", "dimensions", "unwritable")`: open api; `content = b"a,b\nz,9\n"`;
   `sha = hashlib.sha256(content).hexdigest()`; `datasets = ProjectPaths(settings.data_dir,
   "demo").datasets`; `datasets.mkdir(parents=True, exist_ok=True)`.
   symlink: `(datasets / f"{sha}.csv").symlink_to(tmp_path / "outside.csv")` (write the outside
   file first) → upload → 500 `"dataset path escapes datasets directory"`.
   corrupt: `(datasets / f"{sha}.csv").write_bytes(b"different")` → 500 `"content-addressed
   dataset is corrupt"`.
   dimensions: `session.responses.append(ToolSuccess(summary={"rows": 0, "cols": 3}))` → 500
   `{"detail": "internal error"}`.
   unwritable: `chmod(datasets, 0o500)` → 500 `"could not store uploaded dataset"`; restore.
   All: datasets count unchanged (`_readonly_count`).
A7 `test_contract_write_failure_uses_os_error_handler`: open api; `contracts =
   ProjectPaths(settings.data_dir, "demo").contracts`; `contracts.mkdir(parents=True,
   exist_ok=True)`; `chmod(contracts, 0o500)`; upload a contract the way
   `test_contract_upload_versions_validation_findings_and_no_runs` does (copy its request shape)
   → 500 `{"detail": "internal error"}`; restore.
A8 `test_routes_before_lifespan_are_unavailable(tmp_path)`: `app = create_app(Settings(...))`
   (as `_open_project_api`, no lifespan entered); `httpx.AsyncClient(transport=
   httpx.ASGITransport(app=app), base_url="http://test")`; `GET /api/projects` → 503; and a
   run-registry route (`GET /api/runs/r_missing` — if that path does not exist in
   `api/runs.py`, use the first registry-admitted route listed there and report it) → 503.
Checks: Ruff/format/mypy on both files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_api_projects.py tests/test_api_runs.py tests/test_main.py'` → `...-s10.log`.

#### S6 receipt and S6a

S6: foundation `67ab5ff5…9b9963`, tests `7426785b…812bfa`; checks exit 0 (`/tmp/offline-s6.log`).
Main coverage: decisions 100%; foundation only `87->92` (`_tool_summary` with `tools` absent).
S6a: in F1 add `pytest.raises(EngineError, match="missing tool evidence: load_csv")`:
`_tool_summary(NodeResult(status="completed"), "load_csv")`. Checks: Ruff/format/mypy on the
test file; `make test-fast TESTS=tests/test_nodes_foundation.py` → `...-s6a.log`.

S6a accepted: tests `cd620f38…ec14f4`; checks exit 0 (`...-s6a.log`). S6 complete.

### Stage S11 — run restart reconstruction guards (api/runs.py `RunRegistry.binding`/`boot`)

Owned: `backend/statlab/api/runs.py`, `tests/test_api_runs.py`. Targets runs.py 202, 205,
208-209, 212, 215, 218, 220, 227, 240, 246, 248, 250, 256, 262, 266-267, 283-284, 294, 303, 309,
352. Design note (unchanged, recorded): boot fails closed — any corrupted non-terminal run stops
application startup, as the existing `test_historical_restart_rejects_invalid_snapshot` asserts.

Unreachable, removed (runs.py):
- `binding`: the `actual_initial` identity check (`if actual_initial.run_id != row.id or ...:
  raise PersistenceError("run initial state identity mismatch")`) — callers pass either `None`,
  `create()`'s `RunState.initial(run_id=row.id, mode=row.mode, ...)`, or the fork resolver's
  projection, whose run id is the child row id and whose mode is copied from the parent row
  that became the child row's mode. Delete the check.
- `binding.resolver`: `child_row = get_run(...)` + `if child_row is None: raise
  PersistenceError("fork child run is missing")` → `child_row = require_run(self.resources.conn,
  child_paths.run_id)` (ForkService calls it right after `create_run` of that child).
- `boot`: `if run_id not in self.bindings: raise PersistenceError("recovery task has no bound
  run")` — every recovery task comes from a `resolver(row)` call that stored
  `self.bindings[row.id]`. Delete the check (keep `self._adopt(run_id, task)`).

Test (`tests/test_api_runs.py`): new `test_restart_rejects_corrupted_run_reconstruction`,
parametrized over `case`, modeled on `test_historical_restart_rejects_invalid_snapshot`: first
app: `_create_run(api)` (guided) → 201, `await _gate(api, run_id)`; capture `paths = RunPaths(
api.settings.data_dir, "demo", run_id)`, `row = get_run(api.conn, run_id)`, the dataset row
(`get_dataset(api.conn, api.dataset_id)`) and contract row (`get_contract(api.conn,
api.contract_id)`); leave the app. Tamper with a write connection helper
`_tamper(db_path: Path, sql: str, params: tuple[str, ...]) -> None` (connect, execute, commit,
close in `finally`; foreign keys left at SQLite's default off). `config = json.loads(
row.config_json)`; "set config" = `_tamper(db, "UPDATE runs SET config_json = ? WHERE id = ?",
(json.dumps(<modified config>), run_id))`. Then `pytest.raises(PersistenceError, match=message)`
around `_open_run_api(tmp_path, monkeypatch, records=FixtureRecords(), setup=False,
existing_run_id=run_id)` (body `pytest.fail(...)`), except where noted. Cases → (tamper,
message):
- `run_id`: `_tamper(db, "INSERT INTO runs (id, project_id, contract_id, mode, status,
  started_at, config_json) SELECT 'bad id', project_id, contract_id, mode, status, started_at,
  config_json FROM runs WHERE id = ?", (run_id,))` → "stored run id is invalid".
- `project`: `UPDATE runs SET project_id = 'p_missing' WHERE id = ?` → "run project is missing".
- `config`: `UPDATE runs SET config_json = '{}' WHERE id = ?` → "stored run config is invalid".
- `contract_owner`: `UPDATE runs SET contract_id = 'ct_missing' WHERE id = ?` → "run contract
  ownership mismatch".
- `dataset_owner`: config `dataset_id` = `"ds_missing"` → "run dataset ownership mismatch".
- `session_owner`: config `session_id` = `"ss_missing"` → "run session ownership mismatch".
- `memory_owner`: config `memory` = `[{**<a MemoryRow dump with mode="json" from the first run's
  config memory if non-empty>}]` — the fixture project has no memory, so instead: in the first
  app call `remember(api.conn, project_id=<second project id>, key="foreign", value="fact")`
  after creating a second project through `POST /api/projects {"slug": "other", "name":
  "Other"}`; set config `memory` to `[m.model_dump(mode="json") for m in read_memory(api.conn,
  <second project id>)]` → "run memory ownership mismatch".
- `run_root`: move `paths.root` to `tmp_path / "moved-run"` and symlink `paths.root` to it →
  "run path escapes project runs".
- `run_child`: move `paths.artifacts` to `tmp_path / "moved-artifacts"` and symlink it back →
  "run child path escapes run root".
- `contract_path`: copy the contract file to `tmp_path / "elsewhere.json"`; `UPDATE contracts SET
  path = ? WHERE id = ?` with that path → "stored contract path is invalid".
- `contract_bytes`: append `b" "` to the stored contract file (`contract_row.path`) → "stored
  contract file differs from SQL".
- `dataset_hash`: config `dataset_sha256` = `"0" * 64` → "run dataset hash differs from SQL".
- `dataset_path`: copy the dataset file to `tmp_path / "elsewhere.csv"`; `UPDATE datasets SET
  path = ? WHERE id = ?` → "stored dataset path is invalid".
- `dataset_bytes`: overwrite the dataset file (`dataset_row.path`) with `b"a,b\n9,9\n"` →
  "stored dataset bytes differ from run config".
- `incomplete`: `incomplete = DataContract(target=TargetSpec(column="a")).model_dump_json()`;
  `UPDATE contracts SET contract_json = ? WHERE id = ?`; write the same text to
  `contract_row.path` → "stored run contract is incomplete".
- `skill`: write `b"not a skill policy\n"` to `paths.skill_snapshot_md` → "run skill snapshot is
  invalid".
- `new_model` (no raise): replace `b"## problem_definition"` in the skill snapshot with
  `b"### Model\n\nopenai/new-test\n\n## problem_definition"` (same edit as the old-model case);
  open with `reject_old_model=True`; the context yields; assert the last
  `records.provider_checks` entry's models contain `"openai/new-test"` and the run's binding is in
  `restarted.registry.bindings`.
If any case raises a different message (or a precondition such as the dataset path or symlink
handling differs), stop and report the case and message — do not adapt.
Checks: Ruff/format/mypy on both owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS=tests/test_api_runs.py` → `...-s11.log`.

#### S7 stop and correction S7a

S7 (`/tmp/offline-s7.log`): only S1 case c) failed — spec premise (halted branch needs its
failing pending boundary; same rule as S4b). S7a: build the failed branch as
`CandidateProgress(public_id=b.public_id, spec=b.spec, results=b.results[:4], position=10,
status="failed", pending_result=BoundaryResult(node_name="model_interpretation",
execution_id="failed-interpretation", result=NodeResult(status="failed",
error=Failure(error_class="data", message="interpretation refused"))))` with `b =
h.state.branches[0]` (assert the first four result node names are preprocessing,
feature_engineering, model_training, model_evaluation) and `failed = RunState.model_validate(
{**h.state.model_dump(mode="json"), "branches": [<that>.model_dump(mode="json")]})`; expectation
unchanged. Rerun the full S7 selector, log `...-s7a.log`; report the pass count if visible
(`-q` dots count is acceptable).

### Stage S12 — run API lifecycle/shutdown paths and SSE loop (after S11)

Owned: `backend/statlab/api/runs.py`, `backend/statlab/api/stream.py`, `tests/test_api_runs.py`,
`tests/test_api_stream.py`. Targets runs.py 73, 80, 114, 146, 175, 374-391, 396->395, 399,
411->413, 424, 427-429, 446, 464, 471, 476, 495, 498, 506, 526, 541, 543, 613, 627, 694;
stream.py 46->64, 59-60, 100.

Unreachable, removed/restructured (source):
- runs.py `request()`: `task = asyncio.current_task()` + `if task is None: raise EngineError(...)`
  → `task = cast("asyncio.Task[object]", asyncio.current_task())` with comment `# Route handlers
  always run inside an event-loop task.` (`from typing import cast`).
- runs.py `shutdown`: after `await asyncio.gather(*tasks, return_exceptions=True)` every tracked
  task is done; replace `for run_id, task in ...: if task.done(): self._completed(run_id, task)`
  with an unconditional `self._completed(run_id, task)` in that loop.
- runs.py `create`: delete `if _RUN_ID.fullmatch(row.id) is None: raise PersistenceError(
  "created run id is invalid")` and `if os.path.lexists(paths.root): raise PersistenceError(
  "run destination already exists")` (fresh `new_id` from `create_run`; `paths.root.mkdir(...,
  exist_ok=False)` already fails closed on a collision). Drop `os` import if unused.
- runs.py `snapshot`, `_resume`, `message`: the `get_run` + `if ... is None: raise
  HTTPException(404, "run not found")` after `self.run(run_id)` (which already 404s; no deletion
  path exists) → `require_run(self.resources.conn, run_id)`.
- runs.py `_lifecycle`: delete the `context.conn`/`context.state.run_id` binding check (its only
  caller, `message`, builds that context from the same binding and connection).
- stream.py `stream_events`: `EventBus.subscribe` never finishes on its own; declare
  `next_event: asyncio.Task[Event] = ...`, `close_wait: asyncio.Task[bool] = ...` (non-Optional),
  loop `while True:`, replace the `try: event = next_event.result() except StopAsyncIteration:
  break` with `event = next_event.result()`, and in `finally` use `remaining = (next_event,
  close_wait)` (drop the `is not None` filter).

Tests (`tests/test_api_runs.py` unless noted; reuse `_open_run_api`, `_create_run`, `_gate`):
K1 (sync) `test_run_request_models_reject_blank_and_misplaced_fields` parametrize `(model,
   payload, message)`: `RunCreate`, `{"mode": "guided", "dataset_ids": [" "], "contract_id":
   "ct"}`, "dataset id must not be blank"; `RunCreate`, `{"mode": "guided", "dataset_ids": ["ds"],
   "contract_id": " "}`, "contract id must not be blank"; `RunConfig`, `{"dataset_id": " ",
   "dataset_sha256": "0" * 64, "session_id": "ss", "memory": []}`, "identifier must not be
   blank"; `ControlRequest`, `{"action": "pause", "target": "intake"}`, "target and request_id are
   rewind only" → `pytest.raises(ValidationError, match=message)`: `model.model_validate(payload)`.
K2 `test_shutdown_pause_failures` parametrize `case` in `("engine", "tolerated", "foreign")`:
   records with `replies=deque([_decision()])`; `pytest.raises(EngineError, match="fixture pause
   refused")` for engine, `pytest.raises(RuntimeError, match="fixture disk failure")` for foreign,
   no raise for tolerated — around the whole `async with _open_run_api(...) as api:` block. Inside:
   `created = await _create_run(api, mode="autonomous", flags=ExecutorFlags(before_block=True))`;
   `run_id`; `executor = api.records.executors[run_id]`; `await executor.first_entered.wait()`;
   `async def failing_pause() -> RunState:` sets `executor.first_release` and
   `executor.next_release`; for tolerated calls `update_run_status(api.conn, run_id,
   status="paused", current_node=None)`; then raises `EngineError("fixture pause refused")`
   (engine/tolerated) or `RuntimeError("fixture disk failure")` (foreign).
   `monkeypatch.setattr(api.registry.bindings[run_id].lifecycle, "pause", failing_pause)`.
K3 `test_registry_launch_guards_and_cancelled_task_accounting`: `_create_run(api,
   mode="autonomous", flags=ExecutorFlags(before_block=True))`; `await
   executor.first_entered.wait()`; `binding = api.registry.bindings[run_id]`;
   `api.registry._launch(binding, deduplicate=True) is False`; `pytest.raises(HTTPException)` →
   `status_code == 409`: `api.registry._launch(binding)`; `task =
   api.registry.execution_tasks[run_id]`; `task.cancel()`; `with suppress(asyncio.CancelledError):
   await task`; `GET /api/runs/{run_id}` → 200 and `json()["execution_failed"] is True`. Then
   `api.registry.begin_shutdown()`; `pytest.raises(HTTPException)` → 503:
   `api.registry._launch(binding)`.
K4 `test_settle_gate_task_waits_for_live_task_with_pending_gate`: guided run; `await _gate(api,
   run_id)`; `live = asyncio.create_task(asyncio.sleep(0))`;
   `api.registry.execution_tasks[run_id] = live`; `await
   api.registry._settle_gate_task(api.registry.bindings[run_id])`; `live.done()`.
K5 `test_create_rejects_tampered_inputs_and_missing_lockfile` parametrize `case` in
   `("contract", "dataset", "lockfile", "closing")`: open api; contract: append `b" "` to
   `get_contract(api.conn, api.contract_id).path` file → POST create (`_create_run`) → 500;
   dataset: overwrite the dataset file with `b"a,b\n9,9\n"` → 500; lockfile:
   `monkeypatch.chdir(tmp_path)` (no `uv.lock` there) → 500 and the run row is left
   `cancelled` (`get_run(...).status == "cancelled"` for the only run of the project); closing:
   `api.session.block_next = True`; `task = asyncio.create_task(_create_run(api))`; `await
   api.session.entered.wait()`; `api.registry.begin_shutdown()`; `api.session.release.set()`;
   `(await task).status_code == 503`. All: no execution task launched for the project
   (`api.registry.execution_tasks == {}`).
K6 `test_resume_rejects_awaiting_run_and_router_engine_error_is_409`: replies
   `[_decision(), InterruptClass(category="decision").model_dump_json(),
   GateAnswer(gate_id="g_missing", action="approve").model_dump_json()]`; guided run; `await
   _gate(api, run_id)`; `POST /api/runs/{run_id}/control {"action": "resume"}` → 409;
   `POST /api/runs/{run_id}/messages {"text": "approve it"}` → 409.
K7 (`tests/test_api_stream.py`) `test_events_route_rejects_while_closing`: open the stream test
   app the way that file's existing route tests do (reuse its helper; if none supports a run,
   use `_open_run_api` from test_api_runs and a guided run) ; `registry.begin_shutdown()`;
   `GET /api/runs/{run_id}/events` → 503.
If a premise fails (e.g. the cancelled task is not reported failed, or K6's message is not 409),
stop and report — a mismatch may be a real defect.
Checks: Ruff/format/mypy on the four owned files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_api_runs.py tests/test_api_stream.py'` → `...-s12.log`.

S7a accepted: design_inference `dd258a04…a55cff`, tests design `6ac10b27…13375c`, search `eb93a8ce…5538`, binding `97989ead…25fd9`, pipeline `63b6e44d…602db`; checks exit 0 (`...-s7a.log`). S7 complete.

S8a correction (E4 duplicate premise): `ux_node_executions_identity` forbids two rows with equal
(run, candidate, node, attempt). The engine guard compares node/candidate/status only, so the
reachable duplicate is two active attempts. Second `create_node_execution` call uses
`seq=1, attempt=2, status="pending"`; first call unchanged (`attempt` default 1). Expected
`PersistenceError` "duplicate active execution anchors" unchanged. Re-run the S8 focused checks.

S8 accepted (after S8a): candidates `e4ee25d0…67d3`, reconciliation `529419a9…754a`, control
`29fae7a3…f1f`, test_control_lifecycle `be88b0d6…d98d`, test_control_gates `b9bcb090…3c1c`,
test_candidate_fanout `6291bfef…81be`, test_candidate_fanout_tools `4f25d7d2…2fd9`,
test_control_qa_status `c00b7cc7…9ff`. Implementer checks exit 0 (`/tmp/offline-s8a.log`); Ruff,
format, mypy clean. E8 tuple element renamed `_service` (RUF059), accepted. Main graph-suite
coverage: no test failures; residual graph gaps re-measured in the full diagnostic map.

S9a correction (W2 premise): `ForkService._existing` calls `Checkpointer.load` without
`allow_missing`, so a missing published checkpoint raises the generic "run checkpoint is missing"
and the `state is None` guard is typing-only. Decision: add `allow_missing=True` to that one
`Checkpointer(paths).load(...)` call in `rewind.py` `_existing`; keep the
`PersistenceError("published fork checkpoint is unavailable")` guard (now the real missing-file
path). Other load errors (unreadable/invalid/identity mismatch) still raise from `load`. W2
unchanged. Re-run S9 checks (Ruff, format, mypy, the three-file pytest selector).

S9 accepted (after S9a): rewind `9f7f68f0…2167`, test_control_rewind `66389515…742c`; implementer
checks exit 0 (`/tmp/offline-s9a.log`); main coverage rewind.py 100% (168 stmts, 68 branches).

#### S10 receipt and correction S10a

S10: projects `1fe87fb8…e0c4`, tests `77918ab9…bcf0`; checks exit 0 (`/tmp/offline-s10.log`).
Main coverage (projects/runs/main suites): projects only 371-372; main 48 (other main gaps belong
to other suites). Diagnosis:
- projects 371-372: after `mkstemp` succeeds in `paths.root`, a non-`FileExistsError` `OSError`
  from the write/link needs a disk failure; the separate mkstemp handler is the testable twin.
- main 48: every route first runs the app dependency `admitted_request` -> `registry()`, which
  503s when unset; `active_resources`/`active_registry` are set and cleared together with no await
  between, and shutdown drains admitted requests, so `resources()` never sees None.
S10a (owned: `backend/statlab/api/projects.py`, `backend/statlab/main.py`; no test edits):
1. projects.py `create`, after `row = create_project(...)`: one handler block replaces the two —
   `try:` { `fd, name = tempfile.mkstemp(dir=paths.root)`; `try:` { `with os.fdopen(fd, "wb")
   as handle: handle.write(row.model_dump_json().encode("utf-8"))`; `os.link(name,
   paths.project_json)` } `finally:` `Path(name).unlink(missing_ok=True)` }
   `except FileExistsError: raise HTTPException(409, "project metadata already exists") from None`
   `except OSError: raise HTTPException(500, "project was created but metadata could not be
   written") from None`; then `return row`. Remove the `temporary` variable. Same statuses/details.
2. main.py `create_app`: delete the `active_resources` declaration; `resources()` body becomes
   `return registry().resources`; lifespan `nonlocal active_registry` only; build
   `active_registry = RunRegistry(APIResources(<same seven arguments in the same order>))`;
   delete `active_resources = None` in the inner `finally`. Nothing else changes.
Checks: Ruff/format/mypy on projects.py and main.py; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_api_projects.py tests/test_api_runs.py tests/test_main.py
tests/test_api_stream.py'` -> `/tmp/offline-s10a.log`.

S10 accepted (after S10a): projects `ca00c2d3…922f`, main `852e1cdb…03b6`, tests `77918ab9…bcf0`;
checks exit 0 (`/tmp/offline-s10a.log`). Main coverage: projects 100%; main only 164-165
(`ConfigError` handler, S13).

#### S11 stop and correction S11a

S11 (`/tmp/offline-s11.log`): 15/17 cases pass; two spec defects.
- `project`: `connect()` sets `PRAGMA foreign_keys = ON` and `runs.project_id` cascades on
  project delete, so a run without its project cannot exist; boot also lists runs per existing
  project, so the orphan is never bound. The `binding` guard is typing-only narrowing.
- `incomplete`: the contracts column is `json`, not `contract_json`.
S11a (owned additionally: `backend/statlab/persistence/repository.py`,
`tests/test_repository.py`):
1. repository.py: directly after `get_project`, add `def require_project(conn:
   sqlite3.Connection, project_id: str) -> ProjectRow` mirroring `require_run`: `row =
   get_project(conn, project_id)`; `if row is None: raise PersistenceError("project does not
   exist")`; `return row`. Export it wherever `require_run` is exported (if `__all__` lists it).
2. runs.py `binding`: `project = require_project(self.resources.conn, row.project_id)` replaces the
   `get_project` call plus `if project is None: raise PersistenceError("run project is missing")`;
   adjust the repository import (drop `get_project` only if unused).
3. tests/test_api_runs.py: delete the `project` case from `_RESTART_CASES`; in the `incomplete`
   case use `UPDATE contracts SET json = ? WHERE id = ?`.
4. tests/test_repository.py: after `test_require_run_rejects_missing_run`, add
   `test_require_project_rejects_missing_project(migrated_db)`: `pytest.raises(PersistenceError,
   match="project does not exist")`: `require_project(migrated_db, "p_missing")`.
Checks: Ruff/format/mypy on the four files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_api_runs.py tests/test_repository.py'` -> `/tmp/offline-s11a.log`.

S11 accepted (after S11a): runs `c0674d56…6b5d`, test_api_runs `84d37abe…bfc5b`, repository
`85a67f00…821d`, test_repository `2bba8c68…ee24`; checks exit 0 (`/tmp/offline-s11a.log`). Main
coverage: every S11 target closed; residual runs.py gaps are the S12 targets (line numbers now
shifted about -11 from the S12 list; the S12 recipe is content-addressed).

### Stage S13 — LLM stream guards, ConfigError handler, engine all-failed anchor (after S12)

Owned: `backend/statlab/llm/client.py`, `tests/test_llm_stream.py`, `tests/test_api_runs.py`
(one new test only), `tests/test_control_lifecycle.py` (one new test only). Source of the gap map:
full-suite diagnostic after S8 (`$CLAUDE_JOB_DIR/tmp/full.json`): client 126, 301-302, 405, 419,
427, 434, 460, 479 and Protocol stub arcs 58/60; main 164-165; engine 234.

Source (client.py):
- Delete the `_ProseStream` Protocol class (its one-line stub bodies are never executed, which is
  the uncovered 58/60 arcs) and `runtime_checkable` from the typing import (keep `Protocol`, still
  used by `CallRecovery`). Add `AsyncIterable` to the `collections.abc` import; drop
  `AsyncIterator` only if no longer used.
- `_consume_prose` head becomes: `aclose = getattr(raw, "aclose", None)`; `if not isinstance(raw,
  AsyncIterable) or not callable(aclose): raise LlmCallError(Failure(error_class="bug",
  message="LLM returned a malformed stream"))`. The `finally:` becomes `await aclose()`. Nothing
  else in the function changes. (litellm's `CustomStreamWrapper` has `__aiter__` and `aclose`.)

Tests (`tests/test_llm_stream.py`; reuse `FakeStream`, `chunk`, `_settings`, `_rows`,
`llm_context`, `_empty_sink`, `StreamResult`, `_response`):
L1 parametrize the existing `test_stream_output_without_text_field_rejects_before_provider` over
   `output` in `(NoText, IntText)`: move `NoText` to module level and add module-level `class
   IntText(BaseModel): text: int`; body unchanged (`output=output`; still `calls == 0`).
L2 add `('["text"]', None)` to the parameters of
   `test_partial_text_waits_for_incomplete_or_non_text_values`.
L3 `test_sink_and_record_persistence_failures_keep_sink_error`: as
   `test_sink_persistence_error_propagates_and_closes`, but before the call execute
   `conn.execute("CREATE TRIGGER reject_llm_call BEFORE INSERT ON llm_calls BEGIN SELECT
   RAISE(ABORT, 'blocked'); END")`; add `caplog: pytest.LogCaptureFixture`; assert `error.value is
   failure`, `stream.closed`, `_rows(conn) == []` (use `len(_rows(conn)) == 0`), and
   `"Could not record failed prose request" in caplog.text`.
L4 `test_malformed_stream_items_fail_without_repair` parametrize `(case, message)`:
   `("foreign_item", "LLM returned a malformed stream")`: chunks
   `(cast(ModelResponseStream, object()),)`;
   `("second_choice", "LLM returned a malformed stream")`: chunks `(ModelResponseStream(
   choices=[StreamingChoices(index=1, delta=Delta(content='{"text":"x"}'))],
   model="test-model"),)`;
   `("non_text_fragment", "LLM returned a malformed stream")`: chunks `(ModelResponseStream(
   choices=[StreamingChoices(index=0, delta=Delta(content=["x"]))], model="test-model"),)`;
   `("malformed_usage", "LLM returned malformed usage metadata")`: chunks
   `(ModelResponseStream(choices=[], model="test-model", usage="bad"),)`;
   `("builder_mismatch", "LLM returned a malformed stream")`: chunks `(chunk('{"text":"x"}'),
   chunk(None, usage=True))` and `monkeypatch.setattr("statlab.llm.client.stream_chunk_builder",
   lambda chunks, messages: None)` (typed local `def` if mypy needs it).
   Each: `stream = FakeStream(chunks)`; provider counts calls and returns `stream`; `pytest.raises(
   LlmCallError, match=message)` around `complete_prose(..., output=StreamResult,
   sink=_empty_sink)`; assert `error.value.failure.error_class == "bug"`, `calls == 1`,
   `stream.closed`, `len(_rows(conn)) == 1`. Add `cast` (typing) and `StreamingChoices`/`Delta`
   imports as needed (Delta/StreamingChoices already imported).
M1 (`tests/test_api_runs.py`) `test_skill_snapshot_failure_maps_to_application_unavailable`: open
   the API as the existing create tests do; `monkeypatch.setattr("statlab.api.runs.snapshot_skill",
   <typed local def raising ConfigError("could not snapshot skill policy")>)`; `response = await
   _create_run(api)` → `status_code == 503`, `response.json() == {"detail": "application
   unavailable"}`; the only run row has status `"cancelled"` (`list_runs(api.conn,
   api.project_id)`; import `list_runs` if missing).
E12 (`tests/test_control_lifecycle.py`) `test_anchor_after_all_candidates_failed_uses_failed_
   branch`: `nodes = (node("fit", candidate=True),)`; executor returns `NodeResult(status=
   "failed", error=Failure(error_class="data", message="fit refused"))`; `loop =
   fanout_loop(engine_world, nodes, fail, traversal=TraversalPlan())`; `service =
   RunLifecycle(loop, fanout_initial(engine_world, nodes, count=1))`; `state = await
   service.start()`; `anchor = await service.audit_anchor()`; assert `anchor.node_name == "fit"`
   and `anchor.status == "failed"`. If `start()` or `audit_anchor()` raises, stop and report.
Checks: Ruff/format/mypy on the four files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_llm_stream.py tests/test_llm_client.py tests/test_api_runs.py
tests/test_control_lifecycle.py tests/test_control_qa_status.py'` -> `/tmp/offline-s13.log`.

### Stage S14 — fork audit, checkpoint ownership and repository guards (after S13)

Owned: `backend/statlab/mcp_client.py`, `backend/statlab/persistence/repository.py`,
`tests/test_mcp_client.py`, `tests/test_storage.py`, `tests/test_repository.py`,
`tests/test_candidate_fanout.py` (one new test only). Gaps: mcp_client 205, 207, 226-227, 622,
630, 632, 640, 645, 667-668; storage 184, 187, 190; models 93; repository (content-addressed)
`run_ancestry` except, `create_node_execution` reuse branches, `get_gate`/`list_gates` excepts,
`create_gate` ownership/idempotent/except, `transition_gate` stale/except, `update_run_mode`
missing/except, `list_memory` except, `get_champion_model` run-None/ownership/except,
`_check_model_identity` missing-parent, `save_champion_model` except, `remember_run_outcome`
except. Tampering uses the existing pattern `with transaction(conn): conn.execute(...)`. A blob
`X'FF'` in a TEXT column is returned as bytes and fails `str` validation (invalid UTF-8).

Source:
- mcp_client.py `logged_calls.visit`: delete the `visiting` set, its `if index in visiting: raise
  PersistenceError("tool audit contains a producer cycle")`, `visiting.add(index)` and
  `visiting.remove(index)`. Reason: `visit` only recurses with `before=index` and requires
  `producers[ref] < before`, so stack indices strictly decrease; a cycle is impossible.
- repository.py `get_champion_model`: `run = require_run(conn, run_id)` replaces `get_run` + the
  `"champion run does not exist"` raise.

Tests:
P1 (`tests/test_mcp_client.py`) `test_setup_dataset_requires_entered_client_and_existing_project`
   (`world`, `tmp_path`): unentered `client = McpClient(settings(paths, tmp_path), conn,
   session=FakeSession(()))` → `pytest.raises(EngineError, match="MCP client is not entered")`;
   then entered with `FakeSession((served_tool("setup_dataset", schema, allow_in=["setup"],
   phase="setup"),))` (schema as `test_setup_dataset_uses_fixed_tool_and_skips_lineage`) and
   `project="missing-project"` → `match="setup project does not exist"`; `session.calls == []`.
P2 `test_setup_dataset_refuses_non_finite_contract`: same served tool; `bad =
   DataContract.model_validate({**contract.model_dump(mode="json"), "objective":
   {"success_threshold": float("nan")}})`; `pytest.raises(ToolCallError)` around
   `client.setup_dataset(project=paths.slug, dataset="a" * 64, options=DatasetOptions(),
   contract=bad)`; `failure.error_class == "tool_param"`, `failure.message == "tool parameters
   must be strict JSON"`; `session.calls == []`. If no `ToolCallError` is raised (i.e. NaN does
   not survive `model_dump(mode="json")`), stop and report; main will remove the guard instead.
P3 (`tests/test_storage.py`) extract the setup of `test_fork_visibility_and_real_pruned_producer_
   closure` (from `conn = migrated_db` through the child `record_tool_call`) into a module-level
   `def _fork_audit_world(conn, run_paths, run) -> _ForkAudit` with `@dataclass(frozen=True)
   class _ForkAudit` fields `child: RunRow`, `checkpoint: RunState`, `refs: tuple[ArtifactRef,
   ...]`, `metas: tuple[ArtifactMeta, ...]`, `arguments: tuple[dict[str, JsonValue], ...]`,
   `execution: NodeExecutionRow`, `current: NodeExecutionRow`. Move that test's local imports
   into the helper; import `RunState` for the annotation under `if TYPE_CHECKING:` and
   `NodeExecutionRow` with the existing `statlab.persistence.models` import. The existing test
   calls the helper and keeps every assertion unchanged.
P4 `test_fork_audit_rejects_corrupted_producers` parametrize `(case, message)`; setup `w =
   _fork_audit_world(migrated_db, run_paths, run)`; `conn = migrated_db`; then
   `pytest.raises(PersistenceError, match=message)`: `logged_calls(conn, w.child.id,
   fork_aware=True, checkpoint=w.checkpoint)`:
   - `duplicate` / "duplicate artifact producers": `record_tool_call(conn, run_id=run.id,
     node_execution_id=w.execution.id, tool_name="duplicate", args_json="{}",
     result_json=ToolSuccess(artifacts=(w.metas[0],)).model_dump_json(), duration_ms=1)`.
   - `missing` / "missing an earlier artifact producer": `record_tool_call(conn,
     run_id=w.child.id, node_execution_id=w.current.id, tool_name="dangling",
     args_json=json.dumps({"df": "a_0000000000000099"}), result_json=ToolSuccess()
     .model_dump_json(), duration_ms=1)`.
   - `ownership` / "artifact producer ownership mismatch": `UPDATE artifacts SET produced_by =
     'other' WHERE id = ?` with `str(w.refs[0])`.
   - `execution` / "producer execution ownership mismatch": `UPDATE tool_calls SET
     node_execution_id = ? WHERE tool_name = 'load_csv'` with `w.current.id`.
   - `invalid_row` / "cannot compose ancestor producer audit": `UPDATE artifacts SET pruned_at =
     X'FF' WHERE id = ?` with `str(w.refs[0])`.
P5 `test_checkpoint_ownership_rejects_foreign_or_invalid_retained_artifact` parametrize
   `(case, message)`: `third = create_run(conn, project_id=run.project_id,
   contract_id=run.contract_id, mode="autonomous", config_json="{}")` (artifact_owner only);
   `artifact_owner` / "checkpoint artifact ownership mismatch": `UPDATE artifacts SET run_id = ?
   WHERE id = ?` with `(third.id, str(w.refs[1]))`; `invalid_artifact` / "cannot validate
   checkpoint ownership": `UPDATE artifacts SET pruned_at = X'FF' WHERE id = ?` with
   `str(w.refs[1])`. Call `validate_checkpoint_ownership(conn, w.child.id, w.checkpoint)`.
P6 (`tests/test_candidate_fanout.py`) `test_checkpoint_candidate_spec_tamper_is_rejected(world)`:
   `nodes = (node("fit", candidate=True),)`; executor returns `NodeResult(status="completed")`;
   `state = await loop(world, nodes, execute, traversal=TraversalPlan()).run(initial(world, nodes,
   1))`; `state.status == "completed"`; `validate_checkpoint_ownership(world.conn, state.run_id,
   state)` passes; tamper `UPDATE candidates SET spec_json = '{}' WHERE run_id = ?`; then
   `match="checkpoint candidate ownership mismatch"`.
P7 (`tests/test_repository.py`) `test_reuse_pending_execution_guards(migrated_db, project)`:
   `run = champion_run(...)`; `pending = create_node_execution(conn, run_id=run.id,
   node_name="alpha", seq=1, status="pending")`; `reused = create_node_execution(..., seq=1,
   status="pending", reuse_pending=True)` → `reused.id == pending.id`, `reused.status ==
   "pending"`; `(..., seq=2, reuse_pending=True)` → "pending execution topology mismatch";
   `create_node_execution(..., seq=1, attempt=2, status="pending")`; `(..., seq=1,
   reuse_pending=True)` → "duplicate pending execution anchors".
P8 `test_gate_persistence_guards(migrated_db, project)`: `run = champion_run(...)`; `execution =
   create_node_execution(conn, run_id=run.id, node_name="alpha", seq=1)`; `create_gate(conn,
   run_id=run.id, execution_id="ne_missing", question={"phase": "a"})` → "gate execution
   ownership mismatch"; `first = create_gate(..., execution_id=execution.id, question={"phase":
   "a"})`; `second = create_gate(..., question={"phase": "b"})`; `second.id != first.id`;
   `create_gate(..., question={"phase": "a"}) == first`; `create_gate(..., question={"phase":
   "c", "value": float("nan")})` → "cannot persist gate"; `transition_gate(conn, first,
   status="answered", answer_json="{}")` succeeds; repeating it with the stale `first` → "stale or
   already applied"; `transition_gate(conn, second, status="pending", answer_json=None,
   question={"phase": "b", "value": float("nan")})` → "cannot transition gate"; tamper `UPDATE
   gates SET ts = X'FF' WHERE id = ?` (`second.id`) → `get_gate(conn, second.id)` "cannot read
   gate" and `list_gates(conn, run.id)` "cannot read gates".
P9 `test_run_mode_ancestry_and_memory_read_guards(migrated_db, project)`: `run =
   champion_run(...)`; `update_run_mode(conn, "r_missing", "guided")` → "mode transition run does
   not exist"; `remember_run_outcome(conn, project_id=project.id, run_id=run.id, value={"score":
   1})`; tamper `UPDATE memory SET ts = X'FF'` → `list_memory(conn, project.id)` "could not read
   project memory"; tamper `UPDATE runs SET started_at = X'FF' WHERE id = ?` → `run_ancestry(conn,
   run.id)` "cannot read run ancestry"; last, create the trigger `CREATE TRIGGER reject_mode BEFORE
   UPDATE OF mode ON runs BEGIN SELECT RAISE(ABORT, 'blocked'); END` and use a second run created
   before the `started_at` tamper: `update_run_mode(conn, other.id, "guided")` → "cannot
   transition run mode".
P10 `test_b11_champion_resolution_rejects_corruption` parametrize `(case, message)` after the
   setup of `test_b11_champion_pointer_atomic_idempotent_and_replacement_rejected` up to the
   first `save_champion_model`; then `get_champion_model(conn, run.id)`:
   - `ownership` / "invalid champion model ownership": `UPDATE models SET candidate_id = NULL
     WHERE run_id = ?`.
   - `spec` / "cannot resolve champion": `UPDATE candidates SET spec_json = '{}' WHERE id = ?`
     (`candidate_id`).
   - `ancestry` / "model fork ancestry crosses projects or is missing": `other =
     champion_run(conn, project)`; `conn.execute("PRAGMA foreign_keys = OFF")` (outside a
     transaction); `UPDATE artifacts SET run_id = ? WHERE id = ?` (`other.id, str(ref)`);
     `UPDATE runs SET forked_from_run_id = 'r_missing' WHERE id = ?` (`run.id`);
     `conn.execute("PRAGMA foreign_keys = ON")`.
   Also, once (unparametrized, in P11): `get_champion_model(conn, "r_missing")` → "run does not
   exist".
P11 `test_b11_non_finite_champion_and_outcome_are_refused(migrated_db, project, tmp_path)`: run
   + `fitted_champion_artifact`; `save_champion_model(..., parameters={"alpha": float("nan")})` →
   "cannot preserve champion model"; `get_champion_model(conn, run.id) is None`;
   `remember_run_outcome(conn, project_id=project.id, run_id=run.id, value={"score":
   float("nan")})` → "cannot preserve run outcome memory"; plus the `r_missing` assertion above.
P12 extend `test_session_message_round_trip_limits_ownership_and_strict_metadata`: after the NaN
   corruption assertion, tamper `meta_json = '[]'` for the same message → `pytest.raises(
   PersistenceError, match="invalid metadata JSON")`: `get_message(...).metadata()`.
Missing imports (`create_gate`, `transition_gate`, `get_gate`, `list_gates`, `update_run_mode`,
`list_memory`, `run_ancestry`, `transaction`, `validate_checkpoint_ownership`) are added as needed.
If any tamper statement itself fails (constraint/trigger) or a different message appears, stop and
report the case.
Checks: Ruff/format/mypy on the six files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_mcp_client.py tests/test_storage.py tests/test_storage_fork.py
tests/test_repository.py tests/test_candidate_fanout.py tests/test_nodes_champion.py'` ->
`/tmp/offline-s14.log`.

### Stage S15 — setup tool path escapes and row warning (independent)

Owned: `tests/test_tools_setup.py`. Gaps: intake 329, 335, 381 (all reachable).
I1 `test_setup_rejects_project_and_datasets_directory_escape` parametrize `(case, message)`:
   `settings = tool_settings` (assert `ToolSettings`); `paths = ProjectPaths(settings.data_dir,
   "demo")`; `outside = tmp_path / f"outside-{case}"`; `outside.mkdir()`.
   `root` / "project root resolves outside the data directory": `paths.root.parent.mkdir(
   parents=True, exist_ok=True)`; `paths.root.symlink_to(outside, target_is_directory=True)`.
   `datasets` / "datasets directory resolves outside the project": `paths.root.mkdir(
   parents=True)`; `paths.datasets.symlink_to(outside, target_is_directory=True)`.
   `pytest.raises(ToolParamError, match=message)`: `setup_dataset("demo", "a" * 64,
   options=DatasetOptions())`.
I2 `test_setup_warns_above_row_threshold(tool_settings, monkeypatch)`: as the profile test with
   content `b"a,b\n1,2\n3,4\n"`; `import statlab_tools.tools.intake as intake_module`;
   `monkeypatch.setattr(intake_module, "ROW_WARNING_THRESHOLD", 1)`; result `warnings == ("2 rows
   exceeds 1; fitting will be slow (D15)",)`.
Checks: Ruff/format/mypy on the file; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS=tests/test_tools_setup.py` -> `/tmp/offline-s15.log`.

#### S12 receipt and correction S12a

S12: runs `3085fbc5…7b5d`, stream `98c8f28c…a4`, test_api_runs `0ff7838b…9e7f`, test_api_stream
`9311b390…1489`; checks exit 0 (`/tmp/offline-s12.log`). Deviations accepted: K2
`contextlib.nullcontext()` (B022), K4 `cast("asyncio.Task[RunState]", ...)` (typing only).
Main coverage (API suites): projects 100%; runs only `377->346`; stream only 97; main 164-165 (S13).
- stream 97: every route first enters the app dependency `registry().request()`, which already
  raises 503 "application is closing"; K7 proves that path. The handler's own check only fires
  in a shutdown race after admission, where `stream_events` returns at once because `closing` is
  set. Redundant.
- runs `377->346`: `except Exception` with `first_failure` already set needs two failing pauses.
S12a (owned: `backend/statlab/api/stream.py`, `backend/statlab/api/runs.py`; no test edits):
1. stream.py `events_endpoint`: delete `if owner.closing: raise HTTPException(503, "application is
   closing")`; keep `owner = registry()` (still used by `owner.run`). Drop `HTTPException` import
   only if unused.
2. runs.py `shutdown`, the `except Exception as exc:` handler: replace the two-line `if
   first_failure is None: first_failure = exc` with `first_failure = first_failure or exc`.
Checks: Ruff/format/mypy on the two files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_api_runs.py tests/test_api_stream.py tests/test_main.py'` ->
`/tmp/offline-s12a.log`.

S12 accepted (after S12a): runs `4d8c5157…8de8`, stream `0ffba4a3…9a79`, tests unchanged from
S12; checks exit 0 (`/tmp/offline-s12a.log`). Main coverage: api/projects, api/runs, api/stream
100%.

### Stage S16 — diagnosable crashes for the paid online run (after S15)

Finding (main, read-only): two failure paths discard their cause, so a paid online run that hits
a code defect would show only `Node execution failed` (engine) or `execution_failed: true`
(API snapshot), with nothing in any log, `events.jsonl` or SQLite to locate the defect:
- `RunLoop` executor `except Exception` branches (engine.py, both the sequential path and
  `_boundary`): store `self.last_error` in memory only.
- `RunRegistry._completed` (api/runs.py): reads `task.exception()` and drops it.
Decision: log these at ERROR through module loggers with the exception TYPE NAME and traceback
FRAMES only. Never log the exception message or its cause chain: messages can carry secrets or
data values (existing tests assert `"secret credential"`/`"private diagnostic"` stay hidden).
Durable payloads (`Failure`, events, checkpoint) stay unchanged. Uvicorn leaves `statlab.*`
loggers unconfigured, so records reach stderr through Python's last-resort handler (WARNING+).

Owned: `backend/statlab/errors.py`, `backend/statlab/graph/engine.py`,
`backend/statlab/api/runs.py`, `tests/test_graph_engine.py`, `tests/test_candidate_fanout.py`,
`tests/test_api_runs.py` (only the three named tests).
Source:
1. errors.py: add `import traceback` and a public function `traceback_frames(exc: BaseException)
   -> str` returning `"".join(traceback.format_tb(exc.__traceback__))` with docstring "Source
   frames only; never the exception message, which may carry secrets or data values."
2. engine.py: `import logging`; `_LOGGER = logging.getLogger(__name__)` after the imports; import
   `traceback_frames` from `statlab.errors`. In both `except Exception as exc:` executor branches,
   directly after `self.last_error = ...`, call `_LOGGER.error("Node %s executor failed with
   %s\n%s", spec.name, type(exc).__name__, traceback_frames(exc))`.
3. runs.py: `import logging`; `_LOGGER = logging.getLogger(__name__)`; import
   `traceback_frames`. `_completed` body becomes: `if task.cancelled():` → `failure = True`;
   `_LOGGER.warning("Run %s execution task was cancelled", run_id)`; `else:` → `exc =
   task.exception()`; `failure = exc is not None`; `if exc is not None:`
   `_LOGGER.error("Run %s execution failed with %s\n%s", run_id, type(exc).__name__,
   traceback_frames(exc))`. The remaining lines (task removal, `failed_tasks` add/discard) are
   unchanged.
Tests (add a `caplog: pytest.LogCaptureFixture` parameter to each):
T1 `test_exception_conversion_preserves_classes_and_hides_secrets` (test_graph_engine.py): for
   `kind == "bug"` assert `"Node alpha executor failed with ValueError" in caplog.text` and
   `"in execute" in caplog.text`; for other kinds assert `"executor failed" not in caplog.text`;
   always `"secret credential" not in caplog.text`.
T2 `test_fanout_executor_failure_boundary` (test_candidate_fanout.py): after the existing
   asserts on the non-skip path, `("executor failed with ValueError" in caplog.text) == (failure
   == "unexpected")` and `"private diagnostic" not in caplog.text`.
T3 `test_failed_start_is_recorded_and_shutdown_closes_resources` (test_api_runs.py): after the
   `execution_failed is True` assertion: `f"Run {run_id} execution failed with PersistenceError"
   in caplog.text`, `"in fail_start" in caplog.text`, `"fixture start failed" not in
   caplog.text`.
T4 `test_registry_launch_guards_and_cancelled_task_accounting`: after its `execution_failed is
   True` assertion: `f"Run {run_id} execution task was cancelled" in caplog.text`.
Checks: Ruff/format/mypy on the six files; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS='tests/test_graph_engine.py tests/test_candidate_fanout.py tests/test_api_runs.py
tests/test_errors.py'` (drop `tests/test_errors.py` from the selector if that file does not
exist) -> `/tmp/offline-s16.log`.

#### S13 receipt and correction S13a

S13: client `2f1af2e0…ed6f`, test_llm_stream `9c04d7dc…8cb3`, test_api_runs `e317502f…167a`,
test_control_lifecycle `aed4b0c6…730f`; checks exit 0 (`/tmp/offline-s13.log`). Deviations
accepted (L4 created as a new test, typed local defs). Main coverage: client.py 100%; main
`ConfigError` handler covered; engine 234 still missed — E12 premise wrong: with every candidate
failed the run itself ends `failed`, and `ensure_audit_anchor` returns `executions[-1]` for
terminal runs before the candidate logic.
S13a (owned: `tests/test_control_lifecycle.py`, E12 only): after `await service.start()`, bind
it to `state`, assert `state.status == "failed"`, then
`loop.checkpointer.save(RunState.model_validate({**state.model_dump(), "status": "paused"}))`
(same construction as E2) before `audit_anchor()`; assertions unchanged. If `RunState` rejects
the paused state, stop and report the message.
Checks: Ruff/format/mypy on the file; `LOKY_MAX_CPU_COUNT=1 make test-fast
TESTS=tests/test_control_lifecycle.py` -> `/tmp/offline-s13a.log`.

S13b correction (main coverage after S13a: engine 234 still missed). Diagnosis corrected: line
234 is the VIABLE path (`index = end`): run non-terminal, cursor still at region start, every
branch terminal and one viable. The engine persists exactly that state when a pause is requested
while the candidate planner proposes the next wave (`control.status is not None` →
`_finish_fanout()` before `cursor` advances). S13b replaces E12's body (keep its name; rename is
not required) in `tests/test_control_lifecycle.py`:
`nodes = (node("fit", candidate=True),)`; `control = RunControl()`; `loop =
fanout_loop(engine_world, nodes, complete, traversal=TraversalPlan(), control=control)`;
`async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]`: `del state, wave`;
`control.request_pause()`; `return specs(1, offset=1)`; `loop.candidate_planner = planner`;
`service = RunLifecycle(loop, fanout_initial(engine_world, nodes, count=1))`; `state = await
service.start()`; assert `state.status == "paused"`, `state.cursor == 0`, `[b.status for b in
state.branches] == ["completed"]`; `anchor = await service.audit_anchor()`; assert
`anchor.node_name == "fit"` and `anchor.status == "completed"`. Import `specs` from
`tests.test_candidate_fanout` and `CandidateSpec` from `statlab_contracts.candidate`; drop
now-unused imports. Remove the S13a re-save. If `start()` does not return paused with cursor 0,
stop and report the returned status/cursor.
Checks: as S13a -> `/tmp/offline-s13b.log`.

S13c correction (S13b stop: `EngineError: candidate planner requires design node`). Use the
accepted E10 shape: `nodes = (node("design"), node("fit", candidate=True))`; `loop =
fanout_loop(engine_world, nodes, complete, traversal=TraversalPlan(design_node="design"),
control=control)`; `service = RunLifecycle(loop, fanout_initial(engine_world, nodes,
count=0))`. Planner: `if wave == 1: return specs(1)`; otherwise `control.request_pause()` and
`return specs(1, offset=1)` (drop `del wave`; keep `del state`). Assertions: `state.status ==
"paused"`, `state.cursor == 1`, `[b.status for b in state.branches] == ["completed"]`; anchor
`node_name == "fit"`, `status == "completed"`. If start() returns another status/cursor, stop and
report them. Checks as S13a -> `/tmp/offline-s13c.log`.

S13 accepted (after S13c): client `2f1af2e0…ed6f`, test_llm_stream `9c04d7dc…8cb3`,
test_api_runs `e317502f…167a`, test_control_lifecycle `a1a4f4db…be64`; checks exit 0
(`/tmp/offline-s13c.log`). Main coverage: client.py 100%, main ConfigError handler covered,
engine 234 covered (E12 now proves the real pause-during-planning boundary).

#### S14 receipt and correction S14a

S14: mcp_client `8681db2c…3bf9`, repository `119fec3f…7564`, test_mcp_client `2fe3fffc…4083`,
test_storage `befad354…ea04`, test_repository `b53f6ecc…5e66`, test_candidate_fanout
`542d7dca…3cef`; checks exit 0 (`/tmp/offline-s14.log`); P2 proved NaN survives
`model_dump(mode="json")`, so the setup strict-JSON guard is reachable and kept. Main full-suite
diagnostic (`$CLAUDE_JOB_DIR/tmp/full2.json`, 0 failures): only gaps left are repository
`get_model` except (reachable: corrupted `models` row) and intake (S15).
S14a (owned: `tests/test_repository.py`): add case `("model_row", "cannot read model")` to
`test_b11_champion_resolution_rejects_corruption`, tamper `UPDATE models SET created_at = X'FF'
WHERE run_id = ?` (`run.id`); same `get_champion_model(conn, run.id)` call (the error propagates
unwrapped from `get_model`). Checks: Ruff/format/mypy on the file; `LOKY_MAX_CPU_COUNT=1 make
test-fast TESTS=tests/test_repository.py` -> `/tmp/offline-s14a.log`.

S14 accepted (after S14a): test_repository `77a7097f…bc05`, other S14 hashes unchanged; checks
exit 0 (`/tmp/offline-s14a.log`); `get_model` except covered.

S15 accepted: test_tools_setup `3c0bc88c…a8c`; checks exit 0 (`/tmp/offline-s15.log`); intake
329, 335, 381 covered.

#### S16 stop and correction S16a

S16 (`/tmp/offline-s16.log`): `traceback.format_tb` prints each frame's source line, so a
message literal in the raising line leaks (`raise ValueError("private diagnostic")`). Decision:
location only. `traceback_frames(exc)` returns one line per frame from
`traceback.extract_tb(exc.__traceback__)`, each formatted as `"{filename}:{lineno} in {name}"`,
joined with `"\n"`. Docstring: "Frame locations only; never source lines or the exception
message, which may carry secrets or data values." Callers and tests unchanged (`"in execute"`,
`"in fail_start"` still match). Add to `tests/test_errors.py` (newly owned) one test
`test_traceback_frames_lists_locations_without_message_or_source`: raise and catch
`ValueError("hidden message")` inside a local `def explode() -> None`; `frames =
traceback_frames(exc)`; assert `"in explode" in frames`, `"test_errors.py:" in frames`,
`"hidden message" not in frames`.
Checks: as S16 plus `tests/test_errors.py` in Ruff/format/mypy; same pytest selector ->
`/tmp/offline-s16a.log`.

S16 accepted (after S16a): errors `5ecaa4f0…2f92`, engine `e5024ded…3c8e`, runs `6e0468ae…5833`,
test_graph_engine `2b6868c2…9c86`, test_candidate_fanout `93325b9c…ce31`, test_api_runs
`5480c52c…6318`, test_errors `86e418af…bd61`; checks exit 0 (`/tmp/offline-s16a.log`).
Docstring wrapped for E501 (wording unchanged), accepted.
