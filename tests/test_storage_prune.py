"""Chunk 06 §4.5: `prune_intermediates`. P1-P12. P3 and P10/P11 matter most."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import get_args

import pytest
from statlab.domains.base import NodeStatus
from statlab.persistence.models import ArtifactRow, ProjectRow, RunRow
from statlab.persistence.repository import create_contract, create_run
from statlab.storage import create_artifact, ensure_run_layout, fork_artifacts, prune_intermediates
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef
from statlab_contracts.contract import DataContract
from statlab_contracts.layout import RunPaths

_CONTRACT = DataContract.model_validate({"target": {"column": "y"}})


@pytest.fixture
def run(migrated_db: sqlite3.Connection, project: ProjectRow) -> RunRow:
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=_CONTRACT
    )
    return create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )


def _make_artifacts(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow, n: int
) -> tuple[ArtifactRow, ...]:
    rows = []
    for i in range(n):
        ref = ArtifactRef(f"a_00000000000000{i:02d}")
        path = run_paths.artifact(ref, "dataframe")
        path.write_text(f"content {i}")
        meta = ArtifactMeta(ref=ref, kind="dataframe", rows=1, cols=1)
        rows.append(
            create_artifact(
                migrated_db,
                ref=ref,
                run_id=run.id,
                kind="dataframe",
                path=path,
                meta=meta,
                produced_by="fit_imputer",
            )
        )
    return tuple(rows)


def test_p1_completed_prunes_everything_not_in_keep(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P1."""
    rows = _make_artifacts(migrated_db, run_paths, run, 3)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert set(pruned) == {ArtifactRef(r.id) for r in rows}
    for row in rows:
        assert not Path(row.path).exists()


