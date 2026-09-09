---
status: untouched
dependencies: []
completion_date: null
implementation_notes: ""
---

# Chunk 01 — Foundation: dependency resolution, configuration, tooling

**Goal.** Turn an empty repo into one where `make test`, `make lint`, `make typecheck` and
`make check-env` all work, on a **pinned, verified** dependency set. Nothing in this chunk knows
about modelling, MCP, or LLMs. It exists so that every later chunk fails for its own reasons
rather than for environment reasons.

This chunk implements **Task 0** and **M0** from `PLAN.md` §2.1 / §13.

---

## 1. Prerequisites & Context

### Pre-existing state

```
/Users/nandordukat/Documents/Egyetem/MSc/Semester_2/Onlab/
├── PLAN.md                 # master architecture, read-only for this chunk
├── plan/                   # this directory
├── pyproject.toml          # EXISTS, minimal: name="onlab", requires-python=">=3.14", dependencies=[]
├── .venv/                  # EXISTS, Python 3.14.3
└── .idea/                  # IDE files, ignore
```

Verified on this machine: **Python 3.14.3**, **uv 0.10.2**, **node v24.13.1**, **GNU make** at
`/usr/bin/make`. Use `uv` for all dependency work — it is present and resolves faster than pip.

### Preceding chunks

None. This is chunk 01.

### Authoritative decisions this chunk implements

| Decision | Requirement |
|---|---|
| §2.1 Task 0 | Pin an exact, co-installable dependency set; `statsmodels` is the canary; fall back to Python 3.12 if any wheel is missing |
| D14 | Loopback-only binding by default; MCP server bearer-token gated |
| D18 invariant | **No module outside `backend/statlab/llm/` may name an LLM provider or model** |
| D20 | `make dev` starts three processes and **fails fast** on missing configuration |
| D17 | Fit-semaphore permit count must be **injectable**, not discovered, for deterministic tests |

### Boundary correction (read before implementing)

`PLAN.md` D20 says `make dev` should fail fast when `DEEPSEEK_API_KEY` is missing. Implementing
that literally in `config.py` would put a provider name in a module outside `llm/`, violating the
D18 invariant that chunk 26 adds a test for.

**Resolution, binding for this chunk:**

- `config.py` and `settings.py` are **provider-free**. They contain no model strings and no
  provider-specific key names.
- `make check-env` validates only provider-neutral configuration: `STATLAB_MCP_TOKEN` present,
  data directory writable, ports in range, bind address loopback.
- The provider-key check (`DEEPSEEK_API_KEY` or whatever the configured provider needs) is added
  in **chunk 25/26**, inside `llm/`, where naming a provider is legal. `make check-env` will call
  into it once it exists; for now the target has a `# chunk 26: append LLM key check` marker
  comment and nothing else.

Do not add provider names to this chunk's files. There is a DoD grep asserting their absence.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| **modify** | `pyproject.toml` |
| create | `.env.example` |
| create | `.gitignore` |
| create | `Makefile` |
| create | `backend/statlab/__init__.py` |
| create | `backend/statlab/py.typed` |
| create | `backend/statlab/errors.py` |
| create | `backend/statlab/config.py` |
| create | `mcp_server/statlab_tools/__init__.py` |
| create | `mcp_server/statlab_tools/py.typed` |
| create | `mcp_server/statlab_tools/settings.py` |
| create | `tests/__init__.py` |
| create | `tests/conftest.py` |
| create | `tests/test_errors.py` |
| create | `tests/test_config.py` |
| create | `tests/test_tool_settings.py` |
| create | `tests/test_dependencies.py` |

Do **not** create `backend/statlab/main.py`, `db.py`, `graph/`, or any other module in this chunk.
Empty scaffolding directories are speculative work; later chunks create their own files.

---

## 3. Implementation Spec

### 3.1 Task 0 — resolve and pin dependencies (do this first)

Run the resolution, then **write back the versions you actually got**. Do not invent version
numbers, and do not use ranges.

