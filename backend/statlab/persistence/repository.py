"""The four spine repositories: chunk 05 §3.8. Plain module-level functions taking `conn`
first. No repository classes, no base class, no ORM, no session object -- a class holding one
connection attribute is a namespace with extra steps.

Conventions later chunks must follow:

- `get_*` returns `None` when absent; `create_*` and `update_*` return the written row read
  back from the database, so the caller sees defaults and generated ids rather than a hopeful
  echo.
- Absence is `None`, not an exception. A missing project is a 404 the API formats, not an error
  the data layer decides is exceptional.
- Every function opens its own `transaction()` for writes; none commits implicitly.
- `sqlite3.IntegrityError` is wrapped in `PersistenceError` naming the constraint. A
  unique-slug collision must not surface as a raw driver exception three layers up.

`create_*`/`update_*` re-`SELECT` the row inside the same transaction after writing, rather than
echoing back the values the caller passed in -- that is what "read back from the database" means
in practice, and it is also what lets `create_contract` return the DB-computed `version`. The
re-`SELECT` runs inside the same `BEGIN IMMEDIATE` transaction as the write, which holds the
exclusive write lock throughout, so no other connection can delete the row in between: the
`SELECT` cannot return `None`. There is deliberately no defensive `None` check on that result --
per chunk 04's finding with S7, a branch that cannot execute is dead code to delete, not a line to
guard with an `assert` or an `if`, both of which would be just as untestable.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from statlab_contracts.contract import DataContract

from statlab.errors import PersistenceError
from statlab.persistence.db import transaction
from statlab.persistence.ids import new_id, utc_now
from statlab.persistence.models import (
    ContractRow,
    DatasetRow,
    LlmCallRow,
    NodeExecutionRow,
    ProjectRow,
    RunMode,
    RunRow,
    RunStatus,
    ToolCallRow,
)


def _wrap_integrity_error(exc: sqlite3.IntegrityError, *, constraint: str) -> PersistenceError:
    """Name the constraint in the message rather than letting a raw driver exception surface
    three layers up."""
    return PersistenceError(f"constraint violation ({constraint}): {exc}", cause=exc)


def _contract_row_from(row: sqlite3.Row) -> ContractRow:
    """`ContractRow`'s Python field is `contract_json`, not `json` (see its docstring for why);
    the DB column is `json` verbatim per PLAN.md §10.2. This is the one place that renames."""
    data = dict(row)
    data["contract_json"] = data.pop("json")
    return ContractRow.model_validate(data)


# --- projects ----------------------------------------------------------------------------------


def create_project(conn: sqlite3.Connection, *, slug: str, name: str) -> ProjectRow:
    project_id = new_id("p")
    created_at = utc_now()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO projects (id, slug, name, created_at) VALUES (?, ?, ?, ?)",
                (project_id, slug, name, created_at),
            )
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="projects.slug") from exc
    return ProjectRow.model_validate(dict(row))


def get_project(conn: sqlite3.Connection, project_id: str) -> ProjectRow | None:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return ProjectRow.model_validate(dict(row)) if row is not None else None


def get_project_by_slug(conn: sqlite3.Connection, slug: str) -> ProjectRow | None:
    row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    return ProjectRow.model_validate(dict(row)) if row is not None else None


def list_projects(conn: sqlite3.Connection) -> tuple[ProjectRow, ...]:
    rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return tuple(ProjectRow.model_validate(dict(row)) for row in rows)


# --- datasets ------------------------------------------------------------------------------


def create_dataset(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    filename: str,
    path: Path,
    sha256: str,
    n_rows: int,
    n_cols: int,
) -> DatasetRow:
    dataset_id = new_id("ds")
    created_at = utc_now()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO datasets "
                "(id, project_id, filename, path, sha256, n_rows, n_cols, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (dataset_id, project_id, filename, str(path), sha256, n_rows, n_cols, created_at),
            )
            row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="datasets(project_id, sha256)") from exc
    return DatasetRow.model_validate(dict(row))


def get_dataset(conn: sqlite3.Connection, dataset_id: str) -> DatasetRow | None:
    row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    return DatasetRow.model_validate(dict(row)) if row is not None else None


def find_dataset_by_hash(
    conn: sqlite3.Connection, *, project_id: str, sha256: str
) -> DatasetRow | None:
    row = conn.execute(
        "SELECT * FROM datasets WHERE project_id = ? AND sha256 = ?", (project_id, sha256)
    ).fetchone()
    return DatasetRow.model_validate(dict(row)) if row is not None else None


# --- contracts ------------------------------------------------------------------------------


def create_contract(
    conn: sqlite3.Connection, *, project_id: str, path: Path, contract: DataContract
) -> ContractRow:
    """Derives `version` as `MAX(version) + 1` for the project inside its own transaction, so
    two concurrent uploads cannot both claim v1 (§3.8)."""
    contract_id = new_id("ct")
    created_at = utc_now()
    contract_json = contract.model_dump_json()
    with transaction(conn):
        (max_version,) = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM contracts WHERE project_id = ?", (project_id,)
        ).fetchone()
        version = int(max_version) + 1
        conn.execute(
            "INSERT INTO contracts (id, project_id, path, json, version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (contract_id, project_id, str(path), contract_json, version, created_at),
        )
        row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    return _contract_row_from(row)


def get_contract(conn: sqlite3.Connection, contract_id: str) -> ContractRow | None:
    row = conn.execute("SELECT * FROM contracts WHERE id = ?", (contract_id,)).fetchone()
    return _contract_row_from(row) if row is not None else None


def latest_contract(conn: sqlite3.Connection, project_id: str) -> ContractRow | None:
    """Returns the highest version, not the newest `created_at` -- they agree under normal
    operation, but version is the number the schema actually guarantees is monotonic."""
    row = conn.execute(
        "SELECT * FROM contracts WHERE project_id = ? ORDER BY version DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return _contract_row_from(row) if row is not None else None


# --- runs ----------------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    contract_id: str,
    mode: RunMode,
    config_json: str,
    forked_from_run_id: str | None = None,
) -> RunRow:
    run_id = new_id("r")
    started_at = utc_now()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO runs "
                "(id, project_id, contract_id, mode, status, current_node, champion_model_id, "
                "forked_from_run_id, started_at, ended_at, config_json) "
                "VALUES (?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL, ?)",
                (
                    run_id,
                    project_id,
                    contract_id,
                    mode,
                    forked_from_run_id,
                    started_at,
                    config_json,
                ),
            )
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="runs.contract_id/project_id") from exc
    return RunRow.model_validate(dict(row))


def get_run(conn: sqlite3.Connection, run_id: str) -> RunRow | None:
    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return RunRow.model_validate(dict(row)) if row is not None else None


def list_runs(conn: sqlite3.Connection, project_id: str) -> tuple[RunRow, ...]:
    """Newest-first, per the order the UI depends on."""
    rows = conn.execute(
        "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC", (project_id,)
    ).fetchall()
    return tuple(RunRow.model_validate(dict(row)) for row in rows)


def create_node_execution(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    node_name: str,
    seq: int,
    candidate_id: str | None = None,
    attempt: int = 1,
) -> NodeExecutionRow:
    """Create an auditable execution row; callers own reuse across repeated calls."""
    execution_id, started_at = new_id("ne"), utc_now()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO node_executions "
                "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'running', ?)",
                (execution_id, run_id, candidate_id, node_name, seq, attempt, started_at),
            )
            row = conn.execute(
                "SELECT * FROM node_executions WHERE id = ?", (execution_id,)
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="node_executions identity") from exc
    except sqlite3.Error as exc:
        raise PersistenceError("cannot create node execution", cause=exc) from exc
    return NodeExecutionRow.model_validate(dict(row))


def record_tool_call(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    node_execution_id: str,
    tool_name: str,
    args_json: str,
    result_json: str,
    duration_ms: int,
    ts: str | None = None,
) -> ToolCallRow:
    """Persist sent arguments and response, rejecting cross-run attribution."""
    call_id, created_at = new_id("tc"), ts or utc_now()
    try:
        with transaction(conn):
            execution = conn.execute(
                "SELECT run_id FROM node_executions WHERE id = ?", (node_execution_id,)
            ).fetchone()
            if execution is None or execution["run_id"] != run_id:
                raise PersistenceError("tool call execution does not belong to run")
            conn.execute(
                "INSERT INTO tool_calls "
                "(id, run_id, node_execution_id, tool_name, args_json, result_json, "
                "duration_ms, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call_id,
                    run_id,
                    node_execution_id,
                    tool_name,
                    args_json,
                    result_json,
                    duration_ms,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM tool_calls WHERE id = ?", (call_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="tool_calls attribution") from exc
    except sqlite3.Error as exc:
        raise PersistenceError("cannot record tool call", cause=exc) from exc
    return ToolCallRow.model_validate(dict(row))


def record_llm_call(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    node_execution_id: str,
    signature: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    cost_usd: float,
    duration_ms: int,
    ts: str | None = None,
) -> LlmCallRow:
    """Persist LLM accounting metadata, rejecting cross-run attribution."""
    call_id, created_at = new_id("lc"), ts or utc_now()
    try:
        with transaction(conn):
            execution = conn.execute(
                "SELECT run_id FROM node_executions WHERE id = ?", (node_execution_id,)
            ).fetchone()
            if execution is None or execution["run_id"] != run_id:
                raise PersistenceError("LLM call execution does not belong to run")
            conn.execute(
                "INSERT INTO llm_calls "
                "(id, run_id, node_execution_id, signature, model, prompt_tokens, "
                "completion_tokens, cached_tokens, cost_usd, duration_ms, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    call_id,
                    run_id,
                    node_execution_id,
                    signature,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    cached_tokens,
                    cost_usd,
                    duration_ms,
                    created_at,
                ),
            )
            row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="llm_calls attribution") from exc
    except sqlite3.Error as exc:
        raise PersistenceError("cannot record LLM call", cause=exc) from exc
    return LlmCallRow.model_validate(dict(row))


def update_run_status(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    status: RunStatus,
    current_node: str | None = None,
) -> RunRow:
    """Raises `PersistenceError` on an unknown `run_id` -- updating nothing is a bug in the
    caller, unlike reading nothing."""
    try:
        with transaction(conn):
            cursor = conn.execute(
                "UPDATE runs SET status = ?, current_node = ? WHERE id = ?",
                (status, current_node, run_id),
            )
            if cursor.rowcount == 0:
                raise PersistenceError(f"no run with id {run_id!r} to update")
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="runs.status") from exc
    return RunRow.model_validate(dict(row))


def claim_test_scoring(conn: sqlite3.Connection, run_id: str, *, at: str | None = None) -> bool:
    """Atomically claim the run's single permitted test-set scoring (D26, §6.3).

    Returns True if the caller won the claim and may proceed, False if the run has already
    been scored on test. Raises `PersistenceError` if `run_id` does not exist --
    distinguishable from a lost claim, since one is a caller bug and the other is the
    guarantee working.

    One statement does it: `UPDATE ... WHERE id = ? AND test_scored_at IS NULL`, then
    `rowcount == 1` means the caller's row was the one updated. Durable by construction (a
    committed `UPDATE`), so it survives the crash an in-memory flag cannot -- the exact
    failure mode this guarantee exists to prevent. `mcp_client` (chunk 21) remains the
    enforcement point per D30(b); ownership of a guarantee and storage of its state are
    separable, which is why this needs no `locked` column and no artifact-registry lock.

    Distinguishing "won" from "lost" from "unknown run_id" needs a preceding existence check,
    since the single `UPDATE`'s `rowcount` cannot tell a nonexistent row apart from an
    already-claimed one -- both leave `rowcount == 0`.
    """
    claimed_at = at if at is not None else utc_now()
    with transaction(conn):
        exists = conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if exists is None:
            raise PersistenceError(f"no run with id {run_id!r}")
        cursor = conn.execute(
            "UPDATE runs SET test_scored_at = ? WHERE id = ? AND test_scored_at IS NULL",
            (claimed_at, run_id),
        )
        return cursor.rowcount == 1


def set_test_fingerprint(conn: sqlite3.Connection, run_id: str, fingerprint: str) -> bool:
    """Write-once record of the test set this run scored (R4, D32).

    Returns True if this call set the fingerprint, False if it was already set. Raises
    `PersistenceError` if `run_id` does not exist -- distinguishable from "already set", since
    one is a caller bug and the other is the write-once guarantee working.

    Same compare-and-set shape as `claim_test_scoring`: one `UPDATE ... WHERE id = ? AND
    test_fingerprint IS NULL`, `rowcount == 1` means this call won. The existence check runs
    inside the same `BEGIN IMMEDIATE` transaction as the `UPDATE`, so "unknown run" and "already
    set" -- both leaving `rowcount == 0` on the `UPDATE` alone -- stay distinguishable without a
    race window of their own.
    """
    with transaction(conn):
        exists = conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if exists is None:
            raise PersistenceError(f"no run with id {run_id!r}")
        cursor = conn.execute(
            "UPDATE runs SET test_fingerprint = ? WHERE id = ? AND test_fingerprint IS NULL",
            (fingerprint, run_id),
        )
        return cursor.rowcount == 1


def count_prior_test_scorings(
    conn: sqlite3.Connection, *, project_id: str, fingerprint: str, exclude_run_id: str
) -> int:
    """Count runs in `project_id` that already scored the test set identified by `fingerprint`
    (R4, D32) -- i.e. have that `test_fingerprint` and a non-null `test_scored_at` -- excluding
    `exclude_run_id` (the run asking).

    Used to detect cross-run test exposure through forks: a fork inherits the parent's test set,
    so the same fingerprint scored by a sibling counts against this run's single-exposure budget.
    """
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM runs "
        "WHERE project_id = ? AND test_fingerprint = ? AND test_scored_at IS NOT NULL "
        "AND id != ?",
        (project_id, fingerprint, exclude_run_id),
    ).fetchone()
    return int(count)
