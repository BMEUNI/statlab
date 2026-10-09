---
status: completed
dependencies: [67]
completion_date: 2026-10-09
implementation_notes: "Three-part fix accepted; make check 100% line/branch; copied real-data API/MCP boot passed for all 11 runs; original data and protected ports untouched"
---

# Chunk 68 — gate schema compatibility and startup isolation

## Goal, prerequisites and scope

Fix `make dev` startup failure `gate decision schema changed` without changing the user's
database or occupied services. [HANDOFF](./HANDOFF.md) is verified evidence: completed run
`r_3decbe507e119ab4` retains four prepared InterpretationDecision gates whose schema predates
chunk 67's skip_reason description. Candidate base: `161b1d7` (chunk 67 accepted, make check
100%). Preserve the dirty HANDOFF. Reuse accepted chunk 67 evidence; no startup baseline.

Apply the HANDOFF's three changes: ignore string description metadata during schema comparison;
check current schemas only for pending/answered gates during reconciliation; isolate EngineError
from an individual run's gate reconciliation during boot, warning and continuing the scan.
Non-goals: schema migrations, editing stored schemas/answers, changing gate approval semantics,
accepting structural drift, changing model prompts, UI, dependencies, or dev launcher behavior.
No requests, binds, restarts or signals to ports 8765, 8000 or 5173. No paid LLM calls.

Authority: PLAN D10 (durable gates), D17 (caller-owned execution tasks), D27 (generic engine),
D32 (retain test exposure), D36 (lifecycle), §5.1 (boot recovery). This user-directed exception
isolates gate EngineError during the boot scan; it does not weaken checkpoint corruption handling.
Pydantic JsonValue already validates gate schema JSON; BaseModel.model_json_schema supplies JSON
dict/list/scalars. No new SDK behavior or dependencies. ENVIRONMENT's JsonValue and typed-init
guarantees apply. Existing ownership/node/type guards remain authoritative.

## Ownership and integration seams

One pinned implementer owns exactly:

- `backend/statlab/graph/gates.py`: add `_without_descriptions`, change `_validate` comparison
  and `GateService.reconcile` current_schema argument.
- `backend/statlab/graph/reconciliation.py`: logging import/logger and the narrow catch in
  `RestartReconciler.reconcile_projects`.
- `tests/test_control_gates.py`: helper compatibility regression, description-only gate
  reconciliation/answer regression, terminal gate parametrization, structural-drift regression.
- `tests/test_restart_reconciliation.py`: mixed-run EngineError and cancellation regressions.
- `/private/tmp/statlab-chunk68-boot.py`: temporary real-data boot validator, authored now but
  run by main only after make check. No tracked script or new product API.

Not alone in the codebase; preserve others' edits. No plans, policy, commits, dependency or
configuration edits by implementer. Main writes the acceptance receipt/status only.

Read seams: `_validate` callers are check_edits (~128), override (~176), capture (~274/~283),
answer (~310), _prepare (~387), reconcile (~531). All except reconcile keep the default current
schema check because they may use a proposal for continuation. Existing `_schema` (~73) enforces
declared BaseModel MRO. GateQuestion (~31), GateRow.question, transition_gate (~775 repository)
and GateAnswer remain unchanged. Prepared pre-action overrides still validate when used.
RunRegistry.boot (`api/runs.py` ~391–420) calls reconcile_projects then adopts returned tasks.
RunLoop.load_state (`engine.py` ~160–178) remains unchanged, including its mode-repair seam.
The new catch wraps only the explicit gate_reconciler await in reconcile_projects, not resolver,
load_state, ownership checks, boundary repair, capacity checks or task creation.

## Ordered implementation recipe

### 1. gates.py

Add private module helper before GateQuestion:
`_without_descriptions(value: JsonValue) -> JsonValue`.
For a dict, return a new dict in existing order: omit an entry only when its key equals
`description` and its value is str; recursively transform every retained value. For a list,
return a new list of recursively transformed items. Return every scalar unchanged (including
None, bool, int, float and str). Never mutate input. Do not strip titles, defaults, required,
types, bounds, enum values or non-string description values. No generalized normalization.

