"""Shared fixtures. Every test starts from a known-empty environment."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab.persistence.db import connect, migrate
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import create_project
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import (
    CandidateSet,
    CandidateSpec,
    EstimatorSpec,
    FeatureSpec,
    PreprocessingSpec,
)
from statlab_contracts.contract import DataContract
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import ClassWeight, ParamValue, VariationDimension

if TYPE_CHECKING:
    from mcp import ClientSession
    from statlab_tools.settings import ToolSettings

_STATLAB_VARS = (
    "STATLAB_MCP_TOKEN",
    "STATLAB_MCP_URL",
    "STATLAB_DATA_DIR",
    "STATLAB_DB_PATH",
    "STATLAB_API_HOST",
    "STATLAB_API_PORT",
    "STATLAB_ALLOW_NONLOCAL_BIND",
    "STATLAB_MAX_UPLOAD_MB",
    "STATLAB_KEEP_INTERMEDIATES",
    "STATLAB_MAX_CONCURRENT_RUNS",
    "STATLAB_MCP_HOST",
    "STATLAB_MCP_PORT",
    "STATLAB_FIT_PERMITS",
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


@pytest.fixture
def ref() -> ArtifactRef:
    """A syntactically valid, properly typed artifact ref for tests that need one.

    Shared here rather than per test file: `ArtifactRef` is a `NewType`, so
    `init_typed = true` (chunk 02 §6) makes mypy enforce it at every `ArtifactMeta`/
    `NodeResult` construction site, and every chunk from 06 onward constructs artifacts.
    """
    return ArtifactRef("a_0123456789abcdef")


_CONTRACT_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "contracts"


@pytest.fixture
def minimal_contract() -> DataContract:
    """The D8 minimum: target.column and nothing else. Loaded from disk so the same JSON
    exercises the real parse path and doubles as an example file."""
    text = (_CONTRACT_FIXTURES_DIR / "minimal.json").read_text(encoding="utf-8")
    return DataContract.model_validate_json(text)


@pytest.fixture
def full_contract() -> DataContract:
    """Every field populated with a mutually consistent set of values, satisfying every rule
    in chunk 03 §3.4-3.5. Used as the base for cross-group tests: copy the underlying dict with
    one field changed via `model_validate(full_dict | {...})` so a failure names exactly one
    deviation."""
    text = (_CONTRACT_FIXTURES_DIR / "full.json").read_text(encoding="utf-8")
    return DataContract.model_validate_json(text)


@pytest.fixture
def full_contract_dict() -> dict[str, JsonValue]:
    """The raw parsed `full.json`, for tests that construct a one-field deviation via
    `DataContract.model_validate({**full_contract_dict, ...})`. Kept separate from
    `full_contract` (a `DataContract` instance) because negative tests need a plain dict to
    mutate, not a frozen model."""
    text = (_CONTRACT_FIXTURES_DIR / "full.json").read_text(encoding="utf-8")
    result: dict[str, JsonValue] = json.loads(text)
    return result


_CANDIDATE_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "candidates"


def make_candidate(
    *,
    label: str = "candidate",
    rationale: str = "a placeholder rationale long enough to satisfy the length floor",
    is_baseline: bool = False,
    preprocessing: PreprocessingSpec | None = None,
    features: FeatureSpec | None = None,
    estimator_name: str = "logistic_regression",
    estimator_params: dict[str, ParamValue] | None = None,
    class_weight: ClassWeight = "none",
    calibrate: bool = False,
    varies_from_baseline: tuple[VariationDimension, ...] = (),
) -> CandidateSpec:
    """Typed keyword-parameter helper, not `**overrides: object` -- that shape forces a
    `**dict` construction and an `arg-type` suppression under `init_typed = true` (see
    plan/00_index.md's test-fixture-pattern note; `test_domains_base.py`'s surviving
    `**overrides` helper is not the pattern to copy here)."""
    return CandidateSpec(
        label=label,
        rationale=rationale,
        is_baseline=is_baseline,
        preprocessing=preprocessing if preprocessing is not None else PreprocessingSpec(),
        features=features if features is not None else FeatureSpec(),
        estimator=EstimatorSpec(
            name=estimator_name,
            params=estimator_params if estimator_params is not None else {},
            class_weight=class_weight,
            calibrate=calibrate,
        ),
        varies_from_baseline=varies_from_baseline,
    )


@pytest.fixture
def baseline_candidate() -> CandidateSpec:
    """A cheap interpretable baseline: logistic_regression, one-hot, no engineering."""
    return make_candidate(
        label="baseline",
        rationale="cheap interpretable baseline: reference point for whether complexity pays",
        is_baseline=True,
    )


@pytest.fixture
def shortlist() -> CandidateSet:
    """Loaded from tests/fixtures/candidates/shortlist.json.

    Five candidates mirroring PLAN.md §4.2's worked example on an imbalanced binary target:
    baseline logreg; logreg + balanced class weight; random forest; hist GB + target encoding;
    logreg + an age x income interaction. Must satisfy all seven set rules, and each
    `varies_from_baseline` must match its actual diff -- if it does not, the fixture is wrong.
    """
    text = (_CANDIDATE_FIXTURES_DIR / "shortlist.json").read_text(encoding="utf-8")
    return CandidateSet.model_validate_json(text)


@pytest.fixture
def shortlist_dict() -> dict[str, JsonValue]:
    """The raw parsed `shortlist.json`, for tests that construct a one-field deviation."""
    text = (_CANDIDATE_FIXTURES_DIR / "shortlist.json").read_text(encoding="utf-8")
    result: dict[str, JsonValue] = json.loads(text)
    return result


# --- Persistence (chunk 05) -------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A file path for a fresh database. NOT ':memory:' -- WAL is unsupported there and every
    pragma and concurrency assertion in this chunk would pass vacuously."""
    return tmp_path / "statlab.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open, pragma-configured, unmigrated connection. Closed on teardown."""
    connection = connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def migrated_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open connection with all migrations applied."""
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def project(migrated_db: sqlite3.Connection) -> ProjectRow:
    """A persisted project, for tests needing a valid parent FK."""
    return create_project(migrated_db, slug="test-project", name="Test Project")


# --- Artifact store (chunk 06) ------------------------------------------------------------------


@pytest.fixture
def run_paths(tmp_path: Path) -> RunPaths:
    """`RunPaths` rooted in `tmp_path`, layout already created. Never the real `data_dir`."""
    from statlab.storage import ensure_run_layout

    paths = RunPaths(data_dir=tmp_path / "data", slug="proj", run_id="r_test0000000000")
    ensure_run_layout(paths)
    return paths


@pytest.fixture
def sample_frame() -> pd.DataFrame:
    """A frame covering every dtype the store must round-trip: `str`, `category`, `int64`,
    `float64`, `bool`, `datetime64[us]`, `Int64` (nullable, with a null). If a dtype is missing
    here, no test proves it survives storage."""
    return pd.DataFrame(
        {
            "s": pd.array(["a", "b", "c"], dtype="str"),
            "c": pd.Categorical(["x", "y", "x"]),
            "i": pd.array([1, 2, 3], dtype="int64"),
            "f": pd.array([1.5, 2.5, 3.5], dtype="float64"),
            "b": pd.array([True, False, True], dtype="bool"),
            "dt": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
            "ni": pd.array([1, 2, pd.NA], dtype="Int64"),
        }
    )


# --- MCP server foundation (chunk 07) -----------------------------------------------------------

MCP_TEST_TOKEN = "mcp-test-token-not-a-real-secret"  # noqa: S105 -- a fixture constant, not a real credential


@pytest.fixture
def mcp_data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def tool_settings(monkeypatch: pytest.MonkeyPatch, mcp_data_dir: Path) -> ToolSettings:
    """A valid `ToolSettings` pointing at a temporary data directory, with a known token and
    `fit_permits=1` -- deterministic semaphore tests need a fixed size, not the machine's core
    count (D17, §14.2)."""
    from statlab_tools.settings import load_tool_settings

    monkeypatch.setenv("STATLAB_MCP_TOKEN", MCP_TEST_TOKEN)
    monkeypatch.setenv("STATLAB_DATA_DIR", str(mcp_data_dir))
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "1")
    return load_tool_settings()


@pytest.fixture
def run_paths_over_mcp(tool_settings: ToolSettings) -> str:
    """Creates `<mcp_data_dir>/proj/runs/r_mcp0000000000000/` on disk and returns the run id.

    Session-level tests (`test_server_mcp_session.py`) call tools by `project`/`run` string,
    not by `RunPaths` -- this fixture is the MCP-session equivalent of `run_paths`, which
    returns the dataclass directly for tests that call tool bodies without going through a
    session.
    """
    run_id = "r_mcp0000000000000"
    root = tool_settings.data_dir / "proj" / "runs" / run_id / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return run_id


@pytest.fixture
def live_server(tool_settings: ToolSettings, unused_tcp_port: int) -> Iterator[str]:
    """Runs the real app under uvicorn in a thread; yields its base URL.

    Required, not preference: `httpx.ASGITransport` cannot reach the authorised path, because
    the streamable-HTTP session manager needs the ASGI lifespan and fails with "Task group is
    not initialized" without it (verified against the installed `mcp==2.2.0`, `00_index.md`).
    """
    import threading

    import uvicorn
    from statlab_tools.server import create_app

    app = create_app(tool_settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=unused_tcp_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        pass
    try:
        yield f"http://127.0.0.1:{unused_tcp_port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture
def mcp_session(live_server: str) -> Callable[[], AbstractAsyncContextManager[ClientSession]]:
    """A factory for an initialised MCP session over real HTTP with a valid bearer token --
    M1's "external MCP client". Each test does `async with mcp_session() as session:`.

    Returns a factory, not a session directly, and is a plain (sync) fixture rather than an
    async generator one: `ClientSession`/`streamable_http_client` are built on `anyio` task
    groups, which require the same asyncio *task* to enter and exit them. pytest-asyncio
    resumes an async generator fixture's teardown as a distinct task from the one that ran
    setup even within the same event loop, so any task-group-based context manager spanning
    the `yield` in a fixture hits "Attempted to exit cancel scope in a different task than it
    was entered in" on teardown -- reproduced directly against a bare `anyio.create_task_group`
    in a pytest-asyncio generator fixture before this fix, independent of anything MCP-specific.
    Returning a factory the *test* enters keeps the whole session lifetime inside one task: the
    test's own.
    """

    @asynccontextmanager
    async def open_session() -> AsyncIterator[ClientSession]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        # `mcp.client.streamable_http` imports this same object for its own internal use
        # (confirmed: `create_mcp_http_client` from there and from here are identical at
        # runtime), but never lists it in an `__all__` -- so `mypy --strict`'s
        # `no_implicit_reexport` (set explicitly in pyproject.toml) rejects importing it from
        # there, regardless of import style (`from ... import` vs module-qualified attribute
        # access both fail identically). Importing from its actual defining module is the only
        # option that is both correct at runtime and mypy-strict clean without adding a
        # `# type: ignore` shape outside the three permitted ones. If a future SDK release adds
        # `create_mcp_http_client` to `mcp.client.streamable_http.__all__`, switch this back.
        from mcp.shared._httpx_utils import create_mcp_http_client

        http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {MCP_TEST_TOKEN}"})
        async with (
            http_client,
            streamable_http_client(f"{live_server}/mcp", http_client=http_client) as (
                read,
                write,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session

    return open_session


# --- Intake tools (chunk 08) ---------------------------------------------------------------------


def churn_frame() -> pd.DataFrame:
    """240 rows, seeded, matching `full.json`'s seven columns, with known properties:
    `customer_id` unique and strictly increasing; `age` a plausible adult range; `plan` has 3
    levels; `signup_date` ISO-8601 strings parsing 100%; `satisfaction` in
    {low, medium, high}; `notes_free_text` long (mean length > 100) and 100% unique;
    `churned` 0/1 at roughly 30%.

    A plain function, not a fixture, deliberately -- `write_snapshot`/`seeded_run` need a frame
    to derive a real file's bytes from before any fixture graph exists, and `tests/test_tools_
    intake_*.py` need the SAME generator both as a `pytest.fixture`-wrapped value (for direct
    tool-body tests) and as a raw CSV-writer input (for `load_csv` tests), which a fixture alone
    cannot serve to a non-fixture helper. Built in code, not checked in, so the properties stay
    readable here rather than opaque in a binary file.
    """
    rng = np.random.default_rng(20260801)
    n = 240
    start = pd.Timestamp("2023-01-01")
    return pd.DataFrame(
        {
            "customer_id": np.arange(1, n + 1),
            "age": rng.integers(18, 80, size=n),
            "plan": rng.choice(["basic", "standard", "premium"], size=n, p=[0.5, 0.3, 0.2]),
            "signup_date": [
                (start + pd.Timedelta(days=int(d))).date().isoformat()
                for d in rng.integers(0, 700, size=n)
            ],
            "satisfaction": rng.choice(["low", "medium", "high"], size=n, p=[0.2, 0.5, 0.3]),
            "notes_free_text": [
                "Customer discussed plan options extensively during the support call, "
                f"mentioning competitor pricing and overall service quality concerns {i}."
                for i in range(n)
            ],
            "churned": rng.choice([0, 1], size=n, p=[0.7, 0.3]),
        }
    )


@pytest.fixture
def churn_frame_fixture() -> pd.DataFrame:
    """`pytest.fixture` wrapper around `churn_frame()`, for tests that want the frame injected
    directly rather than calling the generator themselves."""
    return churn_frame()


@pytest.fixture
def dataset_csv_bytes() -> bytes:
    """A small, deterministic CSV's raw bytes -- the simplest valid upload, shared by every
    `load_csv` test that does not need a specific dialect or defect."""
    return b"a,b,c\n1,2.5,x\n3,4.5,y\n4,6.5,z\n"


@pytest.fixture
def seeded_run(tool_settings: ToolSettings, dataset_csv_bytes: bytes) -> RunPaths:
    """A run directory with layout created and `dataset_csv_bytes` written under
    `datasets/<sha256>.csv` (the sha256 of those exact bytes). The sha is available via
    `RunPaths` is not enough to resolve it -- tests needing the hash use
    `hashlib.sha256(dataset_csv_bytes).hexdigest()` themselves, same inputs, same digest.
    """
    import hashlib

    from statlab.storage import ensure_run_layout
    from statlab_contracts.layout import ProjectPaths

    paths = RunPaths(data_dir=tool_settings.data_dir, slug="proj", run_id="r_seeded0000000")
    ensure_run_layout(paths)
    project = ProjectPaths(data_dir=tool_settings.data_dir, slug="proj")
    project.datasets.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(dataset_csv_bytes).hexdigest()
    project.dataset_csv(digest).write_bytes(dataset_csv_bytes)
    return paths


@pytest.fixture
def write_snapshot(seeded_run: RunPaths) -> Callable[[DataContract], None]:
    """Writes a contract to `seeded_run`'s `contract.snapshot.json` via `model_dump_json()`,
    the same shape `validate_contract` reads back (D-c)."""

    def write(contract: DataContract) -> None:
        seeded_run.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")

    return write
