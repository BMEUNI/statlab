-- Chunk 71: accounts, sign-in sessions, project membership and budgets. New tables only;
-- projects and runs are not altered because their row models read SELECT * with extra=forbid.

CREATE TABLE users (
    id                TEXT PRIMARY KEY NOT NULL,
    email             TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    password_hash     TEXT NOT NULL,
    is_admin          INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    disabled          INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0, 1)),
    monthly_limit_usd REAL CHECK (monthly_limit_usd IS NULL OR monthly_limit_usd >= 0),
    created_at        TEXT NOT NULL,
    last_login_at     TEXT
);

-- id is the SHA-256 hex of the cookie token; the token itself is never stored.
CREATE TABLE auth_sessions (
    id         TEXT PRIMARY KEY NOT NULL,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX idx_auth_sessions_user_id ON auth_sessions(user_id);

CREATE TABLE project_members (
    id         TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    added_at   TEXT NOT NULL,
    UNIQUE (project_id, user_id)
);
CREATE INDEX idx_project_members_user_id ON project_members(user_id);

-- id is the project's id.
CREATE TABLE project_budgets (
    id            TEXT PRIMARY KEY NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    limit_usd     REAL CHECK (limit_usd IS NULL OR limit_usd >= 0),
    run_limit_usd REAL CHECK (run_limit_usd IS NULL OR run_limit_usd >= 0)
);

-- id is the run's id; started_by is the person the run's spend counts against.
CREATE TABLE run_budgets (
    id         TEXT PRIMARY KEY NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    started_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    limit_usd  REAL CHECK (limit_usd IS NULL OR limit_usd >= 0)
);
CREATE INDEX idx_run_budgets_started_by ON run_budgets(started_by);

-- id is the setting key.
CREATE TABLE app_settings (
    id         TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL
);