```bash
# 1. attempt the full set on Python 3.14
uv venv --python 3.14 .venv
uv pip install \
  fastapi uvicorn[standard] pydantic pydantic-settings python-multipart httpx \
  litellm mcp \
  pandas pyarrow numpy scikit-learn statsmodels matplotlib joblib \
  pytest pytest-cov pytest-asyncio mypy ruff pandas-stubs

# 2. verify every one imports
.venv/bin/python - <<'EOF'
import importlib
mods = ["fastapi","uvicorn","pydantic","pydantic_settings","multipart","httpx",
        "litellm","mcp","pandas","pyarrow","numpy","sklearn","statsmodels",
        "matplotlib","joblib","pytest","mypy","ruff"]
for m in mods:
    importlib.import_module(m)
    print("ok", m)
EOF

# 3. capture exact versions
.venv/bin/python -m pip freeze | sort > /tmp/resolved.txt
```

**Fallback rule.** If any package has no 3.14 wheel or the set cannot co-resolve:

1. Retry with `uv venv --python 3.12 .venv`.
2. Set `requires-python = ">=3.12"` in `pyproject.toml`.
3. Record the reason (which package, which error) in this file's `implementation_notes`.
4. Do **not** try 3.13 first — one fallback, one floor. 3.12 is the documented floor (§2.1).

`statsmodels` is the canary (compiled extensions, slowest to support a new interpreter);
`litellm` is the second risk (moves fast, occasionally pins narrow ranges).

### 3.2 `pyproject.toml`

Replace the whole file. Substitute `X.Y.Z` with the versions from step 3 above — **exact `==`
pins, no ranges, no `~=`**.

```toml
[project]
name = "onlab"
version = "0.1.0"
description = "LLM-driven statistical modelling workbench"
requires-python = ">=3.14"          # or ">=3.12" per the fallback rule
dependencies = [
    "fastapi==X.Y.Z",
    "uvicorn[standard]==X.Y.Z",
    "pydantic==X.Y.Z",
    "pydantic-settings==X.Y.Z",
    "python-multipart==X.Y.Z",
    "httpx==X.Y.Z",
    "litellm==X.Y.Z",
    "mcp==X.Y.Z",
    "pandas==X.Y.Z",
    "pyarrow==X.Y.Z",
    "numpy==X.Y.Z",
    "scikit-learn==X.Y.Z",
    "statsmodels==X.Y.Z",
    "matplotlib==X.Y.Z",
    "joblib==X.Y.Z",
]

[dependency-groups]
dev = [
    "pytest==X.Y.Z",
    "pytest-cov==X.Y.Z",
    "pytest-asyncio==X.Y.Z",
    "mypy==X.Y.Z",
    "ruff==X.Y.Z",
    "pandas-stubs==X.Y.Z",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["backend/statlab", "mcp_server/statlab_tools"]

[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
addopts = "-q --strict-markers --strict-config"
asyncio_mode = "strict"
filterwarnings = ["error"]           # a warning in CI is a defect to look at, not noise

[tool.coverage.run]
branch = true
source = ["backend/statlab", "mcp_server/statlab_tools"]

[tool.coverage.report]
fail_under = 100
show_missing = true
skip_covered = false
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]

[tool.mypy]
python_version = "3.14"              # match requires-python
strict = true
warn_unreachable = true
warn_unused_ignores = true
disallow_any_explicit = true
disallow_any_generics = true
no_implicit_reexport = true
files = ["backend", "mcp_server", "tests"]

[[tool.mypy.overrides]]
# Third-party packages without complete stubs. Keep this list minimal and justified;
# every entry is a place where type safety stops.
module = ["litellm.*", "statsmodels.*", "sklearn.*", "matplotlib.*", "joblib.*", "mcp.*"]
ignore_missing_imports = true

[tool.ruff]
line-length = 100
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "A", "C4", "SIM", "RET", "PTH", "RUF", "ANN", "S"]
ignore = ["ANN401"]                  # dynamic *args typing, not reachable under disallow_any

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101"]                 # assert is the point of a test
```

