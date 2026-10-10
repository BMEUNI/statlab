---
status: in_progress
dependencies: [70]
completion_date: null
implementation_notes: "71a accounts, sessions, access guard and members (backend); 71b budgets and admin API; 71c frontend; 71d harness and docs"
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
