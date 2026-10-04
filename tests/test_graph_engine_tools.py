"""One candidate through served MCP tools and engine-created audit rows."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from mcp import ClientSession
from pydantic import JsonValue
from statlab.domains.base import NodeResult
from statlab.graph import NodeInvocation
from statlab.mcp_client import CallContext, McpClient
from statlab.persistence.models import ProjectRow
from statlab.storage import get_artifact
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import ProjectPaths
from statlab_tools.settings import ToolSettings

from tests.test_graph_engine import new_world
from tests.test_graph_state import node


@pytest.mark.asyncio
async def test_one_candidate_real_tools(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
    tool_settings: ToolSettings,
    mcp_session: Callable[[], AbstractAsyncContextManager[ClientSession]],
) -> None:
    world = new_world(
        migrated_db, project, minimal_contract, tmp_path, data_dir=tool_settings.data_dir
    )
    dataset = (
        "age,constant,churned\n" + "".join(f"{20 + i % 50},1,{i % 2}\n" for i in range(240))
    ).encode()
    digest = hashlib.sha256(dataset).hexdigest()
    project_paths = ProjectPaths(world.paths.data_dir, project.slug)
    project_paths.datasets.mkdir(parents=True, exist_ok=True)
    project_paths.dataset_csv(digest).write_bytes(dataset)
    world.paths.contract_snapshot_json.write_text(minimal_contract.model_dump_json())
    nodes = (
        node("intake"),
        node("data_splitting"),
        node("preprocessing", candidate=True),
        node("model_training", candidate=True),
        node("model_evaluation", candidate=True),
    )
    allowed = {
        "intake": frozenset({"load_csv"}),
        "data_splitting": frozenset({"split_stratified"}),
        "preprocessing": frozenset({"drop_constant_columns"}),
        "model_training": frozenset({"train_estimator"}),
        "model_evaluation": frozenset({"predict_proba", "score_classification"}),
    }
    intermediate: list[ArtifactRef] = []
    async with (
        mcp_session() as session,
        McpClient(world.settings, migrated_db, session=session) as client,
    ):

        async def execute(invocation: NodeInvocation) -> NodeResult:
            ctx = CallContext(
                world.paths,
                invocation.spec.name,
                invocation.execution.id,
                allowed[invocation.spec.name],
                minimal_contract,
                digest,
            )

            async def call(tool: str, arguments: dict[str, JsonValue]) -> ToolSuccess:
                if tool not in ("load_csv", "predict_proba"):
                    arguments["target"] = minimal_contract.target.column
                return await client.call(ctx, tool, arguments)

            if invocation.spec.name == "intake":
                output = await call("load_csv", {})
            elif invocation.spec.name == "data_splitting":
                raw = invocation.state.shared_results[0].result.artifacts[0]
                output = await call("split_stratified", {"frame": str(raw)})
            else:
                assert invocation.candidate is not None
                splits = invocation.state.shared_results[1].result.artifacts
                split_map: dict[str, ArtifactRef] = {}
                for ref in splits:
                    row = get_artifact(migrated_db, str(ref))
                    assert row is not None and row.split is not None
                    split_map[row.split] = ref
                if invocation.spec.name == "preprocessing":
                    first = await call("drop_constant_columns", {"train": str(split_map["train"])})
                    intermediate.append(first.artifacts[0].ref)
                    output = await call(
                        "drop_constant_columns", {"train": str(first.artifacts[0].ref)}
                    )
                elif invocation.spec.name == "model_training":
                    train = invocation.candidate.results[0].result.artifacts[0]
                    output = await call(
                        "train_estimator",
                        {"train": str(train), "name": invocation.candidate.spec.estimator.name},
                    )
                else:
                    model = invocation.candidate.results[1].result.artifacts[0]
                    probabilities = await call(
                        "predict_proba", {"model": str(model), "frame": str(split_map["valid"])}
                    )
                    output = await call(
                        "score_classification",
                        {
                            "probabilities": str(probabilities.artifacts[0].ref),
                            "truth": str(split_map["valid"]),
                        },
                    )
            return NodeResult(
                status="completed",
                artifacts=tuple(meta.ref for meta in output.artifacts),
                summary=output.summary,
                warnings=output.warnings,
            )

        loop = world.loop(nodes, execute)
        state = await loop.run(world.initial(nodes, candidate=True))
    assert state.status == "completed", state.pending_result
    assert state.cursor == 5 and state.candidate is not None
    assert len(state.shared_results) == 2 and len(state.candidate.results) == 3
    row = get_artifact(migrated_db, str(intermediate[0]))
    assert row is not None and row.pruned_at and not Path(row.path).exists()
    for ref in state.retained_refs:
        retained = get_artifact(migrated_db, str(ref))
        assert retained is not None and retained.pruned_at is None and Path(retained.path).exists()
    calls = migrated_db.execute(
        "SELECT tc.*, ne.candidate_id, ne.seq FROM tool_calls tc "
        "JOIN node_executions ne ON tc.node_execution_id = ne.id ORDER BY tc.rowid"
    ).fetchall()
    assert len(calls) == 7
    assert [row["seq"] for row in calls] == [1, 2, 3, 3, 4, 5, 5]
    assert all(
        row["candidate_id"] == state.candidate.database_id(state.run_id) for row in calls[2:]
    )
    run = migrated_db.execute(
        "SELECT test_scored_at FROM runs WHERE id = ?", (state.run_id,)
    ).fetchone()
    assert run[0] is None
