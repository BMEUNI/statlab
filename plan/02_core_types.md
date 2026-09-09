---
status: completed
dependencies: [01]
completion_date: 2026-09-09
implementation_notes: |
  `disallow_any_explicit` (§3.0): confirmed root cause is NOT JsonValue or recursive unions —
  every BaseModel subclass trips it, including `class Trivial(BaseModel): a: int`, because the
  pydantic mypy plugin synthesizes `__init__(**data: Any)` per model and the flag correctly
  flags that synthesized Any. Removed the flag entirely; kept `plugins = ["pydantic.mypy"]`
  (hand-verified it still reports `[call-arg]` on `ToolSuccess(summry={})`, naming the typo).
  Replaced with tests/test_no_explicit_any.py: a source-text regex scan over
  backend/statlab, mcp_server/statlab_tools, contracts/statlab_contracts for the bare token
  `Any`, with two self-check tests (Y2 matches a known-Any line; Y4 asserts the walk found > 0
  files) so the scan cannot silently pass by scanning nothing. Decision on the comment case
  (Y3): a bare `Any` inside a `#` comment DOES count as a violation — the detector is a text
  scan, not a parser, and carving out a comment exception would make the guarantee gameable
  (move the real annotation into a trailing comment to escape it).

  pydantic.JsonValue itself was then used for summary fields exactly as originally specified,
  with zero suppressions, once the flag was gone — confirming it was never JsonValue's fault.

  N9 (metrics with a non-float value): pinned by test. Pydantic's lax numeric coercion accepts
  a numeric string ("0.9" -> 0.9, coerced to float) but rejects a genuinely non-numeric string
  ("not-a-number" -> ValidationError). Both branches are asserted in
  test_node_result_metrics_non_float_value_pinned.

  X10 (no error module imports a heavy library): kept as a subprocess check
  (sys.executable -c "...") rather than an in-process sys.modules diff, because the test
  session already imports pandas/httpx/mcp via other test files' fixtures, which would
  contaminate an in-process check. A subprocess gives a clean interpreter where only
  statlab.errors (and its statlab_contracts dependency) has been imported before the assertion.

  statlab_contracts/ deviation from PLAN.md §3.2's two-package layout: justified because
  ToolResult crosses the backend/mcp_server process boundary that chunk 01's D13 (T9/T10)
  established as import-forbidden in both directions. Two independent definitions of the same
  wire envelope would drift silently (a parse succeeding on a shape neither producer nor
  consumer actually agreed on) rather than loudly, unlike chunk 01's six-line loopback guard,
  which is policy duplicated on purpose because its drift is visible and harmless. Both
  backend/statlab and mcp_server/statlab_tools may import statlab_contracts; neither may
  import the other; statlab_contracts imports nothing but pydantic and stdlib (enforced by
  test_contracts_imports_only_pydantic_and_stdlib, mirroring the amended T9).

  Mid-chunk correction: `Failure.retryable` (and its `_retryable_matches_class` validator) was
  removed after implementation. It was duplicated state — fully determined by `error_class` via
  RETRY_BUDGET, so the validator could only ever agree with its own source of truth or be a
  bug — and it directly contradicted PLAN.md §6.1.1's own failure-envelope example, which is how
  the redundancy surfaced. Retryability is now derived only via `retry_budget(error_class)` and
  `is_retryable(error_class)` in statlab_contracts.envelope; a tool cannot state it because a
  tool cannot know it (whether a retry is still permitted depends on how many attempts already
  happened, which is the recovery handler's state in chunk 31, not the tool's). `Failure` now
  rejects an unexplained `retryable=` kwarg via `extra="forbid"` rather than silently accepting
  and ignoring it — hand-verified: `Failure(error_class="data", message="m", retryable=True)`
  raises `ValidationError` with `extra_forbidden`, naming the field. PLAN.md §6.1.1 was updated
  to match (no `retryable` in the example JSON, with an explanatory note).

  mypy + pydantic-plugin quirk worth flagging to chunk 03: the plugin does not statically
  enforce a BaseModel's `Literal`-typed field against its declared value set, nor coerce/reject
  type mismatches on constructor kwargs the way a plain dataclass or function would — e.g.
  `ArtifactMeta(kind="spreadsheet")` and `Failure(error_class="oops", ...)` both type-check
  clean under mypy --strict and only fail at runtime via ValidationError. Every
  ValidationError-triggering test in this chunk therefore needed no `# type: ignore` at all;
  ignores were only needed for the plain-dataclass NodeSpec cases and for genuinely-mutating
  frozen-model/dataclass tests. Do not add speculative type-ignores to a
  ValidationError-expecting pydantic constructor call without checking mypy first.

  Final numbers: 248 tests total (129 new + 119 from chunk 01, unmodified except T9's amended
  docstring), 100% line and branch coverage project-wide, mypy clean on 21 source files (up
  from 11), ruff clean.
---

# Chunk 02 — Core types: the wire envelope, error taxonomy, and node interfaces

**Goal.** Freeze the types that three later chunks all consume, before any of them exists.
`ToolResult` is produced by chunk 07 and parsed by chunk 21. `NodeSpec`/`NodeResult` are declared
by chunk 27 and driven by chunk 22. `Failure.error_class` is emitted by chunk 07 and dispatched on
by chunk 31. If each of those chunks invents its own version, they will disagree in ways that only
surface at integration.

This chunk implements **M0.5** and `PLAN.md` §4.3.1 + §6.1.1.

