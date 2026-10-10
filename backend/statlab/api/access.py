"""Who is asking, and may they touch this project (chunk 71)."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from statlab.auth import token_digest
from statlab.persistence.accounts import UserRow, member_role, session_user
from statlab.persistence.repository import get_project, get_run, require_run
from statlab.storage import get_artifact

SESSION_COOKIE = "statlab_session"
CSRF_HEADER = "X-Statlab-Csrf"
PUBLIC_PATHS = frozenset(
    {"/api/auth/register", "/api/auth/login", "/api/auth/logout", "/api/auth/config"}
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def current_user(request: Request) -> UserRow:
    user: UserRow = request.state.user
    return user


CurrentUser = Annotated[UserRow, Depends(current_user)]


def has_access(conn: sqlite3.Connection, user: UserRow, project_id: str) -> bool:
    return user.is_admin or member_role(conn, project_id, user.id) is not None


def manages(conn: sqlite3.Connection, user: UserRow, project_id: str) -> bool:
    return user.is_admin or member_role(conn, project_id, user.id) == "owner"


def authorize(request: Request, conn: sqlite3.Connection) -> None:
    """The one access guard for every route (chunk 71)."""
    if request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, "request is missing the X-Statlab-Csrf header")
    path: str = request.scope["route"].path
    if path in PUBLIC_PATHS:
        return
    token = request.cookies.get(SESSION_COOKIE)
    user = session_user(conn, token_digest(token)) if token else None
    if user is None:
        raise HTTPException(401, "sign in first")
    request.state.user = user
    if user.is_admin:
        return
    params = request.path_params
    project_id: str | None = params.get("project_id")
    missing = "project not found"
    if "artifact_id" in params:
        artifact = get_artifact(conn, params["artifact_id"])
        if artifact is None:
            return
        project_id = require_run(conn, artifact.run_id).project_id
        missing = "artifact not found"
    elif "run_id" in params:
        run = get_run(conn, params["run_id"])
        if run is None:
            return
        project_id = run.project_id
        missing = "run not found"
    if project_id is None or get_project(conn, project_id) is None:
        return
    if member_role(conn, project_id, user.id) is None:
        raise HTTPException(404, missing)
