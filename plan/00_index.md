---

status: in_progress
dependencies: []
completion_date: null
implementation_notes: ""
---

# Execution Plan — Chunk Index

Start with [WHATS_NEXT.md](./WHATS_NEXT.md) for live state. Workflow/validation: root [AGENTS.md](../AGENTS.md).

Master architecture: [`PLAN.md`](./PLAN.md) (36 decisions D1–D36, 21 resolved questions).
This directory decomposes it into **41 atomic, topologically ordered chunks** (40 originally; chunk 27 was split into 27a/27b in the 2026-10-01 review).

## Hard rules for the implementing agent

1. **Sequential dependency-ready stages.** Follow the execution-batch table below, not numeric
   order. Reuse only available prerequisite outputs; pass the prior stage's focused report forward.
2. **Validation authority: [AGENTS.md](../AGENTS.md#validation-policy).** Implementers use focused
   tests and targeted static checks. The main agent reviews stage handoffs, then runs one final
   full suite WITH coverage plus full static checks per stable complete batch. Intermediate stages
   do not run global gates. 100% line/branch coverage remains required at acceptance. Completed
   chunk DoDs/review receipts are historical; their commands do not prescribe today's loop.
3. **Minimal viable code.** No speculative abstraction, no boilerplate, no "might need later".
   If a class has one implementation and one caller, it is a function. Delete over add.
4. **Strict typing.** `mypy --strict` clean. No `Any`, no bare `dict`/`list`, no implicit
   `Optional`. No new type ignores; preserve only the existing justified exceptions.
5. **Errors wrap, never leak.** Every boundary (filesystem, HTTP, subprocess, LLM, SQL) maps
   foreign exceptions to a domain error defined for that package: `statlab.errors` in `backend/`,
   the local `Tool*Error` classes in `mcp_server/` (which must never import `statlab.errors` —
   D13, chunk 06 correction). No raw `Exception` escapes a module.
6. **PLAN.md wins.** On any conflict between a chunk file and `PLAN.md`, `PLAN.md` is
   authoritative — in particular §4's normative 13-node spine table.
7. **Main agent owns status.** Implementers report focused results, log paths, exit codes, risks,
   and next-stage hazards. The main agent records evidence, updates frontmatter/index/WHATS_NEXT,
   and marks completed only after final acceptance. Docs/status edits preserve product evidence.

## Verified environment (resolved by chunk 01, 2026-09-09)

Detailed package facts, pitfalls, and prior probe results moved intact to [ENVIRONMENT.md](./ENVIRONMENT.md).
Search that reference for the dependency you touch; no startup full read or re-probe.

## Execution batches (from chunk 12, decided 2026-10-01)

Chunks 01–11 ran one per cycle. The remaining 30 run in **16 batches**. Follow root AGENTS workflow:
one writer, dependency-ordered stages, one consolidated batch review and one stable acceptance gate.
Bound each stage before implementation; write a dependent stage's detailed spec after its prerequisite
handoff. Intermediate review checks dependency/API blockers. Commit only when authorized; specs use
document checks, not product gates. Batch scope and behavior requirements remain unchanged.

| Batch | Chunks | Closes |
|---|---|---|
| B1 | 12 encoding + 13 training | M4 |
| B2 | 14 evaluation + 15 inference | M5 ✅ |
| B3 | 16 walking-skeleton gate (alone: it is the gate) | M6 ✅ |
| B4 | 17 features + 18 interpretation | M7a ✅ |
| B5 | 19 champion/reporting + 20 reproducibility | M7b ✅ for the `pipeline.py` gate; chunk 20r closed the scope gap: `ridge` and `mnlogit` (`fit_mnlogit`) shipped, `split_temporal`/`split_grouped` deferred (D-c), **83 served tools (v1)** |
| B6 | 21 mcp_client + 25 llm_client | completed; full acceptance 2026-10-03; committed |
| B7 | 22 engine core + 23 restart reconciliation | completed; accepted and committed 2026-10-04; M8a core/restart proved, fan-out proof accepted in B8 |
| B8 | 24 candidate fan-out + 26 routing/skills | completed; accepted and committed 2026-10-04; M8a / M8b ✅; [receipt](./receipts/B8.md) |
| B9 | 27a nodes 1–4 + 27b nodes 5–6 | implemented/reviewed; static 2026-10-04; offline runtime/coverage accepted 2026-10-07 (3006 passed, 100%; [offline receipt](./receipts/B9-B14-offline.md)); [receipt](./receipts/B9.md) |
| B10 | 28 nodes 7–11 (alone: the largest) | implemented/reviewed 2026-10-04; offline runtime/coverage accepted 2026-10-07 (3006 passed, 100%; [offline receipt](./receipts/B9-B14-offline.md)); [receipt](./receipts/B10.md) |
| B11 | 29 nodes 12–13 + 30 control router | implemented/reviewed; static 2026-10-05; offline runtime/coverage accepted 2026-10-07 (3006 passed, 100%; [offline receipt](./receipts/B9-B14-offline.md)); [receipt](./receipts/B11.md) |
| B12 | 31 recovery/lifecycle + 32 gates/rewind | M10, M11; offline runtime/coverage accepted 2026-10-07 (3006 passed, 100%; [offline receipt](./receipts/B9-B14-offline.md)) |
| B13 | 33 API projects + 34 API runs | implemented/reviewed; static 2026-10-05; offline runtime/coverage accepted 2026-10-07 (3006 passed, 100%; [offline receipt](./receipts/B9-B14-offline.md)); [receipt](./receipts/B13.md) |
| B14 | 35 SSE + 36 frontend scaffold | implemented/reviewed; static 2026-10-05; frontend typecheck/build passed; offline runtime/coverage accepted 2026-10-07 (3006 passed, 100%; [offline receipt](./receipts/B9-B14-offline.md)); M12 browser smoke still deferred; [receipt](./receipts/B14.md) |
| B15 | 36b read API + 37 setup flow + 38 run workspace (user requirement 2026-10-08: every backend feature usable in the UI) | implemented 2026-10-08 |
| B16 | 39 comparison + 40 report | M13 |
| B17 | 41 chat-first app (user direction 2026-10-08) | completed 2026-10-08 (make check 3091 passed, 100%; npm test 71/71; browser acceptance) |
| B18 | 42 speed, talkative chat, gate forms, blue redesign (user feedback 2026-10-08) | completed 2026-10-08 (make check 3104 passed, 100%; npm test 77/77) |
| B19 | 43 cheaper runs + UI polish (user feedback 2026-10-08) | completed 2026-10-08 (make check 3117 passed, 100%; npm test 79/79; browser acceptance) |
| B20 | 44 chat setting changes (user ask 2026-10-08) | completed 2026-10-08 (make check 3125 passed, 100%; npm test 80/80; offline browser acceptance) |

## Phase A — Foundation (01–06)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 01 | [`01_foundation.md`](./01_foundation.md) — repo skeleton, pinned deps (Task 0), config, Makefile | completed | — |
| 02 | [`02_core_types.md`](./02_core_types.md) — `ToolResult` envelope, `error_class` taxonomy + retry budget, `ArtifactRef`/`ArtifactKind`, `NodeSpec`, `NodeResult`, `GatePolicy`, structured error hierarchy. **Introduces `contracts/statlab_contracts/`** — a third dependency-free package for wire types, so the envelope cannot drift between the two processes | completed | 01 |
| 03 | [`03_data_contract.md`](./03_data_contract.md) — `DataContract` Pydantic tree (§4.1): 10 models, 9 closed vocabularies, 28 consistency rules, JSON round-trip. Lives in `contracts/` (it crosses the process boundary), **not** `backend/` as §3.2 says. Validation against a real *frame* is chunk 08's `validate_contract` tool — this chunk validates the contract against itself | completed | 02 |
| 04 | [`04_candidate_spec.md`](./04_candidate_spec.md) — `CandidateSpec` + `PreprocessingSpec`/`FeatureSpec`/`EstimatorSpec`/`FeatureSelection`, canonical spec-hash with a drift guard, `changed_dimensions` verifying `varies_from_baseline` against the real diff, and `CandidateSet` with 7 shortlist rules. Family cap and registry checks defer to chunk 27b (they need the registry) | completed | 02, 03 |
| 05 | [`05_persistence_sqlite.md`](./05_persistence_sqlite.md) — all 15 tables (§10.2), WAL + `busy_timeout` + `foreign_keys` with a read-back check, forward-only migration runner, `BEGIN IMMEDIATE` transactions, and repositories for the project/dataset/contract/run spine only. The other 11 tables get repositories in the chunk that first writes to them | completed | 01, 02 |
| 06 | [`06_artifact_store.md`](./06_artifact_store.md) — run-folder layout (§10.1) as two frozen dataclasses in `statlab_contracts`, artifacts repository + `pruned_at`, hard-link fork copy (D11), prune-on-success with a status guard (D24), and parquet/joblib/png/json IO in `statlab_tools` — the only module that imports pandas. Spans all three packages by design. No `locked` flag: chunk 05's `runs.test_scored_at` supersedes it | completed | 01, 02, 05 |

## Phase B — MCP server (07–20)

The entire tool layer is built and tested **before any agent code exists** (D22).

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 07 | [`07_mcp_server_foundation.md`](./07_mcp_server_foundation.md) — Streamable HTTP app, static-bearer Starlette middleware, unauthenticated `/health` route, the `@tool` decorator (registration, `meta` for D12, mandatory docstring + parameter descriptions enforced at import, envelope, exception classification, timing, `heavy=` semaphore), and `list_artifacts` / `describe_artifact`. Milestone **M1** | completed | 01, 02, 06 |
| 08 | [`08_tools_intake.md`](./08_tools_intake.md) — `load_csv` (takes the upload's sha256, not a path: traversal unrepresentable by schema), `infer_column_roles`, `validate_contract` (reads the run's contract snapshot; reports every mismatch as a finding), `detect_id_columns`, `detect_leaky_columns`. Widens the pandas boundary rule to `mcp_server/` with a test over `backend/` + `contracts/`. Milestone **M2** | completed | 03, 06, 07 |
| 09 | [`09_tools_profiling.md`](./09_tools_profiling.md) — 9 profiling tools; shared `_frame.py` helpers (`json_scalar`: np.int64 fails the envelope, NaN must not reach JSON); `save_json` made strict; plots via pyplot-free `Figure` with `parse_math=False` (bodies now run in threads) Milestone **M3** (profiling half; completed by chunk 10) | completed | 06, 07, 08 |
| 09r | [`REVISIONS_IMPLEMENTED.md`](./REVISIONS_IMPLEMENTED.md) — revision batch from the v1.8 review: R1-R3 intake contract/leak fixes, R4 schema and wire fields for the D32 lineage lock, R5-R7 small fixes | completed | 08, 09 |
| 10 | [`10_tools_splitting.md`](./10_tools_splitting.md) — `split_random`, `split_stratified` and `verify_split`'s 4 checks (temporal/grouped follow at M7). Writes split roles so the lineage lock has something typed to key on, and the test fingerprint as a content digest (D-c: no dataset identity needed, matches on same-rows-same-frame). Small-split guard (D33c) refuses before writing. Null-target rows dropped by the split tools (D34d). Temporal/grouped stay deferred, and an unavailable strategy escalates rather than falling back. Completes **M3** (completed): `verify_split` catches a deliberately overlapping split (and, via P3, a relabelled duplicate). | completed | 07, 08, 09 |
| 11 | [`11_tools_cleaning.md`](./11_tools_cleaning.md) — 10 tools (adds `drop_high_missing_rows`); `fit_*`/`apply_*` separation; pre-split row drops and `cast_dtypes` in node 4 (D34d); imputation `drop` drops the column, never rows. Fitted state is a frozen dataclass (`FittedImputer`/`FittedClipper`) pickled as kind `transformer`, no sklearn. Core leak test (train median 10, valid median 99; same for clipper bounds) proves `apply_*` replays train-learned state. Target never imputed/clipped/dropped (D-e); `apply_*` works with the target present or absent. No `reset_index` (labels/order preserved). Completes nothing by itself; M4 is finished by chunk 12 (encoding). | completed | 07, 08 |
| 12 | [`12_tools_encoding.md`](./12_tools_encoding.md) — 8 tools; one-hot / ordinal / target / scaler, all fit+apply. `fit_target_encoder` returns OOF-encoded train (D34c). The three shared helpers + `as_str` moved to `_frame.py` (public, `load_fitted` generic over `[T]`); sklearn imported lazily so the scipy-free spearman invariant holds. **Completes M4.** | completed | 07, 11 |
| 13 | [`13_tools_training.md`](./13_tools_training.md) — `ESTIMATORS` registry (6 estimators, D25), `train_estimator` (calibration by CV on train, D34b), `tune_estimator` (CV within train, `max_fit_seconds` enforced in the body, D34a/D36c), `predict`, `predict_proba`; explicit `seed`, `n_jobs=1` (D34e) | completed | 07, 12 |
| 14 | [`14_tools_evaluation.md`](./14_tools_evaluation.md) — 11 tools **plus `rank_models` and `compare_models`** (pulled forward so the chunk 16 gate is self-contained). Every `score_*` reports a bootstrap 95 % CI (D33b) | completed | 07, 13 |
| 15 | [`15_tools_inference.md`](./15_tools_inference.md) — `SPECS` registry, `build_dummy_matrix`, `assemble_inference_matrix`, statsmodels fits incl. Poisson GLM for count targets, coefficient table with confirmatory/exploratory labels and Holm adjustment (D35), VIF, residual/influence tests + known-answer test. **Moved before the skeleton gate** (review 2026-10-01): the skeleton fits an inferential model | completed | 07, 12 |
| 16 | [`16_skeleton_integration.md`](./16_skeleton_integration.md) — **keystone gate**: the 33-tool walking skeleton (§6.4) driven end to end with zero LLM, on a classification *and* a regression fixture. Milestone **M6** | completed | 08–15 |
| 17 | [`17_tools_features.md`](./17_tools_features.md) — 7 feature-construction and selection tools (`bin_numeric` split into `fit_binner`/`apply_binner`) and the `transform_only` tool flag | completed | 07, 12 |
| 18 | [`18_tools_interpretation.md`](./18_tools_interpretation.md) — permutation importance, PDP compute (rendered by `plot_curve`), coefficient importance, error analysis | completed | 07, 13, 14 |
| 19 | [`19_tools_champion_reporting.md`](./19_tools_champion_reporting.md) — `select_champion`, `render_metrics_table`, `assemble_report` | completed | 14, 15, 18 |
| 20 | [`20_tools_reproducibility.md`](./20_tools_reproducibility.md) — `export_pipeline_script` (dependency slice of the champion's test score), `snapshot_run` (manifest incl. `run_hash`); `compute_run_hash` and `prune_intermediate_artifacts` removed (D-a) | completed | 06, 19 |
| 20r | [`20r_ridge_mnlogit.md`](./20r_ridge_mnlogit.md) — closes the M7 scope gap (`PLAN.md` §6.4): `ridge` estimator registry line, `mnlogit` (`SPECS` + `fit_mnlogit` + multinomial `coefficient_table`); `split_temporal`/`split_grouped` deferred (D-c). 83 served tools (v1) | completed | 13, 15, 20 |

## Phase C — Orchestration (21–26)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 21 | [`21_mcp_client.md`](./21_mcp_client.md) — tool discovery + schema cache, allow-list enforcement (D12), `ArtifactRef` resolution, injection of `project`/`run`/`dataset`/`seed` (split tools' `seed` comes from `split.seed`, every other tool's from `constraints.random_seed`), recording the split tool's `test_fingerprint` via `set_test_fingerprint`, **a parameter named `train` never receives a test-derived artifact** (covers every `fit_*` and the train-only deciders of chunk 11), **test-set lineage lock** (D32a–b: `derived_from_test` propagation, rooted at a split tool's output with `meta.split == "test"`; `verify_split` is the one pre-node-12 tool allowed a test input, `fit_*` never on test-derived, transform-only flow in node 12 keyed on each tool's `meta.transform_only` (chunk 17 D-a), one `score_*` via `test_scored_at`; the `fit_*`/`train` clause and the one-score count hold in **every** node, and after node 12 read-only tools such as `describe_artifact` may receive test-derived refs (chunk 16 review); in node 12, after the one score, `reporting`-phase tools may read test-derived tables and `select_champion` never may (chunk 19 D-d)), schema validation before dispatch, optional one-argument repair, error mapping; **record a `LoggedCall` (tool, arguments as sent, output refs) per call** for chunk 20's tools. Keep every dispatched scoring claim on failure; minimal node-execution/tool-call repositories. **Acceptance:** `tests/test_skeleton.py` re-driven through `mcp_client`, with the driver's injection and rule checks deleted in favour of the client's. The actual spine additionally calls served `select_champion` before test replay; this is a prerequisite in the flow, not a change to the normative `SKELETON_TOOLS` 33-tool set. | completed | 02, 05, 06, 07 |
| 22 | [`22_graph_engine_core.md`](./22_graph_engine_core.md) — `RunState`, atomic checkpointer, durable event bus/replay, declared `NodeSpec` traversal, one-candidate engine and D24 prune after completed boundary | completed | 02, 04, 05, 06, 21 |
| 23 | [`23_restart_reconciliation.md`](./23_restart_reconciliation.md) — boot status scan, same-run boundary resume, interrupted-attempt audit, dormant gates and crash-window reconciliation (§5.1) | completed | 22 |
| 24 | [`24_candidate_fanout.md`](./24_candidate_fanout.md) — parallel candidate execution, shared-split guarantee, test-lock integration, budget caps, search waves, pause/cancel at tool-call boundaries (D36c) | completed | 06, 21, 22, 23 |
| 25 | [`25_llm_client.md`](./25_llm_client.md) — LiteLLM wrapper, 3-tier structured output with capability detection, one repair retry for every tier, offline import, prefix capability overrides, thinking toggle, in-flight semaphore, per-attempt token/cached-cost logging | completed | 01, 02, 05, 21 |
| 26 | [`26_routing_and_skills.md`](./26_routing_and_skills.md) — `Role`→model routing with 4-level precedence (§7.3), skill-file parser + startup validation against the declared spine and each tool's `allow_in` (§9.2), invariant-first prompt assembly, **project memory repository + read path and the `remember` action** (D36d; previously unowned) | completed | 05, 21, 22, 25 |

## Phase D — Nodes (27–29)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 27a | [`27a_nodes_shared_foundation.md`](./27a_nodes_shared_foundation.md) — nodes 1–4: intake, problem_definition (incl. the PR-AUC default under imbalance, D33d, and pre-registered inference goals, D35), data_profiling, data_splitting + their decision schemas | completed (static only; tests stopped) | 08, 09, 10, 22, 24, 26 |
| 27b | [`27b_nodes_design_inference.md`](./27b_nodes_design_inference.md) — nodes 5–6: experiment_design (registry check, ≤2-per-family cap, `max_candidates`, chunk 04's deferred checks; reject a candidate whose `preprocessing.missing.drop_rows_above` differs from the contract's, since row drops are shared and pre-split, D34d) and inferential_analysis (D31, D35) + their decision schemas. Split from 27 in review: six nodes in one chunk was the largest chunk in the plan | completed (static only; tests stopped) | 13, 15, 27a |
| 28 | `28_nodes_per_candidate.md` — nodes 7–11: preprocessing, feature_engineering, model_training, model_evaluation, model_interpretation | implemented (static-only; runtime/coverage outstanding) | 11, 12, 13, 14, 17, 18, 27b |
| 29 | `29_nodes_champion.md` — nodes 12–13: champion_selection (selection-bias labelling, CIs, cross-run test-exposure disclosure — D32c/D33; `select_champion` is called once and the backend refuses a second call once test is scored; `assemble_report` gets `prior_test_scorings` (`count_prior_test_scorings`) and `n_waves`; node 13 passes the run's tool log as `LoggedCall`s to `export_pipeline_script` (with `target` = the champion's test score table) and `snapshot_run`, and adds the LLM model name to the manifest; scoring test = replay the champion's node 7–8 calls whose tool is `transform_only`, in order, with the same non-frame arguments and `frame=test`, then predict and one `score_*` — chunk 17 D-a) and artifact_persistence | implemented (static-only; runtime/coverage outstanding) | 19, 20, 28 |

## Phase E — Control layer (30–32)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 30 | `30_control_router.md` — interrupt router classifier, out-of-band Q&A, status explainer | implemented (static-only; runtime/coverage outstanding) | 22, 26 |
| 31 | `31_control_recovery.md` — recovery/retry by `error_class`, cancel/pause/resume lifecycle; verified ancestor artifact/producer audit | implemented; static-only B12, runtime deferred | 22, 30 |
| 32 | `32_control_gates_rewind.md` — persistent HITL gates (7 actions), guided policy, drained fork rewind and inherited score provenance | implemented; static-only B12, runtime deferred | 24, 31 |

## Phase F — HTTP API (33–35)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 33 | `33_api_projects.md` — projects, dataset upload (≤200 MB, stored as `datasets/<sha256>.csv`, chunk 08 D-a), contract upload/validate/propose | implemented; static-only B13, runtime/coverage outstanding | 03, 05, 06 |
| 34 | `34_api_runs.md` — run creation, state snapshot, messages, gate answers, control actions (incl. rewind → new run id) | implemented; static-only B13, runtime/coverage outstanding | 22, 32, 33 |
| 35 | [`35_sse_stream.md`](./35_sse_stream.md) — SSE endpoint, `?since=` replay from `events.jsonl`, 15 s heartbeat, token-delta streaming for prose only | implemented (static only; tests stopped) | 22, 34 |

## Phase G — Frontend (36–44)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 36 | [`36_frontend_scaffold.md`](./36_frontend_scaffold.md) — Vite + TS strict, typed API client, `EventSource` hook with `seq` tracking and reconnect | implemented (static only; tests stopped) | 35 |
| 36b | [`36b_api_read.md`](./36b_api_read.md) — read-only API: datasets/contracts lists, executions + tool/LLM calls + cost, artifacts list/download/preview, run files and report plots | implemented 2026-10-08 (make check 3069 passed, 100%) | 33, 34, 35 |
| 37 | [`37_frontend_setup_flow.md`](./37_frontend_setup_flow.md) — project picker, upload wizard, contract editor (columns/schema form/JSON), structural + data check, mode selection | implemented 2026-10-08 (make check 3074 passed, 100%; browser click-through) | 33, 36, 36b |
| 38 | [`38_frontend_run_workspace.md`](./38_frontend_run_workspace.md) — step table (shared + candidate columns), step inspector with attempts/tool/agent calls and cost, chat with streamed replies, gate panels for all seven actions with edit check, pause/resume/stop/rewind | implemented 2026-10-08 (make check 3075 passed, 100%; browser acceptance) | 35, 37 |
| 39 | [`39_frontend_comparison.md`](./39_frontend_comparison.md) — candidate comparison table sortable by metric with `varies_from_baseline`, file browser with preview/download/D32 lock, fork-aware artifact listing | implemented 2026-10-08 (make check 3076 passed, 100%; browser acceptance) | 38 |
| 40 | [`40_frontend_report.md`](./40_frontend_report.md) — report viewer (in-repo safe markdown subset, no raw HTML), champion card, downloads, run lineage tree and multi-run comparison | implemented 2026-10-08 (npm test 63/63; report browser-checked) | 39 |
| 41 | [`41_chat_first_ui.md`](./41_chat_first_ui.md) — chat-first home: LLM setup assistant with inline widgets, run workspace as chat + progress timeline with candidate lanes, calm light redesign; projects browser at `#/projects` | completed 2026-10-08 (make check 3091 passed, 100%; npm test 71/71; browser acceptance) | 40 |
| 45 | [`45_boot_old_gates.md`](./45_boot_old_gates.md) — boot no longer fails on applied gates stored with an older decision schema | completed 2026-10-08 (make check 3126 passed, 100%) | 44 |
| 46 | [`46_calm_run_screen.md`](./46_calm_run_screen.md) — one grouped decision box, no jumping, brief markdown chat, inference picker, lane scores | completed 2026-10-08 (make check 3128 passed, 100%; npm 86/86) | 45 |
| 47 | [`47_polish_help_report_cost.md`](./47_polish_help_report_cost.md) — light run header, inline close, help menu, report layout and plot grid, time-aware Flash cost | completed 2026-10-08 (make check 100%; npm 89/89) | 46 |
| 48 | [`48_profile_column_lists.md`](./48_profile_column_lists.md) — data profiling question lists numeric and non-numeric columns (role overrides dtype) | completed 2026-10-08 (make check 100%) | 47 |
| 49 | [`49_search_wave_message.md`](./49_search_wave_message.md) — the chat titles a second experiment design as "Search wave N" with a one-line reason | completed 2026-10-08 (npm 90/90) | 48 |
| 50 | [`50_parallel_forest.md`](./50_parallel_forest.md) — faster random forests: a third of the cores for tool loops, threaded final forest fit, single-threaded predictions | completed 2026-10-08 (make check 100%) | 49 |
| 51 | [`51_chat_reliability.md`](./51_chat_reliability.md) — chat retries transient provider errors, friendlier small-talk reply, clean error display | completed 2026-10-08 (make check 100%; npm 91/91) | 50 |
| 52 | [`52_sparse_contract_proposal.md`](./52_sparse_contract_proposal.md) — a proposed contract stores only supplied and non-default fields, so defaults no longer lock every candidate | completed 2026-10-09 (make check 100%) | 51 |
| 53 | [`53_more_models.md`](./53_more_models.md) — eight more estimators (naive Bayes, LDA, extra trees, decision trees, lasso, elastic net); design picks models that suit the data | completed 2026-10-09 (make check 100%; npm 91/91) | 52 |
| 54 | [`54_no_step_cost.md`](./54_no_step_cost.md) — step details no longer show per-call cost | completed 2026-10-09 (npm 91/91) | 53 |
| 55 | [`55_interactive_charts.md`](./55_interactive_charts.md) — report plots are interactive SVG charts with hover values, two per row | completed 2026-10-09 (make check 100%; npm 95/95) | 54 |
| 56 | [`56_rename_project.md`](./56_rename_project.md) — rename a project from its page (display name; slug stays) | completed 2026-10-09 (make check 100%; npm 96/96) | 55 |
| 57 | [`57_rename_moves_slug.md`](./57_rename_moves_slug.md) — renaming a project re-derives its short name and moves its folder and stored paths | completed 2026-10-09 (make check 100%; npm 96/96) | 56 |
| 58 | [`58_candidate_failures.md`](./58_candidate_failures.md) — grid size checked at decision time; one model's failure ends only its branch; no stale decision cards; no Agent calls table | completed 2026-10-09 (make check 100%; npm 96/96) | 57 |
| 59 | [`59_cheaper_live_tests.md`](./59_cheaper_live_tests.md) — cheaper live harness: thinking off, one wave, no retunes; digest for per-model and chat prompts | completed 2026-10-09 (make check 100%) | 58 |
| 60 | [`60_search_progress.md`](./60_search_progress.md) — the model comparison before champion selection shows as running | completed 2026-10-09 (make check 100%; npm 97/97) | 59 |
| 61 | [`61_no_duplicate_project.md`](./61_no_duplicate_project.md) — the setup chat does not create the selected project again | completed 2026-10-09 (make check 100%) | 60 |
| 62 | [`62_schema_echo_and_wide_confusion.md`](./62_schema_echo_and_wide_confusion.md) — the model returns values, not the schema; a wide confusion matrix scrolls | completed 2026-10-09 (make check 100%; npm 97/97) | 61 |
| 63 | [`63_data_quality.md`](./63_data_quality.md) — data quality node; runs keep their captured spine | in progress (63-0, 63a accepted 2026-10-09, make check 100%) | 62 |
| 44 | [`44_chat_setting_changes.md`](./44_chat_setting_changes.md) — "set X to Y" in the chat forks a new run with a new contract version from the earliest affected step; offline stand-in understands chat commands; fork note in the new run's chat | completed 2026-10-08 (make check 3125 passed, 100%; npm test 80/80) | 43 |
| 43 | [`43_cost_and_ui_polish.md`](./43_cost_and_ui_polish.md) — prompt digest + cache-friendly prefix, code-built decisions for fixed steps, ordered stepper, lucide icons, size-safe layout, mode and fit-time labels | completed 2026-10-08 (make check 3117 passed, 100%; npm test 79/79) | 42 |
| 42 | [`42_speed_chat_redesign.md`](./42_speed_chat_redesign.md) — threaded CV/permutation, numpy bootstrap scoring, navy/blue redesign, chat narration + run summary, short gate forms | completed 2026-10-08 (make check 3104 passed, 100%; npm test 77/77) | 41 |

## Dependency graph (phase level)

```
A: 01 → 02 → {03, 05} → {04, 06}
                          │
B:                        07 → 08 → {09, 10, 11} → 12 → 13 → 14 ─┬→ 16 (gate)
                                                    │                │
                                                    ├→ 15 ───────────┤
                                                    ├→ 17            │
                                                    └→ 18 ───────────┴→ 19 → 20
C: 21 → 22 → {23, 24}    25 → 26
D: 27a → 27b → 28 → 29
E: 30 → 31 → 32
F: 33 → 34 → 35
G: 36 → 37 → 38 → 39 → 40 → 41 → 42 → 43 → 44 → 45 → 46
```

## Two checkpoints worth pausing at

- **Chunk 16** is the keystone. A complete statistical modelling pipeline running with *zero* LLM
  involvement. If it is green, every later agent misbehaviour is unambiguously a bad *decision*
  rather than a broken tool. Do not proceed past it on a partial pass.
- **Chunk 24** carries the two honesty invariants (shared split, locked test set). Treat a failure
  here as a correctness bug, not a flaky test.

## Deferred to a human, deliberately

- `skills/modelling.md` **heuristics and pitfalls** — chunk 26 scaffolds all 13 sections with
  registry-validated `Tools` lists and `TODO:` bodies; the domain content is written by hand
  afterwards (D12, §9.3).
- Dependency **version numbers** — chunk 01 resolves and pins them by running the install. No
  version is guessed in advance.