**It contains no behaviour beyond validation.** No IO, no HTTP, no pandas, no business logic.

---

## 1. Prerequisites & Context

### State after chunk 01 (verified)

**Canonical names — do not "correct" these.** The distribution is `modelling-agent`
(`pyproject.toml` `name`). The import packages are `statlab` (backend) and `statlab_tools`
(MCP server), living under `backend/` and `mcp_server/`. The new package this chunk adds is
`statlab_contracts`, consistent with those. The master architecture document now lives **only**
at `plan/PLAN.md`; there is no copy at the repository root.

```
pyproject.toml                          # name="modelling-agent", 3.14.3, all deps pinned ==,
                                        #   mypy strict + plugins=["pydantic.mypy"]
uv.lock                                 # committed
backend/statlab/__init__.py
backend/statlab/errors.py               # StatlabError, ConfigError  ← YOU EXTEND THIS
backend/statlab/config.py               # Settings, load_settings, get_settings, ensure_writable
backend/statlab/py.typed
mcp_server/statlab_tools/__init__.py
mcp_server/statlab_tools/settings.py    # ToolSettings, ToolSettingsError, load_tool_settings
mcp_server/statlab_tools/py.typed
tests/conftest.py                       # _clean_env, _clear_settings_cache, empty_env_file, valid_env
tests/test_errors.py                    # 8 tests
tests/test_config.py                    # 45 tests
tests/test_tool_settings.py             # 44 tests
tests/test_dependencies.py              # 22 tests
Makefile                                # help install check-env test lint format typecheck api mcp ui dev db-reset
```

119 tests, 100 % line + branch coverage, `mypy --strict` clean on **11** source files, ruff clean.
That file count must rise once `contracts/` is registered — it is a DoD check below.

### Environment facts you must respect

See `plan/00_index.md` → "Verified environment". The ones that bind this chunk:

- `pydantic==2.13.5`. The following are **verified working** on this exact version — use them,
  do not substitute: `pydantic.JsonValue`, discriminated unions on a `Literal[True]`/
  `Literal[False]` field, `ConfigDict(frozen=True, extra="forbid")`, `NewType` over `str` inside
  a validated model, and `TypeAdapter` for a union alias.
- `mypy==2.3.1` with `strict = true` and `plugins = ["pydantic.mypy"]`. **`disallow_any_explicit`
  is removed by this chunk** — see §3.0. Do not re-add it.
- `filterwarnings = ["error"]` — keep it green with zero new ignores.

### Design constraint that shapes this chunk

Chunk 01 established, and tests T9/T10 enforce, that **`mcp_server` imports nothing from
`backend` and vice versa** (D13: the two are independently deployable processes).

But `ToolResult` crosses that boundary: the MCP server produces it, the backend parses it. Two
independent definitions of a wire format **will** drift, and a drifted envelope fails as silent
misparsing rather than a loud error.

**Resolution (binding):** introduce a third, deliberately tiny package that contains *only* wire
types and no logic. Both packages may import it; neither may import the other.

```
contracts/statlab_contracts/     ← NEW. Wire types only. Depends on pydantic and nothing else.
backend/statlab/                 ← may import statlab_contracts
mcp_server/statlab_tools/        ← may import statlab_contracts
```

This is a deliberate deviation from `PLAN.md` §3.2's two-package layout, and a deliberate
departure from chunk 01's precedent of duplicating the six-line loopback guard. The distinction:
that guard is *policy* applied independently in two places, where drift is visible and harmless.
This is a *wire contract*, where drift is invisible and corrupting. Record the deviation in your
implementation notes.

`statlab_contracts` must never grow beyond wire types. If a later chunk wants to put a helper
function, an IO call, or a pandas import in it, that is the signal the type belongs elsewhere.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `contracts/statlab_contracts/__init__.py` |
| create | `contracts/statlab_contracts/py.typed` |
| create | `contracts/statlab_contracts/artifacts.py` |
| create | `contracts/statlab_contracts/envelope.py` |
| create | `backend/statlab/domains/__init__.py` |
| create | `backend/statlab/domains/base.py` |
| **modify** | `backend/statlab/errors.py` — append only, change nothing existing |
| **modify** | `pyproject.toml` — register the new package in three places **and remove `disallow_any_explicit`** (§3.0) |
| **modify** | `backend/statlab/config.py` — delete the class-line `# type: ignore[explicit-any]` (§3.0) |
| **modify** | `mcp_server/statlab_tools/settings.py` — delete the class-line `# type: ignore[explicit-any]` (§3.0) |
| **modify** | `tests/test_tool_settings.py` — amend the T9 boundary assertion |
| create | `tests/test_contracts_artifacts.py` |
| create | `tests/test_contracts_envelope.py` |
| create | `tests/test_domains_base.py` |
| create | `tests/test_errors_extended.py` |
| create | `tests/test_no_explicit_any.py` |

Do **not** create in this chunk: `graph/`, `llm/`, `db.py`, `storage.py`, any `Domain` instance,
any concrete `NodeSpec` list, or `tools/`. Chunk 22 owns the engine, chunk 27 owns the
`tabular_supervised` domain.

### `pyproject.toml` — exact diff targets

```toml
[tool.hatch.build.targets.wheel]
packages = ["backend/statlab", "mcp_server/statlab_tools", "contracts/statlab_contracts"]

[tool.mypy]
files = ["backend", "mcp_server", "contracts", "tests"]
# and DELETE this line entirely:
#   disallow_any_explicit = true

[tool.coverage.run]
source = ["backend/statlab", "mcp_server/statlab_tools", "contracts/statlab_contracts"]
```

