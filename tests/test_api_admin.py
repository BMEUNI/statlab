"""Admin panel API, budget refusals and the admin-only access guard (chunk 71)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import JsonValue

from tests.auth_support import PASSWORD, sign_in
from tests.test_api_projects import _open_project_api, _seed_run, _write
from tests.test_api_runs import FixtureRecords, _create_run, _gate, _open_run_api


@pytest.mark.asyncio
async def test_admin_routes_need_admin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, _settings, _session, _project):
        await sign_in(client, "bob@example.test", "Bob")
        response = await client.get("/api/admin/overview")
        assert response.status_code == 403
        assert response.json() == {"detail": "admins only"}


@pytest.mark.asyncio
async def test_admin_settings_close_signup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, _settings, _session, _project):
        current = await client.get("/api/admin/settings")
        assert current.status_code == 200
        assert current.json() == {
            "default_monthly_limit_usd": None,
            "default_project_limit_usd": None,
            "default_run_limit_usd": None,
            "signup_open": True,
        }
        body = {
            "default_monthly_limit_usd": 5,
            "default_project_limit_usd": None,
            "default_run_limit_usd": 1,
            "signup_open": False,
        }
        saved = await client.put("/api/admin/settings", json=body)
        assert saved.status_code == 200
        assert saved.json() == {
            "default_monthly_limit_usd": 5.0,
            "default_project_limit_usd": None,
            "default_run_limit_usd": 1.0,
            "signup_open": False,
        }
        config = await client.get("/api/auth/config")
        assert config.json()["signup_open"] is False
        invalid = await client.put(
            "/api/admin/settings", json={**body, "default_run_limit_usd": -1}
        )
        assert invalid.status_code == 422
        client.cookies.clear()
        closed = await client.post(
            "/api/auth/register",
            json={"email": "new@example.test", "name": "New", "password": PASSWORD},
        )
        assert closed.status_code == 403
        assert closed.json() == {"detail": "sign-up is closed; ask an admin for an account"}


@pytest.mark.asyncio
async def test_admin_users_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, _project):
        bob = await sign_in(client, "bob@example.test", "Bob")
        await sign_in(client)
        listed = await client.get("/api/admin/users")
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 2
        assert rows[0]["email"] == "owner@example.test"
        assert rows[0]["is_admin"] is True
        assert rows[0]["projects"] == 1
        bob_row = rows[1]
        assert bob_row["month"] == {"spent_usd": 0.0, "limit_usd": None}
        assert bob_row["projects"] == 0
        assert bob_row["runs"] == 0
        assert bob_row["total_spent_usd"] == 0.0
        bob_id = str(bob["id"])
        priced = await client.patch(f"/api/admin/users/{bob_id}", json={"monthly_limit_usd": 2.5})
        assert priced.status_code == 200
        assert priced.json()["monthly_limit_usd"] == 2.5
        assert priced.json()["month"]["limit_usd"] == 2.5
        assert priced.json()["is_admin"] is False
        promoted = await client.patch(f"/api/admin/users/{bob_id}", json={"is_admin": True})
        assert promoted.status_code == 200
        assert promoted.json()["is_admin"] is True
        assert promoted.json()["monthly_limit_usd"] == 2.5
        disabled = await client.patch(f"/api/admin/users/{bob_id}", json={"disabled": True})
        assert disabled.status_code == 200
        assert disabled.json()["disabled"] is True
        connection = sqlite3.connect(settings.db_path)
        try:
            sessions = connection.execute(
                "SELECT count(*) FROM auth_sessions WHERE user_id = ?", (bob_id,)
            ).fetchone()
        finally:
            connection.close()
        assert sessions[0] == 0
        own_admin = await client.patch(
            f"/api/admin/users/{rows[0]['id']}", json={"is_admin": False}
        )
        assert own_admin.status_code == 409
        assert own_admin.json() == {
            "detail": "you cannot remove your own admin rights or disable your own account"
        }
        own_disable = await client.patch(
            f"/api/admin/users/{rows[0]['id']}", json={"disabled": True}
        )
        assert own_disable.status_code == 409
        own_limit = await client.patch(
            f"/api/admin/users/{rows[0]['id']}", json={"monthly_limit_usd": 1}
        )
        assert own_limit.status_code == 200
        missing = await client.patch("/api/admin/users/u_missing", json={})
        assert missing.status_code == 404
        assert missing.json() == {"detail": "user not found"}
        budget = await client.get("/api/auth/budget")
        assert budget.json() == {"spent_usd": 0.0, "limit_usd": 1.0}


@pytest.mark.asyncio
async def test_admin_projects_and_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, _settings, _session, project):
        await sign_in(client, "bob@example.test", "Bob")
        await sign_in(client)
        demo = str(project["id"])
        listed = await client.get("/api/admin/projects")
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["owner_name"] == "Owner"
        assert rows[0]["owner_email"] == "owner@example.test"
        assert rows[0]["members"] == 1
        assert rows[0]["runs"] == 0
        assert rows[0]["budget"] == {"spent_usd": 0.0, "limit_usd": None}
        assert rows[0]["limit_usd"] is None
        assert rows[0]["run_limit_usd"] is None
        assert rows[0]["effective_run_limit_usd"] is None
        capped = await client.patch(f"/api/admin/projects/{demo}", json={"limit_usd": 3})
        assert capped.status_code == 200
        assert capped.json()["limit_usd"] == 3.0
        assert capped.json()["budget"]["limit_usd"] == 3.0
        run_capped = await client.patch(f"/api/admin/projects/{demo}", json={"run_limit_usd": 0.5})
        assert run_capped.json()["limit_usd"] == 3.0
        assert run_capped.json()["run_limit_usd"] == 0.5
        assert run_capped.json()["effective_run_limit_usd"] == 0.5
        cleared = await client.patch(f"/api/admin/projects/{demo}", json={"limit_usd": None})
        assert cleared.json()["limit_usd"] is None
        assert cleared.json()["run_limit_usd"] == 0.5
        moved = await client.put(
            f"/api/admin/projects/{demo}/owner", json={"email": " BOB@example.test "}
        )
        assert moved.status_code == 200
        assert moved.json()["owner_name"] == "Bob"
        assert moved.json()["members"] == 2
        members = await client.get(f"/api/projects/{demo}/members")
        assert {m["email"]: m["role"] for m in members.json()} == {
            "bob@example.test": "owner",
            "owner@example.test": "member",
        }
        unknown = await client.put(
            f"/api/admin/projects/{demo}/owner", json={"email": "nobody@example.test"}
        )
        assert unknown.status_code == 404
        assert unknown.json() == {"detail": "no account uses this email"}
        missing_patch = await client.patch("/api/admin/projects/p_missing", json={})
        assert missing_patch.status_code == 404
        assert missing_patch.json() == {"detail": "project not found"}
        missing_owner = await client.put(
            "/api/admin/projects/p_missing/owner", json={"email": "bob@example.test"}
        )
        assert missing_owner.status_code == 404
        assert missing_owner.json() == {"detail": "project not found"}


@pytest.mark.asyncio
async def test_admin_runs_overview_and_run_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _session, project):
        demo = str(project["id"])
        _seed_run(settings.db_path, demo, tmp_path, "completed")
        ts = datetime.now(UTC).isoformat()
        _write(
            settings.db_path,
            "INSERT INTO node_executions (id, run_id, node_name, seq, attempt, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ne_seed", "r_seed", "data_profiling", "1", "1", "completed", ts),
        )
        _write(
            settings.db_path,
            "INSERT INTO llm_calls (id, run_id, node_execution_id, signature, model, "
            "prompt_tokens, completion_tokens, cached_tokens, cost_usd, duration_ms, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("lc_seed", "r_seed", "ne_seed", "s", "m", "0", "0", "0", "0.75", "1", ts),
        )
        runs = await client.get("/api/admin/runs")
        assert runs.status_code == 200
        rows = runs.json()
        assert len(rows) == 1
        assert rows[0]["id"] == "r_seed"
        assert rows[0]["project_name"] == "Demo"
        assert rows[0]["status"] == "completed"
        assert rows[0]["limit_usd"] is None
        assert rows[0]["budget"]["run"] == {"spent_usd": 0.75, "limit_usd": None}
        assert rows[0]["budget"]["started_by"] is None
        assert rows[0]["budget"]["blocked"] is None
        capped = await client.patch("/api/admin/runs/r_seed", json={"limit_usd": 0.5})
        assert capped.status_code == 200
        assert capped.json()["limit_usd"] == 0.5
        blocked_text = "This run has used its budget ($0.75 of $0.50)."
        assert capped.json()["budget"]["blocked"] == blocked_text
        run_budget = await client.get("/api/runs/r_seed/budget")
        assert run_budget.json()["blocked"] == blocked_text
        missing_run = await client.patch("/api/admin/runs/r_missing", json={"limit_usd": 1})
        assert missing_run.status_code == 404
        assert missing_run.json() == {"detail": "run not found"}
        unknown_budget = await client.get("/api/runs/r_0123456789abcdef/budget")
        assert unknown_budget.status_code == 404
        bad_limit = await client.get("/api/admin/runs?limit=0")
        assert bad_limit.status_code == 422
        overview = await client.get("/api/admin/overview")
        assert overview.status_code == 200
        summary = overview.json()
        assert summary["users"] == 1
        assert summary["admins"] == 1
        assert summary["disabled_users"] == 0
        assert summary["projects"] == 1
        assert summary["runs"] == 1
        assert summary["open_runs"] == 0
        assert summary["blocked_runs"] == []
        assert summary["month_spent_usd"] == 0.75
        assert summary["total_spent_usd"] == 0.75
        assert summary["month"] == datetime.now(UTC).strftime("%Y-%m")
        assert summary["settings"] == {
            "default_monthly_limit_usd": None,
            "default_project_limit_usd": None,
            "default_run_limit_usd": None,
            "signup_open": True,
        }
        _write(
            settings.db_path,
            "UPDATE runs SET status = ? WHERE id = ?",
            ("paused", "r_seed"),
        )
        open_overview = await client.get("/api/admin/overview")
        assert open_overview.json()["open_runs"] == 1
        assert [r["id"] for r in open_overview.json()["blocked_runs"]] == ["r_seed"]


@pytest.mark.asyncio
async def test_budget_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _open_run_api(tmp_path, monkeypatch, records=FixtureRecords()) as api:
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        saved = await api.client.put(
            "/api/admin/settings",
            json={
                "default_monthly_limit_usd": None,
                "default_project_limit_usd": None,
                "default_run_limit_usd": 0,
                "signup_open": True,
            },
        )
        assert saved.status_code == 200
        guard = api.registry.bindings[run_id].loop.control.guard
        assert guard is not None
        assert guard() is True
        run_text = "This run has used its budget ($0.00 of $0.00)."
        refusals: list[tuple[str, str, dict[str, JsonValue]]] = [
            ("messages", f"/api/runs/{run_id}/messages", {"text": "hi"}),
            (
                "answer",
                f"/api/runs/{run_id}/gates/{gate.id}",
                {"action": "approve"},
            ),
            ("resume", f"/api/runs/{run_id}/control", {"action": "resume"}),
            (
                "rewind",
                f"/api/runs/{run_id}/control",
                {"action": "rewind", "target": "data_profiling", "request_id": "req-1"},
            ),
        ]
        for _name, path, body in refusals:
            response = await api.client.post(path, json=body)
            assert response.status_code == 402
            assert response.json() == {"detail": run_text}
        budget = await api.client.get(f"/api/runs/{run_id}/budget")
        assert budget.json()["blocked"] == run_text
        new_run = await api.client.post(
            f"/api/projects/{api.project_id}/runs",
            json={
                "mode": "guided",
                "dataset_ids": [api.dataset_id],
                "contract_id": api.contract_id,
            },
        )
        assert new_run.status_code == 402
        assert new_run.json() == {"detail": "New runs here have no budget ($0.00 of $0.00)."}
        person_text = "You have used this month's budget ($0.00 of $0.00)."
        monthly = await api.client.put(
            "/api/admin/settings",
            json={
                "default_monthly_limit_usd": 0,
                "default_project_limit_usd": None,
                "default_run_limit_usd": None,
                "signup_open": True,
            },
        )
        assert monthly.status_code == 200
        turn = await api.client.post(
            "/api/assistant/turn", json={"messages": [{"role": "user", "text": "hi"}]}
        )
        assert turn.status_code == 402
        assert turn.json() == {"detail": person_text}
        propose = await api.client.post(
            f"/api/projects/{api.project_id}/contracts/propose",
            json={"dataset_id": api.dataset_id, "contract": {"target": {"column": "b"}}},
        )
        assert propose.status_code == 402
        assert propose.json() == {"detail": person_text}
        new_run_person = await api.client.post(
            f"/api/projects/{api.project_id}/runs",
            json={
                "mode": "guided",
                "dataset_ids": [api.dataset_id],
                "contract_id": api.contract_id,
            },
        )
        assert new_run_person.status_code == 402
        assert new_run_person.json() == {"detail": person_text}
        starter_text = (
            "The person who started this run has used this month's budget ($0.00 of $0.00)."
        )
        messages = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": "hi"})
        assert messages.status_code == 402
        assert messages.json() == {"detail": starter_text}
