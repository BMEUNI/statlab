---
status: in_progress
dependencies: [01, 02, 06]
completion_date: 2026-09-11
implementation_notes: |
  Implemented as specified. `tool.py` (the `@tool` decorator, `ToolParamError`/`ToolDataError`,
  registration, exception classification, the D17 semaphore, `project`/`run` injection),
  `server.py` (`create_app`, bearer middleware, `/health`), `__main__.py` (exit 2 on
  `ToolSettingsError`), `tools/artifacts.py` (`list_artifacts`, `describe_artifact`).

  1105 tests total (1104 passed, 1 skipped -- the pre-existing chunk 06 F9 skip; none newly
  skipped this chunk). 100% line+branch coverage. mypy --strict clean on 60 source files
  (up from 50). ruff clean.

  Two production bugs found and fixed while writing tests, not left latent:
  - `_failure_from_exception` used `exc.message` for `ToolArtifactError` instead of
    `str(exc)`, silently dropping the path prefix `ToolArtifactError.__str__` adds -- a
    transient-IO failure without the path it happened on. Found by T22 (unknown run
    directory names the path).
  - `describe_artifact`'s `head` rows put `pandas.Timestamp` cells straight into `summary`,
    which is not a valid `JsonValue` -- fixed via duck-typing on `hasattr(value, "isoformat")`
    rather than importing pandas into this module (the single-pandas-module rule from chunk 06
    stays intact; only `mcp_server/statlab_tools/artifacts.py`, the content-IO module, imports
    it).
  - A third, structural issue surfaced only by the project-wide mypy run (not caught running
    mypy file-by-file during development): `list_artifacts`/`describe_artifact` built
    `summary` values as `list[dict[str, str]]`/`dict[str, str]` rather than typed against
    `JsonValue`, which is structurally narrower than `dict[str, JsonValue]` requires. Fixed by
    typing the intermediate collections as `JsonValue`/`dict[str, JsonValue]` directly.

  Two real MCP SDK behaviours needed before the decorator worked at all, neither obvious from
  the recorded facts:
  - Every tool module has `from __future__ import annotations`, so raw
    `inspect.signature(func).parameters[x].annotation` is a *string* at runtime. Fixed with
    `get_type_hints(func, include_extras=True)` throughout `tool.py` -- without it, neither
    the parameter-description check nor the served schema ever saw a real `Annotated`/`Field`.
  - `structured_content` stays `None` unless the *actual* return-type annotation on the
    registered callable is a real `ToolSuccess | ToolFailure` object, not a string and not
    inherited from a `**kwargs`-typed wrapper. `_build_schema_fn` sets
    `return_annotation=ToolSuccess | ToolFailure` explicitly on the signature it hands to the
    SDK for exactly this reason.

  One structural test-infrastructure incompatibility found and worked around, not swallowed:
  `pytest-asyncio` resumes an async-generator fixture's teardown as a different `asyncio` task
  than the one that ran setup, even within the same event loop -- confirmed with a minimal
  bare `anyio.create_task_group()` reproduction, independent of anything MCP-specific. Any
  fixture holding an `anyio` task-group-based context manager (streamable HTTP client,
  `ClientSession`) open across its `yield` hits "Attempted to exit cancel scope in a different
  task than it was entered in" on teardown. Fixed by making `mcp_session` a synchronous
  fixture returning an `@asynccontextmanager` factory; every test opens its own session with
  `async with mcp_session() as session:`, keeping the whole lifetime inside the test's own
  task from start to finish.

  A second, unrelated test-isolation bug: `tool.server` is a genuine module-level singleton
  that both `test_tool_decorator.py`'s throwaway tools and the real `list_artifacts`/
  `describe_artifact` register onto. Running the decorator tests before the session tests in
  one process leaked throwaway names into M2's "exactly the two real tools" assertion. Fixed
  with an autouse fixture in `test_tool_decorator.py` that monkeypatches `tool.server` to a
  fresh, throwaway `MCPServer` per test.

  `health` moved out of PLAN.md §6.3's tool catalogue into a note: it is an unauthenticated
  HTTP route on the same ASGI app, not an MCP tool, and forcing an MCP handshake for a
  liveness probe would defeat the point of having one.

  §8 addendum (commit 69e9b30), resolved:

  §8.2 fixed as specified: `tool.py` no longer detects the `RunPaths` parameter by checking
  `name == "paths"`. `_run_paths_param_names` finds it by resolved type
  (`get_type_hints(..., include_extras=True)` already had the type in hand), and
  `_check_run_paths_param_count` raises at decoration time unless the count is exactly one for
  `needs_run=True` or exactly zero for `needs_run=False` -- the naming convention became a
  checked invariant. `list_artifacts`/`describe_artifact` needed zero code changes and their
  existing tests needed zero edits, confirming the refactor was behaviour-preserving. 7 new
  decoration-time tests added (renamed `RunPaths` param, a `str` param literally named `paths`
  kept on the wire, zero/two `RunPaths` params, a stray one under `needs_run=False`, all
  raising and none registering).

  §8.1 attempted as specified, reverted with the reason recorded: `create_mcp_http_client` was
  moved to the `mcp.client.streamable_http` import the addendum named, and the runtime-identity
  check (both names resolve to the same object) held exactly as verified. But
  `mcp.client.streamable_http` imports that symbol for its own internal use without ever
  listing it in an `__all__`, so `mypy --strict`'s `no_implicit_reexport` (set explicitly in
  this project's `pyproject.toml`) rejects the import regardless of import style -- confirmed
  identical failure for `from ... import` and for module-qualified attribute access. Neither
  the SDK's top-level `mcp` package nor `mcp.client` re-exports it either. The two-line change
  was reverted back to `mcp.shared._httpx_utils`, the only mypy-strict-clean option that adds
  no suppression outside the three permitted test shapes, with a comment at both import sites
  recording why and what would allow switching back (the SDK adding this name to that module's
  `__all__`). The private-import grep this addendum's own DoD asks for does not come back
  clean as a result -- this is reported precisely rather than checked off.

  §8.3, §8.4: no code changes; recorded as resolved / deferred per the addendum text.
