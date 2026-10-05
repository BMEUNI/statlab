---
status: implemented
dependencies: [24, 31]
completion_date: 2026-10-05
implementation_notes: "B12 reviewed; focused and global static gates exit0. Runtime tests stopped by user; behavioral/coverage acceptance deferred. See receipts/B12.md."
---

# Chunk 32 — persistent gates and fork rewind

## Goal

Persist seven gate actions and guided policy; wire decision/change controls to real lifecycle
and fork services. Rewind halts its parent, retains verified immutable inputs and resumes a
new run before the target declared node. No API routes, UI, providers or migrations.

## Prerequisites / reuse

PLAN §5/5.1/8 and D10/D11/D17/D27/D29/D32/D36 are authority. Canonical autonomous mode;
guided declared always/exception/never gates, indefinite persistence, no timeout approval.
Reuse actual Pydantic decision schemas and existing post-approval domain/contract validators.
Existing schemas already reject invalid shapes; add guards only for reachable ownership,
phase, concurrency and transition failures. B6 dispatch/lineage/one repair/semaphore evidence
remains historical evidence for unchanged properties. No new SDK retries/provider config.
No touched dependency change: Python3.14.3/Pydantic2.13.5/mypy2.3.1/Ruff0.16.6.

31 terminal handoff: base b9b4f87;17 changed Python files, focused mypy18/Ruff/format/diff
all0; no runtime checks. `RunLifecycle(loop, initial)` caller-awaited start/resume/pause/cancel,
state/audit_anchor/handle; no hidden tasks. Start/resume must pass this service for drain.
`RunLoop.ensure_audit_anchor` persists checkpoint then creates/reuses/promotes a real pending
node execution. LLM complete takes optional generic `CallRecovery`, per request, default None.
`run_ancestry` current→root verifies projects/cycles/missing rows. `list_artifacts(checkpoint=...)`
and `logged_calls(fork_aware=True, checkpoint=...)` verify retained boundaries/ref ownership;
actual ancestor producers include pruned intermediates, raw sent args unchanged.
`storage.validate_checkpoint_ownership` verifies ancestry/node/seq/status/candidate/spec.
RestartReconciler still assumes own execution rows; never rewrite ancestor terminal rows/events.
Pending ancestor executions cannot anchor same-run LLM. Paused pending gates resume dormant.
Focused receipt: `projects/.receipts/B12-stage31/receipt.json` (persist batch facts in final receipt).

## Exact owned files

- New `contracts/statlab_contracts/gates.py` for typed wire answers/actions.
- New `backend/statlab/graph/gates.py`, `backend/statlab/graph/rewind.py`.
- `backend/statlab/graph/{engine,state,lifecycle,reconciliation,router,control}.py`.
- `backend/statlab/persistence/{repository,models}.py`, `backend/statlab/storage.py`.
- `backend/statlab/domains/{runtime,candidate_pipeline,persistence}.py`.
- New `backend/statlab/domains/rewind.py` only if tabular provenance/report integration needs
  domain-specific names; graph modules must remain generic.
- New `tests/test_control_gates.py`, `tests/test_control_rewind.py`.
- `tests/test_control_router.py`, `tests/test_control_qa_status.py`,
  `tests/test_control_lifecycle.py`, `tests/test_restart_reconciliation.py`,
  `tests/test_storage.py`, `tests/test_nodes_persistence.py`, `tests/test_nodes_foundation.py`
  only affected regression/fixture compatibility.
No other files without main approval. Not alone; preserve31 and other edits. No worker plan
rewrites or commits. Caller boot/routes/task registry remain33–34.

## Frozen behavior and API

### Durable gate service

1. Typed frozen extra-forbid `GateAnswer` has exactly the seven actions: approve,
   approve_with_edits, instruct, approve_all_remaining, skip_node, rewind, abort.
   Require nonblank instruction/reason/declared rewind target only for respective actions;
   edits are JSON field overrides, never code/formulas. Validate combined proposal through
   the actual decision model and existing domain post-approval validators before dispatch.
