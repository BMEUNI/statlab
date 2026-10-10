---
status: completed
dependencies: [70]
completion_date: 2026-10-10
implementation_notes: "accounts with scrypt and session cookies, one access guard, members, enforced budgets per person/project/run, admin API and panel, change owner; make check 100%, npm 113/113, offline 19/19"
---

# Chunk 71 — accounts, project members and admin budgets

User request 2026-10-10: "authentication, users can register and login (make it secure), and
add each other to their projects, also make an admin panel where I can set budget per person
and also per run and per project ... make the admin panel detailed so I can set up stuff".

User decisions (2026-10-10, asked before planning):
1. The first account to register is admin; admins can promote others. Projects and runs that
   exist before the first account belong to that first admin.
2. Budgets are a hard stop: new runs are refused, and a running run pauses before its next LLM
   call or tool call once its run, project or person limit is reached. The admin raises the
   limit, then the user resumes.
3. The person budget is per calendar month (UTC); project and run budgets are lifetime totals.
4. Anyone can register; the admin can disable accounts (and close sign-up in the panel).

PLAN changes: D14 (single user, no login) and D16 (cost never enforced) are superseded by the
addenda D14-71 and D16-71 in [PLAN](./PLAN.md). The API stays bound to loopback by default.

## Design (all stages)

- Stdlib only, no new dependency. Passwords: `hashlib.scrypt` N=2^15, r=8, p=3 (an OWASP
  equivalent of N=2^17, p=1), 16-byte random salt, 32-byte key, constant-time compare, run in a
  worker thread. Sessions: 32-byte `secrets.token_urlsafe` token in an `HttpOnly`,
  `SameSite=Strict` cookie `statlab_session` (`Secure` when `STATLAB_COOKIE_SECURE=true`),
  14 days; the DB keeps only the token's SHA-256. CSRF: every non-GET/HEAD/OPTIONS request
  must carry the header `X-Statlab-Csrf: 1` (a cross-site form cannot set it; the app has no
  CORS, so cross-site scripts cannot either). Sign-in: generic "email or password is
  incorrect", a dummy hash for unknown emails, 5 failures per email in 15 minutes -> 429.
- One access guard (an app-level dependency) covers every route: public auth routes skip it;
  otherwise it loads the session user, and for a path with `project_id`, `run_id` or
  `artifact_id` it requires membership (admins pass). A non-member gets the same 404 text as a
  missing entity. Routes that carry a project id in the body (setup assistant) check it
  themselves.
- Tables (migration `002_accounts.sql`): `users`, `auth_sessions`, `project_members`,
  `project_budgets`, `run_budgets`, `app_settings`. Existing `projects`/`runs` rows are not
  altered (their row models use `SELECT *` with `extra="forbid"`).
- Admin "change owner" (user request 2026-10-10): the admin panel can make any account the
  owner of a project; the previous owner stays as a member; the new owner is added if not
  yet a member. Planned in 71b's admin API.
