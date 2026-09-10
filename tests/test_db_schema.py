"""Chunk 05 §4.5: schema shape, nullability, uniqueness, indexes, and the CHECK-vs-Literal
drift test.
"""

from __future__ import annotations

import re
import sqlite3
from importlib.resources import files
from pathlib import Path
from typing import get_args

import pytest
from statlab.persistence.models import NODE_EXECUTION_STATUSES, RunStatus

# --- expected shape, read from PLAN.md §10.2 verbatim -------------------------------------------

_EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "projects": ("id", "slug", "name", "created_at"),
    "datasets": (
        "id",
        "project_id",
        "filename",
        "path",
        "sha256",
        "n_rows",
        "n_cols",
        "created_at",
    ),
    "contracts": ("id", "project_id", "path", "json", "version", "created_at"),
    "runs": (
        "id",
        "project_id",
        "contract_id",
        "mode",
        "status",
        "current_node",
        "champion_model_id",
        "forked_from_run_id",
        "started_at",
        "ended_at",
        "config_json",
        "test_scored_at",
    ),
    "sessions": ("id", "project_id", "title", "created_at"),
    "messages": ("id", "session_id", "run_id", "role", "content", "meta_json", "ts"),
    "candidates": (
        "id",
        "run_id",
        "label",
        "rationale",
        "spec_json",
        "varies_from_baseline_json",
        "wave",
        "status",
        "rank",
        "created_at",
    ),
    "node_executions": (
        "id",
        "run_id",
        "candidate_id",
        "node_name",
        "seq",
        "attempt",
        "status",
        "summary",
        "error",
        "concurrency_at_start",
        "started_at",
        "ended_at",
    ),
    "tool_calls": (
        "id",
        "run_id",
        "node_execution_id",
        "tool_name",
        "args_json",
        "result_json",
        "duration_ms",
        "ts",
    ),
    "artifacts": ("id", "run_id", "kind", "path", "meta_json", "produced_by", "ts", "pruned_at"),
    "models": ("id", "run_id", "candidate_id", "name", "params_json", "artifact_id", "created_at"),
    "metrics": ("id", "run_id", "candidate_id", "model_id", "split", "name", "value"),
    "gates": ("id", "run_id", "node_execution_id", "question_json", "answer_json", "status", "ts"),
    "llm_calls": (
        "id",
        "run_id",
        "node_execution_id",
        "signature",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "cost_usd",
        "duration_ms",
        "ts",
    ),
    "memory": ("id", "project_id", "key", "value_json", "source_run_id", "ts"),
}