`disallow_any_explicit = true` is what enforces the "zero `any`" requirement — it makes an
explicit `Any` annotation a type error, not a style smell.

### 3.3 `backend/statlab/errors.py`

The root of the domain error hierarchy. Chunk 02 extends this same module; do not create a
second error module later.

```python
"""Domain errors. Every foreign exception is wrapped into one of these at its boundary."""

from __future__ import annotations


class StatlabError(Exception):
    """Base class for every error this application raises deliberately.

    Carries the original exception in `cause` so a boundary can wrap without losing the
    traceback, while callers pattern-match on the domain type rather than on library types.
    """

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause is None:
            return self.message
        return f"{self.message} (caused by {type(self.cause).__name__}: {self.cause})"


class ConfigError(StatlabError):
    """Configuration is invalid or incomplete.

    Raised only at startup, never mid-run: a run that has begun must not fail on config.
    `missing` names the environment variables that were absent, so the message can tell the
    user exactly what to add to `.env`.
    """

    def __init__(
        self,
        message: str,
        *,
        missing: tuple[str, ...] = (),
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message, cause=cause)
        self.missing = missing
```

Exact required behaviour:

| Expression | Result |
|---|---|
| `str(StatlabError("boom"))` | `"boom"` |
| `str(StatlabError("boom", cause=ValueError("x")))` | `"boom (caused by ValueError: x)"` |
| `ConfigError("bad").missing` | `()` |
| `ConfigError("bad", missing=("A", "B")).missing` | `("A", "B")` |
| `isinstance(ConfigError("x"), StatlabError)` | `True` |

### 3.4 `backend/statlab/config.py`

```python
"""Backend process configuration. Provider-free by design (see chunk 01 §1)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from statlab.errors import ConfigError

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class Settings(BaseSettings):
    """Backend settings, read from environment then `.env`.

    `extra="ignore"` is deliberate: the process environment legitimately contains hundreds of
    unrelated variables, so forbidding extras would make the app refuse to start because the
    user has `EDITOR` set. Unknown *declared* fields are still impossible.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    mcp_token: SecretStr = Field(alias="STATLAB_MCP_TOKEN")
    mcp_url: str = Field(default="http://127.0.0.1:8765/mcp", alias="STATLAB_MCP_URL")

    data_dir: Path = Field(default=Path("projects"), alias="STATLAB_DATA_DIR")
    db_path: Path = Field(default=Path("statlab.db"), alias="STATLAB_DB_PATH")

    api_host: str = Field(default="127.0.0.1", alias="STATLAB_API_HOST")
    api_port: int = Field(default=8000, ge=1024, le=65535, alias="STATLAB_API_PORT")
    allow_nonlocal_bind: bool = Field(default=False, alias="STATLAB_ALLOW_NONLOCAL_BIND")

    max_upload_mb: int = Field(default=200, ge=1, le=2048, alias="STATLAB_MAX_UPLOAD_MB")
    keep_intermediates: bool = Field(default=False, alias="STATLAB_KEEP_INTERMEDIATES")
    max_concurrent_runs: int = Field(default=0, ge=0, alias="STATLAB_MAX_CONCURRENT_RUNS")

    @field_validator("data_dir", "db_path")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        """Resolve relative paths against CWD so two processes agree on one location."""
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def _guard_bind_address(self) -> Settings:
        # ponytail: duplicated in mcp_server/statlab_tools/settings.py. Six lines across a
        # process boundary is cheaper than a third shared package. Upgrade path: extract to
        # a `statlab_shared` package if a third process ever needs it.
        if self.api_host not in _LOOPBACK_HOSTS and not self.allow_nonlocal_bind:
            raise ValueError(
                f"api_host {self.api_host!r} is not loopback. This application has no "
                f"authentication (D14); binding it publicly exposes an unauthenticated API. "
                f"Set STATLAB_ALLOW_NONLOCAL_BIND=true only if you understand that."
            )
        return self
```

Signatures that later chunks depend on — do not change them:

