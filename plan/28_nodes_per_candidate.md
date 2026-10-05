---
status: implemented
dependencies: [11, 12, 13, 14, 17, 18, 27b]
completion_date: 2026-10-04
implementation_notes: "B10 implemented/reviewed from 31bdf67; static-only acceptance: mypy 146 sources, Ruff/format 148 files, diff checks exit 0. Sixteen offline test definitions authored, unexecuted under user test stop. Retune conclusion now observes current validation; requested curves/PDP retain plots. Runtime and coverage acceptance outstanding; durable receipt plan/receipts/B10.md."
---

# Chunk 28 — Candidate nodes 7–11

## Goal

Provide callable predictive executors for the five candidate positions in the declared tabular
spine, plus bounded retune and later-wave adapters. Every candidate uses the same split; all
learned transformations fit on train and replay on validation. No candidate accesses test.

## Prerequisites and reuse

- Architecture authority: PLAN §4 normative spine and §4.2; D12, D17, D26–D29, D32–D34, D36.
- Reuse B9 `DomainRuntime`, `ProblemSpec`, approved `CandidateSet`, shared split evidence,
  `FoundationExecutor`/`DesignInferenceExecutor`, and `TABULAR_TRAVERSAL`.
- Reuse B6 entered MCP/LLM clients, typed tools, unchanged skill allow-list, audited tool
  boundary, cancellation, lineage locks and single-repair behavior. Reuse B7/B8 engine hooks,
  deterministic candidate IDs, checkpoint merging and SQL fit budget. Do not duplicate them.
- Existing Pydantic `CandidateSet` already guarantees one baseline, unique hashes/labels and
  truthful variation dimensions. Fit/apply tools already protect targets and fitted state.
- Search ENVIRONMENT only for touched Pydantic/MCP/encoding/training dependencies; no upgrades.

## Owned files

Implementation writer exclusively owns:

- `backend/statlab/domains/candidate_pipeline.py` (new): nodes 7–8 and durable pipeline readers.
- `backend/statlab/domains/candidate_models.py` (new): nodes 9–11 and durable retune adapter.
- `backend/statlab/domains/candidate_search.py` (new): later-wave planner.
- `backend/statlab/domains/decisions.py`: concrete frozen typed decisions for nodes 7–11/search.
- `backend/statlab/domains/runtime.py`: narrow candidate invocation support and prompt evidence.
- `backend/statlab/domains/design_inference.py`: reuse/expose shortlist validation; repeated
  design must confirm checkpointed cumulative specifications rather than replace branches.
- `backend/statlab/domains/tabular.py`: concrete decision schemas for positions 7–11 only.
- `mcp_server/statlab_tools/tools/cleaning.py`: metadata-only allow-in addition for
  `drop_columns` at feature engineering (the current selector tools require this replay).
- `skills/modelling.md`: add `drop_columns` to feature-engineering Tools only.
- `tests/test_nodes_candidate_pipeline.py`, `tests/test_nodes_candidate_models.py`,
  `tests/test_nodes_candidate_search.py` (new), and minimal affected assertions in
  `tests/test_nodes_foundation.py`, `tests/test_nodes_design_inference.py`,
  `tests/test_tools_cleaning_fit.py`, `tests/test_candidate_fanout.py`, `tests/test_registry.py`.

Main owns this spec, index/status and receipt. No other source, schema, config, dependency,
engine, repository, report or API edits without a reported concrete blocker.

## Frozen behavior and API

### Invocation and evidence

- Executors are async callable adapters of `NodeInvocation -> NodeResult`, using the existing
  runtime; caller composes them with shared executors. No whole-agent bootstrap here.
- Runtime accepts shared calls without a candidate and candidate calls only when run, node,
  execution candidate database ID, and the checkpointed branch agree. CallContext has no
  candidate field: identity is carried by the persisted execution, not a new MCP API.
- Prompts include immutable approved candidate spec, its prior successful boundaries/retunes,
  shared problem/split evidence and relevant sibling validation evidence. Never disclose secrets.
- Read latest successful candidate boundaries, including retunes; absence/malformed upstream
  state is a package error. Skipped optional stages preserve their input frames explicitly in
  the reader fallback; do not consume partial failed artifacts.
- Each successful pipeline summary records train/valid refs, ordered replay steps (tool and
  frozen JSON parameters/fitted refs), feature manifest and approved methodology. This is the
  durable input for champion test transforms and later persistence; test refs are absent.

### Preprocessing and features

- Follow approved CandidateSpec; decisions explain execution and exceptions, never silently
  alter approved preprocessing/features/estimator identity. Contract feature exclusions/IDs
  and include restrictions apply equally to both frames, keeping target intact.
- Node 7 decides constant/high-missing drops on train; replay exact dropped names on valid.
  Fit imputer and optional clipper on train, apply fitted objects to train/valid. Candidate
  `drop_rows_above` is shared pre-split configuration; never invoke row drops/casts here.
