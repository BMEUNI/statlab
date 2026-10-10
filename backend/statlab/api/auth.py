"""Sign-up, sign-in, sign-out and project members (chunk 71)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import cache

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from statlab.api.access import SESSION_COOKIE, CurrentUser, manages
from statlab.api.projects import APIResources, ProjectsService
from statlab.auth import (
    SESSION_SECONDS,
    LoginThrottle,
    hash_password,
    new_session_token,
    token_digest,
    verify_password,
)
from statlab.budgets import Meter, month_start, person_meter, read_settings
from statlab.persistence.accounts import (
    MemberRow,
    UserRow,
    add_member,
    count_users,
    create_auth_session,
    create_user,
    delete_auth_session,
    get_user_by_email,
    list_members,
    member_role,
    remove_member,
)

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RegisterRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(max_length=254)
    name: str = Field(max_length=100)
    password: str = Field(min_length=10, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip().lower()
        if _EMAIL.fullmatch(value) is None:
            raise ValueError("enter a valid email address")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(max_length=254)
    password: str = Field(max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return value.strip().lower()


class MemberAdd(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str = Field(max_length=254)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return value.strip().lower()


class AuthUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    email: str
    name: str
    is_admin: bool


class AuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signup_open: bool
    has_users: bool


def _auth_user(user: UserRow) -> AuthUser:
    return AuthUser(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin)


@cache
def _dummy_hash() -> str:
    # verified against for unknown emails so a miss costs as long as a hit
    return hash_password("statlab-unknown-account")


def _start_session(resource: APIResources, response: Response, user_id: str) -> None:
    token, digest = new_session_token()
    expires = datetime.now(UTC) + timedelta(seconds=SESSION_SECONDS)
    create_auth_session(
        resource.conn, token_hash=digest, user_id=user_id, expires_at=expires.isoformat()
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_SECONDS,
        path="/",
        secure=resource.settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def auth_router(resources: Callable[[], APIResources]) -> APIRouter:
    router = APIRouter()
    throttle = LoginThrottle()

    @router.post("/api/auth/register", status_code=201, response_model=AuthUser)
    async def register(body: RegisterRequest, response: Response) -> AuthUser:
        resource = resources()
        if not read_settings(resource.conn).signup_open and count_users(resource.conn) > 0:
            raise HTTPException(403, "sign-up is closed; ask an admin for an account")
        encoded = await asyncio.to_thread(hash_password, body.password)
        user = create_user(resource.conn, email=body.email, name=body.name, password_hash=encoded)
        if user is None:
            raise HTTPException(409, "an account with this email already exists")
        _start_session(resource, response, user.id)
        return _auth_user(user)

    @router.post("/api/auth/login", response_model=AuthUser)
    async def login(body: LoginRequest, response: Response) -> AuthUser:
        if throttle.blocked(body.email):
            raise HTTPException(429, "too many failed sign-ins; try again in 15 minutes")
        resource = resources()
        user = get_user_by_email(resource.conn, body.email)
        encoded = user.password_hash if user is not None else _dummy_hash()
        valid = await asyncio.to_thread(verify_password, body.password, encoded)
        if user is None or not valid:
            throttle.fail(body.email)
            raise HTTPException(401, "email or password is incorrect")
        if user.disabled:
            raise HTTPException(403, "this account is disabled")
        throttle.clear(body.email)
        _start_session(resource, response, user.id)
        return _auth_user(user)

    @router.post("/api/auth/logout", status_code=204)
    async def logout(request: Request) -> Response:
        resource = resources()
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            delete_auth_session(resource.conn, token_digest(token))
        response = Response(status_code=204)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=resource.settings.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @router.get("/api/auth/me", response_model=AuthUser)
    async def me(user: CurrentUser) -> AuthUser:
        return _auth_user(user)

    @router.get("/api/auth/config", response_model=AuthConfig)
    async def config() -> AuthConfig:
        conn = resources().conn
        return AuthConfig(
            signup_open=read_settings(conn).signup_open, has_users=count_users(conn) > 0
        )

    @router.get("/api/auth/budget", response_model=Meter)
    async def my_budget(user: CurrentUser) -> Meter:
        conn = resources().conn
        return person_meter(conn, user, read_settings(conn), month_start(datetime.now(UTC)))

    return router


def members_router(resources: Callable[[], APIResources]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/projects/{project_id}/members", response_model=tuple[MemberRow, ...])
    async def members(project_id: str) -> tuple[MemberRow, ...]:
        resource = resources()
        project = ProjectsService(resource).project(project_id)
        return list_members(resource.conn, project.id)

    @router.post("/api/projects/{project_id}/members", status_code=201, response_model=MemberRow)
    async def add(project_id: str, body: MemberAdd, user: CurrentUser) -> MemberRow:
        resource = resources()
        project = ProjectsService(resource).project(project_id)
        if not manages(resource.conn, user, project.id):
            raise HTTPException(403, "only the project owner can add members")
        target = get_user_by_email(resource.conn, body.email)
        if target is None:
            raise HTTPException(404, "no account uses this email")
        row = add_member(resource.conn, project_id=project.id, user_id=target.id, role="member")
        if row is None:
            raise HTTPException(409, "this person is already a member")
        return row

    @router.delete(
        "/api/projects/{project_id}/members/{user_id}",
        status_code=204,
    )
    async def remove(project_id: str, user_id: str, user: CurrentUser) -> Response:
        resource = resources()
        project = ProjectsService(resource).project(project_id)
        role = member_role(resource.conn, project.id, user_id)
        if role is None:
            raise HTTPException(404, "member not found")
        if role == "owner":
            raise HTTPException(409, "the project owner cannot be removed")
        if user_id != user.id and not manages(resource.conn, user, project.id):
            raise HTTPException(403, "only the project owner can remove members")
        remove_member(resource.conn, project.id, user_id)
        return Response(status_code=204)

    return router