- Spend comes from `llm_calls.cost_usd` (setup-assistant calls are not recorded, as before;
  they are refused when the person's budget is spent). A run's spend counts against the person
  who started it; a fork inherits the starter of its parent.

## Stage 71a (detailed spec) — accounts, sessions, access guard, members

Owned files (new): `backend/statlab/persistence/migrations/002_accounts.sql`,
`backend/statlab/persistence/accounts.py`, `backend/statlab/auth.py`,
`backend/statlab/api/access.py`, `backend/statlab/api/auth.py`, `tests/test_auth.py`,
`tests/test_accounts.py`, `tests/test_api_auth.py`.
Owned files (edit): `backend/statlab/persistence/ids.py`,
`backend/statlab/persistence/repository.py`, `backend/statlab/config.py`,
`backend/statlab/main.py`, `backend/statlab/api/projects.py`,
`backend/statlab/api/assistant.py`, `backend/statlab/api/runs.py`, `tests/test_db_schema.py`,
`tests/test_db_migrations.py`, `tests/test_api_projects.py`, `tests/test_api_runs.py`,
`tests/test_api_assistant.py`. Not alone in the tree: preserve others' edits. No other file.

### 1. `002_accounts.sql`

Header comment (SQL `--` lines): "Chunk 71: accounts, sign-in sessions, project membership and
budgets. New tables only; projects and runs are not altered because their row models read
SELECT * with extra=forbid." Then, in this order:

- `users`: `id TEXT PRIMARY KEY NOT NULL`, `email TEXT NOT NULL UNIQUE`, `name TEXT NOT NULL`,
  `password_hash TEXT NOT NULL`, `is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,
  1))`, `disabled INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1))`, `monthly_limit_usd
  REAL CHECK (monthly_limit_usd IS NULL OR monthly_limit_usd >= 0)`, `created_at TEXT NOT
  NULL`, `last_login_at TEXT`.
- `auth_sessions`: `id TEXT PRIMARY KEY NOT NULL` (comment: SHA-256 hex of the cookie token),
  `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE`, `created_at TEXT NOT NULL`,
  `expires_at TEXT NOT NULL`; `CREATE INDEX idx_auth_sessions_user_id ON
  auth_sessions(user_id);`
- `project_members`: `id TEXT PRIMARY KEY NOT NULL`, `project_id TEXT NOT NULL REFERENCES
  projects(id) ON DELETE CASCADE`, `user_id TEXT NOT NULL REFERENCES users(id) ON DELETE
  CASCADE`, `role TEXT NOT NULL CHECK (role IN ('owner', 'member'))`, `added_at TEXT NOT
  NULL`, `UNIQUE (project_id, user_id)`; `CREATE INDEX idx_project_members_user_id ON
  project_members(user_id);`
- `project_budgets` (comment: id is the project's id): `id TEXT PRIMARY KEY NOT NULL
  REFERENCES projects(id) ON DELETE CASCADE`, `limit_usd REAL CHECK (limit_usd IS NULL OR
  limit_usd >= 0)`, `run_limit_usd REAL CHECK (run_limit_usd IS NULL OR run_limit_usd >= 0)`.
- `run_budgets` (comment: id is the run's id; started_by is the person the run's spend counts
  against): `id TEXT PRIMARY KEY NOT NULL REFERENCES runs(id) ON DELETE CASCADE`,
  `started_by TEXT REFERENCES users(id) ON DELETE SET NULL`, `limit_usd REAL CHECK (limit_usd
  IS NULL OR limit_usd >= 0)`; `CREATE INDEX idx_run_budgets_started_by ON
  run_budgets(started_by);`
- `app_settings` (comment: id is the setting key): `id TEXT PRIMARY KEY NOT NULL`,
  `value_json TEXT NOT NULL`.

### 2. `ids.py`

`PREFIXES`: add `"user": "u"` and `"project_member": "pm"` after `"memory_entry"`.

### 3. `persistence/accounts.py` (new)

Docstring: "Accounts, sign-in sessions and project membership (chunk 71)." Imports:
`sqlite3`, `Literal`, pydantic `BaseModel`, `ConfigDict`; `transaction` from
`statlab.persistence.db`; `new_id`, `utc_now` from `statlab.persistence.ids`; `ProjectRow`
from `statlab.persistence.models`.

- `type MemberRole = Literal["owner", "member"]`.
- `class UserRow(BaseModel)`: `model_config = ConfigDict(frozen=True, extra="forbid")`;
  fields `id: str`, `email: str`, `name: str`, `password_hash: str`, `is_admin: bool`,
  `disabled: bool`, `monthly_limit_usd: float | None`, `created_at: str`,
  `last_login_at: str | None`. (SQLite 0/1 validates to bool in lax mode.)
- `class MemberRow(BaseModel)`: same config; `user_id: str`, `email: str`, `name: str`,
  `role: MemberRole`, `added_at: str`.
- `_MEMBER_SELECT = "SELECT pm.user_id, u.email, u.name, pm.role, pm.added_at FROM
  project_members pm JOIN users u ON u.id = pm.user_id"` (module constant).
- `def get_user(conn, user_id: str) -> UserRow | None` — `SELECT * FROM users WHERE id = ?`.
- `def get_user_by_email(conn, email: str) -> UserRow | None` — `WHERE email = ?`.
- `def count_users(conn) -> int` — `SELECT count(*) FROM users`.
- `def create_user(conn, *, email: str, name: str, password_hash: str) -> UserRow | None` —
  docstring "None when the email is taken. The first account is admin and takes over every
  existing project and run." Body: `user_id = new_id("u")`, `now = utc_now()`; `with
  transaction(conn):` (a) if `SELECT 1 FROM users WHERE email = ?` finds a row: `return None`;
  (b) `first = conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0`; (c) `INSERT INTO
  users (id, email, name, password_hash, is_admin, disabled, monthly_limit_usd, created_at,
  last_login_at) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, NULL)` with `1 if first else 0`; (d) if
  `first`: for each `(project_id,)` of `SELECT id FROM projects`: `INSERT INTO project_members
  (id, project_id, user_id, role, added_at) VALUES (?, ?, ?, 'owner', ?)` with `new_id("pm")`;
  then `INSERT INTO run_budgets (id, started_by) SELECT id, ? FROM runs WHERE id NOT IN (SELECT
  id FROM run_budgets)`; then `UPDATE run_budgets SET started_by = ? WHERE started_by IS NULL`.
  After the `with`: return `get_user(conn, user_id)`.
- `def create_auth_session(conn, *, token_hash: str, user_id: str, expires_at: str) -> None`
  — `now = utc_now()`; one transaction: `DELETE FROM auth_sessions WHERE expires_at <= ?`
  (now); `INSERT INTO auth_sessions (id, user_id, created_at, expires_at) VALUES (?, ?, ?,
  ?)`; `UPDATE users SET last_login_at = ? WHERE id = ?` (now).
- `def session_user(conn, token_hash: str) -> UserRow | None` — `SELECT u.* FROM
  auth_sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ? AND s.expires_at > ? AND
  u.disabled = 0` with `utc_now()`.
- `def delete_auth_session(conn, token_hash: str) -> None` — one transaction, `DELETE FROM
  auth_sessions WHERE id = ?`.
- `def member_role(conn, project_id: str, user_id: str) -> MemberRole | None` — `SELECT role
  FROM project_members WHERE project_id = ? AND user_id = ?`; no row -> None; else `return
  "owner" if row["role"] == "owner" else "member"`.
- `def list_members(conn, project_id: str) -> tuple[MemberRow, ...]` — `_MEMBER_SELECT + "
  WHERE pm.project_id = ? ORDER BY pm.role = 'member', pm.added_at, u.email"`.
- `def add_member(conn, *, project_id: str, user_id: str, role: MemberRole) -> MemberRow |
  None` — one transaction: if `SELECT 1 FROM project_members WHERE project_id = ? AND user_id =
  ?` finds a row: return None; insert with `new_id("pm")` and `utc_now()`. After it: return
  the row from `_MEMBER_SELECT + " WHERE pm.project_id = ? AND pm.user_id = ?"` as
  `MemberRow.model_validate(dict(row))`.
- `def remove_member(conn, project_id: str, user_id: str) -> None` — one transaction, `DELETE
  FROM project_members WHERE project_id = ? AND user_id = ?`.
- `def list_member_projects(conn, user_id: str) -> tuple[ProjectRow, ...]` — `SELECT p.* FROM
  projects p JOIN project_members pm ON pm.project_id = p.id WHERE pm.user_id = ? ORDER BY
  p.created_at DESC`.

Rows convert with `UserRow.model_validate(dict(row))` / `ProjectRow.model_validate(dict(row))`.

### 4. `repository.py`

- `create_project(conn, *, slug, name, owner_id: str | None = None)`: inside the existing
  transaction, after the projects INSERT and before the SELECT: `if owner_id is not None:`
  `INSERT INTO project_members (id, project_id, user_id, role, added_at) VALUES (?, ?, ?,
  'owner', ?)` with `new_id("pm")`, `project_id`, `owner_id`, `created_at`.
- `create_run(..., started_by: str | None = None)` (new last keyword): inside the existing
  transaction, after the runs INSERT: `INSERT INTO run_budgets (id, started_by) VALUES (?,
  COALESCE(?, (SELECT started_by FROM run_budgets WHERE id = ?)))` with `(run_id, started_by,
  forked_from_run_id)`. Docstring line: "A fork inherits the parent's starter (chunk 71)."

### 5. `auth.py` (new, `backend/statlab/auth.py`)

Docstring: "Password hashing, session tokens and sign-in throttling (chunk 71). Stdlib only."
Imports `base64`, `binascii`, `hashlib`, `hmac`, `secrets`, `time`, `Callable`.

Constants: `SCRYPT_N = 2**15`, `SCRYPT_R = 8`, `SCRYPT_P = 3`, `_MAXMEM = 64 * 1024 * 1024`,
`_KEY_BYTES = 32`, `SESSION_SECONDS = 14 * 24 * 3600`, `MAX_FAILURES = 5`,
`FAILURE_WINDOW_SECONDS = 900.0`.

- `def hash_password(password: str) -> str` — `salt = secrets.token_bytes(16)`; `key =
  hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
  maxmem=_MAXMEM, dklen=_KEY_BYTES)`; return `"$".join(("scrypt", str(SCRYPT_N),
  str(SCRYPT_R), str(SCRYPT_P), base64.b64encode(salt).decode(),
  base64.b64encode(key).decode()))`.
- `def verify_password(password: str, encoded: str) -> bool` — `parts = encoded.split("$")`;
  if `len(parts) != 6 or parts[0] != "scrypt"`: return False. `try:` `n, r, p = int(parts[1]),
  int(parts[2]), int(parts[3])`; `salt = base64.b64decode(parts[4], validate=True)`;
  `expected = base64.b64decode(parts[5], validate=True)`; `key = hashlib.scrypt(...,
  n=n, r=r, p=p, maxmem=_MAXMEM, dklen=len(expected))` `except (ValueError, binascii.Error):`
  return False. Return `hmac.compare_digest(key, expected)`.
- `def token_digest(token: str) -> str` — `hashlib.sha256(token.encode()).hexdigest()`.
- `def new_session_token() -> tuple[str, str]` — `token = secrets.token_urlsafe(32)`; return
  `(token, token_digest(token))`.
- `class LoginThrottle` — docstring "Failed sign-ins per email, in memory." `__init__(self,
  clock: Callable[[], float] = time.monotonic)`: `self._clock = clock`, `self._failures:
  dict[str, list[float]] = {}`. Comment above the class: `# ponytail: per-process memory;
  emails never retried keep their entries until restart. Move to SQL if this runs multi-
  process.`
  - `blocked(self, key: str) -> bool`: `now = self._clock()`; `recent = [t for t in
    self._failures.get(key, []) if now - t < FAILURE_WINDOW_SECONDS]`; if `recent`:
    `self._failures[key] = recent` else `self._failures.pop(key, None)`; return `len(recent)
    >= MAX_FAILURES`.
  - `fail(self, key: str) -> None`: `self._failures.setdefault(key, []).append(self._clock())`.
  - `clear(self, key: str) -> None`: `self._failures.pop(key, None)`.

### 6. `config.py`

- Field after `allow_nonlocal_bind`: `cookie_secure: bool = Field(default=False,
  alias="STATLAB_COOKIE_SECURE")`.
- `_guard_bind_address` message becomes: f"api_host {self.api_host!r} is not loopback. Without
  HTTPS, passwords and session cookies cross the network in clear text. Put the API behind an
  HTTPS proxy, set STATLAB_COOKIE_SECURE=true, and set STATLAB_ALLOW_NONLOCAL_BIND=true only if
  you understand that." Logic unchanged.

### 7. `api/access.py` (new)

Docstring: "Who is asking, and may they touch this project (chunk 71)." Imports `sqlite3`,
`Annotated`, fastapi `Depends`, `HTTPException`, `Request`; `session_user`, `member_role`,
`UserRow` from accounts; `token_digest` from `statlab.auth`; `get_project`, `get_run`,
`require_run` from repository; `get_artifact` from `statlab.storage`.

Constants: `SESSION_COOKIE = "statlab_session"`, `CSRF_HEADER = "X-Statlab-Csrf"`,
`PUBLIC_PATHS = frozenset({"/api/auth/register", "/api/auth/login", "/api/auth/logout",
"/api/auth/config"})`, `_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})`.

- `async def current_user(request: Request) -> UserRow` — `user: UserRow =
  request.state.user`; return it. `CurrentUser = Annotated[UserRow, Depends(current_user)]`.
- `def has_access(conn, user: UserRow, project_id: str) -> bool` — `user.is_admin or
  member_role(conn, project_id, user.id) is not None`.
- `def manages(conn, user: UserRow, project_id: str) -> bool` — `user.is_admin or
  member_role(conn, project_id, user.id) == "owner"`.
- `def authorize(request: Request, conn: sqlite3.Connection) -> None` — docstring "The one
  access guard for every route (chunk 71)." Steps:
  1. If `request.method not in _SAFE_METHODS and request.headers.get(CSRF_HEADER) != "1"`:
     raise `HTTPException(403, "request is missing the X-Statlab-Csrf header")`.
  2. `path: str = request.scope["route"].path`; if `path in PUBLIC_PATHS`: return.
  3. `token = request.cookies.get(SESSION_COOKIE)`; `user = session_user(conn,
     token_digest(token)) if token else None`; if `user is None`: raise `HTTPException(401,
     "sign in first")`.
  4. `request.state.user = user`; if `user.is_admin`: return.
  5. `params = request.path_params`; `project_id = params.get("project_id")`; `missing =
     "project not found"`.
     - If `"artifact_id" in params`: `artifact = get_artifact(conn, params["artifact_id"])`; if
       None: return; `project_id = require_run(conn, artifact.run_id).project_id`; `missing =
       "artifact not found"`.
     - elif `"run_id" in params`: `run = get_run(conn, params["run_id"])`; if None: return;
       `project_id = run.project_id`; `missing = "run not found"`.
  6. If `project_id is None or get_project(conn, project_id) is None`: return (the route
     itself answers a missing entity).
  7. If `member_role(conn, project_id, user.id) is None`: raise `HTTPException(404, missing)`.

### 8. `api/auth.py` (new)

Docstring: "Sign-up, sign-in, sign-out and project members (chunk 71)." Imports `asyncio`,
`re`, `Callable`, `functools.cache`, fastapi `APIRouter`, `HTTPException`, `Request`,
`Response`, pydantic `BaseModel`, `ConfigDict`, `Field`, `field_validator`; accounts
(`MemberRow`, `UserRow`, `add_member`, `count_users`, `create_auth_session`, `create_user`,
`delete_auth_session`, `get_user_by_email`, `list_members`, `member_role`, `remove_member`);
`statlab.auth` (`SESSION_SECONDS`, `LoginThrottle`, `hash_password`, `new_session_token`,
`token_digest`, `verify_password`); access (`SESSION_COOKIE`, `CurrentUser`, `manages`);
`APIResources`, `ProjectsService` from `statlab.api.projects`; `datetime`, `UTC`, `timedelta`.

`_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")`.

Models (all `ConfigDict(frozen=True, extra="forbid")`):
- `RegisterRequest`: `email: str = Field(max_length=254)`, `name: str = Field(max_length=100)`,
  `password: str = Field(min_length=10, max_length=128)`. `@field_validator("email")`
  `_email`: `value = value.strip().lower()`; if `_EMAIL.fullmatch(value) is None`: raise
  `ValueError("enter a valid email address")`; return value. `@field_validator("name")`
  `_name`: `value = value.strip()`; if not value: raise `ValueError("name must not be
  blank")`; return value.
- `LoginRequest`: `email: str = Field(max_length=254)`, `password: str =
  Field(max_length=128)`; validator `_email` returns `value.strip().lower()`.
- `MemberAdd`: `email: str = Field(max_length=254)`; validator returns `value.strip().lower()`.
- `AuthUser`: `id: str`, `email: str`, `name: str`, `is_admin: bool`.
- `AuthConfig`: `signup_open: bool`, `has_users: bool`.

Helpers:
- `def _auth_user(user: UserRow) -> AuthUser` — copy the four fields.
- `@cache def _dummy_hash() -> str` — `hash_password("statlab-unknown-account")`; comment:
  "verified against for unknown emails so a miss costs as long as a hit".
- `def _start_session(resource: APIResources, response: Response, user_id: str) -> None` —
  `token, digest = new_session_token()`; `expires = datetime.now(UTC) +
  timedelta(seconds=SESSION_SECONDS)`; `create_auth_session(resource.conn, token_hash=digest,
  user_id=user_id, expires_at=expires.isoformat())`; `response.set_cookie(SESSION_COOKIE,
  token, max_age=SESSION_SECONDS, path="/", secure=resource.settings.cookie_secure,
  httponly=True, samesite="strict")`.

`def auth_router(resources: Callable[[], APIResources]) -> APIRouter` — `router =
APIRouter()`, `throttle = LoginThrottle()`:
- `@router.post("/api/auth/register", status_code=201, response_model=AuthUser)` `async def
  register(body: RegisterRequest, response: Response) -> AuthUser`: `resource =
  resources()`; `encoded = await asyncio.to_thread(hash_password, body.password)`; `user =
  create_user(resource.conn, email=body.email, name=body.name, password_hash=encoded)`; if
  None: raise `HTTPException(409, "an account with this email already exists")`;
  `_start_session(resource, response, user.id)`; return `_auth_user(user)`.
- `@router.post("/api/auth/login", response_model=AuthUser)` `async def login(body:
  LoginRequest, response: Response) -> AuthUser`: if `throttle.blocked(body.email)`: raise
  `HTTPException(429, "too many failed sign-ins; try again in 15 minutes")`; `resource =
  resources()`; `user = get_user_by_email(resource.conn, body.email)`; `encoded =
  user.password_hash if user is not None else _dummy_hash()`; `valid = await
  asyncio.to_thread(verify_password, body.password, encoded)`; if `user is None or not
  valid`: `throttle.fail(body.email)`; raise `HTTPException(401, "email or password is
  incorrect")`. If `user.disabled`: raise `HTTPException(403, "this account is disabled")`.
  `throttle.clear(body.email)`; `_start_session(...)`; return `_auth_user(user)`.
- `@router.post("/api/auth/logout", status_code=204)` `async def logout(request: Request) ->
  Response`: `resource = resources()`; `token = request.cookies.get(SESSION_COOKIE)`; if
  `token`: `delete_auth_session(resource.conn, token_digest(token))`; `response =
  Response(status_code=204)`; `response.delete_cookie(SESSION_COOKIE, path="/",
  secure=resource.settings.cookie_secure, httponly=True, samesite="strict")`; return it.
- `@router.get("/api/auth/me", response_model=AuthUser)` `async def me(user: CurrentUser) ->
  AuthUser`: return `_auth_user(user)`.
- `@router.get("/api/auth/config", response_model=AuthConfig)` `async def config() ->
  AuthConfig`: return `AuthConfig(signup_open=True, has_users=count_users(resources().conn) >
  0)`.

`def members_router(resources: Callable[[], APIResources]) -> APIRouter`:
- `GET /api/projects/{project_id}/members`, `response_model=tuple[MemberRow, ...]`:
  `resource = resources()`; `project = ProjectsService(resource).project(project_id)`; return
  `list_members(resource.conn, project.id)`.
- `POST /api/projects/{project_id}/members`, `status_code=201`, `response_model=MemberRow`,
  args `(project_id: str, body: MemberAdd, user: CurrentUser)`: project as above; if not
  `manages(conn, user, project.id)`: 403 "only the project owner can add members"; `target =
  get_user_by_email(conn, body.email)`; None -> 404 "no account uses this email"; `row =
  add_member(conn, project_id=project.id, user_id=target.id, role="member")`; None -> 409
  "this person is already a member"; return row.
- `DELETE /api/projects/{project_id}/members/{user_id}`, `status_code=204`, args
  `(project_id: str, user_id: str, user: CurrentUser) -> Response`: project as above; `role =
  member_role(conn, project.id, user_id)`; None -> 404 "member not found"; `"owner"` -> 409
  "the project owner cannot be removed"; if `user_id != user.id and not manages(conn, user,
  project.id)`: 403 "only the project owner can remove members"; `remove_member(conn,
  project.id, user_id)`; return `Response(status_code=204)`.

### 9. `main.py`

- Imports: `from statlab.api.access import authorize`; `from statlab.api.auth import
  auth_router, members_router`.
- Inside `create_app`, after `admitted_request`: `async def authenticated(request: Request) ->
  None: authorize(request, resources().conn)` (async on purpose: a sync dependency runs in a
  thread and the SQLite connection is bound to the event-loop thread; say so in a comment).
- `FastAPI(... dependencies=[Depends(admitted_request, scope="request"),
  Depends(authenticated)])`.
- `app.include_router(auth_router(resources))` and `app.include_router(members_router(
  resources))` after the projects router.

### 10. `api/projects.py`

- Import `CurrentUser`, `manages` from `statlab.api.access` and `list_member_projects` from
  `statlab.persistence.accounts`.
- `create_project_record(resources, slug, name, *, owner_id: str)` passes `owner_id=owner_id`
  to `create_project`.
- `create(body: ProjectCreate, user: CurrentUser)` -> `create_project_record(resources(),
  body.slug, body.name, owner_id=user.id)`.
- `list_all(user: CurrentUser)` -> `list_member_projects(resources().conn, user.id)`. Drop the
  `list_projects` import if it becomes unused (it still is used? check with Ruff).
- `delete(project_id: str, user: CurrentUser)`: right after `project =
  service.project(project_id)`: if not `manages(resource.conn, user, project.id)`: raise
  `HTTPException(403, "only the project owner can delete this project")`. Rest unchanged.

### 11. `api/assistant.py`

- `SetupAssistant.__init__(self, resources: APIResources, user: UserRow)` stores `self.user`.
- In `turn`, directly after `self.projects.project(state.project_id)`: if not
  `has_access(conn, self.user, state.project_id)`: raise `HTTPException(404, "project not
  found")`.
- `mine = list_member_projects(conn, self.user.id)` computed once before `project_facts`;
  `project_facts` iterates `mine`; `project_ids = [p.id for p in mine]`; `current_name =
  next((p.name for p in mine if p.id == state.project_id), None)`. Remove the `list_projects`
  import.
- `create_project_record(self.resources, unique_slug(...), name, owner_id=self.user.id)`.
- Route: `async def turn(body: AssistantRequest, user: CurrentUser)` -> `SetupAssistant(
  resources(), user).turn(body)`.

### 12. `api/runs.py`

- `create_run_endpoint(project_id: str, body: RunCreate, user: CurrentUser)` ->
  `registry().create(project_id, body, started_by=user.id)`.
- `RunRegistry.create(self, project_id: str, body: RunCreate, *, started_by: str)`; pass
  `started_by=started_by` to `create_run`.

### 13. Test harness updates (existing files)

Add to `tests/test_api_auth.py` (new, section 14) the shared helper:
`PASSWORD = "correct horse battery"`;
`async def sign_in(client: httpx.AsyncClient, email: str = "owner@example.test", name: str =
"Owner") -> dict[str, JsonValue]` — sets `client.headers["X-Statlab-Csrf"] = "1"`; posts
`/api/auth/register` with `{"email": email, "name": name, "password": PASSWORD}`; if the
status is 409, posts `/api/auth/login` with `{"email": email, "password": PASSWORD}` and
asserts 200; else asserts 201; returns the JSON body (cast).

- `tests/test_api_projects.py`: import `sign_in` from `tests.test_api_auth`; in
  `_open_project_api` and in the direct app at ~line 299 (the test that creates "demo" and
  checks `project.json`), call `await sign_in(client)` immediately before the first
  `client.post("/api/projects", ...)`.
- `tests/test_api_runs.py` `_open_run_api`: `await sign_in(client)` as the first statement
  inside the `async with` (before `if setup:`), so restarts with `setup=False` log in.
- `tests/test_api_assistant.py` `_open_assistant_api`: `await sign_in(client)` before the
  project POST.
- `tests/test_db_schema.py`: add to `_EXPECTED_COLUMNS` (after `memory`): `users` (id, email,
  name, password_hash, is_admin, disabled, monthly_limit_usd, created_at, last_login_at),
  `auth_sessions` (id, user_id, created_at, expires_at), `project_members` (id, project_id,
  user_id, role, added_at), `project_budgets` (id, limit_usd, run_limit_usd), `run_budgets`
  (id, started_by, limit_usd), `app_settings` (id, value_json). Rename
  `test_expected_columns_covers_all_fifteen_tables` to
  `test_expected_columns_covers_all_twenty_one_tables` and assert `== 21`.
- `tests/test_db_migrations.py`: `test_m1...`: `== (1, 2)`; `test_m2...`: build `declared`
  from both `001_initial.sql` and `002_accounts.sql` (same extraction, union) and assert
  `len(declared) == 21`; `test_m3...`: `len(rows) == 2`, rows ordered by version (`SELECT *
  FROM schema_migrations ORDER BY version`), `rows[0]` version 1 name `initial`, `rows[1]`
  version 2 name `accounts`, both `applied_at` parse; `test_m12...`: `== (1, 2)`. Rename
  nothing else. If any other existing test fails, stop and report it.

### 14. New tests

`tests/test_auth.py` (stdlib unit tests):
1. `test_hash_and_verify_round_trip`: `encoded = hash_password(PASSWORD_TEXT)` with
   `PASSWORD_TEXT = "correct horse battery"`; `parts = encoded.split("$")`; `len(parts) == 6`,
   `parts[:4] == ["scrypt", "32768", "8", "3"]`; `verify_password(PASSWORD_TEXT, encoded)`;
   not `verify_password("wrong horse battery", encoded)`; `hash_password(PASSWORD_TEXT) !=
   encoded`.
2. `test_verify_rejects_malformed_hashes`: parametrize `encoded` over `"plain"`,
   `"bcrypt$1$2$3$4$5"`, `"scrypt$x$8$1$AAAA$AAAA"`, `"scrypt$1$8$1$AAAA$AAAA"` (n=1 is not a
   valid scrypt cost), `"scrypt$16$8$1$!!$AAAA"`; each `verify_password("x", encoded) is
   False`.
3. `test_session_token_digest`: `token, digest = new_session_token()`; `digest ==
   token_digest(token)`, `len(digest) == 64`, `token != digest`, `new_session_token()[0] !=
   token`.
4. `test_login_throttle_window`: `now = [0.0]`; `throttle = LoginThrottle(clock=lambda:
   now[0])`; four `fail("a")` then `blocked("a") is False`; one more `fail("a")` ->
   `blocked("a") is True`; `blocked("b") is False`; `now[0] = 901.0` -> `blocked("a") is
   False`; `fail("a")` -> `blocked("a") is False`; `clear("a")`; `clear("missing")` (no error);
   `blocked("a") is False`.

`tests/test_accounts.py` (fixture `migrated_db` from conftest; contract via
`create_contract(migrated_db, project_id=..., path=Path("/c.json"),
contract=DataContract(target=TargetSpec(column="y")))`):
1. `test_first_user_takes_over_existing_projects_and_runs`: `project =
   create_project(migrated_db, slug="old", name="Old")`; contract; `run =
   create_run(migrated_db, project_id=project.id, contract_id=contract.id,
   mode="autonomous", config_json="{}")`; raw insert of a second run `r_raw` (`INSERT INTO runs
   (id, project_id, contract_id, mode, status, started_at, config_json) VALUES ('r_raw', ?, ?,
   'autonomous', 'completed', '2026-01-01T00:00:00+00:00', '{}')`). `first =
   create_user(migrated_db, email="a@example.test", name="A", password_hash="h")`: not None,
   `is_admin`, not `disabled`, `id.startswith("u_")`, `monthly_limit_usd is None`,
   `last_login_at is None`. `[(m.user_id, m.role) for m in list_members(migrated_db,
   project.id)] == [(first.id, "owner")]`. `SELECT id, started_by FROM run_budgets ORDER BY
   id` equals `[(run.id, first.id), ("r_raw", first.id)]` sorted by id (compare as a dict
   `{run.id: first.id, "r_raw": first.id}`). `second = create_user(..., email="b@example.test",
   name="B", ...)`: not `is_admin`; members unchanged. `create_user(..., email="a@example.test",
   ...) is None`. `get_user(migrated_db, second.id) == second`; `get_user(migrated_db,
   "u_missing") is None`; `get_user_by_email(migrated_db, "b@example.test") == second`;
   `get_user_by_email(migrated_db, "x@example.test") is None`; `count_users(migrated_db) == 2`.
2. `test_project_owner_members_and_fork_starter`: `user` and `other` via `create_user`;
   `project = create_project(migrated_db, slug="p", name="P", owner_id=user.id)`;
   `member_role(..., project.id, user.id) == "owner"`; `list_member_projects(migrated_db,
   user.id) == (project,)`; contract; `parent = create_run(..., started_by=user.id)`; `child =
   create_run(..., forked_from_run_id=parent.id)`; `SELECT started_by FROM run_budgets WHERE id
   = ?` for child equals `user.id`. `member_role(..., other.id) is None`; `added =
   add_member(migrated_db, project_id=project.id, user_id=other.id, role="member")`: `added is
   not None and added.role == "member" and added.email == other.email`; second `add_member`
   with the same pair `is None`; `[m.role for m in list_members(...)] == ["owner", "member"]`;
   `list_member_projects(migrated_db, other.id) == (project,)`; `remove_member(migrated_db,
   project.id, other.id)`; `member_role(...) is None`; `list_member_projects(..., other.id) ==
   ()`.
3. `test_auth_sessions_expire_and_respect_disabled`: `user`; `create_auth_session(migrated_db,
   token_hash="t1", user_id=user.id, expires_at="2999-01-01T00:00:00+00:00")`; `found =
   session_user(migrated_db, "t1")`: `found is not None`, `found.id == user.id`,
   `found.last_login_at is not None`; `session_user(migrated_db, "t2") is None`;
   `create_auth_session(..., token_hash="old", ..., expires_at="2000-01-01T00:00:00+00:00")`;
   `session_user(..., "old") is None`; `create_auth_session(..., token_hash="t3", ...,
   expires_at="2999-01-01T00:00:00+00:00")`; `SELECT count(*) FROM auth_sessions WHERE id =
   'old'` is 0; `delete_auth_session(migrated_db, "t3")` -> count of `t3` is 0; `UPDATE users
   SET disabled = 1 WHERE id = ?` -> `session_user(..., "t1") is None`.

`tests/test_api_auth.py` (async; mark `pytest.mark.asyncio` like the other API tests). Harness
`_open_auth_api(tmp_path, monkeypatch)` (asynccontextmanager): monkeypatch
`statlab.main.check_provider_environment` to a no-op; `Settings(mcp_token=SecretStr(
"test-token"), data_dir=tmp_path / "data", db_path=tmp_path / "db.sqlite",
max_concurrent_llm=1)`; `create_app(settings, mcp_session=SetupSession(),
llm_client=LlmClient(settings))` (`SetupSession` from `tests.test_api_projects`); enter
lifespan and an `httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
base_url="http://test")`; yield `(client, settings)`. No sign-in inside the harness.
`_db(settings, sql, params)` helper = the `_write` helper from `tests.test_api_projects`
(import it).

1. `test_register_makes_first_admin_and_signs_in`: `GET /api/auth/config` == `{"signup_open":
   True, "has_users": False}`; `GET /api/auth/me` -> 401 `{"detail": "sign in first"}`; POST
   register without the header -> 403 `{"detail": "request is missing the X-Statlab-Csrf
   header"}`; set the header; register `{"email": " Owner@Example.TEST ", "name": " Owner ",
   "password": PASSWORD}` -> 201, body `id` starts with `u_`, rest == `{"email":
   "owner@example.test", "name": "Owner", "is_admin": True}`; the `set-cookie` header
   lowercased contains `statlab_session=`, `httponly`, `samesite=strict`, `path=/` and not
   `secure`; `GET /api/auth/me` -> 200 same body; config -> `has_users` True; `client.cookies.
   clear()`; register `second@example.test` -> 201 `is_admin` False; register
   `OWNER@example.test` -> 409 `{"detail": "an account with this email already exists"}`.
2. `test_register_rejects_bad_input`: parametrize body over bad email `"nope"`, short password
   `"short"`, blank name `"  "` (others valid) -> 422.
3. `test_login_logout_and_throttle`: `sign_in(client)`; `POST /api/auth/logout` -> 204 and the
   `set-cookie` header contains `statlab_session=` and `max-age=0` (lowercased); `/me` -> 401;
   login with wrong password -> 401 `{"detail": "email or password is incorrect"}`; login with
   `nobody@example.test` -> 401 same detail; correct login -> 200 `is_admin` True; `/me` 200;
   then five wrong-password logins -> each 401; a sixth login with the correct password -> 429
   `{"detail": "too many failed sign-ins; try again in 15 minutes"}`; `client.cookies.clear()`
   then logout -> 204.
4. `test_expired_disabled_and_deleted_sessions`: `sign_in`; `_db(..., "UPDATE auth_sessions SET
   expires_at = ?", ("2000-01-01T00:00:00+00:00",))` -> `/me` 401; correct login -> 200; `token
   = client.cookies["statlab_session"]`; logout -> 204; `client.cookies.set("statlab_session",
   token)` -> `/me` 401; login -> 200; `_db(..., "UPDATE users SET disabled = ?", ("1",))` ->
   `/me` 401; login -> 403 `{"detail": "this account is disabled"}`.
5. `test_members_and_project_access` (uses `_open_project_api` from `tests.test_api_projects`;
   the harness user is `owner@example.test`, the first account, so admin; project `Demo`):
   - `owner = (await client.get("/api/auth/me")).json()`; `GET .../members` -> 200, one entry
     with `user_id == owner["id"]`, `role == "owner"`, `email == "owner@example.test"`.
   - `bob = await sign_in(client, "bob@example.test", "Bob")` (now acting as Bob, not admin):
     `GET /api/projects` -> `[]`; `GET /api/projects/{demo}` -> 404 `{"detail": "project not
     found"}`; `GET .../{demo}/members` -> 404 same; `GET /api/projects/p_missing` -> 404 same;
     `GET /api/vocabulary` -> 200.
   - Bob `POST /api/projects {"slug": "bobs", "name": "Bobs"}` -> 201 (`bobs` id);
     `GET /api/projects` names == `["Bobs"]`.
   - `carol = await sign_in(client, "carol@example.test", "Carol")`; `bob = await
     sign_in(client, "bob@example.test", "Bob")` (login branch). Bob adds `" CAROL@example.test
     "` -> 201 `role == "member"`; again -> 409 `{"detail": "this person is already a
     member"}`; `nobody@example.test` -> 404 `{"detail": "no account uses this email"}`;
     `DELETE .../bobs/members/{carol}` -> 204 (owner removes a member); add Carol again -> 201;
     `DELETE .../bobs/members/u_missing` -> 404 `{"detail": "member not found"}`.
   - `sign_in(client, "carol@example.test", "Carol")`: `GET /api/projects` names `["Bobs"]`;
     add `owner@example.test` -> 403 `{"detail": "only the project owner can add members"}`;
     `DELETE .../members/{bob}` -> 409 `{"detail": "the project owner cannot be removed"}`;
     `DELETE /api/projects/{bobs}` -> 403 `{"detail": "only the project owner can delete this
     project"}`.
   - `sign_in(client, "bob@example.test", "Bob")`; Bob adds `owner@example.test` -> 201;
     `sign_in(client, "carol@example.test", "Carol")`; Carol `DELETE .../members/{owner id}`
     -> 403 `{"detail": "only the project owner can remove members"}`; Carol `DELETE
     .../members/{carol}` -> 204 (leaves); `GET /api/projects/{bobs}` -> 404.
   - `sign_in(client)` (owner, admin): `GET /api/projects/{bobs}` -> 200; `DELETE
     /api/projects/{bobs}` -> 204.
6. `test_run_and_artifact_access`: `_open_project_api`; seed with `_seed_run(settings.db_path,
   demo_id, ProjectPaths(settings.data_dir, "demo").root, "completed")` (run `r_seed`,
   artifact `a_seed`); `sign_in(client, "bob@example.test", "Bob")`: `GET /api/runs/r_seed` ->
   404 `{"detail": "run not found"}`; `GET /api/runs/r_seed/events` -> 404 same; `GET
   /api/artifacts/a_seed` -> 404 `{"detail": "artifact not found"}`; `GET
   /api/runs/r_0123456789abcdef` -> 404; `GET /api/artifacts/a_0123456789abcdef` -> 404.
7. `test_assistant_is_scoped_to_the_user` (uses `_open_assistant_api` from
   `tests.test_api_assistant`, replies `[]`): `sign_in(client, "bob@example.test", "Bob")`;
   `POST /api/assistant/turn` with `{"messages": [{"role": "user", "text": "hi"}], "state":
   {"project_id": <demo id>}}` -> 404 `{"detail": "project not found"}` (no LLM call is made;
   `calls == []`).

### Checks (implementer, in order; stop and report on any failure)

1. `make test-fast TESTS="tests/test_auth.py tests/test_accounts.py tests/test_api_auth.py
   tests/test_db_schema.py tests/test_db_migrations.py tests/test_db_ids.py
   tests/test_repository.py tests/test_config.py"`
2. `make test-fast TESTS="tests/test_api_projects.py tests/test_api_runs.py
   tests/test_api_assistant.py tests/test_api_inspect.py tests/test_api_stream.py
   tests/test_control_rewind.py tests/test_storage_fork.py"`
3. Ruff check, Ruff format --check and mypy on every owned Python file.

Stop conditions: any failing existing test you would have to change beyond section 13; any
needed change outside the owned files; mypy needing a type ignore or `Any`. Report exact
commands, exits and the failing assertion.

Main then: review, `make check`, then stage 71b's detailed spec.

### Addendum 71a-A (sign-in helper module; import cycle)

`sign_in` in `tests/test_api_auth.py` and that module's imports from `tests/test_api_projects.py`
form an import cycle. Resolution: new owned file `tests/auth_support.py` holds `PASSWORD =
"correct horse battery"` and `sign_in` exactly as section 13 describes; it imports only
`httpx`, `typing.cast` and `pydantic.JsonValue` (no test modules). `tests/test_api_projects.py`,
`tests/test_api_runs.py`, `tests/test_api_assistant.py` and `tests/test_api_auth.py` import
`PASSWORD`/`sign_in` from `tests.auth_support`. `tests/test_api_auth.py` does not define them.

The other `client.post("/api/projects", ...)` calls the implementer listed (test_api_projects
~347, 572, 630, 1085-1159, 1245, 1550; test_api_runs ~414, 1777; test_api_assistant ~467) all
run inside a harness context or the ~line-299 app after `sign_in`; no extra edits. If one still
fails with 401/403, stop and report it.

Line-length or formatting fixes that Ruff reports in owned files are mechanical: apply them
(`ruff format` on the owned file is allowed).

### Addendum 71a-B (sign_in login body; FK test filler)

1. `tests/auth_support.py` `sign_in`: on the 409 branch return the login response's JSON body
   (assert 200 first); on the 201 branch return the register body.
2. `tests/test_db_migrations.py` `test_m13_every_fk_is_enforceable`: add `"role": "member"` to
   `known_valid_values` (the new `project_members.role` CHECK). Nothing else in that test.

### Addendum 71a-C (raw ASGI stream helper carries the session cookie)

New owned file: `tests/test_api_stream.py`. `_drive_request` gains a required keyword
`session: str`; the scope's `"headers"` becomes `[*headers, (b"cookie",
f"statlab_session={session}".encode())]`. Both callers (~236, ~298) pass
`session=api.client.cookies["statlab_session"]` (the `_open_run_api` client is signed in).
Nothing else changes.

### Stage 71a receipt (2026-10-10)

Implementer (after addenda A-C): focused check groups 1 and 2 exit 0; Ruff check/format and
mypy on the 21 owned Python files exit 0; no type ignores or `Any`. Deviations accepted: line
`# noqa: S105/S106` on test passwords and token hashes (same convention as
`tests/conftest.py`), `int(...)` around `count_users`' scalar for mypy. Main reviewed the
diff against sections 1-14 (guard order: CSRF, public path, session, admin bypass, membership
404 mirroring the missing-entity texts). `make check` (`check71a.log`, temporary) exit 0:
coverage 100 % (TOTAL 13048 stmts, 0 missed), mypy 208 files, Ruff clean.

