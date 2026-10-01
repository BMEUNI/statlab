-- Chunk 05: initial schema, all 15 tables from PLAN.md §10.2, verbatim in column names and
-- order. Repositories exist here for only four of them (projects, datasets, contracts, runs);
-- the rest are declarative and nearly free, and get repositories in the chunk that first writes
-- to them (§1's "What this chunk does NOT do").
--
-- Status CHECK constraints duplicate the Literal vocabularies in backend/statlab/persistence/
-- models.py in SQL text on purpose -- the drift test in tests/test_db_schema.py polices that
-- duplication so it cannot silently rot.
--
-- Edited in place per §6 (no 002 migration -- nothing has ever run this schema, so a migration
-- correcting it would be archaeology, not history):
--   §6.1 every `id TEXT PRIMARY KEY` now carries `NOT NULL`. SQLite accepts NULL in a
--        non-INTEGER PRIMARY KEY unless NOT NULL is declared -- the same class of defect as an
--        unset `foreign_keys` pragma, a constraint that reads as present and enforces nothing.
--   §6.2 `node_executions`' identity constraint is now a unique expression index over
--        `IFNULL(candidate_id, '')` rather than a table-level UNIQUE, because SQL treats every
--        NULL as distinct from every other NULL -- the table-level UNIQUE protected only the
--        five per-candidate nodes and did nothing for all eight SHARED nodes (1-6, 12-13).
--   §6.3 `runs.test_scored_at` added: the durable half of D26's test-set-scored-exactly-once
--        guarantee (the other half, "unreadable before node 12", is already `champion_model_id
--        IS NULL` and needed no new column).
--   chunk 06: `artifacts.pruned_at TEXT` (nullable) added. Pruning sets it rather than deleting
--        the row: deleting would break `tool_calls`' reference to the artifact id, and leaving
--        a row whose `path` points at a missing file with no explanation makes deliberate
--        pruning indistinguishable from data loss. Same reasoning as `test_scored_at`.
--   R4   schema and wire fields for the D32 test-set lineage lock, also edited in place:
--        `runs.status` CHECK gains 'superseded' (the status a rewind marks the parent run with,
--        D17/§5); `runs.test_fingerprint TEXT` (nullable, the test set a run scored, for
--        counting cross-run exposures); `artifacts.kind` gains a CHECK over every ArtifactKind
--        member (previously unconstrained TEXT, unlike every status column, policed by the same
--        drift test); `artifacts.split TEXT` (nullable, CHECK over SplitRole) and
--        `artifacts.derived_from_test INTEGER NOT NULL DEFAULT 0 CHECK (... IN (0, 1))`.

CREATE TABLE projects (
    id         TEXT PRIMARY KEY NOT NULL,
    slug       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE datasets (
    id         TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    path       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    n_rows     INTEGER NOT NULL,
    n_cols     INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, sha256)
);

CREATE INDEX idx_datasets_project_id ON datasets(project_id);

CREATE TABLE contracts (
    id         TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    json       TEXT NOT NULL,
    version    INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (project_id, version)
);

CREATE TABLE runs (
    id                 TEXT PRIMARY KEY NOT NULL,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    contract_id        TEXT NOT NULL REFERENCES contracts(id),
    mode               TEXT NOT NULL CHECK (mode IN ('guided', 'autonomous')),
    status             TEXT NOT NULL CHECK (
        status IN (
            'pending', 'running', 'awaiting_user', 'paused', 'failed', 'cancelled',
            'completed', 'superseded'
        )
    ),
    current_node       TEXT,
    champion_model_id  TEXT REFERENCES models(id),
    forked_from_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    config_json        TEXT NOT NULL,
    test_scored_at     TEXT,
    test_fingerprint   TEXT
);

CREATE INDEX idx_runs_project_id ON runs(project_id);

CREATE TABLE sessions (
    id         TEXT PRIMARY KEY NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE messages (
    id         TEXT PRIMARY KEY NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    meta_json  TEXT NOT NULL,
    ts         TEXT NOT NULL
);

CREATE INDEX idx_messages_session_id ON messages(session_id);

CREATE TABLE candidates (
    id                         TEXT PRIMARY KEY NOT NULL,
    run_id                     TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    label                      TEXT NOT NULL,
    rationale                  TEXT NOT NULL,
    spec_json                  TEXT NOT NULL,
    varies_from_baseline_json  TEXT NOT NULL,
    wave                       INTEGER NOT NULL,
    status                     TEXT NOT NULL,
    rank                       INTEGER,
    created_at                 TEXT NOT NULL,
    UNIQUE (run_id, label)
);

CREATE INDEX idx_candidates_run_id ON candidates(run_id);

CREATE TABLE node_executions (
    id                    TEXT PRIMARY KEY NOT NULL,
    run_id                TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    candidate_id          TEXT REFERENCES candidates(id) ON DELETE CASCADE,
    node_name             TEXT NOT NULL,
    seq                   INTEGER NOT NULL,
    attempt               INTEGER NOT NULL,
    status                TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'awaiting_user', 'skipped', 'failed', 'completed')
    ),
    summary               TEXT,
    error                 TEXT,
    concurrency_at_start  INTEGER,
    started_at            TEXT NOT NULL,
    ended_at              TEXT
);

