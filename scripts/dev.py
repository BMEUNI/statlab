from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from types import FrameType
from urllib.parse import urlsplit

from statlab.config import Settings, load_settings
from statlab.errors import ConfigError
from statlab.llm.routing import check_startup_environment
from statlab_tools.settings import ToolSettings, ToolSettingsError, load_tool_settings

_ROOT = Path(__file__).resolve().parents[1]
_READY_SECONDS = 30.0
_POLL_SECONDS = 0.1


def check_configuration() -> tuple[Settings, ToolSettings]:
    settings = load_settings()
    try:
        tools = load_tool_settings()
    except ToolSettingsError as exc:
        raise ConfigError(
            "Local development configuration is inconsistent.",
            missing=exc.missing,
            cause=exc,
        ) from exc
    check_startup_environment()
    return settings, tools


def _host_family(host: str) -> str | None:
    if host in {"127.0.0.1", "localhost"}:
        return "ipv4"
    if host == "::1":
        return "ipv6"
    return None


def local_endpoints(settings: Settings, tools: ToolSettings) -> tuple[str, str]:
    api_family = _host_family(settings.api_host)
    tool_family = _host_family(tools.host)
    try:
        parsed = urlsplit(settings.mcp_url)
        parsed_port = parsed.port
    except ValueError as exc:
        raise ConfigError("Local development configuration is inconsistent.", cause=exc) from exc
    if (
        api_family is None
        or tool_family is None
        or settings.data_dir != tools.data_dir
        or settings.mcp_token.get_secret_value() != tools.mcp_token.get_secret_value()
        or parsed.scheme != "http"
        or parsed.path != "/mcp"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _host_family(parsed.hostname or "") != tool_family
        or parsed_port != tools.port
    ):
        raise ConfigError("Local development configuration is inconsistent.")
    api_host = f"[{settings.api_host}]" if api_family == "ipv6" else settings.api_host
    tool_host = f"[{tools.host}]" if tool_family == "ipv6" else tools.host
    return f"http://{api_host}:{settings.api_port}", f"http://{tool_host}:{tools.port}/health"


def _health_ready(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1) as response:
            return bool(response.status == 200)
    except urllib.error.URLError, OSError:
        return False


def _exit_code(code: int) -> int:
    if code < 0:
        return 128 + abs(code)
    return code if code != 0 else 1


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        with suppress(ProcessLookupError):
            process.terminate()
    process.wait()


def run_dev(settings: Settings, tools: ToolSettings, ui_args: tuple[str, ...] = ()) -> int:
    api_origin, health_url = local_endpoints(settings, tools)
    node = shutil.which("node")
    vite = _ROOT / "frontend" / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None or not vite.is_file():
        print("Install frontend dependencies before make dev.", file=sys.stderr)
        return 1
    python = _ROOT / ".venv" / "bin" / "python"
    node_path = Path(node).resolve()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    requested_exit: list[int | None] = [None]

    def handle_signal(sig: int, frame: FrameType | None) -> None:
        del frame
        if requested_exit[0] is None:
            requested_exit[0] = 130 if sig == signal.SIGINT else 143

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle_signal)

    mcp: subprocess.Popen[bytes] | None = None
    api: subprocess.Popen[bytes] | None = None
    ui: subprocess.Popen[bytes] | None = None
    try:
        exit_code = requested_exit[0]
        if exit_code is not None:
            return exit_code
        mcp = subprocess.Popen(  # noqa: S603 - fixed executable and module arguments; no shell
            [str(python), "-m", "statlab_tools"], cwd=_ROOT, start_new_session=True
        )
        deadline = time.monotonic() + _READY_SECONDS
        while True:
            exit_code = requested_exit[0]
            if exit_code is not None:
                return exit_code
            code = mcp.poll()
            if code is not None:
                return _exit_code(code)
            ready = _health_ready(health_url)
            exit_code = requested_exit[0]
            if exit_code is not None:
                return exit_code
            code = mcp.poll()
            if code is not None:
                return _exit_code(code)
            if ready:
                break
            if time.monotonic() >= deadline:
                print("MCP readiness timed out.", file=sys.stderr)
                return 1
            time.sleep(_POLL_SECONDS)
        exit_code = requested_exit[0]
        if exit_code is not None:
            return exit_code
        api = subprocess.Popen(  # noqa: S603 - fixed executable and module arguments; no shell
            [str(python), "-m", "statlab.main"], cwd=_ROOT, start_new_session=True
        )
        exit_code = requested_exit[0]
        if exit_code is not None:
            return exit_code
        environment = os.environ.copy()
        environment["STATLAB_API_ORIGIN"] = api_origin
        ui = subprocess.Popen(  # noqa: S603 - fixed executable and module arguments; no shell
            [str(node_path), str(vite), *ui_args],
            cwd=_ROOT / "frontend",
            start_new_session=True,
            env=environment,
        )
        while True:
            exit_code = requested_exit[0]
            if exit_code is not None:
                return exit_code
            for process in (mcp, api, ui):
                code = process.poll()
                if code is not None:
                    return _exit_code(code)
            time.sleep(_POLL_SECONDS)
    finally:
        try:
            try:
                _stop(ui)
            finally:
                try:
                    _stop(api)
                finally:
                    _stop(mcp)
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, previous[sig])


def main(argv: Sequence[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if list(arguments) not in (["check"], ["dev"], ["share"]):
        print("Usage: dev.py check|dev|share", file=sys.stderr)
        return 2
    try:
        settings, tools = check_configuration()
        if arguments[0] == "check":
            print("Configuration OK")
            return 0
        if arguments[0] == "share":
            if not settings.cookie_secure:
                print("make share needs STATLAB_COOKIE_SECURE=true.", file=sys.stderr)
                return 2
            return run_dev(settings, tools, ("preview",))
        return run_dev(settings, tools)
    except ConfigError as exc:
        print("Configuration is invalid. Check .env and configuration values.", file=sys.stderr)
        if exc.missing:
            print("Missing: " + ", ".join(exc.missing), file=sys.stderr)
        return 2
    except OSError:
        print("Local process startup failed.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
