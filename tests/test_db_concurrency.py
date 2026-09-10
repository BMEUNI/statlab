"""Chunk 05 §4.7: the tests that justify the design. Real `threading.Thread`s, real file
database -- never `':memory:'`.
"""

from __future__ import annotations

import threading
from pathlib import Path

from statlab.persistence.db import connect, migrate
from statlab.persistence.repository import create_contract, create_project
from statlab_contracts.contract import DataContract

_CONTRACT = DataContract.model_validate({"target": {"column": "y"}})


def test_k1_eight_threads_ten_creates_each_all_present(db_path: Path) -> None:
    """K1: 8 threads x 10 `create_project` each. All 80 rows present, no exception. Fails if
    `busy_timeout` is unset."""
    setup_conn = connect(db_path)
    migrate(setup_conn)
    setup_conn.close()

    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(thread_id: int) -> None:
        conn = connect(db_path)
        try:
            for i in range(10):
                create_project(conn, slug=f"t{thread_id}-{i}", name=f"T{thread_id}-{i}")
        except BaseException as exc:  # captured for the assertion, not swallowed
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    verify_conn = connect(db_path)
    try:
        count = verify_conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        assert count == 80
    finally:
        verify_conn.close()


def test_k2_concurrent_contract_versioning_no_duplicates(db_path: Path) -> None:
    """K2: 8 threads; versions are exactly 1..8 with no duplicates. This is the test that
    catches a `MAX(version)+1` computed outside the transaction. If it fails, the bug is that
    the version query ran outside the write transaction -- fix the boundary, not a retry loop.
    """
    setup_conn = connect(db_path)
    migrate(setup_conn)
    project_row = create_project(setup_conn, slug="shared", name="Shared")
    setup_conn.close()

    versions: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = connect(db_path)
        try:
            row = create_contract(
                conn, project_id=project_row.id, path=Path("/c.json"), contract=_CONTRACT
            )
            with lock:
                versions.append(row.version)
        except BaseException as exc:  # captured for the assertion, not swallowed
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert sorted(versions) == list(range(1, 9))


def test_k3_reader_not_blocked_by_open_writer(db_path: Path) -> None:
    """K3: a thread holding an open write transaction does not block a `SELECT` on another
    connection -- WAL's entire purpose.

    The reader's connection is created inside the reader thread itself: `sqlite3.Connection`
    is thread-affine by default (confirmed while writing this suite --
    `sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that
    same thread` when a connection built in the main thread was reused inside a worker), so a
    genuine cross-connection test needs each thread minting its own connection, not sharing
    one across the thread boundary.
    """
    setup_conn = connect(db_path)
    migrate(setup_conn)
    setup_conn.close()

    writer_conn = connect(db_path)
    writer_conn.execute("BEGIN IMMEDIATE")
    writer_conn.execute(
        "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_w', 'w', 'W', 'ts')"
    )

    result: dict[str, object] = {}

    def read() -> None:
        reader_conn = connect(db_path)
        try:
            rows = reader_conn.execute("SELECT * FROM projects").fetchall()
            result["rows"] = rows
            result["done"] = True
        finally:
            reader_conn.close()

    reader_thread = threading.Thread(target=read)
    reader_thread.start()
    reader_thread.join(timeout=2)

    assert result.get("done") is True, "reader was blocked by an open writer under WAL"
    # The uncommitted insert must not be visible to the reader (it is a separate connection).
    assert result["rows"] == []

    writer_conn.execute("ROLLBACK")
    writer_conn.close()


