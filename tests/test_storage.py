"""Chunk 06 §4.3: `ensure_run_layout` and the artifacts registry."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue
from statlab.errors import PersistenceError
from statlab.persistence.ids import parse_ts
from statlab.persistence.models import ArtifactRow, ProjectRow, RunRow
from statlab.persistence.repository import create_contract, create_run
from statlab.storage import (
    create_artifact,
    ensure_run_layout,
    get_artifact,
    list_artifacts,
    mark_pruned,
)
from statlab_contracts.artifacts import ARTIFACT_ID_PATTERN, ArtifactMeta, ArtifactRef
from statlab_contracts.contract import DataContract
from statlab_contracts.layout import RunPaths

_CONTRACT = DataContract.model_validate({"target": {"column": "y"}})


def _write_artifact_file(paths: RunPaths, ref: ArtifactRef) -> Path:
    path = paths.artifact(ref, "dataframe")
    path.write_text("fake parquet bytes")
    return path


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


def _make_artifact(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow, n: int = 1
) -> ArtifactRow:
    ref = ArtifactRef(f"a_000000000000000{n}")
    path = _write_artifact_file(run_paths, ref)
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


# --- ensure_run_layout ---------------------------------------------------------------------------


def test_ensure_run_layout_creates_root(tmp_path: Path) -> None:
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    assert paths.root.is_dir()


def test_ensure_run_layout_creates_artifacts_dir(tmp_path: Path) -> None:
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    assert paths.artifacts.is_dir()


def test_ensure_run_layout_creates_nodes_dir(tmp_path: Path) -> None:
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    assert paths.nodes.is_dir()


def test_ensure_run_layout_creates_candidates_dir(tmp_path: Path) -> None:
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    assert paths.candidates.is_dir()


def test_ensure_run_layout_creates_report_dir(tmp_path: Path) -> None:
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    assert paths.report.is_dir()


def test_ensure_run_layout_is_idempotent(tmp_path: Path) -> None:
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    ensure_run_layout(paths)
    assert paths.root.is_dir()


def test_ensure_run_layout_does_not_create_project_root(tmp_path: Path) -> None:
    """The run's own tree is created; the project root above it must already exist or the
    caller's own project-setup step is missing -- `ensure_run_layout` is not responsible for
    it. `mkdir(parents=True)` creates it anyway as a side effect of the run path, so this test
    only pins that no *extra*, sibling directory is created."""
    paths = RunPaths(data_dir=tmp_path, slug="proj", run_id="r_1")
    ensure_run_layout(paths)
    project_root = tmp_path / "proj"
    assert {p.name for p in project_root.iterdir()} == {"runs"}


# --- create_artifact / get_artifact --------------------------------------------------------------


def test_create_artifact_then_get_round_trip(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    fetched = get_artifact(migrated_db, row.id)
    assert fetched == row


def test_get_artifact_returns_none_for_unknown_id(migrated_db: sqlite3.Connection) -> None:
    assert get_artifact(migrated_db, "a_deadbeefdeadbeef") is None


def test_created_artifact_kind_round_trips(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    assert row.kind == "dataframe"


def test_created_artifact_produced_by_round_trips(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    assert row.produced_by == "load_csv"


def test_created_artifact_ts_is_parseable(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    parse_ts(row.ts)


def test_s1_create_artifact_id_matches_pattern(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S1."""
    row = _make_artifact(migrated_db, run_paths, run)
    assert ARTIFACT_ID_PATTERN.match(row.id)


def test_s2_meta_json_stored_verbatim(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S2."""
    ref = ArtifactRef("a_0000000000000002")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=1, cols=1, label="x")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="load_csv",
    )
    assert row.meta_json == meta.model_dump_json()


def test_s3_artifact_row_meta_parses_back(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S3."""
    ref = ArtifactRef("a_0000000000000003")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=5, cols=3)
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="load_csv",
    )
    assert row.meta() == meta


