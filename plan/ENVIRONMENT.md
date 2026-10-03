# Verified environment reference

Read only sections relevant to changed dependencies. These facts were verified in earlier chunks;
do not rerun historical probes without contrary evidence. Validation authority: [AGENTS.md](../AGENTS.md#validation-policy).

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
