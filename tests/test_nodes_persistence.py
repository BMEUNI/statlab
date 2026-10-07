"""Offline preservation regressions; authored but not executed."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue, TypeAdapter
from statlab.domains.base import NodeResult
from statlab.domains.champion import ChampionExecutor
from statlab.domains.decisions import PersistenceDecision
from statlab.domains.persistence import PersistenceExecutor, _atomic_manifest
from statlab.domains.rewind import champion_provenance, publish_retained_report
from statlab.domains.tabular import TABULAR_SPINE
from statlab.errors import EngineError, PersistenceError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, RunState
from statlab.mcp_client import logged_calls
from statlab.persistence.ids import new_id
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import (
    finish_node_execution,
    list_memory,
    list_node_executions,
    record_llm_call,
)
from statlab.storage import create_artifact, ensure_run_layout
from statlab_contracts.artifacts import ArtifactMeta, new_artifact_ref
from statlab_contracts.layout import RunPaths

from tests.test_nodes_champion import ChampionTools, champion_harness, tail_invocation, with_mode
from tests.test_nodes_foundation import Harness


async def persistence_harness(
    conn: sqlite3.Connection, project: ProjectRow, root: Path
) -> tuple[Harness, ChampionTools]:
    h, tools = await champion_harness(conn, project, root)
    invocation = tail_invocation(h)
    champion = await ChampionExecutor(h.runtime)(invocation)
    assert champion.status == "completed"
    finish_node_execution(conn, invocation.execution.id, champion)
    record_llm_call(
        conn,
        run_id=h.state.run_id,
        node_execution_id=invocation.execution.id,
        signature="offline-accounting",
        model="fixture/audited-model",
        prompt_tokens=1,
        completion_tokens=1,
        cached_tokens=0,
        cost_usd=0,
        duration_ms=1,
    )
    h.state = RunState(
        run_id=h.state.run_id,
        spine_id=h.state.spine_id,
        spine=h.state.spine,
        mode=h.state.mode,
        cursor=12,
        traversal=h.state.traversal,
        branches=h.state.branches,
        shared_results=(
            *h.state.shared_results,
            BoundaryResult(
                node_name=invocation.spec.name,
                execution_id=invocation.execution.id,
                result=champion,
            ),
        ),
    )
    assert logged_calls(conn, h.state.run_id, fork_aware=True, checkpoint=h.state) == logged_calls(
        conn, h.state.run_id
    )
    rows = {row.id: row for row in list_node_executions(conn, h.state.run_id)}
    first, second = h.state.branches
    assert {item.execution_id for item in first.results}.isdisjoint(
        {item.execution_id for item in second.results}
    )
    names = tuple(node.name for node in TABULAR_SPINE)
    for shared in h.state.shared_results:
        row = rows[shared.execution_id]
        assert row.node_name == shared.node_name
        assert row.seq == 1 + names.index(shared.node_name)
        assert row.status == shared.result.status
        assert row.candidate_id is None
    lock = root / "supplied.lock"
    lock.write_bytes(b"version = 1\n# exact caller input\n")
    h.runtime = replace(h.runtime, lockfile_path=lock)
    tools.calls.clear()
    return h, tools


@pytest.mark.asyncio
async def test_guided_preservation_awaits_approval_without_writes(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    h.state = with_mode(h.state, "guided")
    before = {
        path.relative_to(h.runtime.paths.root): path.read_bytes()
        for path in h.runtime.paths.root.rglob("*")
        if path.is_file()
    }
    calls = logged_calls(migrated_db, h.state.run_id)
    llm_count = len(h.llm.calls)
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert output.status == "awaiting_user"
    assert output.summary["decision"] == {
        "rationale": "Preserve exact audited computation and immutable run inputs",
        "preserve": True,
    }
    proposal = TypeAdapter(dict[str, JsonValue]).validate_python(output.summary["proposal"])
    assert proposal["target"] == h.state.shared_results[-1].result.summary["test_score"]
    assert proposal["calls"] == [call.model_dump(mode="json") for call in calls]
    assert {
        path.relative_to(h.runtime.paths.root): path.read_bytes()
        for path in h.runtime.paths.root.rglob("*")
        if path.is_file()
    } == before
    assert tools.calls == [] and list_memory(migrated_db, project.id) == ()
    assert logged_calls(migrated_db, h.state.run_id) == calls
    assert len(h.llm.calls) == llm_count


@pytest.mark.asyncio
async def test_guided_preservation_approval_edits_only_rationale(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    h.state = with_mode(h.state, "guided")
    target = h.state.shared_results[-1].result.summary["test_score"]
    seen: list[tuple[JsonValue, ...]] = []

    async def approve(
        invocation: NodeInvocation, decision: BaseModel, evidence: tuple[JsonValue, ...]
    ) -> BaseModel:
        assert invocation.spec.name == "artifact_persistence"
        assert isinstance(decision, PersistenceDecision)
        assert not (h.runtime.paths.root / "uv.lock").exists() and tools.calls == []
        seen.append(evidence)
        return PersistenceDecision(rationale="Approved exact preservation")

    h.runtime = replace(h.runtime, approval=approve)
    llm_count = len(h.llm.calls)
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert output.status == "completed"
    assert output.summary["decision"] == {
        "rationale": "Approved exact preservation",
        "preserve": True,
    }
    assert output.summary["target"] == tools.calls[0][2]["target"] == target
    assert len(seen) == 1 and len(h.llm.calls) == llm_count


@pytest.mark.asyncio
async def test_preserves_real_calls_target_hashes_models_and_idempotent_memory(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    before = logged_calls(migrated_db, h.state.run_id)
    llm_count = len(h.llm.calls)
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert output.status == "completed"
    assert [name for _, name, _ in tools.calls] == ["export_pipeline_script", "snapshot_run"]
    exported = tools.calls[0][2]
    assert exported["calls"] == [call.model_dump(mode="json") for call in before]
    assert exported["target"] == h.state.shared_results[-1].result.summary["test_score"]
    assert any(exported["target"] in call.outputs for call in before)
    snapshot_calls = TypeAdapter(list[dict[str, JsonValue]]).validate_python(
        tools.calls[1][2]["calls"]
    )
    assert snapshot_calls[-1]["tool"] == "export_pipeline_script"
    manifest = TypeAdapter(dict[str, JsonValue]).validate_json(
        h.runtime.paths.manifest_json.read_bytes()
    )
    preservation = TypeAdapter(dict[str, JsonValue]).validate_python(manifest["preservation"])
    assert manifest["run_hash"] == output.summary["run_hash"] == "h" * 64
    assert preservation["skill_sha256"] == h.runtime.skill.sha256
    assert preservation["audited_llm_models"] == ["fixture/audited-model"]
    models = TypeAdapter(dict[str, dict[str, JsonValue]]).validate_python(
        preservation["node_models"]
    )
    assert models["champion_selection"]["model"] == h.runtime.router.model_for("champion_selection")
    assert h.runtime.lockfile_path is not None
    assert (h.runtime.paths.root / "uv.lock").read_bytes() == h.runtime.lockfile_path.read_bytes()
    files = TypeAdapter(list[dict[str, JsonValue]]).validate_python(manifest["files"])
    assert any(
        row["path"] == "uv.lock" and row["sha256"] == output.summary["lockfile_sha256"]
        for row in files
    )
    assert (
        output.summary["pipeline_sha256"]
        == hashlib.sha256(h.runtime.paths.pipeline_py.read_bytes()).hexdigest()
    )
    retry = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert retry.status == "completed" and retry.summary["memory_id"] == output.summary["memory_id"]
    memory = list_memory(migrated_db, project.id)
    assert len(memory) == 1 and memory[0].source_run_id == h.state.run_id
    assert "selection_bias" in json.loads(memory[0].value_json)
    assert len(h.llm.calls) == llm_count


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "unreadable", "skill", "report"])
async def test_preservation_refuses_missing_inputs_and_changed_immutable_files(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, failure: str
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    if failure == "missing":
        h.runtime = replace(h.runtime, lockfile_path=None)
    elif failure == "unreadable":
        h.runtime = replace(h.runtime, lockfile_path=tmp_path / "absent")
    elif failure == "skill":
        h.runtime.paths.skill_snapshot_md.write_bytes(b"changed policy")
    else:
        (h.runtime.paths.report / "report.md").write_bytes(b"changed report")
    if failure == "report":
        with pytest.raises(EngineError, match="preservation"):
            await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    else:
        output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
        assert output.status == "failed" and output.error is not None
    assert tools.calls == [] and list_memory(migrated_db, project.id) == ()


@pytest.mark.asyncio
async def test_lockfile_retry_preserves_first_bytes_and_rejects_changed_caller_input(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    assert (await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))).status == "completed"
    preserved = (h.runtime.paths.root / "uv.lock").read_bytes()
    assert h.runtime.lockfile_path is not None
    h.runtime.lockfile_path.write_bytes(b"new dependency resolution")
    tools.calls.clear()
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert (
        output.status == "failed" and output.error is not None and "differs" in output.error.message
    )
    assert (h.runtime.paths.root / "uv.lock").read_bytes() == preserved and tools.calls == []


@pytest.mark.asyncio
async def test_preservation_keeps_cancellation_and_rejects_foreign_node(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _ = await persistence_harness(migrated_db, project, tmp_path)
    with pytest.raises(EngineError, match="artifact persistence"):
        await PersistenceExecutor(h.runtime)(tail_invocation(h))

    async def cancelled(self: ChampionTools, *args: object, **kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(ChampionTools, "call", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))


def test_atomic_manifest_creation_failure_leaves_no_temporary(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _atomic_manifest(tmp_path / "missing" / "manifest.json", {})
    assert not (tmp_path / "missing").exists()


@pytest.mark.asyncio
async def test_export_tool_failure_is_structured(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    tools.failure_tool = "export_pipeline_script"
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert output.status == "failed" and output.error is not None
    assert output.error.message == "champion tool refused"


@pytest.mark.asyncio
async def test_score_absent_from_real_audit_refuses(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await persistence_harness(migrated_db, project, tmp_path)
    meta = ArtifactMeta(ref=new_artifact_ref(), kind="table")
    path = h.runtime.paths.artifact(meta.ref, "table")
    path.write_bytes(b"orphan score")
    create_artifact(
        migrated_db,
        ref=meta.ref,
        run_id=h.state.run_id,
        kind="table",
        path=path,
        meta=meta,
        produced_by="fixture",
        derived_from_test=True,
    )
    last = h.state.shared_results[-1]
    replaced = BoundaryResult(
        node_name=last.node_name,
        execution_id=last.execution_id,
        result=NodeResult(
            status="completed",
            summary={**last.result.summary, "test_score": str(meta.ref)},
            artifacts=last.result.artifacts,
        ),
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "shared_results": [
                *(b.model_dump(mode="json") for b in h.state.shared_results[:-1]),
                replaced.model_dump(mode="json"),
            ],
        }
    )
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert output.status == "failed" and output.error is not None
    assert output.error.message == "champion test score is absent from real tool audit"


@pytest.mark.asyncio
async def test_lockfile_changed_during_approval_refuses_before_export(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    h.state = with_mode(h.state, "guided")

    async def approve(
        invocation: NodeInvocation, decision: BaseModel, evidence: tuple[JsonValue, ...]
    ) -> BaseModel:
        (h.runtime.paths.root / "uv.lock").write_bytes(b"changed during approval\n")
        return decision

    h.runtime = replace(h.runtime, approval=approve)
    output = await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert output.status == "failed" and output.error is not None
    assert output.error.message == "run lockfile already differs from supplied bytes"
    assert tools.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]", "snapshot manifest is malformed"),
        ('{"run_hash": "other"}', "snapshot manifest disagrees with returned run hash"),
    ],
)
async def test_snapshot_manifest_must_parse_and_match(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    text: str,
    message: str,
) -> None:
    h, tools = await persistence_harness(migrated_db, project, tmp_path)
    tools.manifest_text = text
    with pytest.raises(EngineError, match="artifact preservation failed") as raised:
        await PersistenceExecutor(h.runtime)(tail_invocation(h, 12))
    assert isinstance(raised.value.__cause__, PersistenceError)
    assert raised.value.__cause__.message == message


@pytest.mark.asyncio
async def test_champion_provenance_rejects_missing_unknown_and_unclaimed(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await persistence_harness(migrated_db, project, tmp_path)
    conn = h.runtime.conn
    last = h.state.shared_results[-1]
    base = h.state.model_dump(mode="json")
    short = RunState.model_validate(
        {
            **base,
            "cursor": 11,
            "shared_results": [b.model_dump(mode="json") for b in h.state.shared_results[:-1]],
        }
    )
    with pytest.raises(PersistenceError, match="retained champion boundary is unavailable"):
        champion_provenance(conn, short)
    unknown = RunState.model_validate(
        {
            **base,
            "shared_results": [
                *(b.model_dump(mode="json") for b in h.state.shared_results[:-1]),
                BoundaryResult(
                    node_name=last.node_name, execution_id="ne_missing", result=last.result
                ).model_dump(mode="json"),
            ],
        }
    )
    with pytest.raises(PersistenceError, match="champion execution is unavailable"):
        champion_provenance(conn, unknown)
    conn.execute("UPDATE runs SET test_scored_at = NULL WHERE id = ?", (h.state.run_id,))
    with pytest.raises(PersistenceError, match="retained champion score provenance is invalid"):
        champion_provenance(conn, h.state)


@pytest.mark.asyncio
async def test_publish_retained_report_copies_plots_and_rejects_changed_report(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await persistence_harness(migrated_db, project, tmp_path)
    parent = h.runtime.paths
    child = RunPaths(parent.data_dir, parent.slug, new_id("r"))
    ensure_run_layout(child)
    ref = sorted(h.state.retained_refs)[0]
    (parent.artifacts / f"{ref}.png").write_bytes(b"png bytes")
    await publish_retained_report(parent, child, h.state)
    assert (child.report / "report.md").read_bytes() == (parent.report / "report.md").read_bytes()
    assert (child.report / "plots" / f"{ref}.png").read_bytes() == b"png bytes"
    (parent.report / "report.md").write_bytes(b"# changed\n")
    with pytest.raises(
        PersistenceError, match="retained report hash disagrees with committed champion"
    ):
        await publish_retained_report(parent, child, h.state)
