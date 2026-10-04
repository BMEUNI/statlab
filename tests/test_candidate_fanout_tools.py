"""B6 dispatch integration, lifecycle drain and audited fit admission."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import ClientSession
from mcp.types import CallToolResult
from pydantic import JsonValue
from statlab.domains.base import NodeResult
from statlab.errors import ArtifactLockedError, PersistenceError
from statlab.graph import (
    CandidateProgress,
    NodeInvocation,
    RunControl,
    RunState,
    RunTracker,
    ToolBoundary,
    TraversalPlan,
)
from statlab.graph.candidates import BoundaryStopError, FitBudget
from statlab.mcp_client import CallContext, McpClient
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import create_node_execution, record_tool_call
from statlab.storage import get_artifact
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.contract import Constraints, DataContract
from statlab_contracts.envelope import Failure, ToolFailure, ToolSuccess
from statlab_contracts.layout import ProjectPaths
from statlab_tools.settings import ToolSettings

from tests.test_candidate_fanout import initial, loop, milestone
from tests.test_graph_engine import new_world
from tests.test_graph_state import node
from tests.test_mcp_client import FakeSession


def heavy_tool() -> SimpleNamespace:
    return SimpleNamespace(
        name="fixture_fit",
        input_schema={"type": "object", "properties": {}},
        meta={"allow_in": ["*"], "phase": "training", "heavy": True, "transform_only": False},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_tool_boundary_drains_and_prevents_second_call(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
    cancel: bool,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    nodes = (node("model_training", candidate=True),)
    entered, release = asyncio.Event(), asyncio.Event()
    control = RunControl()

    class Session(FakeSession):
        async def call_tool(
            self, name: str, arguments: dict[str, JsonValue], *, read_timeout_seconds: float
        ) -> CallToolResult:
            entered.set()
            await release.wait()
            return await super().call_tool(
                name, arguments, read_timeout_seconds=read_timeout_seconds
            )

    session = Session((heavy_tool(),))
    async with McpClient(world.settings, world.conn, session=session) as client:

        async def execute(inv: NodeInvocation) -> NodeResult:
            ctx = CallContext(
                world.paths,
                inv.spec.name,
                inv.execution.id,
                frozenset({"fixture_fit"}),
                minimal_contract,
                "dataset",
            )
            await boundary.call(ctx, "fixture_fit", {})
            await boundary.call(ctx, "fixture_fit", {})
            return NodeResult(status="completed")

        runner = loop(world, nodes, execute, control=control)
        boundary = ToolBoundary(client, control, runner.budget)
        task = asyncio.create_task(runner.run(initial(world, nodes, 1)))
        await milestone(task, entered)
        control.request_pause()
        if cancel:
            control.request_cancel()
        assert not task.done()
        release.set()
        state = await task
    assert state.status == ("cancelled" if cancel else "paused")
    assert len(session.calls) == 1
    assert world.conn.execute("SELECT status FROM node_executions").fetchone()[0] == "running"
    assert runner.last_error is None and state.branches[0].results == ()
    assert runner.checkpointer.load(spine_id="synthetic", spine=state.spine) == state


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["pause", "cancel", "budget"])
async def test_internal_repair_admission_guard(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
    reason: str,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    nodes = (node("intake"),)
    session = FakeSession((heavy_tool(),))
    session.envelopes = [
        ToolFailure(
            error_class="tool_param", message="repair me", field_errors={"fixture": "repair"}
        ).model_dump(mode="json")
    ]
    control = RunControl()

    async def execute(inv: NodeInvocation) -> NodeResult:
        return NodeResult(status="completed")

    runner = loop(
        world, nodes, execute, constraints=Constraints(max_total_fit_seconds=1), control=control
    )
    execution = create_node_execution(
        world.conn, run_id=world.paths.run_id, node_name="intake", seq=1
    )
    ctx = CallContext(
        world.paths, "intake", execution.id, frozenset({"fixture_fit"}), minimal_contract, "dataset"
    )
    async with McpClient(world.settings, world.conn, session=session) as client:
        boundary = ToolBoundary(client, control, runner.budget)

        async def repair(failure: Failure) -> dict[str, JsonValue]:
            if reason == "pause":
                control.request_pause()
            elif reason == "cancel":
                control.request_cancel()
            else:
                record_tool_call(
                    world.conn,
                    run_id=world.paths.run_id,
                    node_execution_id=execution.id,
                    tool_name="fixture_fit",
                    args_json="{}",
                    result_json=ToolSuccess().model_dump_json(),
                    duration_ms=1000,
                )
            return {}

        with pytest.raises(BoundaryStopError) as caught:
            await boundary.call(ctx, "fixture_fit", {}, repair=repair)
    assert (
        caught.value.reason
        == {"pause": "paused", "cancel": "cancelled", "budget": "budget_exhausted"}[reason]
    )
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_fit_overshoot_and_restart_accounting(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    nodes = (node("model_training", candidate=True), node("champion_selection"))
    two_entered, release, first_done = (asyncio.Event() for _ in range(3))
    control = RunControl()
    started = 0

    class Session(FakeSession):
        async def call_tool(
            self, name: str, arguments: dict[str, JsonValue], *, read_timeout_seconds: float
        ) -> CallToolResult:
            nonlocal started
            started += 1
            if started == 2:
                two_entered.set()
            await release.wait()
            return await super().call_tool(
                name, arguments, read_timeout_seconds=read_timeout_seconds
            )

    session = Session((heavy_tool(),))
    async with McpClient(world.settings, world.conn, session=session) as client:

        async def execute(inv: NodeInvocation) -> NodeResult:
            if inv.candidate is None:
                return NodeResult(status="completed")
            if inv.candidate.public_id == "c_03":
                await first_done.wait()
            ctx = CallContext(
                world.paths,
                inv.spec.name,
                inv.execution.id,
                frozenset({"fixture_fit"}),
                minimal_contract,
                "dataset",
            )
            await boundary.call(ctx, "fixture_fit", {})
            record_tool_call(
                world.conn,
                run_id=world.paths.run_id,
                node_execution_id=inv.execution.id,
                tool_name="fixture_fit",
                args_json="{}",
                result_json=ToolSuccess().model_dump_json(),
                duration_ms=1100,
            )
            first_done.set()
            return NodeResult(status="completed")

        runner = loop(
            world, nodes, execute, constraints=Constraints(max_total_fit_seconds=1), control=control
        )
        boundary = ToolBoundary(client, control, runner.budget)
        task = asyncio.create_task(runner.run(initial(world, nodes)))
        await milestone(task, two_entered)
        assert started == 2
        release.set()
        result = await task
        assert result.status == "completed" and result.budget is not None
        assert result.budget.total_fit_ms >= 2200 and result.budget.overshoot_ms >= 1200
        assert result.branches[2].status == "budget_exhausted" and started == 2
        recovering = loop(world, nodes, execute, constraints=Constraints(max_total_fit_seconds=1))
        ToolBoundary(client, recovering.control, recovering.budget)
        assert recovering.budget.snapshot() == result.budget
        with pytest.raises(BoundaryStopError, match="budget_exhausted"):
            recovering.budget.admit()


@pytest.mark.asyncio
async def test_three_concurrent_runs_run_scoped_ids(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> None:
    tracker, all_entered, release = RunTracker(), asyncio.Event(), asyncio.Event()
    worlds = [new_world(migrated_db, project, minimal_contract, tmp_path) for _ in range(3)]
    nodes = (node("model_training", candidate=True), node("champion_selection"))
    entered = 0

    async def execute(inv: NodeInvocation) -> NodeResult:
        nonlocal entered
        if inv.candidate is not None:
            entered += 1
            if entered == 9:
                all_entered.set()
            await release.wait()
        return NodeResult(status="completed")

    runners = [loop(world, nodes, execute, tracker=tracker) for world in worlds]
    tasks = [
        asyncio.create_task(runner.run(initial(world, nodes)))
        for runner, world in zip(runners, worlds, strict=True)
    ]
    group = asyncio.gather(*tasks)
    await milestone(group, all_entered)
    assert tracker.concurrency == 3
    release.set()
    results = await group
    assert all(result.status == "completed" for result in results)
    ids = [b.database_id(result.run_id) for result in results for b in result.branches]
    assert len(set(ids)) == 9 and tracker.concurrency == 0
    assert migrated_db.execute("SELECT count(*) FROM candidates").fetchone()[0] == 9
    assert (
        migrated_db.execute("SELECT max(concurrency_at_start) FROM node_executions").fetchone()[0]
        == 3
    )


@pytest.mark.asyncio
async def test_three_candidates_shared_splits_locked_test_and_one_champion_score(
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
    dataset = ("age,churned\n" + "".join(f"{20 + i % 47},{i % 2}\n" for i in range(240))).encode()
    digest = hashlib.sha256(dataset).hexdigest()
    project_paths = ProjectPaths(world.paths.data_dir, project.slug)
    project_paths.datasets.mkdir(parents=True, exist_ok=True)
    project_paths.dataset_csv(digest).write_bytes(dataset)
    world.paths.contract_snapshot_json.write_text(minimal_contract.model_dump_json())
    nodes = (
        node("intake"),
        node("data_splitting"),
        node("inferential_analysis"),
        node("model_training", candidate=True),
        node("model_evaluation", candidate=True),
        node("champion_selection"),
    )
    candidates = tuple(
        CandidateSpec(
            label=name,
            rationale="Three contrasting predictive estimators.",
            estimator=EstimatorSpec(name=name),
            is_baseline=i == 0,
            varies_from_baseline=() if i == 0 else ("estimator",),
        )
        for i, name in enumerate(
            ("logistic_regression", "random_forest_classifier", "hist_gb_classifier")
        )
    )
    state = RunState.initial(
        run_id=world.paths.run_id,
        spine_id="synthetic",
        nodes=nodes,
        mode="autonomous",
        candidates=candidates,
    )
    all_active, companion_active = asyncio.Event(), asyncio.Event()
    active = 0
    seen_splits: list[tuple[tuple[str, str], ...]] = []
    model_ids: dict[str, str] = {}
    allowed = {
        "intake": frozenset({"load_csv"}),
        "data_splitting": frozenset({"split_stratified"}),
        "inferential_analysis": frozenset({"assemble_inference_matrix", "fit_logit"}),
        "model_training": frozenset({"train_estimator"}),
        "model_evaluation": frozenset({"predict_proba", "score_classification"}),
        "champion_selection": frozenset(
            {
                "compare_models",
                "rank_models",
                "select_champion",
                "predict_proba",
                "score_classification",
            }
        ),
    }
    dispatches: list[str] = []
    async with mcp_session() as session:

        class CountSession:
            async def list_tools(self) -> object:
                return await session.list_tools()

            async def call_tool(
                self, name: str, arguments: dict[str, JsonValue], *, read_timeout_seconds: float
            ) -> CallToolResult:
                dispatches.append(name)
                return await session.call_tool(
                    name, arguments, read_timeout_seconds=read_timeout_seconds
                )

        async with McpClient(world.settings, world.conn, session=CountSession()) as client:

            async def execute(inv: NodeInvocation) -> NodeResult:
                nonlocal active
                ctx = CallContext(
                    world.paths,
                    inv.spec.name,
                    inv.execution.id,
                    allowed[inv.spec.name],
                    minimal_contract,
                    digest,
                )

                async def call(
                    tool: str, args: dict[str, JsonValue], *, champion: bool = False
                ) -> ToolSuccess:
                    if tool in {
                        "split_stratified",
                        "assemble_inference_matrix",
                        "fit_logit",
                        "train_estimator",
                        "score_classification",
                    }:
                        args["target"] = minimal_contract.target.column
                    bound = CallContext(
                        world.paths,
                        inv.spec.name,
                        inv.execution.id,
                        allowed[inv.spec.name],
                        minimal_contract,
                        digest,
                        champion,
                    )
                    return await boundary.call(bound, tool, args)

                if inv.spec.name == "intake":
                    output = await call("load_csv", {})
                elif inv.spec.name == "data_splitting":
                    output = await call(
                        "split_stratified",
                        {"frame": str(inv.state.shared_results[0].result.artifacts[0])},
                    )
                else:
                    splits: dict[str, ArtifactRef] = {}
                    for ref in inv.state.shared_results[1].result.artifacts:
                        row = get_artifact(world.conn, str(ref))
                        assert row is not None and row.split is not None
                        splits[row.split] = ref
                    if inv.spec.name == "inferential_analysis":
                        assert inv.candidate is None and not any(
                            branch.results for branch in inv.state.branches
                        )
                        await all_active.wait()
                        companion_active.set()
                        matrix = await call(
                            "assemble_inference_matrix",
                            {
                                "train": str(splits["train"]),
                                "valid": str(splits["valid"]),
                                "predictors": ["age"],
                            },
                        )
                        output = await call("fit_logit", {"matrix": str(matrix.artifacts[0].ref)})
                    elif inv.spec.name == "model_training":
                        assert inv.candidate is not None
                        active += 1
                        if active == 3:
                            all_active.set()
                        await companion_active.wait()
                        seen_splits.append(
                            tuple(
                                (
                                    str(splits[key]),
                                    hashlib.sha256(
                                        world.paths.artifact(splits[key], "dataframe").read_bytes()
                                    ).hexdigest(),
                                )
                                for key in ("train", "valid", "test")
                            )
                        )
                        before = len(dispatches)
                        with pytest.raises(ArtifactLockedError):
                            await boundary.call(
                                ctx,
                                "train_estimator",
                                {
                                    "train": str(splits["test"]),
                                    "name": inv.candidate.spec.estimator.name,
                                    "target": minimal_contract.target.column,
                                },
                            )
                        assert len(dispatches) == before
                        output = await call(
                            "train_estimator",
                            {
                                "train": str(splits["train"]),
                                "name": inv.candidate.spec.estimator.name,
                            },
                        )
                        model_id = inv.execution.id
                        model_ids[inv.candidate.public_id] = model_id
                        world.conn.execute(
                            "INSERT INTO models(id, run_id, candidate_id, name, params_json, "
                            "artifact_id, created_at) "
                            "VALUES (?, ?, ?, ?, '{}', ?, 'fixture')",
                            (
                                model_id,
                                state.run_id,
                                inv.candidate.database_id(state.run_id),
                                inv.candidate.spec.estimator.name,
                                str(output.artifacts[0].ref),
                            ),
                        )
                    elif inv.spec.name == "model_evaluation":
                        assert inv.candidate is not None
                        model = inv.candidate.results[0].result.artifacts[0]
                        probabilities = await call(
                            "predict_proba", {"model": str(model), "frame": str(splits["valid"])}
                        )
                        output = await call(
                            "score_classification",
                            {
                                "probabilities": str(probabilities.artifacts[0].ref),
                                "truth": str(splits["valid"]),
                            },
                        )
                    else:
                        assert (
                            inv.spec.name == "champion_selection" and len(inv.state.branches) == 3
                        )
                        tables: dict[str, JsonValue] = {
                            b.spec.label: str(b.results[-1].result.artifacts[0])
                            for b in inv.state.branches
                        }
                        names: dict[str, JsonValue] = {
                            b.spec.label: b.spec.estimator.name for b in inv.state.branches
                        }
                        comparison = await call(
                            "compare_models", {"candidates": tables, "metric": "accuracy"}
                        )
                        ranking = await call(
                            "rank_models",
                            {"comparison": str(comparison.artifacts[0].ref), "estimators": names},
                        )
                        label = ranking.summary["top_eligible"]
                        assert isinstance(label, str)
                        champion = next(b for b in inv.state.branches if b.spec.label == label)
                        await call(
                            "select_champion",
                            {
                                "ranking": str(ranking.artifacts[0].ref),
                                "candidate": label,
                                "rationale": "Validation ranking decides the champion.",
                            },
                        )
                        model_id = model_ids[champion.public_id]
                        world.conn.execute(
                            "UPDATE runs SET champion_model_id = ? WHERE id = ?",
                            (model_id, state.run_id),
                        )
                        probabilities = await call(
                            "predict_proba",
                            {
                                "model": str(champion.results[0].result.artifacts[0]),
                                "frame": str(splits["test"]),
                            },
                            champion=True,
                        )
                        output = await call(
                            "score_classification",
                            {
                                "probabilities": str(probabilities.artifacts[0].ref),
                                "truth": str(splits["test"]),
                            },
                            champion=True,
                        )
                        metrics = output.summary["metrics"]
                        assert isinstance(metrics, dict) and isinstance(metrics["accuracy"], dict)
                        value = metrics["accuracy"]["value"]
                        assert isinstance(value, (float, int))
                        world.conn.execute(
                            "INSERT INTO metrics(id,run_id,candidate_id,model_id,split,name,value) "
                            "VALUES ('testmetric',?,?,?,?, 'accuracy',?)",
                            (
                                state.run_id,
                                champion.database_id(state.run_id),
                                model_id,
                                "test",
                                value,
                            ),
                        )
                        with pytest.raises(ArtifactLockedError):
                            await call(
                                "score_classification",
                                {
                                    "probabilities": str(probabilities.artifacts[0].ref),
                                    "truth": str(splits["test"]),
                                },
                                champion=True,
                            )
                return NodeResult(
                    status="completed",
                    artifacts=tuple(meta.ref for meta in output.artifacts),
                    summary=output.summary,
                )

            runner = loop(
                world,
                nodes,
                execute,
                traversal=TraversalPlan(overlap_shared="inferential_analysis"),
            )
            boundary = ToolBoundary(client, runner.control, runner.budget)
            result = await runner.run(state)
    assert result.status == "completed", result.pending_result
    assert len(seen_splits) == 3 and seen_splits[0] == seen_splits[1] == seen_splits[2]
    assert dispatches.count("fit_logit") == 1 and dispatches.count("select_champion") == 1
    assert dispatches.count("score_classification") == 4
    assert dispatches.index("select_champion") < max(
        i for i, name in enumerate(dispatches) if name == "score_classification"
    )
    row = world.conn.execute(
        "SELECT m.model_id, r.champion_model_id, r.test_scored_at FROM metrics m "
        "JOIN runs r ON m.run_id = r.id WHERE m.split = 'test'"
    ).fetchone()
    assert row["model_id"] == row["champion_model_id"] and row["test_scored_at"] is not None
    calls = world.conn.execute(
        "SELECT tc.args_json FROM tool_calls tc JOIN node_executions ne "
        "ON tc.node_execution_id = ne.id WHERE tool_name = 'score_classification' "
        "AND ne.node_name = 'model_evaluation'"
    ).fetchall()
    assert len(calls) == 3 and {json.loads(row["args_json"])["truth"] for row in calls} == {
        seen_splits[0][1][0]
    }
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result


@pytest.mark.parametrize("closed", [False, True])
def test_fit_budget_sql_failure(closed: bool) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    budget = FitBudget(conn, "missing", Constraints())
    if closed:
        conn.close()
    try:
        with pytest.raises(PersistenceError, match="fit budget audit") as caught:
            budget.snapshot()
        assert isinstance(caught.value.cause, sqlite3.Error)
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("heavy", [False, True])
async def test_tool_boundary_successful_repair(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
    heavy: bool,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    tool = heavy_tool()
    tool.meta["heavy"] = heavy
    session = FakeSession((tool,))
    session.envelopes = [
        ToolFailure(
            error_class="tool_param", message="repair", field_errors={"fixture": "repair"}
        ).model_dump(mode="json")
    ]
    repaired: list[Failure] = []
    execution = create_node_execution(
        world.conn, run_id=world.paths.run_id, node_name="intake", seq=1
    )
    ctx = CallContext(
        world.paths, "intake", execution.id, frozenset({"fixture_fit"}), minimal_contract, "dataset"
    )

    async def repair(failure: Failure) -> dict[str, JsonValue]:
        repaired.append(failure)
        return {}

    async with McpClient(world.settings, world.conn, session=session) as client:
        boundary = ToolBoundary(
            client, RunControl(), FitBudget(world.conn, world.paths.run_id, Constraints())
        )
        assert (await boundary.call(ctx, "fixture_fit", {}, repair=repair)).ok
    assert len(repaired) == 1 and len(session.calls) == 2
    assert world.conn.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_shared_heavy_budget_refusal(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    nodes = (node("intake"), node("model_training"))
    session = FakeSession((heavy_tool(),))
    async with McpClient(world.settings, world.conn, session=session) as client:

        async def execute(inv: NodeInvocation) -> NodeResult:
            ctx = CallContext(
                world.paths,
                inv.spec.name,
                inv.execution.id,
                frozenset({"fixture_fit"}),
                minimal_contract,
                "dataset",
            )
            await boundary.call(ctx, "fixture_fit", {})
            record_tool_call(
                world.conn,
                run_id=inv.state.run_id,
                node_execution_id=inv.execution.id,
                tool_name="fixture_fit",
                args_json="{}",
                result_json=ToolSuccess().model_dump_json(),
                duration_ms=1000,
            )
            return NodeResult(status="completed")

        runner = loop(
            world,
            nodes,
            execute,
            traversal=TraversalPlan(),
            constraints=Constraints(max_total_fit_seconds=1),
        )
        boundary = ToolBoundary(client, runner.control, runner.budget)
        start = RunState.initial(
            run_id=world.paths.run_id, spine_id="synthetic", nodes=nodes, mode="autonomous"
        )
        result = await runner.run(start)
    assert result.status == "failed" and len(session.calls) == 1
    assert result.pending_result is not None and result.pending_result.node_name == "model_training"
    assert result.pending_result.result.error == Failure(
        error_class="data", message="Fit budget exhausted"
    )
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result


@pytest.mark.asyncio
async def test_retune_budget_spent_by_inflight_sibling(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    nodes = (
        node("model_training", candidate=True),
        node("model_evaluation", candidate=True),
        node("tail"),
    )
    callback_entered, sibling_finished = asyncio.Event(), asyncio.Event()
    retunes: list[str] = []
    session = FakeSession((heavy_tool(),))
    async with McpClient(world.settings, world.conn, session=session) as client:

        async def execute(inv: NodeInvocation) -> NodeResult:
            if inv.candidate is not None and inv.candidate.public_id == "c_03":
                await sibling_finished.wait()
            if (
                inv.candidate is not None
                and inv.candidate.public_id == "c_02"
                and inv.spec.name == "model_training"
            ):
                await callback_entered.wait()
                ctx = CallContext(
                    world.paths,
                    inv.spec.name,
                    inv.execution.id,
                    frozenset({"fixture_fit"}),
                    minimal_contract,
                    "dataset",
                )
                await boundary.call(ctx, "fixture_fit", {})
                record_tool_call(
                    world.conn,
                    run_id=inv.state.run_id,
                    node_execution_id=inv.execution.id,
                    tool_name="fixture_fit",
                    args_json="{}",
                    result_json=ToolSuccess().model_dump_json(),
                    duration_ms=1000,
                )
                sibling_finished.set()
            return NodeResult(status="completed")

        async def decide(state: RunState, branch: CandidateProgress) -> bool:
            retunes.append(branch.public_id)
            assert branch.public_id == "c_01"
            callback_entered.set()
            await sibling_finished.wait()
            return True

        runner = loop(
            world,
            nodes,
            execute,
            traversal=TraversalPlan(
                training_node="model_training", evaluation_node="model_evaluation"
            ),
            constraints=Constraints(max_total_fit_seconds=1),
        )
        runner.retune_decider = decide
        boundary = ToolBoundary(client, runner.control, runner.budget)
        result = await runner.run(initial(world, nodes))
    assert result.status == "completed" and retunes == ["c_01"] and len(session.calls) == 1
    assert all(branch.retune_count == 0 and len(branch.results) == 2 for branch in result.branches)
    assert result.budget is not None and result.budget.exhausted
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result


@pytest.mark.asyncio
async def test_planner_budget_spent_while_awaiting(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> None:
    world = new_world(migrated_db, project, minimal_contract, tmp_path)
    nodes = (node("intake"), node("fit", candidate=True), node("tail"))
    entered, release = asyncio.Event(), asyncio.Event()

    class Session(FakeSession):
        async def call_tool(
            self, name: str, arguments: dict[str, JsonValue], *, read_timeout_seconds: float
        ) -> CallToolResult:
            entered.set()
            await release.wait()
            return await super().call_tool(
                name, arguments, read_timeout_seconds=read_timeout_seconds
            )

    session = Session((heavy_tool(),))
    async with McpClient(world.settings, world.conn, session=session) as client:

        async def execute(inv: NodeInvocation) -> NodeResult:
            return NodeResult(status="completed")

        async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
            assert wave == 2
            execution = state.shared_results[0].execution_id
            ctx = CallContext(
                world.paths,
                "intake",
                execution,
                frozenset({"fixture_fit"}),
                minimal_contract,
                "dataset",
            )
            await boundary.call(ctx, "fixture_fit", {})
            record_tool_call(
                world.conn,
                run_id=state.run_id,
                node_execution_id=execution,
                tool_name="fixture_fit",
                args_json="{}",
                result_json=ToolSuccess().model_dump_json(),
                duration_ms=1000,
            )
            return (
                CandidateSpec(
                    label="New proposal",
                    rationale="A distinct deterministic proposal.",
                    estimator=EstimatorSpec(name="fixture", params={"choice": 10}),
                    varies_from_baseline=("hyperparameters",),
                ),
            )

        runner = loop(
            world,
            nodes,
            execute,
            traversal=TraversalPlan(design_node="intake"),
            constraints=Constraints(max_total_fit_seconds=1),
        )
        runner.candidate_planner = planner
        boundary = ToolBoundary(client, runner.control, runner.budget)
        task = asyncio.create_task(runner.run(initial(world, nodes)))
        await milestone(task, entered)
        assert not task.done()
        release.set()
        result = await task
    assert result.status == "completed" and result.wave == 1 and len(result.branches) == 3
    assert result.budget is not None and result.budget.exhausted and len(session.calls) == 1
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 3
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result
