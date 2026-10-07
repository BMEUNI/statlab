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

import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError
from statlab_contracts.candidate import CandidateSpec
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, Failure

from statlab.domains.base import NodeResult
from statlab.errors import PersistenceError
from statlab.persistence.db import transaction
from statlab.persistence.ids import new_id, utc_now
from statlab.persistence.models import (
    CandidateRow,
    ContractRow,
    DatasetRow,
    GateRow,
    LlmCallRow,
    MemoryRow,
    MessageRole,
    MessageRow,
    ModelRow,
    NodeExecutionRow,
    ProjectRow,
    RunMode,
    RunRow,
    RunStatus,
    SessionRow,
    ToolCallRow,
)


def create_session(conn: sqlite3.Connection, *, project_id: str, title: str) -> SessionRow:
    if not title.strip() or len(title) > 200:
        raise PersistenceError("invalid session title")
    session_id, created_at = new_id("s"), utc_now()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO sessions (id, project_id, title, created_at) VALUES (?, ?, ?, ?)",
                (session_id, project_id, title, created_at),
            )
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return SessionRow.model_validate(dict(row))
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot create session", cause=exc) from exc


def get_session(conn: sqlite3.Connection, session_id: str) -> SessionRow | None:
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return SessionRow.model_validate(dict(row)) if row is not None else None
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read session", cause=exc) from exc


def create_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    run_id: str,
    role: MessageRole,
    content: str,
    meta: dict[str, JsonValue] | None = None,
) -> MessageRow:
    if not content.strip() or len(content) > 8000:
        raise PersistenceError("invalid message content")
    try:
        meta_json = json.dumps(meta or {}, allow_nan=False, sort_keys=True, separators=(",", ":"))
        TypeAdapter(dict[str, JsonValue]).validate_json(meta_json, strict=True)
        message_id, ts = new_id("msg"), utc_now()
        with transaction(conn):
            owner = conn.execute(
                "SELECT s.project_id AS project_id FROM sessions s "
                "JOIN runs r ON r.id = ? WHERE s.id = ? AND s.project_id = r.project_id",
                (run_id, session_id),
            ).fetchone()
            if owner is None:
                raise PersistenceError("message session/run ownership mismatch")
            conn.execute(
                "INSERT INTO messages (id, session_id, run_id, role, content, meta_json, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, session_id, run_id, role, content, meta_json, ts),
            )
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return MessageRow.model_validate(dict(row))
    except PersistenceError:
        raise
    except (sqlite3.Error, ValidationError, ValueError, TypeError) as exc:
        raise PersistenceError("cannot create message", cause=exc) from exc


def list_messages(
    conn: sqlite3.Connection, session_id: str, *, limit: int = 20
) -> tuple[MessageRow, ...]:
    if not 1 <= limit <= 20:
        raise PersistenceError("invalid message limit")
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY rowid DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return tuple(MessageRow.model_validate(dict(row)) for row in reversed(rows))
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot list messages", cause=exc) from exc


def get_message(conn: sqlite3.Connection, message_id: str) -> MessageRow | None:
    try:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        return MessageRow.model_validate(dict(row)) if row is not None else None
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read message", cause=exc) from exc


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