```python
def load_settings(env_file: Path | None = None) -> Settings: ...
    """Build Settings, wrapping any pydantic ValidationError in ConfigError.

    Raises
    ------
    ConfigError
        With `missing` listing every environment variable reported as absent, and a message
        that names them so `make check-env` output is actionable.
    """

@lru_cache(maxsize=1)
def get_settings() -> Settings: ...
    """Process-wide settings singleton. Tests must call `get_settings.cache_clear()`."""

def ensure_writable(path: Path) -> None: ...
    """Create `path` (and parents) if absent and verify it is writable.

    Raises
    ------
    ConfigError
        If creation fails or the directory is not writable, wrapping the OSError as `cause`.
    """
```

`load_settings` implementation requirements:

1. Call `Settings(_env_file=env_file)` if `env_file` is given, else `Settings()`.
2. Catch `pydantic.ValidationError`. Derive `missing` from errors whose `type` is
   `"missing"`, mapping each to its **environment variable alias** (`STATLAB_MCP_TOKEN`, not
   `mcp_token`) — the user sets env vars, so the message must speak in env vars.
3. Build a message of the form:
   `"Invalid configuration. Missing: STATLAB_MCP_TOKEN. Copy .env.example to .env and fill it in."`
   Omit the `Missing:` clause when nothing is missing and include the underlying validation
   detail instead.
4. Raise `ConfigError(message, missing=tuple(missing), cause=exc) from exc`.
5. After successful construction, call `ensure_writable(settings.data_dir)` and
   `ensure_writable(settings.db_path.parent)`.

`SecretStr` must be used for the token so `repr()` and `str()` of `Settings` render it as
`**********`. There is a test asserting the raw value never appears in either.

### 3.5 `mcp_server/statlab_tools/settings.py`

Independent of `backend.statlab` — **no import may cross that boundary in either direction.**

```python
"""MCP server process configuration. Imports nothing from the backend package."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class ToolSettingsError(Exception):
    """Configuration error in the MCP server process.

    Deliberately NOT `statlab.errors.ConfigError`: importing the backend package here would
    couple two processes that must stay independently deployable (D13).
    """


class ToolSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    mcp_token: SecretStr = Field(alias="STATLAB_MCP_TOKEN")
    data_dir: Path = Field(default=Path("projects"), alias="STATLAB_DATA_DIR")
    host: str = Field(default="127.0.0.1", alias="STATLAB_MCP_HOST")
    port: int = Field(default=8765, ge=1024, le=65535, alias="STATLAB_MCP_PORT")
    allow_nonlocal_bind: bool = Field(default=False, alias="STATLAB_ALLOW_NONLOCAL_BIND")
    fit_permits: int = Field(default=0, ge=0, alias="STATLAB_FIT_PERMITS")

    @field_validator("data_dir")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @model_validator(mode="after")
    def _guard_bind_address(self) -> ToolSettings:
        # ponytail: duplicated from backend/statlab/config.py on purpose — see the note there.
        if self.host not in _LOOPBACK_HOSTS and not self.allow_nonlocal_bind:
            raise ValueError(
                f"host {self.host!r} is not loopback. This server writes files and starts "
                f"model fits on request; do not expose it. Set STATLAB_ALLOW_NONLOCAL_BIND=true "
                f"only if you understand that."
            )
        return self

    @property
    def effective_fit_permits(self) -> int:
        """Semaphore size. `fit_permits=0` means 'one per core'.

        Kept as a property rather than resolved in a validator so tests can inject a fixed
        permit count (D17, §14.2) instead of depending on the machine's core count.
        """
        if self.fit_permits > 0:
            return self.fit_permits
        return os.cpu_count() or 1


def load_tool_settings(env_file: Path | None = None) -> ToolSettings: ...
    """Same contract as `load_settings`, raising `ToolSettingsError`."""

@lru_cache(maxsize=1)
def get_tool_settings() -> ToolSettings: ...
```

### 3.6 `.env.example`

Committed. `.env` itself is gitignored and never committed.