In `_validate`, retain current_schema short-circuit and every existing ownership/node/type
check. Compare `_without_descriptions(question.decision_schema)` with the same helper applied
to `self._schema(question).model_json_schema()`. The error text stays exactly
`gate decision schema changed`. Stored payloads remain verbatim.

In `reconcile`, pass current_schema true exactly for statuses pending and answered. Prepared
and applied still pass all other existing validation and replay the same audit events, answers
and external actions. Do not call _schema solely for their reconciliation. Do not change
override, capture, answer or _prepare's current_schema behavior.

### 2. reconciliation.py

Add stdlib logging import and `_LOGGER = logging.getLogger(__name__)` near module imports.
Within the existing `if loop.gate_reconciler is not None`, wrap only its awaited call in a
try/except EngineError. On success, keep refreshing row with get_run as today. On EngineError,
log one WARNING with format `Run %s gate reconciliation failed: %s`, arguments row.id and
exc.message (do not log cause/traceback/proposal). Immediately continue the inner run loop.
The run is already in seen; do not retry it in this scan. Do not append it to runnable or
dormant, repair boundaries, interrupt it, save a checkpoint or change run status after failure.
Earlier gate reconciliation side effects remain durable; do not invent rollback semantics.
Other runs/projects and newly discovered children continue preparation before any tasks start.
Catch no other exception: PersistenceError/corruption and cancellation retain existing behavior.
No task, lock or transaction boundary changes.

### 3. tests/test_control_gates.py

Reuse shared_gate (~422), engine_world, transition_gate, get_gate, Proposal and GateAnswer.
Import the new private helper alongside existing gate imports; add stdlib deepcopy for input
preservation assertion.

Add `test_schema_comparison_ignores_only_string_descriptions`: typed JsonValue input containing
top-level description `old prose`, title `Keep`, a properties mapping with count type integer,
exclusiveMinimum 0 and description `Count prose`; a property literally named description whose
value is a dict with type string and description `Field prose`; anyOf list containing a dict
with description `Branch prose`, type null, plus scalars None, True, 2, 1.5, `keep`; a nested
dict with description 7; required list `[count]`, default 1, enum `[1, 2]`. Expected value is
identical except the four string-valued description entries are absent. Assert exact expected
equality, input equals deepcopy taken before call, and normalizing expected gives the same
result. This proves recursion, scalars, preserved constraints/non-string values and no mutation.

Add `test_pending_gate_accepts_description_changes`: shared_gate opens one pending gate.
Use Proposal.model_json_schema; replace properties.count with a copy containing description
`Updated count instructions`, and add top-level description `Updated proposal instructions`.
Retain all other properties via existing dicts; use isinstance assertions for JsonValue dict
narrowing. transition_gate to pending with this schema, preserving original question and no
answer. Reconcile lifecycle.state; no exception, returned status awaiting_user, gate still
pending, stored decision_schema exactly the modified schema. Call service.check_edits with
count 2; then answer approve_with_edits with count 2; assert stored gate prepared and proposal
count 2. This exercises _validate through both boot and actual answer and retains original
schema metadata. Do not run continuation or alter unrelated fixtures.

Rename existing `test_reconcile_skips_schema_check_for_applied_gates` (~823) to
`test_reconcile_skips_schema_check_for_terminal_gates`; parametrize status with prepared/applied
and annotate status Literal accordingly. Reuse existing flow: transition to selected status,
approve answer JSON, question schema {}; reconcile no exception and assert gate.closed event.
Also assert gate status remains selected status. Remove its trailing pending mutation section.

Add `test_reconcile_rejects_structural_schema_changes`, parametrized status pending/answered
with typed Literal. shared_gate; transition to selected status with schema {}; answer_json None
for pending or an approve GateAnswer JSON for answered. Reconcile raises EngineError with
exact existing schema-changed text; status and question schema remain selected status/{}.
The existing answer schema-rejection tests continue unchanged.

### 4. tests/test_restart_reconciliation.py

Add logging import. Reuse the setup of
`test_reconcile_projects_corrupt_second_run_starts_no_first_run` (~845): original world and a
second world in another project using create_project/new_world/shared data_dir, nodes (node(),),
saved initial checkpoints, one shared tracker, lifecycles/RecoveryBindings keyed by run ID,
resolver returning keyed binding. Do not corrupt a checkpoint.