## Stage 71b (detailed spec) — budgets, enforcement, admin API, change owner

Prerequisite: stage 71a accepted (`5460ebd`). Owned files (new): `backend/statlab/budgets.py`,
`backend/statlab/api/admin.py`, `tests/test_budgets.py`, `tests/test_api_admin.py`. Owned
files (edit): `backend/statlab/persistence/accounts.py`, `backend/statlab/graph/candidates.py`,
`backend/statlab/api/access.py`, `backend/statlab/api/auth.py`,
`backend/statlab/api/runs.py`, `backend/statlab/api/projects.py`,
`backend/statlab/api/assistant.py`, `backend/statlab/main.py`, `tests/test_accounts.py`,
`tests/test_control_lifecycle.py`, `tests/test_api_auth.py`, `integration/driver.py`,
`integration/run_scenarios.py`. Not alone: preserve others' edits.

### 1. `persistence/accounts.py` additions

- `def list_users(conn) -> tuple[UserRow, ...]` — `SELECT * FROM users ORDER BY created_at, email`.
- `def update_user(conn, user_id: str, *, is_admin: bool, disabled: bool, monthly_limit_usd:
  float | None) -> None` — one transaction: `UPDATE users SET is_admin = ?, disabled = ?,
  monthly_limit_usd = ? WHERE id = ?` (bools as `1 if x else 0`); if `disabled`: `DELETE FROM
  auth_sessions WHERE user_id = ?`.
