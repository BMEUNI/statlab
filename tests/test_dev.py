from __future__ import annotations

import shutil
import signal
import subprocess
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from urllib.error import URLError

import pytest
from pydantic import SecretStr
from statlab.config import Settings
from statlab.errors import ConfigError
from statlab_tools.settings import ToolSettings, ToolSettingsError

from scripts import dev


class FakeProcess:
    def __init__(
        self,
        name: str,
        code: int | None = None,
        *,
        history: list[str] | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.code = code
        self.history = history
        self.on_wait = on_wait
        self.actions: list[str] = []

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.actions.append("terminate")
        if self.history is not None:
            self.history.append(f"{self.name}:terminate")
        self.code = -15

    def wait(self) -> int:
        self.actions.append("wait")
        if self.history is not None:
            self.history.append(f"{self.name}:wait")
        if self.on_wait is not None:
            self.on_wait()
        return self.code or 0


def configuration(
    tmp_path: Path,
    *,
    api_host: str = "127.0.0.1",
    tool_host: str = "127.0.0.1",
    mcp_url: str | None = None,
    tool_data_dir: Path | None = None,
) -> tuple[Settings, ToolSettings]:
    data_dir = tmp_path / "data"
    url_host = f"[{tool_host}]" if ":" in tool_host else tool_host
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        mcp_url=f"http://{url_host}:8765/mcp" if mcp_url is None else mcp_url,
        data_dir=data_dir,
        db_path=data_dir / "statlab.db",
        api_host=api_host,
        api_port=8000,
        allow_nonlocal_bind=True,
    )
    tools = ToolSettings(
        mcp_token=SecretStr("test-token"),
        data_dir=data_dir if tool_data_dir is None else tool_data_dir,
        host=tool_host,
        port=8765,
        allow_nonlocal_bind=True,
    )
    return settings, tools


def install_runtime(
    monkeypatch: pytest.MonkeyPatch,
    processes: list[FakeProcess],
) -> tuple[
    list[tuple[Sequence[str], Path, bool, Mapping[str, str] | None]],
    dict[int, object],
    list[tuple[int, object]],
]:
    commands: list[tuple[Sequence[str], Path, bool, Mapping[str, str] | None]] = []
    installed: dict[int, object] = {}
    signal_calls: list[tuple[int, object]] = []
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(
        Path, "is_file", lambda self: self == dev._ROOT / "frontend/node_modules/vite/bin/vite.js"
    )
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(dev, "_health_ready", lambda url: True)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.SIG_DFL)

    def popen(
        command: Sequence[str],
        *,
        cwd: Path,
        start_new_session: bool,
        env: Mapping[str, str] | None = None,
    ) -> FakeProcess:
        commands.append((command, cwd, start_new_session, env))
        return processes[len(commands) - 1]

    def install(sig: int, handler: object) -> object:
        previous = installed.get(int(sig), signal.SIG_DFL)
        installed[int(sig)] = handler
        signal_calls.append((int(sig), handler))
        return previous

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(signal, "signal", install)
    return commands, installed, signal_calls