Add `test_gate_engine_error_skips_one_run_and_prepares_other` with engine_world, project,
minimal_contract, tmp_path, caplog fixtures. Bind a gate_reconciler on original loop with
signature `(state: RunState) -> RunState` that records state.run_id and raises
EngineError(`gate decision schema changed`). Both executors record entered run IDs, assert
the failed original has already been visited before execution, and call existing complete.
Under caplog.at_level WARNING for statlab.graph.reconciliation, reconcile both project IDs.
Assert one task named statlab-run:<second ID>, no dormant states; await task and assert completed;
entered exactly second ID; gate callback called once for original even after rescan; original
DB row stays pending with no node executions; original checkpoint load stays equal to saved
   initial state (load with spine_id=initial.spine_id and spine=initial.spine). Assert exactly
   one matching logger WARNING with exact format rendered using
original ID and error message. Reuse typed Checkpointer.load arguments from existing tests.

Add `test_gate_reconciliation_cancellation_propagates`: one engine_world initial saved,
shared tracker/binding, callback raises a pre-created asyncio.CancelledError(`preserved`).
Capture asyncio.all_tasks before reconcile; assert same exception object is raised, task set
unchanged, no node execution and original DB status pending. Do not add broad exception catches.
Existing corrupt-second-run regression must still pass, proving only EngineError is isolated.

### 5. Temporary scratch boot validator

Author `/private/tmp/statlab-chunk68-boot.py`; do not execute before main's full acceptance.
No command-line options required. Run from repository root with its .venv interpreter. Main
will wrap it with a 300-second subprocess timeout. Use stdlib shutil/sqlite3/tempfile/hashlib,
asyncio/logging/os/pathlib/secrets and installed httpx. Reuse `integration.services` McpProcess,
ApiServer, build_settings, free_port; use LlmClient with injected completion callback.

Recipe:
1. Resolve cwd, statlab.db and projects. Require source DB/projects exist; require no source
   WAL (immutable backup must not omit writes). Create a unique mkdtemp prefix
   `statlab-chunk68-boot-` under /private/tmp. Hash source DB bytes and every source state.json
   and events.jsonl file under projects before copying, retaining path/hash mapping.
2. Open source as SQLite URI mode=ro&immutable=1; back up into scratch/statlab.db, close both.
   shutil.copytree projects into scratch/projects; no hardlinks to writable source files.
3. Open only scratch DB for writes. For each of datasets, contracts, artifacts, read id/path;
   require each path is under resolved source projects with Path.relative_to (fail rather than
   silently leave a source path), then replace path with scratch projects / relative path using
   parameterized UPDATE per row. Commit/close. Capture run IDs/statuses and original gate rows
   from scratch. Select id/status/question_json/answer_json from gates for run ID
   r_3decbe507e119ab4 and status prepared, then retain rows whose decoded
   question.decision_schema.title equals InterpretationDecision; require exactly four.
   Require that run exists.
   Do not alter any gate/schema/run status as setup.
4. Use logging.basicConfig INFO with scratch/boot.log file and stdout stream. Set
   DEEPSEEK_API_KEY in process environment to `chunk68-offline-placeholder` before app boot;
   do not print environment. Inject async `blocked_completion(**kwargs: object) -> object`
   into LlmClient; increment a local attempt counter then raise RuntimeError
   `LLM calls disabled in scratch boot`. This callback prevents all provider requests even if
   unexpected runnable work appears. API/mcp configuration uses fresh local token (never log it).
5. McpProcess uses scratch projects, random token, scratch/mcp.log and OS ephemeral port via
   its existing free_port. OS ephemeral range excludes protected ports. API free_port must
   assert not in {8765, 8000, 5173}; build_settings points only to scratch DB/projects and
   returned MCP URL. Start McpProcess, then ApiServer with injected LlmClient. Retain both handles
   before starting them; in nested finally stop API if created, then MCP, including failure.
6. Using httpx.AsyncClient(base_url=API URL, timeout=10), GET /api/projects and require 200.
   For every captured run ID GET /api/runs/<ID>; require 200, matching run ID according to
   response JSON state.run_id and run.id, and view_only false. Require failing completed run's state.status is
   completed. GET /api/runs/r_3decbe507e119ab4/executions and /artifacts, require 200.
   No write endpoints, SSE streaming, UI, rewind or model calls. Assert attempt counter 0.