- `def set_owner(conn, project_id: str, user_id: str) -> None` — docstring "The previous owner
  stays as a member." One transaction: `UPDATE project_members SET role = 'member' WHERE
  project_id = ? AND role = 'owner'`; then `INSERT INTO project_members (id, project_id,
  user_id, role, added_at) VALUES (?, ?, ?, 'owner', ?) ON CONFLICT(project_id, user_id) DO
  UPDATE SET role = 'owner'` with `new_id("pm")`, `utc_now()`.

### 2. `budgets.py` (new, `backend/statlab/budgets.py`)

Docstring: "Spend and budget limits (chunk 71). Spend is llm_calls.cost_usd; a run counts
against the person who started it." Imports `sqlite3`, `datetime`, pydantic `BaseModel`,
`ConfigDict`, `Field`; `transaction`; accounts `UserRow`, `get_user`; repository
`require_run`; models `RunRow`.

Models (`ConfigDict(frozen=True, extra="forbid")`):
- `AdminSettings`: `default_monthly_limit_usd: float | None = Field(default=None, ge=0,
  le=1_000_000)`, `default_project_limit_usd` and `default_run_limit_usd` the same,
  `signup_open: bool = True`.
- `Meter`: `spent_usd: float`, `limit_usd: float | None`.
- `RunBudget`: `run: Meter`, `project: Meter`, `person: Meter`, `started_by: str | None` (the
  starter's name), `blocked: str | None`.

Functions:
- `read_settings(conn) -> AdminSettings` — `SELECT value_json FROM app_settings WHERE id =
  'admin'`; no row -> `AdminSettings()`; else `AdminSettings.model_validate_json(row[0])`.
- `write_settings(conn, settings: AdminSettings) -> None` — one transaction: `INSERT INTO
  app_settings (id, value_json) VALUES ('admin', ?) ON CONFLICT(id) DO UPDATE SET value_json =
  excluded.value_json` with `settings.model_dump_json()`.
- `month_start(now: datetime) -> str` — `f"{now:%Y-%m}-01"` (stored timestamps are ISO text,
  so `ts >= "YYYY-MM-01"` selects the month).
- `spend_since(conn, since: str) -> float` — `SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls
  WHERE ts >= ?`; wrap in `float(...)`. `""` means all time.
- `run_spend(conn, run_id) -> float` — `... FROM llm_calls WHERE run_id = ?`.
- `project_spend(conn, project_id) -> float` — `SELECT COALESCE(SUM(l.cost_usd), 0.0) FROM
  llm_calls l JOIN runs r ON r.id = l.run_id WHERE r.project_id = ?`.
- `person_spend(conn, user_id, since: str) -> float` — `SELECT COALESCE(SUM(l.cost_usd), 0.0)
  FROM llm_calls l JOIN run_budgets b ON b.id = l.run_id WHERE b.started_by = ? AND l.ts >= ?`.
- `project_limits(conn, project_id) -> tuple[float | None, float | None]` — `SELECT limit_usd,
  run_limit_usd FROM project_budgets WHERE id = ?`; no row -> `(None, None)`.
- `run_limit(conn, run_id) -> float | None` — `SELECT limit_usd FROM run_budgets WHERE id =
  ?`; no row -> None.
- `set_project_limits(conn, project_id, *, limit_usd: float | None, run_limit_usd: float |
  None) -> None` — one transaction: `INSERT INTO project_budgets (id, limit_usd, run_limit_usd)
  VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET limit_usd = excluded.limit_usd, run_limit_usd
  = excluded.run_limit_usd`.
- `set_run_limit(conn, run_id, limit_usd: float | None) -> None` — one transaction: `INSERT
  INTO run_budgets (id, started_by, limit_usd) VALUES (?, NULL, ?) ON CONFLICT(id) DO UPDATE
  SET limit_usd = excluded.limit_usd`.
- `recent_runs(conn, limit: int) -> tuple[RunRow, ...]` — `SELECT * FROM runs ORDER BY
  started_at DESC, id LIMIT ?`.
- `_first(*values: float | None) -> float | None` — `next((v for v in values if v is not
  None), None)`.
- `_usd(value: float) -> str` — `f"${value:,.2f}"`.
- `_block(meter: Meter, subject: str) -> str | None` — `limit = meter.limit_usd`; if `limit is
  None or meter.spent_usd < limit`: return None; return `f"{subject} ({_usd(meter.spent_usd)}
  of {_usd(limit)})."`.
- `person_meter(conn, user: UserRow, settings: AdminSettings, since: str) -> Meter` —
  `Meter(spent_usd=person_spend(conn, user.id, since), limit_usd=_first(user.monthly_limit_usd,
  settings.default_monthly_limit_usd))`.
- `project_meter(conn, project_id, settings) -> Meter` — `limit, _ = project_limits(...)`;
  `Meter(spent_usd=project_spend(...), limit_usd=_first(limit,
  settings.default_project_limit_usd))`.
- `run_budget(conn, run_id: str, now: datetime) -> RunBudget` — `run = require_run(conn,
  run_id)`; `settings = read_settings(conn)`; `row = conn.execute("SELECT started_by FROM
  run_budgets WHERE id = ?", (run_id,)).fetchone()`; `starter = get_user(conn,
  row["started_by"]) if row is not None and row["started_by"] is not None else None`; `_,
  project_run_limit = project_limits(conn, run.project_id)`; `run_meter =
  Meter(spent_usd=run_spend(conn, run_id), limit_usd=_first(run_limit(conn, run_id),
  project_run_limit, settings.default_run_limit_usd))`; `project = project_meter(conn,
  run.project_id, settings)`; `person = person_meter(conn, starter, settings,
  month_start(now)) if starter is not None else Meter(spent_usd=0.0, limit_usd=None)`;
  `blocked = _block(run_meter, "This run has used its budget") or _block(project, "This
  project has used its budget") or _block(person, "The person who started this run has used
  this month's budget")`; return `RunBudget(run=run_meter, project=project, person=person,
  started_by=starter.name if starter is not None else None, blocked=blocked)`.
- `start_block(conn, project_id: str, user: UserRow, now: datetime) -> str | None` —
  `settings`; `_, project_run_limit = project_limits(...)`; return `_block(Meter(spent_usd=0.0,
  limit_usd=_first(project_run_limit, settings.default_run_limit_usd)), "New runs here have no
  budget") or _block(project_meter(conn, project_id, settings), "This project has used its
  budget") or person_block(conn, user, now)`.
- `person_block(conn, user: UserRow, now: datetime) -> str | None` — `_block(person_meter(conn,
  user, read_settings(conn), month_start(now)), "You have used this month's budget")`.

### 3. `graph/candidates.py` `RunControl`

- `__init__`: add `self.guard: Callable[[], bool] | None = None` with a comment "Budget guard
  (chunk 71): True pauses the run at this check." (`Callable` is already imported in this
  module; if not, add it to the existing `collections.abc` import.)
- `check`: before the existing `if`, add `if self.status is None and self.guard is not None and
  self.guard(): self._pause = True`. The existing raise stays.

### 4. `api/access.py`

In `authorize`, directly after `request.state.user = user`: if `path.startswith("/api/admin/")
and not user.is_admin`: raise `HTTPException(403, "admins only")`.

### 5. `api/runs.py`

- Imports: `datetime`, `UTC`; `run_budget`, `start_block`, `RunBudget` from
  `statlab.budgets`; `UserRow` from accounts.
- `RunRegistry.binding`: directly after `result = bind_tabular(...)` and before
  `self.bindings[row.id] = result`: `run_id = row.id`; `result.loop.control.guard = lambda:
  run_budget(self.resources.conn, run_id, datetime.now(UTC)).blocked is not None`.
- New method `def _budget_gate(self, run_id: str) -> None` — `blocked =
  run_budget(self.resources.conn, run_id, datetime.now(UTC)).blocked`; if not None: raise
  `HTTPException(402, blocked)`.
- `create(self, project_id, body, *, user: UserRow)`: after `project =
  self.projects.project(project_id)`: `blocked = start_block(self.resources.conn, project.id,
  user, datetime.now(UTC))`; if not None: raise `HTTPException(402, blocked)`; pass
  `started_by=user.id` to `create_run`. Route passes `user=user`.
- `message`: directly after `binding = self.run(run_id)`: `self._budget_gate(run_id)`.
- `answer`: directly after `binding = self.run(run_id)`: `self._budget_gate(run_id)`.
- `control`: directly after `binding = self.run(run_id)`: if `body.action in ("resume",
  "rewind")`: `self._budget_gate(run_id)`.
- New method `def budget(self, run_id: str) -> RunBudget` — `get_run` None -> 404 "run not
  found"; return `run_budget(self.resources.conn, run_id, datetime.now(UTC))`. Route
  `@router.get("/api/runs/{run_id}/budget", response_model=RunBudget)` `async def
  budget_endpoint(run_id: str) -> RunBudget: return registry().budget(run_id)`.

### 6. `api/projects.py` and `api/assistant.py`

- `propose_contract(project_id, body, user: CurrentUser)`: first `blocked =
  person_block(resources().conn, user, datetime.now(UTC))`; if not None: raise
  `HTTPException(402, blocked)`.
- Assistant route `turn(body, user)`: same check before `SetupAssistant(...)`.

### 7. `api/auth.py`

- `register`: first statements: `resource = resources()`; if `not
  read_settings(resource.conn).signup_open and count_users(resource.conn) > 0`: raise
  `HTTPException(403, "sign-up is closed; ask an admin for an account")`. Then as before.
- `config`: `conn = resources().conn`; `AuthConfig(signup_open=read_settings(conn).signup_open,
  has_users=count_users(conn) > 0)`.
- New `@router.get("/api/auth/budget", response_model=Meter)` `async def my_budget(user:
  CurrentUser) -> Meter`: `conn = resources().conn`; return `person_meter(conn, user,
  read_settings(conn), month_start(datetime.now(UTC)))`.

### 8. `api/admin.py` (new)

Docstring: "Admin panel API: people, projects, runs, budgets and defaults (chunk 71). The
access guard admits admins only." Models (`frozen`, `extra="forbid"`):
- `AdminUser`: `id`, `email`, `name`, `is_admin: bool`, `disabled: bool`, `created_at`,
  `last_login_at: str | None`, `monthly_limit_usd: float | None` (override), `month: Meter`,
  `total_spent_usd: float`, `projects: int`, `runs: int`.
- `AdminProject`: `id`, `name`, `slug`, `created_at`, `owner_id: str | None`, `owner_name: str |
  None`, `owner_email: str | None`, `members: int`, `runs: int`, `budget: Meter`, `limit_usd:
  float | None`, `run_limit_usd: float | None`, `effective_run_limit_usd: float | None`.
- `AdminRun`: `id`, `project_id`, `project_name`, `status: RunStatus`, `started_at`,
  `limit_usd: float | None` (override), `budget: RunBudget`.
- `AdminOverview`: `month: str`, `month_spent_usd: float`, `total_spent_usd: float`, `users:
  int`, `admins: int`, `disabled_users: int`, `projects: int`, `runs: int`, `open_runs: int`,
  `blocked_runs: tuple[AdminRun, ...]`, `settings: AdminSettings`.