2. Gate service binds same conn/run/lifecycle/spine/events, offers DomainRuntime-compatible
   approval and decision-override callbacks plus typed answer and ControlRouter decision
   handler. Persist existing gates table question/answer JSON, node execution ownership,
   proposal/schema/evidence/reasoning/editable/default/options, candidate identity and phase.
   SQL conditional answer transition prevents double/stale/foreign answers. Answers and
   instruction/proposal revision history remain durable and attributable to the original gate.
   Open pending gates emit gate.opened; applied answers/revisions emit documented events.
3. Guided policy: always gates proposals; exception only actual material warnings/exceptions
   indicated by existing domain helper; never ungated. Autonomous ungated except exhausted
   failures escalating through engine hook. Approve-all applies current proposal then changes
   SQL/checkpoint/active lifecycle binding mode consistently to autonomous. No duplicated gate
   on restart, no timeout/autopass. Existing standalone Approval callables remain compatible.
4. Explicit pre-action versus after-action metadata: DomainRuntime.approve adds a default
   after_action=False keyword and passes generic metadata through NodeInvocation (default
   field keeps existing executor/three-argument Approval signatures compatible).
   exception_approval forwards its existing after_action flag. Pre-action accepted decisions
   bypass a fresh LLM proposal via typed runtime decision override, then execute remaining
   node work with existing contract validators. Repeated read-only evidence calls may occur;
   previously completed fits/scores must not. After-action acknowledgment commits the
   stored awaiting NodeResult as completed, retaining artifacts/metrics/summary and avoiding
   executor replay. Reject edits/instructions that would change completed work; rewind is
   the route for those changes. Preserve candidate sibling pending/completed state.
5. Approve applies exact stored proposal; edits validate an explicitly merged JSON proposal;
   instruct uses caller-injected typed revision callback (or existing shared LLM with same
   immutable skill/context/schema) and re-gates without executing tools. Persist transcript
   in gate revision history so no new session/message schema is necessary. Approved revised
   decisions resolve by original gate/execution/candidate identity, not current wall time.
   Skip requires skippable declared node and reason, creates a real skipped boundary/event;
   rewind returns new_run_id through change service; abort drains/cancels and retains files.
6. Exhausted call/data/bug errors persist escalation even autonomous; failed candidate
   cannot silently disappear while other candidates continue. Engine accepts a generic
   injected failure/gate hook; convert failure to awaiting boundary retaining structured
   Failure in gate payload, with safe message and internal cause only in server diagnostics.
   No automatic whole-node retry and no approve/edits path replays an ambiguous dispatched
   test score. Failure gate actions must be truthful: abort/rewind/declared skip available;
   reject approval when no safe resume strategy exists rather than loop the same failure.
7. Persistence ordering: durable answers precede continuation; idempotent preparation repairs
   answer/checkpoint/status/event interruption windows without reapplying work. Provide a
   gate reconciliation seam to RestartReconciler before it schedules tasks. It rehydrates
   unanswered gates dormant and prepares accepted continuations once. It must not rewrite
   ancestor execution rows/events. Lifecycle owns shared transition lock/drain; no answer
   races with active execution, pending stop, mode switch or second answer. Expose caller-owned
   continuation state/task scheduling, never hidden orphan tasks. No implicit gate approval
   from lifecycle resume. Callers may start ready state through the same lifecycle binding.
8. ControlRouter typed decision/change handlers use real service bindings and existing same-run
   audit anchor. Parse gate answer/rewind intent through shared LLM plus exact typed schemas;
   classification alone never applies invented parameters. Route optional recovery per request
   without multiplying output repair. Visible control replies retain category/new_run_id.

### Fork rewind

9. Generic fork service validates a declared target name/position and binding; halt/drain active
   parent via lifecycle before snapshot. Preserve a completed original; mark abandoned halted
   original superseded only once the child is durably published (D11/D17/D36). Parent remains
   usable/diagnosable on publication failure; failed partial child never starts on restart.
   Child run carries same project/contract/config/effective mode and forked_from_run_id.
