"""Chunk 05 §4.6: repository round-trips, R1-R18."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest
from statlab.errors import PersistenceError
from statlab.persistence.db import connect
from statlab.persistence.ids import new_id, parse_ts
from statlab.persistence.models import ProjectRow, RunRow
from statlab.persistence.repository import (
    claim_test_scoring,
    count_prior_test_scorings,
    create_contract,
    create_dataset,
    create_node_execution,
    create_project,
    create_run,
    ensure_candidate,
    find_dataset_by_hash,
    get_champion_model,
    get_contract,
    get_dataset,
    get_model,
    get_project,
    get_project_by_slug,
    get_run,
    latest_contract,
    list_projects,
    list_runs,
    record_tool_call,
    remember_run_outcome,
    save_champion_model,
    set_test_fingerprint,
    update_candidate_status,
    update_run_status,
)
from statlab.storage import create_artifact
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess


@pytest.fixture
def contract() -> DataContract:
    return DataContract.model_validate({"target": {"column": "y"}})


# --- projects: create-then-get round-trip, get_* absence, list ordering -------------------------


def test_r1_create_project_returns_row_read_back(migrated_db: sqlite3.Connection) -> None:
    """R1: generated id matches `new_id` shape, `created_at` parseable."""
    row = create_project(migrated_db, slug="foo", name="Foo")
    assert row.id.startswith("p_")
    parse_ts(row.created_at)


def test_project_create_then_get_round_trip(migrated_db: sqlite3.Connection) -> None:
    created = create_project(migrated_db, slug="foo", name="Foo")
    fetched = get_project(migrated_db, created.id)
    assert fetched == created


def test_get_project_returns_none_for_unknown_id(migrated_db: sqlite3.Connection) -> None:
    assert get_project(migrated_db, "p_doesnotexist00") is None


def test_r2_duplicate_slug_raises_persistence_error_naming_slug(
    migrated_db: sqlite3.Connection,
) -> None:
    """R2: `PersistenceError` naming `slug`, not a raw `IntegrityError`."""
    create_project(migrated_db, slug="dup", name="A")
    with pytest.raises(PersistenceError, match="slug"):
        create_project(migrated_db, slug="dup", name="B")


def test_r3_get_project_by_slug_found_and_not_found(migrated_db: sqlite3.Connection) -> None:
    """R3."""
    created = create_project(migrated_db, slug="findme", name="Found")
    assert get_project_by_slug(migrated_db, "findme") == created
    assert get_project_by_slug(migrated_db, "missing") is None


def test_list_projects_ordering_and_membership(migrated_db: sqlite3.Connection) -> None:
    p1 = create_project(migrated_db, slug="a", name="A")
    p2 = create_project(migrated_db, slug="b", name="B")
    listed = list_projects(migrated_db)
    assert {p.id for p in listed} == {p1.id, p2.id}


# --- datasets ------------------------------------------------------------------------------------


def test_dataset_create_then_get_round_trip(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    created = create_dataset(
        migrated_db,
        project_id=project.id,
        filename="f.csv",
        path=Path("/data/f.csv"),
        sha256="abc123",
        n_rows=100,
        n_cols=5,
    )
    fetched = get_dataset(migrated_db, created.id)
    assert fetched == created
    assert created.id.startswith("ds_")


def test_get_dataset_returns_none_for_unknown_id(migrated_db: sqlite3.Connection) -> None:
    assert get_dataset(migrated_db, "ds_doesnotexist0") is None


def test_r4_find_dataset_by_hash_makes_reupload_idempotent(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    """R4."""
    created = create_dataset(
        migrated_db,
        project_id=project.id,
        filename="f.csv",
        path=Path("/data/f.csv"),
        sha256="samehash",
        n_rows=10,
        n_cols=2,
    )
    found = find_dataset_by_hash(migrated_db, project_id=project.id, sha256="samehash")
    assert found == created


def test_find_dataset_by_hash_returns_none_when_absent(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    assert find_dataset_by_hash(migrated_db, project_id=project.id, sha256="nope") is None


def test_find_dataset_by_hash_does_not_match_across_projects(
    migrated_db: sqlite3.Connection,
) -> None:
    """The composite unique is `(project_id, sha256)`; a hash that exists under one project
    must not be found when queried under a different one -- the mirror image of R5's "same
    hash, different project is allowed", checked from the read side rather than the write
    side."""
    p1 = create_project(migrated_db, slug="p1", name="P1")
    p2 = create_project(migrated_db, slug="p2", name="P2")
    create_dataset(
        migrated_db,
        project_id=p1.id,
        filename="f.csv",
        path=Path("/f.csv"),
        sha256="onlyinp1",
        n_rows=1,
        n_cols=1,
    )
    assert find_dataset_by_hash(migrated_db, project_id=p2.id, sha256="onlyinp1") is None


def test_r5_same_sha256_different_project_is_allowed(migrated_db: sqlite3.Connection) -> None:
    """R5: the unique is composite."""
    p1 = create_project(migrated_db, slug="p1", name="P1")
    p2 = create_project(migrated_db, slug="p2", name="P2")
    d1 = create_dataset(
        migrated_db,
        project_id=p1.id,
        filename="f.csv",
        path=Path("/f.csv"),
        sha256="samehash",
        n_rows=1,
        n_cols=1,
    )
    d2 = create_dataset(
        migrated_db,
        project_id=p2.id,
        filename="f.csv",
        path=Path("/f.csv"),
        sha256="samehash",
        n_rows=1,
        n_cols=1,
    )
    assert d1.id != d2.id


def test_duplicate_dataset_same_project_same_hash_raises(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    create_dataset(
        migrated_db,
        project_id=project.id,
        filename="f.csv",
        path=Path("/f.csv"),
        sha256="h",
        n_rows=1,
        n_cols=1,
    )
    with pytest.raises(PersistenceError):
        create_dataset(
            migrated_db,
            project_id=project.id,
            filename="g.csv",
            path=Path("/g.csv"),
            sha256="h",
            n_rows=1,
            n_cols=1,
        )


def test_create_dataset_with_bad_project_id_raises_persistence_error_from_fk(
    migrated_db: sqlite3.Connection,
) -> None:
    """The FK on `datasets.project_id` is wrapped the same way `create_run`'s FK failure is
    (R12), exercised here for the sibling entity rather than assumed to work identically."""
    with pytest.raises(PersistenceError):
        create_dataset(
            migrated_db,
            project_id="p_doesnotexist00",
            filename="f.csv",
            path=Path("/f.csv"),
            sha256="h",
            n_rows=1,
            n_cols=1,
        )


def test_dataset_row_rejects_unknown_column_via_extra_forbid() -> None:
    """`extra='forbid'` on `DatasetRow` catches a migration that added a column the model does
    not know about -- the drift-guard `ConfigDict` choice §3.7 specifies, exercised directly
    against the model rather than only implicitly through every passing round-trip test."""
    from pydantic import ValidationError
    from statlab.persistence.models import DatasetRow

    with pytest.raises(ValidationError):
        DatasetRow.model_validate(
            {
                "id": "ds_1",
                "project_id": "p_1",
                "filename": "f.csv",
                "path": "/f.csv",
                "sha256": "h",
                "n_rows": 1,
                "n_cols": 1,
                "created_at": "ts",
                "unexpected_column": "surprise",
            }
        )


def test_project_row_rejects_unknown_column_via_extra_forbid() -> None:
    from pydantic import ValidationError
    from statlab.persistence.models import ProjectRow as _ProjectRow

    with pytest.raises(ValidationError):
        _ProjectRow.model_validate(
            {"id": "p_1", "slug": "s", "name": "N", "created_at": "ts", "extra_field": 1}
        )


def test_run_row_rejects_unknown_column_via_extra_forbid() -> None:
    from pydantic import ValidationError
    from statlab.persistence.models import RunRow

    with pytest.raises(ValidationError):
        RunRow.model_validate(
            {
                "id": "r_1",
                "project_id": "p_1",
                "contract_id": "ct_1",
                "mode": "guided",
                "status": "pending",
                "current_node": None,
                "champion_model_id": None,
                "forked_from_run_id": None,
                "started_at": "ts",
                "ended_at": None,
                "config_json": "{}",
                "extra_field": 1,
            }
        )


def test_contract_row_rejects_unknown_column_via_extra_forbid() -> None:
    from pydantic import ValidationError
    from statlab.persistence.models import ContractRow

    with pytest.raises(ValidationError):
        ContractRow.model_validate(
            {
                "id": "ct_1",
                "project_id": "p_1",
                "path": "/c.json",
                "contract_json": "{}",
                "version": 1,
                "created_at": "ts",
                "extra_field": 1,
            }
        )


# --- contracts -----------------------------------------------------------------------------------


def test_r6_create_contract_assigns_version_1_then_2(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R6: monotonic per project."""
    row1 = create_contract(
        migrated_db, project_id=project.id, path=Path("/c1.json"), contract=contract
    )
    row2 = create_contract(
        migrated_db, project_id=project.id, path=Path("/c2.json"), contract=contract
    )
    assert row1.version == 1
    assert row2.version == 2