- `_LIMIT = Field(default=None, ge=0, le=1_000_000)` style per field:
  `UserPatch`: `is_admin: bool = False`, `disabled: bool = False`, `monthly_limit_usd: float |
  None = Field(default=None, ge=0, le=1_000_000)`. `ProjectPatch`: `limit_usd`,
  `run_limit_usd` (same Field). `RunPatch`: `limit_usd: float | None = Field(ge=0,
  le=1_000_000)` (required, null clears). `OwnerChange`: `email: str = Field(max_length=254)`
  with a validator returning `value.strip().lower()`.
- `_OPEN = ("pending", "running", "awaiting_user", "paused")`.

Helpers (`conn` first argument):
- `_count(conn, sql: str, params: tuple[str, ...] = ()) -> int` — `int(conn.execute(sql,
  params).fetchone()[0])`.
- `_admin_user(conn, user, settings, since) -> AdminUser` — `month=person_meter(...)`,
  `total_spent_usd=person_spend(conn, user.id, "")`, `projects=_count(conn, "SELECT count(*)
  FROM project_members WHERE user_id = ?", (user.id,))`, `runs=_count(conn, "SELECT count(*)
  FROM run_budgets WHERE started_by = ?", (user.id,))`, other fields copied.
- `_admin_project(conn, project: ProjectRow, settings) -> AdminProject` — `owner =
  conn.execute("SELECT u.id, u.name, u.email FROM project_members pm JOIN users u ON u.id =
  pm.user_id WHERE pm.project_id = ? AND pm.role = 'owner'", (project.id,)).fetchone()`;
  `limit, run_limit = project_limits(...)`; `owner_id/owner_name/owner_email = owner[...] if
  owner is not None else None`; `members=_count(... project_members WHERE project_id = ?)`,
  `runs=_count(... runs WHERE project_id = ?)`, `budget=project_meter(...)`,
  `effective_run_limit_usd=_first(run_limit, settings.default_run_limit_usd)` (import `_first`
  is private: instead compute `run_limit if run_limit is not None else
  settings.default_run_limit_usd`).
- `_admin_run(conn, run: RunRow, now) -> AdminRun` — `project_name=require_project(conn,
  run.project_id).name`, `limit_usd=run_limit(conn, run.id)`, `budget=run_budget(conn, run.id,
  now)`.

`def admin_router(resources: Callable[[], APIResources]) -> APIRouter`:
- `GET /api/admin/overview` -> `AdminOverview`: `now = datetime.now(UTC)`; open runs =
  `recent_runs(conn, 1_000_000)` filtered by `status in _OPEN`; `blocked_runs` = their
  `_admin_run` where `budget.blocked is not None`; `month=f"{now:%Y-%m}"`;
  `month_spent_usd=spend_since(conn, month_start(now))`; `total_spent_usd=spend_since(conn,
  "")`; counts: users, `... WHERE is_admin = 1`, `... WHERE disabled = 1`, projects, runs;
  `open_runs=len(open)`; `settings=read_settings(conn)`.
- `GET /api/admin/users` -> `tuple[AdminUser, ...]` over `list_users`.
- `PATCH /api/admin/users/{user_id}` (`body: UserPatch`, `me: CurrentUser`) -> `AdminUser`:
  `target = get_user(conn, user_id)`; None -> 404 "user not found"; `fields =
  body.model_fields_set`; if `target.id == me.id and (("is_admin" in fields and not
  body.is_admin) or ("disabled" in fields and body.disabled))`: 409 "you cannot remove your own
  admin rights or disable your own account"; `update_user(conn, target.id,
  is_admin=body.is_admin if "is_admin" in fields else target.is_admin, disabled=body.disabled
  if "disabled" in fields else target.disabled, monthly_limit_usd=body.monthly_limit_usd if
  "monthly_limit_usd" in fields else target.monthly_limit_usd)`; return `_admin_user(conn,
  get_user(...) re-read, settings, month_start(now))` (re-read with `get_user`; it exists).
- `GET /api/admin/projects` -> over `list_projects(conn)`.
- `PATCH /api/admin/projects/{project_id}` (`ProjectPatch`) -> `AdminProject`: `project =
  ProjectsService(resource).project(project_id)` (404 "project not found"); `limit, run_limit =
  project_limits(...)`; merge by `model_fields_set` as above; `set_project_limits`; return.
- `PUT /api/admin/projects/{project_id}/owner` (`OwnerChange`) -> `AdminProject`: project as
  above; `target = get_user_by_email`; None -> 404 "no account uses this email";
  `set_owner(conn, project.id, target.id)`; return.
- `GET /api/admin/runs` with `limit: Annotated[int, Query(ge=1, le=500)] = 100` ->
  `tuple[AdminRun, ...]` over `recent_runs(conn, limit)`.
- `PATCH /api/admin/runs/{run_id}` (`RunPatch`) -> `AdminRun`: `run = get_run(...)`; None ->
  404 "run not found"; `set_run_limit(conn, run.id, body.limit_usd)`; return `_admin_run`.
- `GET /api/admin/settings` -> `AdminSettings`; `PUT /api/admin/settings` (`body:
  AdminSettings`) -> `write_settings`; return `read_settings(conn)`.

`main.py`: `app.include_router(admin_router(resources))` after the members router.

### 9. Integration harness

- `integration/driver.py` `Driver`: new `async def sign_in(self) -> None` — sets
  `self.client.headers["X-Statlab-Csrf"] = "1"`; credentials `HARNESS_EMAIL =
  "harness@example.test"`, `HARNESS_NAME = "Harness"`, `HARNESS_PASSWORD =
  "harness-password-0"` (module constants); `response = await self._call("POST",
  "/api/auth/register", expected={201, 409}, json={"email": ..., "name": ..., "password":
  ...})`; if `response.status_code == 409`: `await self._call("POST", "/api/auth/login",
  expected={200}, json={"email": ..., "password": ...})`.
- `integration/run_scenarios.py`: directly after `driver = Driver(...)`: `await
  driver.sign_in()`.

### 10. Tests

`tests/test_accounts.py` add `test_admin_account_updates`: users `a` (admin) and `b`;
`list_users(conn) == (a, b)` by id order of creation (compare ids); session for `b`;
`update_user(conn, b.id, is_admin=True, disabled=False, monthly_limit_usd=2.5)` -> `get_user`
shows `is_admin`, limit 2.5, session still resolves; `update_user(..., disabled=True, ...)` ->
session count for `b` is 0. Project owned by `a` (`create_project(owner_id=a.id)`):
`set_owner(conn, project.id, b.id)` -> roles `{a.id: "member", b.id: "owner"}`;
`set_owner(conn, project.id, a.id)` -> `{a.id: "owner", b.id: "member"}`.

`tests/test_control_lifecycle.py` add `test_budget_guard_pauses_at_next_check(engine_world)`:
`nodes = (node(), node("beta"))`; `blocked = [False]`; `control = RunControl()`;
`control.guard = lambda: blocked[0]`; `calls = 0`; `work`: `calls += 1`; `control.check()`;
if `calls == 1`: `blocked[0] = True`; return completed. `loop = engine_world.loop(nodes,
work)`; `loop.control = control`; `service = RunLifecycle(loop, engine_world.initial(nodes))`;
`paused = await service.start()`: `status == "paused"`, `cursor == 1`, `control.status ==
"paused"`; `blocked[0] = False`; `resumed = await service.resume()`: `status ==
"completed"`; `calls == 3`.

`tests/test_budgets.py` (`migrated_db`; `now = datetime(2026, 10, 10, tzinfo=UTC)`):
1. `test_settings_round_trip`: default equals `AdminSettings()`; write
   `AdminSettings(default_monthly_limit_usd=5.0, signup_open=False)` -> read equals; write
   `AdminSettings()` -> read equals `AdminSettings()`.
2. `test_run_budget_limits_and_messages`: user `a` (`name="A"`); project with `owner_id`;
   contract; `run = create_run(..., started_by=a.id)`; `execution =
   create_node_execution(migrated_db, run_id=run.id, node_name="data_profiling", seq=1)`;
   `record_llm_call(..., cost_usd=0.5, ts="2026-10-05T00:00:00+00:00")` and
   `record_llm_call(..., cost_usd=0.25, ts="2026-09-30T23:00:00+00:00")` (signature "s", model
   "m", tokens 0, duration 1). Assertions in order:
   - `month_start(now) == "2026-10-01"`; `spend_since(..., "2026-10-01") == 0.5`;
     `spend_since(..., "") == 0.75`.
   - `run_budget(conn, run.id, now) == RunBudget(run=Meter(spent_usd=0.75, limit_usd=None),
     project=Meter(spent_usd=0.75, limit_usd=None), person=Meter(spent_usd=0.5,
     limit_usd=None), started_by="A", blocked=None)`.
   - write settings `default_run_limit_usd=0.75` -> `.blocked == "This run has used its
     budget ($0.75 of $0.75)."`.
   - `set_project_limits(..., limit_usd=None, run_limit_usd=1.0)` -> `.blocked is None` and
     `.run.limit_usd == 1.0`.
   - `set_run_limit(conn, run.id, 0.5)` -> `.blocked == "This run has used its budget ($0.75 of
     $0.50)."`; `run_limit(conn, run.id) == 0.5`.
   - `set_run_limit(conn, run.id, None)`; `set_project_limits(..., limit_usd=0.75,
     run_limit_usd=None)`; write `AdminSettings()` -> `.blocked == "This project has used its
     budget ($0.75 of $0.75)."`; `project_limits(...) == (0.75, None)`.
   - `set_project_limits(..., None, None)`; `update_user(conn, a.id, is_admin=True,
     disabled=False, monthly_limit_usd=0.5)`; `user = get_user(conn, a.id)` -> `.blocked ==
     "The person who started this run has used this month's budget ($0.50 of $0.50)."`;
     `person_block(conn, user, now) == "You have used this month's budget ($0.50 of $0.50)."`;
     `start_block(conn, project.id, user, now)` equals the same "You have..." text.
   - `update_user(..., monthly_limit_usd=None)`; `start_block(...) is None`;
     `set_project_limits(..., limit_usd=None, run_limit_usd=0.0)` -> `start_block(...) == "New
     runs here have no budget ($0.00 of $0.00)."`; `set_project_limits(..., limit_usd=0.75,
     run_limit_usd=None)` -> `start_block(...) == "This project has used its budget ($0.75 of
     $0.75)."`.
   - Raw run `r_raw` (raw INSERT as in test_accounts, started_at `2026-01-01T00:00:00+00:00`,
     no run_budgets row): `run_budget(conn, "r_raw", now).started_by is None` and `.person ==
     Meter(spent_usd=0.0, limit_usd=None)`; `run_limit(conn, "r_raw") is None`;
     `[r.id for r in recent_runs(conn, 10)] == [run.id, "r_raw"]`; `len(recent_runs(conn, 1))
     == 1`.

New tests go in `tests/test_api_admin.py` (imports `_open_project_api`, `_seed_run`, `_write` from
`tests.test_api_projects`; `_open_run_api`, `_create_run`, `_gate` from `tests.test_api_runs`;
`_open_assistant_api` is not needed; `sign_in` from `tests.auth_support`):
1. `test_admin_routes_need_admin`: `_open_project_api`; `sign_in(client, "bob@example.test",
   "Bob")`; `GET /api/admin/overview` -> 403 `{"detail": "admins only"}`.
2. `test_admin_settings_close_signup`: owner: `GET /api/admin/settings` == `{
   "default_monthly_limit_usd": None, "default_project_limit_usd": None,
   "default_run_limit_usd": None, "signup_open": True}`; `PUT` with `{"default_monthly_limit_usd":
   5, "default_project_limit_usd": None, "default_run_limit_usd": 1, "signup_open": False}` ->
   200, body equals it with floats `5.0`, `1.0`; `GET /api/auth/config` -> `signup_open`
   False; `PUT` with `default_run_limit_usd: -1` -> 422; `client.cookies.clear()`; register
   `new@example.test` -> 403 `{"detail": "sign-up is closed; ask an admin for an account"}`.
3. `test_admin_users_patch`: `bob = await sign_in(client, "bob@example.test", "Bob")`; `owner =
   await sign_in(client)`; `GET /api/admin/users` -> 2 rows, first `email ==
   "owner@example.test"` and `is_admin`, Bob row `month == {"spent_usd": 0.0, "limit_usd":
   None}`, `projects == 0`, `runs == 0`, `total_spent_usd == 0.0`, owner row `projects == 1`.
   `PATCH /api/admin/users/{bob}` `{"monthly_limit_usd": 2.5}` -> 200, `monthly_limit_usd ==
   2.5`, `month["limit_usd"] == 2.5`, `is_admin` False; `{"is_admin": True}` -> `is_admin`
   True and `monthly_limit_usd` still 2.5; `{"disabled": True}` -> `disabled` True and `SELECT
   count(*) FROM auth_sessions WHERE user_id = ?` (bob, via `sqlite3.connect(settings.db_path)`
   closed in `finally`) is 0. Self: `{"is_admin": False}` and `{"disabled": True}` -> each 409
   `{"detail": "you cannot remove your own admin rights or disable your own account"}`;
   `{"monthly_limit_usd": 1}` -> 200. `PATCH /api/admin/users/u_missing` `{}` -> 404
   `{"detail": "user not found"}`. `GET /api/auth/budget` -> `{"spent_usd": 0.0, "limit_usd":
   1.0}`.
4. `test_admin_projects_and_owner`: `bob = await sign_in(client, "bob@example.test", "Bob")`;
   `await sign_in(client)`; `GET /api/admin/projects` -> one row: `owner_name == "Owner"`,
   `owner_email == "owner@example.test"`, `members == 1`, `runs == 0`, `budget ==
   {"spent_usd": 0.0, "limit_usd": None}`, `limit_usd`, `run_limit_usd`,
   `effective_run_limit_usd` all None. `PATCH .../{demo}` `{"limit_usd": 3}` -> `limit_usd ==
   3.0`, `budget["limit_usd"] == 3.0`; `{"run_limit_usd": 0.5}` -> `limit_usd == 3.0`,
   `run_limit_usd == 0.5`, `effective_run_limit_usd == 0.5`; `{"limit_usd": None}` ->
   `limit_usd` None, `run_limit_usd == 0.5`. `PUT .../{demo}/owner` `{"email": "
   BOB@example.test "}` -> `owner_name == "Bob"`, `members == 2`; `GET
   /api/projects/{demo}/members` roles by email `{"bob@example.test": "owner",
   "owner@example.test": "member"}`. `PUT .../owner` `{"email": "nobody@example.test"}` -> 404
   `{"detail": "no account uses this email"}`; `PATCH /api/admin/projects/p_missing` `{}` and
   `PUT /api/admin/projects/p_missing/owner` -> 404 `{"detail": "project not found"}`.
