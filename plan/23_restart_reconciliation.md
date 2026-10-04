---
status: completed
dependencies: [22]
completion_date: 2026-10-04
implementation_notes: "B7 accepted and committed: restart preserves boundaries/audit/claims, repairs interrupted attempts and dormant gates. Full coverage/static gates exit 0; see receipts/B7.md."
---

# Chunk 23 — Restart reconciliation

## Goal and authority

Recover runs after process death at durable node boundaries, without a fork or lost audit.
PLAN §5.1, D9/D17/D24/D27/D32 and chunk 22's frozen contracts govern. Root AGENTS.md owns
execution/validation policy. This is a callable boot service; FastAPI lifespan wiring belongs
to chunk 33. No candidate fan-out, gate generation/answering, user resume or retry policy here.

## Prerequisites and reuse

Stage 22 terminal handoff: 52 focused cases and three existing repository regressions passed;
scoped mypy (12 files), Ruff and format checks exited 0. Handoff:
`/private/tmp/statlab-b7-stage22-handoff.md`; aggregate candidate
`fe92d47f0082cf95e2cdd81b6b46647411b0ade059ee3f4634722fd92800586b`.
No full acceptance yet. B6 accepted evidence remains valid for unchanged client inputs.

Reuse `RunState`, `BoundaryResult`, `SpineEntry`, `Checkpointer`, `EventBus`, `RunLoop`,
shared `RunTracker`, `list_runs`, `get_run`, node outcome/attempt repositories and existing
SQLite transactions. Initial state/configuration comes from an injected typed binding resolver;
do not parse a new domain config or hardcode nodes. No dependency or SQL migration.

Relevant seams: Checkpointer.load validates run/spine identity and strict JSON; EventBus.replay
is exclusive and append owns durable sequence; RunLoop.run(initial) loads the checkpoint and
uses its cursor, increments attempts and preserves test-score claims. A running final cursor
can finish without dispatch. Awaiting/failed state retains `pending_result`; paused state need
not have a pending result. NodeResult/Pydantic already validate failure/reason/output invariants.
ENVIRONMENT remains Python 3.14.3, Pydantic 2.13.5/init_typed and stdlib SQLite WAL.

## Exact owned files

- New `backend/statlab/graph/reconciliation.py`, `tests/test_restart_reconciliation.py`.
- Extend `backend/statlab/graph/__init__.py` with public recovery API exports.
- Extend `backend/statlab/persistence/models.py`, `repository.py` only for actual recovery
  callers: typed gate reads, node execution listing/interruption and new-run history check.
- Bounded amendments to `backend/statlab/graph/engine.py` and `tests/test_graph_engine.py`:
  genuinely-new missing-state check and refusal to execute a DB-dormant/terminal run through
  an old running checkpoint; reuse recovery seam rather than a parallel implementation.
- `backend/statlab/events.py` / `tests/test_events.py` only if one small replay/repair seam is
  necessary; prefer existing replay/append. `state.py` / `test_graph_state.py` only if recovery
  needs a validated status transition helper; no topology expansion.

No B6 clients, contracts, storage, migrations, dependency/test config, prior tests, plans or
policy changes. Request an ownership amendment for a concrete blocker outside these files.

## Frozen behavior/API

1. An injected resolver accepts a `RunRow` and returns a typed binding containing its RunLoop
   and initial RunState. A per-project boot scan uses existing `list_runs`. Validate binding
   identity/mode/declared topology before side effects. One EventBus per run and one shared
   RunTracker per process. No implicit construction of a second bus or client.
2. `RestartReconciler` prepares eligible runs, then schedules pending/recoverable running runs
   as asyncio tasks. Return a typed batch with dormant states and task handles; the caller
   owns awaiting, cancellation and exception consumption. Do not schedule any task until the
   scan/preparation succeeds, so a preparation error cannot leak an unowned execution task.
   Boot scanning is invoked once before new live runs start, not as a live-run reconciliation
   endpoint. A subsequent process boot may repeat interrupted preparation safely.
3. SQL `completed`, `failed`, `cancelled`, `superseded` rows are untouched: no resolver,
   checkpoint read, event append, node write or dispatch for them. SQL `paused` and
   `awaiting_user` are dormant even if their checkpoint still says running. Load strictly;
   keep cursor/results/artifacts. Paused state may be projected/saved as paused. Awaiting
   requires its matching retained pending result; never invent a question/result or approval.
4. For awaiting runs, read only unanswered `status='pending'` gate rows belonging to this run
   and its pending node execution. Parse question JSON as a JSON object without evaluation;
   malformed JSON/ownership mismatch is a package persistence error. Re-publish each actual
   pending gate as `gate.opened` with gate ID, node execution ID and question. Do not create or
   answer gates. Zero gate rows is allowed for chunk 22's bare awaiting result; chunk 32 owns
   gate persistence. Answered/stale gates are not re-published. Remain dormant.
5. For SQL `running`, load the existing checkpoint; absence/corruption/mismatched identity
   fails closed before dispatch. Mark every genuinely `running` node execution of that run
   failed, with `Failure(error_class='bug', message='process terminated')` and ended_at.
   Preserve IDs, attempts, summaries, tool/LLM audit and artifacts. Emit the corresponding
   node.failed event once per interrupted execution. Do not overwrite already completed,
   failed, skipped or awaiting executions. No pruning for interruption/reconciliation.
