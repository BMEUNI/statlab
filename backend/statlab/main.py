"""Lazy FastAPI composition root."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from types import FrameType

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from statlab.api.access import authorize
from statlab.api.assistant import assistant_router
from statlab.api.auth import auth_router, members_router
from statlab.api.inspect import inspect_router
from statlab.api.projects import APIResources, projects_router
from statlab.api.runs import RunRegistry, runs_router
from statlab.api.stream import stream_router
from statlab.config import Settings, ensure_writable, load_settings
from statlab.domains.routing import NODE_ROLES
from statlab.domains.tabular import TABULAR_DOMAIN
from statlab.errors import (
    ConfigError,
    LlmCallError,
    LlmOutputError,
    StatlabError,
    ToolCallError,
)
from statlab.llm.client import LlmClient
from statlab.llm.routing import ModelRouter, check_provider_environment, load_routing_environment
from statlab.llm.skills import load_skill
from statlab.mcp_client import McpClient
from statlab.persistence.db import connect, migrate


def create_app(
    settings: Settings | None = None,
    *,
    mcp_session: object | None = None,
    llm_client: LlmClient | None = None,
) -> FastAPI:
    active_registry: RunRegistry | None = None

    def resources() -> APIResources:
        return registry().resources

    def registry() -> RunRegistry:
        if active_registry is None:
            raise HTTPException(503, "application resources are unavailable")
        return active_registry

    async def admitted_request() -> AsyncIterator[None]:
        async with registry().request():
            yield

    async def authenticated(request: Request) -> None:
        # async on purpose: a sync dependency runs in a thread, and the SQLite connection is
        # bound to the event-loop thread.
        authorize(request, resources().conn)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal active_registry
        selected = settings if settings is not None else load_settings()
        ensure_writable(selected.data_dir)
        ensure_writable(selected.db_path.parent)
        conn = connect(selected.db_path)
        client = McpClient(selected, conn, session=mcp_session)
        try:
            migrate(conn)
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(client)
                environment = load_routing_environment()
                router = ModelRouter(NODE_ROLES, environment)
                policy = load_skill(
                    TABULAR_DOMAIN.skill_file,
                    spine=TABULAR_DOMAIN.nodes,
                    tools=client.tools,
                )
                models = tuple(
                    dict.fromkeys(
                        (
                            *router.models,
                            *(
                                router.model_for(skill.node, skill_model=skill.model)
                                for skill in policy.nodes
                            ),
                        )
                    )
                )
                check_provider_environment(models, environment=environment)
                os.environ.update(environment)
                active_registry = RunRegistry(
                    APIResources(
                        selected,
                        conn,
                        client,
                        llm_client or LlmClient(selected),
                        router,
                        policy,
                        environment,
                    )
                )
                application.state.run_registry = active_registry
                try:
                    await active_registry.boot()
                    yield
                finally:
                    try:
                        await active_registry.shutdown()
                    finally:
                        application.state.run_registry = None
                        active_registry = None
        finally:
            conn.close()

    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(admitted_request, scope="request"), Depends(authenticated)],
    )
    app.include_router(projects_router(resources))
    app.include_router(auth_router(resources))
    app.include_router(members_router(resources))
    app.include_router(assistant_router(resources))
    app.include_router(inspect_router(resources))
    app.include_router(runs_router(registry))
    app.include_router(stream_router(registry))

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request
        del exc
        return JSONResponse(status_code=422, content={"detail": "invalid request"})

    @app.exception_handler(ToolCallError)
    async def tool_error(request: Request, exc: ToolCallError) -> JSONResponse:
        del request
        status = {
            "tool_param": 422,
            "data": 422,
            "transient": 503,
            "bug": 500,
        }[exc.failure.error_class]
        return JSONResponse(status_code=status, content={"detail": "setup tool failed"})

    @app.exception_handler(LlmOutputError)
    async def llm_output_error(request: Request, exc: LlmOutputError) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=502, content={"detail": "LLM output failed"})

    @app.exception_handler(LlmCallError)
    async def llm_error(request: Request, exc: LlmCallError) -> JSONResponse:
        del request
        status = 503 if exc.failure.error_class == "transient" else 502
        return JSONResponse(status_code=status, content={"detail": "LLM request failed"})

    @app.exception_handler(StatlabError)
    async def statlab_error(request: Request, exc: StatlabError) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.exception_handler(OSError)
    @app.exception_handler(sqlite3.Error)
    async def foreign_error(request: Request, exc: OSError | sqlite3.Error) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    @app.exception_handler(ConfigError)
    async def config_error(request: Request, exc: ConfigError) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=503, content={"detail": "application unavailable"})

    return app


class _RunServer(uvicorn.Server):
    def __init__(self, application: FastAPI, settings: Settings) -> None:
        self._application = application
        super().__init__(
            uvicorn.Config(application, host=settings.api_host, port=settings.api_port)
        )

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        registry = getattr(self._application.state, "run_registry", None)
        if isinstance(registry, RunRegistry):
            registry.begin_shutdown()
        if self.should_exit:
            return
        super().handle_exit(sig, frame)


def main() -> int:
    try:
        settings = load_settings()
        application = create_app(settings)
        server = _RunServer(application, settings)
        server.run()
        return 0
    except ConfigError:
        print(
            "Backend configuration is invalid. Check .env and configuration values.",
            file=sys.stderr,
        )
        return 2


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
