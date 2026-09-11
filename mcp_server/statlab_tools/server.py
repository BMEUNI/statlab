"""The ASGI app: MCP over Streamable HTTP at `/mcp`, plus an unauthenticated `/health`.

PLAN.md D13, D14. Static bearer auth is Starlette middleware, not `token_verifier` -- passing
`token_verifier` without `auth=` raises `ValueError`, and `AuthSettings` requires
`issuer_url`/`resource_server_url`, the OAuth resource-server path. A shared secret is not an
OAuth issuer; declaring a fake one would be a lie in config and would expose OAuth metadata
routes nobody asked for. See `00_index.md`'s MCP SDK 2.2.0 facts, verified against the
installed package before this module was written.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from statlab_tools import tool as tool_module
from statlab_tools import tools as _tools  # noqa: F401 -- import registers every tool module
from statlab_tools.settings import ToolSettings

_UNAUTHORIZED_BODY: dict[str, str] = {"error": "unauthorized"}
"""Identical for an absent and a wrong token (A6). Distinguishing them only helps someone
guessing which is closer to correct."""


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    """Rejects every request without `Authorization: Bearer <token>` matching
    `settings.mcp_token`, except `/health`.

    Token comparison uses `secrets.compare_digest`, not `==` -- a plain equality check on a
    secret is a timing side channel, and this is the one place in the project where that
    applies.
    """

    def __init__(self, app: object, *, token: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/health":
            return await call_next(request)

        header = request.headers.get("authorization", "")
        scheme, _, value = header.partition(" ")
        if scheme != "Bearer" or not value or not secrets.compare_digest(value, self._token):
            return JSONResponse(_UNAUTHORIZED_BODY, status_code=401)

        return await call_next(request)


async def _health(request: Request) -> Response:
    """Liveness for `make dev`. Unauthenticated by design (D14): a probe needing a secret fails
    for reasons unrelated to liveness. Returns the literal string `ok` and nothing else -- no
    version, no port, no tool count, no path. Loopback-bound like every other route here, so it
    reveals only that a process is up."""
    del request
    return PlainTextResponse("ok")


def create_app(settings: ToolSettings) -> Starlette:
    """Build the ASGI app: MCP over Streamable HTTP at `/mcp`, plus an unauthenticated
    `/health`. Importing `statlab_tools.tools` (via the module-level import above) registers
    every tool onto `tool_module.server` before this function ever runs, so the app it returns
    always serves the full, current registry."""
    mcp_app = tool_module.server.streamable_http_app(streamable_http_path="/mcp")
    mcp_app.add_route("/health", _health, methods=["GET"])
    mcp_app.user_middleware.insert(
        0, Middleware(_BearerAuthMiddleware, token=settings.mcp_token.get_secret_value())
    )
    mcp_app.middleware_stack = mcp_app.build_middleware_stack()
    return mcp_app