Verify importability afterwards:

```bash
.venv/bin/python -c "import statlab_contracts; print(statlab_contracts.__file__)"
```

If it fails, re-run `uv sync --all-groups`. Do **not** fix it with `sys.path` or a `conftest.py`
hack.

---

## 3. Implementation Spec

### 3.0 Resolve the `disallow_any_explicit` conflict FIRST

Chunk 01 set `disallow_any_explicit = true`. That flag is **incompatible with the pydantic mypy
plugin** and must be removed before you write any model. Verified empirically on this exact
toolchain: every `BaseModel` subclass trips `explicit-any`, including
`class Trivial(BaseModel): a: int`, because the plugin synthesizes `__init__(**data: Any)` per
model and the flag correctly flags that synthesized `Any`. It is not about `JsonValue` or
recursive unions.

Do these four things, in order:

1. **Delete `disallow_any_explicit = true`** from `[tool.mypy]` in `pyproject.toml`. Keep every
   other strict setting, and keep `plugins = ["pydantic.mypy"]` — it is what catches a
   constructor typo like `ToolSuccess(summry={})` as `[call-arg]`, which matters more here than
   the flag did.
2. **Delete the two now-unused class-line ignores** left by chunk 01:
   - `backend/statlab/config.py:16` — `class Settings(BaseSettings):  # type: ignore[explicit-any] ...`
   - `mcp_server/statlab_tools/settings.py:40` — same pattern on `ToolSettings`

   With the flag gone these are unused, and `warn_unused_ignores = true` makes an unused ignore
   an **error** — so mypy will fail until you remove them. Self-enforcing; do not work around it.
3. **Add `tests/test_no_explicit_any.py`** (spec in §4.5) to recover the guarantee at source
   level. This is stronger than the flag was: it inspects our source text, so it cannot be
   defeated by a class-line ignore.
4. Confirm `make typecheck` is clean before continuing.

Then use `pydantic.JsonValue` for `summary` fields as originally specified. Do **not** substitute
`dict[str, object]`: `object` accepts non-JSON-serialisable values, moving a failure from parse
time to serialise time, which is exactly wrong for a wire type.

### 3.1 `contracts/statlab_contracts/artifacts.py`

Artifact identity and kind. The *storage* of artifacts is chunk 06; this file only fixes what an
artifact reference looks like on the wire so both processes agree.

```python
"""Artifact identity and kinds. Wire types only — no IO, no storage policy."""

from __future__ import annotations

import re
from typing import Final, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

ArtifactRef = NewType("ArtifactRef", str)
"""An artifact ID. Opaque to the LLM; resolved to a path by chunk 06's registry."""

ARTIFACT_ID_PATTERN: Final = re.compile(r"^a_[0-9a-f]{16}$")
"""`a_` followed by exactly 16 lowercase hex characters. Chunk 06 mints these."""

ArtifactKind = Literal[
    "dataframe",        # parquet
    "predictions",      # parquet: one column of predictions, or class probabilities
    "transformer",      # a fitted sklearn transformer (imputer, encoder, scaler)
    "model",            # a fitted sklearn estimator
    "inferential_fit",  # a fitted statsmodels results object
    "curve",            # json: roc / pr / calibration curve data (compute, not render)
    "table",            # json: coefficient table, metrics table, ranking
    "plot",             # png
    "report",           # markdown
]
"""Every artifact kind the tool catalogue (PLAN.md §6.3) can produce.

Deliberately closed. Adding a kind is a one-line change here plus one line in chunk 06's
extension map — which is the point: a new kind cannot be introduced by accident.
"""


def parse_artifact_ref(value: str) -> ArtifactRef:
    """Validate and narrow a raw string to an ArtifactRef.

    Raises
    ------
    ValueError
        If `value` does not match ARTIFACT_ID_PATTERN. Callers at a boundary must wrap this
        into a domain error; this module raises no domain errors because it must stay
        importable by both processes.
    """


class ArtifactMeta(BaseModel):
    """What a tool reports about an artifact it produced.

    Small by design: this travels in every tool response and is read by the LLM. It carries
    enough to reason about shape, never enough to reconstruct data (PLAN.md §3.1).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: ArtifactRef
    kind: ArtifactKind
    label: str | None = Field(default=None, max_length=120)
    rows: int | None = Field(default=None, ge=0)
    cols: int | None = Field(default=None, ge=0)
```

Required behaviour:

| Input | Result |
|---|---|
| `parse_artifact_ref("a_0123456789abcdef")` | returns it unchanged |
| `parse_artifact_ref("a_0123456789ABCDEF")` | `ValueError` — uppercase rejected |
| `parse_artifact_ref("a_0123")` | `ValueError` — wrong length |
| `parse_artifact_ref("b_0123456789abcdef")` | `ValueError` — wrong prefix |
| `parse_artifact_ref("")` | `ValueError` |
| `ArtifactMeta(ref=..., kind="dataframe", rows=-1)` | `ValidationError` |
| `ArtifactMeta(..., kind="spreadsheet")` | `ValidationError` |
| `ArtifactMeta(..., bogus=1)` | `ValidationError` — `extra="forbid"` |
| mutating any field | `ValidationError` — frozen |

`ArtifactMeta` does **not** validate `ref` against the pattern. Rationale: `NewType` is a
static-only construct, so runtime validation would need a custom annotated type, and the only
producer of `ref` values is chunk 06's registry, which mints them. Validating at the mint point
is sufficient; validating again on every response is cost with no new information. State this in
a comment so a later reader does not "fix" it.

