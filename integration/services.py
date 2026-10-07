"""MCP subprocess and in-process API server for the harness."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from statlab.api.runs import RunRegistry
from statlab.config import Settings
from statlab.llm.client import LlmClient
from statlab.main import create_app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


class McpProcess:
    def __init__(self, *, data_dir: Path, token: str, log_path: Path) -> None:
        self._data_dir = data_dir
        self._token = token
        self._log_path = log_path
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> str:
        port = free_port()
        env = {
            **os.environ,
            "STATLAB_MCP_TOKEN": self._token,
            "STATLAB_DATA_DIR": str(self._data_dir),
            "STATLAB_MCP_HOST": "127.0.0.1",
            "STATLAB_MCP_PORT": str(port),
            "STATLAB_FIT_PERMITS": "1",
        }
        with self._log_path.open("ab") as log:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "statlab_tools"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and self._proc.poll() is None:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
                if response.status_code == 200:
                    return f"http://127.0.0.1:{port}/mcp"
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        self.stop()
        raise RuntimeError(f"MCP server did not become healthy; see {self._log_path}")

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


class _HarnessServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        """Leave SIGINT/SIGTERM to the harness so Ctrl-C reaches its cleanup."""
        yield


class ApiServer:
    def __init__(self, *, settings: Settings, llm_client: LlmClient | None) -> None:
        self._settings = settings
        self._llm_client = llm_client
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self._app: FastAPI | None = None

    async def start(self) -> str:
        app = create_app(self._settings, llm_client=self._llm_client)
        self._app = app
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self._settings.api_port,
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        server = _HarnessServer(config)
        self._server = server
        task = asyncio.create_task(server.serve())
        self._task = task
        deadline = time.monotonic() + 120
        while not server.started and not task.done() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if task.done():
            exc = task.exception()
            if exc is not None:
                raise exc
            raise RuntimeError("API stopped during startup")
        return f"http://127.0.0.1:{self._settings.api_port}"

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        registry = getattr(self._app.state, "run_registry", None) if self._app else None
        if isinstance(registry, RunRegistry):
            registry.begin_shutdown()
        self._server.should_exit = True
        await asyncio.wait_for(self._task, 120)
        self._server = None
        self._task = None


def build_settings(
    *, token: str, mcp_url: str, data_dir: Path, db_path: Path, api_port: int
) -> Settings:
    return Settings(
        mcp_token=SecretStr(token),
        mcp_url=mcp_url,
        data_dir=data_dir,
        db_path=db_path,
        api_port=api_port,
    )