10. Project a validated RunState retaining exactly committed boundaries before target; target
    itself reruns. Shared prefix rewind drops downstream candidates; candidate-region rewind
    rewinds affected branches coherently, preserving approved specs and genuine producer
    history, dropping downstream outcomes/retunes. Shared tail rewind retains completed
    candidate frontiers. Handle existing overlap, waves and retunes through declared traversal
    metadata; never infer domain node names in graph. If requested frontier is unreachable,
    reject clearly before publishing, do not fabricate results. Report any ambiguous projection
    decision to main. No inherited pending gate/execution or abandoned outputs in child.
    Frozen wave/retune rule: for a target before or inside the candidate region, retain all
    approved specifications (waves with a committed declared design boundary; caller-supplied
    candidates when no design is declared), consolidate them into child wave1, and retain only
    the latest committed design boundary as the genuine cumulative shortlist audit. For a
    target at/before design, drop that boundary and downstream specifications. Drop all retune
    rounds; retained per-candidate prefix is its first-pass committed prefix before target,
    retune_count=0. All retained branches restart target; if a branch lacks required preceding
    boundaries, reject that frontier before child creation rather than silently start earlier.
    Shared tail targets retain complete wave/retune histories and completed/terminal-failed
    branch frontiers. An inherited terminal failed boundary is immutable diagnostic history,
    never a runnable/pending gate; verify its ancestry too. Record source wave/projection in
    fork metadata/response. Never inherit a live/awaiting gate or a running/pending SQL execution.
11. Use existing fork_artifacts to hard-link exactly retained refs, same inode/ref, no new artifact
    rows; validate all required files before publication (helper skips missing/pruned files).
    Copy immutable contract/skill/lock inputs; do not copy mutable checkpoint/event logs.
    Retained report/plot publication needed for preservation belongs domain callback and must
    agree with stored hashes; new fork writes cannot mutate parent's report or inputs.
    Verify inherited committed execution ownership via31 helper in restart reconciliation,
    while retaining original IDs. Ensure child candidates are durably registered with their own
    IDs/specs before execution. No ancestor status/summary writes from child reconciliation.
12. Inherit test fingerprint only from a retained valid split; child test-scoring claim stays new
    (never clear parent claim). A rewind before champion may score once in child and must
    disclose prior project exposure using existing D32 count. A fork after a completed champion
    retains that score's original provenance for preservation; don't manufacture a child score
    claim or silently require another score. Adapt domain preservation to resolve the verified
    retained ancestor champion/model/score audit and report, keeping current-run path unchanged.
    Record inheritance facts in preservation metadata. Parent lineage locks remain authoritative.
13. Returned child identity/state is concrete but execution is caller scheduled. A resolver
    creates fresh bound loop/clients/runtime/events at child paths using existing seams;
    no hidden provider, server, thread or task bootstrap. Early child controls use a real
    same-run pending execution, never an ancestor audit anchor.

## Focused checks / stop

No runtime tests/collection/coverage/mutations/probes/replays/live calls (explicit user stop).
Author unexecuted offline selectors in test_control_gates.py and test_control_rewind.py:
seven actions, edited validation/persistent revision, indefinite restart, duplicate/foreign
answers, before/after-action no replay, approve-all consistent mode, autonomous escalation;
parent drain/supersede, shared/candidate/tail frontiers, overlap/wave/retune projection,
hardlinks/no copied rows/missing refs, inherited reconciliation/no ancestor mutation,
fork preservation/score provenance and incomplete publication. No count quota.
Run only scoped mypy/Ruff check/format and diff checks. Main owns one final stable global
static gate and consolidated review. Stop at terminal focused-green handoff: exact files,
base/hashes, commands/genuine exits/logs, unexecuted selectors, public APIs and33–34 hazards.
No schema migration/API/UI/provider changes or worker commits. Report scope blockers first.

## Review decisions

- Evaluation proposes `EvaluationActions` before tools, then produces a composite
  `EvaluationDecision` after scoring. Persist and validate the actual proposal type using
  the declared model's existing Pydantic MRO; no dynamic imports. Override only the exact
  requested pre-action type. `ValidationConclusion` remains fresh post-score reasoning;
  after-action composite approval acknowledges stored work.
- Candidate-region rewind consolidates approved specifications into one fresh wave and
  drops retunes. Shared-tail rewind preserves completed frontiers and immutable failed
  diagnostics. Reject unreachable prefixes before child publication.
- Review correction: positive GateAnswer test cases use typed kwargs and exact Literal
  actions; negative payload validation remains unchanged. Focused static checks exit0.
