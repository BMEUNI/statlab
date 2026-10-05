# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**37 of 41 chunks implemented, plus revisions 09r and 20r. B14 reviewed with static-only acceptance 2026-10-05.**
B9–B14 runtime/coverage acceptance is outstanding under the user's test stop; B8 remains the last
batch with full behavioral/coverage acceptance. [B9 receipt](./receipts/B9.md),
[B10 receipt](./receipts/B10.md), [B11 receipt](./receipts/B11.md), [B12 receipt](./receipts/B12.md)
and [B13 receipt](./receipts/B13.md), [B14 receipt](./receipts/B14.md)
record this limit. B9 committed at `31bdf67`.
B7 committed at `1e9fe6a`; B6 committed at `de2aa83`. Phases A/B and M7b complete.

Accepted B8: **2428 passed, one designed skip; 100% line/branch; full mypy/Ruff/format exit 0**.
Full pytest time: 98.83 s. [Durable B8 receipt](./receipts/B8.md) records exact dirty product
hashes on `1e9fe6a`, focused handoffs, review corrections and both acceptance attempts.
B6 client/LLM/lineage/semaphore/mutation/replay evidence remains accepted; no dependency or
migration change. Docs/status edits preserve product evidence; no startup baseline.

B7 plus B8 prove M8a and M8b, including shared inference overlapping real candidate execution,
three identical split consumers, and exactly one selected champion test score. Routing/skills
are M9 prerequisites; B9–B11 domain/control behavior still needs runtime acceptance.

## Next: batch B15 (chunks37–38, setup flow then run workspace; unstarted)

Chunk35's [SSE implementation](./35_sse_stream.md) and chunk36's
[browser scaffold/launcher](./36_frontend_scaffold.md) are terminal and reviewed. Scoped statics,
frontend typecheck/build/Make dryrun and one final global Python static acceptance passed.
M12 runtime/replay proof remains outstanding. Backend implementation is complete; behavioral
acceptance remains limited by the test stop. B15 and B16 remain; no B15 spec or source work started.
Specify chunk37 only when authorized, using dependencies33/36; specify38 only after37's terminal
handoff (dependencies35/37). Preserve B13's fresh per-run
runtime/loop/lifecycle/GateService/ForkService, revision callback and tabular retained-report
publisher. API caller owns registry, execution/continuation tasks and shutdown drain; inbound
controls use real same-run audit_anchor. Gate reconciliation precedes restart scans. Retried
direct forks require durable request IDs; real service callbacks perform control actions.
Keep dependent stages sequential. Main owns all design decisions, detailed per-file recipes,
review and acceptance; main writes no code. Delegate only implementation-ready plans to the Luna/low
`implementer`. Missing decisions/failures return to main for a resolved correction plan. No Sol workers
or model escalation. Follow [role and plan requirements](../AGENTS.md#roles-plannerreviewer-and-mechanical-implementer).
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
  B12 supplies callable lifecycle, gate reconciliation and fork services. B13 supplies boot/API
  wiring, registry and continuation scheduling; all-project preparation precedes admission.
  Capacity-denied accepted gates/forks remain durable; retries do not duplicate children/tasks.
  Shutdown drains admitted HTTP and execution work before MCP/SQL close; behavior is runtime-unverified.
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
  audit anchor; it performs no engine lifecycle work. B12 supplies handlers/early anchors;
  API owns binding and delivery. Seven gate actions retain revisions; post-action approval
  acknowledges captured results without replay. Forks retain genuine ancestor IDs/artifact
  hardlinks, consolidate candidate rewinds into wave1, preserve tail histories and original
  champion score provenance. Partial unpublished children stay cancelled and diagnostic.
- Startup must validate selected role models and explicit skill model literals and preserve
  effective runtime credentials. The isolated SDK check fails closed for providers with no
  SDK-established credential requirements; additional provider support needs a future decision.
- Reuse one shared B6 LLM client/semaphore: one repair, 60-second timeout, no hidden retry.
  Event bus remains one writer per run/process; no multi-process coordination added.
- B14 keeps persisted envelope replay and atomic replay/live handoff, max(query,Last-Event-ID),
  total seq across candidates, shutdown-first SSE close and bounded prose-only deltas. Only
  validated final responses feed controls; attempt accounting remains authoritative. Supported API launcher is
  `python -m statlab.main`; bare Uvicorn lacks the early stream-close signal hook.
  Local dev drains UI, API, then MCP without force-kill; no runtime proof yet.
- Browser native EventSource owns reconnect; fatal parse/identity/sequence errors close the
  stream, cleanup invalidates late callbacks. `useRunEvents.lastEvent` is display-only, not a
  complete history; B15 workspace must consume every `connectRunEvents` callback. Scaffold
  has read-only projects/run inspection, no gate/lifecycle actions. Build emitted unsuppressed
  dependency `use client` directive warnings; browser-only entry has no Server Components.

## Deferred responsibilities

| Responsibility | Owner |
|---|---|
| Chat workspace | B15 / 38 |
| Remaining table repositories | First real caller's chunk |

## References on demand

- [Verified environment and dependency pitfalls](./ENVIRONMENT.md): search affected dependency only.
- [Historical workflow](./WORKFLOW_HISTORY.md): old counts/exceptions are historical; root AGENTS wins.
- [Codex setup and audit](./CODEX_WORKFLOW.md): human guide; not required startup context.
- [Revision audit trail](./REVISIONS_IMPLEMENTED.md): historical, not another gate checklist.
