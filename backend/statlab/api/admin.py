"""Admin panel API: people, projects, runs, budgets and defaults (chunk 71). The access guard
admits admins only."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from statlab.api.access import CurrentUser
from statlab.api.projects import APIResources, ProjectsService
from statlab.budgets import (
    AdminSettings,
    Meter,
    RunBudget,
    month_start,
    person_meter,
    person_spend,
    project_limits,
    project_meter,
    read_settings,
    recent_runs,
    run_budget,
    run_limit,
    set_project_limits,
    set_run_limit,
    spend_since,
    write_settings,
)
from statlab.persistence.accounts import (
    UserRow,
    get_user,
    get_user_by_email,
    list_users,
    set_owner,
    update_user,
)
from statlab.persistence.models import ProjectRow, RunRow, RunStatus
from statlab.persistence.repository import get_run, list_projects, require_project

_OPEN = ("pending", "running", "awaiting_user", "paused")


class AdminUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    email: str
    name: str
    is_admin: bool
    disabled: bool
    created_at: str
    last_login_at: str | None
    monthly_limit_usd: float | None
    month: Meter
    total_spent_usd: float
    projects: int
    runs: int


class AdminProject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    slug: str
    created_at: str
    owner_id: str | None
    owner_name: str | None
    owner_email: str | None
    members: int
    runs: int
    budget: Meter
    limit_usd: float | None
    run_limit_usd: float | None
    effective_run_limit_usd: float | None


class AdminRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    project_id: str
    project_name: str
    status: RunStatus
    started_at: str
    limit_usd: float | None
    budget: RunBudget


class AdminOverview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    month: str
    month_spent_usd: float
    total_spent_usd: float
    users: int
    admins: int
    disabled_users: int
    projects: int
    runs: int
    open_runs: int
    blocked_runs: tuple[AdminRun, ...]
    settings: AdminSettings


class UserPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    is_admin: bool = False
    disabled: bool = False
    monthly_limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)


class ProjectPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    run_limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)


class RunPatch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit_usd: float | None = Field(ge=0, le=1_000_000)


class OwnerChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return value.strip().lower()


def _count(conn: sqlite3.Connection, sql: str, params: tuple[str, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _admin_user(
    conn: sqlite3.Connection, user: UserRow, settings: AdminSettings, since: str
) -> AdminUser:
    return AdminUser(
        id=user.id,
        email=user.email,
        name=user.name,
        is_admin=user.is_admin,
        disabled=user.disabled,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        monthly_limit_usd=user.monthly_limit_usd,
        month=person_meter(conn, user, settings, since),
        total_spent_usd=person_spend(conn, user.id, ""),
        projects=_count(conn, "SELECT count(*) FROM project_members WHERE user_id = ?", (user.id,)),
        runs=_count(conn, "SELECT count(*) FROM run_budgets WHERE started_by = ?", (user.id,)),
    )


def _admin_project(
    conn: sqlite3.Connection, project: ProjectRow, settings: AdminSettings
) -> AdminProject:
    owner = conn.execute(
        "SELECT u.id, u.name, u.email FROM project_members pm JOIN users u ON u.id = pm.user_id "
        "WHERE pm.project_id = ? AND pm.role = 'owner'",
        (project.id,),
    ).fetchone()
    limit, run_limit_usd = project_limits(conn, project.id)
    return AdminProject(
        id=project.id,
        name=project.name,
        slug=project.slug,
        created_at=project.created_at,
        owner_id=owner["id"] if owner is not None else None,
        owner_name=owner["name"] if owner is not None else None,
        owner_email=owner["email"] if owner is not None else None,
        members=_count(
            conn, "SELECT count(*) FROM project_members WHERE project_id = ?", (project.id,)
        ),
        runs=_count(conn, "SELECT count(*) FROM runs WHERE project_id = ?", (project.id,)),
        budget=project_meter(conn, project.id, settings),
        limit_usd=limit,
        run_limit_usd=run_limit_usd,
        effective_run_limit_usd=(
            run_limit_usd if run_limit_usd is not None else settings.default_run_limit_usd
        ),
    )


def _admin_run(conn: sqlite3.Connection, run: RunRow, now: datetime) -> AdminRun:
    return AdminRun(
        id=run.id,
        project_id=run.project_id,
        project_name=require_project(conn, run.project_id).name,
        status=run.status,
        started_at=run.started_at,
        limit_usd=run_limit(conn, run.id),
        budget=run_budget(conn, run.id, now),
    )


def admin_router(resources: Callable[[], APIResources]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/overview", response_model=AdminOverview)
    async def overview() -> AdminOverview:
        conn = resources().conn
        now = datetime.now(UTC)
        open_runs = [run for run in recent_runs(conn, 1_000_000) if run.status in _OPEN]
        blocked_runs = tuple(
            admin_run
            for admin_run in (_admin_run(conn, run, now) for run in open_runs)
            if admin_run.budget.blocked is not None
        )
        return AdminOverview(
            month=f"{now:%Y-%m}",
            month_spent_usd=spend_since(conn, month_start(now)),
            total_spent_usd=spend_since(conn, ""),
            users=_count(conn, "SELECT count(*) FROM users"),
            admins=_count(conn, "SELECT count(*) FROM users WHERE is_admin = 1"),
            disabled_users=_count(conn, "SELECT count(*) FROM users WHERE disabled = 1"),
            projects=_count(conn, "SELECT count(*) FROM projects"),
            runs=_count(conn, "SELECT count(*) FROM runs"),
            open_runs=len(open_runs),
            blocked_runs=blocked_runs,
            settings=read_settings(conn),
        )

    @router.get("/api/admin/users", response_model=tuple[AdminUser, ...])
    async def users() -> tuple[AdminUser, ...]:
        conn = resources().conn
        settings = read_settings(conn)
        since = month_start(datetime.now(UTC))
        return tuple(_admin_user(conn, user, settings, since) for user in list_users(conn))

    @router.patch("/api/admin/users/{user_id}", response_model=AdminUser)
    async def patch_user(user_id: str, body: UserPatch, me: CurrentUser) -> AdminUser:
        conn = resources().conn
        target = get_user(conn, user_id)
        if target is None:
            raise HTTPException(404, "user not found")
        fields = body.model_fields_set
        if target.id == me.id and (
            ("is_admin" in fields and not body.is_admin) or ("disabled" in fields and body.disabled)
        ):
            raise HTTPException(
                409, "you cannot remove your own admin rights or disable your own account"
            )
        updated = target.model_copy(
            update={
                "is_admin": body.is_admin if "is_admin" in fields else target.is_admin,
                "disabled": body.disabled if "disabled" in fields else target.disabled,
                "monthly_limit_usd": (
                    body.monthly_limit_usd
                    if "monthly_limit_usd" in fields
                    else target.monthly_limit_usd
                ),
            }
        )
        update_user(
            conn,
            target.id,
            is_admin=updated.is_admin,
            disabled=updated.disabled,
            monthly_limit_usd=updated.monthly_limit_usd,
        )
        now = datetime.now(UTC)
        return _admin_user(conn, updated, read_settings(conn), month_start(now))

    @router.get("/api/admin/projects", response_model=tuple[AdminProject, ...])
    async def projects() -> tuple[AdminProject, ...]:
        conn = resources().conn
        settings = read_settings(conn)
        return tuple(_admin_project(conn, project, settings) for project in list_projects(conn))

    @router.patch("/api/admin/projects/{project_id}", response_model=AdminProject)
    async def patch_project(project_id: str, body: ProjectPatch) -> AdminProject:
        resource = resources()
        conn = resource.conn
        project = ProjectsService(resource).project(project_id)
        limit, run_limit_usd = project_limits(conn, project.id)
        fields = body.model_fields_set
        set_project_limits(
            conn,
            project.id,
            limit_usd=body.limit_usd if "limit_usd" in fields else limit,
            run_limit_usd=body.run_limit_usd if "run_limit_usd" in fields else run_limit_usd,
        )
        return _admin_project(conn, project, read_settings(conn))

    @router.put("/api/admin/projects/{project_id}/owner", response_model=AdminProject)
    async def change_owner(project_id: str, body: OwnerChange) -> AdminProject:
        resource = resources()
        conn = resource.conn
        project = ProjectsService(resource).project(project_id)
        target = get_user_by_email(conn, body.email)
        if target is None:
            raise HTTPException(404, "no account uses this email")
        set_owner(conn, project.id, target.id)
        return _admin_project(conn, project, read_settings(conn))

    @router.get("/api/admin/runs", response_model=tuple[AdminRun, ...])
    async def runs(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> tuple[AdminRun, ...]:
        conn = resources().conn
        now = datetime.now(UTC)
        return tuple(_admin_run(conn, run, now) for run in recent_runs(conn, limit))

    @router.patch("/api/admin/runs/{run_id}", response_model=AdminRun)
    async def patch_run(run_id: str, body: RunPatch) -> AdminRun:
        conn = resources().conn
        run = get_run(conn, run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        set_run_limit(conn, run.id, body.limit_usd)
        return _admin_run(conn, run, datetime.now(UTC))

    @router.get("/api/admin/settings", response_model=AdminSettings)
    async def settings() -> AdminSettings:
        return read_settings(resources().conn)

    @router.put("/api/admin/settings", response_model=AdminSettings)
    async def put_settings(body: AdminSettings) -> AdminSettings:
        conn = resources().conn
        write_settings(conn, body)
        return read_settings(conn)

    return router
