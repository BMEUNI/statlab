"""Accounts, sign-in sessions and project membership (chunk 71)."""

from __future__ import annotations

import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict

from statlab.persistence.db import transaction
from statlab.persistence.ids import new_id, utc_now
from statlab.persistence.models import ProjectRow

type MemberRole = Literal["owner", "member"]


class UserRow(BaseModel):
    """A `users` row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    email: str
    name: str
    password_hash: str
    is_admin: bool
    disabled: bool
    monthly_limit_usd: float | None
    created_at: str
    last_login_at: str | None


class MemberRow(BaseModel):
    """A project member: a `project_members` row joined to its user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str
    email: str
    name: str
    role: MemberRole
    added_at: str


_MEMBER_SELECT = (
    "SELECT pm.user_id, u.email, u.name, pm.role, pm.added_at FROM project_members pm "
    "JOIN users u ON u.id = pm.user_id"
)


def get_user(conn: sqlite3.Connection, user_id: str) -> UserRow | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return UserRow.model_validate(dict(row)) if row is not None else None


def get_user_by_email(conn: sqlite3.Connection, email: str) -> UserRow | None:
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return UserRow.model_validate(dict(row)) if row is not None else None


def count_users(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM users").fetchone()[0])


def create_user(
    conn: sqlite3.Connection, *, email: str, name: str, password_hash: str
) -> UserRow | None:
    """None when the email is taken. The first account is admin and takes over every existing
    project and run."""
    user_id = new_id("u")
    now = utc_now()
    with transaction(conn):
        if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone() is not None:
            return None
        first = conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0
        conn.execute(
            "INSERT INTO users (id, email, name, password_hash, is_admin, disabled, "
            "monthly_limit_usd, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?, ?, 0, NULL, ?, NULL)",
            (user_id, email, name, password_hash, 1 if first else 0, now),
        )
        if first:
            for (project_id,) in conn.execute("SELECT id FROM projects").fetchall():
                conn.execute(
                    "INSERT INTO project_members (id, project_id, user_id, role, added_at) "
                    "VALUES (?, ?, ?, 'owner', ?)",
                    (new_id("pm"), project_id, user_id, now),
                )
            conn.execute(
                "INSERT INTO run_budgets (id, started_by) SELECT id, ? FROM runs "
                "WHERE id NOT IN (SELECT id FROM run_budgets)",
                (user_id,),
            )
            conn.execute(
                "UPDATE run_budgets SET started_by = ? WHERE started_by IS NULL", (user_id,)
            )
    return get_user(conn, user_id)


def create_auth_session(
    conn: sqlite3.Connection, *, token_hash: str, user_id: str, expires_at: str
) -> None:
    now = utc_now()
    with transaction(conn):
        conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        conn.execute(
            "INSERT INTO auth_sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, now, expires_at),
        )
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))


def session_user(conn: sqlite3.Connection, token_hash: str) -> UserRow | None:
    row = conn.execute(
        "SELECT u.* FROM auth_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.id = ? AND s.expires_at > ? AND u.disabled = 0",
        (token_hash, utc_now()),
    ).fetchone()
    return UserRow.model_validate(dict(row)) if row is not None else None


def delete_auth_session(conn: sqlite3.Connection, token_hash: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM auth_sessions WHERE id = ?", (token_hash,))


def member_role(conn: sqlite3.Connection, project_id: str, user_id: str) -> MemberRole | None:
    row = conn.execute(
        "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return "owner" if row["role"] == "owner" else "member"


def list_members(conn: sqlite3.Connection, project_id: str) -> tuple[MemberRow, ...]:
    rows = conn.execute(
        _MEMBER_SELECT
        + " WHERE pm.project_id = ? ORDER BY pm.role = 'member', pm.added_at, u.email",
        (project_id,),
    ).fetchall()
    return tuple(MemberRow.model_validate(dict(row)) for row in rows)


def add_member(
    conn: sqlite3.Connection, *, project_id: str, user_id: str, role: MemberRole
) -> MemberRow | None:
    with transaction(conn):
        exists = conn.execute(
            "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        ).fetchone()
        if exists is not None:
            return None
        conn.execute(
            "INSERT INTO project_members (id, project_id, user_id, role, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_id("pm"), project_id, user_id, role, utc_now()),
        )
    row = conn.execute(
        _MEMBER_SELECT + " WHERE pm.project_id = ? AND pm.user_id = ?",
        (project_id, user_id),
    ).fetchone()
    return MemberRow.model_validate(dict(row))


def remove_member(conn: sqlite3.Connection, project_id: str, user_id: str) -> None:
    with transaction(conn):
        conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id),
        )


def list_member_projects(conn: sqlite3.Connection, user_id: str) -> tuple[ProjectRow, ...]:
    rows = conn.execute(
        "SELECT p.* FROM projects p JOIN project_members pm ON pm.project_id = p.id "
        "WHERE pm.user_id = ? ORDER BY p.created_at DESC",
        (user_id,),
    ).fetchall()
    return tuple(ProjectRow.model_validate(dict(row)) for row in rows)


def list_users(conn: sqlite3.Connection) -> tuple[UserRow, ...]:
    rows = conn.execute("SELECT * FROM users ORDER BY created_at, email").fetchall()
    return tuple(UserRow.model_validate(dict(row)) for row in rows)


def update_user(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    is_admin: bool,
    disabled: bool,
    monthly_limit_usd: float | None,
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE users SET is_admin = ?, disabled = ?, monthly_limit_usd = ? WHERE id = ?",
            (1 if is_admin else 0, 1 if disabled else 0, monthly_limit_usd, user_id),
        )
        if disabled:
            conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))


def set_owner(conn: sqlite3.Connection, project_id: str, user_id: str) -> None:
    """The previous owner stays as a member."""
    with transaction(conn):
        conn.execute(
            "UPDATE project_members SET role = 'member' WHERE project_id = ? AND role = 'owner'",
            (project_id,),
        )
        conn.execute(
            "INSERT INTO project_members (id, project_id, user_id, role, added_at) "
            "VALUES (?, ?, ?, 'owner', ?) ON CONFLICT(project_id, user_id) DO "
            "UPDATE SET role = 'owner'",
            (new_id("pm"), project_id, user_id, utc_now()),
        )
