# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**24 of 41 chunks complete, plus revisions 09r and 20r. B7 accepted and committed 2026-10-04.**
Acceptance parent: `4b3c19d`; the B7 commit contains the receipt's exact accepted product inputs.
Phases A/B and M7b complete. B6 remains committed at `de2aa83`.

Accepted B7: **2298 passed, one designed skip; 100% line/branch; full mypy/Ruff/format exit 0**.
JUnit coverage-run time: 78.857 s. [Durable B7 receipt](./receipts/B7.md) records candidate hashes,
focused handoffs, review corrections, the initial coverage-only failure and final acceptance.
B6 client/dependency inputs and required mutation/replay evidence were reused; no new dependency
or migration. Docs/status changes preserve product acceptance; no startup baseline.

B7 proves M8a's engine/restart subset. PLAN's node-6/fan-out concurrency proof remains chunk 24;
the complete M8a milestone is not yet closed.

## Next: batch B8 (chunks 24 candidate fan-out + 26 routing/skills)

Draft bounded chunk 24 from PLAN D17/D24/D29/D31/D32/D36c and the accepted engine/restart seams.
Specify the shared-split guarantee, candidate progress/checkpoints, real fan-out/concurrency proof,
test-lock preservation, budget/search-wave behavior and tool-boundary control requirements.
Use `implementer_strong` for these concurrency/state boundaries. Then bound chunk 26 routing,
skills validation and project memory using the accepted B6 LLM client and declared spine.

Follow AGENTS: exact ownership, focused terminal handoffs, consolidated review and one stable
acceptance gate. Work sequentially unless independent parallel ownership is explicitly authorized.

## B7 seams and hazards for B8

- Reuse frozen `RunState`/`BoundaryResult`, `Checkpointer`, `EventBus`, `RunLoop` and shared
  `RunTracker`; generic graph code contains no domain node names. Current state accepts at most
  one supplied candidate. Public `c_01` and run-qualified DB identity are distinct.
- `RunLoop` saves each boundary before pruning exact-execution dataframe intermediates; all
  retained refs and non-frame artifacts survive. Cancellation/crashed attempts never prune.
- `RestartReconciler` prepares the whole scan before returning scheduled task handles. Caller
  owns awaiting/cancellation/exception consumption; boot invocation precedes new live runs.
  Paused/awaiting remain dormant; only actual unanswered pending gates are republished.
- Cursor is recovery authority. SQL-ahead outcome replays a new attempt; checkpoint-ahead state
  repairs audit/events/status without dispatch. Interrupted attempt is bug/`process terminated`.
  Test score claims/fingerprints and prior tool audit are never reset, so replay may fail closed.
- Event bus is one writer per run/process; no multi-process writers. Failed checkpoint cleanup
  can leave a temporary file while retaining the original error and prior checkpoint.
- Share one B6 LLM client/semaphore. One output repair, timeout 60, no hidden retry; cancellation
  retains identity. SDK validates ordinary fields; usage extras remain boundary checks.

## Deferred responsibilities

| Responsibility | Owner |
|---|---|
| Multi-candidate progress/fan-out and full M8a concurrency proof | 24 |
| Estimator validation, family cap D28, `max_candidates` | 27b |
| Remaining table repositories | First real caller's chunk |
| Provider-name environment check, within `llm/` | 26 |
| Cancel running node at tool-call boundary (D36c) | 31 |
| Gate creation/answering and callable recovery API boot wiring | 32 / 33 |
| Modelling heuristics/pitfalls | Human; 26 scaffolds TODO headings only (D12) |

## References on demand

- [Verified environment and dependency pitfalls](./ENVIRONMENT.md): search affected dependency only.
- [Historical workflow, conventions and completed batches](./WORKFLOW_HISTORY.md): old counts and
  exceptions are historical; root AGENTS wins. Full previous continuation file retained there.
- [Codex setup and audit](./CODEX_WORKFLOW.md): human guide; not required startup context.
- [Revision audit trail](./REVISIONS_IMPLEMENTED.md): historical, not another gate checklist.