---

# Chunk 07 — MCP server foundation

**Goal.** A running, authenticated MCP service and the `@tool` decorator every later tool inherits.
`PLAN.md` §6.1, §6.2, D13, D17, D22, D23, milestone M1.

The decorator is the important part. Roughly eighty tools will be written against whatever shape it
has, so a wrong signature here is eighty call sites to fix later. Everything else in this chunk is
small.

---

## 1. Prerequisites & Context

### State after chunk 06 (verified, committed at `4b2ff6b`)

971 tests, 100 % line and branch coverage, mypy clean on 50 files, boundary clean both directions.

| Symbol | Location | Use |
|---|---|---|
| `ToolSuccess`, `ToolFailure`, `ToolResult`, `Failure`, `ErrorClass` | `statlab_contracts/envelope.py` | the envelope the decorator builds. **Verified to serialise through `structured_content` unchanged** |
| `ArtifactRef`, `ArtifactKind`, `ArtifactMeta`, `new_artifact_ref`, `parse_artifact_ref` | `statlab_contracts/artifacts.py` | refs in, refs out |
| `RunPaths`, `ProjectPaths`, `EXTENSION_FOR_KIND` | `statlab_contracts/layout.py` | ref → path |
| `save_*` / `load_*`, `ToolArtifactError` | `statlab_tools/artifacts.py` | content IO; `save_*` already returns `ArtifactMeta` |
| `ToolSettings`, `ToolSettingsError`, `effective_fit_permits` | `statlab_tools/settings.py` | token, host, port, `data_dir`, semaphore size |

### The SDK facts are already recorded — read them, do not re-derive

`00_index.md` has a verified **MCP SDK 2.2.0** block. `mcp` 2.x is a breaking rename from 1.x and
four plausible 1.x assumptions are wrong: `FastMCP` does not exist, attributes are snake_case,
static bearer auth cannot use `token_verifier`, and `httpx.ASGITransport` cannot reach the
authorised path. Every statement there was probed against the installed package.

### One decision this chunk forces that `PLAN.md` does not answer