7. Stop scratch servers. Open scratch DB read-only; assert the four stored prepared gates
   retain IDs/status/question_json/answer_json, and run statuses equal captured statuses.
   Hash source DB/state/events files again; require exact equality with original hashes and
   path set. Require no warning `gate reconciliation failed` in boot log for this description
   drift. Print PASS, scratch directory, server URLs, run count/snapshot statuses, blocked-call
   count and source-hash preservation; do not print gate payloads or secrets. Any failed assert
   or unexpected exception yields nonzero; do not recover or weaken assertions. Leave scratch
   copy/logs for review. No production data deletion and no signals to unrelated processes.

## Focused checks and handoff

Implementer runs exactly these, with full outputs in unique /private/tmp/chunk68-* logs:

- `make test-fast TESTS="tests/test_control_gates.py tests/test_restart_reconciliation.py tests/test_api_runs.py tests/test_graph_engine.py"`
- `.venv/bin/python -m ruff check backend/statlab/graph/gates.py backend/statlab/graph/reconciliation.py tests/test_control_gates.py tests/test_restart_reconciliation.py /private/tmp/statlab-chunk68-boot.py`
- `.venv/bin/python -m ruff format --check backend/statlab/graph/gates.py backend/statlab/graph/reconciliation.py tests/test_control_gates.py tests/test_restart_reconciliation.py /private/tmp/statlab-chunk68-boot.py`
- `.venv/bin/python -m mypy backend/statlab/graph/gates.py backend/statlab/graph/reconciliation.py tests/test_control_gates.py tests/test_restart_reconciliation.py`
- `.venv/bin/python -m py_compile /private/tmp/statlab-chunk68-boot.py`

Mechanical formatting of owned files allowed. No full suite, global coverage, boot script execution,
mutation or standalone replay in implementer checks. These direct regressions sufficiently prove
the three changed guards; no extra mutation quota. Stop on unexpected failure or missing decision;
report command, exit, relevant output and partial diff. Main resolves corrections in addendum first.

Receipt: changed files, base commit plus exact tracked-file hashes, commands/scopes, genuine exits,
test/static summaries, logs, deviations, temporary validator path and next-stage hazards.

## Acceptance and stop conditions

Main reviews complete diff and focused receipt, then runs one stable `make check` under AGENTS
validation policy (unique full log, genuine exit, pytest summary, coverage TOTAL, mypy and Ruff).
After success, main runs authored scratch boot validator under 300-second timeout and reads its
log/result. Full acceptance requires both passing, 100% line/branch coverage, no structural drift
acceptance, cancelled work preserved, all 11 current user runs accessible on copied storage,
four formerly failing prepared gates preserved, source hashes unchanged, no provider calls and
scratch servers stopped. A source/input change invalidates only affected evidence.

Then update this frontmatter/receipt, index row and single Next heading. Graphify update after
source acceptance. Commit exact chunk files only under existing local-commit authorization;
never stage dirty HANDOFF, push, or launch make dev on protected ports. Final tells user to rerun
make dev and states boot-check scope. No dependent feature work.

## Review correction 1 — focused-check failures

Initial candidate product changes follow the resolved recipe. Focused pytest exited 2 at make
level: the synchronous helper test retained an asyncio marker, the mixed-run test compared
tuple results to lists, and the cancellation test used a nonexistent RunPaths.project_id.
Scoped Ruff exited 1 (unused test assignment and temporary script lint); mypy exited 1
(the same tuple/project mistakes plus an unnarrowed JsonValue). Format and compilation exited 0.
Logs: /private/tmp/chunk68-focused-tests.log, chunk68-ruff-check.log, chunk68-mypy.log,
chunk68-ruff-format.log, chunk68-pycompile.log. No validator execution occurred.

Same implementer, same ownership. Do not change product behavior. Apply these exact corrections:

1. In test_control_gates, remove only the asyncio decorator on the synchronous normalization
   test. In the pending-description test, bind the prepared question's proposal to a local,
   assert it is a dict, then assert its count is 2. In the terminal-status test, retain the
   transition_gate call but remove its unused applied assignment.
