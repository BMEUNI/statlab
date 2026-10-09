"""Offline complete dispatcher checks; authored but not executed."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from statlab.domains.base import NodeResult
from statlab.domains.candidate_models import CandidateModelExecutor
from statlab.domains.candidate_pipeline import CandidatePipelineExecutor
from statlab.domains.champion import ChampionExecutor
from statlab.domains.design_inference import DesignInferenceExecutor
from statlab.domains.executor import TabularExecutor
from statlab.domains.foundation import FoundationExecutor
from statlab.domains.persistence import PersistenceExecutor
from statlab.domains.tabular import TABULAR_SPINE
from statlab.errors import EngineError
from statlab.graph.engine import NodeInvocation
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import create_node_execution, next_node_attempt

from tests.test_nodes_champion import champion_harness, tail_invocation
from tests.test_nodes_foundation import LEGACY_SPINE


@pytest.mark.asyncio
async def test_dispatches_all_thirteen_declared_nodes(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h, _ = await champion_harness(migrated_db, project, tmp_path)
    dispatcher = TabularExecutor(h.runtime)
    expected = (
        (FoundationExecutor,) * 4
        + (DesignInferenceExecutor,) * 2
        + (CandidatePipelineExecutor,) * 2
        + (CandidateModelExecutor,) * 3
        + (ChampionExecutor, PersistenceExecutor)
    )
    assert tuple(dispatcher.executors) == tuple(node.name for node in TABULAR_SPINE)
    assert dispatcher.executors["data_quality"] is dispatcher.executors["data_profiling"]
    seen: list[tuple[type[object], str]] = []

    async def recording(self: object, invocation: NodeInvocation) -> NodeResult:
        seen.append((type(self), invocation.spec.name))
        return NodeResult(status="completed", summary={"node": invocation.spec.name})

    for executor_type in set(expected):
        monkeypatch.setattr(executor_type, "__call__", recording)
    for seq, (node, executor_type) in enumerate(zip(LEGACY_SPINE, expected, strict=True), 1):
        branch = h.state.branches[0] if node.kind == "PER_CANDIDATE" else None
        candidate_id = branch.database_id(h.state.run_id) if branch else None
        execution = create_node_execution(
            migrated_db,
            run_id=h.state.run_id,
            node_name=node.name,
            seq=seq,
            candidate_id=candidate_id,
            attempt=next_node_attempt(
                migrated_db,
                run_id=h.state.run_id,
                node_name=node.name,
                candidate_id=candidate_id,
            ),
        )
        output = await dispatcher(NodeInvocation(h.state, node, execution, branch))
        assert output.summary == {"node": node.name}
        assert seen[-1] == (executor_type, node.name)


@pytest.mark.asyncio
async def test_unknown_node_is_package_error(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await champion_harness(migrated_db, project, tmp_path)
    invocation = tail_invocation(h)
    with pytest.raises(EngineError, match="unknown tabular"):
        await TabularExecutor(h.runtime)(
            replace(invocation, spec=replace(invocation.spec, name="unregistered"))
        )