def test_p2_artifacts_in_keep_survive(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P2."""
    rows = _make_artifacts(migrated_db, run_paths, run, 2)
    keep_ref = ArtifactRef(rows[0].id)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset({keep_ref}),
        keep_intermediates=False,
    )
    assert keep_ref not in pruned
    assert Path(rows[0].path).exists()
    assert not Path(rows[1].path).exists()


def test_p3_failed_prunes_nothing(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P3: D24's core rule. Files all present, `pruned_at` all `None`."""
    rows = _make_artifacts(migrated_db, run_paths, run, 2)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="failed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert pruned == ()
    for row in rows:
        assert Path(row.path).exists()
    from statlab.storage import get_artifact

    for row in rows:
        fetched = get_artifact(migrated_db, row.id)
        assert fetched is not None
        assert fetched.pruned_at is None


def test_p4_skipped_prunes_nothing(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P4."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="skipped",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert pruned == ()
    assert Path(rows[0].path).exists()


def test_p5_awaiting_user_prunes_nothing(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P5: a suspended node may resume and need its intermediates."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="awaiting_user",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert pruned == ()
    assert Path(rows[0].path).exists()


@pytest.mark.parametrize("status", get_args(NodeStatus))
def test_p6_only_completed_prunes(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow, status: NodeStatus
) -> None:
    """P6: derived from `get_args(NodeStatus)`. Adding a status forces a decision here."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status=status,
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    if status == "completed":
        assert pruned == (ArtifactRef(rows[0].id),)
    else:
        assert pruned == ()


def test_p7_keep_intermediates_true_prunes_nothing_even_when_completed(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P7."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=True,
    )
    assert pruned == ()
    assert Path(rows[0].path).exists()


def test_p8_empty_produced_returns_empty_no_error(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P8."""
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=(),
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert pruned == ()


def test_p9_keep_ref_not_in_produced_is_ignored(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P9: not an error -- the engine may pass the whole node-boundary set."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    phantom = ArtifactRef("a_ffffffffffffffff")
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset({phantom}),
        keep_intermediates=False,
    )
    assert pruned == (ArtifactRef(rows[0].id),)


def test_p9b_everything_produced_is_kept_prunes_nothing(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """The `to_prune` list can end up empty even on a `completed` node, when every produced
    artifact is in `keep` -- e.g. a node that only produces the one frame it hands forward."""
    rows = _make_artifacts(migrated_db, run_paths, run, 2)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset(ArtifactRef(r.id) for r in rows),
        keep_intermediates=False,
    )
    assert pruned == ()
    for row in rows:
        assert Path(row.path).exists()


def test_p10_fork_then_prune_child_parent_survives(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P10: the D11xD24 interaction. Assert the parent's file is readable after the child
    prunes its link."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    child_paths = RunPaths(
        data_dir=run_paths.data_dir, slug=run_paths.slug, run_id="r_child000000000"
    )
    ensure_run_layout(child_paths)
    fork_artifacts(run_paths, child_paths, rows)

    child_file = child_paths.artifacts / Path(rows[0].path).name
    child_row = rows[0].model_copy(update={"id": "a_0000000000000cc1", "path": str(child_file)})
    prune_intermediates(
        migrated_db,
        child_paths,
        node_status="completed",
        produced=(child_row,),
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert Path(rows[0].path).exists()
    assert Path(rows[0].path).read_text() == "content 0"


def test_p11_fork_then_prune_parent_child_survives(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """P11: the reverse. Together with P10, why there is no refcount."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    child_paths = RunPaths(
        data_dir=run_paths.data_dir, slug=run_paths.slug, run_id="r_child000000001"
    )
    ensure_run_layout(child_paths)
    fork_artifacts(run_paths, child_paths, rows)

    prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    child_file = child_paths.artifacts / Path(rows[0].path).name
    assert child_file.exists()
    assert child_file.read_text() == "content 0"


def test_p12_pruning_is_transactional(
    migrated_db: sqlite3.Connection,
    run_paths: RunPaths,
    run: RunRow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P12: patch `Path.unlink` to raise on the second artifact; assert no `pruned_at` was
    committed."""
    rows = _make_artifacts(migrated_db, run_paths, run, 2)
    real_unlink = Path.unlink
    call_count = {"n": 0}

    def failing_unlink(self: Path, *, missing_ok: bool = False) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated failure on second unlink")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with pytest.raises(OSError, match="simulated failure"):
        prune_intermediates(
            migrated_db,
            run_paths,
            node_status="completed",
            produced=rows,
            keep=frozenset(),
            keep_intermediates=False,
        )

    from statlab.storage import get_artifact

    for row in rows:
        fetched = get_artifact(migrated_db, row.id)
        assert fetched is not None
        assert fetched.pruned_at is None


def test_prune_multiple_kept_refs_among_many_produced(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """Two of four produced artifacts are kept; exactly the other two prune."""
    rows = _make_artifacts(migrated_db, run_paths, run, 4)
    keep = frozenset({ArtifactRef(rows[0].id), ArtifactRef(rows[2].id)})
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=keep,
        keep_intermediates=False,
    )
    assert set(pruned) == {ArtifactRef(rows[1].id), ArtifactRef(rows[3].id)}
    assert Path(rows[0].path).exists()
    assert not Path(rows[1].path).exists()
    assert Path(rows[2].path).exists()
    assert not Path(rows[3].path).exists()


def test_prune_missing_file_on_disk_still_marks_pruned_at(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """`Path.unlink(missing_ok=True)` tolerates a file already absent -- the row still gets
    `pruned_at` set rather than the whole call failing on a file that vanished some other way."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    Path(rows[0].path).unlink()
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert pruned == (ArtifactRef(rows[0].id),)
    from statlab.storage import get_artifact

    fetched = get_artifact(migrated_db, rows[0].id)
    assert fetched is not None
    assert fetched.pruned_at is not None


def test_prune_returns_refs_in_produced_order(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    rows = _make_artifacts(migrated_db, run_paths, run, 3)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="completed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=False,
    )
    assert pruned == tuple(ArtifactRef(r.id) for r in rows)


def test_prune_keep_intermediates_and_failed_together_still_no_op(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """Both guards independently forbid pruning; combined, still nothing prunes."""
    rows = _make_artifacts(migrated_db, run_paths, run, 1)
    pruned = prune_intermediates(
        migrated_db,
        run_paths,
        node_status="failed",
        produced=rows,
        keep=frozenset(),
        keep_intermediates=True,
    )
    assert pruned == ()
    assert Path(rows[0].path).exists()