2. In test_restart_reconciliation, compare both empty list_node_executions results with the
   empty tuple. Add the existing project: ProjectRow fixture to the cancellation test and
   reconcile project.id. Assert the mixed-run matching log record's levelno is logging.WARNING.
3. Temporary validator: remove sys.path insertion; put both application imports in the normal
   sorted top-level import block, before ROOT. Main supplies the repository root in PYTHONPATH
   when executing this external script. Keep sys for stdout logging.
4. Import contextlib.closing. Wrap every sqlite3 connection with closing so each is actually
   closed; the connection context manager alone commits but does not close. Use one multi-context
   with statement for backup source/destination. Keep explicit scratch path-update commit.
5. Replace table-name SQL interpolation with three fixed SELECT/UPDATE string pairs, one each
   for datasets, contracts and artifacts. SELECT id/path from the literal table; UPDATE its
   path by parameterized path/id values. Loop over those pairs, preserving existing path
   confinement checks. Add ORDER BY id to both prepared-gate SELECTs for deterministic comparison.
6. Replace every validator assert with an explicit failed-condition branch raising RuntimeError.
   Preserve every condition. Messages identify missing source, source WAL, missing target run,
   incorrect target-gate count, protected API port, projects/run/detail HTTP failure (include
   run ID or suffix and numeric status only), mismatched snapshot run IDs, view-only snapshot,
   target run no longer completed, attempted LLM call, changed gates/statuses/source hashes,
   or unexpected gate-reconciliation warning, respectively. Never include payloads/secrets.
   No suppression, ignored rule, new helper or weakened condition. Existing Path.relative_to
   failures continue propagating. Existing cleanup finally blocks remain unchanged.

Run only the following correction pytest group (unchanged callers' earlier passing checks
remain reusable), then the original scoped Ruff check/format, mypy and validator compilation
commands above; use new correction-1 log paths. Formatting owned files is mechanical only.

`make test-fast TESTS="tests/test_control_gates.py::test_schema_comparison_ignores_only_string_descriptions tests/test_control_gates.py::test_pending_gate_accepts_description_changes tests/test_control_gates.py::test_reconcile_skips_schema_check_for_terminal_gates tests/test_control_gates.py::test_reconcile_rejects_structural_schema_changes tests/test_restart_reconciliation.py::test_gate_engine_error_skips_one_run_and_prepares_other tests/test_restart_reconciliation.py::test_gate_reconciliation_cancellation_propagates"`

Stop on any unexpected failure; report exact exits, outputs, hashes and remaining deviations.
Do not execute boot validator or global gates, edit plans, commit or expand scope.

## Review correction 2 — temporary validator lint only

Correction 1: all eight parametrized regression cases passed (make exit 0); scoped mypy,
format and compilation passed. Ruff found only temporary-script I001 and SIM102, exit 1.
Logs are /private/tmp/chunk68-correction1-{focused-tests,ruff-check,ruff-format,mypy,pycompile}.log.
Product/test inputs are now stable; no test or mypy rerun for this script-only correction.

Same implementer changes only /private/tmp/statlab-chunk68-boot.py. Move the contextlib closing
import after the stdlib plain imports and before pathlib. Third-party block contains httpx
then statlab.llm.client; separate integration.services as the project import block afterwards.
Combine target run ID equality and non-completed snapshot status into one short-circuit AND
condition; raise the same RuntimeError. No behavior or assertion changes.

Run Ruff check, Ruff format --check and py_compile on that single temporary file, with
correction-2 logs. No product tests, mypy, global gates, boot execution or tracked edits.
Stop on any unexpected failure and return terminal receipt. Helper placement before GateService
instead of before GateQuestion is an accepted ordering-only deviation; no relocation needed.

## Acceptance receipt (2026-10-09)

Main reviewed the complete product/test diff and the complete temporary validator against
the resolved recipes. Candidate: base commit `161b1d7999080f7de25c764241b7a0958c752308`
plus the four tracked files below; only this candidate received product acceptance.

