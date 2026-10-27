"""Append-only project facts and provenance at the SQL boundary."""

import json
import math
import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue
from statlab.errors import PersistenceError
from statlab.memory import read_memory, recall_memory, remember
from statlab.persistence.models import MemoryRow
from statlab.persistence.repository import create_contract, create_project, create_run
from statlab_contracts.contract import DataContract


def test_append_order_isolation_and_snapshot(
    migrated_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "statlab.persistence.repository.utc_now", lambda: "2026-10-04T00:00:00+00:00"
    )
    project = create_project(migrated_db, slug="facts", name="Facts")
    other = create_project(migrated_db, slug="other-facts", name="Other")
    first = remember(migrated_db, project_id=project.id, key="fact", value={"z": 1, "a": [True]})
    snapshot = read_memory(migrated_db, project.id)
    second = remember(migrated_db, project_id=project.id, key="fact", value="=SUM(A1)")
    assert first.value_json == '{"a":[true],"z":1}'
    assert first.value() == {"a": [True], "z": 1}
    assert second.value() == "=SUM(A1)"
    assert first.source_run_id is None
    assert snapshot == (first,)
    assert read_memory(migrated_db, project.id) == (first, second)
    assert read_memory(migrated_db, other.id) == ()


def test_recall_keeps_notes_and_three_most_relevant_outcomes() -> None:
    def row(id_: str, key: str, value: JsonValue, ts: str) -> MemoryRow:
        return MemoryRow(
            id=id_,
            project_id="p",
            key=key,
            value_json=json.dumps(value),
            source_run_id=None,
            ts=ts,
        )

    note = row("note", "fact", 1, "t0")
    old = row("old", "run_outcome", {"target": "y"}, "t1")
    d1 = row("d1", "run_outcome", {"dataset_sha256": "s", "target": "y"}, "t2")
    d2 = row("d2", "run_outcome", {"dataset_sha256": "s", "target": "y"}, "t3")
    d3 = row("d3", "run_outcome", {"dataset_sha256": "s", "target": "y"}, "t4")
    d4 = row("d4", "run_outcome", {"dataset_sha256": "s", "target": "y"}, "t5")
    other = row("other", "run_outcome", {"dataset_sha256": "x", "target": "z"}, "t6")
    scalar = row("scalar", "run_outcome", 3, "t7")
    rows = (note, old, d1, d2, d3, d4, other, scalar)
    assert recall_memory(rows, dataset_sha256="s", target="y") == (note, d2, d3, d4)
    assert recall_memory(rows, dataset_sha256="none", target="y") == (note, d2, d3, d4)
    assert recall_memory(rows, dataset_sha256="none", target="z") == (note, other)


def test_provenance(
    migrated_db: sqlite3.Connection, minimal_contract: DataContract, tmp_path: Path
) -> None:
    project = create_project(migrated_db, slug="source", name="Source")
    other = create_project(migrated_db, slug="destination", name="Destination")
    contract = create_contract(
        migrated_db,
        project_id=project.id,
        path=tmp_path / "contract.json",
        contract=minimal_contract,
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract.id,
        mode="autonomous",
        config_json="{}",
    )
    row = remember(migrated_db, project_id=project.id, key="fact", value=3, source_run_id=run.id)
    assert row.source_run_id == run.id
    for project_id, source in ((other.id, run.id), (project.id, "absent")):
        with pytest.raises(PersistenceError, match="provenance"):
            remember(migrated_db, project_id=project_id, key="fact", value=3, source_run_id=source)
    assert read_memory(migrated_db, other.id) == ()


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, {"nested": [math.nan]}])
def test_nonfinite_rejected(value: JsonValue, migrated_db: sqlite3.Connection) -> None:
    with pytest.raises(PersistenceError, match="JSON"):
        remember(migrated_db, project_id="absent", key="fact", value=value)


def test_blank_missing_project_and_sql_errors(migrated_db: sqlite3.Connection) -> None:
    with pytest.raises(PersistenceError, match="blank"):
        remember(migrated_db, project_id="absent", key=" ", value=None)
    with pytest.raises(PersistenceError, match="write"):
        remember(migrated_db, project_id="absent", key="fact", value=None)
    conn = sqlite3.connect(":memory:")
    with pytest.raises(PersistenceError, match="write"):
        remember(conn, project_id="absent", key="fact", value=None)
    with pytest.raises(PersistenceError, match="read"):
        read_memory(conn, "absent")
    conn.close()


@pytest.mark.parametrize("document", ["NaN", "1e999", "invalid"])
def test_corrupt_row_json(document: str) -> None:
    row = MemoryRow(
        id="fact",
        project_id="project",
        key="fact",
        value_json=document,
        source_run_id=None,
        ts="now",
    )
    with pytest.raises(PersistenceError, match="JSON"):
        row.value()
