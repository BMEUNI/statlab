"""Sign-up, sign-in, sessions, members and access guard over HTTP (chunk 71)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from statlab.config import Settings
from statlab.llm.client import LlmClient
from statlab.main import create_app
from statlab_contracts.layout import ProjectPaths

from tests.auth_support import PASSWORD, sign_in
from tests.test_api_assistant import _open_assistant_api
from tests.test_api_projects import SetupSession, _open_project_api, _seed_run, _write


@asynccontextmanager
async def _open_auth_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings]]:
    import statlab.main as main_module

    monkeypatch.setattr(main_module, "check_provider_environment", lambda *args, **kwargs: None)
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        max_concurrent_llm=1,
    )
    app = create_app(settings, mcp_session=SetupSession(), llm_client=LlmClient(settings))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client, settings


def _db(settings: Settings, sql: str, params: tuple[str, ...]) -> None:
    _write(settings.db_path, sql, params)


@pytest.mark.asyncio
async def test_register_makes_first_admin_and_signs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_auth_api(tmp_path, monkeypatch) as (client, _settings):
        config = await client.get("/api/auth/config")
        assert config.json() == {"signup_open": True, "has_users": False}
        unauthorized = await client.get("/api/auth/me")
        assert unauthorized.status_code == 401
        assert unauthorized.json() == {"detail": "sign in first"}
        no_header = await client.post(
            "/api/auth/register",
            json={"email": "owner@example.test", "name": "Owner", "password": PASSWORD},
        )
        assert no_header.status_code == 403
        assert no_header.json() == {"detail": "request is missing the X-Statlab-Csrf header"}
        client.headers["X-Statlab-Csrf"] = "1"
        registered = await client.post(
            "/api/auth/register",
            json={"email": " Owner@Example.TEST ", "name": " Owner ", "password": PASSWORD},
        )
        assert registered.status_code == 201
        body = registered.json()
        assert body["id"].startswith("u_")
        assert {key: value for key, value in body.items() if key != "id"} == {
            "email": "owner@example.test",
            "name": "Owner",
            "is_admin": True,
        }
        cookie = registered.headers["set-cookie"].lower()
        assert "statlab_session=" in cookie
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "path=/" in cookie
        assert "secure" not in cookie
        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json() == body
        config = await client.get("/api/auth/config")
        assert config.json() == {"signup_open": True, "has_users": True}
        client.cookies.clear()
        second = await client.post(
            "/api/auth/register",
            json={"email": "second@example.test", "name": "Second", "password": PASSWORD},
        )
        assert second.status_code == 201
        assert second.json()["is_admin"] is False
        duplicate = await client.post(
            "/api/auth/register",
            json={"email": "OWNER@example.test", "name": "Owner", "password": PASSWORD},
        )
        assert duplicate.status_code == 409
        assert duplicate.json() == {"detail": "an account with this email already exists"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"email": "nope", "name": "Owner", "password": PASSWORD},
        {"email": "owner@example.test", "name": "Owner", "password": "short"},
        {"email": "owner@example.test", "name": "  ", "password": PASSWORD},
    ],
)
async def test_register_rejects_bad_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, str]
) -> None:
    async with _open_auth_api(tmp_path, monkeypatch) as (client, _settings):
        client.headers["X-Statlab-Csrf"] = "1"
        rejected = await client.post("/api/auth/register", json=body)
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_login_logout_and_throttle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_auth_api(tmp_path, monkeypatch) as (client, _settings):
        await sign_in(client)
        logout = await client.post("/api/auth/logout")
        assert logout.status_code == 204
        cleared = logout.headers["set-cookie"].lower()
        assert "statlab_session=" in cleared
        assert "max-age=0" in cleared
        assert (await client.get("/api/auth/me")).status_code == 401
        wrong = await client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": "wrong password"}
        )
        assert wrong.status_code == 401
        assert wrong.json() == {"detail": "email or password is incorrect"}
        nobody = await client.post(
            "/api/auth/login", json={"email": "nobody@example.test", "password": PASSWORD}
        )
        assert nobody.status_code == 401
        assert nobody.json() == {"detail": "email or password is incorrect"}
        correct = await client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
        )
        assert correct.status_code == 200
        assert correct.json()["is_admin"] is True
        assert (await client.get("/api/auth/me")).status_code == 200
        for _ in range(5):
            failed = await client.post(
                "/api/auth/login",
                json={"email": "owner@example.test", "password": "wrong password"},
            )
            assert failed.status_code == 401
        blocked = await client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
        )
        assert blocked.status_code == 429
        assert blocked.json() == {"detail": "too many failed sign-ins; try again in 15 minutes"}
        client.cookies.clear()
        assert (await client.post("/api/auth/logout")).status_code == 204


@pytest.mark.asyncio
async def test_expired_disabled_and_deleted_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_auth_api(tmp_path, monkeypatch) as (client, settings):
        await sign_in(client)
        _db(
            settings,
            "UPDATE auth_sessions SET expires_at = ?",
            ("2000-01-01T00:00:00+00:00",),
        )
        assert (await client.get("/api/auth/me")).status_code == 401
        relogin = await client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
        )
        assert relogin.status_code == 200
        token = client.cookies["statlab_session"]
        assert (await client.post("/api/auth/logout")).status_code == 204
        client.cookies.set("statlab_session", token)
        assert (await client.get("/api/auth/me")).status_code == 401
        relogin = await client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
        )
        assert relogin.status_code == 200
        _db(settings, "UPDATE users SET disabled = ?", ("1",))
        assert (await client.get("/api/auth/me")).status_code == 401
        disabled = await client.post(
            "/api/auth/login", json={"email": "owner@example.test", "password": PASSWORD}
        )
        assert disabled.status_code == 403
        assert disabled.json() == {"detail": "this account is disabled"}


@pytest.mark.asyncio
async def test_members_and_project_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, _settings, _session, demo):
        demo_id = demo["id"]
        owner = (await client.get("/api/auth/me")).json()
        members = await client.get(f"/api/projects/{demo_id}/members")
        assert members.status_code == 200
        assert len(members.json()) == 1
        assert members.json()[0]["user_id"] == owner["id"]
        assert members.json()[0]["role"] == "owner"
        assert members.json()[0]["email"] == "owner@example.test"

        await sign_in(client, "bob@example.test", "Bob")
        assert (await client.get("/api/projects")).json() == []
        hidden = {"detail": "project not found"}
        seen = await client.get(f"/api/projects/{demo_id}")
        assert seen.status_code == 404
        assert seen.json() == hidden
        seen_members = await client.get(f"/api/projects/{demo_id}/members")
        assert seen_members.status_code == 404
        assert seen_members.json() == hidden
        missing = await client.get("/api/projects/p_missing")
        assert missing.status_code == 404
        assert missing.json() == hidden
        assert (await client.get("/api/vocabulary")).status_code == 200
        created = await client.post("/api/projects", json={"slug": "bobs", "name": "Bobs"})
        assert created.status_code == 201
        bobs_id = created.json()["id"]
        assert [p["name"] for p in (await client.get("/api/projects")).json()] == ["Bobs"]

        carol = await sign_in(client, "carol@example.test", "Carol")
        bob = await sign_in(client, "bob@example.test", "Bob")
        added = await client.post(
            f"/api/projects/{bobs_id}/members", json={"email": " CAROL@example.test "}
        )
        assert added.status_code == 201
        assert added.json()["role"] == "member"
        again = await client.post(
            f"/api/projects/{bobs_id}/members", json={"email": "carol@example.test"}
        )
        assert again.status_code == 409
        assert again.json() == {"detail": "this person is already a member"}
        nobody = await client.post(
            f"/api/projects/{bobs_id}/members", json={"email": "nobody@example.test"}
        )
        assert nobody.status_code == 404
        assert nobody.json() == {"detail": "no account uses this email"}
        removed = await client.delete(f"/api/projects/{bobs_id}/members/{carol['id']}")
        assert removed.status_code == 204
        readded = await client.post(
            f"/api/projects/{bobs_id}/members", json={"email": "carol@example.test"}
        )
        assert readded.status_code == 201
        unknown = await client.delete(f"/api/projects/{bobs_id}/members/u_missing")
        assert unknown.status_code == 404
        assert unknown.json() == {"detail": "member not found"}

        await sign_in(client, "carol@example.test", "Carol")
        assert [p["name"] for p in (await client.get("/api/projects")).json()] == ["Bobs"]
        owner_add = await client.post(
            f"/api/projects/{bobs_id}/members", json={"email": "owner@example.test"}
        )
        assert owner_add.status_code == 403
        assert owner_add.json() == {"detail": "only the project owner can add members"}
        owner_remove = await client.delete(f"/api/projects/{bobs_id}/members/{bob['id']}")
        assert owner_remove.status_code == 409
        assert owner_remove.json() == {"detail": "the project owner cannot be removed"}
        delete_bobs = await client.delete(f"/api/projects/{bobs_id}")
        assert delete_bobs.status_code == 403
        assert delete_bobs.json() == {"detail": "only the project owner can delete this project"}

        await sign_in(client, "bob@example.test", "Bob")
        bob_adds_owner = await client.post(
            f"/api/projects/{bobs_id}/members", json={"email": "owner@example.test"}
        )
        assert bob_adds_owner.status_code == 201
        await sign_in(client, "carol@example.test", "Carol")
        carol_removes_owner = await client.delete(f"/api/projects/{bobs_id}/members/{owner['id']}")
        assert carol_removes_owner.status_code == 403
        assert carol_removes_owner.json() == {"detail": "only the project owner can remove members"}
        carol_leaves = await client.delete(f"/api/projects/{bobs_id}/members/{carol['id']}")
        assert carol_leaves.status_code == 204
        assert (await client.get(f"/api/projects/{bobs_id}")).status_code == 404

        await sign_in(client)
        assert (await client.get(f"/api/projects/{bobs_id}")).status_code == 200
        assert (await client.delete(f"/api/projects/{bobs_id}")).status_code == 204


@pytest.mark.asyncio
async def test_run_and_artifact_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, demo):
        _seed_run(
            settings.db_path,
            str(demo["id"]),
            ProjectPaths(settings.data_dir, "demo").root,
            "completed",
        )
        await sign_in(client, "bob@example.test", "Bob")
        run = await client.get("/api/runs/r_seed")
        assert run.status_code == 404
        assert run.json() == {"detail": "run not found"}
        events = await client.get("/api/runs/r_seed/events")
        assert events.status_code == 404
        assert events.json() == {"detail": "run not found"}
        artifact = await client.get("/api/artifacts/a_seed")
        assert artifact.status_code == 404
        assert artifact.json() == {"detail": "artifact not found"}
        assert (await client.get("/api/runs/r_0123456789abcdef")).status_code == 404
        assert (await client.get("/api/artifacts/a_0123456789abcdef")).status_code == 404


@pytest.mark.asyncio
async def test_assistant_is_scoped_to_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_assistant_api(tmp_path, monkeypatch, []) as (
        client,
        _settings,
        _session,
        demo,
        calls,
    ):
        await sign_in(client, "bob@example.test", "Bob")
        turn = await client.post(
            "/api/assistant/turn",
            json={
                "messages": [{"role": "user", "text": "hi"}],
                "state": {"project_id": demo["id"]},
            },
        )
        assert turn.status_code == 404
        assert turn.json() == {"detail": "project not found"}
        assert calls == []