### 3.2 `contracts/statlab_contracts/envelope.py`

The uniform tool result (PLAN.md §6.1.1). Every tool returns exactly one of these; tools never
raise across the MCP boundary.

```python
"""The tool result envelope. Produced by the MCP server, parsed by the backend."""

from __future__ import annotations

from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

from statlab_contracts.artifacts import ArtifactMeta

ErrorClass = Literal["transient", "tool_param", "data", "bug"]
"""The only dimension the recovery handler (chunk 31) dispatches on.

transient   — network, timeout, 429, transient IO. Retry with backoff.
tool_param  — schema-valid but semantically wrong arguments. Repairable by the agent.
data        — the data cannot support the request. NOT repairable by retrying; escalate
              to the user even in auto mode.
bug         — an unexpected exception. Escalate with a traceback (server log only).
"""

def retry_budget(error_class: ErrorClass) -> int:
    """Attempts permitted for this class. The single source of truth for retryability."""
    return RETRY_BUDGET[error_class]


def is_retryable(error_class: ErrorClass) -> bool:
    """Whether retrying could possibly help. Derived, never carried on the wire.

    `Failure` deliberately has no `retryable` field: it would be fully determined by
    `error_class`, so it could only ever agree or be a bug. A tool also cannot know the answer,
    because "may I retry" depends on how many attempts already happened — the recovery
    handler's state (chunk 31), not the tool's.
    """
    return retry_budget(error_class) > 0


RETRY_BUDGET: Final[dict[ErrorClass, int]] = {
    "transient": 3,
    "tool_param": 2,
    "data": 0,
    "bug": 0,
}
"""Maximum attempts per class, from PLAN.md §6.1.1. Chunk 31 consumes this; it lives here so
the numbers cannot diverge between the taxonomy and the handler that applies it."""


class Failure(BaseModel):
    """The payload of a failed operation.

    Shared by tool failures and node failures (NodeResult.error) so there is exactly one
    failure shape in the system.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_class: ErrorClass
    message: str = Field(min_length=1)
    field_errors: dict[str, str] | None = None

    @model_validator(mode="after")
    def _field_errors_iff_tool_param(self) -> Failure:
        """`field_errors` is mandatory for tool_param and forbidden otherwise.

        This is what makes chunk 21's single repair retry mechanical: the agent is handed
        per-field reasons, not a prose blob to re-interpret.
        """


class ToolSuccess(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[True] = True
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: tuple[ArtifactMeta, ...] = ()
    warnings: tuple[str, ...] = ()


class ToolFailure(Failure):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: Literal[False] = False


ToolResult = Annotated[ToolSuccess | ToolFailure, Field(discriminator="ok")]
"""Discriminated on `ok`, so parsing is unambiguous and errors point at the right branch."""

TOOL_RESULT_ADAPTER: Final = TypeAdapter(ToolResult)
"""The single parse/serialise entry point. Chunk 21 must use this rather than hand-branching
on `ok`, so a malformed envelope fails at one place with one message."""
```

Exact validator semantics:

| `error_class` | `field_errors` | Valid? |
|---|---|---|
| `tool_param` | `{"k": "v"}` | ✅ |
| `tool_param` | `None` | ❌ `ValidationError` |
| `tool_param` | `{}` | ❌ — empty means "no reasons given", which defeats the purpose |
| `transient` | `None` | ✅ |
| `transient` | `{"k": "v"}` | ❌ |
| `data` / `bug` | `None` | ✅ |
| `data` / `bug` | anything | ❌ |

**`Failure` has no `retryable` field, deliberately.** An earlier draft of this spec carried one
plus a validator asserting it agreed with `RETRY_BUDGET`. That validator was a checksum over
duplicated state: a field fully determined by `error_class` can only ever agree or be a bug, so
it adds validation burden and zero information. It also contradicted `PLAN.md` §6.1.1's own
example, which is how the redundancy was found.

Retryability is derived at the one place that needs it via `is_retryable(error_class)`. A
`Failure` therefore cannot misreport it, because it cannot state it. If a `tool_param` failure
truly cannot be fixed by changing arguments, it is a `data` failure — the taxonomy already draws
that line, so no per-failure override is needed.

Use `tuple[...]` rather than `list[...]` for `artifacts` and `warnings`. Rationale: `frozen=True`
freezes attribute assignment but not list contents, so a `list` field on a frozen model is
mutable in practice. `tuple` makes immutability real. Pydantic coerces an incoming JSON array to
a tuple automatically, so the wire format is unaffected.

### 3.3 `backend/statlab/domains/base.py`

The node interface (PLAN.md §4.3.1). Backend-only — the MCP server has no concept of a node.

