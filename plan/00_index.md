---
status: in_progress
dependencies: []
completion_date: null
implementation_notes: ""
---

# Execution Plan — Chunk Index

Master architecture: [`PLAN.md`](./PLAN.md) (31 decisions D1–D31, 21 resolved questions).
This directory decomposes it into **40 atomic, topologically ordered chunks**.

## Hard rules for the implementing agent

1. **Sequential only.** Execute chunks in ascending order. Chunk *N* may reference schemas,
   types and utilities from chunks `< N` and **never** from chunks `> N`.
2. **DoD gates.** Do not begin chunk *N+1* until chunk *N* satisfies its Definition of Done:
   100 % line + branch coverage on code added by that chunk, **and** the entire prior
   regression suite green (`make test`).
3. **Minimal viable code.** No speculative abstraction, no boilerplate, no "might need later".
   If a class has one implementation and one caller, it is a function. Delete over add.
4. **Strict typing.** `mypy --strict` clean. No `Any`, no bare `dict`/`list`, no implicit
   `Optional`. `# type: ignore` requires an inline justification comment.
5. **Errors wrap, never leak.** Every boundary (filesystem, HTTP, subprocess, LLM, SQL) maps
   foreign exceptions to a domain error defined in chunk 02. No raw `Exception` escapes a module.
6. **PLAN.md wins.** On any conflict between a chunk file and `PLAN.md`, `PLAN.md` is
   authoritative — in particular §4's normative 13-node spine table.
7. **Update your own frontmatter.** On completion set `status: completed`, fill
   `completion_date`, and append an implementation note recording outcomes, trade-offs, and
   anything the next chunk must know.

## Phase A — Foundation (01–06)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 01 | [`01_foundation.md`](./01_foundation.md) — repo skeleton, pinned deps (Task 0), config, Makefile | untouched | — |
| 02 | `02_core_types.md` — `ToolResult`/`ToolError` envelope, `error_class` taxonomy, `ArtifactRef`, `NodeSpec`, `NodeResult`, `GatePolicy`, domain error hierarchy | untouched | 01 |
| 03 | `03_data_contract.md` — `DataContract` Pydantic tree (§4.1), validation against a real frame, JSON round-trip | untouched | 02 |
| 04 | `04_candidate_spec.md` — `CandidateSpec`, `PreprocessingSpec`, `FeatureSpec`, `EstimatorSpec`, canonical spec-hash, dedupe + baseline + family-cap rules (D28) | untouched | 02, 03 |
| 05 | `05_persistence_sqlite.md` — schema DDL (§10.2), WAL + `busy_timeout`, migration runner, typed repository functions | untouched | 02 |
| 06 | `06_artifact_store.md` — run-folder layout (§10.1), parquet/joblib/png/json IO, artifact registry, `locked` flag, hard-link fork copy, prune-on-success | untouched | 02, 05 |

## Phase B — MCP server (07–20)

The entire tool layer is built and tested **before any agent code exists** (D22).

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 07 | `07_mcp_server_foundation.md` — Streamable HTTP app, bearer auth, `@tool` decorator (registration, validation, envelope, timing, `heavy=` semaphore), `health`, `list_artifacts`, `describe_artifact` | untouched | 02, 06 |
| 08 | `08_tools_intake.md` — `load_csv`, `infer_column_roles`, `validate_contract`, `detect_id_columns`, `detect_leaky_columns` | untouched | 03, 07 |
| 09 | `09_tools_profiling.md` — 9 profiling tools incl. compute/render split | untouched | 07, 08 |
| 10 | `10_tools_splitting.md` — 4 per-strategy split tools + `verify_split`'s 4 checks | untouched | 07, 08 |
| 11 | `11_tools_cleaning.md` — 9 tools; `fit_*`/`apply_*` separation | untouched | 07, 08 |
| 12 | `12_tools_encoding.md` — 8 tools; one-hot / ordinal / target / scaler, all fit+apply | untouched | 07, 11 |
| 13 | `13_tools_training.md` — `ESTIMATORS` registry (6 estimators, D25), `train_estimator`, `tune_estimator`, `predict`, `predict_proba` | untouched | 07, 12 |
| 14 | `14_tools_evaluation.md` — 11 tools **plus `rank_models`** (pulled forward so chunk 15 is self-contained — audit fix C4/S2) | untouched | 07, 13 |
| 15 | `15_skeleton_integration.md` — **keystone gate**: the 33-tool walking skeleton (§6.4) driven end to end with zero LLM, on a classification *and* a regression fixture | untouched | 08–14 |
| 16 | `16_tools_inference.md` — `SPECS` registry, `build_dummy_matrix`, `assemble_inference_matrix`, statsmodels fits, coefficient table, VIF, residual/influence tests + known-answer test | untouched | 07, 12 |
| 17 | `17_tools_features.md` — 6 feature-construction and selection tools | untouched | 07, 12 |
| 18 | `18_tools_interpretation.md` — permutation importance, PDP compute + render, coefficient importance, error analysis | untouched | 07, 13, 14 |
| 19 | `19_tools_champion_reporting.md` — `select_champion`, `render_metrics_table`, `assemble_report` | untouched | 14, 16, 18 |
| 20 | `20_tools_reproducibility.md` — `export_pipeline_script` (champion path only), `snapshot_run`, `compute_run_hash`, `prune_intermediate_artifacts` | untouched | 06, 19 |

