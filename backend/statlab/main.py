"""Lazy FastAPI composition root."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from statlab.api.projects import APIResources, projects_router
from statlab.api.runs import RunRegistry, runs_router
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
    active_resources: APIResources | None = None
    active_registry: RunRegistry | None = None

    def resources() -> APIResources:
        if active_resources is None:
            raise HTTPException(503, "application resources are unavailable")
        return active_resources

    def registry() -> RunRegistry:
        if active_registry is None:
            raise HTTPException(503, "application resources are unavailable")
        return active_registry

    async def admitted_request() -> AsyncIterator[None]:
        async with registry().request():
            yield

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        nonlocal active_resources, active_registry
        del application
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
                active_resources = APIResources(
                    selected,
                    conn,
                    client,
                    llm_client or LlmClient(selected),
                    router,
                    policy,
                    environment,
                )
                active_registry = RunRegistry(active_resources)
                try:
                    await active_registry.boot()
                    yield
                finally:
                    try:
                        await active_registry.shutdown()
                    finally:
                        active_registry = None
                        active_resources = None
        finally:
            conn.close()

    app = FastAPI(
        lifespan=lifespan,
        dependencies=[Depends(admitted_request, scope="request")],
    )
    app.include_router(projects_router(resources))
    app.include_router(runs_router(registry))

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


app = create_app()
