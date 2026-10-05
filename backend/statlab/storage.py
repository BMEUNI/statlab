"""Run-folder creation, artifact registry, hard-link forks, prune-on-success. `PLAN.md` §10.1,
D11, D24. No pandas, no `statlab_tools.*` — the backend never opens a frame; content IO belongs
with the tools that produce it (`mcp_server/statlab_tools/artifacts.py`).
"""

from __future__ import annotations

import errno
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, ArtifactRef
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import SplitRole

from statlab.domains.base import NodeStatus
from statlab.errors import ArtifactError, PersistenceError
from statlab.persistence.db import transaction
from statlab.persistence.ids import utc_now
from statlab.persistence.models import ArtifactRow
from statlab.persistence.repository import run_ancestry

if TYPE_CHECKING:
    from statlab.graph.state import RunState


def ensure_run_layout(paths: RunPaths) -> None:
    """Create the run's directory tree. Idempotent (`exist_ok=True`) -- a resumed run
    (chunk 23) calls this again."""
    for directory in (paths.root, paths.artifacts, paths.nodes, paths.candidates, paths.report):
        directory.mkdir(parents=True, exist_ok=True)


def _wrap_integrity_error(exc: sqlite3.IntegrityError, *, constraint: str) -> PersistenceError:
    return PersistenceError(f"constraint violation ({constraint}): {exc}", cause=exc)


def _artifact_row_from(row: sqlite3.Row) -> ArtifactRow:
    """`artifacts.derived_from_test` is stored as INTEGER 0/1; map it to `bool` explicitly here
    rather than leaning on pydantic's int->bool coercion, so the model's type is honest about
    what the column means. Every artifact read goes through this one place."""
    data = dict(row)
    data["derived_from_test"] = bool(data["derived_from_test"])
    return ArtifactRow.model_validate(data)


def create_artifact(
    conn: sqlite3.Connection,
    *,
    ref: ArtifactRef,
    run_id: str,
    kind: ArtifactKind,
    path: Path,
    meta: ArtifactMeta,
    produced_by: str,
    split: SplitRole | None = None,
    derived_from_test: bool = False,
) -> ArtifactRow:
    """Register an artifact already written to disk.

    `ref` is minted by the writer (`statlab_contracts.new_artifact_ref`), not here -- an
    artifact ref is born exactly once, in the process that writes the file (§7.2). Raises
    `PersistenceError` if `meta.ref != ref`: the two arrive from the same caller, and
    disagreeing means the caller is confused, not that the registry should guess.

    `split`/`derived_from_test` (R4) carry the D32 lineage facts. If `meta.split` is set it must
    equal `split` -- the same guard shape as the `meta.ref == ref` check above, for the same
    reason: two values of the same fact arriving from one caller must not disagree.
    """
    if meta.ref != ref:
        raise PersistenceError(f"ref mismatch: meta.ref={meta.ref!r} but ref={ref!r}")
    if meta.split is not None and meta.split != split:
        raise PersistenceError(f"split mismatch: meta.split={meta.split!r} but split={split!r}")
    ts = utc_now()
    meta_json = meta.model_dump_json()
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO artifacts "
                "(id, run_id, kind, path, meta_json, produced_by, ts, pruned_at, split, "
                "derived_from_test) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    str(ref),
                    run_id,
                    kind,
                    str(path),
                    meta_json,
                    produced_by,
                    ts,
                    split,
                    int(derived_from_test),
                ),
            )
            row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (str(ref),)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise _wrap_integrity_error(exc, constraint="artifacts.run_id") from exc
    return _artifact_row_from(row)


def get_artifact(conn: sqlite3.Connection, artifact_id: str) -> ArtifactRow | None:
    row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    return _artifact_row_from(row) if row is not None else None


def list_artifacts(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    include_pruned: bool = False,
    checkpoint: RunState | None = None,
) -> tuple[ArtifactRow, ...]:
    if include_pruned:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY ts", (run_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM artifacts WHERE run_id = ? AND pruned_at IS NULL ORDER BY ts",
            (run_id,),
        ).fetchall()
    own = tuple(_artifact_row_from(row) for row in rows)
    if checkpoint is None:
        return own
    owners = validate_checkpoint_ownership(conn, run_id, checkpoint)
    inherited = tuple(
        row for row in owners if row.run_id != run_id and (include_pruned or row.pruned_at is None)
    )
    return tuple(sorted((*own, *inherited), key=lambda row: (row.ts, row.id)))


