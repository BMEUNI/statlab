---
status: completed
dependencies: [06, 21, 22, 23]
completion_date: 2026-10-04
implementation_notes: "B8 accepted on 1e9fe6a plus recorded dirty inputs: 177 focused passes; 2428 full passes, one designed skip, 100% line/branch; full static exits 0. See receipts/B8.md. Committed with exact accepted product inputs."
---

# Chunk 24 — durable candidate fan-out

## Goal

Extend the accepted custom engine to advance independent candidate branches concurrently,
then join before the shared comparison/reporting tail. Keep shared artifacts identical,
test exposure fail-closed, append-only attempts and independently durable recovery.

## Prerequisites and reuse

Architecture: PLAN D1/D3/D12/D17/D24/D26–D29/D31/D32/D36c, normative §4 spine,
§4.2 budget, §5.1 restart and §14 deterministic concurrency checks.
B7 receipt proves the existing node-boundary/checkpointer/event/restart implementation;
B6 receipt proves dispatch allow-lists, train-only fitting, lineage and atomic test-score claims.
Reuse those implementations/evidence; neither server semaphore nor MCP client needs rewriting.
Reuse CandidateSpec/CandidateSet validation and DataContract.constraints. Family registry/cap
belongs to 27b; guided gates/controls/boot wiring remain 31–33.

## Exact owned files

- backend/statlab/graph/state.py, checkpoint.py, engine.py, reconciliation.py, __init__.py
- backend/statlab/graph/candidates.py (new scheduling/budget/tool-boundary helpers)
- backend/statlab/persistence/models.py, repository.py (candidate wave/status callers only)
- tests/test_candidate_fanout.py, tests/test_candidate_fanout_tools.py (new)
- tests/test_graph_state.py, test_graph_checkpoint.py, test_graph_engine.py,
  test_graph_engine_tools.py, test_restart_reconciliation.py, test_repository.py
  (only affected shared-state/API regressions)

No MCP/server/contracts/config/migration edits. Ask main before additional ownership or
architecture changes. Main owns plans. Worker is not alone; preserve other edits.

## Frozen behavior and public seams

1. Keep RunLoop/NodeExecutor/NodeInvocation and the existing no-candidate/single-candidate path.
   RunState.initial accepts multiple CandidateSpec values and assigns stable c_01, c_02, …
   with existing run-qualified SQL identities. Canonical progress is a tuple of branches;
   expose a read-only `candidate` compatibility view for existing single-candidate consumers.
   Load B7 checkpoint JSON's singular candidate without dropping progress. Freeze candidate
   wave, ordered boundary history, current position, retune count, status and halted boundary;
   checkpoint JSON includes the scheduling declaration and completed shared history.
   Pydantic validates topology/unique branch identities and durable halt consistency.
2. `TraversalPlan` is domain-supplied, frozen and generic: optional `overlap_shared` node name,
   optional `design_node`, and paired `training_node`/`evaluation_node` names. It refers only
   to the supplied spine; validate kinds/order once before side effects. No domain names or
   numeric node positions in graph code. The contiguous PER_CANDIDATE region is the fan-out;
   preceding/following SHARED nodes are barriers. Explicit overlap permits the final preceding
   SHARED node to run concurrently with the candidate region; join it before the shared tail.
   Its input snapshot contains completed shared prerequisites and no candidate-local result.
3. RunLoop takes optional typed `constraints`, `traversal`, `candidate_planner`, `retune_decider`
   and `control` arguments. Defaults preserve B7 behavior. `CandidatePlanner` receives the
   current RunState and next wave number, returning a tuple of new CandidateSpec proposals;
   an empty tuple ends search. Initial supplied shortlist is wave 1. Validate the accumulated
   shortlist with CandidateSet, retaining the original baseline rather than requiring another
   baseline per wave. When another wave is requested, append a new design-node execution,
   then run only new candidates; never rerun split/overlap/inference. `RetuneDecider` receives
   current state and candidate after evaluation and returns bool. True returns to training;
   prior preprocessing/features remain immutable and every training/evaluation iteration gets
   a fresh SQL attempt. Missing callbacks mean one wave/no retuning.
4. Candidate tasks genuinely overlap: each may advance to its next node while another waits.
   Exactly one serialized coordinator merges each completed boundary into the latest state,
   saves it, emits events, and only then prunes that execution's unretained dataframe outputs.
   Never save a stale branch snapshot over a sibling's boundary. NodeInvocation carries its
   candidate and the immutable snapshot available at dispatch; shared tail sees all outcomes.
   Failure stops only that branch; healthy siblings finish. Awaiting-user stops that branch;
   join produces a dormant run without dispatching shared tail until resolved. With no viable
   candidate, fail the run rather than invent a champion. Preserve candidate failure details.
   Events use existing declared node/run event names with candidate identity in payload.