def test_check_configuration_order_errors_usage_and_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    settings, tools = configuration(tmp_path)

    def backend_settings() -> Settings:
        calls.append("backend")
        return settings

    def tool_settings() -> ToolSettings:
        calls.append("tools")
        return tools

    def routing() -> None:
        calls.append("routing")

    monkeypatch.setattr(dev, "load_settings", backend_settings)
    monkeypatch.setattr(dev, "load_tool_settings", tool_settings)
    monkeypatch.setattr(dev, "check_startup_environment", routing)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected process")
    )
    assert dev.main(["check"]) == 0
    assert calls == ["backend", "tools", "routing"]
    assert capsys.readouterr().out == "Configuration OK\n"
    monkeypatch.setattr(
        dev,
        "load_tool_settings",
        lambda: (_ for _ in ()).throw(
            ToolSettingsError(
                "SECRET", missing=("STATLAB_MCP_TOKEN",), cause=RuntimeError("SECRET")
            )
        ),
    )
    assert dev.main(["check"]) == 2
    error = capsys.readouterr().err
    assert error == (
        "Configuration is invalid. Check .env and configuration values.\n"
        "Missing: STATLAB_MCP_TOKEN\n"
    )
    assert "SECRET" not in error
    monkeypatch.setattr(dev, "load_tool_settings", lambda: tools)
    monkeypatch.setattr(
        dev,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            ConfigError("SECRET", missing=("STATLAB_MCP_TOKEN",), cause=RuntimeError("SECRET"))
        ),
    )
    assert dev.main(["check"]) == 2
    error = capsys.readouterr().err
    assert error == (
        "Configuration is invalid. Check .env and configuration values.\n"
        "Missing: STATLAB_MCP_TOKEN\n"
    )
    assert "SECRET" not in error
    monkeypatch.setattr(dev, "load_settings", lambda: settings)
    assert dev.main(["unknown"]) == 2
    assert capsys.readouterr().err == "Usage: dev.py check|dev|share\n"
    monkeypatch.setattr(dev, "load_settings", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert dev.main(["check"]) == 130


def test_share_runs_vite_preview_with_secure_cookies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings, tools = configuration(tmp_path)
    mcp, api, ui = FakeProcess("mcp"), FakeProcess("api"), FakeProcess("ui")
    commands, _, _ = install_runtime(monkeypatch, [mcp, api, ui])

    def sleep(seconds: float) -> None:
        del seconds
        api.code = 3

    monkeypatch.setattr(time, "sleep", sleep)
    assert dev.run_dev(settings, tools, ("preview",)) == 3
    assert list(commands[2][0]) == [
        "/usr/bin/node",
        str(dev._ROOT / "frontend/node_modules/vite/bin/vite.js"),
        "preview",
    ]

    calls: list[tuple[str, ...]] = []

    def fake_run_dev(settings: Settings, tools: ToolSettings, ui_args: tuple[str, ...] = ()) -> int:
        del settings, tools
        calls.append(ui_args)
        return 0

    monkeypatch.setattr(dev, "run_dev", fake_run_dev)
    monkeypatch.setattr(dev, "load_tool_settings", lambda: tools)
    monkeypatch.setattr(dev, "check_startup_environment", lambda: None)
    monkeypatch.setattr(dev, "load_settings", lambda: settings)
    assert dev.main(["share"]) == 2
    assert capsys.readouterr().err == "make share needs STATLAB_COOKIE_SECURE=true.\n"
    assert calls == []

    monkeypatch.setattr(
        dev, "load_settings", lambda: settings.model_copy(update={"cookie_secure": True})
    )
    assert dev.main(["share"]) == 0
    assert calls == [("preview",)]
    assert dev.main(["dev"]) == 0
    assert calls == [("preview",), ()]


@pytest.mark.parametrize(
    ("api_host", "tool_host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1", ("http://127.0.0.1:8000", "http://127.0.0.1:8765/health")),
        ("127.0.0.1", "localhost", ("http://127.0.0.1:8000", "http://localhost:8765/health")),
        ("::1", "::1", ("http://[::1]:8000", "http://[::1]:8765/health")),
    ],
)
def test_local_endpoints_accept_loopback(
    tmp_path: Path, api_host: str, tool_host: str, expected: tuple[str, str]
) -> None:
    settings, tools = configuration(tmp_path, api_host=api_host, tool_host=tool_host)
    assert dev.local_endpoints(settings, tools) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:8765/mcp",
        "http://user:SECRET@127.0.0.1:8765/mcp",
        "http://127.0.0.1:9999/mcp",
        "http://127.0.0.1:8765/other",
        "http://127.0.0.1:8765/mcp?x=1",
        "http://127.0.0.1:8765/mcp#x",
        "http://[::1]:8765/mcp",
        "http://127.0.0.1:invalid/mcp",
    ],
)
def test_local_endpoints_reject_bad_mcp_url(tmp_path: Path, url: str) -> None:
    settings, tools = configuration(tmp_path, mcp_url=url)
    with pytest.raises(ConfigError) as error:
        dev.local_endpoints(settings, tools)
    assert error.value.message == "Local development configuration is inconsistent."