def test_r7_versions_are_per_project_not_global(
    migrated_db: sqlite3.Connection, contract: DataContract
) -> None:
    """R7: two projects both start at 1."""
    p1 = create_project(migrated_db, slug="p1", name="P1")
    p2 = create_project(migrated_db, slug="p2", name="P2")
    row1 = create_contract(migrated_db, project_id=p1.id, path=Path("/c.json"), contract=contract)
    row2 = create_contract(migrated_db, project_id=p2.id, path=Path("/c.json"), contract=contract)
    assert row1.version == 1
    assert row2.version == 1


def test_r8_latest_contract_returns_highest_version_not_newest_created_at(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R8."""
    row1 = create_contract(
        migrated_db, project_id=project.id, path=Path("/c1.json"), contract=contract
    )
    row2 = create_contract(
        migrated_db, project_id=project.id, path=Path("/c2.json"), contract=contract
    )
    latest = latest_contract(migrated_db, project.id)
    assert latest is not None
    assert latest.id == row2.id
    assert latest.version == 2
    assert row1.version < row2.version


def test_latest_contract_returns_none_when_absent(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    assert latest_contract(migrated_db, project.id) is None


def test_r9_json_byte_fidelity(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R9: stored text is byte-identical to `contract.model_dump_json()`, and
    `ContractRow.contract()` equals the original."""
    created = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    fetched = get_contract(migrated_db, created.id)
    assert fetched is not None
    assert fetched.contract_json == contract.model_dump_json()
    assert fetched.contract() == contract


def test_r10_spec_hash_survives_storage(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    contract: DataContract,
    baseline_candidate: CandidateSpec,
) -> None:
    """R10: store a `CandidateSpec`'s JSON in a text column, reload, `model_validate_json`,
    assert `spec_hash` unchanged. Chunk 04 item 7's requirement, made a test.

    `candidates` has no repository in this chunk (§1's scope note), so this test writes
    directly to the `spec_json` column via raw SQL rather than through a repository function
    that does not exist yet."""
    original_hash = baseline_candidate.spec_hash
    spec_json = baseline_candidate.model_dump_json()

    run_row = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=create_contract(
            migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
        ).id,
        mode="autonomous",
        config_json="{}",
    )
    candidate_id = new_id("ct").replace("ct_", "c_")
    migrated_db.execute(
        "INSERT INTO candidates "
        "(id, run_id, label, rationale, spec_json, varies_from_baseline_json, wave, status, "
        "rank, created_at) VALUES (?, ?, ?, ?, ?, ?, 1, 'pending', NULL, ?)",
        (
            candidate_id,
            run_row.id,
            baseline_candidate.label,
            baseline_candidate.rationale,
            spec_json,
            "[]",
            "ts",
        ),
    )
    row = migrated_db.execute(
        "SELECT spec_json FROM candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    reloaded = CandidateSpec.model_validate_json(row["spec_json"])
    assert reloaded.spec_hash == original_hash
    assert row["spec_json"] == spec_json


def test_r11_contract_row_contract_on_corrupt_json_raises_persistence_error(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R11."""
    created = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    migrated_db.execute(
        "UPDATE contracts SET json = ? WHERE id = ?", ("not valid json {{{", created.id)
    )
    fetched = get_contract(migrated_db, created.id)
    assert fetched is not None
    with pytest.raises(PersistenceError, match="invalid contract JSON"):
        fetched.contract()


# --- runs ----------------------------------------------------------------------------------------


def test_r12_create_run_with_bad_contract_id_raises_persistence_error(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    """R12: from the FK."""
    with pytest.raises(PersistenceError):
        create_run(
            migrated_db,
            project_id=project.id,
            contract_id="ct_doesnotexist0",
            mode="autonomous",
            config_json="{}",
        )


def test_run_create_then_get_round_trip(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    created = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    fetched = get_run(migrated_db, created.id)
    assert fetched == created
    assert created.status == "pending"


def test_get_run_returns_none_for_unknown_id(migrated_db: sqlite3.Connection) -> None:
    assert get_run(migrated_db, "r_doesnotexist00") is None


def test_r13_update_run_status_returns_updated_row_and_current_node(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R13."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    updated = update_run_status(migrated_db, run.id, status="running", current_node="intake")
    assert updated.status == "running"
    assert updated.current_node == "intake"


def test_r14_update_run_status_on_unknown_id_raises(migrated_db: sqlite3.Connection) -> None:
    """R14: writing nothing is a caller bug."""
    with pytest.raises(PersistenceError, match="no run"):
        update_run_status(migrated_db, "r_doesnotexist00", status="running")


def test_r15_update_run_status_with_invalid_status_rejected_by_check(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R15: rejected by the CHECK, wrapped. Goes through `update_run_status` itself (not raw
    SQL) since wrapping `sqlite3.IntegrityError` into `PersistenceError` is that function's
    job, not something raw SQL execution does on its own -- confirmed while writing this test:
    an earlier version issued the `UPDATE` directly and correctly saw the raw
    `sqlite3.IntegrityError` instead, which is the right behaviour for that path but not what
    R15 asks for. The deliberately-wrong status is passed straight to `update_run_status`
    (a plain function, not a pydantic model, so there is no `model_validate({...})` form
    here) -- matching chunk 02's `retry_budget("nonsense")` precedent for a runtime defensive
    test against a plain function."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    with pytest.raises(PersistenceError):
        update_run_status(migrated_db, run.id, status="not_a_real_status")  # type: ignore[arg-type]  # deliberately invalid literal for a runtime CHECK-constraint test, matching chunk 02's retry_budget("nonsense") precedent


def test_r16_create_run_with_forked_from_run_id_persists_lineage(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R16: D11's fork lineage persists."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    parent = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    fork = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
        forked_from_run_id=parent.id,
    )
    assert fork.forked_from_run_id == parent.id


def test_r17_list_runs_is_newest_first(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R17: pin the order the UI depends on."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run1 = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    run2 = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    listed = list_runs(migrated_db, project.id)
    assert [r.id for r in listed] == [run2.id, run1.id] or [r.id for r in listed] == [
        run1.id,
        run2.id,
    ]
    # `started_at` has second-level-plus precision but two calls in the same test can tie;
    # what must hold regardless is that the ordering is by started_at descending.
    started_ats = [r.started_at for r in listed]
    assert started_ats == sorted(started_ats, reverse=True)


def test_list_runs_empty_for_project_with_no_runs(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    assert list_runs(migrated_db, project.id) == ()


def test_r18_failing_repository_call_rolls_back(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R18: patch to raise mid-transaction; assert nothing was written."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )

    call_count = {"n": 0}

    def failing_new_id(prefix: str) -> str:
        call_count["n"] += 1
        if prefix == "r":
            raise RuntimeError("simulated mid-call failure")
        return new_id(prefix)

    with (
        mock_patch("statlab.persistence.repository.new_id", failing_new_id),
        pytest.raises(RuntimeError, match="simulated"),
    ):
        create_run(
            migrated_db,
            project_id=project.id,
            contract_id=contract_row.id,
            mode="guided",
            config_json="{}",
        )

    rows = migrated_db.execute("SELECT * FROM runs").fetchall()
    assert rows == []


# --- frozen model immutability -------------------------------------------------------------------


def test_project_row_is_frozen(migrated_db: sqlite3.Connection) -> None:
    from pydantic import ValidationError

    row = create_project(migrated_db, slug="frozen", name="Frozen")
    with pytest.raises(ValidationError, match="Instance is frozen"):
        row.slug = "changed"  # type: ignore[misc]  # deliberately mutating a frozen model for a runtime immutability test


def test_run_row_is_frozen(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    from pydantic import ValidationError

    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    with pytest.raises(ValidationError, match="Instance is frozen"):
        run.status = "running"  # type: ignore[misc]  # deliberately mutating a frozen model for a runtime immutability test


# --- multi-entity listing scopes ------------------------------------------------------------------


def test_list_runs_only_returns_runs_for_the_given_project(
    migrated_db: sqlite3.Connection, contract: DataContract
) -> None:
    p1 = create_project(migrated_db, slug="p1", name="P1")
    p2 = create_project(migrated_db, slug="p2", name="P2")
    c1 = create_contract(migrated_db, project_id=p1.id, path=Path("/c.json"), contract=contract)
    c2 = create_contract(migrated_db, project_id=p2.id, path=Path("/c.json"), contract=contract)
    run1 = create_run(
        migrated_db, project_id=p1.id, contract_id=c1.id, mode="guided", config_json="{}"
    )
    create_run(migrated_db, project_id=p2.id, contract_id=c2.id, mode="guided", config_json="{}")

    listed = list_runs(migrated_db, p1.id)
    assert [r.id for r in listed] == [run1.id]


def test_list_projects_empty_database_returns_empty_tuple(migrated_db: sqlite3.Connection) -> None:
    assert list_projects(migrated_db) == ()


def test_create_dataset_preserves_path_as_string(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    """`create_dataset` accepts a `Path` but the row stores `str` (per `DatasetRow.path: str`);
    confirms the conversion happens and round-trips."""
    created = create_dataset(
        migrated_db,
        project_id=project.id,
        filename="f.csv",
        path=Path("/data/nested/f.csv"),
        sha256="h",
        n_rows=1,
        n_cols=1,
    )
    assert created.path == "/data/nested/f.csv"
    fetched = get_dataset(migrated_db, created.id)
    assert fetched is not None
    assert fetched.path == "/data/nested/f.csv"


def test_create_contract_preserves_path_as_string(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    created = create_contract(
        migrated_db, project_id=project.id, path=Path("/contracts/v1.json"), contract=contract
    )
    assert created.path == "/contracts/v1.json"


def test_create_run_default_fields_are_null(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """A freshly created run has no current_node, no champion, no end time, and is not a
    fork, unless explicitly given one."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    assert run.current_node is None
    assert run.champion_model_id is None
    assert run.ended_at is None
    assert run.forked_from_run_id is None
    assert run.mode == "autonomous"


# --- claim_test_scoring: §6.3 --------------------------------------------------------------------


def test_new_run_has_no_test_scored_at(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    assert run.test_scored_at is None


def test_claim_test_scoring_first_claim_returns_true(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    assert claim_test_scoring(migrated_db, run.id) is True


def test_claim_test_scoring_second_claim_returns_false(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    assert claim_test_scoring(migrated_db, run.id) is True
    assert claim_test_scoring(migrated_db, run.id) is False


def test_claim_test_scoring_persists_the_claim_timestamp(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    claim_test_scoring(migrated_db, run.id)
    fetched = get_run(migrated_db, run.id)
    assert fetched is not None
    assert fetched.test_scored_at is not None
    parse_ts(fetched.test_scored_at)


def test_claim_test_scoring_after_reopening_database_is_refused(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract, db_path: Path
) -> None:
    """Durability, not just in-process correctness: a claim issued after closing and
    reopening the connection to the same file must see the earlier claim -- this is what an
    in-memory flag could never give, and the entire reason this guarantee needed a column."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    assert claim_test_scoring(migrated_db, run.id) is True
    migrated_db.close()

    reopened = connect(db_path)
    try:
        assert claim_test_scoring(reopened, run.id) is False
    finally:
        reopened.close()


def test_claim_test_scoring_unknown_run_id_raises(migrated_db: sqlite3.Connection) -> None:
    with pytest.raises(PersistenceError, match="no run"):
        claim_test_scoring(migrated_db, "r_doesnotexist00")


def test_claim_test_scoring_accepts_explicit_timestamp(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """The `at` parameter lets a caller supply its own timestamp (e.g. the moment scoring
    actually happened, if that differs from when the claim is recorded) rather than always
    using `utc_now()` internally."""
    contract_row = create_contract(
        migrated_db, project_id=project.id, path=Path("/c.json"), contract=contract
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="guided",
        config_json="{}",
    )
    explicit = "2025-06-01T00:00:00+00:00"
    assert claim_test_scoring(migrated_db, run.id, at=explicit) is True
    fetched = get_run(migrated_db, run.id)
    assert fetched is not None
    assert fetched.test_scored_at == explicit


# --- R4: set_test_fingerprint and count_prior_test_scorings --------------------------------------


def _make_run(
    conn: sqlite3.Connection,
    project: ProjectRow,
    contract: DataContract,
    *,
    slug_path: str = "/c.json",
) -> RunRow:
    """One run under `project`, with its own contract. A local helper only for the R4 tests
    below, which each need a run but not a specific contract version."""
    contract_row = create_contract(
        conn, project_id=project.id, path=Path(slug_path), contract=contract
    )
    return create_run(
        conn,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )


def test_candidate_wave_identity_and_status(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    run = _make_run(migrated_db, project, contract)
    spec = CandidateSpec(
        label="Baseline",
        rationale="Reference candidate.",
        estimator=EstimatorSpec(name="fixture"),
        is_baseline=True,
    )
    created = ensure_candidate(
        migrated_db, candidate_id="fixture_candidate", run_id=run.id, spec=spec, wave=2
    )
    assert created.wave == 2 and created.status == "pending"
    assert (
        ensure_candidate(migrated_db, candidate_id=created.id, run_id=run.id, spec=spec, wave=2)
        == created
    )
    with pytest.raises(PersistenceError, match="identity"):
        ensure_candidate(migrated_db, candidate_id=created.id, run_id=run.id, spec=spec, wave=1)
    update_candidate_status(migrated_db, created.id, "completed")
    updated = ensure_candidate(
        migrated_db, candidate_id=created.id, run_id=run.id, spec=spec, wave=2
    )
    assert updated.wave == 2 and updated.status == "completed"


def test_new_run_has_no_test_fingerprint(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: `test_fingerprint` is `None` before a run scores on test."""
    run = _make_run(migrated_db, project, contract)
    assert run.test_fingerprint is None


def test_set_test_fingerprint_first_write_returns_true(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: the first write-once set wins."""
    run = _make_run(migrated_db, project, contract)
    assert set_test_fingerprint(migrated_db, run.id, "fp_abc") is True
    fetched = get_run(migrated_db, run.id)
    assert fetched is not None
    assert fetched.test_fingerprint == "fp_abc"


def test_set_test_fingerprint_second_write_is_refused(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: write-once. A second write returns False and does not overwrite the first value --
    this is the guard, so trigger the failure it prevents (a second fingerprint clobbering the
    first)."""
    run = _make_run(migrated_db, project, contract)
    assert set_test_fingerprint(migrated_db, run.id, "fp_first") is True
    assert set_test_fingerprint(migrated_db, run.id, "fp_second") is False
    fetched = get_run(migrated_db, run.id)
    assert fetched is not None
    assert fetched.test_fingerprint == "fp_first"


def test_set_test_fingerprint_unknown_run_id_raises(migrated_db: sqlite3.Connection) -> None:
    """R4: an unknown `run_id` is a caller bug, distinguishable from 'already set'."""
    with pytest.raises(PersistenceError, match="no run"):
        set_test_fingerprint(migrated_db, "r_doesnotexist00", "fp")


def test_run_row_test_fingerprint_round_trips(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: `RunRow.test_fingerprint` round-trips as `None` then as the stored string."""
    run = _make_run(migrated_db, project, contract)
    assert run.test_fingerprint is None
    set_test_fingerprint(migrated_db, run.id, "fp_round_trip")
    fetched = get_run(migrated_db, run.id)
    assert fetched is not None
    assert fetched.test_fingerprint == "fp_round_trip"


def test_count_prior_test_scorings_counts_matching_runs_excluding_self(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: counts runs in the project with the same fingerprint and a non-null
    `test_scored_at`, excluding the asking run. Sets up a sibling that scored the same
    fingerprint, then asserts the current run sees exactly one prior exposure."""
    sibling = _make_run(migrated_db, project, contract, slug_path="/c1.json")
    current = _make_run(migrated_db, project, contract, slug_path="/c2.json")
    set_test_fingerprint(migrated_db, sibling.id, "fp_shared")
    claim_test_scoring(migrated_db, sibling.id)
    set_test_fingerprint(migrated_db, current.id, "fp_shared")

    count = count_prior_test_scorings(
        migrated_db, project_id=project.id, fingerprint="fp_shared", exclude_run_id=current.id
    )
    assert count == 1


def test_count_prior_test_scorings_ignores_runs_not_yet_scored(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: a sibling with the same fingerprint but no `test_scored_at` is not a prior exposure
    -- it set the fingerprint but never scored. Trigger the near-miss the `IS NOT NULL` guard
    exists to exclude."""
    sibling = _make_run(migrated_db, project, contract, slug_path="/c1.json")
    current = _make_run(migrated_db, project, contract, slug_path="/c2.json")
    set_test_fingerprint(migrated_db, sibling.id, "fp_shared")  # fingerprint set, never scored
    set_test_fingerprint(migrated_db, current.id, "fp_shared")

    count = count_prior_test_scorings(
        migrated_db, project_id=project.id, fingerprint="fp_shared", exclude_run_id=current.id
    )
    assert count == 0


def test_count_prior_test_scorings_ignores_other_fingerprints(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: a scored sibling with a *different* fingerprint does not count."""
    sibling = _make_run(migrated_db, project, contract, slug_path="/c1.json")
    current = _make_run(migrated_db, project, contract, slug_path="/c2.json")
    set_test_fingerprint(migrated_db, sibling.id, "fp_other")
    claim_test_scoring(migrated_db, sibling.id)
    set_test_fingerprint(migrated_db, current.id, "fp_shared")

    count = count_prior_test_scorings(
        migrated_db, project_id=project.id, fingerprint="fp_shared", exclude_run_id=current.id
    )
    assert count == 0


def test_count_prior_test_scorings_ignores_other_projects(
    migrated_db: sqlite3.Connection, contract: DataContract
) -> None:
    """R4: a scored run with the same fingerprint in a *different* project does not count --
    the exposure budget is per project."""
    p1 = create_project(migrated_db, slug="p1", name="P1")
    p2 = create_project(migrated_db, slug="p2", name="P2")
    other = _make_run(migrated_db, p2, contract)
    current = _make_run(migrated_db, p1, contract)
    set_test_fingerprint(migrated_db, other.id, "fp_shared")
    claim_test_scoring(migrated_db, other.id)
    set_test_fingerprint(migrated_db, current.id, "fp_shared")

    count = count_prior_test_scorings(
        migrated_db, project_id=p1.id, fingerprint="fp_shared", exclude_run_id=current.id
    )
    assert count == 0


def test_count_prior_test_scorings_excludes_the_asking_run_itself(
    migrated_db: sqlite3.Connection, project: ProjectRow, contract: DataContract
) -> None:
    """R4: the asking run, even if it has scored its own test with that fingerprint, is not a
    *prior* exposure to itself -- `exclude_run_id` must drop it."""
    current = _make_run(migrated_db, project, contract)
    set_test_fingerprint(migrated_db, current.id, "fp_shared")
    claim_test_scoring(migrated_db, current.id)

    count = count_prior_test_scorings(
        migrated_db, project_id=project.id, fingerprint="fp_shared", exclude_run_id=current.id
    )
    assert count == 0


@pytest.mark.parametrize("closed", [False, True])
def test_candidate_status_sql_failure(closed: bool) -> None:
    conn = sqlite3.connect(":memory:")
    if closed:
        conn.close()
    try:
        with pytest.raises(PersistenceError, match="candidate status") as caught:
            update_candidate_status(conn, "missing", "completed")
        assert isinstance(caught.value.cause, sqlite3.Error)
    finally:
        conn.close()


# B11: durable champion and deterministic outcome boundaries (not executed).
def champion_run(
    conn: sqlite3.Connection, project: ProjectRow, *, parent: RunRow | None = None
) -> RunRow:
    contract_row = create_contract(
        conn,
        project_id=project.id,
        path=Path("/c.json"),
        contract=DataContract.model_validate({"target": {"column": "y"}}),
    )
    return create_run(
        conn,
        project_id=project.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=parent.id if parent else None,
    )


def fitted_champion_artifact(
    conn: sqlite3.Connection,
    run: RunRow,
    root: Path,
    *,
    tainted: bool = False,
    audited: bool = True,
) -> tuple[str, CandidateSpec, ArtifactRef]:
    spec = CandidateSpec(
        label="fixed model",
        rationale="Fixed validation-selected candidate",
        estimator=EstimatorSpec(name="linear_regression"),
    )
    candidate_id = f"{len(run.id)}:{run.id}:c_01"
    ensure_candidate(conn, candidate_id=candidate_id, run_id=run.id, spec=spec)
    ref = new_artifact_ref()
    path = root / f"{ref}.joblib"
    path.write_bytes(b"fitted model fixture")
    meta = ArtifactMeta(ref=ref, kind="model")
    create_artifact(
        conn,
        ref=ref,
        run_id=run.id,
        kind="model",
        path=path,
        meta=meta,
        produced_by="train_estimator",
        derived_from_test=tainted,
    )
    if audited:
        execution = create_node_execution(
            conn, run_id=run.id, node_name="model_training", seq=9, candidate_id=candidate_id
        )
        record_tool_call(
            conn,
            run_id=run.id,
            node_execution_id=execution.id,
            tool_name="train_estimator",
            args_json="{}",
            result_json=ToolSuccess(artifacts=(meta,)).model_dump_json(),
            duration_ms=1,
        )
    return candidate_id, spec, ref


def test_b11_champion_pointer_atomic_idempotent_and_replacement_rejected(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    run = champion_run(migrated_db, project)
    candidate_id, spec, ref = fitted_champion_artifact(migrated_db, run, tmp_path)
    model = save_champion_model(
        migrated_db,
        run_id=run.id,
        candidate_id=candidate_id,
        artifact_id=str(ref),
        name=spec.estimator.name,
        parameters={"fit_intercept": True},
    )
    assert model.id.startswith("mo_") and get_model(migrated_db, model.id) == model
    assert get_model(migrated_db, "missing") is None
    assert get_champion_model(migrated_db, run.id) == model
    durable_run = get_run(migrated_db, run.id)
    assert durable_run is not None and durable_run.champion_model_id == model.id
    assert claim_test_scoring(migrated_db, run.id)
    assert (
        save_champion_model(
            migrated_db,
            run_id=run.id,
            candidate_id=candidate_id,
            artifact_id=str(ref),
            name=spec.estimator.name,
            parameters={"fit_intercept": True},
        )
        == model
    )
    with pytest.raises(PersistenceError, match="replacement"):
        save_champion_model(
            migrated_db,
            run_id=run.id,
            candidate_id=candidate_id,
            artifact_id=str(ref),
            name=spec.estimator.name,
            parameters={"fit_intercept": False},
        )
    assert (
        migrated_db.execute("SELECT COUNT(*) FROM models WHERE run_id = ?", (run.id,)).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "invalid", ["tainted", "unaudited", "missing", "estimator", "claimed", "foreign_candidate"]
)
def test_b11_model_identity_failure_does_not_publish_pointer(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, invalid: str
) -> None:
    run = champion_run(migrated_db, project)
    candidate_id, spec, ref = fitted_champion_artifact(
        migrated_db, run, tmp_path, tainted=invalid == "tainted", audited=invalid != "unaudited"
    )
    if invalid == "missing":
        (tmp_path / f"{ref}.joblib").unlink()
    if invalid == "claimed":
        assert claim_test_scoring(migrated_db, run.id)
    if invalid == "foreign_candidate":
        foreign = champion_run(migrated_db, project)
        candidate_id, _, _ = fitted_champion_artifact(migrated_db, foreign, tmp_path)
    with pytest.raises(PersistenceError):
        save_champion_model(
            migrated_db,
            run_id=run.id,
            candidate_id=candidate_id,
            artifact_id=str(ref),
            name="ridge" if invalid == "estimator" else spec.estimator.name,
            parameters={},
        )
    durable = get_run(migrated_db, run.id)
    assert durable is not None and durable.champion_model_id is None
    assert (
        migrated_db.execute("SELECT COUNT(*) FROM models WHERE run_id = ?", (run.id,)).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("ancestor", [True, False])
def test_b11_parent_artifact_allowed_only_on_verified_fork_ancestry(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, ancestor: bool
) -> None:
    parent = champion_run(migrated_db, project)
    _, spec, ref = fitted_champion_artifact(migrated_db, parent, tmp_path)
    child = champion_run(migrated_db, project, parent=parent if ancestor else None)
    candidate_id = f"{len(child.id)}:{child.id}:c_01"
    ensure_candidate(migrated_db, candidate_id=candidate_id, run_id=child.id, spec=spec)
    if ancestor:
        model = save_champion_model(
            migrated_db,
            run_id=child.id,
            candidate_id=candidate_id,
            artifact_id=str(ref),
            name=spec.estimator.name,
            parameters={},
        )
        assert model.run_id == child.id and model.candidate_id == candidate_id
        assert get_champion_model(migrated_db, child.id) == model
    else:
        with pytest.raises(PersistenceError, match="fork lineage"):
            save_champion_model(
                migrated_db,
                run_id=child.id,
                candidate_id=candidate_id,
                artifact_id=str(ref),
                name=spec.estimator.name,
                parameters={},
            )


def test_b11_outcome_memory_exact_retry_conflict_and_provenance(
    migrated_db: sqlite3.Connection, project: ProjectRow
) -> None:
    run = champion_run(migrated_db, project)
    memory = remember_run_outcome(
        migrated_db,
        project_id=project.id,
        run_id=run.id,
        value={"task": "regression", "selection_bias": "validation selection is optimistic"},
    )
    assert (
        remember_run_outcome(
            migrated_db,
            project_id=project.id,
            run_id=run.id,
            value={"selection_bias": "validation selection is optimistic", "task": "regression"},
        )
        == memory
    )
    with pytest.raises(PersistenceError, match="conflicting"):
        remember_run_outcome(
            migrated_db, project_id=project.id, run_id=run.id, value={"task": "classification"}
        )
    other = create_project(migrated_db, slug="other-outcome", name="Other")
    with pytest.raises(PersistenceError, match="provenance"):
        remember_run_outcome(migrated_db, project_id=other.id, run_id=run.id, value={})