- Node 8 applies deterministic declared datetime parts/interactions to both frames, train-fit
  binning, chosen encoding and scaling, then train-only selection with exact validation drops.
  Use ordinal levels from contract. Target encoding uses its returned OOF **train dataframe**;
  apply the fitted encoder only to validation. Unsupported combinations fail honestly.
- Preserve indices/target and fitted refs through existing tools; no dataframe/compute imports.
  Do not invent ratio features or missing indicators absent from approved FeatureSpec.
- `drop_columns` replay is already supported server behavior; add only its node authorization
  and matching skill declaration. Do not broaden imputation/clipper fit permissions.

### Training, evaluation, interpretation

- Node 9 uses the declared estimator/class weight/calibration. Typed decision chooses direct
  fit or bounded grid, never another family. Tune on prepared train with problem metric;
  tuning returns params, then train_estimator fits/calibrates on train. Respect contract seed,
  CV and tool/batch budgets; do not override n_jobs/random_state or quote prepared-frame CV as
  leakage-free end-to-end CV. Persist model ref, effective params and tuning caveat/history.
- Node 10 predicts/scores **valid only**, with seeded existing score tool bootstrap CIs.
  Report primary metric and direction, selection bias, wave/retune information. Optional
  task-compatible confusion/ROC/PR/calibration curves, threshold, train-only CV/learning curves
  use existing tools. Render requested supported curve artifacts through `plot_curve`, retaining
  curve and plot refs; confusion remains a table. Persist predictions/probabilities and selected
  threshold for node 12. Decide retuning after observing current validation metrics and diagnostics;
  a post-score conclusion cannot change already performed diagnostic/threshold actions.
  Require a finite available primary metric; regression metric unavailability is a real failure.
- Node 11 uses latest successful model/features/evaluation and validation permutation
  importance/errors; supported coefficient importance and PDP are optional. Predictive
  diagnostics only; render requested PDP curves with the existing plot tool. No repeated
  inferential fitting or test diagnostics. Skip only with reason.
- Guided candidate nodes use exception gating (D29), not a gate at every ordinary decision.
  Concrete warnings/failures, decision-declared material exceptions and invalid metric ranges
  may request approval through the existing callback. Do not invent a numerical expected-range
  policy from success_threshold. Preserve awaiting evidence; durable answer/resume UI is 31–33.

### Retunes and search

- Retune adapter derives its answer from the latest durable node-10 decision; generic engine
  enforces max_retune_loops/budget and rewinds only nodes 9–10. Next training sees previous
  validation and retune decision, uses original prepared train and never changes methodology.
- Planner wave 1 reuses approved initial shortlist. Later waves use the completed comparison
  and remaining limits to propose additional CandidateSpecs. Preserve all old specs exactly;
  validate the cumulative set, contract constraints, registry/family justification and dedupe.
  Empty proposal ends search. Use an existing persisted design execution for typed planning
  audit if needed; no fabricated NodeExecutionRow or side-channel mutable proposal cache.
- Engine persists proposed new branch specs/wave before repeated design; repeated node 5
  validates and approves that cumulative checkpointed set before any new branch executes.
  Restart reuses checkpointed specifications and does not replan saved waves. D29 guided
  shortlist gate remains shared. No generic engine changes unless an actual blocker is reported.

## Offline regression selectors and static checks

Author tests in the three new modules for frozen replay/OOF, candidate identity, branch isolation,
train-only fitting and validation scoring, latest retune model, task-compatible diagnostics,
exception approval, cancellation/domain errors and cumulative search/restart. Reuse existing
SDK/Pydantic/tool guarantees; no model_construct/validator bypass or unreachable negatives.
Affected regression modules are those named under ownership. Counts are estimates, never quotas.

**Current user override:** do not run pytest, collection, coverage, mutation/probes or live LLM
calls. Run scoped Ruff check/format and mypy, and `git diff --check`. Main performs one global
static acceptance pass once review is stable. Record tests as authored/unexecuted, never passed.

## Definition of done / stop conditions

Stop implementation at a terminal focused handoff: changed files, exact candidate hashes,
static commands, genuine exits/log paths, unexecuted selectors and concrete next-stage hazards.
No commits, status/plan rewrites, dependencies or speculative helpers by writer. Stop/report if
the existing tools/contracts cannot express approved behavior or an engine/repository change
is required. Main reviews once, sends consolidated reachable corrections, accepts statics,
records durable runtime limitations and commits locally. Nodes 12–13, report/pipeline export,
full-agent/API wiring and persistent gate handlers are outside B10.

## Review addendum — 2026-10-04

One consolidated correction round addressed two reachable gaps: the initial retune decision
preceded current validation scoring, and requested curve data lacked PLAN-required plots.
Separate `EvaluationActions` and `ValidationConclusion` now freeze actions before dispatch and
derive the durable retune decision afterward from current metrics/CIs, diagnostics, warnings
and limits. Existing `plot_curve` renders requested supported curves/learning/PDP artifacts;
threshold-only ROC stays unplotted and confusion remains a table. No new tool guards or
dependency changes. Reviewed terminal handoff and one global static acceptance; see
[B10 receipt](./receipts/B10.md). Runtime tests and coverage remain unexecuted.