```bash
# ── Required ───────────────────────────────────────────────────────────────────
# Shared secret between the backend and the MCP server. Any long random string:
#   python -c "import secrets; print(secrets.token_urlsafe(32))"
STATLAB_MCP_TOKEN=

# ── Paths (defaults are fine for local use) ────────────────────────────────────
STATLAB_DATA_DIR=projects
STATLAB_DB_PATH=statlab.db

# ── Backend ───────────────────────────────────────────────────────────────────
STATLAB_API_HOST=127.0.0.1
STATLAB_API_PORT=8000
STATLAB_MAX_UPLOAD_MB=200
STATLAB_KEEP_INTERMEDIATES=false
STATLAB_MAX_CONCURRENT_RUNS=0          # 0 = unbounded (D17)

# ── MCP server ────────────────────────────────────────────────────────────────
STATLAB_MCP_HOST=127.0.0.1
STATLAB_MCP_PORT=8765
STATLAB_MCP_URL=http://127.0.0.1:8765/mcp
STATLAB_FIT_PERMITS=0                  # 0 = one per CPU core

# ── Danger zone ───────────────────────────────────────────────────────────────
# Neither service has authentication beyond the MCP bearer token (D14). Only set this
# if you understand that you are exposing an unauthenticated API.
STATLAB_ALLOW_NONLOCAL_BIND=false

# ── LLM provider keys: added in chunk 25/26, inside backend/statlab/llm/ ───────
# Deliberately absent here. Provider names must not appear outside llm/ (D18).
```

### 3.7 `.gitignore`

```gitignore
.env
.venv/
projects/
statlab.db
statlab.db-wal
statlab.db-shm
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/
node_modules/
dist/
.idea/
.DS_Store
```

### 3.8 `Makefile`

**Recipe lines must be indented with a literal TAB.** There is a DoD check for this.

```make
.DEFAULT_GOAL := help
PY := .venv/bin/python
SHELL := /bin/bash

.PHONY: help install check-env test lint format typecheck api mcp ui dev db-reset

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## Sync the pinned dependency set
	uv sync --all-groups

check-env: ## Fail fast if configuration is missing or unsafe
	@$(PY) -c "from statlab.config import load_settings; load_settings(); print('backend config OK')"
	@$(PY) -c "from statlab_tools.settings import load_tool_settings; load_tool_settings(); print('mcp config OK')"
	# chunk 26: append the LLM provider-key check here (must live inside llm/, D18)

test: ## Run the suite with coverage gating
	$(PY) -m pytest --cov --cov-report=term-missing

lint: ## Ruff check + format verification
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

format: ## Apply formatting
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: ## mypy --strict
	$(PY) -m mypy

api: check-env ## Run the backend alone (chunk 33+)
	@echo "not implemented until chunk 33"

mcp: check-env ## Run the MCP server alone (chunk 07+)
	@echo "not implemented until chunk 07"

ui: ## Run the Vite dev server alone (chunk 36+)
	@echo "not implemented until chunk 36"

dev: check-env ## Run all three processes; one Ctrl-C stops everything
	@echo "not implemented until chunk 36; check-env works today"

db-reset: ## Delete the database (chunk 05+)
	rm -f statlab.db statlab.db-wal statlab.db-shm
```

`api`, `mcp`, `ui` and `dev` are placeholders **on purpose**: writing process-launch plumbing for
processes that do not exist is speculative work. Each later chunk replaces its own line. The
`check-env` dependency is real from today, which is the part D20 actually asks for.

### 3.9 Import path

Both packages must be importable as `statlab` and `statlab_tools` (not `backend.statlab`). With
the `[tool.hatch.build.targets.wheel] packages` setting above plus `uv sync`, an editable install
provides this. Verify with:

```bash
.venv/bin/python -c "import statlab, statlab_tools; print(statlab.__file__, statlab_tools.__file__)"
```

If it fails, add to `pyproject.toml`:

```toml
[tool.uv]
package = true
```

Do **not** solve it with `sys.path` manipulation or a `conftest.py` hack.

---

## 4. Test Suite Spec