## Phase C — Orchestration (21–26)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 21 | `21_mcp_client.md` — tool discovery + schema cache, allow-list enforcement (D12), `ArtifactRef` resolution, **test-set lock** refusal pre-dispatch (D26), error mapping | untouched | 02, 06, 07 |
| 22 | `22_graph_engine_core.md` — `RunState`, checkpointer, event bus with monotonic `seq` → `events.jsonl`, declared `NodeSpec` spine traversal, shared-node sequential execution | untouched | 02, 04, 05, 21 |
| 23 | `23_restart_reconciliation.md` — boot-time status scan, node-boundary resume, interrupted-run marking (§5.1) | untouched | 22 |
| 24 | `24_candidate_fanout.md` — parallel candidate execution, shared-split guarantee, test-lock integration, budget caps, search waves | untouched | 06, 21, 22 |
| 25 | `25_llm_client.md` — LiteLLM wrapper, 3-tier structured output with capability detection, one repair retry, token/cost logging | untouched | 01, 02 |
| 26 | `26_routing_and_skills.md` — `Role`→model routing with 4-level precedence (§7.3), skill-file parser + startup validation, invariant-first prompt assembly | untouched | 21, 25 |

## Phase D — Nodes (27–29)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 27 | `27_nodes_shared.md` — nodes 1–6: intake, problem_definition, data_profiling, data_splitting, experiment_design, inferential_analysis + their decision schemas | untouched | 08, 09, 10, 16, 22, 24, 26 |
| 28 | `28_nodes_per_candidate.md` — nodes 7–11: preprocessing, feature_engineering, model_training, model_evaluation, model_interpretation | untouched | 11, 12, 13, 14, 17, 18, 27 |
| 29 | `29_nodes_champion.md` — nodes 12–13: champion_selection (incl. the single test score) and artifact_persistence | untouched | 19, 20, 28 |

## Phase E — Control layer (30–32)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 30 | `30_control_router.md` — interrupt router classifier, out-of-band Q&A, status explainer | untouched | 22, 26 |
| 31 | `31_control_recovery.md` — recovery/retry by `error_class`, cancel/pause/resume lifecycle | untouched | 22, 30 |
| 32 | `32_control_gates_rewind.md` — HITL gates (7 actions), guided mode `gate_policy`, fork-based rewind with hard-linking | untouched | 24, 31 |

## Phase F — HTTP API (33–35)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 33 | `33_api_projects.md` — projects, dataset upload (≤200 MB), contract upload/validate/propose | untouched | 03, 05, 06 |
| 34 | `34_api_runs.md` — run creation, state snapshot, messages, gate answers, control actions (incl. rewind → new run id) | untouched | 22, 32, 33 |
| 35 | `35_sse_stream.md` — SSE endpoint, `?since=` replay from `events.jsonl`, 15 s heartbeat, token-delta streaming for prose only | untouched | 22, 34 |

## Phase G — Frontend (36–40)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 36 | `36_frontend_scaffold.md` — Vite + TS strict, typed API client, `EventSource` hook with `seq` tracking and reconnect | untouched | 35 |
| 37 | `37_frontend_setup_flow.md` — project picker, upload wizard, contract mapping table, mode selection | untouched | 33, 36 |
| 38 | `38_frontend_run_workspace.md` — node graph (shared spine + candidate columns), chat with token streaming, inline gate forms | untouched | 35, 37 |
| 39 | `39_frontend_comparison.md` — candidate comparison table sortable by metric with `varies_from_baseline`, artifact browser, live cost | untouched | 38 |
| 40 | `40_frontend_report.md` — report viewer with plots, champion card, downloads, run-history lineage tree | untouched | 39 |

## Dependency graph (phase level)

```
A: 01 → 02 → {03, 05} → {04, 06}
                          │
B:                        07 → 08 → {09, 10, 11} → 12 → 13 → 14 → 15 (gate)
                                                    │           │
                                                    ├→ 16 ──────┤
                                                    ├→ 17       │
                                                    └→ 18 ──────┴→ 19 → 20
C: 21 → 22 → {23, 24}    25 → 26
D: 27 → 28 → 29
E: 30 → 31 → 32
F: 33 → 34 → 35
G: 36 → 37 → 38 → 39 → 40
```

## Two checkpoints worth pausing at

- **Chunk 15** is the keystone. A complete statistical modelling pipeline running with *zero* LLM
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
