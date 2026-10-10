"""Accounts, sign-in sessions and project membership (chunk 71)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from statlab.persistence.accounts import (
    add_member,
    count_users,
    create_auth_session,
    create_user,
    delete_auth_session,
    get_user,
    get_user_by_email,
    list_member_projects,
    list_members,
    member_role,
    remove_member,
    session_user,
)
from statlab.persistence.repository import create_contract, create_project, create_run
from statlab_contracts.contract import DataContract, TargetSpec


def _contract(conn: sqlite3.Connection, project_id: str) -> str:
    contract = create_contract(
        conn,
        project_id=project_id,
        path=Path("/c.json"),
        contract=DataContract(target=TargetSpec(column="y")),
    )
    return contract.id


def test_first_user_takes_over_existing_projects_and_runs(migrated_db: sqlite3.Connection) -> None:
    project = create_project(migrated_db, slug="old", name="Old")
    contract_id = _contract(migrated_db, project.id)
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_id,
        mode="autonomous",
        config_json="{}",
    )
    migrated_db.execute(
        "INSERT INTO runs (id, project_id, contract_id, mode, status, started_at, config_json) "
        "VALUES ('r_raw', ?, ?, 'autonomous', 'completed', '2026-01-01T00:00:00+00:00', '{}')",
        (project.id, contract_id),
    )
    first = create_user(migrated_db, email="a@example.test", name="A", password_hash="h")  # noqa: S106 -- test value, not a real credential
    assert first is not None
    assert first.is_admin
    assert not first.disabled
    assert first.id.startswith("u_")
    assert first.monthly_limit_usd is None
    assert first.last_login_at is None
    assert [(m.user_id, m.role) for m in list_members(migrated_db, project.id)] == [
        (first.id, "owner")
    ]
    rows = migrated_db.execute("SELECT id, started_by FROM run_budgets").fetchall()
    assert {row["id"]: row["started_by"] for row in rows} == {
        run.id: first.id,
        "r_raw": first.id,
    }
    second = create_user(migrated_db, email="b@example.test", name="B", password_hash="h")  # noqa: S106 -- test value, not a real credential
    assert second is not None
    assert not second.is_admin
    assert [(m.user_id, m.role) for m in list_members(migrated_db, project.id)] == [
        (first.id, "owner")
    ]
    assert create_user(migrated_db, email="a@example.test", name="A", password_hash="h") is None  # noqa: S106 -- test value, not a real credential
    assert get_user(migrated_db, second.id) == second
    assert get_user(migrated_db, "u_missing") is None
    assert get_user_by_email(migrated_db, "b@example.test") == second
    assert get_user_by_email(migrated_db, "x@example.test") is None
    assert count_users(migrated_db) == 2


def test_project_owner_members_and_fork_starter(migrated_db: sqlite3.Connection) -> None:
    user = create_user(migrated_db, email="u@example.test", name="U", password_hash="h")  # noqa: S106 -- test value, not a real credential
    other = create_user(migrated_db, email="o@example.test", name="O", password_hash="h")  # noqa: S106 -- test value, not a real credential
    assert user is not None and other is not None
    project = create_project(migrated_db, slug="p", name="P", owner_id=user.id)
    assert member_role(migrated_db, project.id, user.id) == "owner"
    assert list_member_projects(migrated_db, user.id) == (project,)
    contract_id = _contract(migrated_db, project.id)
    parent = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_id,
        mode="autonomous",
        config_json="{}",
        started_by=user.id,
    )
    child = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=parent.id,
    )
    row = migrated_db.execute(
        "SELECT started_by FROM run_budgets WHERE id = ?", (child.id,)
    ).fetchone()
    assert row["started_by"] == user.id
    assert member_role(migrated_db, project.id, other.id) is None
    added = add_member(migrated_db, project_id=project.id, user_id=other.id, role="member")
    assert added is not None
    assert added.role == "member"
    assert added.email == other.email
    assert add_member(migrated_db, project_id=project.id, user_id=other.id, role="member") is None
    assert [m.role for m in list_members(migrated_db, project.id)] == ["owner", "member"]
    assert list_member_projects(migrated_db, other.id) == (project,)
    remove_member(migrated_db, project.id, other.id)
    assert member_role(migrated_db, project.id, other.id) is None
    assert list_member_projects(migrated_db, other.id) == ()


def test_auth_sessions_expire_and_respect_disabled(migrated_db: sqlite3.Connection) -> None:
    user = create_user(migrated_db, email="s@example.test", name="S", password_hash="h")  # noqa: S106 -- test value, not a real credential
    assert user is not None
    create_auth_session(
        migrated_db,
        token_hash="t1",  # noqa: S106 -- test value, not a real credential
        user_id=user.id,
        expires_at="2999-01-01T00:00:00+00:00",
    )
    found = session_user(migrated_db, "t1")
    assert found is not None
    assert found.id == user.id
    assert found.last_login_at is not None
    assert session_user(migrated_db, "t2") is None
    create_auth_session(
        migrated_db,
        token_hash="old",  # noqa: S106 -- test value, not a real credential
        user_id=user.id,
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert session_user(migrated_db, "old") is None
    create_auth_session(
        migrated_db,
        token_hash="t3",  # noqa: S106 -- test value, not a real credential
        user_id=user.id,
        expires_at="2999-01-01T00:00:00+00:00",
    )
    count = migrated_db.execute("SELECT count(*) FROM auth_sessions WHERE id = 'old'").fetchone()
    assert count[0] == 0
    delete_auth_session(migrated_db, "t3")
    count = migrated_db.execute("SELECT count(*) FROM auth_sessions WHERE id = 't3'").fetchone()
    assert count[0] == 0
    migrated_db.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user.id,))
    assert session_user(migrated_db, "t1") is None