All tests use `monkeypatch` for environment manipulation and `tmp_path` for filesystem work. No
test may read the developer's real `.env`, and no test may leave a file outside `tmp_path`.

### 4.1 `tests/conftest.py`

```python
"""Shared fixtures. Every test starts from a known-empty environment."""

from collections.abc import Iterator
from pathlib import Path

import pytest

_STATLAB_VARS = (
    "STATLAB_MCP_TOKEN", "STATLAB_MCP_URL", "STATLAB_DATA_DIR", "STATLAB_DB_PATH",
    "STATLAB_API_HOST", "STATLAB_API_PORT", "STATLAB_ALLOW_NONLOCAL_BIND",
    "STATLAB_MAX_UPLOAD_MB", "STATLAB_KEEP_INTERMEDIATES", "STATLAB_MAX_CONCURRENT_RUNS",
    "STATLAB_MCP_HOST", "STATLAB_MCP_PORT", "STATLAB_FIT_PERMITS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every STATLAB_* var so tests cannot be influenced by the real environment."""
    for var in _STATLAB_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Drop the lru_cache singletons before and after each test."""
    from statlab.config import get_settings
    from statlab_tools.settings import get_tool_settings

    get_settings.cache_clear()
    get_tool_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_tool_settings.cache_clear()


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    """An env file with nothing in it, so `.env` on disk cannot leak into a test."""
    path = tmp_path / "empty.env"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def valid_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Minimum viable valid configuration pointing at a temporary data directory."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "test-token-not-a-real-secret")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("STATLAB_DB_PATH", str(tmp_path / "statlab.db"))
    return tmp_path
```

Every test that constructs settings must pass `env_file=empty_env_file`, so a developer's real
`.env` can never change a test outcome.

### 4.2 `tests/test_errors.py`

| # | Test | Assertion |
|---|---|---|
| E1 | `str` without cause | `str(StatlabError("boom")) == "boom"` |
| E2 | `str` with cause | equals `"boom (caused by ValueError: x)"` |
| E3 | attributes stored | `.message == "boom"`, `.cause is exc` |
| E4 | `ConfigError` default `missing` | `== ()` |
| E5 | `ConfigError` explicit `missing` | `== ("A", "B")` |
| E6 | subclass relation | `issubclass(ConfigError, StatlabError)` |
| E7 | `raise ... from` preserves chain | `exc.__cause__ is original` |
| E8 | catchable as `Exception` | a bare `except Exception` catches it |

### 4.3 `tests/test_config.py`

**Happy path and defaults**

| # | Test | Setup | Assertion |
|---|---|---|---|
| C1 | loads with only the required var | `valid_env` | `settings.mcp_token.get_secret_value() == "test-token-not-a-real-secret"` |
| C2 | defaults applied | `valid_env` | `api_host == "127.0.0.1"`, `api_port == 8000`, `max_upload_mb == 200`, `keep_intermediates is False`, `max_concurrent_runs == 0`, `mcp_url == "http://127.0.0.1:8765/mcp"` |
| C3 | overrides honoured | set all vars | every field equals the override, with correct type (`int` not `str`, `bool` not `str`) |
| C4 | `"false"`/`"true"` coerce | `STATLAB_KEEP_INTERMEDIATES=true` | `is True` (identity, not truthiness) |
| C5 | relative path made absolute | `STATLAB_DATA_DIR=projects` | `settings.data_dir.is_absolute()` |
| C6 | `~` expanded | `STATLAB_DATA_DIR=~/x` | no literal `~` in the result |
| C7 | data dir created | dir absent | `settings.data_dir.is_dir()` after load |
| C8 | db parent created | nested path | parent directory exists |

**Validation failures — each must raise `ConfigError`, not `ValidationError`**

