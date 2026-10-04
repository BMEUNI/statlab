---
status: completed
dependencies: [02, 04, 05, 06, 21]
completion_date: 2026-10-04
implementation_notes: "B7 accepted and committed: 2298 passed, one designed skip; 100% line/branch; full mypy/Ruff clean. See receipts/B7.md; full M8a concurrency proof remains chunk 24."
---

# Chunk 22 — Engine core

## Goal and authority

Build the custom, domain-agnostic async run loop, typed node-boundary checkpoint and durable
event bus. PLAN D1/D3/D9/D17/D24/D27, §4.3.1, §10.1–10.2 and §11.1 govern. Root AGENTS.md
governs execution. Chunk 23 adds boot reconciliation after this stage's focused handoff.

## Prerequisites and reuse

B6 is accepted at `de2aa83`, with docs-only descendants through startup `4b3c19d`.
Reuse `NodeSpec`/`NodeResult`, `CandidateSpec`, `DataContract`, `RunPaths`, SQLite transaction,
`create_node_execution`, `McpClient`/`CallContext`, artifact registry and `prune_intermediates`.
Do not repeat B6 lineage mutations or skeleton/reproducibility proofs. No new dependency,
migration, agent framework, LLM routing or statistical node implementation.

Existing NodeResult validates reasons/errors and forbids failed/skipped output artifacts.
NodeSpec is a trusted frozen dataclass. Do not duplicate those validators or bypass them.
ENVIRONMENT: Python 3.14.3, Pydantic 2.13.5 with init_typed=true; stdlib SQLite WAL,
explicit transactions; MCP 2.2.0 uses snake_case Python fields. No LangGraph dependency.

## Exact owned files

- New `backend/statlab/graph/__init__.py`, `state.py`, `checkpoint.py`, `engine.py`.
- New `backend/statlab/events.py`.
- Extend `backend/statlab/persistence/models.py`, `repository.py` only for engine callers:
  run status/current-node writes, node outcomes/attempt lookup/concurrency, candidate row seam.
- New `tests/test_graph_state.py`, `test_graph_checkpoint.py`, `test_events.py`,
  `test_graph_engine.py`, `test_graph_engine_tools.py`.
- `tests/test_persistence_repository.py` only if that exact existing module needs regression
  coverage for changed helpers; otherwise keep repository checks in new engine tests.

No changes to B6 MCP/LLM clients, contracts, storage, SQL schema, dependency/test configuration,
existing skeleton drivers, domain node implementations, plans or policy. Request a bounded
ownership amendment if a real blocker needs another file.

## Frozen behavior/API

1. `RunState` is a frozen Pydantic model: durable run identity; declared spine identity/order;
   next node cursor; status/mode; shared boundary artifact refs/results; optional one-candidate
   progress and typed spec. State contains JSON-sized summaries, not compute objects or secrets.
   Use tuples for ordered frozen fields. A completed/skipped node advances the cursor; failure
   and awaiting_user do not advance it. Keep candidate outputs separate from shared outputs.
   Candidate public IDs are sequential `c_01`, `c_02`, etc. scoped to a run. Since candidates.id
   is a global SQL primary key, derive an unambiguous run-qualified DB identity; do not migrate
   the schema or replace the human CandidateSpec.label with this ID. This stage executes at most
   one supplied candidate; generation, waves, family caps and fan-out belong to 24/27b.
2. `Checkpointer` saves/loads `RunPaths.state_json` using strict JSON/Pydantic validation,
   temporary sibling + flush/fsync + atomic replace. Existing checkpoint survives failed writes.
   Validate run/spine identity before traversal. Missing state is allowed only for a genuinely
   pending new run; malformed or mismatched state fails closed with a package error.
3. `EventBus` appends typed envelopes `{seq, run_id, ts, type, payload}` to events.jsonl.
   Sequence is assigned under one per-run async lock at append time, starts at 1, survives reopen,
   and has one total order. Append/flush succeeds before live notification. Replay `since`
   is exclusive; since >= maximum is valid and yields none. Provide replay + live subscription
   with no lost/duplicated event across registration, and release subscriptions on cancellation.
   HTTP framing, heartbeat, token streaming and MCP call hooks remain later chunks.
   Bus owns the file for this process/run; no multi-process writer support. A torn final record
   without newline may be discarded before the next append; malformed complete records or
   non-monotonic/wrong-run envelopes are persistence errors, never silently skipped.
4. `RunLoop` accepts a declared tuple of NodeSpec and injected async node executor (typed
   protocol/callable), so no node name occurs in generic engine code. Invocation provides
   input RunState, spec, execution identity and optional candidate identity. This supports
   construction of B6 CallContext with the existing trusted contract/dataset/allow-list; the
   engine does not reimplement dispatch authorization or lineage. Executor returns NodeResult.
   SHARED nodes run once sequentially. An optional single candidate runs PER_CANDIDATE nodes
   sequentially between shared stages. A synthetic three-node domain uses the same engine.
   More than one candidate is refused rather than silently serialized as fake fan-out.
