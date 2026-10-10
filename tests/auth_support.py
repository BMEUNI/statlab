"""Shared sign-in helper for API tests (chunk 71). Imports no test modules."""

from __future__ import annotations

from typing import cast

import httpx
from pydantic import JsonValue

PASSWORD = "correct horse battery"  # noqa: S105 -- test value, not a real credential


async def sign_in(
    client: httpx.AsyncClient, email: str = "owner@example.test", name: str = "Owner"
) -> dict[str, JsonValue]:
    client.headers["X-Statlab-Csrf"] = "1"
    registered = await client.post(
        "/api/auth/register", json={"email": email, "name": name, "password": PASSWORD}
    )
    if registered.status_code == 409:
        logged_in = await client.post(
            "/api/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert logged_in.status_code == 200
        return cast(dict[str, JsonValue], logged_in.json())
    assert registered.status_code == 201
    return cast(dict[str, JsonValue], registered.json())
