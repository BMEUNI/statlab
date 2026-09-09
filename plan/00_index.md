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

## Verified environment (resolved by chunk 01, 2026-09-09)

Do not re-derive these. Every later chunk may rely on them.

**Interpreter:** Python **3.14.3** — no fallback needed. `requires-python = ">=3.14"`.

**Pins:** `fastapi==0.141.1`, `uvicorn[standard]==0.52.4`, `pydantic==2.13.5`,
`pydantic-settings==2.15.0`, `python-multipart==0.0.32`, `httpx==0.28.1`, `litellm==1.100.0`,
`mcp==2.2.0`, `pandas==3.0.5`, `pyarrow==25.0.1`, `numpy==2.5.3`, `scikit-learn==1.9.0`,
`statsmodels==0.15.0`, `matplotlib==3.11.1`, `joblib==1.6.0`.
Dev: `pytest==9.1.1`, `pytest-cov==7.1.0`, `pytest-asyncio==1.4.0`, `mypy==2.3.1`,
`ruff==0.16.6`, `pandas-stubs==3.0.5.260730`.

**pandas 3.0 behaviour — verified empirically, and a trap for chunks 06, 08, 09, 10, 17:**

| Fact | Consequence |
|---|---|
| String columns have dtype **`str`**, not `object` | **Never** detect strings with `dtype == object`. Use `pandas.api.types.is_string_dtype`. A role-inference function written against pandas 2 conventions fails silently here. |
| Copy-on-write is **always on**, cannot be disabled | Helps D22's immutable-artifact rule; tools cannot accidentally mutate a caller's frame through a view. Never rely on mutation-through-view. |
| Datetimes default to `datetime64[us]`, not `[ns]` | Assert on `[us]` in fixtures; temporal splits and datetime-part extraction must not hardcode `[ns]`. |
| Parquet round-trip preserves `str`, `category`, `datetime64[us]`, `bool` exactly | Chunk 06 can assert full dtype equality after round-trip rather than tolerating drift. |
| `Pandas4Warning` exists as a deprecation category | With `filterwarnings = ["error"]`, touching a pandas-4-deprecated API fails the suite. That is intended. |

**Tooling notes carried forward:**
- `[tool.mypy] plugins = ["pydantic.mypy"]` is required — without it mypy rejects
  `Settings(_env_file=...)`, and constructor kwargs on every model go unchecked.
- **`disallow_any_explicit` is incompatible with the pydantic mypy plugin and must stay off.**
  Verified empirically on mypy 2.3.1 + pydantic 2.13.5: *every* `BaseModel` subclass trips
  `explicit-any`, including `class Trivial(BaseModel): a: int`. The plugin synthesizes
  `__init__(**data: Any)` for each model, and the flag correctly flags the synthesized `Any`.
  This is not about `JsonValue` or recursive unions — those were a red herring.

  **Resolution (chunk 02, binding project-wide):** drop the flag; keep the plugin; enforce
  "zero `Any`" with a source-level test asserting the token `Any` appears nowhere in our three
  packages. This is *stronger* than the flag was, because it inspects our source text and
  therefore cannot be defeated by a class-line `# type: ignore`. It also keeps the plugin's
  real value — a constructor typo like `M(alhpa=1)` is still caught as `[call-arg]`.

  Consequence: chunk 01's two class-line `# type: ignore[explicit-any]` on `Settings` and
  `ToolSettings` **must be deleted**. With the flag gone they are unused, and
  `warn_unused_ignores = true` turns an unused ignore into an error — so the cleanup is
  self-enforcing rather than something to remember.

  Rejected alternative: a class-line ignore on every model. Fifty-plus blanket suppressions,
  each silencing any genuine explicit `Any` inside its class, is worse than no flag at all.
- **`[tool.pydantic-mypy] init_typed = true` is required project-wide** (chunk 02 addendum).
  Without it the plugin synthesizes `__init__(**data: Any)`, so **constructor argument types are
  never checked**: `ArtifactMeta(kind="spreadsheet")` and `Failure(error_class="oops")` both
  type-check clean, and — worse — a `NewType` like `ArtifactRef` is completely unenforced, making
  it decorative. Verified: with `init_typed = true` all three become `[arg-type]` errors, while
  `Model.model_validate({...})` stays unchecked because a dict is not statically inspected.

  **Convention that follows:** construct with **kwargs** in production and positive tests, so
  types are checked; use **`model_validate({...})`** in negative tests that deliberately pass
  invalid values. The second is the more honest test anyway — real input arrives as a dict parsed
  from JSON, which is exactly the path `model_validate` exercises.

  `disallow_any_explicit` stays off regardless. With `init_typed` it would pass, but its only
  unique contribution over the source-text scan is flagging third-party `Any` leakage we cannot
  fix and would suppress. The scan is stronger for our own code.
- `pydantic.JsonValue` is the correct type for arbitrary JSON payloads (`summary` fields). It
  validates JSON-serialisability, which `dict[str, object]` does not — `object` would move a
  failure from parse time to serialise time.
- `filterwarnings = ["error"]` is viable with **zero** ignores. Keep it that way; fix the cause,
  do not suppress.
- macOS `grep` is BSD, so `grep -P` is unavailable. Use `grep -c $'^\t'` for tab assertions.
- `plan/` is excluded from ruff scope — it is spec markdown with illustrative fences.

**Test-fixture pattern for later chunks (from chunk 02's addendum).** Under `init_typed = true`,
a fixture helper written as `def _spec(**overrides: object)` forces a `**dict` construction and
therefore an `# type: ignore[arg-type]`. Write helpers with **typed keyword parameters and
defaults** instead:

```python
def _spec(*, name: str = "intake", kind: NodeKind = "SHARED", ...) -> NodeSpec:
    return NodeSpec(name=name, kind=kind, ...)
```

No suppression, and callers like `_spec(kind="PER_CANDIDATE")` get checked. Chunks 22, 27 and 28
all need `NodeSpec` fixtures — use the typed form, not the `**overrides` form that survives in
`tests/test_domains_base.py`. Two suppressions there are accepted and need no change: a
deliberately invalid literal passed to a plain function (`retry_budget("nonsense")`, an inherent
type violation for a runtime defensive test) and one `__file__` narrowing case.

**Precedent set in chunk 01:** a pydantic `value_error` may be re-classified into a
user-facing category when the two differ (a present-but-blank `STATLAB_MCP_TOKEN` is reported
as *missing*, because that is what the user needs to hear). Reuse this pattern rather than
inventing a second convention.

## Phase A — Foundation (01–06)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 01 | [`01_foundation.md`](./01_foundation.md) — repo skeleton, pinned deps (Task 0), config, Makefile | completed | — |
| 02 | [`02_core_types.md`](./02_core_types.md) — `ToolResult` envelope, `error_class` taxonomy + retry budget, `ArtifactRef`/`ArtifactKind`, `NodeSpec`, `NodeResult`, `GatePolicy`, structured error hierarchy. **Introduces `contracts/statlab_contracts/`** — a third dependency-free package for wire types, so the envelope cannot drift between the two processes | completed | 01 |
| 03 | [`03_data_contract.md`](./03_data_contract.md) — `DataContract` Pydantic tree (§4.1): 10 models, 9 closed vocabularies, 28 consistency rules, JSON round-trip. Lives in `contracts/` (it crosses the process boundary), **not** `backend/` as §3.2 says. Validation against a real *frame* is chunk 08's `validate_contract` tool — this chunk validates the contract against itself | untouched | 02 |
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