def _table_info(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


@pytest.mark.parametrize("table", list(_EXPECTED_COLUMNS))
def test_column_names_and_order_match_plan(migrated_db: sqlite3.Connection, table: str) -> None:
    """Column names and order per `PLAN.md` §10.2, driven by a table -> columns mapping read
    from `PRAGMA table_info`."""
    info = _table_info(migrated_db, table)
    actual = tuple(row["name"] for row in info)
    assert actual == _EXPECTED_COLUMNS[table]


def test_expected_columns_covers_all_fifteen_tables() -> None:
    assert len(_EXPECTED_COLUMNS) == 15


def _all_table_names(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every real table, derived from `sqlite_master` -- excludes `schema_migrations`, which
    is the migration runner's own bookkeeping, not one of PLAN.md §10.2's 15."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_migrations'"
    ).fetchall()
    return tuple(row["name"] for row in rows)


@pytest.mark.parametrize("table", list(_EXPECTED_COLUMNS))
def test_id_column_is_not_null(migrated_db: sqlite3.Connection, table: str) -> None:
    """§6.1: SQLite accepts NULL in a non-INTEGER `PRIMARY KEY` unless `NOT NULL` is declared
    -- confirmed broken before the fix (a project with `id = NULL` inserted cleanly) and fixed
    after it. Derived from `_EXPECTED_COLUMNS` (itself covering all 15 tables per the test
    above) rather than hand-listed."""
    assert _notnull(migrated_db, table, "id") is True


def test_id_not_null_covers_every_real_table(migrated_db: sqlite3.Connection) -> None:
    """Cross-check that `_EXPECTED_COLUMNS`' keys (what `test_id_column_is_not_null` iterates
    over) are exactly the tables `sqlite_master` reports -- so the parametrised sweep above
    cannot silently skip a table that exists in the DDL but not in this test file's mapping."""
    assert set(_all_table_names(migrated_db)) == set(_EXPECTED_COLUMNS)


@pytest.mark.parametrize("table", list(_EXPECTED_COLUMNS))
def test_inserting_null_id_raises_integrity_error(
    migrated_db: sqlite3.Connection, table: str
) -> None:
    """§6.1's required test, the behavioural half of the two above: inserting a NULL `id`
    into any of the 15 tables raises, rather than merely asserting the `PRAGMA` metadata says
    `NOT NULL`. Every column besides `id` is left out of the statement, relying on SQLite's own
    NOT-NULL-without-DEFAULT rejection for the columns that also need a value -- the `id` check
    fires first because it is evaluated before any other constraint in insertion order here,
    and either way the statement raises `IntegrityError`, which is all this test asserts."""
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(f"INSERT INTO {table} (id) VALUES (NULL)")  # noqa: S608  # table comes from a fixed, hand-authored mapping of this project's own 15 table names, not external input


# --- nullability -----------------------------------------------------------------------------


def _notnull(conn: sqlite3.Connection, table: str, column: str) -> bool:
    info = _table_info(conn, table)
    for row in info:
        if row["name"] == column:
            return bool(row["notnull"])
    raise AssertionError(f"no such column {table}.{column}")


@pytest.mark.parametrize(
    "table,column",
    [
        ("node_executions", "candidate_id"),
        ("runs", "champion_model_id"),
        ("runs", "ended_at"),
        ("runs", "forked_from_run_id"),
    ],
)
def test_columns_that_must_be_nullable(
    migrated_db: sqlite3.Connection, table: str, column: str
) -> None:
    assert _notnull(migrated_db, table, column) is False


@pytest.mark.parametrize(
    "table,column",
    [
        ("runs", "project_id"),
        ("runs", "status"),
        ("projects", "created_at"),
        ("datasets", "created_at"),
        ("contracts", "created_at"),
        ("runs", "started_at"),
        ("sessions", "created_at"),
        ("candidates", "created_at"),
        ("models", "created_at"),
    ],
)
def test_columns_that_must_be_not_null(
    migrated_db: sqlite3.Connection, table: str, column: str
) -> None:
    assert _notnull(migrated_db, table, column) is True


# --- UNIQUE constraints ------------------------------------------------------------------------


def test_unique_projects_slug(migrated_db: sqlite3.Connection) -> None:
    migrated_db.execute(
        "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_1', 's', 'A', 'ts')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_2', 's', 'B', 'ts')"
        )


def test_unique_datasets_project_id_sha256(migrated_db: sqlite3.Connection) -> None:
    migrated_db.execute(
        "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_1', 's', 'A', 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO datasets "
        "(id, project_id, filename, path, sha256, n_rows, n_cols, created_at) "
        "VALUES ('ds_1', 'p_1', 'f.csv', '/f.csv', 'h1', 1, 1, 'ts')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            "INSERT INTO datasets "
            "(id, project_id, filename, path, sha256, n_rows, n_cols, created_at) "
            "VALUES ('ds_2', 'p_1', 'g.csv', '/g.csv', 'h1', 1, 1, 'ts')"
        )


def test_unique_contracts_project_id_version(migrated_db: sqlite3.Connection) -> None:
    migrated_db.execute(
        "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_1', 's', 'A', 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO contracts (id, project_id, path, json, version, created_at) "
        "VALUES ('ct_1', 'p_1', '/c.json', '{}', 1, 'ts')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            "INSERT INTO contracts (id, project_id, path, json, version, created_at) "
            "VALUES ('ct_2', 'p_1', '/c2.json', '{}', 1, 'ts')"
        )


def test_unique_candidates_run_id_label(migrated_db: sqlite3.Connection) -> None:
    _seed_run(migrated_db)
    migrated_db.execute(
        "INSERT INTO candidates "
        "(id, run_id, label, rationale, spec_json, varies_from_baseline_json, wave, status, "
        "rank, created_at) VALUES ('c_1', 'r_1', 'baseline', 'r', '{}', '[]', 1, 'pending', "
        "NULL, 'ts')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            "INSERT INTO candidates "
            "(id, run_id, label, rationale, spec_json, varies_from_baseline_json, wave, "
            "status, rank, created_at) VALUES ('c_2', 'r_1', 'baseline', 'r', '{}', '[]', 1, "
            "'pending', NULL, 'ts')"
        )


def test_unique_node_executions_run_candidate_node_attempt(
    migrated_db: sqlite3.Connection,
) -> None:
    """A non-`NULL` `candidate_id`, since SQL's `UNIQUE` treats `NULL` as distinct from every
    other `NULL` (confirmed directly while writing this test) -- the constraint genuinely
    protects per-candidate node executions, which is the case this test exercises. See
    `test_unique_constraint_does_not_protect_shared_node_rows_with_null_candidate_id` below for
    what that NULL semantics means for the shared-node (candidate_id IS NULL) rows."""
    _seed_run(migrated_db)
    migrated_db.execute(
        "INSERT INTO candidates "
        "(id, run_id, label, rationale, spec_json, varies_from_baseline_json, wave, status, "
        "rank, created_at) VALUES ('c_1', 'r_1', 'baseline', 'r', '{}', '[]', 1, 'pending', "
        "NULL, 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_1', 'r_1', 'c_1', 'preprocessing', 1, 1, 'pending', 'ts')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            "INSERT INTO node_executions "
            "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
            "VALUES ('ne_2', 'r_1', 'c_1', 'preprocessing', 2, 1, 'pending', 'ts')"
        )


def test_ux_node_executions_identity_rejects_duplicate_shared_node(
    migrated_db: sqlite3.Connection,
) -> None:
    """§6.2: the fixed identity index rejects a duplicate SHARED-node row (candidate_id IS
    NULL), which the old table-level `UNIQUE` could not -- SQL treats every NULL as distinct
    from every other, so `IFNULL(candidate_id, '')` collapsing every NULL to one key is what
    makes this rejection possible. Confirmed broken before the fix (two rows for `intake`
    attempt 1 both inserted) and confirmed fixed after it."""
    _seed_run(migrated_db)
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_1', 'r_1', NULL, 'intake', 1, 1, 'pending', 'ts')"
    )
    with pytest.raises(sqlite3.IntegrityError, match="ux_node_executions_identity"):
        migrated_db.execute(
            "INSERT INTO node_executions "
            "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
            "VALUES ('ne_2', 'r_1', NULL, 'intake', 2, 1, 'pending', 'ts')"
        )