```python
"""Node interface types. The engine knows only these; it never names a node.

D27's invariant: no node name appears outside `backend/statlab/domains/`. Chunk 27 adds the
`Domain` aggregate and the concrete `tabular_supervised` spine to this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.envelope import Failure

NodeKind = Literal["SHARED", "PER_CANDIDATE"]
"""SHARED runs once per run; PER_CANDIDATE runs once per candidate (PLAN.md §4.2)."""

GatePolicy = Literal["always", "exception", "never"]
"""Guided-mode gating (D29). `always` for shared nodes, `exception` for per-candidate nodes,
`never` for nodes with no user-meaningful decision."""

NodeStatus = Literal["completed", "skipped", "failed", "awaiting_user"]


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """A declared position in the spine. Ordering is the list's; this carries no index.

    A frozen dataclass rather than a pydantic model: these are constructed in code by chunk 27,
    never parsed from untrusted input, so validation would be cost without benefit.
    """

    name: str
    kind: NodeKind
    skippable: bool
    decision_schema: type[BaseModel]
    tool_phases: tuple[str, ...]
    gate_policy: GatePolicy


class NodeResult(BaseModel):
    """What a node hands back to the engine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: NodeStatus
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    metrics: dict[str, float] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    skip_reason: str | None = None
    error: Failure | None = None

    @model_validator(mode="after")
    def _skip_reason_iff_skipped(self) -> NodeResult:
        """A skip must always carry a reason, and only a skip may (D3)."""

    @model_validator(mode="after")
    def _error_iff_failed(self) -> NodeResult:
        """A failure must always carry a Failure, and only a failure may."""

    @model_validator(mode="after")
    def _no_artifacts_on_failure(self) -> NodeResult:
        """A failed or skipped node reports no artifacts and no metrics.

        Artifacts may exist on disk from a partial attempt — chunk 06 keeps them for diagnosis
        (D24) — but they are not part of the run's state, because the engine must not treat a
        half-finished node's output as an input to the next one.
        """
```

Validator matrix:

| `status` | `skip_reason` | `error` | `artifacts` | `metrics` | Valid? |
|---|---|---|---|---|---|
| `completed` | `None` | `None` | any | any | ✅ |
| `skipped` | `"nothing to engineer"` | `None` | `()` | `{}` | ✅ |
| `skipped` | `None` | `None` | `()` | `{}` | ❌ |
| `skipped` | `""` | `None` | `()` | `{}` | ❌ — blank is not a reason |
| `completed` | `"x"` | `None` | any | any | ❌ |
| `failed` | `None` | `Failure(...)` | `()` | `{}` | ✅ |
| `failed` | `None` | `None` | `()` | `{}` | ❌ |
| `completed` | `None` | `Failure(...)` | any | any | ❌ |
| `failed` | `None` | `Failure(...)` | `("a_...",)` | `{}` | ❌ |
| `skipped` | `"x"` | `None` | `()` | `{"auc": 0.9}` | ❌ |
| `awaiting_user` | `None` | `None` | `()` | `{}` | ✅ |

### 3.4 `backend/statlab/errors.py` — extend, do not rewrite

Append. Leave `StatlabError` and `ConfigError` byte-identical; chunk 01's 8 tests must keep
passing unchanged.

```python
class ToolCallError(StatlabError):
    """A tool returned a ToolFailure, or its response could not be parsed.

    Carries the wire failure so the recovery handler (chunk 31) dispatches on `error_class`
    rather than on string matching.
    """

    def __init__(self, tool: str, failure: Failure, *, cause: BaseException | None = None) -> None:
        super().__init__(f"tool {tool!r} failed: {failure.message}", cause=cause)
        self.tool = tool
        self.failure = failure

    @property
    def error_class(self) -> ErrorClass: ...
    @property
    def retry_budget(self) -> int:
        """Attempts allowed for this failure's class, from RETRY_BUDGET."""


class ToolNotAllowedError(StatlabError):
    """A node attempted a tool outside its skill-file allow-list (D12).

    Raised by chunk 21 before dispatch. Not a ToolCallError: the call never happened, and this
    is a programming or skill-file defect rather than a runtime condition.
    """

    def __init__(self, tool: str, node: str, allowed: frozenset[str]) -> None: ...
    tool: str
    node: str
    allowed: frozenset[str]


class ArtifactError(StatlabError):
    """An artifact is missing, unreadable, or of an unexpected kind."""

    def __init__(self, ref: str, message: str, *, cause: BaseException | None = None) -> None: ...
    ref: str


class ArtifactLockedError(ArtifactError):
    """A locked artifact was passed as a tool parameter (D26, PLAN.md §4.2 rule 2).

    The test-set lock. Raised by chunk 21 pre-dispatch. Deliberately its own type so the
    guarantee is greppable and a test can assert this exact class.
    """

    def __init__(self, ref: str, current_node: str) -> None: ...
    current_node: str


class EngineError(StatlabError):
    """An invalid state transition in the run loop. Always a bug in our code."""
```

Rules for this hierarchy:

- Later chunks add **plain** subclasses of `StatlabError` as they need them (`LLMError`,
  `SkillFileError`, …). Chunk 02 defines only errors that carry **structured payloads**, because
  a field-free subclass added later costs nothing and risks nothing, whereas an inconsistent
  payload shape is expensive to unify.
- No error class may import pandas, httpx, or `mcp`. Wrapping happens at the boundary that owns
  those libraries.
- Message convention: lowercase, no trailing period, name the subject in quotes
  (`tool 'split_random' failed: ...`).

### 3.5 `tests/test_tool_settings.py` — amend T9

Chunk 01's T9 asserts `mcp_server` imports nothing from `backend`. That must stay true, and it
must now explicitly permit `statlab_contracts`. Replace the assertion with:

```python
def test_tool_settings_imports_nothing_from_backend() -> None:
    """D13: the MCP server must stay independently deployable.

    `statlab_contracts` is permitted — it is the shared wire contract (chunk 02 §1) and has no
    dependencies of its own. Importing `statlab.*` is not.
    """
    import mcp_server.statlab_tools.settings as mod

    for name, value in vars(mod).items():
        module = getattr(value, "__module__", None) or (
            value.__name__ if isinstance(value, type(mod)) else None
        )
        if module is None:
            continue
        assert not module.startswith("statlab."), f"{name} leaks backend module {module}"
```