def test_artifact_row_meta_on_corrupt_json_raises(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    migrated_db.execute("UPDATE artifacts SET meta_json = ? WHERE id = ?", ("not json{{{", row.id))
    fetched = get_artifact(migrated_db, row.id)
    assert fetched is not None
    with pytest.raises(PersistenceError, match="invalid meta JSON"):
        fetched.meta()


def test_s4_pruned_at_none_on_creation(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S4."""
    row = _make_artifact(migrated_db, run_paths, run)
    assert row.pruned_at is None


def test_artifact_row_is_frozen(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    from pydantic import ValidationError

    row = _make_artifact(migrated_db, run_paths, run)
    with pytest.raises(ValidationError, match="Instance is frozen"):
        row.kind = "model"  # type: ignore[misc]  # deliberately mutating a frozen model to prove it raises


def test_artifact_row_rejects_unknown_column_via_extra_forbid() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ArtifactRow.model_validate(
            {
                "id": "a_1",
                "run_id": "r_1",
                "kind": "dataframe",
                "path": "/p",
                "meta_json": "{}",
                "produced_by": "x",
                "ts": "ts",
                "pruned_at": None,
                "split": None,
                "derived_from_test": False,
                "extra_field": 1,
            }
        )


# --- list_artifacts -------------------------------------------------------------------------------


def test_s5_list_artifacts_excludes_pruned_by_default(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S5."""
    row = _make_artifact(migrated_db, run_paths, run)
    mark_pruned(migrated_db, [ArtifactRef(row.id)])
    assert list_artifacts(migrated_db, run.id) == ()


def test_s6_list_artifacts_include_pruned_true(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S6."""
    row = _make_artifact(migrated_db, run_paths, run)
    mark_pruned(migrated_db, [ArtifactRef(row.id)])
    listed = list_artifacts(migrated_db, run.id, include_pruned=True)
    assert len(listed) == 1
    assert listed[0].pruned_at is not None


def test_list_artifacts_empty_for_run_with_none(
    migrated_db: sqlite3.Connection, run: RunRow
) -> None:
    assert list_artifacts(migrated_db, run.id) == ()


def test_list_artifacts_ordering(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row1 = _make_artifact(migrated_db, run_paths, run, n=1)
    row2 = _make_artifact(migrated_db, run_paths, run, n=2)
    listed = list_artifacts(migrated_db, run.id)
    assert [r.id for r in listed] == [row1.id, row2.id]


def test_list_artifacts_only_scoped_to_its_own_run(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow, project: ProjectRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    other_contract = create_contract(
        migrated_db, project_id=project.id, path=Path("/c2.json"), contract=_CONTRACT
    )
    other_run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=other_contract.id,
        mode="autonomous",
        config_json="{}",
    )
    assert list_artifacts(migrated_db, other_run.id) == ()
    assert [r.id for r in list_artifacts(migrated_db, run.id)] == [row.id]


# --- mark_pruned ------------------------------------------------------------------------------


def test_s7_mark_pruned_returns_count_and_sets_parseable_timestamp(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S7."""
    row1 = _make_artifact(migrated_db, run_paths, run, n=1)
    row2 = _make_artifact(migrated_db, run_paths, run, n=2)
    count = mark_pruned(migrated_db, [ArtifactRef(row1.id), ArtifactRef(row2.id)])
    assert count == 2
    fetched = get_artifact(migrated_db, row1.id)
    assert fetched is not None
    assert fetched.pruned_at is not None
    parse_ts(fetched.pruned_at)


def test_s8_mark_pruned_on_already_pruned_ref_is_idempotent(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S8: does not overwrite the first timestamp."""
    row = _make_artifact(migrated_db, run_paths, run)
    mark_pruned(migrated_db, [ArtifactRef(row.id)])
    first = get_artifact(migrated_db, row.id)
    assert first is not None
    second_count = mark_pruned(migrated_db, [ArtifactRef(row.id)])
    assert second_count == 0
    second = get_artifact(migrated_db, row.id)
    assert second is not None
    assert second.pruned_at == first.pruned_at


def test_mark_pruned_empty_refs_returns_zero(migrated_db: sqlite3.Connection) -> None:
    assert mark_pruned(migrated_db, []) == 0


def test_mark_pruned_mixed_pruned_and_unpruned_refs(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """One already-pruned ref and one fresh ref in the same call: the count reflects only the
    fresh one."""
    row1 = _make_artifact(migrated_db, run_paths, run, n=1)
    row2 = _make_artifact(migrated_db, run_paths, run, n=2)
    mark_pruned(migrated_db, [ArtifactRef(row1.id)])
    count = mark_pruned(migrated_db, [ArtifactRef(row1.id), ArtifactRef(row2.id)])
    assert count == 1


# --- CASCADE inherited from chunk 05 --------------------------------------------------------------


def test_s9_artifacts_cascade_on_run_delete(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """S9: inherited from chunk 05's schema; assert it still holds."""
    row = _make_artifact(migrated_db, run_paths, run)
    with migrated_db:
        migrated_db.execute("DELETE FROM runs WHERE id = ?", (run.id,))
    assert get_artifact(migrated_db, row.id) is None


def test_create_artifact_bad_run_id_raises_persistence_error(
    migrated_db: sqlite3.Connection, run_paths: RunPaths
) -> None:
    ref = ArtifactRef("a_0000000000000009")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=1, cols=1)
    with pytest.raises(PersistenceError):
        create_artifact(
            migrated_db,
            ref=ref,
            run_id="r_doesnotexist00",
            kind="dataframe",
            path=path,
            meta=meta,
            produced_by="load_csv",
        )


def test_create_artifact_with_model_kind(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref = ArtifactRef("a_0000000000000011")
    path = run_paths.artifact(ref, "model")
    path.write_text("fake joblib bytes")
    meta = ArtifactMeta(ref=ref, kind="model")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="model",
        path=path,
        meta=meta,
        produced_by="train_estimator",
    )
    assert row.kind == "model"
    assert row.path.endswith(".joblib")


def test_create_artifact_with_plot_kind(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref = ArtifactRef("a_0000000000000012")
    path = run_paths.artifact(ref, "plot")
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    meta = ArtifactMeta(ref=ref, kind="plot")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="plot",
        path=path,
        meta=meta,
        produced_by="plot_roc_curve",
    )
    assert row.kind == "plot"


def test_list_artifacts_mix_of_pruned_and_not_with_include_pruned(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row1 = _make_artifact(migrated_db, run_paths, run, n=1)
    row2 = _make_artifact(migrated_db, run_paths, run, n=2)
    mark_pruned(migrated_db, [ArtifactRef(row1.id)])
    active_only = list_artifacts(migrated_db, run.id)
    assert [r.id for r in active_only] == [row2.id]
    all_rows = list_artifacts(migrated_db, run.id, include_pruned=True)
    assert {r.id for r in all_rows} == {row1.id, row2.id}


def test_run_row_gains_test_scored_at_field_unaffected_by_chunk_06(
    migrated_db: sqlite3.Connection, run: RunRow
) -> None:
    """A regression pin: chunk 06 adds `pruned_at` to `artifacts`, not to `runs` -- confirms
    the two schema changes did not bleed into each other."""
    assert run.test_scored_at is None


def test_create_artifact_with_table_kind(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref = ArtifactRef("a_0000000000000013")
    path = run_paths.artifact(ref, "table")
    path.write_text("{}")
    meta = ArtifactMeta(ref=ref, kind="table")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="table",
        path=path,
        meta=meta,
        produced_by="rank_models",
    )
    assert row.kind == "table"
    assert row.path.endswith(".json")


def test_create_artifact_with_report_kind(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref = ArtifactRef("a_0000000000000014")
    path = run_paths.artifact(ref, "report")
    path.write_text("# Report")
    meta = ArtifactMeta(ref=ref, kind="report")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="report",
        path=path,
        meta=meta,
        produced_by="assemble_report",
    )
    assert row.kind == "report"
    assert row.path.endswith(".md")


def test_artifact_meta_with_label_round_trips(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref = ArtifactRef("a_0000000000000015")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=10, cols=4, label="cleaned train split")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="apply_imputer",
    )
    assert row.meta().label == "cleaned train split"


def test_artifact_meta_without_rows_cols_round_trips(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """A `model`/`transformer` artifact carries no row/col shape."""
    ref = ArtifactRef("a_0000000000000016")
    path = run_paths.artifact(ref, "transformer")
    path.write_text("fake joblib")
    meta = ArtifactMeta(ref=ref, kind="transformer")
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="transformer",
        path=path,
        meta=meta,
        produced_by="fit_imputer",
    )
    fetched_meta = row.meta()
    assert fetched_meta.rows is None
    assert fetched_meta.cols is None


def test_created_artifact_path_round_trips_as_string(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    ref = ArtifactRef("a_0000000000000021")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=1, cols=1)
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="load_csv",
    )
    assert row.path == str(path)


def test_get_artifact_after_mark_pruned_reflects_pruned_at_in_row(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row = _make_artifact(migrated_db, run_paths, run)
    before = get_artifact(migrated_db, row.id)
    assert before is not None
    assert before.pruned_at is None
    mark_pruned(migrated_db, [ArtifactRef(row.id)])
    after = get_artifact(migrated_db, row.id)
    assert after is not None
    assert after.pruned_at is not None


def test_two_artifacts_same_run_different_produced_by(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    row1 = _make_artifact(migrated_db, run_paths, run, n=1)
    ref2 = ArtifactRef("a_0000000000000022")
    path2 = run_paths.artifact(ref2, "dataframe")
    path2.write_text("other content")
    meta2 = ArtifactMeta(ref=ref2, kind="dataframe", rows=1, cols=1)
    row2 = create_artifact(
        migrated_db,
        ref=ref2,
        run_id=run.id,
        kind="dataframe",
        path=path2,
        meta=meta2,
        produced_by="apply_imputer",
    )
    assert row1.produced_by == "load_csv"
    assert row2.produced_by == "apply_imputer"


# --- §7.2: ref identity invariant --------------------------------------------------------------


def test_created_artifact_id_equals_path_stem(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """§7.2's required invariant: the row's own id is the ref the writer chose, and that ref
    is the path's stem -- so a lookup by the ref a tool returned always finds the row that
    owns the real file."""
    row = _make_artifact(migrated_db, run_paths, run)
    assert row.id == Path(row.path).stem


def test_created_artifact_meta_ref_equals_row_id(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """§7.2's second half of the invariant: `meta.ref` (what travels back to the LLM) agrees
    with `row.id` (what the registry indexes on)."""
    row = _make_artifact(migrated_db, run_paths, run)
    assert ArtifactMeta.model_validate_json(row.meta_json).ref == row.id


def test_create_artifact_rejects_ref_meta_ref_mismatch(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """§7.2: `ref` and `meta.ref` arrive from the same caller. Disagreeing means the caller is
    confused, not that the registry should silently prefer one over the other."""
    ref = ArtifactRef("a_0000000000000031")
    other_ref = ArtifactRef("a_0000000000000032")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=other_ref, kind="dataframe", rows=1, cols=1)
    with pytest.raises(PersistenceError, match="ref mismatch"):
        create_artifact(
            migrated_db,
            ref=ref,
            run_id=run.id,
            kind="dataframe",
            path=path,
            meta=meta,
            produced_by="load_csv",
        )


# --- R4: create_artifact split / derived_from_test ----------------------------------------------


def test_create_artifact_split_and_derived_default(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """R4: an ordinary artifact is not a split frame (`split=None`) and is not test-derived
    (`derived_from_test=False`) unless told otherwise."""
    row = _make_artifact(migrated_db, run_paths, run)
    assert row.split is None
    assert row.derived_from_test is False


def test_create_artifact_records_split_and_derived_from_test(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """R4: both fields round-trip through the registry, with `derived_from_test` mapped back
    to a real `bool` (not the raw INTEGER 0/1)."""
    ref = ArtifactRef("a_0000000000000041")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="test", rows=1, cols=1)
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="split_random",
        split="test",
        derived_from_test=True,
    )
    assert row.split == "test"
    assert row.derived_from_test is True
    fetched = get_artifact(migrated_db, row.id)
    assert fetched is not None
    assert fetched.split == "test"
    assert fetched.derived_from_test is True


def test_create_artifact_rejects_meta_split_mismatch(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """R4: if `meta.split` is set it must equal the `split` argument -- the same guard shape as
    the `meta.ref == ref` check. Trigger the disagreement the guard forbids."""
    ref = ArtifactRef("a_0000000000000042")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="train", rows=1, cols=1)
    with pytest.raises(PersistenceError, match="split mismatch"):
        create_artifact(
            migrated_db,
            ref=ref,
            run_id=run.id,
            kind="dataframe",
            path=path,
            meta=meta,
            produced_by="split_random",
            split="test",
        )


def test_create_artifact_allows_none_meta_split_with_set_split_arg(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    """R4: the guard only fires when `meta.split` is *set* and disagrees. A `None` `meta.split`
    with a concrete `split` argument is fine -- the meta simply did not carry the fact."""
    ref = ArtifactRef("a_0000000000000043")
    path = _write_artifact_file(run_paths, ref)
    meta = ArtifactMeta(ref=ref, kind="dataframe", rows=1, cols=1)  # split unset
    row = create_artifact(
        migrated_db,
        ref=ref,
        run_id=run.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="split_random",
        split="valid",
    )
    assert row.split == "valid"


def test_forked_test_artifact_keeps_split_and_derived_from_test(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow, project: ProjectRow
) -> None:
    """R4's required test: after a fork, the parent's test artifact is still test.

    The implemented design (chunk 06, PLAN.md D11): `fork_artifacts` hard-links each file into
    the child's `artifacts/` under the **same** ref and creates **no** registry rows. Refs are
    globally unique, so the registry is keyed by ref, not by `(run, ref)`; the single parent
    row carries the lineage flags, and a fork inherits them for free by referring to the same
    ref. Nothing is copied, so nothing can disagree, and `row.id == Path(row.path).stem` holds
    for the one row that exists.
    """
    from statlab.storage import fork_artifacts

    parent_ref = ArtifactRef("a_0000000000000051")
    parent_path = _write_artifact_file(run_paths, parent_ref)
    parent_meta = ArtifactMeta(ref=parent_ref, kind="dataframe", split="test", rows=1, cols=1)
    parent_row = create_artifact(
        migrated_db,
        ref=parent_ref,
        run_id=run.id,
        kind="dataframe",
        path=parent_path,
        meta=parent_meta,
        produced_by="split_random",
        split="test",
        derived_from_test=True,
    )

    child_contract = create_contract(
        migrated_db, project_id=project.id, path=Path("/c_child.json"), contract=_CONTRACT
    )
    child_run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=child_contract.id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=run.id,
    )
    child_paths = RunPaths(
        data_dir=run_paths.data_dir, slug=run_paths.slug, run_id="r_forkchild00000"
    )
    ensure_run_layout(child_paths)

    linked = fork_artifacts(run_paths, child_paths, [parent_row])
    assert linked == (ArtifactRef(parent_row.id),)

    # The linked file keeps the parent's ref as its stem and shares the parent's inode.
    child_file = child_paths.artifacts / Path(parent_row.path).name
    assert child_file.stem == parent_ref
    assert child_file.stat().st_ino == Path(parent_row.path).stat().st_ino

    # Resolving that ref yields the single parent row, still test, still test-derived.
    resolved = get_artifact(migrated_db, parent_ref)
    assert resolved is not None
    assert resolved.id == parent_ref
    assert resolved.split == "test"
    assert resolved.derived_from_test is True

    # No child rows are created: a fork links files, it does not register artifacts.
    assert list_artifacts(migrated_db, child_run.id) == ()


def test_fork_visibility_and_real_pruned_producer_closure(
    migrated_db: sqlite3.Connection, run_paths: RunPaths, run: RunRow
) -> None:
    from statlab.domains.base import NodeResult
    from statlab.graph.state import BoundaryResult, RunState
    from statlab.mcp_client import logged_calls
    from statlab.persistence.repository import (
        create_node_execution,
        finish_node_execution,
        record_tool_call,
        run_ancestry,
    )
    from statlab_contracts.envelope import ToolSuccess

    from tests.test_graph_state import node

    conn = migrated_db
    nodes = (node(), node("beta"))
    execution = create_node_execution(conn, run_id=run.id, node_name="alpha", seq=1)
    refs = tuple(ArtifactRef(f"a_000000000000001{i}") for i in range(4))
    metas = tuple(ArtifactMeta(ref=ref, kind="dataframe") for ref in refs)
    arguments: tuple[dict[str, JsonValue], ...] = (
        {"dataset": "original-dataset", "project": "original-project", "run": run.id},
        {"df": str(refs[0])},
        {"df": str(refs[1])},
    )
    for meta, tool, args in zip(
        metas[:3], ("load_csv", "transform", "abandoned"), arguments, strict=True
    ):
        create_artifact(
            conn,
            ref=meta.ref,
            run_id=run.id,
            kind=meta.kind,
            path=run_paths.artifact(meta.ref, meta.kind),
            meta=meta,
            produced_by=tool,
        )
        record_tool_call(
            conn,
            run_id=run.id,
            node_execution_id=execution.id,
            tool_name=tool,
            args_json=json.dumps(args),
            result_json=ToolSuccess(artifacts=(meta,)).model_dump_json(),
            duration_ms=1,
        )
    result = NodeResult(status="completed", artifacts=(refs[1],))
    finish_node_execution(conn, execution.id, result)
    mark_pruned(conn, (refs[0],))
    child = create_run(
        conn,
        project_id=run.project_id,
        contract_id=run.contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=run.id,
    )
    checkpoint = RunState(
        run_id=child.id,
        spine_id="synthetic",
        spine=RunState.initial(
            run_id=child.id, spine_id="synthetic", nodes=nodes, mode="autonomous"
        ).spine,
        mode="autonomous",
        cursor=1,
        shared_results=(
            BoundaryResult(node_name="alpha", execution_id=execution.id, result=result),
        ),
    )
    current = create_node_execution(conn, run_id=child.id, node_name="beta", seq=2)
    create_artifact(
        conn,
        ref=refs[3],
        run_id=child.id,
        kind="dataframe",
        path=run_paths.artifact(refs[3], "dataframe"),
        meta=metas[3],
        produced_by="child_transform",
    )
    record_tool_call(
        conn,
        run_id=child.id,
        node_execution_id=current.id,
        tool_name="child_transform",
        args_json=json.dumps({"df": str(refs[1])}),
        result_json=ToolSuccess(artifacts=(metas[3],)).model_dump_json(),
        duration_ms=1,
    )
    assert [ancestor.id for ancestor in run_ancestry(conn, child.id)] == [child.id, run.id]
    assert {row.id for row in list_artifacts(conn, child.id, checkpoint=checkpoint)} == {
        str(refs[1]),
        str(refs[3]),
    }
    assert [call.tool for call in logged_calls(conn, child.id)] == ["child_transform"]
    calls = logged_calls(conn, child.id, fork_aware=True, checkpoint=checkpoint)
    assert [call.tool for call in calls] == ["load_csv", "transform", "child_transform"]
    assert calls[0].arguments == arguments[0]
    assert calls[0].outputs == (str(refs[0]),)
    retained_row = get_artifact(conn, str(refs[1]))
    assert retained_row is not None and retained_row.run_id == run.id
    with pytest.raises(PersistenceError, match="requires a retained checkpoint"):
        logged_calls(conn, child.id, fork_aware=True)
    with pytest.raises(PersistenceError, match="ownership"):
        list_artifacts(conn, run.id, checkpoint=checkpoint)
    conn.execute("UPDATE runs SET forked_from_run_id = ? WHERE id = ?", (child.id, run.id))
    with pytest.raises(PersistenceError, match="cycle"):
        run_ancestry(conn, child.id)


def test_ancestry_missing_and_cross_project_are_domain_errors(
    migrated_db: sqlite3.Connection, run: RunRow, tmp_path: Path
) -> None:
    from statlab.persistence.repository import create_project, run_ancestry

    with pytest.raises(PersistenceError, match="incomplete"):
        run_ancestry(migrated_db, "r_missing")
    foreign = create_project(migrated_db, slug="foreign-ancestor", name="foreign")
    contract = create_contract(
        migrated_db, project_id=foreign.id, path=tmp_path / "foreign.json", contract=_CONTRACT
    )
    child = create_run(
        migrated_db,
        project_id=foreign.id,
        contract_id=contract.id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=run.id,
    )
    with pytest.raises(PersistenceError, match="crosses projects"):
        run_ancestry(migrated_db, child.id)
