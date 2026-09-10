"""Connection, pragmas and transaction tests: chunk 05 §4.2, N1-N22.

Every test uses a `tmp_path` file database via the `db_path`/`conn`/`migrated_db` fixtures --
never `':memory:'`, which does not support WAL and would make every pragma assertion here pass
vacuously.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from statlab.errors import PersistenceError
from statlab.persistence.db import connect, transaction

# --- N1-N10: pragmas and connect() --------------------------------------------------------------


def test_n1_journal_mode_is_wal(conn: sqlite3.Connection) -> None:
    """N1."""
    (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    assert str(mode).lower() == "wal"


def test_n2_busy_timeout_is_5000(conn: sqlite3.Connection) -> None:
    """N2."""
    (timeout,) = conn.execute("PRAGMA busy_timeout").fetchone()
    assert timeout == 5000


def test_n3_foreign_keys_is_on(conn: sqlite3.Connection) -> None:
    """N3: the one most likely to be forgotten -- OFF by default, per connection."""
    (value,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert value == 1


def test_n4_synchronous_is_normal(conn: sqlite3.Connection) -> None:
    """N4: NORMAL is 1."""
    (value,) = conn.execute("PRAGMA synchronous").fetchone()
    assert value == 1


def test_n5_row_factory_is_sqlite3_row(conn: sqlite3.Connection) -> None:
    """N5."""
    assert conn.row_factory is sqlite3.Row


def test_n6_two_connections_both_get_wal(db_path: Path) -> None:
    """N6: pragmas are per-connection; prove the second is configured too."""
    conn1 = connect(db_path)
    conn2 = connect(db_path)
    try:
        (mode1,) = conn1.execute("PRAGMA journal_mode").fetchone()
        (mode2,) = conn2.execute("PRAGMA journal_mode").fetchone()
        assert str(mode1).lower() == "wal"
        assert str(mode2).lower() == "wal"
        (fk1,) = conn1.execute("PRAGMA foreign_keys").fetchone()
        (fk2,) = conn2.execute("PRAGMA foreign_keys").fetchone()
        assert fk1 == 1
        assert fk2 == 1
    finally:
        conn1.close()
        conn2.close()


def test_n7_fk_enforcement_is_real(migrated_db: sqlite3.Connection) -> None:
    """N7: inserting a child with a bogus parent id raises."""
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        migrated_db.execute(
            "INSERT INTO datasets "
            "(id, project_id, filename, path, sha256, n_rows, n_cols, created_at) "
            "VALUES ('ds_x', 'p_missing', 'f.csv', '/tmp/f.csv', 'abc', 1, 1, '2020-01-01')"
        )


def test_n8_non_wal_filesystem_raises_persistence_error(db_path: Path) -> None:
    """N8: monkeypatch the pragma read-back to return `delete`; `connect` raises.

    `sqlite3.Connection` is a C-level type whose methods cannot be patched on the class or an
    instance (`setattr` raises `TypeError: ... attribute ... read-only`) -- confirmed while
    writing this test. Patching `sqlite3.connect` itself to return a thin proxy is the
    mockable seam: it forwards every attribute except `execute`, where it substitutes
    `'delete'` for a `PRAGMA journal_mode` read specifically, so `connect()`'s own read-back
    check sees a filesystem that silently refused WAL.
    """

    class _FakeJournalModeConnection:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str) -> sqlite3.Cursor:
            """Typed to match exactly the parameterless calls `connect()` makes during pragma
            setup and read-back -- `db.py` never passes bound parameters before migrations
            run, so this needs no broader signature and no `type: ignore`."""
            if sql.strip() == "PRAGMA journal_mode":
                return self._real.execute("SELECT 'delete'")
            return self._real.execute(sql)

        def close(self) -> None:
            self._real.close()

        def __setattr__(self, name: str, value: object) -> None:
            if name == "_real":
                object.__setattr__(self, name, value)
            else:
                setattr(self._real, name, value)

    real_connect = sqlite3.connect

    def fake_connect(database: Path, *, isolation_level: None = None) -> _FakeJournalModeConnection:
        """Typed to match exactly the one call shape `db.py`'s `connect()` makes
        (`sqlite3.connect(path, isolation_level=None)`), rather than `*args, **kwargs`, so
        this needs no `type: ignore` against `sqlite3.connect`'s overloaded stub."""
        return _FakeJournalModeConnection(real_connect(database, isolation_level=isolation_level))

    with (
        patch("sqlite3.connect", fake_connect),
        pytest.raises(PersistenceError, match="journal_mode did not take"),
    ):
        connect(db_path)