CREATE INDEX idx_node_executions_run_id ON node_executions(run_id);

-- §6.2: a table-level UNIQUE(run_id, candidate_id, node_name, attempt) is decorative for every
-- SHARED node (candidate_id IS NULL): SQL treats each NULL as distinct from every other NULL,
-- so two rows for the same shared node/attempt would both insert cleanly. IFNULL(candidate_id,
-- '') collapses every NULL to the same empty-string key, so the identity this index enforces is
-- "same run, same effective candidate (or none), same node, same attempt" for all thirteen
-- nodes, not just the five per-candidate ones.
CREATE UNIQUE INDEX ux_node_executions_identity
    ON node_executions(run_id, IFNULL(candidate_id, ''), node_name, attempt);

CREATE TABLE tool_calls (
    id                 TEXT PRIMARY KEY NOT NULL,
    run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_execution_id  TEXT NOT NULL REFERENCES node_executions(id) ON DELETE CASCADE,
    tool_name          TEXT NOT NULL,
    args_json          TEXT NOT NULL,
    result_json        TEXT NOT NULL,
    duration_ms        INTEGER NOT NULL,
    ts                 TEXT NOT NULL
);

CREATE INDEX idx_tool_calls_run_id ON tool_calls(run_id);
CREATE INDEX idx_tool_calls_node_execution_id ON tool_calls(node_execution_id);

CREATE TABLE artifacts (
    id                TEXT PRIMARY KEY NOT NULL,
    run_id            TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind              TEXT NOT NULL CHECK (
        kind IN (
            'dataframe', 'predictions', 'transformer', 'model', 'inferential_fit',
            'curve', 'table', 'plot', 'report'
        )
    ),
    path              TEXT NOT NULL,
    meta_json         TEXT NOT NULL,
    produced_by       TEXT NOT NULL,
    ts                TEXT NOT NULL,
    pruned_at         TEXT,
    split             TEXT CHECK (split IN ('train', 'valid', 'test')),
    derived_from_test INTEGER NOT NULL DEFAULT 0 CHECK (derived_from_test IN (0, 1))
);

CREATE INDEX idx_artifacts_run_id ON artifacts(run_id);

CREATE TABLE models (
    id          TEXT PRIMARY KEY NOT NULL,
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES candidates(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    params_json TEXT NOT NULL,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_models_run_id ON models(run_id);

CREATE TABLE metrics (
    id            TEXT PRIMARY KEY NOT NULL,
    run_id        TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    candidate_id  TEXT REFERENCES candidates(id) ON DELETE CASCADE,
    model_id      TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    split         TEXT NOT NULL,
    name          TEXT NOT NULL,
    value         REAL NOT NULL,
    UNIQUE (run_id, model_id, split, name)
);

CREATE INDEX idx_metrics_run_id ON metrics(run_id);

CREATE TABLE gates (
    id                 TEXT PRIMARY KEY NOT NULL,
    run_id             TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_execution_id  TEXT NOT NULL REFERENCES node_executions(id) ON DELETE CASCADE,
    question_json      TEXT NOT NULL,
    answer_json        TEXT,
    status             TEXT NOT NULL,
    ts                 TEXT NOT NULL
);

CREATE TABLE llm_calls (
    id                  TEXT PRIMARY KEY NOT NULL,
    run_id              TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_execution_id   TEXT NOT NULL REFERENCES node_executions(id) ON DELETE CASCADE,
    signature           TEXT NOT NULL,
    model               TEXT NOT NULL,
    prompt_tokens       INTEGER NOT NULL,
    completion_tokens   INTEGER NOT NULL,
    cached_tokens       INTEGER NOT NULL,
    cost_usd            REAL NOT NULL,
    duration_ms         INTEGER NOT NULL,
    ts                  TEXT NOT NULL
);

CREATE INDEX idx_llm_calls_run_id ON llm_calls(run_id);

CREATE TABLE memory (
    id             TEXT PRIMARY KEY NOT NULL,
    project_id     TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key            TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    source_run_id  TEXT REFERENCES runs(id),
    ts             TEXT NOT NULL
);

CREATE INDEX idx_memory_project_id ON memory(project_id);
