---
status: implemented
dependencies: [22, 30]
completion_date: 2026-10-05
implementation_notes: "B12 reviewed; focused and global static gates exit0. Runtime tests stopped by user; behavioral/coverage acceptance deferred. See receipts/B12.md."
---

# Chunk 31 — bounded recovery and lifecycle

## Goal

Make the existing control router usable for pause/resume/cancel, recover individual failed
foreign calls within the declared error taxonomy, and establish fork-safe audit inputs for32.
No API, UI, provider change, schema migration, or whole-node automatic replay.

## Prerequisites and reuse

Architecture: PLAN §5, §5.1, §6.1.1; D11/D17/D29/D32/D36. Reuse RunLoop, RunControl,
ToolBoundary, RestartReconciler, Checkpointer, EventBus and ControlRouter. B6 already proves
MCP dispatch/schema/lineage/one argument repair and LLM one output repair/timeout/semaphore.
RETRY_BUDGET means maximum attempts: transient3, tool_param2, data/bug0 automatic retries.
An exhausted LLM output repair must not be multiplied by outer recovery.
B11 proves SQL champion selection and irrevocable dispatched test-score claims; ambiguous
test scoring must never retry, including after restart. All domain/spine literals stay in domains.
Touched pins: Python3.14.3, Pydantic2.13.5, mypy2.3.1, Ruff0.16.6; no dependency changes.

## Owned files

- New `backend/statlab/graph/recovery.py`, `backend/statlab/graph/lifecycle.py`.
- `backend/statlab/graph/{engine,candidates,reconciliation}.py`.
- `backend/statlab/persistence/repository.py`, `backend/statlab/storage.py`.
- `backend/statlab/mcp_client.py`, `backend/statlab/domains/{runtime,persistence}.py`.
- `backend/statlab/llm/client.py`, `tests/test_llm_client.py` only for explicit
  request-level recovery injection, retaining the default B6 behavior.
- `tests/test_nodes_foundation.py` only for the shared FakeLlm's optional recovery
  keyword compatibility; existing domain fixtures reuse this override.
- New `tests/test_control_recovery.py`, `tests/test_control_lifecycle.py`.
- `tests/test_mcp_client.py`, `tests/test_storage.py`, `tests/test_restart_reconciliation.py`
  only for affected regression cases (if existing filename differs, report before expanding).
No other files; no worker plan edits or commits. Preserve other edits.

## Frozen behavior/API

1. One generic asynchronous call-recovery helper with injected sleep for offline tests.
   Retry only structured transient failures, with bounded exponential backoff and at most
   three call attempts. Preserve cancellation and typed domain errors. Unknown exceptions
   retain internal cause and expose a safe bug message. No traceback/secret in events.
   LLM recovery must wrap individual `_request` calls through an explicit optional recovery
   callback, not restart `complete`: invalid initial output followed by a transient repair
   request is reachable, and restarting complete would multiply the accepted one-repair loop.
   Default client behavior remains no hidden retry. DomainRuntime supplies recovery explicitly.
   Tool parameter repair remains exclusively the existing MCP repair callback, at most two
   attempts total. Never re-enter an exhausted LLM output repair. Wire retry taxonomy has
   no redundant retryable flag. Use the helper at individual domain LLM and ToolBoundary
   calls, never rerun a node after partially successful tools. A scoring claim is a hard stop:
   no second dispatched score attempt even when the first fails transiently.
   Mixed MCP errors share an invocation-wide cap of three admitted attempts, including
   the existing repair dispatch: transient/transient/tool_param cannot dispatch a fourth
   call. A pure tool_param sequence remains two attempts; a repaired failure never starts
   a fresh outer repair cycle.
2. Caller-owned lifecycle service binds the actual RunLoop/control/checkpoint and returns
   durable state; supply a compatible ControlRouter lifecycle callback. Pause/cancel request
   control, drain admitted tool calls and branch tasks, then persist status/events/checkpoint.
   Cancel dominates pause, preserves attempts, artifacts, lineage and test claims. Resume
   only a paused run, reuses run ID and committed cursor/branch progress, clears only the
   lifecycle request, and invokes the same loop without concurrent duplicate starts.
   Pending/dormant cancellation must work without launching tools. Terminal states reject
   invalid transitions. Task ownership stays with caller; no hidden orphan tasks or preemption.
3. Sequential and fanout engine paths obey identical boundary stops. Stop after a successfully
   returned tool must not leave its execution falsely running; an incomplete node retains its
   audited partial work but does not advance. Resume/restart starts a new auditable node
   attempt at the same committed boundary. Completed sibling results stay retained. Refresh
   per-invocation lifecycle flags on resume. Restart remains a prepared full scan before tasks.
4. Early control audit anchor: persist initial checkpoint first, create/reuse a legitimate pending
   execution of the actual first/current declared node (with candidate ownership where needed),
   and promote/reuse it when that node starts. No phantom control node, no duplicated first
   attempt, no unaudited control LLM call. Existing LLM client already verifies same-run anchor.
   Anchor creation and engine execution must agree under concurrent control/start.
5. Add verified same-project run ancestry helper and ancestry-aware artifact listing. Keep
   globally unique refs and original lineage rows; expose only inherited refs retained by the
   child's checkpoint plus its own artifacts, not abandoned parent outputs. Detect cycles,
   missing/cross-project ancestors, corrupt checkpoint ownership; domain PersistenceError.
6. Extend logged_calls through an explicit fork-aware option used by domain persistence.
   Compose real ancestor producer calls needed by retained checkpoint refs and inputs of
   current-run calls, recursively in original dispatch order, then current-run audit. Validate
   same-project ancestry and artifact producer ownership; no fabricated sent arguments,
   registry rows, inherited post-rewind work, or unrelated failed branches. Preserve producer
   dependency closure even for pruned intermediate producer outputs (export reconstructs them).
   Existing export/snapshot validation stays intact, including exactly one load_csv dataset.
   Plain current-run logged_calls behavior stays compatible. Keep fork-only policy explicit;
   never silently repair malformed or incomplete history.32 will supply retained child state.

## Focused checks

User prohibits runtime tests, collection, coverage, mutations, replay and live LLM calls.
Author offline tests unexecuted: taxonomy/attempt caps/backoff/cancellation/score refusal;
pause during tool and concurrent branches, drain/cancel precedence, same-ID resume;
early anchor reuse and duplicate start; verified ancestry/artifact visibility/audit closure.
Intended future selectors: test_control_recovery.py, test_control_lifecycle.py, affected
test_mcp_client.py/test_storage.py/restart tests. No count quota.
Run scoped mypy and Ruff check/format on owned Python files only; git diff --check.
Do not run global acceptance; main owns one stable batch static acceptance after32.

## DoD / stop conditions

Stop at focused static-green handoff with changed files, base identity/file hashes, actual
commands/exit codes/logs, authored-unexecuted selectors and32 API hazards. Ask main about
an insufficient schema/API or ownership expansion; do not invent architecture. No runtime
tests, migrations, provider edits, API routes, gate persistence or fork creation in this stage.
Persistent data/bug escalation and human retry/rewind policy belong32. Main reviews reachable
failures against Pydantic/SDK guarantees before requesting corrections.

## Review decisions

- Request-level LLM recovery: existing complete owns the single output-repair loop. A transient
  failure inside its repair request is reachable; outer complete replay would reset that loop.
  Explicit per-request injection preserves default B6 behavior and the existing SDK retry=0.
- The shared domain FakeLlm override must accept the new optional keyword; this is fixture
  compatibility, not a reason to broaden runtime validation against the user's test stop.
