"""Migration runner tests: chunk 05 §4.3, M1-M15."""

from __future__ import annotations

import re
import sqlite3
from importlib.resources import files
from pathlib import Path

import pytest
from statlab.errors import MigrationError
from statlab.persistence.db import migrate
from statlab.persistence.ids import parse_ts

# --- M1-M4: basic apply / idempotence -----------------------------------------------------------


def test_m1_migrate_on_empty_db_returns_one(conn: sqlite3.Connection) -> None:
    """M1."""
    assert migrate(conn) == (1, 2)


def test_m2_all_fifteen_tables_exist(migrated_db: sqlite3.Connection) -> None:
    """M2: query `sqlite_master`, compare against a set derived from the SQL files, not
    hand-typed."""
    declared: set[str] = set()
    for filename in ("001_initial.sql", "002_accounts.sql"):
        sql_path = Path(str(files("statlab.persistence") / "migrations" / filename))
        sql_text = sql_path.read_text(encoding="utf-8")
        declared |= {
            name.split("(")[0].strip() for name in _extract_after(sql_text, "CREATE TABLE ")
        }
    actual = {
        row["name"]
        for row in migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_migrations'"
        ).fetchall()
    }
    assert actual == declared
    assert len(declared) == 21


def _extract_after(text: str, marker: str) -> list[str]:
    """Every table name immediately following `marker` in the DDL text, one per occurrence."""
    names = []
    idx = 0
    while True:
        pos = text.find(marker, idx)
        if pos == -1:
            break
        start = pos + len(marker)
        end = text.index("(", start)
        names.append(text[start:end].strip())
        idx = end
    return names


def test_m3_schema_migrations_has_one_parseable_row(migrated_db: sqlite3.Connection) -> None:
    """M3."""
    rows = migrated_db.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    assert len(rows) == 2
    assert rows[0]["version"] == 1
    assert rows[0]["name"] == "initial"
    parse_ts(rows[0]["applied_at"])  # raises PersistenceError if not parseable
    assert rows[1]["version"] == 2
    assert rows[1]["name"] == "accounts"
    parse_ts(rows[1]["applied_at"])


def test_m4_second_migrate_returns_empty(migrated_db: sqlite3.Connection) -> None:
    """M4: idempotence, the property that makes startup safe."""
    assert migrate(migrated_db) == ()


# --- M5-M10: ordering, failure atomicity, error taxonomy ----------------------------------------


def _write_migration(directory: Path, filename: str, sql: str) -> None:
    (directory / filename).write_text(sql, encoding="utf-8")


def test_m5_integer_ordering_not_string_ordering(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M5: synthetic `009`/`010` fixtures apply in numeric order."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "009_a.sql", "CREATE TABLE t_a (id INTEGER PRIMARY KEY);")
    _write_migration(migrations_dir, "010_b.sql", "CREATE TABLE t_b (id INTEGER PRIMARY KEY);")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    versions = migrate(conn)
    assert versions == (9, 10)


def test_m6_failing_migration_leaves_no_partial_schema(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M6: a valid statement then a syntax error; the first statement's table is absent and
    `schema_migrations` is empty."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(
        migrations_dir,
        "001_bad.sql",
        "CREATE TABLE t_ok (id INTEGER PRIMARY KEY);\nTHIS IS NOT VALID SQL AT ALL;",
    )

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    with pytest.raises(MigrationError):
        migrate(conn)

    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 't_ok'"
    ).fetchone()
    assert table_exists is None
    applied_rows = conn.execute("SELECT * FROM schema_migrations").fetchall()
    assert applied_rows == []