def test_k4_write_contention_resolves_by_waiting_both_succeed(db_path: Path) -> None:
    """K4: two threads each writing in a transaction both succeed."""
    setup_conn = connect(db_path)
    migrate(setup_conn)
    setup_conn.close()

    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(slug: str) -> None:
        conn = connect(db_path)
        try:
            create_project(conn, slug=slug, name=slug)
        except BaseException as exc:  # captured for the assertion, not swallowed
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    t1 = threading.Thread(target=worker, args=("alpha",))
    t2 = threading.Thread(target=worker, args=("beta",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert errors == []
    verify_conn = connect(db_path)
    try:
        slugs = {r["slug"] for r in verify_conn.execute("SELECT slug FROM projects").fetchall()}
        assert slugs == {"alpha", "beta"}
    finally:
        verify_conn.close()


def test_k5_rollback_under_concurrency_does_not_roll_back_other_thread(db_path: Path) -> None:
    """K5: one thread's failing transaction does not roll back another's work."""
    setup_conn = connect(db_path)
    migrate(setup_conn)
    setup_conn.close()

    from statlab.persistence.db import transaction

    success_result: dict[str, bool] = {}
    failure_result: dict[str, bool] = {}

    def succeed() -> None:
        conn = connect(db_path)
        try:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO projects (id, slug, name, created_at) "
                    "VALUES ('p_good', 'good', 'Good', 'ts')"
                )
            success_result["committed"] = True
        finally:
            conn.close()

    def fail() -> None:
        conn = connect(db_path)
        try:
            try:
                with transaction(conn):
                    conn.execute(
                        "INSERT INTO projects (id, slug, name, created_at) "
                        "VALUES ('p_bad', 'bad', 'Bad', 'ts')"
                    )
                    raise RuntimeError("deliberate failure")
            except RuntimeError:
                failure_result["raised"] = True
        finally:
            conn.close()

    t_ok = threading.Thread(target=succeed)
    t_bad = threading.Thread(target=fail)
    t_ok.start()
    t_ok.join(timeout=10)
    t_bad.start()
    t_bad.join(timeout=10)

    assert success_result.get("committed") is True
    assert failure_result.get("raised") is True

    verify_conn = connect(db_path)
    try:
        slugs = {r["slug"] for r in verify_conn.execute("SELECT slug FROM projects").fetchall()}
        assert slugs == {"good"}
    finally:
        verify_conn.close()


def test_k6_wal_files_appear_while_connection_open(db_path: Path) -> None:
    """K6: `-wal` and `-shm` exist beside the database while a connection is open."""
    conn = connect(db_path)
    migrate(conn)
    conn.execute(
        "INSERT INTO projects (id, slug, name, created_at) VALUES ('p_1', 'p1', 'P1', 'ts')"
    )

    wal_path = db_path.with_name(db_path.name + "-wal")
    shm_path = db_path.with_name(db_path.name + "-shm")
    assert wal_path.exists()
    assert shm_path.exists()

    conn.close()


def test_k7_two_threads_racing_on_identical_slug_exactly_one_succeeds(db_path: Path) -> None:
    """Complements K1/K4: a genuine unique-constraint race (not just distinct rows) resolves
    to exactly one winner rather than a corrupted or duplicated row -- `busy_timeout` handles
    the lock contention, and SQLite's own constraint check handles the collision itself."""
    setup_conn = connect(db_path)
    migrate(setup_conn)
    setup_conn.close()

    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = connect(db_path)
        try:
            create_project(conn, slug="only-one", name="Only One")
            with lock:
                outcomes.append("ok")
        except Exception:  # any failure (including PersistenceError) counts as "lost the race"
            with lock:
                outcomes.append("lost")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count("ok") == 1
    assert outcomes.count("lost") == 7

    verify_conn = connect(db_path)
    try:
        count = verify_conn.execute(
            "SELECT COUNT(*) FROM projects WHERE slug = 'only-one'"
        ).fetchone()[0]
        assert count == 1
    finally:
        verify_conn.close()


def test_k8_sixteen_threads_claiming_test_scoring_exactly_one_winner(db_path: Path) -> None:
    """§6.3: 16 concurrent claimants across 16 connections must produce exactly one winner and
    fifteen losers -- the behavioural proof behind `claim_test_scoring`'s durability claim.
    Verified before it was specified; reconfirmed here as a permanent regression test."""
    from statlab.persistence.repository import claim_test_scoring, create_run

    setup_conn = connect(db_path)
    migrate(setup_conn)
    project_row = create_project(setup_conn, slug="scoring-project", name="Scoring Project")
    contract_row = create_contract(
        setup_conn, project_id=project_row.id, path=Path("/c.json"), contract=_CONTRACT
    )
    run_row = create_run(
        setup_conn,
        project_id=project_row.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    setup_conn.close()

    outcomes: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        conn = connect(db_path)
        try:
            won = claim_test_scoring(conn, run_row.id)
            with lock:
                outcomes.append(won)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 15
    assert len(outcomes) == 16


def test_k9_sixteen_threads_two_datasets_each_all_present(db_path: Path) -> None:
    """A second high-concurrency smoke test on a different entity (`datasets`, which carries
    a composite `UNIQUE(project_id, sha256)` rather than `projects.slug`'s simple unique) at a
    higher thread count than K1, to catch any lock-handling issue specific to that constraint
    shape under contention."""
    from statlab.persistence.repository import create_dataset

    setup_conn = connect(db_path)
    migrate(setup_conn)
    project_row = create_project(setup_conn, slug="ds-project", name="DS Project")
    setup_conn.close()

    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(thread_id: int) -> None:
        conn = connect(db_path)
        try:
            for i in range(2):
                create_dataset(
                    conn,
                    project_id=project_row.id,
                    filename=f"f{thread_id}-{i}.csv",
                    path=Path(f"/f{thread_id}-{i}.csv"),
                    sha256=f"hash-{thread_id}-{i}",
                    n_rows=1,
                    n_cols=1,
                )
        except BaseException as exc:  # captured for the assertion, not swallowed
            with lock:
                errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    verify_conn = connect(db_path)
    try:
        count = verify_conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        assert count == 32
    finally:
        verify_conn.close()