| # | Test | Setup | Assertion |
|---|---|---|---|
| C9 | missing token | no `STATLAB_MCP_TOKEN` | `ConfigError`; `"STATLAB_MCP_TOKEN" in exc.missing`; the env var name (not `mcp_token`) appears in `str(exc)`; message mentions `.env.example` |
| C10 | port too low | `STATLAB_API_PORT=80` | `ConfigError`, `missing == ()` |
| C11 | port too high | `=70000` | `ConfigError` |
| C12 | port not an integer | `=notaport` | `ConfigError` |
| C13 | upload limit zero | `STATLAB_MAX_UPLOAD_MB=0` | `ConfigError` |
| C14 | upload limit over cap | `=4096` | `ConfigError` |
| C15 | negative concurrency | `STATLAB_MAX_CONCURRENT_RUNS=-1` | `ConfigError` |
| C16 | `cause` preserved | missing token | `exc.cause` is a `ValidationError` |

**Boundaries** — parametrised: `api_port` ∈ {1024, 65535} valid; {1023, 65536} invalid.
`max_upload_mb` ∈ {1, 2048} valid; {0, 2049} invalid.

**Bind-address guard**

| # | Test | Setup | Assertion |
|---|---|---|---|
| C17 | public bind refused | `STATLAB_API_HOST=0.0.0.0` | `ConfigError`; message contains `STATLAB_ALLOW_NONLOCAL_BIND` |
| C18 | public bind allowed with opt-in | `+ STATLAB_ALLOW_NONLOCAL_BIND=true` | loads; `api_host == "0.0.0.0"` |
| C19 | each loopback form accepted | parametrise `127.0.0.1`, `::1`, `localhost` | loads |
| C20 | arbitrary host refused | `STATLAB_API_HOST=10.0.0.5` | `ConfigError` |

**Secret hygiene**

| # | Test | Assertion |
|---|---|---|
| C21 | secret absent from `repr` | `"test-token-not-a-real-secret" not in repr(settings)` |
| C22 | secret absent from `str` | same for `str(settings)` |
| C23 | secret absent from `model_dump_json()` | same |
| C24 | secret retrievable explicitly | `get_secret_value()` returns it |

**`ensure_writable`**

| # | Test | Setup | Assertion |
|---|---|---|---|
| C25 | creates nested dirs | `tmp_path/"a"/"b"/"c"` | `is_dir()` afterwards |
| C26 | idempotent | call twice | no raise |
| C27 | unwritable dir rejected | `chmod 0o500` on `tmp_path/"ro"` | `ConfigError`, `cause` is an `OSError`. Skip on Windows and when running as root (`os.geteuid() == 0`) — root ignores the mode bit |
| C28 | path exists as a file | create a file, pass it | `ConfigError` |

**Caching**

| # | Test | Assertion |
|---|---|---|
| C29 | singleton | `get_settings() is get_settings()` |
| C30 | `cache_clear` gives a fresh object | after clear, `is not` the previous one |

### 4.4 `tests/test_tool_settings.py`

Mirror C1–C30 where applicable, raising `ToolSettingsError`, plus:

| # | Test | Setup | Assertion |
|---|---|---|---|
| T1 | default port | none | `port == 8765` |
| T2 | permits 0 → core count | `STATLAB_FIT_PERMITS=0` | `effective_fit_permits == (os.cpu_count() or 1)` |
| T3 | permits injected | `=3` | `effective_fit_permits == 3` |
| T4 | permits 1 valid | `=1` | `== 1` |
| T5 | negative permits | `=-1` | `ToolSettingsError` |
| T6 | non-numeric permits | `=many` | `ToolSettingsError` |
| T7 | cpu_count None handled | `monkeypatch.setattr(os, "cpu_count", lambda: None)` | `== 1` |
| T8 | bind guard | `STATLAB_MCP_HOST=0.0.0.0` | `ToolSettingsError` mentioning the flag |
| T9 | **boundary isolation** | inspect `statlab_tools.settings.__dict__` | no imported name resolves to a module under `statlab.` |
| T10 | error type independence | — | `not issubclass(ToolSettingsError, StatlabError)` |

T9/T10 are the tests that keep the two processes independently deployable (D13). They will look
pedantic until someone adds a convenience import in chunk 07.

### 4.5 `tests/test_dependencies.py`

Task 0's verification, kept as a permanent regression test.