def test_m7_migration_error_names_file_and_version(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M7."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "001_bad.sql", "NOT VALID SQL;")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    with pytest.raises(MigrationError) as exc_info:
        migrate(conn)
    assert exc_info.value.version == 1
    assert exc_info.value.name == "bad"
    assert "001_bad.sql" in str(exc_info.value)


def test_m8_duplicate_version_numbers_raise(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M8."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "001_a.sql", "CREATE TABLE t_a (id INTEGER PRIMARY KEY);")
    _write_migration(migrations_dir, "001_b.sql", "CREATE TABLE t_b (id INTEGER PRIMARY KEY);")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    with pytest.raises(MigrationError, match="duplicate migration version"):
        migrate(conn)


def test_m9_malformed_filename_names_the_file(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M9."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "not_a_valid_name.sql", "SELECT 1;")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    with pytest.raises(MigrationError, match=re.escape("not_a_valid_name.sql")):
        migrate(conn)


def test_m10_recorded_version_absent_on_disk_mentions_downgrade(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M10."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "001_a.sql", "CREATE TABLE t_a (id INTEGER PRIMARY KEY);")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    migrate(conn)

    # Now point at a directory with no migrations at all -- version 1 is "on disk" as far as
    # the database recorded, but absent from what's discoverable now.
    empty_dir = tmp_path / "empty_migrations"
    empty_dir.mkdir()

    def fake_files_empty(package: str) -> Path:
        return empty_dir

    monkeypatch.setattr("statlab.persistence.db.files", fake_files_empty)
    with pytest.raises(MigrationError, match="downgrade"):
        migrate(conn)


# --- M11-M13: resource discovery, DDL validity, FK enforceability --------------------------------


# --- _split_statements: direct unit tests for its two internal branches -------------------------


def test_split_statements_skips_blank_segments_between_statements() -> None:
    """`_split_statements` accumulates lines until `sqlite3.complete_statement` is satisfied;
    a blank line right after a completed statement produces an empty `buffer.strip()` that
    must be skipped rather than appended as a spurious empty statement."""
    from statlab.persistence.db import _split_statements

    sql = "CREATE TABLE a (id INTEGER);\n\n\nCREATE TABLE b (id INTEGER);\n"
    statements = _split_statements(sql)
    assert statements == (
        "CREATE TABLE a (id INTEGER);",
        "CREATE TABLE b (id INTEGER);",
    )


def test_split_statements_captures_trailing_incomplete_statement() -> None:
    """A migration file missing its final semicolon still has that trailing text captured
    (so SQLite's own `execute()` raises a clear syntax error on it), rather than silently
    dropped by the splitter."""
    from statlab.persistence.db import _split_statements

    sql = "CREATE TABLE a (id INTEGER);\nCREATE TABLE b (id INTEGER)"  # no trailing ;
    statements = _split_statements(sql)
    assert statements == (
        "CREATE TABLE a (id INTEGER);",
        "CREATE TABLE b (id INTEGER)",
    )


def test_m11_migrations_load_via_importlib_resources() -> None:
    """M11: assert the discovered path resolves inside the package."""
    directory = Path(str(files("statlab.persistence") / "migrations"))
    assert directory.is_dir()
    assert (directory / "001_initial.sql").exists()
    assert "persistence" in directory.parts
    assert "migrations" in directory.parts


def test_m12_initial_sql_is_valid(conn: sqlite3.Connection) -> None:
    """M12: applies with no error."""
    versions = migrate(conn)
    assert versions == (1, 2)


def test_m13_every_fk_is_enforceable(migrated_db: sqlite3.Connection) -> None:
    """M13: for each `REFERENCES`, insert a child with a bad parent and assert
    `IntegrityError`. Derive the pairs from `PRAGMA foreign_key_list`, do not hand-list them."""
    # A handful of columns carry a CHECK (... IN (...)) constraint in addition to NOT NULL;
    # generic filler ("x" / 1) would trip the CHECK before the FK ever gets evaluated, giving
    # a false negative for this test's purpose. These are exactly the columns §3.5's Literal
    # vocabularies back -- one concrete, valid member each, so the row fails on the FK we are
    # actually testing rather than an unrelated constraint.
    known_valid_values = {
        "mode": "autonomous",
        "status": "pending",
        "kind": "dataframe",
        "role": "member",
    }
    tables = [
        row["name"]
        for row in migrated_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_migrations'"
        ).fetchall()
    ]
    checked = 0
    for table in tables:
        fk_rows = migrated_db.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        for fk in fk_rows:
            from_col = fk["from"]
            columns = [
                row["name"] for row in migrated_db.execute(f"PRAGMA table_info({table})").fetchall()
            ]
            not_null = {
                row["name"]: row["notnull"]
                for row in migrated_db.execute(f"PRAGMA table_info({table})").fetchall()
            }
            values: list[object] = []
            for col in columns:
                if col == from_col:
                    values.append("does_not_exist_at_all")
                elif col in known_valid_values:
                    values.append(known_valid_values[col])
                elif not_null.get(col, 0) == 1:
                    values.append("x" if _is_text_column(table, col, migrated_db) else 1)
                else:
                    values.append(None)
            placeholders = ", ".join("?" for _ in columns)
            col_list = ", ".join(columns)
            with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
                migrated_db.execute(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",  # noqa: S608  # table/col_list come from PRAGMA introspection of our own migrated schema, not external input; SQL cannot parameterize identifiers, only values (which are bound via placeholders)
                    values,
                )
            checked += 1
    assert checked > 0


def _is_text_column(table: str, column: str, conn: sqlite3.Connection) -> bool:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    for row in info:
        if row["name"] == column:
            return "TEXT" in str(row["type"]).upper() or str(row["type"]) == ""
    return True


# --- M14-M15: CASCADE / SET NULL semantics --------------------------------------------------------


def test_m14_cascade_deletes_work(migrated_db: sqlite3.Connection, tmp_path: Path) -> None:
    """M14: delete a project, assert its runs and their children vanish."""
    from statlab.persistence.repository import create_contract, create_project, create_run
    from statlab_contracts.contract import DataContract

    proj = create_project(migrated_db, slug="p1", name="P1")
    contract = DataContract.model_validate({"target": {"column": "y"}})
    contract_row = create_contract(
        migrated_db, project_id=proj.id, path=tmp_path / "c.json", contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=proj.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    migrated_db.execute(
        "INSERT INTO node_executions "
        "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
        "VALUES ('ne_x', ?, NULL, 'intake', 1, 1, 'completed', 'ts')",
        (run.id,),
    )

    with migrated_db:
        migrated_db.execute("DELETE FROM projects WHERE id = ?", (proj.id,))

    assert migrated_db.execute("SELECT * FROM runs WHERE id = ?", (run.id,)).fetchone() is None
    assert migrated_db.execute("SELECT * FROM node_executions WHERE id = 'ne_x'").fetchone() is None


def test_m15_forked_from_run_id_is_set_null_not_cascade(
    migrated_db: sqlite3.Connection, tmp_path: Path
) -> None:
    """M15: delete a parent run, assert the fork survives with a null parent -- D11's
    requirement, and the easiest one to get backwards."""
    from statlab.persistence.repository import create_contract, create_project, create_run
    from statlab_contracts.contract import DataContract

    proj = create_project(migrated_db, slug="p2", name="P2")
    contract = DataContract.model_validate({"target": {"column": "y"}})
    contract_row = create_contract(
        migrated_db, project_id=proj.id, path=tmp_path / "c.json", contract=contract
    )
    parent_run = create_run(
        migrated_db,
        project_id=proj.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    fork_run = create_run(
        migrated_db,
        project_id=proj.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=parent_run.id,
    )

    with migrated_db:
        migrated_db.execute("DELETE FROM runs WHERE id = ?", (parent_run.id,))

    row = migrated_db.execute("SELECT * FROM runs WHERE id = ?", (fork_run.id,)).fetchone()
    assert row is not None
    assert row["forked_from_run_id"] is None


# --- additional migration edge cases --------------------------------------------------------------


def test_migrate_returns_versions_in_ascending_order_across_three_files(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "003_c.sql", "CREATE TABLE t_c (id INTEGER PRIMARY KEY);")
    _write_migration(migrations_dir, "001_a.sql", "CREATE TABLE t_a (id INTEGER PRIMARY KEY);")
    _write_migration(migrations_dir, "002_b.sql", "CREATE TABLE t_b (id INTEGER PRIMARY KEY);")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    assert migrate(conn) == (1, 2, 3)


def test_migrate_applying_a_subset_then_the_rest_is_idempotent_per_version(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apply version 1 alone, then re-run with both 1 and 2 present -- only 2 is newly
    applied, confirming `already_applied` is tracked per version rather than as an
    all-or-nothing flag."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "001_a.sql", "CREATE TABLE t_a (id INTEGER PRIMARY KEY);")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    assert migrate(conn) == (1,)

    _write_migration(migrations_dir, "002_b.sql", "CREATE TABLE t_b (id INTEGER PRIMARY KEY);")
    assert migrate(conn) == (2,)
    assert migrate(conn) == ()


def test_schema_migrations_table_created_if_absent_on_first_call(
    conn: sqlite3.Connection,
) -> None:
    """`migrate()`'s own bookkeeping table does not need to pre-exist -- confirms the
    `CREATE TABLE IF NOT EXISTS schema_migrations` guard actually creates it on a
    brand-new, unmigrated connection."""
    exists_before = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    assert exists_before is None
    migrate(conn)
    exists_after = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    assert exists_after is not None


def test_migration_error_is_a_persistence_error(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MigrationError` is a `PersistenceError` subclass (§3.9), so a caller catching the
    broader type still catches migration failures."""
    from statlab.errors import PersistenceError

    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "bad_name.sql", "SELECT 1;")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    with pytest.raises(PersistenceError):
        migrate(conn)


def test_discover_migrations_ignores_non_sql_files(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray non-`.sql` file (e.g. a `.gitkeep` or `README.md`) in the migrations directory
    is not treated as a malformed migration filename -- only `*.sql` is globbed."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    _write_migration(migrations_dir, "001_a.sql", "CREATE TABLE t_a (id INTEGER PRIMARY KEY);")
    (migrations_dir / "README.md").write_text("not a migration", encoding="utf-8")

    def fake_files(package: str) -> Path:
        return tmp_path

    monkeypatch.setattr("statlab.persistence.db.files", fake_files)
    assert migrate(conn) == (1,)