Add the mirror test in `tests/test_contracts_envelope.py`:

```python
def test_contracts_imports_only_pydantic_and_stdlib() -> None:
    """statlab_contracts must stay dependency-free so both processes can import it."""
```

It must assert that no name in either contracts module resolves to a module starting with
`statlab.` or `statlab_tools.`, and that no third-party module other than `pydantic` appears.

---

## 4. Test Suite Spec

Target: **~86 new tests**. Existing 119 must still pass untouched (except T9's amended body).

### 4.1 `tests/test_contracts_artifacts.py` (~20)

**`parse_artifact_ref`** — parametrised, each asserting `ValueError` unless noted:

| # | Input | Expect |
|---|---|---|
| A1 | `"a_0123456789abcdef"` | returns unchanged |
| A2 | `"a_ffffffffffffffff"` | returns unchanged |
| A3 | `"a_0123456789ABCDEF"` | raises |
| A4 | `"a_0123456789abcde"` (15) | raises |
| A5 | `"a_0123456789abcdef0"` (17) | raises |
| A6 | `"b_0123456789abcdef"` | raises |
| A7 | `"a0123456789abcdef"` (no underscore) | raises |
| A8 | `""` | raises |
| A9 | `"a_"` | raises |
| A10 | `"a_0123456789abcdeg"` (non-hex) | raises |
| A11 | `" a_0123456789abcdef"` (leading space) | raises |
| A12 | `"a_0123456789abcdef\n"` | raises — anchor the regex with `$`, not a bare match |

A12 exists because `re.match(r"...$")` accepts a trailing newline; use `\Z` or
`fullmatch`. This is the kind of thing that passes review and fails in production.

**`ArtifactMeta`**

| # | Case | Expect |
|---|---|---|
| A13 | minimal valid (`ref`, `kind`) | constructs; `label`/`rows`/`cols` are `None` |
| A14 | all fields | constructs, values preserved |
| A15 | every `ArtifactKind` value | parametrised over all 9, each constructs |
| A16 | unknown kind | `ValidationError` |
| A17 | `rows=-1` | `ValidationError` |
| A18 | `cols=-1` | `ValidationError` |
| A19 | `rows=0` | valid — an empty frame is a real, reportable outcome |
| A20 | `label` 121 chars | `ValidationError` |
| A21 | extra field | `ValidationError` |
| A22 | mutation attempt | `ValidationError` (frozen) |
| A23 | JSON round-trip | `model_validate_json(model_dump_json())` equals the original |

### 4.2 `tests/test_contracts_envelope.py` (~35)

**`ToolSuccess`**

| # | Case | Expect |
|---|---|---|
| E1 | empty (`ok` defaults `True`) | constructs; `summary == {}`, `artifacts == ()`, `warnings == ()` |
| E2 | nested `summary` | `{"a": {"b": [1, 2.5, "x", None, True]}}` preserved exactly |
| E3 | `artifacts` given as a list | coerced to `tuple` |
| E4 | `warnings` given as a list | coerced to `tuple` |
| E5 | `ok=False` on `ToolSuccess` | `ValidationError` |
| E6 | extra field | `ValidationError` |
| E7 | mutation | `ValidationError` |
| E8 | `artifacts` immutable | `isinstance(m.artifacts, tuple)` and `AttributeError` on `.append` |

**`Failure` — `field_errors` rule**, parametrised across the §3.2 matrix (8 rows), each asserting
valid construction or `ValidationError`, and for failures that the message names `field_errors`.

**No `retryable` field**

| # | Case | Expect |
|---|---|---|
| E8a | `Failure(..., retryable=True)` | `ValidationError` — `extra="forbid"` rejects the removed field, so a stale producer fails loudly rather than being silently ignored |
| E8b | `retryable` absent from serialised JSON | assert the key is not in `model_dump()` nor in `dump_json()` |

| # | Case | Expect |
|---|---|---|
| E9 | `message=""` | `ValidationError` (`min_length=1`) |
| E10 | unknown `error_class` | `ValidationError` |
| E11 | `field_errors={}` on `tool_param` | `ValidationError` |
| E12 | `field_errors` with a non-string value | `ValidationError` |

**`RETRY_BUDGET`**

| # | Case | Expect |
|---|---|---|
| E13 | exhaustive over `ErrorClass` | every literal in `ErrorClass` is a key; no extra keys. Derive the literal set via `typing.get_args`, do not hardcode it — the point is that adding a class without a budget fails |
| E14 | values | `transient == 3`, `tool_param == 2`, `data == 0`, `bug == 0` |
| E15 | `retry_budget` delegates | `retry_budget(c) == RETRY_BUDGET[c]` for every class |
| E15a | `is_retryable` | `True` for `transient`/`tool_param`, `False` for `data`/`bug` |
| E15b | `is_retryable` agrees with the budget | `is_retryable(c) == (retry_budget(c) > 0)` for every class, derived via `get_args` |
| E15c | unknown class | `retry_budget("nonsense")` raises `KeyError` — assert it is not silently defaulted to 0, which would make an unmapped class look non-retryable instead of failing

**`TOOL_RESULT_ADAPTER` discrimination**

| # | Input | Expect |
|---|---|---|
| E16 | `{"ok": true, ...}` | `ToolSuccess` |
| E17 | `{"ok": false, "error_class": "data", "message": "m"}` | `ToolFailure` |
| E18 | `ok` absent | `ValidationError` naming the discriminator |
| E19 | `{"ok": "yes"}` | `ValidationError` |
| E20 | success round-trip | `validate_json(dump_json(x)) == x` |
| E21 | failure round-trip | same |
| E22 | failure JSON contains `"ok":false` | assert on the serialised bytes — the wire shape is the contract |
| E23 | unknown field on either branch | `ValidationError` |

**Boundary isolation**

| # | Case | Expect |
|---|---|---|
| E24 | contracts import nothing from `statlab.*` / `statlab_tools.*` | passes |
| E25 | contracts import no third party but `pydantic` | passes |

### 4.3 `tests/test_domains_base.py` (~22)

Define a throwaway `class _Decision(BaseModel): x: int` as the `decision_schema` fixture. Do
**not** import a real decision schema — none exists until chunk 27, and depending on one would
break topological order.

**`NodeSpec`**

| # | Case | Expect |
|---|---|---|
| N1 | construct with all fields | values preserved |
| N2 | frozen | `dataclasses.FrozenInstanceError` on assignment |
| N3 | `slots=True` | `AttributeError` when setting an undeclared attribute |
| N4 | hashable | usable as a dict key (frozen dataclass) |
| N5 | equality by value | two identical specs compare equal |
| N6 | both `NodeKind` values accepted | parametrised |
| N7 | all three `GatePolicy` values accepted | parametrised |

**`NodeResult`** — parametrised over the full §3.3 matrix (11 rows), then:

| # | Case | Expect |
|---|---|---|
| N8 | minimal `completed` | defaults are empty containers, not `None` |
| N9 | `metrics` with a non-float value | `ValidationError`, or coercion to float — assert whichever pydantic does and pin it, do not leave it unasserted |
| N10 | `artifacts` list → tuple | coerced |
| N11 | blank `skip_reason` on skip | `ValidationError` |
| N12 | whitespace-only `skip_reason` | `ValidationError` — reuse chunk 01's blank-token precedent |
| N13 | mutation | `ValidationError` |
| N14 | extra field | `ValidationError` |
| N15 | JSON round-trip with a `Failure` | equal to the original |
| N16 | every `NodeStatus` value | parametrised, each valid with its required companions |

### 4.4 `tests/test_errors_extended.py` (~10)

| # | Case | Expect |
|---|---|---|
| X1 | `ToolCallError` message | contains the tool name in quotes and the failure message |
| X2 | `.error_class` delegates | equals `failure.error_class` |
| X3 | `.retry_budget` delegates | equals `RETRY_BUDGET[failure.error_class]` |
| X4 | `cause` preserved | `.cause is original` |
| X5 | `ToolNotAllowedError` | stores `tool`, `node`, `allowed`; message names all three |
| X6 | `ArtifactError` | stores `ref`; message contains it |
| X7 | `ArtifactLockedError` is an `ArtifactError` | `issubclass` |
| X8 | `ArtifactLockedError` message | mentions the test set and the current node |
| X9 | every new class is a `StatlabError` | parametrised over all six |
| X10 | no error module imports a heavy library | assert `pandas`, `httpx`, `mcp` absent from `sys.modules` additions when importing `statlab.errors` in a subprocess |

X10 needs a subprocess (`sys.executable -c`) because the test session already imports pandas.
Keep it, or drop it and instead assert on the module's source text with a comment explaining why.
Either is acceptable; do not silently omit it.

### 4.5 `tests/test_no_explicit_any.py` (~4)

Replaces `disallow_any_explicit` (§3.0) with a stricter, source-level guarantee.

```python
_ANY_TOKEN = re.compile(r"(?<![\w.])Any(?![\w])")
_PACKAGES = ("backend/statlab", "mcp_server/statlab_tools", "contracts/statlab_contracts")
```

| # | Test | Assertion |
|---|---|---|
| Y1 | no `Any` in any package source | walk every `*.py` under `_PACKAGES`; assert no line matches `_ANY_TOKEN`. Failure message must name file and line so it is actionable |
| Y2 | the detector actually works | run `_ANY_TOKEN` against `"def f(x: Any) -> Any: ..."` and assert it matches — a guard test that never silently passes because the regex broke |
| Y3 | the detector does not false-positive | assert it does **not** match `"Anything"`, `"anyway"`, `"typing.Anyx"`, or a comment containing `"# Any"`… decide and pin the comment case explicitly |
| Y4 | the walk found files | assert the number of scanned files is > 0, so a wrong path cannot make Y1 vacuously pass |

Y2 and Y4 exist because Y1 alone is a test that passes when it is broken. A source-scanning
assertion with no self-check is worse than none: it reports success whether or not it looked.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **205** tests (119 existing + ~86 new).
- [ ] Coverage **100 %** line and branch on `contracts/statlab_contracts/artifacts.py`,
      `contracts/statlab_contracts/envelope.py`, `backend/statlab/domains/base.py`, and the
      appended half of `backend/statlab/errors.py`. Whole-project coverage stays at 100 %.
- [ ] No `pragma: no cover` added. No lowering of `fail_under`.
- [ ] Chunk 01's tests pass unmodified except T9, whose amended body is justified in a docstring.

**Static analysis**
- [ ] `disallow_any_explicit` is **gone** from `pyproject.toml`; the two chunk-01 class-line
      ignores are **deleted**; `make typecheck` exits 0 with no unused-ignore errors.
- [ ] `tests/test_no_explicit_any.py` passes, including its self-check tests Y2 and Y4.
- [ ] The pydantic plugin still catches a bad constructor kwarg — verify once by hand with a
      throwaway `ToolSuccess(summry={})` and confirm mypy reports `[call-arg]`.
- [ ] `make lint` exits 0.
- [ ] `mypy` sees the new `contracts` package (it appears in the "source files" count, which must
      rise from 11).

**Type behaviour verified by test, not by inspection**
- [ ] The `field_errors` validator rejects every invalid combination in §3.2's matrix.
- [ ] `Failure` has no `retryable` field, and passing one raises `ValidationError`.
- [ ] `is_retryable` and `retry_budget` agree with `RETRY_BUDGET` for every `ErrorClass`.
- [ ] All three `NodeResult` validators reject every invalid row in §3.3's matrix.
- [ ] `RETRY_BUDGET` is exhaustive over `ErrorClass`, checked via `typing.get_args`.
- [ ] `TOOL_RESULT_ADAPTER` round-trips both branches losslessly, asserted on serialised JSON.
- [ ] `artifacts` and `warnings` are genuinely immutable (`tuple`, not `list`).

**Boundary**
- [ ] `contracts/` imports nothing but stdlib and `pydantic`.
- [ ] `mcp_server/` imports nothing from `backend/` (amended T9 green).
- [ ] `backend/` does not import `statlab_tools`.
- [ ] `python -c "import statlab, statlab_tools, statlab_contracts"` succeeds.

**Housekeeping**
- [ ] Frontmatter set to `completed` with `completion_date` and implementation notes covering:
      whether `JsonValue` satisfied `disallow_any_explicit`; what pydantic does with a
      non-float `metrics` value (N9); whether X10's subprocess check was kept or replaced; and
      the `statlab_contracts` deviation from `PLAN.md` §3.2 with its justification.
- [ ] `plan/00_index.md` chunk 02 row → `completed`.

---

## 6. Addendum — adopt `init_typed` before chunk 03

Discovered after chunk 02 was implemented and committed. Do this as a **separately gated step**
before starting chunk 03; it is a hardening pass over existing code, not new functionality.

### Why

The pydantic mypy plugin defaults to synthesizing `__init__(**data: Any)`, so **no constructor
argument type is statically checked**. Three consequences, all verified on this toolchain:

1. `ArtifactMeta(kind="spreadsheet")` type-checks clean.
2. `Failure(error_class="oops", ...)` type-checks clean.
3. `ArtifactMeta(ref="a_0123456789abcdef")` type-checks clean even though `ref` is declared
   `ArtifactRef`, a `NewType` over `str`. **The `NewType` is therefore decorative** — the one
   thing it exists to prevent, passing a bare string where a validated ref is required, is
   exactly what it fails to catch.

Point 3 is the reason this cannot wait. Artifact IDs flow through every tool, node and API
response in the remaining 38 chunks. Enforcing the `NewType` costs 26 test-site edits now and
grows more expensive with every chunk that adds an unchecked construction.

### Changes

1. Add to `pyproject.toml`:
   ```toml
   [tool.pydantic-mypy]
   init_typed = true
   ```
   Do **not** re-add `disallow_any_explicit`; `tests/test_no_explicit_any.py` already covers our
   code more strongly.

2. Fix the 26 resulting errors — all in test files, **zero in production code**:
   - **Negative tests** (deliberately invalid values: `kind="spreadsheet"`, `error_class="oops"`,
     `ok=False` on `ToolSuccess`, a non-string `field_errors` value) → switch to
     `Model.model_validate({...})`. No `type: ignore`.
   - **`ref` arguments** → wrap properly: `ArtifactRef("a_0123456789abcdef")`, or add a
     `conftest.py` fixture returning a correctly typed ref and use it everywhere.
   - **List-to-tuple coercion tests** (`artifacts=[...]`, `warnings=[...]`) → these assert a
     *wire* behaviour, so use `model_validate` with a JSON-shaped dict. That is what actually
     happens in production; passing a Python list by kwarg never does.
   - Remove any now-unneeded `# type: ignore`; `warn_unused_ignores = true` will flag stragglers.

3. Do not weaken a signature to make a test compile. If a test cannot be expressed without
   `type: ignore`, it is a `model_validate` case.

### Definition of Done for the addendum

- [ ] `init_typed = true` present; `make typecheck` exits 0 on all 21 files.
- [ ] Zero `# type: ignore` added for `[arg-type]`. Any surviving ignore has an inline
      justification and is not an `arg-type` suppression.
- [ ] `make test` still green at the same count, coverage still 100 %.
- [ ] Hand-verify the guard now works: temporarily write `ArtifactMeta(kind="spreadsheet")` with
      kwargs, confirm mypy reports `[arg-type]`, then delete it.
- [ ] `ArtifactRef` is enforced: `ArtifactMeta(ref="raw string")` is a mypy error.

---

### Explicitly out of scope

No `Domain` aggregate, no concrete spine, no `RunState`, no `graph/`, no `llm/`, no artifact
storage, no `@tool` decorator, no HTTP. Chunk 03 (`DataContract`) is next and depends only on
what this chunk defines.

### Stop and ask if

- Anything in §3.0 does not play out as described — that resolution is already verified on this
  toolchain, so a deviation means something else changed.
- A real case appears that needs per-failure retryability independent of `error_class` — that
  would mean the taxonomy itself is wrong, which is a `PLAN.md` decision, not a chunk detail.
- Making `contracts` importable requires anything beyond the three `pyproject.toml` edits.
