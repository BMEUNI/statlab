"""Shared fixtures. Every test starts from a known-empty environment."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

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