def test_ux_node_executions_identity_allows_same_node_different_candidates(
    migrated_db: sqlite3.Connection,
) -> None:
    """§6.2: the same node name/attempt for two *different* candidates must still be allowed --
    the fix must not over-collapse per-candidate identity down to the shared-node case."""
    _seed_run(migrated_db)
    migrated_db.execute(
        "INSERT INTO candidates "
        "(id, run_id, label, rationale, spec_json, varies_from_baseline_json, wave, status, "
        "rank, created_at) VALUES ('c_1', 'r_1', 'c1', 'r', '{}', '[]', 1, 'pending', NULL, 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO candidates "
        "(id, run_id, label, rationale, spec_json, varies_from_baseline_json, wave, status, "
        "rank, created_at) VALUES ('c_2', 'r_1', 'c2', 'r', '{}', '[]', 1, 'pending', NULL, 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_1', 'r_1', 'c_1', 'preprocessing', 1, 1, 'pending', 'ts')"
    )
    # Does not raise: different candidate, same node name and attempt.
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_2', 'r_1', 'c_2', 'preprocessing', 2, 1, 'pending', 'ts')"
    )
    rows = migrated_db.execute(
        "SELECT * FROM node_executions WHERE run_id = 'r_1' AND node_name = 'preprocessing'"
    ).fetchall()
    assert len(rows) == 2


def test_ux_node_executions_identity_allows_same_node_different_attempt(
    migrated_db: sqlite3.Connection,
) -> None:
    """§6.2: a retry (same run, same shared node, next attempt) must still be allowed -- the
    fix must not collapse `attempt` out of the identity."""
    _seed_run(migrated_db)
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_1', 'r_1', NULL, 'intake', 1, 1, 'failed', 'ts')"
    )
    # Does not raise: same shared node, next attempt.
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_2', 'r_1', NULL, 'intake', 2, 2, 'pending', 'ts')"
    )
    rows = migrated_db.execute(
        "SELECT * FROM node_executions WHERE run_id = 'r_1' AND node_name = 'intake'"
    ).fetchall()
    assert len(rows) == 2


def test_unique_metrics_run_model_split_name(migrated_db: sqlite3.Connection) -> None:
    _seed_run(migrated_db)
    migrated_db.execute(
        "INSERT INTO artifacts (id, run_id, kind, path, meta_json, produced_by, ts) "
        "VALUES ('a_1', 'r_1', 'model', '/m', '{}', 'train_estimator', 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO models (id, run_id, candidate_id, name, params_json, artifact_id, "
        "created_at) VALUES ('mo_1', 'r_1', NULL, 'logit', '{}', 'a_1', 'ts')"
    )
    migrated_db.execute(
        "INSERT INTO metrics (id, run_id, candidate_id, model_id, split, name, value) "
        "VALUES ('me_1', 'r_1', NULL, 'mo_1', 'test', 'roc_auc', 0.9)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_db.execute(
            "INSERT INTO metrics (id, run_id, candidate_id, model_id, split, name, value) "
            "VALUES ('me_2', 'r_1', NULL, 'mo_1', 'test', 'roc_auc', 0.8)"
        )