5. Enforce max_candidates across all waves before inserting/dispatching excess candidates;
   count failed candidates too. Refuse duplicate spec hashes across waves. Cap search waves
   and per-candidate retunes before new node attempts. Use existing CandidateSet guarantees
   for baseline/labels/declared variation, rather than duplicating their validators.
6. `RunControl` exposes request_pause/request_cancel; cancel takes precedence. `ToolBoundary`
   delegates to one entered B6 McpClient and its unchanged CallContext/call contract. It checks
   control before dispatch and after a dispatched call finishes, preventing subsequent calls
   inside the same node. Preserve ordinary asyncio cancellation identity; explicit lifecycle
   requests do not interrupt a running server thread. Loop joins in-flight calls, checkpoints
   paused/cancelled status, keeps unfinished attempt outputs, and never prunes interrupted nodes.
   Boundary stop is a dedicated internal signal, not converted into a node bug. API control
   handlers/resume overrides belong to 31; expose this callable seam now.
7. Budget is fit admission, not thread preemption: account discovered `heavy` calls' audited
   duration_ms; stop admission of further heavy calls/waves/retunes at max_total_fit_seconds.
   Rehydrate accounting from SQL audit on restart so failed/crashed attempts are charged too.
   Concurrent already-admitted fits may exceed the threshold; report that fact in state/outcome,
   never pretend to forcibly stop an estimator. Existing tuning max_fit_seconds remains its
   tool's local bounded best-so-far limit. No LLM dollar cap or duration-based model ranking.
   Exhaustion preserves successful comparison candidates; no excess dispatch. Use typed budget
   state/helper in candidates.py with a deterministic injected clock only if genuinely needed.
8. Extend RestartReconciler to every branch's committed/halted boundaries and overlap boundary.
   Replay only unfinished branches; repair checkpoint-ahead SQL/events idempotently, fail each
   interrupted attempt with the accepted process-terminated failure, leave dormant gates and
   terminal runs alone. Never clear test_scored_at/fingerprint/audit or prune crash artifacts.

## Focused verification

Use event/barrier tests, never sleeps/timing thresholds/core counts. Small synthetic spines
prove independent candidate advancement, serial durable merge, overlap/join, failed sibling,
search and retune caps, unchanged shared inputs, pause/cancel between two calls, in-flight drain,
budget admission, crash windows and B7 checkpoint compatibility. Validate actual bad persisted
JSON with model_validate/validate_json; do not bypass Pydantic to invent unreachable states.

Real-tool integration through B6 client: at least three candidates reference byte-identical
train/valid/test split artifacts; candidate validation is the selection input; attempted early
test access is refused before injected session dispatch; select_champion precedes exactly one
champion test score and persisted metric model_id equals runs.champion_model_id. Explicit
domain overlap proof demonstrates one shared inference operation while candidates are active.
Reuse existing server semaphore tests for its two-permit admission behavior if unchanged;
new tests must prove the new scheduler overlaps rather than assuming existing server evidence
proves backend fan-out. Three concurrent runs retain run-scoped IDs/no SQLite lock errors.

Focused selector group:
`make test-fast TESTS='tests/test_candidate_fanout.py tests/test_candidate_fanout_tools.py
tests/test_graph_state.py tests/test_graph_checkpoint.py tests/test_graph_engine.py
tests/test_graph_engine_tools.py tests/test_restart_reconciliation.py'`
plus only affected repository selectors. Run scoped mypy and Ruff check/format on owned edits.
No full suite/coverage or mutation quota. These deterministic integration/regression tests are
the required probes; no separate artifact export/replay requirement in this engine chunk.

## DoD / stop conditions

Focused green handoff: exact changed files/hash manifest, real commands/exits/summaries/logs,
requirements demonstrated, B7 regressions and next-stage hazards. Stop there; no commit,
plan/status edits or global gate. If the declared seam cannot satisfy a named invariant, stop
and report the specific blocker before introducing a framework, contract/schema migration,
concrete modelling nodes or changing B6 dispatch semantics.

## Consolidated review addendum

2026-10-04: corrected the running SQL `current_node` projection at serialized fan-out
dispatch and included shared halted boundaries when republishing dormant pending gates.
Seven public-loop/restart regressions failed before the fixes; the affected 61-case group
and scoped static checks then exited 0. See the stage-24 focused receipt for exact evidence.

Coverage review removed unreachable coordinator/result/repair guards and a redundant
lifecycle check; valid public callback, SQL and persisted-topology paths gained focused
proof. A reachable second-wave design halt previously failed checkpoint validation:
only an unfinished declared design at the candidate barrier is now admitted, preserving
shared overlap and preventing replanning. Reload/reconciliation and corruption refusals
are proved with and without overlap. Final stage group: 177 passed; focused/static exits 0.
