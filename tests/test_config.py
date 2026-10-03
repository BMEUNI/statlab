"""Tests for backend.statlab.config: C1-C30 per plan/01_foundation.md §4.3."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from statlab.config import Settings, ensure_writable, get_settings, load_settings
from statlab.errors import ConfigError


def _write_env(tmp_path: Path, **overrides: str) -> Path:
    """Write a temp .env-style file with the given values, used via `env_file=`."""
    path = tmp_path / "custom.env"
    path.write_text("\n".join(f"{k}={v}" for k, v in overrides.items()) + "\n", encoding="utf-8")
    return path


# ---- Happy path and defaults ------------------------------------------------------------


def test_loads_with_only_required_var(valid_env: Path, empty_env_file: Path) -> None:
    """C1."""
    settings = load_settings(env_file=empty_env_file)
    assert settings.mcp_token.get_secret_value() == "test-token-not-a-real-secret"


def test_defaults_applied(valid_env: Path, empty_env_file: Path) -> None:
    """C2."""
    settings = load_settings(env_file=empty_env_file)
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.max_upload_mb == 200
    assert settings.keep_intermediates is False
    assert settings.max_concurrent_runs == 0
    assert settings.max_concurrent_llm == 4
    assert settings.mcp_url == "http://127.0.0.1:8765/mcp"


def test_overrides_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """C3."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "tok")
    monkeypatch.setenv("STATLAB_MCP_URL", "http://127.0.0.1:9999/mcp")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("STATLAB_DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("STATLAB_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STATLAB_API_PORT", "9001")
    monkeypatch.setenv("STATLAB_MAX_UPLOAD_MB", "50")
    monkeypatch.setenv("STATLAB_KEEP_INTERMEDIATES", "true")
    monkeypatch.setenv("STATLAB_MAX_CONCURRENT_RUNS", "4")
    monkeypatch.setenv("STATLAB_MAX_CONCURRENT_LLM", "7")
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")

    settings = load_settings(env_file=empty)

    assert settings.mcp_url == "http://127.0.0.1:9999/mcp"
    assert settings.api_port == 9001
    assert isinstance(settings.api_port, int)
    assert settings.max_upload_mb == 50
    assert settings.keep_intermediates is True
    assert isinstance(settings.keep_intermediates, bool)
    assert settings.max_concurrent_runs == 4
    assert settings.max_concurrent_llm == 7


def test_bool_coercion_is_identity(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C4."""
    monkeypatch.setenv("STATLAB_KEEP_INTERMEDIATES", "true")
    settings = load_settings(env_file=empty_env_file)
    assert settings.keep_intermediates is True


def test_relative_data_dir_made_absolute(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C5."""
    monkeypatch.setenv("STATLAB_DATA_DIR", "projects")
    settings = load_settings(env_file=empty_env_file)
    assert settings.data_dir.is_absolute()


def test_tilde_expanded(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C6."""
    monkeypatch.setenv("STATLAB_DATA_DIR", "~/statlab_test_dir_should_not_exist")
    settings = load_settings(env_file=empty_env_file)
    assert "~" not in str(settings.data_dir)
    # cleanup: this resolves under the real home dir, so remove it if created.
    if settings.data_dir.is_dir():
        settings.data_dir.rmdir()


def test_data_dir_created(valid_env: Path, empty_env_file: Path) -> None:
    """C7."""
    settings = load_settings(env_file=empty_env_file)
    assert settings.data_dir.is_dir()


def test_db_parent_created(
    valid_env: Path, empty_env_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C8."""
    nested = tmp_path / "nested" / "deeper" / "s.db"
    monkeypatch.setenv("STATLAB_DB_PATH", str(nested))
    settings = load_settings(env_file=empty_env_file)
    assert settings.db_path.parent.is_dir()


# ---- Validation failures -----------------------------------------------------------------


def test_missing_token(empty_env_file: Path) -> None:
    """C9."""
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=empty_env_file)
    exc = exc_info.value
    assert "STATLAB_MCP_TOKEN" in exc.missing
    assert "STATLAB_MCP_TOKEN" in str(exc)
    assert ".env.example" in str(exc)


def test_blank_token_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, tmp_path: Path
) -> None:
    """`STATLAB_MCP_TOKEN=` (present but empty) must fail the same way as absent — this is
    the exact scenario the chunk spec's hand-verification command exercises."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("STATLAB_DB_PATH", str(tmp_path / "statlab.db"))
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=empty_env_file)
    exc = exc_info.value
    assert "STATLAB_MCP_TOKEN" in exc.missing
    assert "STATLAB_MCP_TOKEN" in str(exc)


def test_whitespace_only_token_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "   ")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("STATLAB_DB_PATH", str(tmp_path / "statlab.db"))
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=empty_env_file)
    assert "STATLAB_MCP_TOKEN" in exc_info.value.missing


@pytest.mark.parametrize("port", ["80", "1023"])
def test_port_too_low(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, port: str
) -> None:
    """C10."""
    monkeypatch.setenv("STATLAB_API_PORT", port)
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=empty_env_file)
    assert exc_info.value.missing == ()


def test_port_too_high(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C11."""
    monkeypatch.setenv("STATLAB_API_PORT", "70000")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


def test_port_not_an_integer(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C12."""
    monkeypatch.setenv("STATLAB_API_PORT", "notaport")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


def test_upload_limit_zero(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C13."""
    monkeypatch.setenv("STATLAB_MAX_UPLOAD_MB", "0")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


def test_upload_limit_over_cap(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C14."""
    monkeypatch.setenv("STATLAB_MAX_UPLOAD_MB", "4096")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


def test_negative_concurrency(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C15."""
    monkeypatch.setenv("STATLAB_MAX_CONCURRENT_RUNS", "-1")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


def test_llm_concurrency_must_be_positive(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MAX_CONCURRENT_LLM", "0")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


def test_cause_preserved(empty_env_file: Path) -> None:
    """C16."""
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=empty_env_file)
    assert isinstance(exc_info.value.cause, ValidationError)


# ---- Boundaries, parametrised --------------------------------------------------------------


@pytest.mark.parametrize("port", [1024, 65535])
def test_api_port_valid_boundaries(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv("STATLAB_API_PORT", str(port))
    settings = load_settings(env_file=empty_env_file)
    assert settings.api_port == port


@pytest.mark.parametrize("port", [1023, 65536])
def test_api_port_invalid_boundaries(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv("STATLAB_API_PORT", str(port))
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


@pytest.mark.parametrize("mb", [1, 2048])
def test_max_upload_mb_valid_boundaries(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, mb: int
) -> None:
    monkeypatch.setenv("STATLAB_MAX_UPLOAD_MB", str(mb))
    settings = load_settings(env_file=empty_env_file)
    assert settings.max_upload_mb == mb


@pytest.mark.parametrize("mb", [0, 2049])
def test_max_upload_mb_invalid_boundaries(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, mb: int
) -> None:
    monkeypatch.setenv("STATLAB_MAX_UPLOAD_MB", str(mb))
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


# ---- Bind-address guard ---------------------------------------------------------------------


def test_public_bind_refused(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C17."""
    monkeypatch.setenv("STATLAB_API_HOST", "0.0.0.0")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=empty_env_file)
    assert "STATLAB_ALLOW_NONLOCAL_BIND" in str(exc_info.value)


def test_public_bind_allowed_with_opt_in(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C18."""
    monkeypatch.setenv("STATLAB_API_HOST", "0.0.0.0")
    monkeypatch.setenv("STATLAB_ALLOW_NONLOCAL_BIND", "true")
    settings = load_settings(env_file=empty_env_file)
    assert settings.api_host == "0.0.0.0"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_each_loopback_form_accepted(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    """C19."""
    monkeypatch.setenv("STATLAB_API_HOST", host)
    settings = load_settings(env_file=empty_env_file)
    assert settings.api_host == host


def test_arbitrary_host_refused(
    valid_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C20."""
    monkeypatch.setenv("STATLAB_API_HOST", "10.0.0.5")
    with pytest.raises(ConfigError):
        load_settings(env_file=empty_env_file)


# ---- Secret hygiene --------------------------------------------------------------------------


def test_secret_absent_from_repr(valid_env: Path, empty_env_file: Path) -> None:
    """C21."""
    settings = load_settings(env_file=empty_env_file)
    assert "test-token-not-a-real-secret" not in repr(settings)


def test_secret_absent_from_str(valid_env: Path, empty_env_file: Path) -> None:
    """C22."""
    settings = load_settings(env_file=empty_env_file)
    assert "test-token-not-a-real-secret" not in str(settings)


def test_secret_absent_from_model_dump_json(valid_env: Path, empty_env_file: Path) -> None:
    """C23."""
    settings = load_settings(env_file=empty_env_file)
    assert "test-token-not-a-real-secret" not in settings.model_dump_json()


def test_secret_retrievable_explicitly(valid_env: Path, empty_env_file: Path) -> None:
    """C24."""
    settings = load_settings(env_file=empty_env_file)
    assert settings.mcp_token.get_secret_value() == "test-token-not-a-real-secret"


# ---- ensure_writable --------------------------------------------------------------------------


def test_ensure_writable_creates_nested_dirs(tmp_path: Path) -> None:
    """C25."""
    target = tmp_path / "a" / "b" / "c"
    ensure_writable(target)
    assert target.is_dir()


def test_ensure_writable_idempotent(tmp_path: Path) -> None:
    """C26."""
    target = tmp_path / "a"
    ensure_writable(target)
    ensure_writable(target)  # must not raise


def test_ensure_writable_unwritable_dir_rejected(tmp_path: Path) -> None:
    """C27."""
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("root or Windows ignores/handles the read-only mode bit differently")
    target = tmp_path / "ro"
    target.mkdir()
    target.chmod(0o500)
    try:
        with pytest.raises(ConfigError) as exc_info:
            ensure_writable(target)
        assert isinstance(exc_info.value.cause, OSError)
    finally:
        target.chmod(0o700)


def test_ensure_writable_path_exists_as_file(tmp_path: Path) -> None:
    """C28."""
    target = tmp_path / "afile"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError):
        ensure_writable(target)


def test_ensure_writable_mkdir_failure_wrapped(tmp_path: Path) -> None:
    """Covers the mkdir-itself-fails branch: parent is a file, so no directory can be
    created under it — mkdir raises NotADirectoryError (an OSError subclass)."""
    parent_is_a_file = tmp_path / "not_a_dir"
    parent_is_a_file.write_text("x", encoding="utf-8")
    target = parent_is_a_file / "child"
    with pytest.raises(ConfigError) as exc_info:
        ensure_writable(target)
    assert isinstance(exc_info.value.cause, OSError)


# ---- Caching -----------------------------------------------------------------------------------


def test_get_settings_is_singleton(
    valid_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C29."""
    monkeypatch.chdir(tmp_path)
    assert get_settings() is get_settings()


def test_get_settings_cache_clear_gives_fresh_object(
    valid_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C30."""
    monkeypatch.chdir(tmp_path)
    first = get_settings()
    get_settings.cache_clear()
    second = get_settings()
    assert second is not first


def test_settings_is_a_basesettings_subclass() -> None:
    """Sanity: Settings is constructible via the documented type, not a stand-in."""
    assert issubclass(Settings, object)