| File | SHA256 |
| --- | --- |
| backend/statlab/graph/gates.py | bb07d9cc5955847a4ba4be42d9be9ca2e52de115bd69ac59dd5fc67f5740f34e |
| backend/statlab/graph/reconciliation.py | 191ff30981c68e74df9485b6ad3a8cb4725062f674bed52235cf48f504a0795d |
| tests/test_control_gates.py | 4204785bb4f68ea7bb136d60cab882b272d4e2cf2d4677801b2dc3b53fa24f46 |
| tests/test_restart_reconciliation.py | 4bb96efc4b5ca9733bc83a447d84f6f8f7cad526207bb64f8631f2d0abfcf4ce |

Pinned implementer terminal receipts: initial focused group exposed three test setup failures;
correction 1's exact six selectors passed all eight parametrized cases, exit 0. Scoped mypy
exit 0 on all four files; scoped format exited 0 and Ruff's remaining findings were script-only. Temporary validator's remaining
two style issues were resolved without product/test changes; single-file Ruff, format and
compilation exited 0. Logs/earlier genuine failures are retained at the paths in both review
addenda. No tests were weakened and no failed check was claimed successful.

Main `make check` attempt 1 exited 2: sandbox denied asyncio fixture socket binds, a home
directory fixture write and physical-core discovery; resulting HTTP tests could not cover
their paths. No source change followed. Runtime permission input changed by approved execution
outside the sandbox; main repeated the same gate on the same hashes, attempt 2 exit 0.
Pytest reached [100%] with one designed skip and no failure/error summary (double quiet output
suppresses the numeric pass summary). Coverage TOTAL: 12595 statements, 0 missed, 3620
branches, 0 partial, 100.00%. Mypy: 200 files, no issues. Ruff check clean; 209 files formatted.
Full logs: /private/tmp/chunk68-make-check-20261009-1.log (unaccepted sandbox attempt),
/private/tmp/chunk68-make-check-20261009-2.log (accepted).
Accepted log SHA256: 0526b8b05c3e5719dda2ca9bbc32eabac7f2222883543fe0eb8f806b3c9c803f.

Main then ran /private/tmp/statlab-chunk68-boot.py from the repository root, using its .venv
interpreter, repository PYTHONPATH and a subprocess timeout of 300 seconds, outside the sandbox
for local socket access. Genuine exit 0 and PASS. Validator SHA256:
bd5d1f8abf7bc6d3435584d19afd09719bf89cada90b2a0f2eab42edb3bf56a3.
Scratch: /private/tmp/statlab-chunk68-boot-o05dwbuv. API 127.0.0.1:53771;
MCP 127.0.0.1:53766. Full output: /private/tmp/chunk68-scratch-boot-20261009-1.log
(SHA256 98aa52d866c809c77ea7e470140e7dee9f31bb9f0b0eebd5f7a3c31b20824971);
scratch/boot.log and scratch/mcp.log show successful startup and complete shutdown.

Boot facts: projects endpoint and all 11 run snapshots returned 200 with matching IDs and
view_only false. Formerly failing r_3decbe507e119ab4 remained completed; executions/artifacts
endpoints returned 200. Its four prepared InterpretationDecision gate IDs/statuses/question
JSON/answer JSON stayed byte-identical. Run statuses stayed seven completed, two cancelled,
one superseded and one awaiting_user. No gate-reconciliation warning. Injected provider
callback attempts: 0. Source statlab.db and every source state.json/events.jsonl path/hash
matched before/after. Source DB SHA256 after the proven identical boot:
0d9c0bd7b73b30b118e9a3ee9c457d6f3f4907e1523568f9593c5b2a2084e977.
Source reads were immutable/read-only; only scratch DB paths were remapped. Both scratch
servers stopped; no binds, requests, signals or restarts on 8765/8000/5173.

Proved requirements: description-only compatibility without mutation, structural drift still
rejected for pending/answered gates, terminal schemas retained, EngineError isolates one run
before other tasks launch, cancellation and corruption handling preserved. Existing normal
suite supplies caller regressions. No mutation/standalone replay required by this chunk.
Graphify AST refresh exited 0 after acceptance; its graph is navigation, not validation evidence.
Dirty HANDOFF remains outside this change/commit. No paid/live-provider or browser/UI check;
a truly incompatible gate is warned and skipped during this boot scan, with durable data kept.
Next-stage feature work remains chunk 64 and the separately tracked offline-rich S01 issue.