def test_local_endpoints_reject_nonlocal_data_and_token_mismatch(tmp_path: Path) -> None:
    for api_host, tool_host, other_dir in [
        ("0.0.0.0", "127.0.0.1", None),
        ("127.0.0.1", "0.0.0.0", None),
        ("127.0.0.1", "127.0.0.1", tmp_path / "other"),
    ]:
        settings, tools = configuration(
            tmp_path,
            api_host=api_host,
            tool_host=tool_host,
            tool_data_dir=other_dir,
        )
        with pytest.raises(ConfigError) as error:
            dev.local_endpoints(settings, tools)
        assert error.value.message == "Local development configuration is inconsistent."
    settings, _ = configuration(tmp_path)
    tools = ToolSettings(
        mcp_token=SecretStr("different-token"),
        data_dir=settings.data_dir,
        host="127.0.0.1",
        port=8765,
        allow_nonlocal_bind=True,
    )
    with pytest.raises(ConfigError) as error:
        dev.local_endpoints(settings, tools)
    assert error.value.message == "Local development configuration is inconsistent."


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.closed = False

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.closed = True


class FakeProxyHandler:
    def __init__(self, proxies: dict[str, str]) -> None:
        self.proxies = proxies


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, float]] = []

    def open(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append((url, timeout))
        return self.response


def test_health_ready_uses_proxy_free_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeResponse(200)
    opener = FakeOpener(response)
    monkeypatch.setattr(urllib.request, "ProxyHandler", FakeProxyHandler)

    def build_opener(handler: FakeProxyHandler) -> FakeOpener:
        assert handler.proxies == {}
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    assert dev._health_ready("http://127.0.0.1:8765/health") is True
    assert opener.calls == [("http://127.0.0.1:8765/health", 1)]
    assert response.closed
    response = FakeResponse(503)
    opener = FakeOpener(response)
    assert dev._health_ready("http://127.0.0.1:8765/health") is False
    assert opener.calls == [("http://127.0.0.1:8765/health", 1)]
    assert response.closed

    class FailedOpener(FakeOpener):
        def open(self, url: str, *, timeout: float) -> FakeResponse:
            del url, timeout
            raise URLError("SECRET")

    opener = FailedOpener(response)
    assert dev._health_ready("http://127.0.0.1:8765/health") is False


@pytest.mark.parametrize(
    ("signal_number", "exit_code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)]
)
def test_run_dev_signal_order_and_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signal_number: int,
    exit_code: int,
) -> None:
    settings, tools = configuration(tmp_path)
    history: list[str] = []
    mcp = FakeProcess("mcp", history=history)
    api = FakeProcess(
        "api",
        history=history,
        on_wait=lambda: None if mcp.code is None else pytest.fail("MCP stopped before API drain"),
    )
    ui = FakeProcess("ui", history=history)
    processes = [mcp, api, ui]
    commands, installed, signal_calls = install_runtime(monkeypatch, processes)
    sent = False

    def sleep(seconds: float) -> None:
        nonlocal sent
        del seconds
        if not sent:
            sent = True
            handler = installed[int(signal_number)]
            assert callable(handler)
            handler(int(signal_number), None)
            other = signal.SIGTERM if signal_number == signal.SIGINT else signal.SIGINT
            second = installed[int(other)]
            assert callable(second)
            second(int(other), None)

    monkeypatch.setattr(time, "sleep", sleep)
    assert dev.run_dev(settings, tools) == exit_code
    assert [list(command) for command, _, _, _ in commands] == [
        [str(dev._ROOT / ".venv/bin/python"), "-m", "statlab_tools"],
        [str(dev._ROOT / ".venv/bin/python"), "-m", "statlab.main"],
        ["/usr/bin/node", str(dev._ROOT / "frontend/node_modules/vite/bin/vite.js")],
    ]
    assert [cwd for _, cwd, _, _ in commands] == [dev._ROOT, dev._ROOT, dev._ROOT / "frontend"]
    assert all(new_session for _, _, new_session, _ in commands)
    env = commands[2][3]
    assert env is not None and env["STATLAB_API_ORIGIN"] == "http://127.0.0.1:8000"
    assert history == [
        "ui:terminate",
        "ui:wait",
        "api:terminate",
        "api:wait",
        "mcp:terminate",
        "mcp:wait",
    ]
    assert signal_calls[-2:] == [
        (int(signal.SIGINT), signal.SIG_DFL),
        (int(signal.SIGTERM), signal.SIG_DFL),
    ]
    assert all(process.actions.count("wait") == 1 for process in processes)


@pytest.mark.parametrize(("code", "expected"), [(7, 7), (0, 1), (-9, 137)])
def test_run_dev_child_exit_drains_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int, expected: int
) -> None:
    settings, tools = configuration(tmp_path)
    history: list[str] = []
    mcp = FakeProcess("mcp", history=history)
    api = FakeProcess("api", history=history)
    ui = FakeProcess("ui", history=history)
    processes = [mcp, api, ui]
    commands, _, _ = install_runtime(monkeypatch, processes)

    def sleep(seconds: float) -> None:
        del seconds
        api.code = code

    monkeypatch.setattr(time, "sleep", sleep)
    assert dev.run_dev(settings, tools) == expected
    assert history == ["ui:terminate", "ui:wait", "api:wait", "mcp:terminate", "mcp:wait"]
    assert all(process.actions.count("wait") == 1 for process in processes)
    assert len(commands) == 3