**How does a stateless tool turn an `ArtifactRef` into a path?** It needs
`RunPaths(data_dir, slug, run_id)`. `data_dir` comes from `ToolSettings`, but slug and run id must
arrive with the call. §6.3's catalogue already shows `list_artifacts(run)` and `snapshot_run(run)`
taking a run, so a run parameter is established; the project is not mentioned anywhere.

**Decision: the decorator injects `project: str` and `run: str` into the schema of every tool that
declares artifact parameters.** Rejected alternatives, with reasons, because this affects all
eighty tools:

- *Resolve the project by globbing `data_dir/*/runs/<run_id>`* — one parameter fewer, but it makes
  every call do a filesystem scan and turns an explicit argument into ambient discovery. Run ids are
  unique enough for it to work, which is what makes it tempting and still wrong.
- *An `X-Statlab-Run` header read from request context* — invisible in the schema, so the tool stops
  being independently testable (§6.1 rule 7) and gains hidden input.
- *MCP `Context` / session state* — directly contradicts D26's stateless server.

Both injected fields carry `description="Supplied by the orchestrator; do not choose a value."`
`mcp_client` (chunk 21) fills them and overwrites anything the model proposes. They are noise in the
schema D23 calls the agent's only documentation, which is the accepted cost of statelessness.

### What this chunk does NOT do

| Not here | Where |
|---|---|
| Any domain tool (`load_csv`, splits, fits) | 08 onward |
| The estimator and inference registries | 13, 19 |
| `mcp_client`, allow-list enforcement, the test-set lock | 21 |
| `snapshot_run`, `prune_intermediate_artifacts` as *tools* | 20 (the primitives exist in chunk 06) |
| Retry, repair, error recovery | 31 |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tool.py` — the decorator and registry |
| create | `mcp_server/statlab_tools/server.py` — `MCPServer`, middleware, health, app factory |
| create | `mcp_server/statlab_tools/__main__.py` — `python -m statlab_tools` |
| create | `mcp_server/statlab_tools/tools/__init__.py` |
| create | `mcp_server/statlab_tools/tools/artifacts.py` — `list_artifacts`, `describe_artifact` |
| **modify** | `Makefile` — `make mcp` runs the module |
| create | `tests/test_tool_decorator.py` |
| create | `tests/test_server_auth.py` |
| create | `tests/test_server_mcp_session.py` |
| create | `tests/test_tools_artifacts_tools.py` |
| **modify** | `tests/conftest.py` — `live_server`, `mcp_session` fixtures |

No `backend/` change. No `contracts/` change.

---

## 3. Implementation Spec

### 3.1 `tool.py` — the decorator

```python
def tool(
    *,
    phase: str,
    allow_in: Sequence[str],
    heavy: bool = False,
    needs_run: bool = True,
) -> Callable[[ToolFn], ToolFn]:
    """Register a tool with the MCP server, wrapping it in the uniform envelope (§6.1.1).

    The decorated function contains only its own logic. This wrapper owns:
      registration, `phase`/`allow_in`/`heavy` metadata, run-path resolution, the result
      envelope, exception classification, timing, and the D17 semaphore for `heavy=True`.

    `needs_run=False` is for tools with no artifact input — `health`-adjacent utilities. It
    suppresses the injected `project`/`run` parameters.
    """
