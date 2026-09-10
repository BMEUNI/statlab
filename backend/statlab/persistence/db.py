"""Connection, pragmas, transactions and migrations: chunk 05 §3.1-§3.3.

No global connection object, no thread-local caching. `connect()` returns a fresh connection and
the caller closes it. Opening SQLite is microseconds; a shared connection across threads is the
bug this design refuses to have.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path

from statlab.errors import MigrationError, PersistenceError
from statlab.persistence.ids import utc_now

_BUSY_TIMEOUT_MS = 5000
_MIGRATION_NAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")


def connect(path: Path) -> sqlite3.Connection:
    """Open `path` with the pragmas D17 requires, and PEP 249 transaction control.

    `isolation_level=None` puts the driver in autocommit mode so transactions are begun
    explicitly by `transaction()`. Without this, `sqlite3`'s legacy control begins a DEFERRED
    transaction implicitly and swallows the choice §3.2 depends on.

    Raises
    ------
    PersistenceError
        If the directory is not writable/openable, or `journal_mode` did not actually become
        `wal` on read-back -- the pragma is a silent no-op on a filesystem without
        shared-memory support (some network mounts), and a run executing without WAL under
        D17's unbounded concurrency corrupts rather than fails.
    """
    try:
        conn = sqlite3.connect(path, isolation_level=None)
    except sqlite3.OperationalError as exc:
        raise PersistenceError(f"cannot open database at {path}", cause=exc) from exc

    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = NORMAL")

        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        if str(mode).lower() != "wal":
            raise PersistenceError(
                f"journal_mode did not take: PRAGMA journal_mode reports {mode!r}, not 'wal' "
                f"-- {path} may be on a filesystem without shared-memory support"
            )
    except PersistenceError:
        conn.close()
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise PersistenceError(f"failed to configure connection to {path}", cause=exc) from exc

    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a write transaction, committing on success and rolling back on any exception.

    Issues `BEGIN IMMEDIATE`, not the driver's default DEFERRED. This is the load-bearing
    choice in this chunk.

    A DEFERRED transaction takes no lock until its first statement, and takes a *read* lock if
    that statement is a SELECT. When it later writes, it must upgrade to a write lock -- and if
    another connection already holds one, SQLite returns SQLITE_BUSY **immediately, ignoring
    busy_timeout**, because waiting could only deadlock: both sides would be holding read locks
    neither can release. `BEGIN IMMEDIATE` takes the write lock upfront, so contention becomes a
    wait that busy_timeout resolves rather than an instant error.

    Under D17's unbounded parallel runs this is the difference between two runs serialising and
    two runs failing.

    Reads need no transaction and must not be wrapped in one -- an autocommit `SELECT` under
    WAL never blocks a writer, which is the whole reason WAL was chosen.

    Nesting is not supported: `BEGIN` inside a transaction is an error from SQLite itself, and
    this function adds no re-entrancy over that -- if a caller needs it, the caller's
    transaction boundaries are wrong.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _migrations_dir() -> Path:
    """Locate the migrations directory via `importlib.resources`, not `Path(__file__).parent`
    -- the former works when the package is zipped or installed."""
    return Path(str(files("statlab.persistence") / "migrations"))


def _discover_migrations() -> tuple[tuple[int, str, Path], ...]:
    """Discover `migrations/*.sql`, parse `NNN_name.sql`, sort by the integer prefix -- not
    the string, or `010` sorts before `009`.

    Raises
    ------
    MigrationError
        On a filename that does not match `^(\\d{3})_([a-z0-9_]+)\\.sql$`, or a duplicate
        version number across two files.
    """
    directory = _migrations_dir()
    found: dict[int, tuple[str, Path]] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_NAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"migration filename {path.name!r} does not match NNN_name.sql",
                version=-1,
                name=path.name,
            )
        version = int(match.group(1))
        name = match.group(2)
        if version in found:
            raise MigrationError(
                f"duplicate migration version {version}: {found[version][1].name!r} and "
                f"{path.name!r}",
                version=version,
                name=name,
            )
        found[version] = (name, path)
    return tuple((version, name, path) for version, (name, path) in sorted(found.items()))


def _split_statements(sql: str) -> tuple[str, ...]:
    """Split a `.sql` file's text into individually executable statements.

    `sqlite3.Connection.executescript` cannot be used inside `transaction()`'s `BEGIN
    IMMEDIATE`: it issues an implicit `COMMIT` before running anything (a legacy behaviour of
    the `sqlite3` module, present regardless of `isolation_level`), which silently ends the
    transaction before the DDL even runs. Splitting and running each statement through
    `execute()` keeps everything inside the one transaction `migrate()` opens.

    Uses `sqlite3.complete_statement` (stdlib) to find statement boundaries correctly even if a
    string literal contains a semicolon, rather than a naive `.split(";")`.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            # `complete_statement` is only True once `buffer` contains a statement-terminating
            # `;` outside any string/comment -- a semicolon is never whitespace, so
            # `buffer.strip()` can never be empty here. No `if stripped:` guard: that branch
            # would be unreachable dead code (confirmed while writing this function's tests),
            # the same class of finding as chunk 04's S7.
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        # Trailing text that never became a complete statement -- SQLite's own execute() will
        # raise on it, which is the right error to surface rather than silently dropping it.
        statements.append(buffer.strip())
    return tuple(statements)


def migrate(conn: sqlite3.Connection) -> tuple[int, ...]:
    """Apply every pending migration in version order. Returns the versions applied.

    Idempotent: a second call on the same database applies nothing and returns `()`.

    Raises
    ------
    MigrationError
        Naming the file and version on a `sqlite3.Error` while applying a migration, on a
        malformed filename, a duplicate version number, or a database whose recorded version
        is absent from disk (a downgrade, meaning the code is older than the data).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )

    discovered = _discover_migrations()
    on_disk = {version for version, _, _ in discovered}

    applied_rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    already_applied = {int(row["version"]) for row in applied_rows}

    missing_from_disk = already_applied - on_disk
    if missing_from_disk:
        oldest_missing = min(missing_from_disk)
        raise MigrationError(
            f"database has migration {oldest_missing} recorded as applied, but it is not on "
            f"disk -- this looks like a downgrade (the code is older than the data)",
            version=oldest_missing,
            name="<missing>",
        )

    applied: list[int] = []
    for version, name, path in discovered:
        if version in already_applied:
            continue
        sql = path.read_text(encoding="utf-8")
        statements = _split_statements(sql)

        try:
            with transaction(conn):
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, utc_now()),
                )
        except sqlite3.Error as exc:
            raise MigrationError(
                f"migration {path.name!r} (version {version}) failed: {exc}",
                version=version,
                name=name,
                cause=exc,
            ) from exc
        applied.append(version)

    return tuple(applied)
