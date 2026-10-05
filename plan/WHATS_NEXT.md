# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**31 of 41 chunks implemented, plus revisions 09r and 20r. B11 reviewed with static-only acceptance 2026-10-05.**
B9–B11 runtime/coverage acceptance is outstanding under the user's test stop; B8 remains the last
batch with full behavioral/coverage acceptance. [B9 receipt](./receipts/B9.md),
[B10 receipt](./receipts/B10.md) and [B11 receipt](./receipts/B11.md) record this limit. B9 committed at `31bdf67`.
B7 committed at `1e9fe6a`; B6 committed at `de2aa83`. Phases A/B and M7b complete.

Accepted B8: **2428 passed, one designed skip; 100% line/branch; full mypy/Ruff/format exit 0**.
Full pytest time: 98.83 s. [Durable B8 receipt](./receipts/B8.md) records exact dirty product
hashes on `1e9fe6a`, focused handoffs, review corrections and both acceptance attempts.
B6 client/LLM/lineage/semaphore/mutation/replay evidence remains accepted; no dependency or
migration change. Docs/status edits preserve product evidence; no startup baseline.

B7 plus B8 prove M8a and M8b, including shared inference overlapping real candidate execution,
three identical split consumers, and exactly one selected champion test score. Routing/skills
are M9 prerequisites; B9–B11 domain/control behavior still needs runtime acceptance.

## Next: batch B12 (chunks31–32, recovery/lifecycle then gates/rewind)

Write chunk31's bounded detailed spec from its dependency rows and relevant PLAN decisions, using
B11's typed control callbacks and accepted engine/reconciliation seams. Implement error-class
recovery and pause/resume/cancel first. Include verified parent producer-audit composition for
fork export/snapshot; retained parent refs must not invent artifact rows/history. Establish an
existing same-run LLM audit anchor for inbound controls before the first execution. After31's
terminal handoff, write32's detailed spec for seven gate actions, guided policy and fork rewind.
Keep dependent stages sequential. Main owns architecture/review/acceptance; implementers own source.
User authorizes automatic local commits after finished work; never push. Runtime tests remain
stopped by user; use focused statics and one final global static gate, disclose runtime gaps.

## Accepted seams and next-stage hazards

- `RunState.branches`, `TraversalPlan`, `RunLoop`, `NodeInvocation` and `ToolBoundary` are
  generic; domain code supplies spine/overlap/design/training/evaluation declarations and
  callbacks. B9 can bootstrap the approved initial shortlist through the existing planner,
  deterministic candidate IDs, SQL ensure and checkpoint before candidate launch. This new
  path is reviewed/static-clean but runtime-unverified; no initial-budget launch when exhausted.
  Shared inference receives no candidate-local progress; completed siblings merge durably.
- B10 supplies callable nodes 7–11, candidate-bound runtime, train/valid pipeline manifests,
  post-score retune decisions and cumulative later-wave planning. Prepared-frame CV is a
  diagnostic, not end-to-end leakage-free CV. Requested supported curves/PDP retain plots.
  Frequency encoding, random search and calibrated-estimator CV diagnostics fail honestly.
  B11 node12 checks latest model/evaluation alignment, freezes selection before exact test replay,
  and retains ambiguous score claims. Node13 gates guided preservation, requires a supplied
  lockfile and records immutable skill/model/lock hashes plus deterministic outcome memory.
  `TabularExecutor` now covers all13; B11 is reviewed/static-clean, runtime-unverified.
- Restart cursor/checkpoint remains authority, including repeated design and branch gates.
  `RestartReconciler` prepares a full scan before scheduling; caller owns returned tasks.
  Interrupted attempts retain audit/test claims and artifacts; paused/awaiting remain dormant.
  Boot/API wiring and resume/answer handling remain 31–33.
- Domain tool calls must use `ToolBoundary` with the entered B6 client and unchanged allowed
  context. Heavy budget is SQL-audited admission; admitted concurrent fits may overshoot.
  Explicit controls drain calls; ordinary cancellation retains identity. No thread preemption.
- Pass declared spine and entered tool registry to `load_run_skill`, then `skill.allowed`
  unchanged to `CallContext`. Snapshot bytes/hash are immutable; B11 adds manifest integration.
  User-authorized modelling policy now fills all 26 Heuristics/Pitfalls sections (D12);
  all role defaults use V4.1-Flash (D16). [Policy/model receipt](./receipts/modelling-skill-2026-10-04.md):
  18 focused skill checks passed; full acceptance timed out; further tests stopped by user.
  B9–B11 provide callable nodes1–13; this policy adjunct remains separate evidence.
- Initialize one immutable `PromptContext` from project memory. `remember` is append-only;
  later writes affect future contexts/runs. B11 Q&A routes by current-node role/skill and validates
  visible citations. Status preserves canonical facts and explicitly truncates oversized text.
  `ControlRouter` requires explicit decision/change/lifecycle callbacks and an existing same-run
  audit anchor; it performs no engine lifecycle work. B12/API owns handlers/delivery/early anchors.
- Startup must validate selected role models and explicit skill model literals and preserve
  effective runtime credentials. The isolated SDK check fails closed for providers with no
  SDK-established credential requirements; additional provider support needs a future decision.
- Reuse one shared B6 LLM client/semaphore: one repair, 60-second timeout, no hidden retry.
  Event bus remains one writer per run/process; no multi-process coordination added.

## Deferred responsibilities

| Responsibility | Owner |
|---|---|
| Verified ancestor producer logs for fork export/snapshot | 31 |
| Lifecycle/change/gate handlers, resume and callable recovery API boot wiring | 31–34 |
| Remaining table repositories | First real caller's chunk |

## References on demand

- [Verified environment and dependency pitfalls](./ENVIRONMENT.md): search affected dependency only.
- [Historical workflow](./WORKFLOW_HISTORY.md): old counts/exceptions are historical; root AGENTS wins.
- [Codex setup and audit](./CODEX_WORKFLOW.md): human guide; not required startup context.
- [Revision audit trail](./REVISIONS_IMPLEMENTED.md): historical, not another gate checklist.