```

Responsibilities, in order:

1. **Metadata.** Pass `meta={"phase": phase, "allow_in": list(allow_in), "heavy": heavy}` to
   `server.tool`. Verified to arrive at the client as `Tool.meta`, which is how D12's allow-list
   reaches `mcp_client` without a second registry.
2. **Description.** Use the function's docstring as `description`. Raise at import time if it is
   missing — D23 makes the schema the agent's only documentation, so an undocumented tool is a
   defect that must fail the build, not ship silently.
3. **Parameter descriptions.** Raise at import time if any parameter lacks a `Field(description=)`.
   Same reason. This is the single most valuable check in the chunk: it is enforced once and gets
   eighty tools right.
4. **Run resolution.** When `needs_run`, build `RunPaths(settings.data_dir, project, run)` and make
   it available to the body. The body receives resolved paths, never raw refs.
5. **Envelope.** A body returns `ToolSuccess` or raises. Never returns `ToolFailure` directly —
   raising is what a tool body naturally does, and one conversion point beats eighty.
6. **Exception classification** into `ErrorClass`, per §6.1.1's table:

   | Raised | `error_class` | Note |
   |---|---|---|
   | `ToolParamError` (new, in `tool.py`) | `tool_param` | carries mandatory `field_errors` |
   | `ToolDataError` (new) | `data` | the data cannot support the request |
   | `ToolArtifactError` (chunk 06), `OSError`, `TimeoutError` | `transient` | IO and timeouts |
   | anything else | `bug` | **traceback to the server log only, never into `message`** (§6.1.1) |

7. **Timing.** Wall-clock milliseconds into the log line. Not into `summary` — a duration is not a
   result, and putting it there would make every tool's output non-deterministic and unfit for the
   `pipeline.py` comparison in chunk 38.
8. **Semaphore.** `heavy=True` acquires from a module-level `anyio.Semaphore(settings.effective_fit_permits)`
   (D17). Acquired *inside* the wrapper so a queued fit does not hold an HTTP worker idle.

**Registration must not require a central list.** Decorating registers; `tools/__init__.py` imports
each module; `server.py` imports the package. Adding a tool is one decorated function (§6.2). A test
asserts no module contains a list of tool names.

### 3.2 `server.py`

```python
def create_app(settings: ToolSettings) -> Starlette:
    """Build the ASGI app: MCP over Streamable HTTP at /mcp, plus an unauthenticated /health."""
```

- `MCPServer(name="statlab-tools", version=...)`, then `streamable_http_app(streamable_http_path="/mcp")`.
- **Bearer middleware** (Starlette `BaseHTTPMiddleware`) comparing against
  `settings.mcp_token.get_secret_value()` with `secrets.compare_digest` — a plain `==` on a secret
  is a timing side channel, and this is the one place in the project where that applies.
- Return **401** with `{"error": "unauthorized"}` and no detail. Never state whether the token was
  absent or wrong; that distinction only helps someone guessing.
- `/health` is exempt and returns `200 "ok"`. **Unauthenticated by design**: a liveness probe that
  needs a secret fails for reasons unrelated to liveness, and `make dev` must be able to poll it.
  It returns the literal string only — no version, no config, no tool count. It is loopback-bound
  (D14) and reveals only that a process is up.
- `ToolSettings._guard_bind_address` already refuses a non-loopback host without
  `STATLAB_ALLOW_NONLOCAL_BIND`. Do not re-implement that check; assert it still fires.

### 3.3 `__main__.py`

`python -m statlab_tools` loads settings, builds the app, runs uvicorn on
`settings.host`/`settings.port`. On `ToolSettingsError`, print the message to stderr and exit **2**
— fail fast per D20, and 2 distinguishes a config error from a crash.

`make mcp` invokes it.

### 3.4 `tools/artifacts.py` — the two v1 tools

```python
@tool(phase="artifacts", allow_in=["*"])
def list_artifacts(...) -> ToolSuccess:
    """List every artifact file present in the run's artifact directory."""

@tool(phase="artifacts", allow_in=["*"])
def describe_artifact(artifact: ArtifactRef, kind: ArtifactKind, head_rows: int = 10) -> ToolSuccess:
    """Describe an artifact: shape, dtypes, and the first rows for a frame."""
```

**These list the filesystem, not the database.** The MCP server has no database access — D26 makes
it stateless and the boundary forbids importing `statlab.persistence`. `list_artifacts` reads
`RunPaths.artifacts` with `iterdir`. The backend's registry is the authority on *provenance*; the
server only reports what is on disk. Say so in both docstrings.

`describe_artifact` needs `kind` because the extension alone cannot distinguish `dataframe` from
`predictions`. Dtypes are reported as `str(dtype)` — and per chunk 06, string columns report `str`,
not `object`.

`allow_in=["*"]` means every node. These two are diagnostic, so restricting them by phase would
only obstruct debugging.

---

## 4. Test Suite Spec

Target **~150 new tests**, total ≥ **1105**.

### 4.1 `conftest.py` additions

```python
@pytest.fixture
def live_server(tmp_path, unused_tcp_port) -> Iterator[str]:
    """Run the real app under uvicorn in a thread; yield its base URL.

    Required, not preference: httpx.ASGITransport cannot reach the authorised path because the
    streamable-HTTP session manager needs the ASGI lifespan and fails with
    "Task group is not initialized". See 00_index.md's MCP facts. Poll `srv.started`, and set
    `should_exit` on teardown.
    """

