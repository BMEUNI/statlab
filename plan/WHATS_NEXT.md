# What's next

Live state only. Workflow/validation authority: [AGENTS.md](../AGENTS.md).
Read relevant [batch/dependency rows](./00_index.md#execution-batches-from-chunk-12-decided-2026-10-01),
then active spec and named [PLAN](./PLAN.md) decisions. Do not read archives at startup.

## State

**37 of 41 chunks implemented, plus revisions 09r and 20r. B9–B14 offline runtime/coverage
accepted 2026-10-07: `make check` exit 0, 3006 passed, 1 designed skip, 100% line/branch.**
See [offline receipt](./receipts/B9-B14-offline.md) and [offline validation plan](./offline_runtime_validation.md).
Next validation step: the paid ONLINE runtime run (needs explicit user go-ahead and API credits).
Browser smoke (M12) remains deferred. Earlier static-only receipts: [B9 receipt](./receipts/B9.md),
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

## Next: user's online test of chunks 46-51, then the next phase

Chunks 48-51 accepted 2026-10-08: profiling question lists numeric/non-numeric columns (48),
chat titles search waves (49), threaded random-forest final fit and a third of the cores for
tool loops (50), chat retries transient provider errors and shows errors cleanly (51).
Open: Q&A prompt is ~151k tokens per question (whole state summary); trim candidate.

Chunk [47](./47_polish_help_report_cost.md) accepted 2026-10-08: light run header, close icon
inside the step box, navbar help menu from the registry, champion tiles, full-width justified
report with a two-column plot grid, Flash cost priced by call time (off-peak half). Open: live
multiclass (S12) not re-run since `694eefb`; offline-rich S01 stalls at experiment_design.

Chunk [46](./46_calm_run_screen.md) accepted 2026-10-08: per-candidate exception gates ask once
per attempt, pending gates are grouped per step in one chat decision box (approve all or per
model), the chat no longer force-scrolls, chat renders markdown/JSON, step messages are one
sentence plus "More", the setup card can add inference questions, lanes show validation scores.
The user runs online tests themselves; no paid run without their fresh go-ahead. Real cost per
run is still unmeasured. Open idea: thread random-forest fits and CV/learning-curve diagnostics.
Restart `make dev` to pick up the changes.

## Earlier: paid ONLINE runtime run (needs fresh explicit user go-ahead)

B15–B17 accepted 2026-10-08 (frontend 36b–41). The main screen is now a chat (`#/`): an LLM
setup assistant (`POST /api/assistant/turn`, role `setup_assistant` on the default model) asks
for project, dataset, target and mode with inline widgets; runs open as chat (left) plus
progress timeline, gates, candidates, files and report (right). Projects & runs browser lives
at `#/projects`. See [41 spec](./41_chat_first_ui.md) receipt.
Next: the paid online run, which also exercises the real setup assistant. DeepSeek balance was
about $0.78; check readiness first, then ask the user to confirm and top up. Offline stack:
`.venv/bin/python -m integration.serve_ui` + `STATLAB_API_ORIGIN=http://127.0.0.1:8765 make ui`.
No paid run without fresh explicit user confirmation.
User authorizes automatic local commits after finished work; never push. Main writes no code;
delegate resolved plans to the pinned `implementer` only.

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