def require_project(conn: sqlite3.Connection, project_id: str) -> ProjectRow:
    row = get_project(conn, project_id)
    if row is None:
        raise PersistenceError("project does not exist")
    return row


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
    status: RunStatus = "pending",
) -> RunRow:
    run_id = new_id("r")
    started_at = utc_now()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO runs "
                "(id, project_id, contract_id, mode, status, current_node, champion_model_id, "
                "forked_from_run_id, started_at, ended_at, config_json) "
                "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?)",
                (
                    run_id,
                    project_id,
                    contract_id,
                    mode,
                    status,
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


def require_run(conn: sqlite3.Connection, run_id: str) -> RunRow:
    row = get_run(conn, run_id)
    if row is None:
        raise PersistenceError("run does not exist")
    return row


def list_runs(conn: sqlite3.Connection, project_id: str) -> tuple[RunRow, ...]:
    """Newest-first, per the order the UI depends on."""
    rows = conn.execute(
        "SELECT * FROM runs WHERE project_id = ? ORDER BY started_at DESC", (project_id,)
    ).fetchall()
    return tuple(RunRow.model_validate(dict(row)) for row in rows)


def run_ancestry(conn: sqlite3.Connection, run_id: str) -> tuple[RunRow, ...]:
    """Return current run through root, rejecting incomplete or cross-project chains."""
    lineage: list[RunRow] = []
    visited: set[str] = set()
    current: str | None = run_id
    try:
        while current is not None:
            if current in visited:
                raise PersistenceError("run ancestry contains a cycle")
            visited.add(current)
            row = get_run(conn, current)
            if row is None:
                raise PersistenceError("run ancestry is incomplete")
            if lineage and row.project_id != lineage[0].project_id:
                raise PersistenceError("run ancestry crosses projects")
            lineage.append(row)
            current = row.forked_from_run_id
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read run ancestry", cause=exc) from exc
    return tuple(lineage)


def create_node_execution(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    node_name: str,
    seq: int,
    candidate_id: str | None = None,
    attempt: int = 1,
    concurrency_at_start: int | None = None,
    status: Literal["pending", "running"] = "running",
    reuse_pending: bool = False,
) -> NodeExecutionRow:
    """Create an auditable execution row; callers own reuse across repeated calls."""
    execution_id, started_at = new_id("ne"), utc_now()
    try:
        with transaction(conn):
            if reuse_pending:
                pending = conn.execute(
                    "SELECT * FROM node_executions WHERE run_id = ? AND node_name = ? "
                    "AND candidate_id IS ? AND status = 'pending' ORDER BY rowid",
                    (run_id, node_name, candidate_id),
                ).fetchall()
                if len(pending) > 1:
                    raise PersistenceError("duplicate pending execution anchors")
                if pending:
                    if pending[0]["seq"] != seq:
                        raise PersistenceError("pending execution topology mismatch")
                    if status == "running":
                        conn.execute(
                            "UPDATE node_executions SET status = 'running', "
                            "concurrency_at_start = ? WHERE id = ?",
                            (concurrency_at_start, pending[0]["id"]),
                        )
                    reused = conn.execute(
                        "SELECT * FROM node_executions WHERE id = ?", (pending[0]["id"],)
                    ).fetchone()
                    return NodeExecutionRow.model_validate(dict(reused))
            conn.execute(
                "INSERT INTO node_executions "
                "(id, run_id, candidate_id, node_name, seq, attempt, status, started_at, "
                "concurrency_at_start) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    execution_id,
                    run_id,
                    candidate_id,
                    node_name,
                    seq,
                    attempt,
                    status,
                    started_at,
                    concurrency_at_start,
                ),
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
    ended_at: str | None = None,
) -> RunRow:
    """Raises `PersistenceError` on an unknown `run_id` -- updating nothing is a bug in the
    caller, unlike reading nothing."""
    try:
        with transaction(conn):
            cursor = conn.execute(
                "UPDATE runs SET status = ?, current_node = ?, ended_at = COALESCE(?, ended_at) "
                "WHERE id = ?",
                (status, current_node, ended_at, run_id),
            )
            if cursor.rowcount == 0:
                raise PersistenceError(f"no run with id {run_id!r} to update")
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="runs.status") from exc
    return RunRow.model_validate(dict(row))


def next_node_attempt(
    conn: sqlite3.Connection, *, run_id: str, node_name: str, candidate_id: str | None
) -> int:
    try:
        (attempt,) = conn.execute(
            "SELECT COALESCE(MAX(attempt), 0) + 1 FROM node_executions "
            "WHERE run_id = ? AND node_name = ? AND candidate_id IS ?",
            (run_id, node_name, candidate_id),
        ).fetchone()
    except sqlite3.Error as exc:
        raise PersistenceError("cannot read node attempts", cause=exc) from exc
    return int(attempt)