@pytest.fixture
async def mcp_session(live_server) -> AsyncIterator[ClientSession]:
    """An initialised MCP session over real HTTP with a valid bearer token.

    create_mcp_http_client(headers=...) -> streamable_http_client(url, http_client=...) ->
    ClientSession -> initialize(). This is M1's "external MCP client".
    """
```

### 4.2 `test_tool_decorator.py` (~56)

| # | Test | Expect |
|---|---|---|
| T1 | a success body yields `ok=True` with summary and artifacts | |
| T2 | `meta` carries `phase`, `allow_in`, `heavy` | |
| T3 | missing docstring | raises **at decoration time**, naming the function |
| T4 | a parameter without `Field(description=)` | raises at decoration time, naming the parameter |
| T5 | every parameter description reaches `input_schema` | the D23 guarantee |
| T6 | bounds and enums reach `input_schema` | `ge`/`le` → `minimum`/`maximum`; `Literal` → `enum` |
| T7 | `ToolParamError` → `tool_param` with `field_errors` | |
| T8 | `tool_param` without `field_errors` | rejected — chunk 02's `Failure` validator already enforces it; assert the decorator cannot produce it |
| T9 | `ToolDataError` → `data` | |
| T10 | `ToolArtifactError`, `OSError`, `TimeoutError` → `transient` | parametrised |
| T11 | `ValueError`/`KeyError`/`ZeroDivisionError` → `bug` | parametrised |
| T12 | **no traceback in `message`** | raise with a distinctive frame; assert neither the file name nor `Traceback` appears in the envelope |
| T13 | the traceback *is* logged | caplog contains it — §6.1.1 requires it go somewhere |
| T14 | a body returning `ToolFailure` directly | rejected; raising is the contract |
| T15 | duration is logged, absent from `summary` | |
| T16 | `heavy=True` acquires a permit | with `effective_fit_permits=1`, two concurrent heavy calls serialise; a heavy and a light call do not |
| T17 | `heavy=False` never blocks | |
| T18 | permit released on exception | a failing heavy tool does not leak a permit; run it 3× with 1 permit |
| T19 | `needs_run=True` injects `project` and `run` | present in `input_schema`, marked orchestrator-supplied |
| T20 | `needs_run=False` omits them | |
| T21 | body receives a `RunPaths`, never a raw ref | |
| T22 | unknown run directory | `transient`, message names the path |
| T23 | registration needs no central list | source scan: no module holds a tool-name list |
| T24 | duplicate tool name | raises at import |

T3/T4 are the highest-value tests here — they are enforced once and keep eighty later tools honest.

### 4.3 `test_server_auth.py` (~34)

The 401 cases run in-process via `httpx.ASGITransport` (the middleware short-circuits before the
session manager, so no lifespan is needed). The 200 cases need `live_server`.

| # | Test | Expect |
|---|---|---|
| A1 | `POST /mcp` with no `Authorization` | 401 |
| A2 | wrong token | 401 |
| A3 | `Basic` instead of `Bearer` | 401 |
| A4 | `Bearer` with empty value | 401 |
| A5 | token with correct prefix but extra suffix | 401 — pins that comparison is not `startswith` |
| A6 | **response body reveals nothing** | identical body for absent and wrong tokens |
| A7 | valid token | reaches the MCP layer |
| A8 | `GET /health` with no token | 200, body exactly `ok` |
| A9 | `/health` leaks nothing | no version, no port, no tool names, no path |
| A10 | comparison is constant-time | assert `secrets.compare_digest` is used (source scan); do not attempt to time it |
| A11 | non-loopback host without the override | `ToolSettingsError` — chunk 01's guard still fires |
| A12 | non-loopback with the override | permitted |

### 4.4 `test_server_mcp_session.py` (~30) — M1's acceptance

| # | Test | Assertion |
|---|---|---|
| M1 | `initialize` succeeds over real HTTP | |
| M2 | `tools/list` returns both artifact tools | |
| M3 | every listed tool has a non-empty description | across the whole registry |
| M4 | every parameter of every tool has a description | the D23 guarantee, over the real registry rather than a fixture |
| M5 | `Tool.meta` has `phase`, `allow_in`, `heavy` for all | |
| M6 | a full `call_tool` round-trip | `is_error` false, `structured_content["result"]["ok"]` true |
| M7 | the envelope validates as `ToolResult` | feed `structured_content["result"]` to `TOOL_RESULT_ADAPTER` — proves server and client agree on chunk 02's contract |
| M8 | a schema-invalid argument | `is_error` true, and the message is repairable feedback rather than a traceback |
| M9 | two sequential calls on one session | statelessness: no leakage between calls |
| M10 | server survives a tool raising | subsequent calls still work |

M7 is the one that matters: it proves the wire contract chunk 02 defined is the contract the server
actually speaks.

### 4.5 `test_tools_artifacts_tools.py` (~30)

`list_artifacts` on an empty directory, one artifact, many; excludes non-artifact files; reports
kind from the extension. `describe_artifact` returns shape, dtypes and head rows for a frame;
reports `str` (not `object`) for string columns; handles an empty frame; returns
`data`/`transient` appropriately for a missing file; respects `head_rows`; rejects `head_rows=0`.
Plus: neither tool imports `statlab.persistence`, and both docstrings state that the filesystem,
not the registry, is what they report.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **1105**.
- [ ] Coverage **100 %** line and branch on all new modules; project stays 100 %.
- [ ] T3/T4 fail at decoration time, proven by a decorator applied inside `pytest.raises`.
- [ ] M4 iterates the real registry, not a fixture.
- [ ] M7 validates the envelope through `TOOL_RESULT_ADAPTER`.
- [ ] T16/T18 prove the semaphore serialises and does not leak.
- [ ] No test uses `httpx.ASGITransport` for an authorised request.

**Static analysis**
- [ ] `make typecheck`, `make lint` exit 0.
- [ ] No `Any`, no `pragma: no cover`. `type: ignore` only in the three permitted test shapes.
- [ ] `mcp_server/` imports no `statlab.*`; `backend/` imports no `statlab_tools.*`. Chunk 06's
      tests already cover this — confirm they still pass.

**Behaviour**
- [ ] `python -m statlab_tools` starts and serves `/health`.
- [ ] A tokenless request is rejected; an external MCP client completes `tools/list` and a call.
      That is milestone M1 verbatim.
- [ ] Exit code 2 on a config error.
- [ ] No traceback reaches any envelope or the event stream.

**Housekeeping**
- [ ] Frontmatter, `00_index.md` chunk 07 row, `WHATS_NEXT.md` state table.
- [ ] `PLAN.md` §6.3: move `health()` out of the tool catalogue into a note that it is an
      unauthenticated HTTP route, with the reason. It is listed as a tool today, which would force
      an MCP handshake for a liveness probe.

### Stop and ask if

- The injected `project`/`run` parameters cannot be added to the schema without hand-writing it.
  That would undermine D23 and I want to know before eighty tools depend on it.
- `heavy` serialisation cannot be tested deterministically. A flaky semaphore test means the design
  is wrong, not that the test needs a sleep.
- Raising-only (no `ToolFailure` returns) turns out to be awkward for some real tool shape.
- Anything in `00_index.md`'s MCP block is wrong. I probed all of it, but the version may differ
  from what you resolve.

### Report back with

1. Test count, coverage, mypy file count, ruff.
2. Whether T3/T4 (missing docstring / missing parameter description) fire at **import** time or
   only at call time, and how you proved it.
3. The `input_schema` of `describe_artifact`, verbatim, so I can see what the agent will read.
4. How you tested the semaphore deterministically.
5. Whether the injected `project`/`run` parameters felt right, or whether writing the two real tools
   suggested a better shape. This is your last cheap chance to change it — chunk 08 starts the
   catalogue.
6. Anything in the recorded MCP facts that did not hold.
7. For chunk 08 (`load_csv`, `infer_column_roles`, `validate_contract`, …): does the decorator give
   a tool body everything it needs, or did you find yourself wanting something it does not provide?

---

## 8. Addendum — two corrections, one resolved question

**Status: gated. Chunk 08 is blocked until this passes.**

### 8.1 Use the public `create_mcp_http_client` path

`tests/conftest.py:348` and `tests/test_server_mcp_session.py:269` import from
`mcp.shared._httpx_utils`. That module is private by the leading underscore, so it carries no
stability guarantee across patch releases — and the public path is right there in `00_index.md`'s
recorded facts:

```python
from mcp.client.streamable_http import create_mcp_http_client
```

Verified: both names resolve to **the same object**, so this is a pure import change with no
behavioural difference. Fix both sites. A private import is the kind of thing that survives until an
SDK patch bump breaks the suite for a reason nobody remembers.

### 8.2 Find the `RunPaths` parameter by type, not by name

Your item 5 named this precisely: *"a naming convention resting on string matching rather than a
type."* It is also a direct violation of `WHATS_NEXT.md`'s convention 5, **no implicit string
conventions** — the same rule that rejected `"mutual_info_k20"` in chunk 04 and the
any-unreserved-key-is-a-column-name shape in chunk 03. You were right to flag it; it is worth fixing
rather than documenting.

`tool.py:151` (`if name == "paths"`) and `tool.py:193` (`if name != "paths"`) become a lookup on the
resolved annotation. You already call `get_type_hints(..., include_extras=True)`, so the type is in
hand:

```python
_run_paths_params = [n for n, t in hints.items() if t is RunPaths]
```

Verified against five signatures. Type matching fixes three cases name matching gets wrong:

| Signature | Name match | Type match |
|---|---|---|
| `def t(paths: RunPaths, ...)` | finds it | finds it |
| `def t(p: RunPaths, ...)` | finds **nothing** — silently skips injection | finds `p` |
| `def t(paths: Annotated[str, Field(...)])` | strips a **real wire parameter** | correctly finds none |
| `def t(a: RunPaths, b: RunPaths)` | undetectable | finds two, so it can be rejected |

The third row is a live footgun, not a hypothetical: a tool taking a list of file paths would have
that parameter silently deleted from its schema, and the agent would then be unable to call it
correctly with no indication why.

**Raise at decoration time** when `needs_run=True` and the count is not exactly one, and when
`needs_run=False` and the count is not zero. That converts a naming convention into a checked
invariant, consistent with T3/T4 already failing the build for a missing description.

The parameter may then be named anything. Keep `paths` by convention in the tools you have written —
the point is that the decorator no longer *depends* on it.

### 8.3 Resolved, do not change: keep the session-based transport

`streamable_http_app(stateless_http=True)` exists and I tested it. **Keep the default.**

D26's statelessness is a property of *tools* — no application state between calls — which is already
true and enforced by the tools reading only artifacts. An MCP transport session holds protocol state
(session id, negotiated version), which is a different thing and contradicts nothing. Measured cost
of switching: `stateless_http=True` negotiated protocol revision **2025-11-25** against the
session-based default's **2026-07-28**. Trading a newer protocol revision for a redundant restatement
of a guarantee we already have is a bad trade.

Recorded here so it is not re-litigated in a later chunk.

### 8.4 Deferred to chunk 08, not resolved here

Your item 7 stands: `load_csv` takes a filesystem path rather than an artifact ref, and whether that
path needs its own validation shape — must resolve inside an allowed upload directory, no traversal —
is chunk 08's decision. Note that §8.2 removes the incidental hazard, since a parameter named `path`
or `paths` is now safe regardless.

### 8.5 Definition of Done for the addendum

- [ ] `make test` green; count roughly unchanged plus the new decoration-time tests.
- [ ] Coverage stays 100 % line and branch.
- [ ] `make typecheck`, `make lint` clean, no new suppressions.
- [ ] `grep -rn 'mcp\.shared\._\|mcp\._' tests/ backend/ contracts/ mcp_server/` returns nothing.
- [ ] No `== "paths"` or `!= "paths"` remains in `tool.py`.
- [ ] New tests: a `RunPaths` parameter named something other than `paths` works; a tool with a
      `str` parameter named `paths` keeps it on the wire; `needs_run=True` with zero and with two
      `RunPaths` parameters each raise at decoration time; `needs_run=False` with one raises.
- [ ] The two artifact tools still pass unchanged, proving this is a refactor.

### Report back with

1. Test count, coverage, mypy file count, ruff.
2. Confirmation that the private-import grep is clean.
3. Whether type-based detection needed anything beyond the `get_type_hints` call you already make.
4. Whether any existing test had to change — if the two tools' tests needed edits, the refactor was
   not behaviour-preserving and I want to know where.

---

## 9. Addendum — three production suppressions the DoD forbade

**Status: gated. Chunk 08 is blocked until this passes.**

§5 said "no `Any`, no `pragma: no cover`, `type: ignore` only in the three permitted test shapes."
There are three in **production** code, none reported and none justified inline:

```
mcp_server/statlab_tools/server.py:41   super().__init__(app)                    # type: ignore[arg-type]
mcp_server/statlab_tools/tool.py:267    schema_fn.__signature__ = ...            # type: ignore[attr-defined]
mcp_server/statlab_tools/tool.py:365    wrapper.__signature__ = ...              # type: ignore[attr-defined]
```

They are not equivalent. One is avoidable, two are not.

### 9.1 `server.py:41` — avoidable, self-inflicted

`_BearerAuthMiddleware.__init__` declares `app: object`, so passing it to
`BaseHTTPMiddleware.__init__` cannot type-check. Starlette's own signature is properly typed —
`BaseHTTPMiddleware.__init__(self, app: ASGIApp, dispatch: DispatchFunction | None = None)`. The
suppression exists only because the parameter was widened to `object`.

Fix: `from starlette.types import ASGIApp`, then `def __init__(self, app: ASGIApp, *, token: str)`.
Verified in isolation — the correctly-typed version produces no error, the `object`-typed version
produces exactly one. Delete the suppression.

### 9.2 `tool.py:267` and `:365` — a genuine stub gap; keep, but justify

Assigning `__signature__` on a function object is the documented way to change what
`inspect.signature` reports, and the runtime honours it: after assignment,
`inspect.signature(f)` returns the replacement. mypy's function type simply does not declare the
attribute as writable, so `attr-defined` is unavoidable without a `cast` that would obscure more than
it fixes. Verified both halves: mypy errors, and the runtime respects the assignment.

Keep both, and add the inline justification every other suppression in the project carries. A bare
`# type: ignore[...]` is indistinguishable from laziness six months later; the comment is what makes
it reviewable.