5. Save initial input boundary before dispatch. Create an auditable running node row with
   incremented attempt, declared one-based spine seq and concurrency_at_start. Count concurrent
   live runs through an injected/shared loop-owned tracker; do not assume CPU permits or add
   global fit scheduling. Emit run.status, node.started and terminal node events from persisted
   outcomes. Results/summary/error/ended_at are persisted; metrics remain in typed boundary
   result until model-linked metric consumers exist (metrics table requires model_id).
6. Completed/skipped outcomes become a durable boundary before the next invocation.
   Non-skippable skip is an engine defect. Failed/awaiting_user halt traversal with matching
   durable run status; awaiting state must retain its pending result and cursor for chunk 23.
   Gate decision generation/answering remains 32; no autonomous approval. Reject duplicate
   concurrent invocation of the same run. Unexpected executor exceptions become a safe bug
   Failure with original cause internal; tool/LLM domain failures preserve their error_class.
   Cancellation propagates unchanged and leaves recoverable running input, without pruning.
7. After completed boundary is durably saved, call storage.prune_intermediates with artifacts
   produced by this exact node execution's logged calls. Keep all committed boundary refs,
   including shared splits and earlier/candidate retained outputs. Filter to within-node
   dataframe intermediates: do not delete fitted models/transformers, reports or diagnostics.
   Honor settings.keep_intermediates. Never prune failed/skipped/awaiting or crashed attempts.
   No pruning before the durable boundary: a crash must not destroy replay input.
8. Run row and filesystem cannot commit atomically. State cursor is resume authority; a crash
   after SQL outcome but before checkpoint may replay that node with a new attempt. Never clear
   B6 test_scored_at/test_fingerprint or rewrite tool audit. If a retained score claim prevents
   replay, fail closed; chunk 23 must preserve it. No automatic retries/backoff here (31).

## Focused proof

Run `make test-fast TESTS='tests/test_graph_state.py tests/test_graph_checkpoint.py
tests/test_events.py tests/test_graph_engine.py tests/test_graph_engine_tools.py'` with genuine
exit/log receipt. Add affected repository selectors only if existing behavior changed.
Targeted Ruff check/format and scoped mypy for owned Python files. No global coverage yet.

Prove JSON round-trip/identity mismatch; failed atomic replace leaves prior state; event reopen,
concurrent append, exclusive replay, replay/live handover and cancellation; torn tail handling;
three-node synthetic traversal; skip/failure/awaiting/cancellation; unique attempts and concurrent
runs; shared/candidate state separation; D24 boundary keep + intermediate deletion and no prune
on failed attempt. Drive a minimal one-candidate autonomous domain through real served MCP tools
using existing mcp_session fixtures, B6 client and engine-created execution rows. Reuse existing
tool inputs; no paid LLM. This need not reprove the completed 33-tool skeleton.

No mutation quota. Ordinary failure-injection tests adequately prove checkpoint/prune ordering
and events persistence. No standalone pipeline artifacts required by B7.

## DoD and stop conditions

Stop at focused green handoff: exact changed files, startup candidate plus diff/file hashes,
commands/selectors, exits, test/static summaries, full-log paths, exported API and chunk-23 hazards.
Do not commit, push, rewrite plans, run global gates, implement dependent restart scanning or fan-out.
If scope appears to require over ~200 focused tests, stop and report a proposed split.

B7 proves M8a engine/restart subset and one-candidate real-tool execution. PLAN M8a's
node-6/fan-out concurrency requirement belongs to dependency-ready chunk 24 (D31/D29); do not
claim that requirement is proved by sequential B7. Concrete 13-node LLM decisions arrive 27–29.
Final batch acceptance and durable receipt are main-owned after chunk 23 and consolidated review.

## Consolidated review addendum

Actual corrections: checkpoint cleanup could mask the original save error when unlink also
failed; cleanup now runs only on failed save and preserves its package error/cause. Previous
checkpoint survives; failed cleanup can leave a temporary file. Two tests now construct valid
typed committed states instead of bypassing cursor/result validation. Stage 23 closed direct
initialization/history and SQL control-status gaps and removed the undeclared awaiting node event.
Final coverage identified a Protocol-stub exit arc; NodeExecutor is now an equivalent stdlib
Callable/Awaitable alias, preserving exported name and positional signature without a fake test
or coverage suppression. Focused evidence and final acceptance are retained in
[B7 receipt](./receipts/B7.md).