def validate_checkpoint_ownership(
    conn: sqlite3.Connection, run_id: str, checkpoint: RunState
) -> tuple[ArtifactRow, ...]:
    """Validate retained boundaries/refs against genuine same-project ancestor rows."""
    ancestry = run_ancestry(conn, run_id)
    lineage = {row.id for row in ancestry}
    if checkpoint.run_id != run_id:
        raise PersistenceError("checkpoint run ownership mismatch")
    try:
        boundaries = (
            *((boundary, None) for boundary in checkpoint.shared_results),
            *((boundary, branch) for branch in checkpoint.branches for boundary in branch.results),
            *(
                (branch.pending_result, branch)
                for branch in checkpoint.branches
                if branch.status in ("failed", "budget_exhausted")
                and branch.pending_result is not None
            ),
        )
        for boundary, branch in boundaries:
            execution = conn.execute(
                "SELECT * FROM node_executions WHERE id = ?", (boundary.execution_id,)
            ).fetchone()
            seq = next(
                i + 1
                for i, entry in enumerate(checkpoint.spine)
                if entry.name == boundary.node_name
            )
            if (
                execution is None
                or execution["run_id"] not in lineage
                or execution["node_name"] != boundary.node_name
                or execution["seq"] != seq
                or execution["status"] != boundary.result.status
                or execution["candidate_id"]
                != (branch.database_id(execution["run_id"]) if branch is not None else None)
            ):
                raise PersistenceError("checkpoint execution ownership mismatch")
            if branch is not None:
                candidate = conn.execute(
                    "SELECT run_id, spec_json FROM candidates WHERE id = ?",
                    (execution["candidate_id"],),
                ).fetchone()
                if (
                    candidate is None
                    or candidate["run_id"] != execution["run_id"]
                    or candidate["spec_json"] != branch.spec.model_dump_json()
                ):
                    raise PersistenceError("checkpoint candidate ownership mismatch")
        retained = tuple(get_artifact(conn, str(ref)) for ref in sorted(checkpoint.retained_refs))
        if any(row is None or row.run_id not in lineage for row in retained):
            raise PersistenceError("checkpoint artifact ownership mismatch")
        return tuple(row for row in retained if row is not None)
    except (sqlite3.Error, ValidationError, StopIteration) as exc:
        raise PersistenceError("cannot validate checkpoint ownership", cause=exc) from exc


def mark_pruned(conn: sqlite3.Connection, refs: Sequence[ArtifactRef]) -> int:
    """Set `pruned_at` for every ref not already pruned. Returns the count actually updated.

    Idempotent: a ref already pruned is skipped (`AND pruned_at IS NULL`), so calling this
    twice on the same ref does not overwrite the first timestamp.
    """
    if not refs:
        return 0
    ts = utc_now()
    updated = 0
    with transaction(conn):
        for ref in refs:
            cursor = conn.execute(
                "UPDATE artifacts SET pruned_at = ? WHERE id = ? AND pruned_at IS NULL",
                (ts, ref),
            )
            updated += cursor.rowcount
    return updated


def fork_artifacts(
    parent: RunPaths, child: RunPaths, artifacts: Sequence[ArtifactRow]
) -> tuple[ArtifactRef, ...]:
    """Hard-link the parent's artifacts into the child run (D11). Returns the refs linked.

    A fork keeps the parent's refs: each file is linked into the child's `artifacts/` under the
    **same** ref (refs are globally unique, so the registry is keyed by ref) and **no** registry
    rows are created, so the lineage facts (`split`, `derived_from_test`) live on the single
    parent row and are inherited for free. Because of this, listing a fork's artifacts must
    follow `forked_from_run_id` (chunk 31), and a run-deletion feature must refuse while a fork
    still references the run's artifacts, since `artifacts.run_id` cascades.

    Hard links, not copies: artifacts are immutable (PLAN.md §6.1 rule 2), so sharing an inode
    is safe and disk cost is near zero -- a 200 MB dataset forked four times still occupies
    200 MB.

    Skips artifacts already pruned in the parent (no file left to link).

    No refcounting. Hard-linked artifacts are node-boundary artifacts of a completed, halted
    parent (D17: rewind marks the parent superseded); pruned artifacts are within-node
    intermediates from a run's own execution of its own node. The two sets do not intersect, so
    a fork never prunes something it linked. Even if they did, `unlink` removes a directory
    entry, not an inode -- confirmed in both directions before this was written: linking a
    parent artifact into a fork, then unlinking either side, leaves the other readable with
    `nlink` decremented. The data survives while any link remains. A refcount would be a second
    bookkeeping system guarding something the filesystem already makes impossible.
    """
    linked: list[ArtifactRef] = []
    for row in artifacts:
        if row.pruned_at is not None:
            continue
        source = Path(row.path)
        if not source.exists():
            continue
        ref = ArtifactRef(row.id)
        destination = child.artifacts / source.name
        try:
            os.link(source, destination)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise ArtifactError(
                    str(ref),
                    f"cannot hard-link across filesystems: {source} -> {destination}",
                    cause=exc,
                ) from exc
            raise
        linked.append(ref)
    return tuple(linked)


def prune_intermediates(
    conn: sqlite3.Connection,
    paths: RunPaths,
    *,
    node_status: NodeStatus,
    produced: Sequence[ArtifactRow],
    keep: frozenset[ArtifactRef],
    keep_intermediates: bool,
) -> tuple[ArtifactRef, ...]:
    """Delete within-node intermediates after a node finishes (D24). Returns what was pruned.

    No-op unless `node_status == "completed"`. D24: never prune a failed node -- those
    intermediates are exactly what diagnosis needs -- so a failed or skipped node returns
    early. The guard lives here, not in each caller: one check in the shared function beats one
    per call site, and a caller that forgets is a silent data-loss bug.

    No-op when `keep_intermediates` is set (`STATLAB_KEEP_INTERMEDIATES`).

    Unlinks the file and sets `pruned_at`, in one transaction per artifact set.
    """
    del paths  # not needed: `produced` already carries each artifact's own path
    if node_status != "completed" or keep_intermediates:
        return ()
    if not produced:
        return ()

    to_prune = [row for row in produced if ArtifactRef(row.id) not in keep]
    if not to_prune:
        return ()

    ts = utc_now()
    pruned: list[ArtifactRef] = []
    with transaction(conn):
        for row in to_prune:
            Path(row.path).unlink(missing_ok=True)
            conn.execute("UPDATE artifacts SET pruned_at = ? WHERE id = ?", (ts, row.id))
            pruned.append(ArtifactRef(row.id))
    return tuple(pruned)
