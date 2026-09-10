"""Shared fixtures. Every test starts from a known-empty environment."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.contract import DataContract

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