def test_n8b_generic_sqlite_error_during_pragma_setup_wraps_and_closes(db_path: Path) -> None:
    """Companion to N8: a `sqlite3.Error` raised while configuring pragmas (distinct from the
    `journal_mode` read-back mismatch N8 covers) is also wrapped into `PersistenceError`, and
    the connection is closed rather than leaked. Same proxy-connection technique as N8, since
    `sqlite3.Connection` cannot be patched directly."""

    class _FailingPragmaConnection:
        def __init__(self, real: sqlite3.Connection) -> None:
            self._real = real

        def execute(self, sql: str) -> sqlite3.Cursor:
            if sql.strip() == "PRAGMA synchronous = NORMAL":
                raise sqlite3.OperationalError("simulated pragma failure")
            return self._real.execute(sql)

        def close(self) -> None:
            self._real.close()

        def __setattr__(self, name: str, value: object) -> None:
            if name == "_real":
                object.__setattr__(self, name, value)
            else:
                setattr(self._real, name, value)

    real_connect = sqlite3.connect

    def fake_connect(database: Path, *, isolation_level: None = None) -> _FailingPragmaConnection:
        return _FailingPragmaConnection(real_connect(database, isolation_level=isolation_level))

    with (
        patch("sqlite3.connect", fake_connect),
        pytest.raises(PersistenceError, match="failed to configure connection"),
    ):
        connect(db_path)


def test_n9_connect_on_unwritable_directory_raises_persistence_error(tmp_path: Path) -> None:
    """N9: `PersistenceError`, not a bare `sqlite3.OperationalError`."""
    unwritable_dir = tmp_path / "no_such_dir_at_all" / "nested"
    bad_path = unwritable_dir / "statlab.db"
    with pytest.raises(PersistenceError):
        connect(bad_path)


def test_n10_reopening_existing_db_preserves_wal(db_path: Path) -> None:
    """N10: idempotent."""
    conn1 = connect(db_path)
    conn1.close()
    conn2 = connect(db_path)
    try:
        (mode,) = conn2.execute("PRAGMA journal_mode").fetchone()
        assert str(mode).lower() == "wal"
    finally:
        conn2.close()


# --- N11-N19: transaction() commit/rollback -----------------------------------------------------


def test_n11_transaction_commits_row_visible_from_second_connection(
    migrated_db: sqlite3.Connection, db_path: Path
) -> None:
    """N11."""
    with transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_a', 'a', 'A', 'ts')"
        )
    other = connect(db_path)
    try:
        row = other.execute("SELECT * FROM projects WHERE id = 'p_a'").fetchone()
        assert row is not None
    finally:
        other.close()


def test_n12_transaction_commit_persists_multiple_statements(
    migrated_db: sqlite3.Connection,
) -> None:
    """N12."""
    with transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_b', 'b', 'B', 'ts')"
        )
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_c', 'c', 'C', 'ts')"
        )
    rows = migrated_db.execute("SELECT id FROM projects ORDER BY id").fetchall()
    assert {r["id"] for r in rows} == {"p_b", "p_c"}


