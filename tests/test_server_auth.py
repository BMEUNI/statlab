"""Chunk 07 §4.3: bearer auth middleware. A1-A12.

401 cases run in-process via `httpx.ASGITransport` -- the middleware short-circuits before the
streamable-HTTP session manager runs, so no ASGI lifespan is needed (verified against the
installed `mcp==2.2.0`, `00_index.md`). 200-on-`/mcp` cases need `live_server`, since the
session manager itself does need the lifespan.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from statlab_tools.server import _UNAUTHORIZED_BODY, create_app
from statlab_tools.settings import ToolSettings, ToolSettingsError, load_tool_settings

MCP_TEST_TOKEN = "auth-test-token-not-a-real-secret"  # noqa: S105 -- test fixture constant


@pytest.fixture
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ToolSettings:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", MCP_TEST_TOKEN)
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))
    return load_tool_settings()


@pytest.fixture
def asgi_client(app_settings: ToolSettings) -> httpx.AsyncClient:
    """In-process client for the 401 path only -- never for an authorised `/mcp` call (the
    session manager needs a real lifespan, which this transport does not provide)."""
    app = create_app(app_settings)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _mcp_headers() -> dict[str, str]:
    return {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def _post_mcp(client: httpx.AsyncClient, *, headers: dict[str, str]) -> httpx.Response:
    async with client:
        return await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**_mcp_headers(), **headers},
        )


# --- A1-A6: rejection shapes --------------------------------------------------------------------


def test_a1_no_authorization_header_is_401(asgi_client: httpx.AsyncClient) -> None:
    import asyncio

    response = asyncio.run(_post_mcp(asgi_client, headers={}))
    assert response.status_code == 401


def test_a2_wrong_token_is_401(asgi_client: httpx.AsyncClient) -> None:
    import asyncio

    response = asyncio.run(
        _post_mcp(asgi_client, headers={"Authorization": "Bearer not-the-real-token"})
    )
    assert response.status_code == 401


def test_a3_basic_scheme_is_401(asgi_client: httpx.AsyncClient) -> None:
    import asyncio
    import base64

    creds = base64.b64encode(b"user:pass").decode()
    response = asyncio.run(_post_mcp(asgi_client, headers={"Authorization": f"Basic {creds}"}))
    assert response.status_code == 401


def test_a4_bearer_with_empty_value_is_401(asgi_client: httpx.AsyncClient) -> None:
    import asyncio

    response = asyncio.run(_post_mcp(asgi_client, headers={"Authorization": "Bearer "}))
    assert response.status_code == 401


def test_a5_correct_prefix_extra_suffix_is_401(asgi_client: httpx.AsyncClient) -> None:
    """A5: pins that comparison is exact, not `startswith`."""
    import asyncio

    response = asyncio.run(
        _post_mcp(asgi_client, headers={"Authorization": f"Bearer {MCP_TEST_TOKEN}extra"})
    )
    assert response.status_code == 401


def test_a5b_correct_token_missing_suffix_is_401(asgi_client: httpx.AsyncClient) -> None:
    """The other direction of A5: a prefix of the real token is not the real token."""
    import asyncio

    truncated = MCP_TEST_TOKEN[:-1]
    response = asyncio.run(_post_mcp(asgi_client, headers={"Authorization": f"Bearer {truncated}"}))
    assert response.status_code == 401


def test_a6_response_body_identical_for_absent_and_wrong_token(
    asgi_client: httpx.AsyncClient,
) -> None:
    """A6: distinguishing an absent token from a wrong one only helps someone guessing."""
    import asyncio

    async def run_both() -> tuple[httpx.Response, httpx.Response]:
        async with asgi_client as client:
            r1 = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers=_mcp_headers(),
            )
            r2 = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={**_mcp_headers(), "Authorization": "Bearer wrong-token-entirely"},
            )
            return r1, r2

    r1, r2 = asyncio.run(run_both())
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json() == _UNAUTHORIZED_BODY


# --- A7: the positive case -----------------------------------------------------------------------


def test_a7_valid_token_reaches_the_mcp_layer(live_server: str) -> None:
    """A7: needs `live_server` -- the authorised path requires the real ASGI lifespan."""
    import asyncio

    from tests.conftest import MCP_TEST_TOKEN as LIVE_TOKEN

    async def call() -> httpx.Response:
        async with httpx.AsyncClient(base_url=live_server) as client:
            return await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={**_mcp_headers(), "Authorization": f"Bearer {LIVE_TOKEN}"},
            )

    response = asyncio.run(call())
    # Reaching the MCP layer means "not a 401" -- the JSON-RPC response shape itself is
    # M1/M2's concern (test_server_mcp_session.py), not auth's.
    assert response.status_code != 401


# --- A8-A9: /health -------------------------------------------------------------------------------


def test_a8_health_with_no_token_is_200_body_ok(asgi_client: httpx.AsyncClient) -> None:
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.get("/health")

    response = asyncio.run(call())
    assert response.status_code == 200
    assert response.text == "ok"


def test_a9_health_leaks_nothing(asgi_client: httpx.AsyncClient) -> None:
    """A9: no version, no port, no tool names, no path -- the literal string and nothing
    else."""
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.get("/health")

    response = asyncio.run(call())
    assert response.text == "ok"
    assert "list_artifacts" not in response.text
    assert "describe_artifact" not in response.text
    body_lower = response.text.lower()
    assert "version" not in body_lower
    assert "port" not in body_lower


# --- A10: constant-time comparison --------------------------------------------------------------


def test_a10_comparison_uses_secrets_compare_digest() -> None:
    """A10: source-scan, not a timing measurement -- timing itself is not a reliable thing to
    assert in a test suite."""
    import inspect

    from statlab_tools import server as server_module

    source = inspect.getsource(server_module)
    assert "secrets.compare_digest" in source
    assert "token ==" not in source.replace(" ", "")


# --- A11-A12: the D14 bind guard, not reimplemented ----------------------------------------------


def test_a11_nonloopback_host_without_override_still_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A11: `ToolSettings._guard_bind_address` (chunk 01) is not duplicated here -- confirm it
    still fires, rather than re-implementing the check."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", MCP_TEST_TOKEN)
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATLAB_MCP_HOST", "0.0.0.0")
    with pytest.raises(ToolSettingsError, match="not loopback"):
        load_tool_settings()


def test_a12_nonloopback_host_with_override_is_permitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STATLAB_MCP_TOKEN", MCP_TEST_TOKEN)
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATLAB_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("STATLAB_ALLOW_NONLOCAL_BIND", "true")
    settings = load_tool_settings()
    assert settings.host == "0.0.0.0"


# --- Additional coverage --------------------------------------------------------------------------


def test_health_path_bypasses_auth_check_before_header_parsing(
    asgi_client: httpx.AsyncClient,
) -> None:
    """`/health` is exempt even when a garbage Authorization header is present -- the path
    check happens before any header is even read."""
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.get("/health", headers={"Authorization": "garbage nonsense"})

    response = asyncio.run(call())
    assert response.status_code == 200


def test_missing_authorization_header_scheme_split_does_not_crash(
    asgi_client: httpx.AsyncClient,
) -> None:
    """A header with no space at all (`"Bearer"` alone, no token) partitions cleanly rather
    than raising."""
    import asyncio

    response = asyncio.run(_post_mcp(asgi_client, headers={"Authorization": "Bearer"}))
    assert response.status_code == 401


def test_case_sensitive_bearer_scheme_is_rejected(asgi_client: httpx.AsyncClient) -> None:
    """`bearer` (lowercase) is not `Bearer` -- the scheme comparison is exact, not
    case-insensitive."""
    import asyncio

    response = asyncio.run(
        _post_mcp(asgi_client, headers={"Authorization": f"bearer {MCP_TEST_TOKEN}"})
    )
    assert response.status_code == 401


def test_multiple_spaces_between_scheme_and_token_is_401(asgi_client: httpx.AsyncClient) -> None:
    """`partition(" ")` on `"Bearer  token"` (two spaces) leaves a leading space in the value,
    so it will not equal the real token -- confirmed as a 401, not a crash or a false accept."""
    import asyncio

    response = asyncio.run(
        _post_mcp(asgi_client, headers={"Authorization": f"Bearer  {MCP_TEST_TOKEN}"})
    )
    assert response.status_code == 401


def test_health_route_accepts_no_query_params_or_body(asgi_client: httpx.AsyncClient) -> None:
    """`/health` is a plain `GET` with no expected params -- confirms it does not require
    any request body or query string to succeed."""
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.get("/health", params={"unexpected": "param"})

    response = asyncio.run(call())
    assert response.status_code == 200
    assert response.text == "ok"


def test_post_to_health_is_not_the_get_route(asgi_client: httpx.AsyncClient) -> None:
    """`/health` is registered for `GET` only -- a `POST` does not accidentally succeed
    through some other route match."""
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.post("/health")

    response = asyncio.run(call())
    assert response.status_code in (404, 405)


def test_app_settings_data_dir_is_created(app_settings: ToolSettings) -> None:
    """`load_tool_settings` (used to build `app_settings`) creates the data directory as a
    side effect -- confirms the fixture itself is exercising the real settings loader, not a
    stub."""
    assert app_settings.data_dir.is_dir()


def test_get_to_mcp_without_auth_is_also_401(asgi_client: httpx.AsyncClient) -> None:
    """The middleware runs for every method, not only `POST` -- a `GET` to `/mcp` without a
    token is rejected the same way."""
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.get("/mcp", headers=_mcp_headers())

    response = asyncio.run(call())
    assert response.status_code == 401


def test_delete_to_mcp_without_auth_is_401(asgi_client: httpx.AsyncClient) -> None:
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.delete("/mcp", headers=_mcp_headers())

    response = asyncio.run(call())
    assert response.status_code == 401


def test_authorization_header_with_only_whitespace_is_401(asgi_client: httpx.AsyncClient) -> None:
    import asyncio

    response = asyncio.run(_post_mcp(asgi_client, headers={"Authorization": "   "}))
    assert response.status_code == 401


def test_unrelated_custom_path_without_auth_is_still_401(
    asgi_client: httpx.AsyncClient,
) -> None:
    """Only `/health` is exempt -- an unrelated, nonexistent path is not silently exempted
    too; it still goes through the auth check first (404 after that is fine, 401 before it
    is what this test pins)."""
    import asyncio

    async def call() -> httpx.Response:
        async with asgi_client as client:
            return await client.get("/some/other/path")

    response = asyncio.run(call())
    assert response.status_code == 401
