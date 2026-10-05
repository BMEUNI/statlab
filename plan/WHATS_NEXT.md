# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**29 of 41 chunks implemented, plus revisions 09r and 20r. B10 reviewed with static-only acceptance 2026-10-04.**
B9/B10 runtime/coverage acceptance is outstanding under the user's test stop; B8 remains the last
batch with full behavioral/coverage acceptance. [B9 receipt](./receipts/B9.md) and
[B10 receipt](./receipts/B10.md) record this limit. B9 committed at `31bdf67`.
B7 committed at `1e9fe6a`; B6 committed at `de2aa83`. Phases A/B and M7b complete.

Accepted B8: **2428 passed, one designed skip; 100% line/branch; full mypy/Ruff/format exit 0**.
Full pytest time: 98.83 s. [Durable B8 receipt](./receipts/B8.md) records exact dirty product
hashes on `1e9fe6a`, focused handoffs, review corrections and both acceptance attempts.
B6 client/LLM/lineage/semaphore/mutation/replay evidence remains accepted; no dependency or
migration change. Docs/status edits preserve product evidence; no startup baseline.

B7 plus B8 prove M8a and M8b, including shared inference overlapping real candidate execution,
three identical split consumers, and exactly one selected champion test score. Routing/skills
are M9 prerequisites; concrete domain decisions/full agent execution remain B9–B11.

## Next: batch B11 (chunks 29–30, champion/persistence and control router)

Write bounded detailed specs from dependency rows and relevant PLAN decisions. Implement
chunk 29 first: champion selection, one test score, report and artifact persistence through
accepted tools. Reuse B10's latest successful candidate model/evaluation, selected threshold,
ordered transform replay and fitted refs. Before scoring test, select once and replay only
the champion's transform-only calls in order with unchanged non-frame arguments. Preserve
D32 lineage/exposure disclosure and D33 selection-bias/CIs. Node 13 consumes real tool logs
for export/snapshot and records skill/model manifests. Then implement chunk 30's control
router using accepted engine and routing seams; persistent gate/API handlers remain 31–33.
Keep stages sequential. Main owns architecture/review/acceptance; implementers own source/tests.
User authorizes automatic local commits after finished work (2026-10-04); never push.
Test execution remains stopped by user; preserve this constraint and disclose runtime gaps.

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
  Latest model/evaluation and threshold must stay aligned when node 12 replays a champion.
- Restart cursor/checkpoint remains authority, including repeated design and branch gates.
  `RestartReconciler` prepares a full scan before scheduling; caller owns returned tasks.
  Interrupted attempts retain audit/test claims and artifacts; paused/awaiting remain dormant.
  Boot/API wiring and resume/answer handling remain 31–33.
- Domain tool calls must use `ToolBoundary` with the entered B6 client and unchanged allowed
  context. Heavy budget is SQL-audited admission; admitted concurrent fits may overshoot.
  Explicit controls drain calls; ordinary cancellation retains identity. No thread preemption.
- Pass declared spine and entered tool registry to `load_run_skill`, then `skill.allowed`
  unchanged to `CallContext`. Snapshot bytes/hash are immutable; manifest integration is 29.
  User-authorized modelling policy now fills all 26 Heuristics/Pitfalls sections (D12);
  all role defaults use V4.1-Flash (D16). [Policy/model receipt](./receipts/modelling-skill-2026-10-04.md):
  18 focused skill checks passed; full acceptance timed out; further tests stopped by user.
  B9/B10 provide callable nodes 1–11; this policy adjunct remains separate evidence.
- Initialize one immutable `PromptContext` from project memory. `remember` is append-only;
  later writes affect future contexts/runs. Q&A routing uses current node role.
- Startup must validate selected role models and explicit skill model literals and preserve
  effective runtime credentials. The isolated SDK check fails closed for providers with no
  SDK-established credential requirements; additional provider support needs a future decision.
- Reuse one shared B6 LLM client/semaphore: one repair, 60-second timeout, no hidden retry.
  Event bus remains one writer per run/process; no multi-process coordination added.

## Deferred responsibilities

| Responsibility | Owner |
|---|---|
| Full domain assembly with champion/persistence executors | 29 |
| Final reporting, skill hash manifest and eventual automatic memory extraction | 29 |
| Control handlers, resume, gate answers and callable recovery API boot wiring | 30–33 |
| Remaining table repositories | First real caller's chunk |

## References on demand

- [Verified environment and dependency pitfalls](./ENVIRONMENT.md): search affected dependency only.
- [Historical workflow](./WORKFLOW_HISTORY.md): old counts/exceptions are historical; root AGENTS wins.
- [Codex setup and audit](./CODEX_WORKFLOW.md): human guide; not required startup context.
- [Revision audit trail](./REVISIONS_IMPLEMENTED.md): historical, not another gate checklist.