def test_run_dev_mcp_exit_and_readiness_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, tools = configuration(tmp_path)
    process = FakeProcess("mcp", 5)
    commands, _, _ = install_runtime(monkeypatch, [process])
    monkeypatch.setattr(
        dev, "_health_ready", lambda url: pytest.fail("health called after child exit")
    )
    assert dev.run_dev(settings, tools) == 5
    assert len(commands) == 1 and process.actions == ["wait"]
    process = FakeProcess("mcp")
    commands, _, _ = install_runtime(monkeypatch, [process])
    monkeypatch.setattr(dev, "_health_ready", lambda url: False)
    times = iter([0.0, 31.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(times))
    assert dev.run_dev(settings, tools) == 1
    assert capsys.readouterr().err == "MCP readiness timed out.\n"
    assert len(commands) == 1 and process.actions == ["terminate", "wait"]


def test_run_dev_signal_during_health_and_vite_spawn_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings, tools = configuration(tmp_path)
    mcp = FakeProcess("mcp")
    commands, installed, _ = install_runtime(monkeypatch, [mcp])

    def health(url: str) -> bool:
        handler = installed[int(signal.SIGINT)]
        assert callable(handler)
        handler(int(signal.SIGINT), None)
        return True

    monkeypatch.setattr(dev, "_health_ready", health)
    assert dev.run_dev(settings, tools) == 130
    assert len(commands) == 1 and mcp.actions == ["terminate", "wait"]

    settings, tools = configuration(tmp_path)
    history: list[str] = []
    api = FakeProcess("api", history=history)
    mcp = FakeProcess("mcp", history=history)
    commands, _, signal_calls = install_runtime(monkeypatch, [mcp, api])

    def failing_popen(
        command: Sequence[str],
        *,
        cwd: Path,
        start_new_session: bool,
        env: Mapping[str, str] | None = None,
    ) -> FakeProcess:
        commands.append((command, cwd, start_new_session, env))
        if len(commands) == 3:
            raise OSError("SECRET")
        return [mcp, api][len(commands) - 1]

    monkeypatch.setattr(subprocess, "Popen", failing_popen)
    monkeypatch.setattr(dev, "load_settings", lambda: settings)
    monkeypatch.setattr(dev, "load_tool_settings", lambda: tools)
    monkeypatch.setattr(dev, "check_startup_environment", lambda: None)
    assert dev.main(["dev"]) == 1
    error = capsys.readouterr().err
    assert error == "Local process startup failed.\n"
    assert "SECRET" not in error
    assert history == ["api:terminate", "api:wait", "mcp:terminate", "mcp:wait"]
    assert len(commands) == 3
    assert history.count("mcp:wait") == 1
    assert history.count("api:wait") == 1
    assert signal_calls[-2:] == [
        (int(signal.SIGINT), signal.SIG_DFL),
        (int(signal.SIGTERM), signal.SIG_DFL),
    ]


@pytest.mark.parametrize(("node", "vite_exists"), [(None, True), ("/usr/bin/node", False)])
def test_missing_node_or_vite_prevents_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    node: str | None,
    vite_exists: bool,
) -> None:
    settings, tools = configuration(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name: node)
    monkeypatch.setattr(Path, "is_file", lambda self: vite_exists)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected process")
    )
    assert dev.run_dev(settings, tools) == 1
    assert capsys.readouterr().err == "Install frontend dependencies before make dev.\n"