### 9.3 This is a fourth permitted shape, and it is now written down

The three permitted shapes were all test-only: frozen mutation, invalid `Literal`, extra kwarg. A
**stdlib or third-party stub gap in production code** is a fourth, and it is legitimate — but only
under conditions, because "the stubs are wrong" is also the easiest excuse available:

1. The runtime behaviour must be **verified**, not assumed. Assign the attribute, call the function,
   and confirm the effect.
2. There must be **no correctly-typed alternative**. §9.1 is the counter-example: an alternative
   existed and the suppression was masking a widened parameter.
3. It carries an **inline comment** naming the gap and what would remove it.

`WHATS_NEXT.md` records this so later chunks do not re-litigate it — and so a reviewer can tell a
real stub gap from a shortcut.

### 9.4 Definition of Done

- [ ] `make test` green, count unchanged — this is typing only, no behaviour change.
- [ ] Coverage stays 100 % line and branch.
- [ ] `make typecheck`, `make lint` clean.
- [ ] `grep -rnE '# type: ignore\[' backend contracts mcp_server` returns exactly **two** lines, both
      `tool.py`'s `__signature__` assignments, both with an inline justification.
- [ ] `_BearerAuthMiddleware.__init__` takes `app: ASGIApp`; the auth tests pass unchanged.

### Report back with

1. Test count, coverage, mypy file count, ruff.
2. The suppression grep, verbatim.
3. Whether typing `app: ASGIApp` required any other change — if the tests construct the middleware
   directly with something that is not an `ASGIApp`, say so.
