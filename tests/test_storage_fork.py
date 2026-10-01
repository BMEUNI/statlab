"""Chunk 06 §4.4: `fork_artifacts`. F1-F9."""

from __future__ import annotations

import errno
import os
import sqlite3
from pathlib import Path

import pytest
from statlab.errors import ArtifactError
from statlab.persistence.models import ArtifactRow, ProjectRow, RunRow
from statlab.persistence.repository import create_contract, create_run
from statlab.storage import create_artifact, ensure_run_layout, fork_artifacts
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


def _child_paths(run_paths: RunPaths, run_id: str) -> RunPaths:
    child = RunPaths(data_dir=run_paths.data_dir, slug=run_paths.slug, run_id=run_id)
    ensure_run_layout(child)
    return child


def _make_artifact(
    migrated_db: sqlite3.Connection,
    run_paths: RunPaths,
    run: RunRow,
    content: bytes = b"parent bytes",
) -> ArtifactRow:
    ref = ArtifactRef("a_0000000000000001")
    path = run_paths.artifact(ref, "dataframe")
    path.write_bytes(content)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=1, cols=1)
    return create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="load_csv",
    )


def test_f1_linked_file_is_same_inode(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F1."""
    row = _make_artifact(migrated_db, run_paths, run)
    child = _child_paths(run_paths, "r_child0000000000")
    fork_artifacts(run_paths, child, [row])
    parent_stat = Path(row.path).stat()
    child_stat = (child.artifacts / Path(row.path).name).stat()
    assert parent_stat.st_ino == child_stat.st_ino
    assert parent_stat.st_nlink == 2


def test_f2_fork_keeps_parent_refs(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F2: a fork keeps the parent's refs (D11). Refs are globally unique (64 random bits), so
    the registry is keyed by ref, not by `(run, ref)`. `fork_artifacts` hard-links each file
    into the child under the **same** ref and creates no registry rows; the lineage facts live
    on the single parent row and a fork inherits them by referring to that same ref. The
    returned refs are therefore the parents' own ids -- they identify which files were linked,
    not new child artifacts."""
    row = _make_artifact(migrated_db, run_paths, run)
    child = _child_paths(run_paths, "r_child0000000000")
    linked = fork_artifacts(run_paths, child, [row])
    assert linked == (ArtifactRef(row.id),)


def test_f3_content_is_identical(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F3."""
    row = _make_artifact(migrated_db, run_paths, run, content=b"exact bytes 123")
    child = _child_paths(run_paths, "r_child0000000000")
    fork_artifacts(run_paths, child, [row])
    child_file = child.artifacts / Path(row.path).name
    assert child_file.read_bytes() == b"exact bytes 123"


def test_f4_pruned_parent_artifacts_are_skipped(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F4."""
    row = _make_artifact(migrated_db, run_paths, run)
    Path(row.path).unlink()
    pruned_row = row.model_copy(update={"pruned_at": "2025-01-01T00:00:00+00:00"})
    child = _child_paths(run_paths, "r_child0000000000")
    linked = fork_artifacts(run_paths, child, [pruned_row])
    assert linked == ()
    assert list(child.artifacts.iterdir()) == []


def test_f4b_missing_file_without_pruned_at_is_also_skipped(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """A file missing on disk with no `pruned_at` set (should not normally happen, but the
    function checks existence directly rather than trusting the flag alone) is still skipped
    rather than raising."""
    row = _make_artifact(migrated_db, run_paths, run)
    Path(row.path).unlink()
    child = _child_paths(run_paths, "r_child0000000000")
    linked = fork_artifacts(run_paths, child, [row])
    assert linked == ()


def test_f5_forking_twice_from_one_parent(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F5."""
    row = _make_artifact(migrated_db, run_paths, run)
    child1 = _child_paths(run_paths, "r_child1000000000")
    child2 = _child_paths(run_paths, "r_child2000000000")
    fork_artifacts(run_paths, child1, [row])
    fork_artifacts(run_paths, child2, [row])
    assert Path(row.path).stat().st_nlink == 3
    assert (child1.artifacts / Path(row.path).name).read_bytes() == b"parent bytes"
    assert (child2.artifacts / Path(row.path).name).read_bytes() == b"parent bytes"


def test_f6_forking_a_fork(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F6: lineage of three, all readable."""
    row = _make_artifact(migrated_db, run_paths, run)
    child = _child_paths(run_paths, "r_child0000000000")
    fork_artifacts(run_paths, child, [row])
    grandchild_row = row.model_copy(update={"path": str(child.artifacts / Path(row.path).name)})
    grandchild = _child_paths(run_paths, "r_grandchild00000")
    fork_artifacts(child, grandchild, [grandchild_row])

    assert Path(row.path).read_bytes() == b"parent bytes"
    assert (child.artifacts / Path(row.path).name).read_bytes() == b"parent bytes"
    assert (grandchild.artifacts / Path(row.path).name).read_bytes() == b"parent bytes"
    assert Path(row.path).stat().st_nlink == 3


def test_f7_exdev_wraps_in_artifact_error(
    migrated_db: sqlite3.Connection,
    run_paths: RunPaths,
    run: RunRow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: monkeypatch `os.link` to raise `OSError(errno.EXDEV, ...)`; both paths appear in
    the message."""
    row = _make_artifact(migrated_db, run_paths, run)
    child = _child_paths(run_paths, "r_child0000000000")

    def fake_link(src: object, dst: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "link", fake_link)
    with pytest.raises(ArtifactError) as exc_info:
        fork_artifacts(run_paths, child, [row])
    message = str(exc_info.value)
    assert str(run_paths.artifacts) in message or row.path in message
    assert str(child.artifacts) in message


def test_f7b_non_exdev_oserror_propagates_unwrapped(
    migrated_db: sqlite3.Connection,
    run_paths: RunPaths,
    run: RunRow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-EXDEV `OSError` (e.g. permission denied) is not disguised as the cross-filesystem
    case -- it propagates as-is, since wrapping it under the EXDEV message would misdiagnose
    the real cause."""
    row = _make_artifact(migrated_db, run_paths, run)
    child = _child_paths(run_paths, "r_child0000000000")

    def fake_link(src: object, dst: object) -> None:
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(os, "link", fake_link)
    with pytest.raises(OSError) as exc_info:
        fork_artifacts(run_paths, child, [row])
    assert exc_info.value.errno == errno.EACCES


def test_f8_empty_artifact_list(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F8: returns `()`, creates no files."""
    child = _child_paths(run_paths, "r_child0000000000")
    linked = fork_artifacts(run_paths, child, [])
    assert linked == ()
    assert list(child.artifacts.iterdir()) == []


@pytest.mark.skip(reason="F9: writing 200 MB is unnecessary -- nlink proves no bytes are copied")
def test_f9_200mb_forked_four_times(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """F9, skipped by default per spec: the claim is that no bytes are copied, and `nlink`
    (already proven by F5 at a smaller scale) proves that without writing 200 MB."""


def test_fork_multiple_artifacts_at_once(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref1 = ArtifactRef("a_0000000000000001")
    ref2 = ArtifactRef("a_0000000000000002")
    path1 = run_paths.artifact(ref1, "dataframe")
    path2 = run_paths.artifact(ref2, "dataframe")
    path1.write_bytes(b"one")
    path2.write_bytes(b"two")
    meta1 = ArtifactMeta(ref=ref1, kind="dataframe", rows=1, cols=1)
    meta2 = ArtifactMeta(ref=ref2, kind="dataframe", rows=1, cols=1)
    row1 = create_artifact(
        migrated_db,
        ref=ref1,
        run_id=run.id,
        kind="dataframe",
        path=path1,
        meta=meta1,
        produced_by="a",
    )
    row2 = create_artifact(
        migrated_db,
        ref=ref2,
        run_id=run.id,
        kind="dataframe",
        path=path2,
        meta=meta2,
        produced_by="b",
    )
    child = _child_paths(run_paths, "r_child0000000000")
    linked = fork_artifacts(run_paths, child, [row1, row2])
    assert set(linked) == {ArtifactRef(row1.id), ArtifactRef(row2.id)}
    assert (child.artifacts / "a_0000000000000001.parquet").read_bytes() == b"one"
    assert (child.artifacts / "a_0000000000000002.parquet").read_bytes() == b"two"


def test_fork_mixed_pruned_and_active_artifacts(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """One pruned, one active, in the same call -- only the active one links."""
    active = _make_artifact(migrated_db, run_paths, run, content=b"active")
    pruned_ref = ArtifactRef("a_0000000000000099")
    pruned_path = run_paths.artifact(pruned_ref, "dataframe")
    pruned_path.write_bytes(b"pruned")
    pruned_meta = ArtifactMeta(ref=pruned_ref, kind="dataframe", rows=1, cols=1)
    pruned_row = create_artifact(
        migrated_db,
        ref=pruned_ref,
        run_id=run.id,
        kind="dataframe",
        path=pruned_path,
        meta=pruned_meta,
        produced_by="x",
    )
    pruned_path.unlink()
    pruned_row = pruned_row.model_copy(update={"pruned_at": "2025-01-01T00:00:00+00:00"})

    child = _child_paths(run_paths, "r_child0000000000")
    linked = fork_artifacts(run_paths, child, [active, pruned_row])
    assert linked == (ArtifactRef(active.id),)
