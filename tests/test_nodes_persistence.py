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
from statlab.domains.champion import ChampionExecutor
from statlab.domains.decisions import PersistenceDecision
from statlab.domains.persistence import PersistenceExecutor
from statlab.errors import EngineError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, RunState
from statlab.mcp_client import logged_calls
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import finish_node_execution, list_memory, record_llm_call

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
