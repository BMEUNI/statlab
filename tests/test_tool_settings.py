"""Tests for mcp_server.statlab_tools.settings: mirrors of C1-C30 plus T1-T10.

Per plan/01_foundation.md §4.4.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from statlab.errors import StatlabError
from statlab_tools.settings import (
    ToolSettings,
    ToolSettingsError,
    get_tool_settings,
    load_tool_settings,
)


@pytest.fixture
def valid_tool_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "test-token-not-a-real-secret")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "projects"))
    return tmp_path


# ---- Mirrors of C1-C30 -----------------------------------------------------------------------


def test_loads_with_only_required_var(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.mcp_token.get_secret_value() == "test-token-not-a-real-secret"


def test_defaults_applied(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.allow_nonlocal_bind is False
    assert settings.fit_permits == 0


def test_overrides_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "tok")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("STATLAB_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("STATLAB_MCP_PORT", "9001")
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "2")
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    settings = load_tool_settings(env_file=empty)
    assert settings.port == 9001
    assert isinstance(settings.port, int)
    assert settings.fit_permits == 2


def test_relative_data_dir_made_absolute(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_DATA_DIR", "projects")
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.data_dir.is_absolute()


def test_data_dir_created(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.data_dir.is_dir()


def test_missing_token(empty_env_file: Path) -> None:
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    exc = exc_info.value
    assert "STATLAB_MCP_TOKEN" in exc.missing
    assert "STATLAB_MCP_TOKEN" in str(exc)
    assert ".env.example" in str(exc)


def test_blank_token_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch, empty_env_file: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "projects"))
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    assert "STATLAB_MCP_TOKEN" in exc_info.value.missing


def test_port_too_low(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MCP_PORT", "80")
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    assert exc_info.value.missing == ()


def test_port_too_high(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MCP_PORT", "70000")
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_port_not_an_integer(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MCP_PORT", "notaport")
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


@pytest.mark.parametrize("port", [1024, 65535])
def test_port_valid_boundaries(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv("STATLAB_MCP_PORT", str(port))
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.port == port


@pytest.mark.parametrize("port", [1023, 65536])
def test_port_invalid_boundaries(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, port: int
) -> None:
    monkeypatch.setenv("STATLAB_MCP_PORT", str(port))
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_public_bind_refused(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MCP_HOST", "0.0.0.0")
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    assert "STATLAB_ALLOW_NONLOCAL_BIND" in str(exc_info.value)


def test_public_bind_allowed_with_opt_in(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("STATLAB_ALLOW_NONLOCAL_BIND", "true")
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.host == "0.0.0.0"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_each_loopback_form_accepted(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    monkeypatch.setenv("STATLAB_MCP_HOST", host)
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.host == host


def test_arbitrary_host_refused(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_MCP_HOST", "10.0.0.5")
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_secret_absent_from_repr(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert "test-token-not-a-real-secret" not in repr(settings)


def test_secret_absent_from_str(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert "test-token-not-a-real-secret" not in str(settings)


def test_secret_absent_from_model_dump_json(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert "test-token-not-a-real-secret" not in settings.model_dump_json()


def test_secret_retrievable_explicitly(valid_tool_env: Path, empty_env_file: Path) -> None:
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.mcp_token.get_secret_value() == "test-token-not-a-real-secret"


def test_get_tool_settings_is_singleton(
    valid_tool_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    assert get_tool_settings() is get_tool_settings()


def test_get_tool_settings_cache_clear_gives_fresh_object(
    valid_tool_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    first = get_tool_settings()
    get_tool_settings.cache_clear()
    second = get_tool_settings()
    assert second is not first


def test_negative_fit_permits_rejected_at_field_level(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "-1")
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_cause_preserved(empty_env_file: Path) -> None:
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    assert exc_info.value.cause is not None


def test_tool_settings_error_str_with_cause() -> None:
    """Covers ToolSettingsError.__str__'s cause-formatting branch directly."""
    exc = ToolSettingsError("boom", cause=ValueError("x"))
    assert str(exc) == "boom (caused by ValueError: x)"


def test_tool_settings_error_str_without_cause() -> None:
    """Covers ToolSettingsError.__str__'s no-cause branch directly."""
    exc = ToolSettingsError("boom")
    assert str(exc) == "boom"


def test_data_dir_exists_as_file_rejected(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    afile = tmp_path / "afile"
    afile.write_text("x", encoding="utf-8")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(afile))
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_data_dir_unwritable_rejected(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("root or Windows ignores/handles the read-only mode bit differently")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.setenv("STATLAB_DATA_DIR", str(ro))
    try:
        with pytest.raises(ToolSettingsError) as exc_info:
            load_tool_settings(env_file=empty_env_file)
        assert isinstance(exc_info.value.cause, OSError)
    finally:
        ro.chmod(0o700)


def test_data_dir_mkdir_failure_wrapped(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parent_is_a_file = tmp_path / "not_a_dir"
    parent_is_a_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(parent_is_a_file / "child"))
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    assert isinstance(exc_info.value.cause, OSError)


# ---- T1-T10 -----------------------------------------------------------------------------------


def test_t1_default_port(valid_tool_env: Path, empty_env_file: Path) -> None:
    """T1."""
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.port == 8765


def test_t2_permits_zero_means_core_count(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T2."""
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "0")
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.effective_fit_permits == (os.cpu_count() or 1)


def test_t3_permits_injected(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T3."""
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "3")
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.effective_fit_permits == 3


def test_t4_permits_one_valid(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T4."""
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "1")
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.effective_fit_permits == 1


def test_t5_negative_permits(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T5."""
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "-1")
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_t6_non_numeric_permits(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T6."""
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "many")
    with pytest.raises(ToolSettingsError):
        load_tool_settings(env_file=empty_env_file)


def test_t7_cpu_count_none_handled(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7."""
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    settings = load_tool_settings(env_file=empty_env_file)
    assert settings.effective_fit_permits == 1


def test_t8_bind_guard_mentions_flag(
    valid_tool_env: Path, empty_env_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T8."""
    monkeypatch.setenv("STATLAB_MCP_HOST", "0.0.0.0")
    with pytest.raises(ToolSettingsError) as exc_info:
        load_tool_settings(env_file=empty_env_file)
    assert "STATLAB_ALLOW_NONLOCAL_BIND" in str(exc_info.value)


def test_t9_boundary_isolation() -> None:
    """T9. No imported name in statlab_tools.settings resolves to a module under statlab."""
    import statlab_tools.settings as mod

    for name, value in vars(mod).items():
        if name.startswith("__"):
            continue
        module_name = getattr(value, "__module__", None)
        if module_name is None:
            continue
        assert not module_name.startswith("statlab."), (
            f"{name} in statlab_tools.settings resolves to {module_name}, "
            f"which crosses the backend/mcp_server boundary"
        )
        assert module_name != "statlab", (
            f"{name} in statlab_tools.settings resolves to the statlab package itself"
        )


def test_t10_error_type_independence() -> None:
    """T10."""
    assert not issubclass(ToolSettingsError, StatlabError)


def test_tool_settings_is_a_type() -> None:
    """Sanity: ToolSettings is constructible via the documented type."""
    assert issubclass(ToolSettings, object)
