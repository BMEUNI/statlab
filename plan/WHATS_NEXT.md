# What's next

Handoff note. Written 2026-09-10, after chunk 05. Read this, then `00_index.md`, then `PLAN.md`.

This file holds only what is **not** written down elsewhere: live state, the working loop, and the
lessons that cost something to learn. Everything else is in `PLAN.md` (architecture, 31 locked
decisions) and `00_index.md` (the 40-chunk map, hard rules, verified environment facts). Do not
duplicate them here.

---

## State

**7 of 40 chunks complete, including a gated addendum.** Phase B started; `08` is next
(intake tools).

| Verified, uncommitted at write time | |
|---|---|
| Tests | 1112 (1111 passed, 1 skipped -- chunk 06's F9, by spec) |
| Coverage | 100 % line **and** branch (1550 stmts, 338 branches, 0 missed) |
| mypy | `--strict` clean on 60 source files, `init_typed = true` |
| ruff | `check` and `format --check` clean |
| Warnings | `filterwarnings = ["error"]`, zero ignores |
| Suppressions | zero in production code; zero `pragma: no cover`; zero `Any` in `backend/`, `mcp_server/`, `contracts/`. `# type: ignore` in tests only, all in the three permitted shapes |
| Git | local `master`, never pushed, tree clean before this chunk's edits |

Re-verify with `make test && make typecheck && make lint` before trusting this table.

---

## The working loop

1. I draft `plan/NN_name.md` — the complete, unambiguous spec.
2. I write a handoff prompt for a separate implementation agent.
3. The agent implements and reports back against a fixed set of questions.
4. **I verify independently** — never on the report alone. Run the gates, then probe the specific
   claims empirically (does the constraint actually reject? does the hash actually match?).
5. I commit. Then draft the next chunk.

Step 4 is the one that has caught everything. Three of the last five chunks had a defect that a
green test suite did not reveal, found by executing a probe against the claim rather than reading
the assertion.

**Commits happen only when the user asks.** Never push.

---

## Chunk 06, complete: artifact store (incl. §7 addendum)

Run-folder layout (`PLAN.md` §10.1), the artifacts registry (+`pruned_at`), hard-link fork
copies (D11), prune-on-success (D24), and content IO (parquet/joblib/png/json/text) in
`mcp_server/statlab_tools/artifacts.py` — the only module in the project that imports pandas.

**The D11×D24 design question is closed, not just settled on paper.** P10/P11 confirmed both
directions empirically: forking then pruning the child leaves the parent's file readable, and
the reverse. No refcounting was added. All seven pandas 3.0 dtypes (`str`, `category`, `int64`,
`float64`, `bool`, `datetime64[us]`, `Int64`) round-trip through parquet unchanged — measured,
not assumed — so `save_dataframe`/`load_dataframe` carry no dtype-preservation logic.

**Accepted with a gated addendum, then closed.** The first pass had two real defects, both the
same shape as this session's other findings — a guarantee that read as present while enforcing
nothing:

- The `statlab.errors` import inside the pandas module was a real D13 boundary violation, not
  a spec ambiguity. `ToolSettingsError`'s own chunk-01 docstring had already settled this
  exact question for `settings.py`; the fix mirrors it — `ToolArtifactError(Exception)`,
  defined locally, `statlab.errors` import dropped. The boundary tests had only ever covered
  `contracts/`, so neither direction between `backend/` and `mcp_server/` had a test; both
  directions are required tests now.
- `create_artifact` minted its own id independently of the ref used to build the file path.
  Reproduced: the row's id pointed at a file that did not exist, while the real file's ref
  went unregistered. `new_artifact_ref()` now lives in `statlab_contracts.artifacts` (the one
  package both processes may import, `secrets.token_hex(8)`), is the only minter, and
  `new_id("a")` raises rather than silently allowing a second one. `create_artifact` takes
  `ref` and rejects a `meta.ref` that disagrees with it. Invariant tests:
  `row.id == Path(row.path).stem`, `meta.ref == row.id`.

`save_dataframe`/`save_model`/`save_plot`/`save_json`/`save_text` now return `ArtifactMeta`
directly and take `ref`/`label` (and `kind`, where one writer serves several kinds). Zero test
call sites build an `ArtifactMeta` by hand after a save now; 36 call sites in
`test_tool_artifacts.py` alone stopped needing it.

**The general lesson, worth carrying into every remaining chunk:** a test that confirms a
guard exists is not the same as a test that demonstrates the failure the guard prevents.
`UNIQUE` over a nullable column, a `PRIMARY KEY` accepting `NULL`, a boundary test covering one
of three directions, a registry row whose id and path can silently disagree — all four read as
correct and were green. The fix each time was the same shape as chunk 05's `IntegrityError`
probes and this chunk's P10/P11: insert the duplicate, delete the parent, import across the
boundary, mint the id twice — and check the property holds, not that the code exists.

## Chunk 07, complete: MCP server foundation (M1)

Streamable HTTP app, static-bearer Starlette middleware, unauthenticated `/health`, and the
`@tool` decorator every later tool (~80 of them, chunks 08-20) inherits: registration,
mandatory docstring + parameter-description enforcement at decoration time (D23), the uniform
envelope, exception classification into `error_class`, timing, and the D17 fit semaphore. Two
v1 tools, `list_artifacts`/`describe_artifact`, prove the decorator against real bodies.

**M1 passed literally, not just by inference:** `python -m statlab_tools` starts and serves
`/health`; a tokenless request to `/mcp` is rejected; an external MCP client (a real
`ClientSession` over real HTTP, not `httpx.ASGITransport`) completes `tools/list` and a
round-trip `call_tool`. M7 additionally proves the envelope chunk 02 defined is the one the
server actually speaks: `structured_content["result"]` round-trips through
`TOOL_RESULT_ADAPTER` for both the success and failure shapes.

**Two production bugs found while writing tests, not left latent:**
- `_failure_from_exception` used `exc.message` for `ToolArtifactError` instead of `str(exc)`,
  silently dropping the path prefix `ToolArtifactError.__str__` adds — a transient-IO failure
  reported with no path is a message the agent cannot act on. T22 (an unknown run directory
  must name the path) caught it.
- `describe_artifact`'s head rows put `pandas.Timestamp` cells straight into `summary`, which
  is not a valid `JsonValue`. Fixed via `hasattr(value, "isoformat")` duck-typing rather than
  importing pandas into the tools module — chunk 06's "pandas in exactly one module" rule
  holds; only `mcp_server/statlab_tools/artifacts.py` (content IO) imports it.
- A third, caught only by the *project-wide* mypy run, not file-by-file during development:
  both tools built `summary` values as `list[dict[str, str]]`/`dict[str, str]`, structurally
  narrower than the `dict[str, JsonValue]` `ToolSuccess.summary` actually requires. Fixed by
  typing the intermediate collections against `JsonValue` directly. Worth remembering for
  chunk 08 onward: **run `make typecheck` project-wide before trusting a file-scoped mypy
  pass** — this one only surfaced once every new module was checked together.

**Two MCP SDK behaviours the recorded facts didn't cover, both discovered empirically:**
- Every tool module has `from __future__ import annotations` (project convention), so raw
  `inspect.signature(func).parameters[x].annotation` is a *string* at runtime, not a real
  `Annotated[...]` object. `get_type_hints(func, include_extras=True)` is required everywhere
  `tool.py` reads a parameter's `Field` metadata — without it, neither the decoration-time
  check nor the served schema ever see a real annotation.
- `structured_content` stays `None` unless the registered callable's actual return-type
  annotation is a real `ToolSuccess | ToolFailure` object, not inherited from a
  `**kwargs`-typed wrapper and not left as a string. `_build_schema_fn` sets
  `return_annotation=ToolSuccess | ToolFailure` explicitly for exactly this reason.

**A structural pytest-asyncio incompatibility, reproduced independent of anything
MCP-specific:** an async-generator fixture that holds an `anyio` task-group-based context
manager open across its `yield` fails on teardown with "Attempted to exit cancel scope in a
different task than it was entered in" — `pytest-asyncio` resumes the teardown segment as a
different `asyncio` task than the one that ran setup, even within the same event loop.
Confirmed with a bare `anyio.create_task_group()` fixture before touching any MCP code. Fixed
by making `mcp_session` a *synchronous* fixture that returns an `@asynccontextmanager`
factory; every test opens its own session with `async with mcp_session() as session:`, so the
whole session lifetime stays inside the test's own task. Worth remembering for chunk 21
(`mcp_client`) and any later fixture wrapping a real MCP session.

Next: chunk 08 (`load_csv`, `infer_column_roles`, `validate_contract`, `detect_id_columns`,
`detect_leaky_columns`) — the first tools written against this decorator for real.

**Addendum (§8), resolved.** The type-based `RunPaths` detection landed clean: `tool.py` now
finds the parameter by `get_type_hints(...)[name] is RunPaths`, raises at decoration time on
zero or two matches, and the two real tools needed no changes at all — direct proof the
refactor was behaviour-preserving, not just an assertion of it.

The public-import fix did not land, for a reason worth remembering past this chunk:
`mcp.client.streamable_http.create_mcp_http_client` and `mcp.shared._httpx_utils`'s copy are
the same object at runtime, but the former module never lists the name in an `__all__`, and
this project's `pyproject.toml` sets `no_implicit_reexport = true` explicitly. Runtime-identity
and mypy-strict-clean are two different questions, and an SDK path can pass one and fail the
other. `mcp_client` (chunk 21) will hit the same import; the private path stays correct until
the SDK's own `__all__` changes.

---

## Conventions that are binding and non-obvious

`00_index.md` has the full list. These are the ones most likely to be violated by someone who has
not read it:

- **Derive, don't duplicate-and-validate.** If field A is fully determined by B, don't store both.
  This has been violated twice and both times the "fix" was deletion.
- **`model_validate({...})` in negative tests**, kwargs in production and positive tests. Under
  `init_typed`, kwargs are type-checked, so a deliberately-wrong kwarg needs a suppression.
- **Typed keyword-parameter test helpers**, never `def helper(**overrides: object)` — that shape
  forces `**dict` construction and an `arg-type` suppression. The surviving `**overrides` helper in
  `tests/test_domains_base.py` is *not* the pattern to copy.
- **Derive schema and exhaustiveness assertions**, never hand-list them. Use `get_args`,
  `model_fields`, `PRAGMA table_info`, `PRAGMA foreign_key_list`. A hand-typed list is a second
  place to forget to update.
- **`tuple`, not `list`, on frozen models** — `frozen=True` does not freeze list contents.
- **Boundary isolation:** `backend/` and `mcp_server/` never import each other; both may import
  `statlab_contracts`, which imports only stdlib + pydantic. `mcp_server/` must never import
  `statlab.persistence` — the MCP server is stateless (D26). Source-scan tests enforce all three.
- **`disallow_any_explicit` must stay OFF** — it is incompatible with the pydantic mypy plugin for
  *every* `BaseModel`. `tests/test_no_explicit_any.py` replaces it and is stronger.
- **No unreachable code, and therefore no `pragma: no cover`.** If a line cannot be reached, delete
  it. If you want to bypass a validator to reach a line, that line is the problem.
- **`# type: ignore` is banned in production code and permitted in exactly one test situation:**
  when a runtime test must deliberately violate a static type. Mutating a frozen model to prove it
  raises, passing an invalid `Literal` to prove the runtime rejects it, or passing an extra kwarg to
  prove `extra="forbid"` fires — mypy is *correct* to object in all three, so the suppression is the
  test working, not the test cheating. There are 15, each with an inline justification naming why.
  Precedent: chunk 02's `retry_budget("nonsense")`.

  Do not "clean these up". Do challenge any new one that is not one of those three shapes — in
  particular `arg-type` on a `**dict` construction, which means a test helper took
  `**overrides: object` instead of typed keyword parameters.

---

## Corrections so far, and the pattern in them

Five defects have been found and fixed. Four were mine, in the specs; one was latent in `PLAN.md`.

| # | Defect | Resolution |
|---|---|---|
| 1 | `disallow_any_explicit` mandated without testing it against a model | Flag removed; source-scan test instead |
| 2 | `retryable` field on `Failure`, fully determined by `error_class`, policed by a validator | Field deleted; derived helper functions |
| 3 | Chunk 04's rule S7, redundant with S3, kept for message quality | Rule deleted; also removed an unreachable `AssertionError` |
| 4 | Two node-status vocabularies sharing one name since `PLAN.md` §10.2 was written | `NodeExecutionStatus` derived from `NodeStatus` |
| 5 | `UNIQUE` and `PRIMARY KEY` that read as present but enforced nothing over NULLs | `NOT NULL` on all ids; expression index over `IFNULL(candidate_id, '')` |

**1–3 share a shape:** adding a mechanism that *reads* as rigour — an extra flag, an extra field, an
extra rule — where the correct move was subtraction. 2 and 3 are the same error twice, which is why
it is written down here. The detector: **if a check can only be satisfied trivially, or can never
fire, it is not a check.**

**4–5 share a different shape:** both came from writing a schema as *names* rather than *types*. A
markdown table saying `node ∈ {pending, running, ...}` or `UNIQUE(run_id, candidate_id, ...)` never
forces the question "is this enforceable?" or "is this the same set as that one?" — an executed
`CREATE TABLE` or a `Literal` union does, immediately. Both were invisible until something had to
execute against them.

---

## Deferred deliberately

Not oversights. Each is waiting on something that does not exist yet.

| Deferred | Lands in | Waiting on |
|---|---|---|
| Estimator-name validation, model-family cap (D28), `max_candidates` | 27 | The registry (13) and the contract together |
| Candidate id minting (`c_01`, sequential and human-readable) | 22 | The engine that creates waves |
| Repositories for 11 of the 15 tables | Whichever chunk first writes to each | Callers that exist |
| Provider-name env check (`DEEPSEEK_API_KEY`) | 26 | D18's rule that no provider is named outside `llm/` |
| Cancelling a *running* node | 31 | Resolves live rows to `failed`; needs no new status |
| `skills/modelling.md` heuristics and pitfalls | **the human, by hand** | Nodes existing first (D12, §9.3). Chunk 26 scaffolds headings with `TODO:` only |

---

## Two named checkpoints ahead

From `00_index.md`: **chunk 15** (walking skeleton — first end-to-end run) and **chunk 24**. Treat
both as integration gates, not as ordinary chunks. If the architecture is wrong somewhere, 15 is
where it shows.