def test_stop_race_exit_codes_and_pre_mcp_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dev._stop(None)
    settings, tools = configuration(tmp_path)
    done = FakeProcess("done", 4)
    commands, _, _ = install_runtime(monkeypatch, [done])
    monkeypatch.setattr(
        dev, "_health_ready", lambda url: pytest.fail("health called after child exit")
    )
    assert dev.run_dev(settings, tools) == 4
    assert len(commands) == 1
    assert done.actions == ["wait"]

    class VanishedProcess(FakeProcess):
        def terminate(self) -> None:
            self.actions.append("terminate")
            raise ProcessLookupError

    vanished = VanishedProcess("vanished")
    commands, installed, _ = install_runtime(monkeypatch, [vanished])

    def vanished_health(url: str) -> bool:
        del url
        handler = installed[int(signal.SIGINT)]
        assert callable(handler)
        handler(int(signal.SIGINT), None)
        return True

    monkeypatch.setattr(dev, "_health_ready", vanished_health)
    assert dev.run_dev(settings, tools) == 130
    assert len(commands) == 1
    assert vanished.actions == ["terminate", "wait"]
    assert [dev._exit_code(code) for code in (0, 7, -9)] == [1, 7, 137]

    pre_spawn_commands: list[tuple[Sequence[str], Path, bool, Mapping[str, str] | None]] = []
    handlers: dict[int, object] = {}
    restored: list[tuple[int, object]] = []

    def popen(
        command: Sequence[str],
        *,
        cwd: Path,
        start_new_session: bool,
        env: Mapping[str, str] | None = None,
    ) -> FakeProcess:
        pre_spawn_commands.append((command, cwd, start_new_session, env))
        return FakeProcess("unexpected")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/node")
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.SIG_DFL)

    def install(sig: int, handler: object) -> object:
        handlers[int(sig)] = handler
        if sig == signal.SIGTERM and callable(handler):
            interrupt = handlers[int(signal.SIGINT)]
            assert callable(interrupt)
            interrupt(int(signal.SIGINT), None)
        restored.append((int(sig), handler))
        return signal.SIG_DFL

    monkeypatch.setattr(signal, "signal", install)
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert dev.run_dev(settings, tools) == 130
    assert pre_spawn_commands == []
    assert restored[-2:] == [
        (int(signal.SIGINT), signal.SIG_DFL),
        (int(signal.SIGTERM), signal.SIG_DFL),
    ]