5. `test_admin_runs_overview_and_run_budget`: `_seed_run(settings.db_path, demo, root,
   "completed")`; `ts = datetime.now(UTC).isoformat()`; `_write` `INSERT INTO node_executions
   (id, run_id, node_name, seq, attempt, status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)` with
   `("ne_seed", "r_seed", "data_profiling", "1", "1", "completed", ts)`; `_write` `INSERT INTO
   llm_calls (id, run_id, node_execution_id, signature, model, prompt_tokens,
   completion_tokens, cached_tokens, cost_usd, duration_ms, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
   ?, ?, ?)` with `("lc_seed", "r_seed", "ne_seed", "s", "m", "0", "0", "0", "0.75", "1", ts)`.
   `GET /api/admin/runs` -> one row: `id == "r_seed"`, `project_name == "Demo"`, `status ==
   "completed"`, `limit_usd` None, `budget["run"] == {"spent_usd": 0.75, "limit_usd": None}`,
   `budget["started_by"] is None` (`r_seed` is inserted after sign-in with no run_budgets
   row),
   `budget["blocked"]` None. `PATCH /api/admin/runs/r_seed` `{"limit_usd": 0.5}` ->
   `limit_usd == 0.5`, `budget["blocked"] == "This run has used its budget ($0.75 of
   $0.50)."`; `GET /api/runs/r_seed/budget` -> same `blocked`; `PATCH
   /api/admin/runs/r_missing` `{"limit_usd": 1}` -> 404 `{"detail": "run not found"}`; `GET
   /api/runs/r_0123456789abcdef/budget` -> 404; `GET /api/admin/runs?limit=0` -> 422. `GET
   /api/admin/overview` -> `users == 1`, `admins == 1`, `disabled_users == 0`, `projects ==
   1`, `runs == 1`, `open_runs == 0`, `blocked_runs == []`, `month_spent_usd == 0.75`,
   `total_spent_usd == 0.75`, `month == datetime.now(UTC).strftime("%Y-%m")`, `settings`
   equals the defaults dict. `_write` `UPDATE runs SET status = ? WHERE id = ?` `("paused",
   "r_seed")` -> overview `open_runs == 1`, `[r["id"] for r in blocked_runs] == ["r_seed"]`.
6. `test_budget_refusals` (`_open_run_api(tmp_path, monkeypatch, records=FixtureRecords())` —
   import `FixtureRecords` from `tests.test_api_runs`): `created = await _create_run(api)`;
   `run_id = str(created.json()["run_id"])`; `gate = await _gate(api, run_id)`; `PUT
   /api/admin/settings` `{"default_monthly_limit_usd": None, "default_project_limit_usd":
   None, "default_run_limit_usd": 0, "signup_open": True}` -> 200;
   `api.registry.bindings[run_id].loop.control.guard` is not None and returns True. Each of
   these returns 402 `{"detail": "This run has used its budget ($0.00 of $0.00)."}`: `POST
   /api/runs/{run_id}/messages` `{"text": "hi"}`; `POST /api/runs/{run_id}/gates/{gate.id}`
   `{"action": "approve"}`; `POST /api/runs/{run_id}/control` `{"action": "resume"}`; same with
   `{"action": "rewind", "target": "data_profiling", "request_id": "req-1"}`. `GET
   /api/runs/{run_id}/budget` -> `blocked` equals that text. `POST
   /api/projects/{api.project_id}/runs` (same body `_create_run` sends) -> 402 `{"detail":
   "New runs here have no budget ($0.00 of $0.00)."}`. Then `PUT` settings with
   `default_run_limit_usd: None, default_monthly_limit_usd: 0` -> `POST /api/assistant/turn`
   `{"messages": [{"role": "user", "text": "hi"}]}`, `POST
   /api/projects/{api.project_id}/contracts/propose` `{"dataset_id": api.dataset_id,
   "contract": {"target": {"column": "b"}}}` and the run POST each -> 402 `{"detail": "You
   have used this month's budget ($0.00 of $0.00)."}`; `POST /api/runs/{run_id}/messages` ->
   402 `{"detail": "The person who started this run has used this month's budget ($0.00 of
   $0.00)."}`.

### Checks (implementer, in order; stop and report on any failure)

1. `make test-fast TESTS="tests/test_budgets.py tests/test_accounts.py tests/test_api_admin.py
   tests/test_api_auth.py tests/test_control_lifecycle.py"`
2. `make test-fast TESTS="tests/test_api_runs.py tests/test_api_projects.py
   tests/test_api_assistant.py tests/test_api_stream.py tests/test_candidate_fanout.py
   tests/test_control_recovery.py"`
3. Ruff check, Ruff format --check and mypy on every owned Python file (including
   `integration/driver.py` and `integration/run_scenarios.py`).
4. `.venv/bin/python -m integration.run_scenarios --llm offline --only S01 S07` (offline, no
   cost); report exit, PASS/FAIL rows and the output folder.

Stop conditions as in 71a. Main then: review, `make check`, then stage 71c's spec.

### Addendum 71b-A (no re-read after the user patch)

`PATCH /api/admin/users/{user_id}`: do not re-read with `get_user`. Compute the merged values,
build `updated = target.model_copy(update={"is_admin": ..., "disabled": ...,
"monthly_limit_usd": ...})`, call `update_user(conn, target.id, is_admin=updated.is_admin,
disabled=updated.disabled, monthly_limit_usd=updated.monthly_limit_usd)`, and return
`_admin_user(conn, updated, settings, month_start(now))`. Accepted placement choices: budget
route before `GET /api/runs/{run_id}`; `PUT .../p_missing/owner` body `{"email":
"bob@example.test"}`. Rerun Check 3, then Check 4.

### Stage 71b receipt (2026-10-10)

Implementer (after addendum A): check 1 (budgets, accounts, admin API, auth API, lifecycle)
exit 0; check 2 (runs, projects, assistant, stream, fan-out, recovery) exit 0, 224 passed;
Ruff check/format and mypy on the 17 owned files exit 0; offline S01 and S07 exit 0, PASS
25/25 and 11/11 (`integration/out/20261010T080829Z-offline`, harness signs in). Main reviewed
the diff (guard in `RunControl.check`, 402 gates on create/message/answer/resume/rewind,
propose and assistant, admin-only prefix, signup switch, change owner keeps the old owner as a
member). `make check` (`check71b.log`, temporary) exit 0: coverage 100 % (TOTAL                                               13367      0   3740      0   100%), mypy
212 files, Ruff clean.

## Stage 71c (detailed spec) — frontend: sign-in, user menu, members, admin panel, budget banner

Prerequisite: 71b accepted (`dacc02d`). API shapes are the 71a/71b models above.

