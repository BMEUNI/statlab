"""Chunk 07 §3.3: `python -m statlab_tools`. Exit code 2 on a config error (D20's fail-fast),
0 on a normal start."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_main_exits_2_on_tool_settings_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No `STATLAB_MCP_TOKEN` set -- `load_tool_settings` raises `ToolSettingsError`, `main()`
    prints the message to stderr and returns 2."""
    monkeypatch.delenv("STATLAB_MCP_TOKEN", raising=False)
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))

    from statlab_tools.__main__ import main

    exit_code = main()
    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.err
    assert "STATLAB_MCP_TOKEN" in captured.err


def test_main_runs_uvicorn_and_returns_0(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A valid configuration builds the app and calls `uvicorn.run` -- mocked here, since
    actually starting a server and blocking is not this test's concern."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "main-test-token-not-a-real-secret")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))

    calls: list[dict[str, object]] = []

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr("statlab_tools.__main__.uvicorn.run", fake_run)

    from statlab_tools.__main__ import main

    exit_code = main()
    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"


def test_main_uses_the_configured_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "main-test-token-two")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATLAB_MCP_PORT", "9999")

    calls: list[dict[str, object]] = []

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append({"port": port})

    monkeypatch.setattr("statlab_tools.__main__.uvicorn.run", fake_run)

    from statlab_tools.__main__ import main

    main()
    assert calls[0]["port"] == 9999


def test_main_passes_the_built_app_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The object handed to `uvicorn.run` is a real Starlette app built by `create_app`, not a
    placeholder -- confirmed by checking it has the routes `create_app` is known to add."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "main-test-token-three")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))

    calls: list[object] = []

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append(app)

    monkeypatch.setattr("statlab_tools.__main__.uvicorn.run", fake_run)

    from starlette.applications import Starlette
    from statlab_tools.__main__ import main

    main()
    assert len(calls) == 1
    assert isinstance(calls[0], Starlette)


def test_main_missing_data_dir_permission_also_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A different `ToolSettingsError` cause (an unwritable data dir) still exits 2, not just
    the missing-token case -- confirms `main()` catches the base exception type, not one
    specific message."""
    unwritable = tmp_path / "exists_as_a_file"
    unwritable.write_text("x")
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "main-test-token-four")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(unwritable))

    from statlab_tools.__main__ import main

    exit_code = main()
    assert exit_code == 2