def has_run_history(conn: sqlite3.Connection, run_id: str) -> bool:
    """A missing checkpoint may initialize only a run without durable work or score claims."""
    try:
        row = conn.execute(
            "SELECT test_scored_at, test_fingerprint FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise PersistenceError("run does not exist")
        if row["test_scored_at"] is not None or row["test_fingerprint"] is not None:
            return True
        (present,) = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM node_executions WHERE run_id = ?) "
            "OR EXISTS(SELECT 1 FROM candidates WHERE run_id = ?) "
            "OR EXISTS(SELECT 1 FROM gates WHERE run_id = ?) "
            "OR EXISTS(SELECT 1 FROM artifacts WHERE run_id = ?)",
            (run_id, run_id, run_id, run_id),
        ).fetchone()
        return bool(present)
    except sqlite3.Error as exc:
        raise PersistenceError("cannot read run history", cause=exc) from exc


def list_node_executions(conn: sqlite3.Connection, run_id: str) -> tuple[NodeExecutionRow, ...]:
    try:
        rows = conn.execute(
            "SELECT * FROM node_executions WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return tuple(NodeExecutionRow.model_validate(dict(row)) for row in rows)
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read node executions", cause=exc) from exc


def interrupt_node_execution(conn: sqlite3.Connection, execution_id: str) -> None:
    """Keep the original summary and every attempt/audit row when a process dies."""
    try:
        with transaction(conn):
            conn.execute(
                "UPDATE node_executions SET status = 'failed', error = ?, ended_at = ? "
                "WHERE id = ? AND status = 'running'",
                (
                    Failure(error_class="bug", message="process terminated").model_dump_json(),
                    utc_now(),
                    execution_id,
                ),
            )
    except sqlite3.Error as exc:
        raise PersistenceError("cannot interrupt node execution", cause=exc) from exc


def list_pending_gates(
    conn: sqlite3.Connection, *, run_id: str, execution_id: str
) -> tuple[GateRow, ...]:
    try:
        rows = conn.execute(
            "SELECT gates.* FROM gates "
            "WHERE gates.run_id = ? AND gates.node_execution_id = ? "
            "AND gates.status = 'pending' AND gates.answer_json IS NULL ORDER BY gates.rowid",
            (run_id, execution_id),
        ).fetchall()
        gates = tuple(GateRow.model_validate(dict(row)) for row in rows)
        execution = conn.execute(
            "SELECT run_id FROM node_executions WHERE id = ?", (execution_id,)
        ).fetchone()
        if execution is None or execution["run_id"] != run_id:
            raise PersistenceError("gate execution ownership mismatch")
        return gates
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read pending gates", cause=exc) from exc


def get_gate(conn: sqlite3.Connection, gate_id: str) -> GateRow | None:
    try:
        row = conn.execute("SELECT * FROM gates WHERE id = ?", (gate_id,)).fetchone()
        return GateRow.model_validate(dict(row)) if row is not None else None
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read gate", cause=exc) from exc


def list_gates(conn: sqlite3.Connection, run_id: str) -> tuple[GateRow, ...]:
    try:
        return tuple(
            GateRow.model_validate(dict(row))
            for row in conn.execute(
                "SELECT * FROM gates WHERE run_id = ? ORDER BY rowid", (run_id,)
            )
        )
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read gates", cause=exc) from exc


def create_gate(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    execution_id: str,
    question: dict[str, JsonValue],
) -> GateRow:
    """One original gate per execution/phase; revisions stay in its JSON transcript."""
    try:
        with transaction(conn):
            execution = conn.execute(
                "SELECT run_id FROM node_executions WHERE id = ?", (execution_id,)
            ).fetchone()
            if execution is None or execution["run_id"] != run_id:
                raise PersistenceError("gate execution ownership mismatch")
            rows = conn.execute(
                "SELECT * FROM gates WHERE run_id = ? AND node_execution_id = ?",
                (run_id, execution_id),
            ).fetchall()
            for existing in rows:
                gate = GateRow.model_validate(dict(existing))
                if gate.question().get("phase") == question.get("phase"):
                    return gate
            gate_id = new_id("g")
            conn.execute(
                "INSERT INTO gates VALUES (?, ?, ?, ?, NULL, 'pending', ?)",
                (gate_id, run_id, execution_id, json.dumps(question, allow_nan=False), utc_now()),
            )
            row = conn.execute("SELECT * FROM gates WHERE id = ?", (gate_id,)).fetchone()
        return GateRow.model_validate(dict(row))
    except (sqlite3.Error, ValueError) as exc:
        raise PersistenceError("cannot persist gate", cause=exc) from exc


def transition_gate(
    conn: sqlite3.Connection,
    gate: GateRow,
    *,
    status: str,
    answer_json: str | None,
    question: dict[str, JsonValue] | None = None,
) -> GateRow:
    """Compare the complete durable revision and answer, preventing stale/double writes."""
    try:
        with transaction(conn):
            changed = conn.execute(
                "UPDATE gates SET status = ?, answer_json = ?, question_json = ? "
                "WHERE id = ? AND run_id = ? AND node_execution_id = ? "
                "AND status = ? AND answer_json IS ? AND question_json = ?",
                (
                    status,
                    answer_json,
                    json.dumps(question, allow_nan=False)
                    if question is not None
                    else gate.question_json,
                    gate.id,
                    gate.run_id,
                    gate.node_execution_id,
                    gate.status,
                    gate.answer_json,
                    gate.question_json,
                ),
            )
            if changed.rowcount != 1:
                raise PersistenceError("gate answer is stale or already applied")
            row = conn.execute("SELECT * FROM gates WHERE id = ?", (gate.id,)).fetchone()
        return GateRow.model_validate(dict(row))
    except (sqlite3.Error, ValueError) as exc:
        raise PersistenceError("cannot transition gate", cause=exc) from exc


def update_run_mode(conn: sqlite3.Connection, run_id: str, mode: RunMode) -> None:
    try:
        with transaction(conn):
            changed = conn.execute("UPDATE runs SET mode = ? WHERE id = ?", (mode, run_id))
            if changed.rowcount != 1:
                raise PersistenceError("mode transition run does not exist")
    except sqlite3.Error as exc:
        raise PersistenceError("cannot transition run mode", cause=exc) from exc


def finish_node_execution(
    conn: sqlite3.Connection, execution_id: str, result: NodeResult
) -> NodeExecutionRow:
    try:
        with transaction(conn):
            cursor = conn.execute(
                "UPDATE node_executions SET status = ?, summary = ?, error = ?, ended_at = ? "
                "WHERE id = ?",
                (
                    result.status,
                    json.dumps(result.summary),
                    result.error.model_dump_json() if result.error else None,
                    utc_now(),
                    execution_id,
                ),
            )
            if cursor.rowcount == 0:
                raise PersistenceError("node execution does not exist")
            row = conn.execute(
                "SELECT * FROM node_executions WHERE id = ?", (execution_id,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise PersistenceError("cannot persist node outcome", cause=exc) from exc
    return NodeExecutionRow.model_validate(dict(row))


def ensure_candidate(
    conn: sqlite3.Connection, *, candidate_id: str, run_id: str, spec: CandidateSpec, wave: int = 1
) -> CandidateRow:
    try:
        with transaction(conn):
            row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO candidates (id, run_id, label, rationale, spec_json, "
                    "varies_from_baseline_json, wave, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (
                        candidate_id,
                        run_id,
                        spec.label,
                        spec.rationale,
                        spec.model_dump_json(),
                        json.dumps(spec.varies_from_baseline),
                        wave,
                        utc_now(),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
                ).fetchone()
            elif (
                row["run_id"] != run_id
                or row["spec_json"] != spec.model_dump_json()
                or row["wave"] != wave
            ):
                raise PersistenceError("candidate identity mismatch")
    except sqlite3.Error as exc:
        raise PersistenceError("cannot persist candidate", cause=exc) from exc
    return CandidateRow.model_validate(dict(row))


def update_candidate_status(conn: sqlite3.Connection, candidate_id: str, status: str) -> None:
    try:
        with transaction(conn):
            conn.execute("UPDATE candidates SET status = ? WHERE id = ?", (status, candidate_id))
    except sqlite3.Error as exc:
        raise PersistenceError("cannot update candidate status", cause=exc) from exc


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


# --- project memory ---------------------------------------------------------------------------


def create_memory(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    key: str,
    value: JsonValue,
    source_run_id: str | None = None,
) -> MemoryRow:
    if not key.strip():
        raise PersistenceError("project memory key must not be blank")
    try:
        validated: JsonValue = TypeAdapter(JsonValue).validate_python(value, strict=True)
        serialized = json.dumps(
            validated, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except ValueError, TypeError:
        raise PersistenceError("invalid project memory JSON") from None
    memory_id = new_id("mem")
    try:
        with transaction(conn):
            if source_run_id is not None:
                source = conn.execute(
                    "SELECT project_id FROM runs WHERE id = ?", (source_run_id,)
                ).fetchone()
                if source is None or source["project_id"] != project_id:
                    raise PersistenceError("memory provenance must belong to destination project")
            conn.execute(
                "INSERT INTO memory (id, project_id, key, value_json, source_run_id, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, project_id, key, serialized, source_run_id, utc_now()),
            )
            row = conn.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()
    except sqlite3.Error:
        raise PersistenceError("could not write project memory") from None
    return MemoryRow.model_validate(dict(row))


def list_memory(conn: sqlite3.Connection, project_id: str) -> tuple[MemoryRow, ...]:
    try:
        rows = conn.execute(
            "SELECT * FROM memory WHERE project_id = ? ORDER BY ts, rowid", (project_id,)
        ).fetchall()
        return tuple(MemoryRow.model_validate(dict(row)) for row in rows)
    except sqlite3.Error, ValidationError:
        raise PersistenceError("could not read project memory") from None


def get_model(conn: sqlite3.Connection, model_id: str) -> ModelRow | None:
    try:
        row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
        return ModelRow.model_validate(dict(row)) if row is not None else None
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot read model", cause=exc) from exc


def get_champion_model(conn: sqlite3.Connection, run_id: str) -> ModelRow | None:
    """Resolve a pointer only when its model, candidate and fitted artifact agree."""
    try:
        run = require_run(conn, run_id)
        if run.champion_model_id is None:
            return None
        model = get_model(conn, run.champion_model_id)
        if model is None or model.run_id != run_id or model.candidate_id is None:
            raise PersistenceError("invalid champion model ownership")
        _check_model_identity(conn, run_id, model.candidate_id, model.artifact_id, model.name)
        return model
    except (sqlite3.Error, ValidationError, OSError) as exc:
        raise PersistenceError("cannot resolve champion", cause=exc) from exc


def _check_model_identity(
    conn: sqlite3.Connection, run_id: str, candidate_id: str, artifact_id: str, name: str
) -> None:
    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    artifact = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if candidate is None or candidate["run_id"] != run_id:
        raise PersistenceError("model candidate ownership mismatch")
    spec = CandidateSpec.model_validate_json(candidate["spec_json"])
    if spec.estimator.name != name:
        raise PersistenceError("model estimator differs from candidate")
    if (
        artifact is None
        or artifact["kind"] != "model"
        or artifact["derived_from_test"]
        or artifact["pruned_at"] is not None
        or not Path(artifact["path"]).is_file()
    ):
        raise PersistenceError("model requires an available fitted artifact")
    current = require_run(conn, run_id)
    ancestor = current
    visited: set[str] = set()
    while ancestor.id != artifact["run_id"]:
        if ancestor.id in visited or ancestor.forked_from_run_id is None:
            raise PersistenceError("model artifact is outside run fork lineage")
        visited.add(ancestor.id)
        parent = get_run(conn, ancestor.forked_from_run_id)
        if parent is None or parent.project_id != current.project_id:
            raise PersistenceError("model fork ancestry crosses projects or is missing")
        ancestor = parent
    rows = conn.execute(
        "SELECT tc.result_json, c.id, c.spec_json FROM tool_calls tc JOIN node_executions ne "
        "ON ne.id = tc.node_execution_id JOIN candidates c ON c.id = ne.candidate_id "
        "WHERE tc.run_id = ? AND tc.tool_name = 'train_estimator'",
        (ancestor.id,),
    ).fetchall()
    for row in rows:
        if ancestor.id == run_id:
            matches = row["id"] == candidate_id
        else:
            matches = (
                CandidateSpec.model_validate_json(row["spec_json"]).spec_hash == spec.spec_hash
            )
        result = TOOL_RESULT_ADAPTER.validate_json(row["result_json"])
        if matches and result.ok and artifact_id in {str(meta.ref) for meta in result.artifacts}:
            return
    raise PersistenceError("fitted artifact lacks matching candidate training audit")


def save_champion_model(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    candidate_id: str,
    artifact_id: str,
    name: str,
    parameters: dict[str, JsonValue],
) -> ModelRow:
    """Publish model and champion pointer atomically; an identical selection is idempotent."""
    try:
        serialized = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with transaction(conn):
            _check_model_identity(conn, run_id, candidate_id, artifact_id, name)
            run = require_run(conn, run_id)
            previous = get_champion_model(conn, run_id)
            if previous is not None:
                if (
                    previous.candidate_id,
                    previous.artifact_id,
                    previous.name,
                    previous.params_json,
                ) != (candidate_id, artifact_id, name, serialized):
                    raise PersistenceError("champion replacement is forbidden")
                return previous
            if run.test_scored_at is not None:
                raise PersistenceError("new selection after test claim is forbidden")
            model_id = new_id("mo")
            conn.execute(
                "INSERT INTO models "
                "(id, run_id, candidate_id, name, params_json, artifact_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (model_id, run_id, candidate_id, name, serialized, artifact_id, utc_now()),
            )
            conn.execute("UPDATE runs SET champion_model_id = ? WHERE id = ?", (model_id, run_id))
            row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
        return ModelRow.model_validate(dict(row))
    except (sqlite3.Error, ValueError, TypeError, OSError) as exc:
        raise PersistenceError("cannot preserve champion model", cause=exc) from exc


def remember_run_outcome(
    conn: sqlite3.Connection, *, project_id: str, run_id: str, value: dict[str, JsonValue]
) -> MemoryRow:
    """Append one deterministic outcome per source run, rejecting conflicting retries."""
    key = "run_outcome"
    try:
        serialized = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
        with transaction(conn):
            source = get_run(conn, run_id)
            if source is None or source.project_id != project_id:
                raise PersistenceError("memory provenance must belong to destination project")
            rows = conn.execute(
                "SELECT * FROM memory WHERE project_id = ? AND source_run_id = ? AND key = ?",
                (project_id, run_id, key),
            ).fetchall()
            if rows:
                if len(rows) != 1 or rows[0]["value_json"] != serialized:
                    raise PersistenceError("conflicting same-run outcome memory")
                return MemoryRow.model_validate(dict(rows[0]))
            memory_id = new_id("mem")
            conn.execute(
                "INSERT INTO memory (id, project_id, key, value_json, source_run_id, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, project_id, key, serialized, run_id, utc_now()),
            )
            row = conn.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()
        return MemoryRow.model_validate(dict(row))
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise PersistenceError("cannot preserve run outcome memory", cause=exc) from exc