Design notes (frontend-design review): keep the app's tokens (navy top bar, Atkinson
Hyperlegible, `--signal` blue, `--gate` amber, `--fault` red). One signature element: the
budget meter (thin track, fill turns amber at 80 % and red at the limit, dashed track when there
is no limit), used in the user menu, every admin table and the run banner. The sign-in brand
panel carries a faint ruled-rows texture (a table, the product's material). Admin overview
figures sit in a ruled strip, not a card grid. No all-caps labels, no middle-dot meta strings,
no arrows appended to links.

Owned files (new, under `frontend/`): `src/admin/budget.ts`, `src/ui/BudgetMeter.tsx`,
`src/admin/LimitField.tsx`, `src/admin/AdminPage.tsx`, `src/admin/AdminOverview.tsx`,
`src/admin/AdminPeople.tsx`, `src/admin/AdminProjects.tsx`, `src/admin/AdminRuns.tsx`,
`src/admin/AdminDefaults.tsx`, `src/auth/AuthPage.tsx`, `src/auth/UserMenu.tsx`,
`src/project/MembersPanel.tsx`, `src/run/BudgetBanner.tsx`, `tests/budget.test.ts`.
Owned files (edit): `src/api/types.ts`, `src/api/client.ts`, `src/routes.ts`, `src/App.tsx`,
`src/pages/ProjectPage.tsx`, `src/pages/RunPage.tsx`, `src/ui/format.ts`, `src/style.css`,
`tests/client.test.ts`, `tests/routes.test.ts`, `package.json` (test script only). Not alone:
preserve others' edits. All visible text below is exact.

### 1. `src/routes.ts`

- `export type AdminSection = 'overview' | 'people' | 'projects' | 'runs' | 'defaults';` and
  add `| { name: 'admin'; section: AdminSection }` to `Route`.
- `parseRoute`: after the `projects` single-segment check: one segment `admin` -> `{ name:
  'admin', section: 'overview' }`; two segments with head `admin` and second one of `people`,
  `projects`, `runs`, `defaults` -> that section; any other `admin/...` -> notFound (this check
  sits before the generic `id` handling).
- `formatRoute`: `admin` -> `#/admin` for `overview`, else `#/admin/<section>`.

### 2. `src/api/types.ts`

Add (snake_case like `ProjectSummary`): `AuthUser { id; email; name; is_admin: boolean }`,
`AuthConfig { signup_open: boolean; has_users: boolean }`, `Meter { spent_usd: number;
limit_usd: number | null }`, `RunBudget { run: Meter; project: Meter; person: Meter;
started_by: string | null; blocked: string | null }`, `MemberRole = 'owner' | 'member'`,
`Member { user_id; email; name; role: MemberRole; added_at }`, `AdminSettings {
default_monthly_limit_usd: number | null; default_project_limit_usd: number | null;
default_run_limit_usd: number | null; signup_open: boolean }`, `AdminUser { id; email; name;
is_admin: boolean; disabled: boolean; created_at; last_login_at: string | null;
monthly_limit_usd: number | null; month: Meter; total_spent_usd: number; projects: number;
runs: number }`, `AdminProject { id; name; slug; created_at; owner_id: string | null;
owner_name: string | null; owner_email: string | null; members: number; runs: number; budget:
Meter; limit_usd: number | null; run_limit_usd: number | null; effective_run_limit_usd: number
| null }`, `AdminRun { id; project_id; project_name; status: RunStatus; started_at; limit_usd:
number | null; budget: RunBudget }`, `AdminOverview { month; month_spent_usd; total_spent_usd;
users; admins; disabled_users; projects; runs; open_runs: number; blocked_runs: AdminRun[];
settings: AdminSettings }`, `UserPatch { is_admin?: boolean; disabled?: boolean;
monthly_limit_usd?: number | null }`, `ProjectPatch { limit_usd?: number | null;
run_limit_usd?: number | null }`. (Unlisted fields are strings.)

### 3. `src/api/client.ts`

- `RequestOptions`: `method` adds `'PUT'`; new optional `messages?: Partial<Record<number,
  string>>`.
- `request()`: headers always include `'X-Statlab-Csrf': '1'`. On a non-ok response: if
  `response.status === 401`, call every listener in a module `Set<() => void>` named
  `signedOutListeners`; the message is `options.messages?.[response.status]` when defined,
  else the existing chain extended with `401 -> 'Sign in to continue.'`, `402 -> 'A budget
  limit is reached. Ask an admin to raise it.'`, `429 -> 'Too many failed sign-ins. Try again
  in 15 minutes.'`. Error bodies are still never read.
- `export function onSignedOut(listener: () => void): () => void` — adds to the set, returns a
  function that deletes it.
- New private helper `requiredBoolean(object, key)` (throws `invalidResponse()` unless
  boolean). Parsers (exported, each throws `invalidResponse()` on a wrong shape): `parseAuthUser`,
  `parseAuthConfig`, `parseMeter`, `parseRunBudget`, `parseMembers` (role must be `owner` or
  `member`), `parseAdminSettings`, `parseAdminUsers`, `parseAdminUser`, `parseAdminProjects`,
  `parseAdminProject`, `parseAdminRuns`, `parseAdminRun` (status via `isRunStatus`),
  `parseAdminOverview`. Use `requiredString`, `nullableString`, `requiredNumber`,
  `nullableNumber`, `objectArray`.
- Functions (paths encode ids with `encodeURIComponent`, projects via `projectPath`):
  - `getAuthConfig(signal?)` GET `/api/auth/config`; `getMe(signal?)` GET `/api/auth/me`.
  - `signIn(email, password)` POST `/api/auth/login` `{email, password}`, messages `{401:
    'Email or password is incorrect.', 403: 'This account is disabled. Ask an admin.'}`.
  - `register(name, email, password)` POST `/api/auth/register` `{email, name, password}`,
    messages `{403: 'Sign-up is closed. Ask an admin for an account.', 409: 'An account with
    this email already exists.', 422: 'Check the email address and use a password of at least
    10 characters.'}`.
  - `signOut()` POST `/api/auth/logout`, `text: true`, returns void.
  - `getMyBudget(signal?)` GET `/api/auth/budget` -> `Meter`.
  - `listMembers(projectId, signal?)` GET `${projectPath}/members`; `addMember(projectId,
    email)` POST `{email}`, messages `{403: 'Only the project owner can add people.', 404: 'No
    account uses this email.', 409: 'This person is already a member.'}`; `removeMember(
    projectId, userId)` DELETE `${projectPath}/members/${encoded}`, `text: true`, messages
    `{403: 'Only the project owner can remove people.', 409: 'The project owner cannot be
    removed.'}`.
  - `deleteProject` gains messages `{403: 'Only the project owner can delete this project.'}`.
  - `getRunBudget(runId, signal?)` GET `/api/runs/${encoded}/budget`.
  - Admin, each with messages including `403: 'Admins only.'`: `getAdminOverview(signal?)`,
    `listAdminUsers(signal?)`, `updateAdminUser(userId, patch: UserPatch)` PATCH (also `409:
    'You cannot remove your own admin rights or disable your own account.'`),
    `listAdminProjects(signal?)`, `updateAdminProject(projectId, patch: ProjectPatch)` PATCH,
    `changeProjectOwner(projectId, email)` PUT `/api/admin/projects/${encoded}/owner` `{email}`
    (also `404: 'No account uses this email.'`), `listAdminRuns(signal?)` GET
    `/api/admin/runs?limit=200`, `updateRunLimit(runId, limitUsd: number | null)` PATCH
    `{limit_usd}`, `getAdminSettings(signal?)`, `saveAdminSettings(settings)` PUT.

### 4. `src/admin/budget.ts` (pure, no React)

- `formatUsd(value)`: `value > 0 && value < 0.005` -> `'<$0.01'`; else `'$' +
  value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })`.
- `type MeterTone = 'none' | 'ok' | 'near' | 'over'`; `meterTone(meter)`: limit null -> none;
  `spent >= limit` -> over; `limit > 0 && spent / limit >= 0.8` -> near; else ok.
- `meterFill(meter)`: limit null -> 0; limit 0 -> 1; else `Math.min(1, spent / limit)`.
- `meterText(meter)`: limit null -> `` `${formatUsd(spent)} spent, no limit` ``; else
  `` `${formatUsd(spent)} of ${formatUsd(limit)}` ``.
- `parseLimit(text): number | null | 'invalid'`: trim; `''` -> null; drop one leading `$`;
  `Number(rest)`; if `rest === ''` or not finite or `< 0` or `> 1_000_000` -> `'invalid'`; else
  `Math.round(value * 100) / 100`.
- `limitText(value)`: null -> `''`; else `value.toFixed(2)`.
- `inheritedText(value)`: null -> `'No limit'`; else `` `Default ${formatUsd(value)}` ``.
- `monthLabel(month)`: match `^(\d{4})-(\d{2})$`; no match -> `month`; else `new
  Date(Date.UTC(y, m - 1, 1)).toLocaleString('en-US', { month: 'long', year: 'numeric',
  timeZone: 'UTC' })`.
- `initials(name)`: words of `name.trim()` split on whitespace; first letter of up to the
  first two words, upper-cased; empty -> `'?'`.
- `isOpenStatus(status: RunStatus)`: `pending`, `running`, `awaiting_user`, `paused`.

### 5. Components

`src/ui/BudgetMeter.tsx` — props `{ meter: Meter; label: string; compact?: boolean }`. Root
`div` class `meter meter-${tone}` plus `compact` when set. Children: `div.meter-track`
(`aria-hidden="true"`) containing `span.meter-fill` with inline `width` = `meterFill * 100`
%; then `span.meter-text` containing `span.sr-only` `{label}: ` and `meterText(meter)`.

`src/admin/LimitField.tsx` — props `{ label: string; value: number | null; placeholder:
string; pending: boolean; onSave: (value: number | null) => void }`. `useId()` for the input
id. State `draft` initialised to `limitText(value)`; an effect resets it when `value` changes.
`parsed = parseLimit(draft)`; `dirty = draft.trim() !== limitText(value)`. Renders `form.limit-
field` (onSubmit: preventDefault; if `parsed !== 'invalid'` call `onSave(parsed)`):
`label.sr-only` (htmlFor) `{label}`, `span.limit-prefix` `$` (aria-hidden), `input`
`type="text" inputMode="decimal"` value/onChange, `placeholder`, `aria-invalid={parsed ===
'invalid'}`; when `dirty`: `button.limit-save` `type="submit"` text `Save`, disabled when
`pending || parsed === 'invalid'`.

`src/auth/AuthPage.tsx` — props `{ config: AuthConfig; onSignedIn: () => void }`. `canRegister
= config.signup_open || !config.has_users`. State `mode: 'signin' | 'register'` initial
`config.has_users ? 'signin' : 'register'`; `name`, `email`, `password`. Mutation calls
`signIn(email.trim(), password)` or `register(name.trim(), email.trim(), password)`; success ->
`onSignedIn()`. Markup: `div.auth` > `section.auth-brand` (`span.brand-mark.large`
aria-hidden, `p.auth-name` `Statlab`, `p.auth-line` `Upload a table. An agent cleans it, fits
several models and scores the best one once on held-out data. You approve the key
decisions.`) and `main.auth-panel` > `div.auth-card`: when `canRegister && config.has_users`
a `div.tabs` role tablist with two tab buttons `Sign in` / `Create account`
(`aria-selected`, click sets mode and resets the mutation); `h1` `Sign in` or `Create your
account`; when `!config.has_users` `p.help` `No accounts yet. The first account becomes the
admin.`; `form` (onSubmit preventDefault, mutate): register only `div.field` label `Name`
input text `autoComplete="name"` required maxLength 100; `div.field` label `Email` input
`type="email"` `autoComplete="email"` required maxLength 254; `div.field` label `Password`
input `type="password"` required, `autoComplete` `current-password` or `new-password`,
`minLength={10}` and `maxLength={128}` in register mode, with `p.help` `At least 10
characters.` in register mode; error `p` role alert `errorText(error)`; submit
`button.primary.auth-submit` text `Sign in` / `Create account`, while pending `Signing in…` /
`Creating account…`, disabled while pending. After the form, when `!canRegister`: `p.help`
`Sign-up is closed. Ask an admin for an account.`

`src/auth/UserMenu.tsx` — props `{ user: AuthUser; onSignedOut: () => void }`. Query
`['auth', 'budget']` `getMyBudget`. Sign-out mutation `signOut`, success -> `onSignedOut()`.
`details.user-menu` > `summary` (aria-label `` `Account: ${user.name}` ``) with
`span.avatar` (aria-hidden) `initials(user.name)` and `span.user-name` `{user.name}`; then
`div.help-pop.user-pop`: `p.user-pop-name` name, `p.help` email, `p.help-pop-title` `This
month`, the meter (`BudgetMeter` label `Your spend this month`) or `Loading…` or the error
text; when admin `a.button` href `#/admin` with `ShieldCheck` icon and `Admin panel`;
`button` type button with `LogOut` icon and `Sign out` (disabled while pending).

`src/project/MembersPanel.tsx` — props `{ projectId: string; me: AuthUser; navigate }`.
Query `['members', projectId]`. `canManage = me.is_admin || members.some(m => m.user_id ===
me.id && m.role === 'owner')`. Add mutation (`addMember`) success: clear the email,
invalidate `['members', projectId]`. Remove mutation (`removeMember`) success: if the removed
id is `me.id` navigate to `{ name: 'projects' }`, else invalidate. Markup: `section.panel.
members` aria-labelledby; `h2` `People`; loading `Loading people…`, error alert; `ul.member-
list` > `li` per member: `span.avatar.small` initials, `span.member-who` with `strong`
`{name}` plus ` (you)` for me, and `small.help` email; `span` class `pill signal` / `pill` text
`Owner` / `Member`; for a `member` row: if `m.user_id === me.id` a `button.link-button` `Leave`
(window.confirm `Leave this project? You lose access until the owner adds you again.`), else if
`canManage` a `button.link-button` `Remove` (aria-label `` `Remove ${name}` ``). When
`canManage`: `form.inline-add` with `div.field` label `Add a person by email` and input
`type="email"` required, and `button.primary` type submit with `UserPlus` icon and `Add`
(disabled while pending); `p.help` `They need an account first. Members can start runs, chat
and change settings. Only the owner can add people or delete the project.` Errors of either
mutation in `p` role alert.

`src/run/BudgetBanner.tsx` — props `{ runId: string; status: RunStatus; isAdmin: boolean }`.
Query `['budget', runId, status]` `getRunBudget`. Render nothing unless `data?.blocked` is a
string. Else `div.budget-banner` role status: `TriangleAlert` icon (class `icon`,
aria-hidden), then `div` with `p` (`strong` `Paused by a budget.` when status is `paused`,
else `Budget limit reached.`, then a space and the `blocked` text) and `p.help`: admin ->
`Raise the limit in the ` + `a` href `#/admin/runs` `admin panel` + `, then resume the run.`;
others -> `Ask an admin to raise the limit, then resume the run.`

`src/admin/AdminPage.tsx` — props `{ section: AdminSection; meId: string }`. Markup:
`div.admin` > `header.admin-head` (`h1` `Admin`, `p.secondary-line` `Budgets, people and
projects. Spend counts the model calls of each run; a run counts against the person who
started it.`) and `div.admin-body` > `nav.admin-nav` (aria-label `Admin sections`) with
links (href via `formatRoute`, `aria-current="page"` on the active one, icon + text):
`LayoutDashboard` `Overview`, `Users` `People`, `FolderOpen` `Projects`, `Activity` `Runs`,
`SlidersHorizontal` `Defaults`; and `div.admin-main` rendering the section component
(`AdminPeople` gets `meId`). All admin mutations invalidate the query prefix `['admin']`.

`src/admin/AdminOverview.tsx` — query `['admin', 'overview']`. Loading `Loading…`, error
alert. `h2.admin-title` `monthLabel(month)`. `dl.figures` with five `div`s: `Spent this
month` / `formatUsd`; `Spent in total` / `formatUsd`; `People` / `users` plus `dd.figure-note`
`` `${admins} admin${admins === 1 ? '' : 's'}` `` and, when `disabled_users > 0`, `` `,
${disabled_users} disabled` ``; `Projects` / `projects`; `Runs` / `runs` plus
`dd.figure-note` `` `${open_runs} open` ``. `h2` `Waiting on a budget`. Empty -> `p.help` `No
run is waiting on a budget.` Else `ul.attention` > `li.attention-row` per run: `div` with
`a` (href run route) `project_name`, a space, `code` id, and `p` `budget.blocked`; then
`LimitField` label `` `Run limit for ${id}` `` value `limit_usd` placeholder `Inherited`
pending, save -> `updateRunLimit`. Then `h2` `Defaults in use` and `dl.defaults-summary` with
`Monthly limit per person`, `Lifetime limit per project`, `Limit per run` (each `formatUsd` or
`No limit`) and `Sign-up` (`Open` / `Closed`), then `a` href `#/admin/defaults` `Change
defaults`.

`src/admin/AdminPeople.tsx` — props `{ meId: string }`. Queries `['admin', 'users']`,
`['admin', 'settings']`. `p.help` `Empty limit fields use the default. Spend resets on the 1st
of each month (UTC).` Mutation error alert. Table (`div.table-wrap` > `table`, caption sr-only
`People`) columns `Person`, `Role`, `This month`, `Monthly limit`, `Projects`, `Runs`, `Last
sign-in`, `Actions` (last header `span.sr-only`). Cells: `div.person` (`span.avatar.small`,
`span` with `strong` name and `small.help` email); role `span.pill.signal` `Admin` or text
`Member`, plus `span.pill.fault` `Disabled` when disabled; `BudgetMeter` compact label `` `${name}
this month` ``; `LimitField` label `` `Monthly limit for ${name}` `` placeholder
`inheritedText(settings.default_monthly_limit_usd)` (or `No limit` before settings load), save
-> `updateAdminUser(id, { monthly_limit_usd })`; `td.num` projects, runs; `formatTime(
last_login_at)`; actions: for `meId` row `span.help` `You`; else `div.row-actions` with button
`Make admin` / `Remove admin` (patch `is_admin`) and `Disable` (confirm `` `Disable
${name}? They are signed out and cannot sign in until you enable the account again.` ``) /
`Enable` (patch `disabled`).

`src/admin/AdminProjects.tsx` — queries `['admin', 'projects']`, `['admin', 'settings']`;
state `ownerFor: string | null` and `ownerEmail`. `p.help` `Project limits count every run in
the project. The run limit applies to each run that has no limit of its own.` Table columns
`Project`, `Owner`, `Members`, `Runs`, `Spent`, `Project limit`, `Run limit`. Cells: `a` to the
project route with name, `small.help` slug; owner name or `—`, `small.help` email when present,
then a `button.link-button` `Change owner` that sets `ownerFor`; when `ownerFor === id` a
`form.owner-change` with `label.sr-only` `New owner's email`, input `type="email"` required
placeholder `Email of the new owner`, `button` submit `Make owner`, `button` `Cancel`, and
`p.help` `The current owner stays as a member.`; success resets `ownerFor`/`ownerEmail`;
`td.num` members, runs; `BudgetMeter` compact `budget`; `LimitField` `limit_usd` placeholder
`inheritedText(settings.default_project_limit_usd)` -> `updateAdminProject(id, { limit_usd
})`; `LimitField` `run_limit_usd` placeholder `inheritedText(
settings.default_run_limit_usd)` -> `updateAdminProject(id, { run_limit_usd })`.

`src/admin/AdminRuns.tsx` — query `['admin', 'runs']`; state `filter: 'all' | 'open' |
'blocked'`. `div.chips` with three `button.chip` (`aria-pressed`): `All`, `Open`, `Waiting on a
budget`. `p.help` `The latest 200 runs. A run paused by a budget continues after you raise the
limit and its owner resumes it.` Filtered empty -> `p` `No runs match.` Table columns `Run`,
`Project`, `Started by`, `Status`, `Started`, `Run spend`, `Run limit`. Cells: `a` run route with
`code` id and, when blocked, `p.row-note` the text; project name; `budget.started_by ?? '—'`;
`span` class `statusPillClass(status)` text status; `formatTime(started_at)`; `BudgetMeter`
compact `budget.run`; `LimitField` `limit_usd` placeholder `Inherited` -> `updateRunLimit`.

`src/admin/AdminDefaults.tsx` — query `['admin', 'settings']`; local text state for the three
limits (from `limitText`) and `signupOpen`, initialised when data arrives (effect on data).
`form.panel.defaults-form`: `h2` `Defaults`; `p.help` `Used when a person, project or run has
no limit of its own. Leave a field empty for no limit.`; three `div.field` each label, a
`span.limit-prefix` `$`, input text inputMode decimal (`aria-invalid` when `parseLimit` is
invalid), and `p.help`: `Monthly limit per person` / `Resets on the 1st of each month (UTC).`;
`Lifetime limit per project` / `Counts every run in the project.`; `Limit per run` / `A run
pauses when it reaches this.`; `div.field.check-field` checkbox `Anyone can create an
account` with `p.help` `When off, only existing accounts can sign in. The first account can
always be created.`; error alert; after success `p` role status `Defaults saved.`; `div.actions`
`button.primary` submit `Save defaults` disabled while pending or any limit invalid. Submit ->
`saveAdminSettings` with parsed values.

### 6. Page edits

- `src/ui/format.ts`: move `pillClass` from `ProjectPage.tsx` here as exported
  `statusPillClass(status: RunStatus)` (same body); `ProjectPage` imports it.
- `src/pages/ProjectPage.tsx`: new prop `me: AuthUser`; render `<MembersPanel projectId me
  navigate />` directly before the `Datasets` heading.
- `src/pages/RunPage.tsx`: new prop `isAdmin: boolean`; render `<BudgetBanner runId
  status={data.run.status} isAdmin />` directly after `RunHeader`.
- `src/App.tsx`:
  - Queries `['auth', 'config']` `getAuthConfig` and `['auth', 'me']` `getMe`; vocabulary
    query gets `enabled: me.isSuccess`.
  - State `signedOut` (false). Effect: `return onSignedOut(() => setSignedOut(true))`.
  - `signedIn()` = `setSignedOut(false); void queryClient.resetQueries();`.
    `afterSignOut()` = `setSignedOut(true); queryClient.removeQueries({ predicate: (query) =>
    query.queryKey[0] !== 'auth' })`.
  - `needsAuth = signedOut || (me.isError && me.error instanceof ApiError && me.error.status
    === 401)`.
  - Render order: config pending, or me pending while not `signedOut` -> `div.auth-loading`
    `p` `Loading…`; config error -> `div.page` alert `errorText`; `needsAuth` -> `<AuthPage
    config onSignedIn={signedIn} />`; me error -> `div.page` alert; else the current app. The
    top bar gets `<UserMenu user={me.data} onSignedOut={afterSignOut} />` after the help menu.
  - Route `admin`: non-admin -> `div.page` with `h1` `Admins only` and `p` `Ask an admin if
    you need access.`; admin -> `div.page.wide` `<AdminPage section meId={me.data.id} />`.
  - `ProjectPage` gets `me={me.data}`, `RunPage` gets `isAdmin={me.data.is_admin}`.

### 7. `src/style.css` (append; tokens only, no new colors)

- Extend both `input[type="text"], input[type="number"], select, textarea` selector lists
  (lines ~89 and ~348) with `input[type="email"], input[type="password"]`.
- `.page.wide` max-width 84rem. `.auth-loading` grid, place-items center, height 100dvh, color
  graphite.
- `.auth` grid, min-height 100dvh, columns `minmax(0, 5fr) minmax(0, 7fr)`. `.auth-brand`
  background navy, color navy-ink, padding 48px, flex column, justify-content flex-end, gap
  16px, background-image `repeating-linear-gradient(to bottom, transparent 0 31px,
  rgba(255,255,255,0.06) 31px 32px)`. `.brand-mark.large` 56px square. `.auth-name` margin 0,
  font-size 2.5rem, weight 700, letter-spacing -0.01em. `.auth-line` margin 0, max-width 28rem,
  color navy-muted, font-size 1.125rem, line-height 1.55. `.auth-panel` grid, place-items
  center, padding 32px 16px. `.auth-card` width `min(26rem, 100%)`, background surface, border
  1px rule, radius-lg, padding 32px, shadow. `.auth-card h1` font-size 1.5rem, margin 16px 0 4px.
  `.auth-card .field input` width 100%. `.auth-submit` width 100%, margin-top 8px. At max-width
  48rem: `.auth` single column; `.auth-brand` padding 24px 16px, justify-content flex-start;
  `.auth-name` 1.75rem.
- `.user-menu` position relative. `.user-menu > summary` list-style none, inline-flex, center,
  gap 8px, padding 4px 12px 4px 4px, border 1px `rgba(255,255,255,0.28)`, radius 999px, color
  navy-ink, cursor pointer; hide the webkit marker; hover and `[open]` background navy-2.
  `.avatar` inline-grid, place-items center, 28px square, radius 50%, background sky, color
  navy, weight 700, font-size 0.8125rem, flex none. `.avatar.small` 24px, background target,
  color signal. `.user-name` max-width 10rem, overflow hidden, ellipsis, nowrap; hidden at
  max-width 40rem. `.user-pop` width `min(18rem, calc(100vw - 32px))`. `.user-pop-name` margin
  0, weight 600. `.user-pop .button, .user-pop button` width 100%, margin-top 8px.
- Meter: `.meter` grid, gap 4px, min-width 9rem. `.meter-track` height 6px, radius 999px,
  background rule, overflow hidden. `.meter-fill` block, height 100%, background signal.
  `.meter-near .meter-fill` gate. `.meter-over .meter-fill` fault. `.meter-over .meter-text`
  color fault, weight 600. `.meter-none .meter-track` height 0, background transparent,
  border-top 2px dashed rule. `.meter-text` font-size 0.875rem, tabular-nums. `.meter.compact
  .meter-text` 0.8125rem. Inside `prefers-reduced-motion: no-preference`: `.meter-fill`
  transition width 400ms ease-out.
- Admin: `.admin-head` margin-bottom 16px. `.admin-body` grid, columns `12rem minmax(0, 1fr)`,
  gap 24px, align-items start. `.admin-nav` sticky, top 16px, flex column, gap 2px. `.admin-nav
  a` flex, center, gap 8px, padding 8px 12px, radius, color graphite, no underline, weight 600.
  Hover: background surface, color ink. `[aria-current="page"]`: background surface, color
  signal, box-shadow `inset 3px 0 0 var(--signal)`. At max-width 48rem: single column; nav row,
  overflow-x auto, static. `.admin-title` margin-top 0. `.figures` grid, columns
  `repeat(auto-fit, minmax(9rem, 1fr))`, margin 0 0 24px, border-top and border-bottom 1px
  rule; `.figures > div` padding 16px 16px 16px 0; `.figures dt` color graphite, 0.875rem;
  `.figures dd` margin 0, 1.75rem, weight 700, tabular-nums; `.figures dd.figure-note` 0.875rem,
  weight 400, color graphite. `.attention` list-style none, padding 0, margin 0.
  `.attention-row` flex, wrap, gap 12px, space-between, center, padding 12px 16px, border 1px
  gate, border-left-width 4px, radius, background gate-soft, margin-bottom 8px; `.attention-row
  p` margin 4px 0 0. `.defaults-summary` grid, columns `max-content 1fr`, gap 4px 16px;
  `.defaults-summary dt` color graphite; `.defaults-summary dd` margin 0.
- `.limit-field` inline-flex, center, gap 4px. `.limit-prefix` color graphite. `.limit-field
  input` width 6.5rem, padding 4px 8px, tabular-nums. `input[aria-invalid="true"]`
  border-color fault. `.limit-save` padding 4px 10px, 0.875rem.
- `.person` flex, center, gap 10px. `.row-actions` flex, wrap, gap 6px; its buttons padding
  4px 10px, 0.875rem. `.row-note` margin 4px 0 0, color fault, 0.875rem. `td.num` right
  aligned, tabular-nums. `.owner-change` flex, wrap, gap 6px, margin-top 6px. `.link-button`
  border 0, background none, color signal, padding 4px 8px. `.check-field label` inline-flex,
  center. `.defaults-form .field input[type="text"]` width 9rem.
- `.member-list` list-style none, padding 0, margin 0 0 8px; `.member-list li` flex, center,
  gap 10px, padding 8px 0, border-bottom 1px rule; `.member-who` flex column, flex 1,
  min-width 0.
- `.budget-banner` flex, gap 10px, align flex-start, padding 12px 14px, border 1px gate,
  border-left-width 4px, radius, background gate-soft, margin 12px 0; `.budget-banner p`
  margin 0 0 4px; `.budget-banner .icon` color gate, flex none, margin-top 2px.

### 8. Tests

- `package.json` `test` script: append `tests/budget.test.ts`.
- `tests/budget.test.ts`: `formatUsd` 0 -> `$0.00`, 1234.5 -> `$1,234.50`, 0.004 -> `<$0.01`;
  `meterTone` `{0.5, null}` none, `{1, 2}` ok, `{1.6, 2}` near, `{2, 2}` over, `{0, 0}` over;
  `meterFill` `{1, null}` 0, `{0, 0}` 1, `{0.5, 2}` 0.25, `{3, 2}` 1; `meterText` `{0.5, null}`
  `$0.50 spent, no limit`, `{0.5, 2}` `$0.50 of $2.00`; `parseLimit` `''` null, `' 2.5 '` 2.5,
  `'$3'` 3, `'1.239'` 1.24, `'$'` invalid, `'-1'` invalid, `'abc'` invalid, `'2000000'`
  invalid; `limitText` null `''`, 2 `'2.00'`; `inheritedText` null `No limit`, 5 `Default
  $5.00`; `monthLabel('2026-10')` `October 2026`, `monthLabel('bad')` `bad`; `initials('Ada
  Lovelace')` `AL`, `initials('cher')` `C`, `initials('  ')` `?`; `isOpenStatus` true for
  `paused`, false for `completed`.
- `tests/client.test.ts` (import the new names):
  1. In the fixed-message test add `[401, 'Sign in to continue.']`, `[402, 'A budget limit is
     reached. Ask an admin to raise it.']`, `[429, 'Too many failed sign-ins. Try again in 15
     minutes.']`; `bodyReads` stays 0.
  2. `createProject` test: also assert header `X-Statlab-Csrf` is `'1'`.
  3. New `onSignedOut fires on 401 only`: register a counting listener and a second one that
     is removed right away; fetch returning 401 -> `listProjects` rejects, count 1; fetch
     returning 404 -> count still 1; the removed listener never ran; remove the first one at
     the end.
  4. New `signIn posts credentials and maps 401`: with a recording fetch returning `{id: 'u_1',
     email: 'a@b.co', name: 'A', is_admin: false}` -> result deep-equals it, input
     `/api/auth/login`, method POST, body JSON `{email: 'a@b.co', password: 'pw-1234567'}`;
     then fetch returning 401 -> rejects with message `Email or password is incorrect.`.
  5. New `admin parsers`: `parseAdminOverview` of a full sample (one blocked run, status
     `paused`) deep-equals the input; changing that run's status to `boss` throws `Backend
     returned an invalid response.`; `parseMembers([{user_id: 'u', email: 'e', name: 'n',
     role: 'boss', added_at: 't'}])` throws; `parseRunBudget` sample deep-equals.
  6. New `admin writes use PATCH and PUT`: `updateAdminUser('u/1', { monthly_limit_usd: 2 })`
     -> input `/api/admin/users/u%2F1`, method PATCH, body `{"monthly_limit_usd":2}`
     (response: a valid AdminUser sample); `saveAdminSettings(sample)` -> method PUT, input
     `/api/admin/settings`.
- `tests/routes.test.ts`: round-trip list gains `{ name: 'admin', section }` for all five
  sections; `parseRoute('#/admin')` -> overview; `formatRoute({ name: 'admin', section:
  'overview' })` is `#/admin`; `#/admin/x` and `#/admin/people/x` -> notFound.

### Checks (implementer, from `frontend/`)

`npm test`, `npm run typecheck`, `npm run build`. Stop and report on any failure, a missing
export, or a needed file outside the owned list. Main then: review, browser check on scratch
servers (8766/8767/5174), `make check` only if Python changed (none expected).

### Addendum 71c-A (labels, loading/error texts, return types)

1. Meter labels: AdminProjects `` `Spend of ${name}` ``; AdminRuns `` `Spend of run ${id}` ``.
2. Every admin section: while its main query is pending render `p` `Loading…`; on its error
   render `p` role alert `errorText(error)` (AdminPeople and AdminProjects: the users/projects
   query; the settings query only feeds placeholders and shows nothing on error). Every
   mutation in AdminPeople, AdminProjects, AdminRuns, AdminOverview and AdminDefaults shows its
   error as `p` role alert `errorText(error)` directly above the table (or the form). One
   mutation per component may serve all its rows (pass the row id in the variables).
3. Return types confirmed: `addMember -> Member`, `changeProjectOwner -> AdminProject`,
   `updateRunLimit -> AdminRun`, `updateAdminUser -> AdminUser`, `updateAdminProject ->
   AdminProject`, `saveAdminSettings -> AdminSettings`.
4. MembersPanel sends `email.trim()`; AdminProjects sends `ownerEmail.trim()`.
5. `useId()` ids with `htmlFor` are correct everywhere a label needs an input id.

### Addendum 71c-B (limit field labels; RunPage prop)

LimitField labels: AdminProjects `` `Project limit for ${name}` `` and `` `Run limit for runs in
${name}` ``; AdminRuns `` `Run limit for ${id}` ``. RunPage passes `isAdmin={isAdmin}` (the
prop's value) to `BudgetBanner`; that is what the spec meant.

### Addendum 71c-C (browser review corrections)

Browser check on scratch servers found:
1. `BudgetBanner` also shows on finished runs ("then resume the run" is wrong there). Render it
   only when `isOpenStatus(status)` and `blocked` is a string.
2. AdminRuns filter `Waiting on a budget` keeps runs with `budget.blocked !== null &&
   isOpenStatus(status)` (same meaning as the overview list).
3. `MembersPanel`: the ` (you)` text goes inside the `strong` (it currently wraps onto its own
   line in the flex column).
4. `style.css`: `.members .inline-add .field` margin 0; `.members .inline-add input` width
   `min(20rem, 100%)` (the Add button sat lower than the input).
Checks: `npm test`, `npm run typecheck`, `npm run build`.

### Stage 71c receipt and chunk acceptance (2026-10-10)

Implementer (after addenda A-C): `npm test` 113/113, `npm run typecheck` 0, `npm run build` 0.
Main reviewed App gating (401 listener, reset on sign-in, removal of non-auth queries on sign
out), request CSRF header and fixed messages (error bodies still unread), and checked the UI
in a browser on scratch servers (8766/8767/5174, copy of `integration/out/20261010T080829Z-
offline`): sign-in page, admin overview, people, runs (a $0 run limit turns the meter red and
shows the reason), run budget banner, project People panel (owner added a member). Addendum C
fixed four review findings; re-checked the People panel. Screenshots `.playwright-mcp/71-*.png`.
Main then ran the full offline suite: exit 0, 19/19 PASS (`integration/out/20261010T082604Z-
offline`, one pre-existing soft warning in S12). No Python changed in 71c, so 71b's `make check`
stands. No live run (no paid LLM behaviour changed).