| # | Test | Assertion |
|---|---|---|
| D1 | every runtime dependency imports | parametrised over the module list in §3.1; `importlib.import_module` succeeds |
| D2 | interpreter matches `requires-python` | parse `requires-python` from `pyproject.toml`; assert `sys.version_info` satisfies it |
| D3 | every dependency is pinned exactly | parse `pyproject.toml`; assert every entry in `dependencies` and `dependency-groups.dev` contains `==` and no `>=`, `~=`, `*` or `^` |
| D4 | installed versions match the pins | for each pinned package, compare `importlib.metadata.version(...)` to the pin |
| D5 | **no provider names in config modules** | read `backend/statlab/config.py` and `mcp_server/statlab_tools/settings.py`; assert none of `deepseek`, `anthropic`, `openai`, `claude`, `gpt` appears, case-insensitively (D18) |

D3–D5 are cheap and each catches a class of regression that is otherwise invisible until much
later. D5 in particular pre-echoes the invariant chunk 26 will test properly.

---

## 5. Definition of Done

All of the following must hold. Do not start chunk 02 until every line is verified by a command
you actually ran.

**Dependencies**
- [ ] `uv sync --all-groups` completes from a clean `.venv`.
- [ ] Every package in §3.1 imports (test D1 green).
- [ ] `pyproject.toml` pins every dependency with `==` (test D3 green) and the pins match what is
      installed (test D4 green).
- [ ] `requires-python` reflects the interpreter that actually worked. If it is `>=3.12`, this
      file's `implementation_notes` records which package forced the fallback and the exact error.

**Tests**
- [ ] `make test` exits 0.
- [ ] Coverage is **100 %** line *and* branch on `backend/statlab/errors.py`,
      `backend/statlab/config.py`, `mcp_server/statlab_tools/settings.py`. Enforced by
      `fail_under = 100`; do not lower it, and do not add `pragma: no cover` to reach it.
- [ ] Every test in §4.2–§4.5 exists and passes. Count: 8 + 30 + 10 + 5 = **53 tests minimum**.
- [ ] No test touches a path outside `tmp_path`; no test reads the repo's real `.env`.

**Static analysis**
- [ ] `make typecheck` exits 0 under `mypy --strict` with `disallow_any_explicit`.
- [ ] `make lint` exits 0 (`ruff check` and `ruff format --check`).
- [ ] Zero `# type: ignore` without an inline justification comment on the same line.

**Configuration behaviour**
- [ ] `make check-env` exits 0 with a valid `.env`.
- [ ] `make check-env` exits non-zero with a message naming `STATLAB_MCP_TOKEN` when it is
      absent. Verify by hand: `STATLAB_MCP_TOKEN= make check-env`.
- [ ] `make check-env` refuses a non-loopback `STATLAB_API_HOST` unless
      `STATLAB_ALLOW_NONLOCAL_BIND=true`.

**Repo hygiene**
- [ ] `.env` is gitignored; `.env.example` is committed and lists every variable in §3.6.
- [ ] `git status --porcelain` shows no `projects/`, `statlab.db`, `.venv/` or cache directories.
- [ ] `grep -Pc '^\t' Makefile` returns a non-zero count (recipes use real tabs).
- [ ] `python -c "import statlab, statlab_tools"` succeeds.

**Boundary**
- [ ] `mcp_server/` imports nothing from `backend/` (test T9 green).
- [ ] No provider or model name appears in this chunk's files (test D5 green).

**Housekeeping**
- [ ] This file's frontmatter is updated: `status: completed`, `completion_date` set,
      `implementation_notes` recording the resolved interpreter, any fallback taken, the exact
      pinned versions, and anything chunk 02 needs to know.
- [ ] `plan/00_index.md` row for chunk 01 shows `completed`.

### Explicitly out of scope

Do not create in this chunk: `main.py`, FastAPI app, `db.py`, any `graph/`, `llm/`, `domains/` or
`tools/` module, the frontend, Docker files, CI configuration, or a logging framework. Each has an
owning chunk. Adding them here is the speculative work this plan is designed to avoid.
