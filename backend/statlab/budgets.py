"""Spend and budget limits (chunk 71). Spend is llm_calls.cost_usd; a run counts against the
person who started it."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from statlab.persistence.accounts import UserRow, get_user
from statlab.persistence.db import transaction
from statlab.persistence.models import RunRow
from statlab.persistence.repository import require_run


class AdminSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    default_monthly_limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    default_project_limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    default_run_limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    signup_open: bool = True


class Meter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spent_usd: float
    limit_usd: float | None


class RunBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run: Meter
    project: Meter
    person: Meter
    started_by: str | None
    blocked: str | None


def read_settings(conn: sqlite3.Connection) -> AdminSettings:
    row = conn.execute("SELECT value_json FROM app_settings WHERE id = 'admin'").fetchone()
    if row is None:
        return AdminSettings()
    return AdminSettings.model_validate_json(row[0])


def write_settings(conn: sqlite3.Connection, settings: AdminSettings) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO app_settings (id, value_json) VALUES ('admin', ?) "
            "ON CONFLICT(id) DO UPDATE SET value_json = excluded.value_json",
            (settings.model_dump_json(),),
        )


def month_start(now: datetime) -> str:
    return f"{now:%Y-%m}-01"


def spend_since(conn: sqlite3.Connection, since: str) -> float:
    return float(
        conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE ts >= ?", (since,)
        ).fetchone()[0]
    )


def run_spend(conn: sqlite3.Connection, run_id: str) -> float:
    return float(
        conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    )


def project_spend(conn: sqlite3.Connection, project_id: str) -> float:
    return float(
        conn.execute(
            "SELECT COALESCE(SUM(l.cost_usd), 0.0) FROM llm_calls l "
            "JOIN runs r ON r.id = l.run_id WHERE r.project_id = ?",
            (project_id,),
        ).fetchone()[0]
    )


def person_spend(conn: sqlite3.Connection, user_id: str, since: str) -> float:
    return float(
        conn.execute(
            "SELECT COALESCE(SUM(l.cost_usd), 0.0) FROM llm_calls l "
            "JOIN run_budgets b ON b.id = l.run_id WHERE b.started_by = ? AND l.ts >= ?",
            (user_id, since),
        ).fetchone()[0]
    )


def project_limits(conn: sqlite3.Connection, project_id: str) -> tuple[float | None, float | None]:
    row = conn.execute(
        "SELECT limit_usd, run_limit_usd FROM project_budgets WHERE id = ?", (project_id,)
    ).fetchone()
    if row is None:
        return None, None
    return row[0], row[1]


def run_limit(conn: sqlite3.Connection, run_id: str) -> float | None:
    row = conn.execute("SELECT limit_usd FROM run_budgets WHERE id = ?", (run_id,)).fetchone()
    return row[0] if row is not None else None


def set_project_limits(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    limit_usd: float | None,
    run_limit_usd: float | None,
) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO project_budgets (id, limit_usd, run_limit_usd) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET limit_usd = excluded.limit_usd, "
            "run_limit_usd = excluded.run_limit_usd",
            (project_id, limit_usd, run_limit_usd),
        )


def set_run_limit(conn: sqlite3.Connection, run_id: str, limit_usd: float | None) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO run_budgets (id, started_by, limit_usd) VALUES (?, NULL, ?) "
            "ON CONFLICT(id) DO UPDATE SET limit_usd = excluded.limit_usd",
            (run_id, limit_usd),
        )


def recent_runs(conn: sqlite3.Connection, limit: int) -> tuple[RunRow, ...]:
    rows = conn.execute(
        "SELECT * FROM runs ORDER BY started_at DESC, id LIMIT ?", (limit,)
    ).fetchall()
    return tuple(RunRow.model_validate(dict(row)) for row in rows)


def _first(*values: float | None) -> float | None:
    return next((v for v in values if v is not None), None)


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _block(meter: Meter, subject: str) -> str | None:
    limit = meter.limit_usd
    if limit is None or meter.spent_usd < limit:
        return None
    return f"{subject} ({_usd(meter.spent_usd)} of {_usd(limit)})."


def person_meter(
    conn: sqlite3.Connection, user: UserRow, settings: AdminSettings, since: str
) -> Meter:
    return Meter(
        spent_usd=person_spend(conn, user.id, since),
        limit_usd=_first(user.monthly_limit_usd, settings.default_monthly_limit_usd),
    )


def project_meter(conn: sqlite3.Connection, project_id: str, settings: AdminSettings) -> Meter:
    limit, _ = project_limits(conn, project_id)
    return Meter(
        spent_usd=project_spend(conn, project_id),
        limit_usd=_first(limit, settings.default_project_limit_usd),
    )


def run_budget(conn: sqlite3.Connection, run_id: str, now: datetime) -> RunBudget:
    run = require_run(conn, run_id)
    settings = read_settings(conn)
    row = conn.execute("SELECT started_by FROM run_budgets WHERE id = ?", (run_id,)).fetchone()
    starter = (
        get_user(conn, row["started_by"])
        if row is not None and row["started_by"] is not None
        else None
    )
    _, project_run_limit = project_limits(conn, run.project_id)
    run_meter = Meter(
        spent_usd=run_spend(conn, run_id),
        limit_usd=_first(
            run_limit(conn, run_id), project_run_limit, settings.default_run_limit_usd
        ),
    )
    project = project_meter(conn, run.project_id, settings)
    person = (
        person_meter(conn, starter, settings, month_start(now))
        if starter is not None
        else Meter(spent_usd=0.0, limit_usd=None)
    )
    blocked = (
        _block(run_meter, "This run has used its budget")
        or _block(project, "This project has used its budget")
        or _block(
            person,
            "The person who started this run has used this month's budget",
        )
    )
    return RunBudget(
        run=run_meter,
        project=project,
        person=person,
        started_by=starter.name if starter is not None else None,
        blocked=blocked,
    )


def start_block(
    conn: sqlite3.Connection, project_id: str, user: UserRow, now: datetime
) -> str | None:
    settings = read_settings(conn)
    _, project_run_limit = project_limits(conn, project_id)
    return (
        _block(
            Meter(
                spent_usd=0.0,
                limit_usd=_first(project_run_limit, settings.default_run_limit_usd),
            ),
            "New runs here have no budget",
        )
        or _block(project_meter(conn, project_id, settings), "This project has used its budget")
        or person_block(conn, user, now)
    )


def person_block(conn: sqlite3.Connection, user: UserRow, now: datetime) -> str | None:
    return _block(
        person_meter(conn, user, read_settings(conn), month_start(now)),
        "You have used this month's budget",
    )