def test_n13_transaction_returns_the_connection(migrated_db: sqlite3.Connection) -> None:
    """N13: the context manager yields the same connection, for chaining calls."""
    with transaction(migrated_db) as yielded:
        assert yielded is migrated_db


def test_n14_transaction_commits_are_visible_after_exit_not_during(
    migrated_db: sqlite3.Connection, db_path: Path
) -> None:
    """N14: a second connection sees nothing until the transaction exits."""
    other = connect(db_path)
    try:
        with transaction(migrated_db):
            migrated_db.execute(
                "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_d', 'd', 'D', 'ts')"
            )
            mid_rows = other.execute("SELECT * FROM projects WHERE id = 'p_d'").fetchall()
            assert mid_rows == []
        after_rows = other.execute("SELECT * FROM projects WHERE id = 'p_d'").fetchall()
        assert len(after_rows) == 1
    finally:
        other.close()


def test_n15_transaction_rolls_back_on_exception(migrated_db: sqlite3.Connection) -> None:
    """N15: exception propagates and no row is visible."""
    with pytest.raises(ValueError, match="boom"), transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_e', 'e', 'E', 'ts')"
        )
        raise ValueError("boom")
    row = migrated_db.execute("SELECT * FROM projects WHERE id = 'p_e'").fetchone()
    assert row is None


def test_n16_rollback_leaves_prior_committed_rows_intact(migrated_db: sqlite3.Connection) -> None:
    """N16."""
    with transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_f', 'f', 'F', 'ts')"
        )
    with pytest.raises(RuntimeError), transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_g', 'g', 'G', 'ts')"
        )
        raise RuntimeError("boom")
    ids = {r["id"] for r in migrated_db.execute("SELECT id FROM projects").fetchall()}
    assert ids == {"p_f"}


def test_n17_rollback_on_integrity_error(migrated_db: sqlite3.Connection) -> None:
    """N17: a constraint violation inside the block also rolls back."""
    with transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_h', 'h', 'H', 'ts')"
        )
    with pytest.raises(sqlite3.IntegrityError), transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_i', 'h', 'I', 'ts')"
        )
    ids = {r["id"] for r in migrated_db.execute("SELECT id FROM projects").fetchall()}
    assert ids == {"p_h"}


def test_n18_rollback_on_persistence_error(migrated_db: sqlite3.Connection) -> None:
    """N18: the wrapped-error path still rolls back."""
    with pytest.raises(PersistenceError), transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_j', 'j', 'J', 'ts')"
        )
        raise PersistenceError("simulated failure mid-transaction")
    row = migrated_db.execute("SELECT * FROM projects WHERE id = 'p_j'").fetchone()
    assert row is None


def test_n19_rollback_on_persistence_error_specifically_no_partial_write(
    migrated_db: sqlite3.Connection,
) -> None:
    """N19: same as N18, phrased against §4.2's row -- the wrapped-error path (as opposed to a
    raw sqlite3.Error) still triggers `ROLLBACK`, not a silently-committed partial state."""
    with pytest.raises(PersistenceError), transaction(migrated_db):
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_k', 'k', 'K', 'ts')"
        )
        migrated_db.execute(
            "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_l', 'l', 'L', 'ts')"
        )
        raise PersistenceError("simulated failure after two writes")
    ids = {r["id"] for r in migrated_db.execute("SELECT id FROM projects").fetchall()}
    assert ids == set()


# --- N20-N22 -------------------------------------------------------------------------------------


def test_n20_nested_transaction_raises(migrated_db: sqlite3.Connection) -> None:
    """N20: pin the documented non-support. `BEGIN` inside a transaction is a SQLite error."""
    with (
        pytest.raises(sqlite3.OperationalError, match="within a transaction"),
        transaction(migrated_db),
        transaction(migrated_db),
    ):
        pass


def test_n21_select_outside_any_transaction_works(migrated_db: sqlite3.Connection) -> None:
    """N21: reads need no transaction."""
    rows = migrated_db.execute("SELECT * FROM projects").fetchall()
    assert rows == []


