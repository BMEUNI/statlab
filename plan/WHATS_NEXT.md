# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**26 of 41 chunks complete, plus revisions 09r and 20r. B8 accepted and committed 2026-10-04.**
B7 committed at `1e9fe6a`; B6 committed at `de2aa83`. Phases A/B and M7b complete.

Accepted B8: **2428 passed, one designed skip; 100% line/branch; full mypy/Ruff/format exit 0**.
Full pytest time: 98.83 s. [Durable B8 receipt](./receipts/B8.md) records exact dirty product
hashes on `1e9fe6a`, focused handoffs, review corrections and both acceptance attempts.
B6 client/LLM/lineage/semaphore/mutation/replay evidence remains accepted; no dependency or
migration change. Docs/status edits preserve product evidence; no startup baseline.

B7 plus B8 prove M8a and M8b, including shared inference overlapping real candidate execution,
three identical split consumers, and exactly one selected champion test score. Routing/skills
are M9 prerequisites; concrete domain decisions/full agent execution remain B9–B11.

## Next: batch B9 (chunks 27a nodes 1–4 + 27b nodes 5–6)

Draft bounded 27a from PLAN's normative spine and relevant D12/D16/D27/D32/D33d/D34d/D35
decisions: intake, problem definition, profiling, splitting and typed decision schemas.
Reuse accepted tools/clients, declared engine, role routing, skill policy and memory context.
After its terminal focused handoff, detail 27b's experiment design and inference, registry/family
cap, contract row-drop consistency and initial candidate shortlist integration with the B8 engine.
Keep dependent stages sequential. Main owns architecture/review/acceptance; implementers own
source/tests. Reuse the committed B8 receipt and exact accepted product inputs.

## Accepted seams and next-stage hazards

- `RunState.branches`, `TraversalPlan`, `RunLoop`, `NodeInvocation` and `ToolBoundary` are
  generic; domain code supplies spine/overlap/design/training/evaluation declarations and
  callbacks. Initial shortlist is caller-supplied; concrete domain assembly belongs to B9/B10.
  Shared inference receives no candidate-local progress; completed siblings merge durably.
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
  This adjunct task does not complete any B9 domain implementation.
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
| Estimator registry, ≤2 per family, `max_candidates`, row-drop contract consistency | 27b |
| Concrete candidate executors and initial shortlist assembly | 27b / 28 |
| Final reporting, skill hash manifest and eventual automatic memory extraction | 29 |
| Control handlers, resume, gate answers and callable recovery API boot wiring | 30–33 |
| Remaining table repositories | First real caller's chunk |

## References on demand

- [Verified environment and dependency pitfalls](./ENVIRONMENT.md): search affected dependency only.
- [Historical workflow](./WORKFLOW_HISTORY.md): old counts/exceptions are historical; root AGENTS wins.
- [Codex setup and audit](./CODEX_WORKFLOW.md): human guide; not required startup context.
- [Revision audit trail](./REVISIONS_IMPLEMENTED.md): historical, not another gate checklist.
