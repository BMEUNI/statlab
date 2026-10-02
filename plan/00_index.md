---

> **Picking this up cold?** Read [`WHATS_NEXT.md`](./WHATS_NEXT.md) first — live state, the working loop, and the corrections made so far.
status: in_progress
dependencies: []
completion_date: null
implementation_notes: ""
---

# Execution Plan — Chunk Index

Master architecture: [`PLAN.md`](./PLAN.md) (31 decisions D1–D31, 21 resolved questions).
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
| Under copy-on-write, `Series.to_numpy()` returns a **read-only** array | Use `to_numpy(copy=True)` before mutating the result; writing to the shared buffer raises. |
| Datetimes default to `datetime64[us]`, not `[ns]` | Assert on `[us]` in fixtures; temporal splits and datetime-part extraction must not hardcode `[ns]`. |
| Parquet round-trip preserves `str`, `category`, `datetime64[us]`, `bool` exactly | Chunk 06 can assert full dtype equality after round-trip rather than tolerating drift. |
| `Pandas4Warning` exists as a deprecation category | With `filterwarnings = ["error"]`, touching a pandas-4-deprecated API fails the suite. That is intended. |
| `np.int64`/`np.float64`/etc. from a pandas reduction are not `int`/`float` for pydantic's `JsonValue` — `np.int64` is not an `int` subclass, though `np.float64` passes since it is a `float` subclass | Every value from `.count()`, `.sum()`, `value_counts()`, or integer `.iloc[i]` needs explicit conversion before it reaches a `ToolSuccess` summary or a JSON artifact. Chunk 09's `_frame.json_scalar` is the one place this happens. |
| An **integer-valued** categorical reads back from parquet as `int64`, dropping its categories; a string-valued one keeps them, unused levels included (verified chunk 10 review) | A test needing a categorical to survive an artifact round-trip must use string levels |
| `Series.value_counts()` on a categorical lists unused categories with count 0 (`nunique`/`unique` do not) | Count levels with `_frame.observed_counts` (verified chunk 10 review): a bare `value_counts()` makes a split refuse a categorical target over a class no row has, and `summarize_class_target` report `minority_share` 0.0. |
| `json.dumps` writes `NaN`/`Infinity` as bare, non-standard tokens by default; `allow_nan=False` is required for strict JSON | A constant column's correlation is `NaN`; without the strict flag, `save_json` would write a file a browser's `JSON.parse` rejects. Chunk 09 made `save_json` strict for this reason. |
| `pandas.api.types.is_numeric_dtype(bool_series)` is `True`, but `DataFrame.select_dtypes("number")` excludes `bool` | Two different "is this numeric" checks disagree on the same column. Pick one predicate (chunk 09's `_frame.is_numeric_column`: numeric dtype and not bool) and use it everywhere a tool needs to know. |
| `pd.util.hash_pandas_object` is deterministic across processes and `PYTHONHASHSEED`; with `index=False` it ignores the index, so a relabelled copy of a row hashes equal (verified chunk 10, P2/P3). Parquet keeps a non-range index, and a `RangeIndex` stays one (P1). | `verify_split`'s duplicate check uses `index=False` to catch a relabelled train row planted in test — which its index-overlap check cannot see. The test-set fingerprint (D-c) uses `sort_index()` + `index=True` so it is order-independent but label- and value-sensitive. Split tools never `reset_index`, so the stored index the checks rely on survives. |
| `Int64.fillna(2.5)` raises `TypeError` (verified chunk 11, P1) | A median/mean fill on a nullable-int column crashes. Mean/median imputation casts the column to `float64` first, on every frame, so dtypes agree across splits. |
| `Categorical.fillna(<new value>)` raises `TypeError` (verified chunk 11, P2); and `pandas.api.types.is_string_dtype(categorical)` is **`True`** | `constant` imputation on a categorical-dtype column is `tool_param`, not a silent string fill — so the string-fill predicate must exclude `CategoricalDtype` even though `is_string_dtype` accepts it. |
| `pd.to_datetime(format="ISO8601", errors="coerce")` still **raises `ValueError` on mixed UTC offsets** despite `errors="coerce"` (verified chunk 11, P4) | Catch the `ValueError`, re-parse with `utc=True`, and warn. `cast_dtypes`' datetime branch does exactly this. |
| Constructing a `pd.Categorical` with a `categories=` list that does **not** cover a present non-null value raises a `Pandas4Warning` (→ error under `-W error`); the old silent out-of-category → NaN coercion is deprecated (verified chunk 11, NOT in that chunk's P1–P7) | `cast_dtypes`' ordinal branch maps every value outside the declared levels (and every null) to `None` **before** constructing the `Categorical`, rather than relying on the constructor to coerce it. |
| Integral floats stringify as `"1.0"`; any cast that compares string forms (categorical, ordinal levels, boolean tokens) must route through `Int64` first (verified chunk 11, P3; review 2026-10-01) | Use `cleaning._as_str`: integer-coded columns with a null load as `float64`, so `astype("str")` gives `"1.0"` and silently falls outside the levels/token map, nulling every value. The shared helper casts all-integral numerics via `Int64` so `1.0` → `"1"`. (Chunk 12 moved it to `_frame.as_str` and reuses it in the ordinal/one-hot encoders for the same reason.) The same rule applies to **class labels and level names**, not just column casts: a 0.0/1.0 float-coded target or a float-coded one-hot level must be named/compared by `_frame.as_str_scalar` (the scalar twin of `as_str`), or `str(1.0)` → `"1.0"` breaks scoring of float-coded targets, one-hot of float-coded columns, and contract `positive_class` matching (batch B2 review 2026-10-01). |

**scikit-learn 1.9.0 behaviour — verified empirically under `-W error` (chunk 12 §1):**

| Fact | Consequence |
|---|---|
| `TargetEncoder(shuffle=, random_state=)` raises a `FutureWarning` (deprecated in 1.9, removed in 1.11) → an error under `filterwarnings=["error"]` (chunk 12, P1) | Pass a seeded splitter as `cv` instead: `StratifiedKFold(5, shuffle=True, random_state=seed)` for a class target, `KFold(...)` for a continuous one. |
| `StratifiedKFold` emits a `UserWarning` ("least populated class has N members, < n_splits") → error, *before* any `ValueError`, when a class has fewer rows than folds (chunk 12, P1) | Check the smallest observed class count against the fold count *before* calling `fit_transform` and raise `ToolDataError`, so neither sklearn's warning nor its `ValueError` surfaces as `bug`. |
| `TargetEncoder.fit_transform` is out-of-fold (cross-fitted) and differs from `fit(...).transform(...)` on the same rows; deterministic for a fixed splitter seed; works on `str` dtype (chunk 12, P2) | D34c's leak-free train encoding comes free. A leak test must compare the tool's train output against BOTH `fit_transform` (equal) and `fit().transform()` (differs) — equality alone passes an in-sample implementation. |
| Multiclass `TargetEncoder` outputs one column per class (`get_feature_names_out` gives `{col}_{class}`); an unseen level transforms to the target mean; `classes_` holds `np.str_`, not `str` (chunk 12, P3) | Name outputs `{col}__te_{class}` with `str(c)` for each class; an unseen level at apply is representable (the mean), counted, never an error (D-a). |
| `n_jobs=-1` (or any `n_jobs > 1`) inside a tool body starts loky worker processes from a worker thread; the chunk 13 test run **deadlocked** on it (verified chunk 13 review) | `n_jobs=1` on every estimator **and** every `cross_val_score`/CV helper; `test_tuning_cv_is_single_threaded` spies on it |
| **sklearn pulls scipy** (an unpinned transitive dep); importing any sklearn module at package-init time therefore imports scipy. `tools/__init__.py` imports every tool module at init | A tool module that uses sklearn imports it **lazily inside the tool body**, not at module top level, so chunk 09's `compute_correlations` spearman path (`df.rank().corr()`, scipy-free by design) stays scipy-free even though importing a sibling tool runs the package `__init__`. `encoding.py` is the first module to do this. |

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

**MCP SDK 2.2.0 facts — verified empirically 2026-09-10; chunks 07–20 depend on these.**
`mcp` 2.x is a breaking rename from 1.x. Four assumptions taken from the 1.x API were wrong, so do
not re-derive any of this from memory:

- **`FastMCP` no longer exists.** `from mcp.server.mcpserver import MCPServer`. The import error
  itself names the migration guide.
- Model attributes are **snake_case**: `Tool.input_schema` (not `inputSchema`),
  `CallToolResult.is_error`, `.structured_content`, `.meta`. The *wire* JSON stays camelCase.
- `@server.tool(description=..., meta={...})`. Parameters are declared as
  `Annotated[T, Field(description=..., ge=..., le=...)]`, and the generated JSON Schema carries
  `description`, `enum`, `minimum`/`maximum`, `default` and `required` — exactly what D23 requires,
  with no hand-written schema.
- **`meta` survives `tools/list`** and arrives as `Tool.meta`. That is how D12's `phase` /
  `allow_in` reach `mcp_client` without a second registry.
- A union return type is wrapped: `structured_content == {"result": {...}}`. Chunk 21 unwraps
  `["result"]`.
- `server.streamable_http_app(streamable_http_path="/mcp")` returns a **Starlette** app;
  `@server.custom_route(path, methods=[...])` adds a plain HTTP route beside it.
- **Static bearer auth is Starlette middleware, not `token_verifier`.** Passing `token_verifier`
  without `auth=` raises `ValueError`, and `AuthSettings` requires `issuer_url` +
  `resource_server_url` — the OAuth resource-server path. Declaring a fake issuer for a shared
  secret would be a lie in config *and* would expose OAuth metadata endpoints.
- **`httpx.ASGITransport` cannot test the authorised path.** The streamable-HTTP session manager
  needs the ASGI lifespan, so an in-process request fails with
  `RuntimeError: Task group is not initialized`. The 401 path *does* work in-process, because the
  middleware short-circuits before the manager runs. Authorised tests run `uvicorn.Server` in a
  thread, polling `srv.started`.
- Client side: `create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})` →
  `streamable_http_client(url, http_client=...)` → `ClientSession(read, write)` → `initialize()`.
  `StreamableHTTPTransport(url)` takes **only** a url; headers go on the http client.
- **`from __future__ import annotations` makes every annotation a string at runtime**, so any code
  reading a parameter's `Field` must use `get_type_hints(fn, include_extras=True)`. Applies to the
  `@tool` decorator and anything later that introspects a tool signature.
- `structured_content` populates only when the registered callable's **actual return-type object** is
  the union — a stringified annotation is not enough. Chunk 07 hit this.
- `create_mcp_http_client` is defined in `mcp.shared._httpx_utils` and re-imported (without an
  `__all__` entry) into `mcp.client.streamable_http`. Both names resolve to the same object at
  runtime -- but `mypy --strict`'s `no_implicit_reexport` (set explicitly in this project's
  `pyproject.toml`) rejects importing it from the latter, since that module never declares it
  re-exportable, regardless of `from ... import` vs module-qualified access. **Import from
  `mcp.shared._httpx_utils`** despite the leading underscore: it is the only option that is both
  correct at runtime and mypy-strict clean without adding a `# type: ignore` shape outside the
  three this project permits in tests. Re-check if a future SDK release adds the name to
  **The decisive evidence:** `mcp.shared._httpx_utils.__all__` *explicitly contains*
  `create_mcp_http_client`, while `mcp.client.streamable_http` has no `__all__` at all. The module
  with the underscore is the one whose author declared the symbol public; the leading underscore is
  on the module path, not on the export. Rejected alternative: a scoped
  `no_implicit_reexport = false` override for `mcp.*`, which does work and does *not* weaken our own
  modules (verified) — but it loosens a strictness flag to paper over a third-party packaging gap,
  and adds config surface, to buy a cosmetically nicer import path.
  `mcp.client.streamable_http.__all__`.
- **`Field(pattern=...)` with a *string* is compiled by pydantic-core's Rust regex engine, which rejects `\Z` with a `SchemaError` at class creation — use `\z`.** A pre-compiled `re.Pattern` goes through Python's `re` instead, which is why `ARTIFACT_ID_PATTERN` (compiled, `\Z`) never hit it. Python 3.14's `re` accepts `\z` too, so one string works in both. Verified: `\z` rejects a trailing newline and a literal `z` under both engines. Found in chunk 08.
- **`MCPServer` runs a *sync* tool in an AnyIO worker thread but awaits an *async* tool on the event loop.** `@tool`'s wrapper is async (it awaits the fit semaphore), so it must offload the body itself with `anyio.to_thread.run_sync` — otherwise every tool call in every run serialises on one thread. Measured: 4 × 0.5 s calls took 2.09 s inline, 0.54 s offloaded. Test T17b pins it with a two-party barrier. Found 2026-10-01 while drafting chunk 09.
- `mcp` depends on **`httpx2` 2.12.0**, installed alongside our pinned `httpx==0.28.1`. Both
  exist by design; do not "consolidate" them.

## Execution batches (from chunk 12, decided 2026-10-01)

Chunks 01–11 ran one per cycle. The remaining 30 run in **16 batches**. One cycle per batch: both
specs are written and committed, two implementation stages run in sequence (the second sees the
first's code and focused report), there is one independent review, one final acceptance execution,
and one authorized commit. Specs use document checks, not product gates. Scope is unchanged. Every
chunk so far had 2–4 defects caught in review, so the review is not reduced, only shared.

| Batch | Chunks | Closes |
|---|---|---|
| B1 | 12 encoding + 13 training | M4 |
| B2 | 14 evaluation + 15 inference | M5 ✅ |
| B3 | 16 walking-skeleton gate (alone: it is the gate) | M6 ✅ |
| B4 | 17 features + 18 interpretation | M7a ✅ |
| B5 | 19 champion/reporting + 20 reproducibility | M7b ✅ for the `pipeline.py` gate; chunk 20r closed the scope gap: `ridge` and `mnlogit` (`fit_mnlogit`) shipped, `split_temporal`/`split_grouped` deferred (D-c), **83 served tools (v1)** |
| B6 | 21 mcp_client + 25 llm_client (independent) | |
| B7 | 22 engine core + 23 restart reconciliation | M8a |
| B8 | 24 candidate fan-out + 26 routing/skills | M8b |
| B9 | 27a nodes 1–4 + 27b nodes 5–6 | |
| B10 | 28 nodes 7–11 (alone: the largest) | |
| B11 | 29 nodes 12–13 + 30 control router | M9 |
| B12 | 31 recovery/lifecycle + 32 gates/rewind | M10, M11 |
| B13 | 33 API projects + 34 API runs | |
| B14 | 35 SSE + 36 frontend scaffold | M12 |
| B15 | 37 setup flow + 38 run workspace | |
| B16 | 39 comparison + 40 report | M13 |

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
| 21 | [`21_mcp_client.md`](./21_mcp_client.md) — tool discovery + schema cache, allow-list enforcement (D12), `ArtifactRef` resolution, injection of `project`/`run`/`dataset`/`seed` (split tools' `seed` comes from `split.seed`, every other tool's from `constraints.random_seed`), recording the split tool's `test_fingerprint` via `set_test_fingerprint`, **a parameter named `train` never receives a test-derived artifact** (covers every `fit_*` and the train-only deciders of chunk 11), **test-set lineage lock** (D32a–b: `derived_from_test` propagation, rooted at a split tool's output with `meta.split == "test"`; `verify_split` is the one pre-node-12 tool allowed a test input, `fit_*` never on test-derived, transform-only flow in node 12 keyed on each tool's `meta.transform_only` (chunk 17 D-a), one `score_*` via `test_scored_at`; the `fit_*`/`train` clause and the one-score count hold in **every** node, and after node 12 read-only tools such as `describe_artifact` may receive test-derived refs (chunk 16 review); in node 12, after the one score, `reporting`-phase tools may read test-derived tables and `select_champion` never may (chunk 19 D-d)), schema validation before dispatch, optional one-argument repair, error mapping; **record a `LoggedCall` (tool, arguments as sent, output refs) per call** for chunk 20's tools. Keep every dispatched scoring claim on failure; minimal node-execution/tool-call repositories. **Acceptance:** `tests/test_skeleton.py` re-driven through `mcp_client`, with the driver's injection and rule checks deleted in favour of the client's. | untouched | 02, 05, 06, 07 |
| 22 | `22_graph_engine_core.md` — `RunState`, checkpointer, event bus with monotonic `seq` → `events.jsonl`, declared `NodeSpec` spine traversal, shared-node sequential execution; on node success call `statlab.storage.prune_intermediates` (D24; it replaced the `prune_intermediate_artifacts` tool, chunk 20 D-a). If the spec exceeds ~200 tests, split checkpointer/event bus from traversal | untouched | 02, 04, 05, 06, 21 |
| 23 | `23_restart_reconciliation.md` — boot-time status scan, node-boundary resume, interrupted-run marking (§5.1) | untouched | 22 |
| 24 | `24_candidate_fanout.md` — parallel candidate execution, shared-split guarantee, test-lock integration, budget caps, search waves, pause/cancel at tool-call boundaries (D36c) | untouched | 06, 21, 22 |
| 25 | [`25_llm_client.md`](./25_llm_client.md) — LiteLLM wrapper, 3-tier structured output with capability detection, one repair retry for every tier, offline import, prefix capability overrides, thinking toggle, in-flight semaphore, per-attempt token/cached-cost logging | untouched | 01, 02, 05, 21 |
| 26 | `26_routing_and_skills.md` — `Role`→model routing with 4-level precedence (§7.3), skill-file parser + startup validation against the declared spine and each tool's `allow_in` (§9.2), invariant-first prompt assembly, **project memory repository + read path and the `remember` action** (D36d; previously unowned) | untouched | 05, 21, 22, 25 |

## Phase D — Nodes (27–29)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 27a | `27a_nodes_shared_foundation.md` — nodes 1–4: intake, problem_definition (incl. the PR-AUC default under imbalance, D33d, and pre-registered inference goals, D35), data_profiling, data_splitting + their decision schemas | untouched | 08, 09, 10, 22, 24, 26 |
| 27b | `27b_nodes_design_inference.md` — nodes 5–6: experiment_design (registry check, ≤2-per-family cap, `max_candidates`, chunk 04's deferred checks; reject a candidate whose `preprocessing.missing.drop_rows_above` differs from the contract's, since row drops are shared and pre-split, D34d) and inferential_analysis (D31, D35) + their decision schemas. Split from 27 in review: six nodes in one chunk was the largest chunk in the plan | untouched | 13, 15, 27a |
| 28 | `28_nodes_per_candidate.md` — nodes 7–11: preprocessing, feature_engineering, model_training, model_evaluation, model_interpretation | untouched | 11, 12, 13, 14, 17, 18, 27b |
| 29 | `29_nodes_champion.md` — nodes 12–13: champion_selection (selection-bias labelling, CIs, cross-run test-exposure disclosure — D32c/D33; `select_champion` is called once and the backend refuses a second call once test is scored; `assemble_report` gets `prior_test_scorings` (`count_prior_test_scorings`) and `n_waves`; node 13 passes the run's tool log as `LoggedCall`s to `export_pipeline_script` (with `target` = the champion's test score table) and `snapshot_run`, and adds the LLM model name to the manifest; scoring test = replay the champion's node 7–8 calls whose tool is `transform_only`, in order, with the same non-frame arguments and `frame=test`, then predict and one `score_*` — chunk 17 D-a) and artifact_persistence | untouched | 19, 20, 28 |

## Phase E — Control layer (30–32)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 30 | `30_control_router.md` — interrupt router classifier, out-of-band Q&A, status explainer | untouched | 22, 26 |
| 31 | `31_control_recovery.md` — recovery/retry by `error_class`, cancel/pause/resume lifecycle; rewind handler: forks keep the parent's artifact refs and create no rows (D11); listing follows `forked_from_run_id` | untouched | 22, 30 |
| 32 | `32_control_gates_rewind.md` — HITL gates (7 actions), guided mode `gate_policy`, fork-based rewind with hard-linking | untouched | 24, 31 |

## Phase F — HTTP API (33–35)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 33 | `33_api_projects.md` — projects, dataset upload (≤200 MB, stored as `datasets/<sha256>.csv`, chunk 08 D-a), contract upload/validate/propose | untouched | 03, 05, 06 |
| 34 | `34_api_runs.md` — run creation, state snapshot, messages, gate answers, control actions (incl. rewind → new run id) | untouched | 22, 32, 33 |
| 35 | `35_sse_stream.md` — SSE endpoint, `?since=` replay from `events.jsonl`, 15 s heartbeat, token-delta streaming for prose only | untouched | 22, 34 |

## Phase G — Frontend (36–40)

| # | Chunk | Status | Depends on |
|---|---|---|---|
| 36 | `36_frontend_scaffold.md` — Vite + TS strict, typed API client, `EventSource` hook with `seq` tracking and reconnect | untouched | 35 |
| 37 | `37_frontend_setup_flow.md` — project picker, upload wizard, contract mapping table, mode selection | untouched | 33, 36 |
| 38 | `38_frontend_run_workspace.md` — node graph (shared spine + candidate columns), chat with token streaming, inline gate forms | untouched | 35, 37 |
| 39 | `39_frontend_comparison.md` — candidate comparison table sortable by metric with `varies_from_baseline`, artifact browser, live cost | untouched | 38 |
| 40 | `40_frontend_report.md` — report viewer with plots (**render `report.md` with raw HTML disabled**: section bodies are LLM text, chunk 19 D-e), champion card, downloads, run-history lineage tree | untouched | 39 |

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
G: 36 → 37 → 38 → 39 → 40
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