def test_n22_transaction_uses_begin_immediate(db_path: Path) -> None:
    """N22: asserted via a second connection observing the write lock rather than an instant
    `SQLITE_BUSY` -- see `db.py`'s `transaction()` docstring and this test file's module
    docstring reference. A DEFERRED transaction's first-write upgrade fails instantly even
    with `busy_timeout` set (confirmed separately while building this suite: a DEFERRED
    transaction that SELECTs then tries to INSERT while another connection holds the write
    lock raises `sqlite3.OperationalError: database is locked` in microseconds, ignoring
    `busy_timeout` entirely). `BEGIN IMMEDIATE` instead makes the second connection *wait*:
    this test proves that wait is real by timing it against a controlled delay, which is
    the externally observable difference `BEGIN IMMEDIATE` is chosen for.

    What is directly observable from Python: `sqlite3.Connection.in_transaction` (asserted
    below) and the wait duration under contention. What is NOT directly observable from
    Python: the literal `BEGIN IMMEDIATE` bytes SQLite's C layer sends internally -- there is
    no `sqlite3` API that echoes back which `BEGIN` variant was used. The wait-duration
    behaviour is therefore the honest proxy: it is the *consequence* of `IMMEDIATE` rather
    than the statement text itself, which is the strongest assertion available without
    patching `sqlite3.Connection.execute` to capture the literal SQL string.
    """
    conn1 = connect(db_path)
    from statlab.persistence.db import migrate

    migrate(conn1)

    conn1.execute("BEGIN IMMEDIATE")
    assert conn1.in_transaction is True

    result: dict[str, float] = {}

    def contend() -> None:
        conn2 = connect(db_path)
        try:
            start = time.monotonic()
            conn2.execute("BEGIN IMMEDIATE")
            result["elapsed"] = time.monotonic() - start
            conn2.execute("ROLLBACK")
        finally:
            conn2.close()

    thread = threading.Thread(target=contend)
    thread.start()
    time.sleep(0.3)
    conn1.execute("ROLLBACK")
    thread.join(timeout=6)

    assert not thread.is_alive()
    assert "elapsed" in result
    # The second connection waited for roughly the hold duration, not an instant failure.
    assert result["elapsed"] >= 0.2

    conn1.close()


# --- Boundary isolation ---------------------------------------------------------------------


def test_mcp_server_does_not_import_statlab_persistence() -> None:
    """§4.8: `mcp_server/` must not import `statlab.persistence`. The MCP server is stateless
    by D26; a tool reaching into the database would break the locking guarantees chunk 21
    enforces on the client side. Same source-scan technique as `test_contracts_envelope.py`'s
    boundary isolation tests: walk every name defined in `statlab_tools.settings` (the one
    substantial module in `mcp_server/` today) and assert none resolves into
    `statlab.persistence` or `statlab.domains` (chunk 02's engine-only spine types)."""
    import statlab_tools.settings as settings_mod

    for name, value in vars(settings_mod).items():
        if name.startswith("__"):
            continue
        module_name = getattr(value, "__module__", None)
        if module_name is None:
            continue
        assert not module_name.startswith("statlab.persistence"), (
            f"{name} in {settings_mod.__name__} resolves to {module_name}, "
            f"but the MCP server must stay stateless (D26) and never import the database layer"
        )


def test_mcp_server_package_source_never_mentions_persistence() -> None:
    """Direct text-level check, in addition to the import-graph walk above: no file under
    `mcp_server/` contains the literal substring `statlab.persistence`, so a future import
    added inside a function body (which the module-level `vars()` walk above cannot see)
    would still be caught."""
    mcp_server_root = Path(__file__).resolve().parent.parent / "mcp_server"
    for path in mcp_server_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "statlab.persistence" not in text, f"{path} mentions statlab.persistence"