6. Checkpoint cursor/results are completed-boundary authority. SQL outcome ahead of checkpoint
   does not advance the cursor: replay the unfinished node with a new attempt. SQL behind
   checkpoint is repaired from its committed/pending results without invoking those nodes.
   Repair missing node terminal events from checkpoint boundary results by execution ID;
   an existing matching terminal event is not duplicated. Persist SQL before appending its
   outcome event. Detect conflicting/missing execution ownership instead of fabricating rows.
   PLAN §11 declares node.completed/node.failed/node.skipped; awaiting_user emits run.status
   and actual gate.opened only. Remove chunk 22's undeclared node.awaiting_user event. Event.type
   is a string, so Pydantic does not enforce this vocabulary for the engine.
   A durable completed/failed/awaiting/paused checkpoint ahead of a running row repairs run
   status and emits run.status; terminal/dormant checkpoint does not dispatch. A running
   checkpoint at final cursor completes via RunLoop without another node invocation.
7. For `pending`, initialize only a genuinely new run: no checkpoint is allowed only when
   there are no prior node execution/candidate/gate/artifact rows or durable events. Existing
   valid initial/running boundary may be recovered; never silently replace execution history.
   This fixes a reachable case: old node audit + reset SQL pending + deleted state.json.
   Pydantic validates state shape, not SQL history. The same check belongs in direct RunLoop
   initialization, so callers cannot bypass it. Existing scored/fingerprinted pending rows
   also fail the missing-state check; neither claim is ever cleared.
8. Direct RunLoop must refuse dormant/terminal SQL status while an old running checkpoint
   exists; this is reachable when a control write precedes a checkpoint write. No exception
   is needed for crash recovery: reconciler only schedules SQL pending/running rows with
   recovered pending/running checkpoints. Cancellation remains CancelledError, not failure.
9. Recovery preserves run ID/fork lineage, retained artifact refs, candidate public/DB identity,
   labels/specs, every existing scoring claim/fingerprint and all prior attempt audit. Replayed
   tools create new artifact IDs through existing B6 behavior. A retained test-score claim may
   prevent replay; fail closed through the existing client. Multi-candidate recovery arrives
   with chunk 24; no simulated fan-out or concurrency proof for full M8a here.
10. Wrap foreign SQLite/file/JSON/Pydantic boundary errors with package errors and safe messages;
    preserve cancellation. Do not hide failed recovery by marking the run completed or resetting
    it to pending. No paid live LLM, retry/backoff, mutable global registry or framework.

## Focused proof

Use `make test-fast TESTS='tests/test_restart_reconciliation.py tests/test_graph_engine.py'`
plus affected graph/state/events selectors when those modules change. Each pytest process uses
the explicit 900-second subprocess timeout, no coverage. Scoped mypy and targeted Ruff/format.
Reuse stage 22's unchanged real MCP evidence; no need to re-drive its tools for status-only edits.

Prove all SQL statuses including superseded; pending normal start; dormant paused/awaiting and
pending gate republish/filter; malformed state/gate and mismatched binding fail closed; terminal
rows untouched even if files are corrupt; cancellation unchanged; no task leak on preparation
failure. Kill an async run after a completed boundary and while its next node is executing:
reopen checkpointer/event bus, reconcile, verify same run ID, completed prefix not re-executed,
old running attempt failed with exact bug/reason, next attempt increments, crashed artifacts
and tool audit preserved and never pruned. Include one-candidate progress recovery.

Inject crash windows: SQL outcome before checkpoint (replay); checkpoint before terminal event
or run status (repair without duplicate dispatch/events); no running node yet (resume normally).
Verify preservation of test_scored_at/test_fingerprint and genuinely-new missing-state refusal.
Use reachable persisted states and normal typed constructors; no validator bypass for coverage.
No mutation quota or extra standalone B6 artifact replay.

## DoD and stop conditions

Stop at terminal focused green: changed files, candidate/diff hashes, exact commands/selectors,
genuine exits, summaries, logs, exported API, remaining review/next-stage hazards. No global
coverage/full suite, commit/push, plan updates, API lifespan or dependent chunks. Main consolidates
B7 review, executes one stable acceptance gate and records durable evidence. Full M8a fan-out
concurrency remains chunk 24's proof obligation.

## Consolidated review addendum

Actual corrections: use PLAN's event vocabulary for awaiting nodes; refuse missing state when
SQL/event history exists; honor SQL dormant/terminal status despite an old running checkpoint.
Binding safety relies on validated identity/topology and the shared tracker; connection-object
identity adds no necessary guarantee and its restriction/test were removed. Pydantic already
validates the awaiting pending result, so the consumer uses a typed cast rather than another
assertion/guard. Final coverage's Protocol-stub exit arc was removed through an equivalent
BindingResolver Callable alias, without changing dispatch behavior or adding artificial tests.
Focused evidence and final acceptance are retained in [B7 receipt](./receipts/B7.md).