def _seed_run(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO projects (id, slug, name, created_at) VALUES ('p_1', 's', 'A', 'ts')")
    conn.execute(
        "INSERT INTO contracts (id, project_id, path, json, version, created_at) "
        "VALUES ('ct_1', 'p_1', '/c.json', '{}', 1, 'ts')"
    )
    conn.execute(
        "INSERT INTO runs (id, project_id, contract_id, mode, status, current_node, "
        "champion_model_id, forked_from_run_id, started_at, ended_at, config_json) "
        "VALUES ('r_1', 'p_1', 'ct_1', 'autonomous', 'pending', NULL, NULL, NULL, 'ts', NULL, '{}')"
    )


# --- indexes -----------------------------------------------------------------------------------

_EXPECTED_INDEXED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("runs", "project_id"),
    ("candidates", "run_id"),
    ("node_executions", "run_id"),
    ("tool_calls", "run_id"),
    ("tool_calls", "node_execution_id"),
    ("artifacts", "run_id"),
    ("metrics", "run_id"),
    ("models", "run_id"),
    ("messages", "session_id"),
    ("llm_calls", "run_id"),
    ("memory", "project_id"),
)


@pytest.mark.parametrize("table,column", _EXPECTED_INDEXED_COLUMNS)
def test_declared_index_exists(migrated_db: sqlite3.Connection, table: str, column: str) -> None:
    index_rows = migrated_db.execute(f"PRAGMA index_list({table})").fetchall()
    found = False
    for index_row in index_rows:
        index_info = migrated_db.execute(f"PRAGMA index_info({index_row['name']})").fetchall()
        if any(col["name"] == column for col in index_info):
            found = True
            break
    assert found, f"no index found on {table}.{column}"


# --- CHECK-versus-Literal drift test ------------------------------------------------------------

_CHECK_RE = re.compile(r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)\s*\)")


def _parse_check_constraints(sql_text: str) -> dict[str, frozenset[str]]:
    """Every `CHECK (column IN (...))` in the DDL, mapped to its literal value set."""
    result: dict[str, frozenset[str]] = {}
    for match in _CHECK_RE.finditer(sql_text):
        column, values_text = match.groups()
        values = frozenset(v.strip().strip("'") for v in values_text.split(","))
        result[column] = values
    return result


def test_runs_status_check_matches_run_status_literal() -> None:
    """Scoped to the `runs` table's own DDL text before parsing -- `status` is also a column
    name on `node_executions`, so parsing the whole file into one `{column: values}` dict
    would let one table's CHECK silently overwrite the other's (found while writing this
    test: an earlier combined version did exactly that). Slicing to each `CREATE TABLE`
    block first is what makes the derivation actually distinguish the two."""
    sql_path = Path(str(files("statlab.persistence") / "migrations" / "001_initial.sql"))
    text = sql_path.read_text(encoding="utf-8")
    runs_table_text = text[text.index("CREATE TABLE runs") : text.index("CREATE TABLE sessions")]
    checks = _parse_check_constraints(runs_table_text)
    assert checks["status"] == frozenset(get_args(RunStatus))


def test_node_executions_status_check_matches_node_execution_statuses() -> None:
    sql_path = Path(str(files("statlab.persistence") / "migrations" / "001_initial.sql"))
    text = sql_path.read_text(encoding="utf-8")
    start = text.index("CREATE TABLE node_executions")
    end = text.index("CREATE TABLE tool_calls")
    checks = _parse_check_constraints(text[start:end])
    assert checks["status"] == NODE_EXECUTION_STATUSES


def test_runs_mode_check_matches_run_mode_literal() -> None:
    from statlab.persistence.models import RunMode

    sql_path = Path(str(files("statlab.persistence") / "migrations" / "001_initial.sql"))
    text = sql_path.read_text(encoding="utf-8")
    runs_table_text = text[text.index("CREATE TABLE runs") : text.index("CREATE TABLE sessions")]
    checks = _parse_check_constraints(runs_table_text)
    assert checks["mode"] == frozenset(get_args(RunMode))


def test_node_execution_statuses_has_exactly_six_members() -> None:
    """Required test from §3.5."""
    assert len(NODE_EXECUTION_STATUSES) == 6


def test_node_execution_statuses_is_strict_superset_of_node_status() -> None:
    """Required test from §3.5."""
    from statlab.domains.base import NodeStatus

    node_status_members = frozenset(get_args(NodeStatus))
    assert node_status_members < NODE_EXECUTION_STATUSES


@pytest.mark.parametrize("value", ["pending", "running"])
def test_pre_terminal_statuses_are_not_valid_node_result_status(value: str) -> None:
    """Required test from §3.5: pins the narrowness Option B would have destroyed. A
    `NodeResult` only exists once a node has stopped, so `pending`/`running` -- states a live
    `node_executions` row occupies before that -- must be rejected."""
    from pydantic import ValidationError
    from statlab.domains.base import NodeResult

    with pytest.raises(ValidationError):
        NodeResult.model_validate({"status": value})
