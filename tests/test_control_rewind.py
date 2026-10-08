"""Offline rewind regressions; authored but deliberately not executed in B12."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
from collections.abc import Callable

import pytest
from pydantic import JsonValue
from statlab.domains.base import NodeResult, NodeSpec
from statlab.domains.rewind import champion_provenance, publish_retained_report
from statlab.errors import EngineError, PersistenceError
from statlab.graph.candidates import TraversalPlan
from statlab.graph.control import ControlContext
from statlab.graph.engine import NodeInvocation
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.reconciliation import RecoveryBinding, RestartReconciler
from statlab.graph.rewind import ForkService, project_frontier
from statlab.graph.router import InterruptClass
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.persistence.models import ContractRow, RunRow
from statlab.persistence.repository import (
    claim_test_scoring,
    count_prior_test_scorings,
    create_contract,
    create_run,
    get_run,
    list_node_executions,
    list_runs,
    record_tool_call,
    require_run,
    save_champion_model,
    set_test_fingerprint,
)
from statlab.storage import create_artifact, list_artifacts
from statlab_contracts.artifacts import ArtifactMeta, new_artifact_ref
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.contract import DataContract, TargetSpec
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.gates import RewindIntent
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import SplitRole

from tests.test_control_router import FakeClient, context
from tests.test_graph_engine import World, artifact, complete, log_outputs
from tests.test_graph_engine import engine_world as engine_world
from tests.test_graph_state import candidate_spec, node


def boundary(name: str, identity: str) -> BoundaryResult:
    return BoundaryResult(
        node_name=name, execution_id=identity, result=NodeResult(status="completed")
    )


def wave_state() -> RunState:
    nodes = (
        node("design"),
        node("overlap"),
        node("prepare", candidate=True),
        node("train", candidate=True),
        node("evaluate", candidate=True),
        node("tail"),
    )
    initial = RunState.initial(
        run_id="r_source", spine_id="synthetic", nodes=nodes, mode="autonomous"
    )
    traversal = TraversalPlan(
        design_node="design",
        overlap_shared="overlap",
        training_node="train",
        evaluation_node="evaluate",
    )
    first = CandidateProgress(
        public_id="c_01",
        spec=candidate_spec(),
        wave=1,
        position=5,
        retune_count=1,
        status="completed",
        results=tuple(
            boundary(name, f"first_{i}")
            for i, name in enumerate(("prepare", "train", "evaluate", "train", "evaluate"))
        ),
    )
    second = CandidateProgress(
        public_id="c_02",
        spec=CandidateSpec(
            label="Second",
            rationale="Another approved specification",
            estimator=EstimatorSpec(name="logistic_regression", params={"fit_intercept": False}),
        ),
        wave=2,
        position=5,
        status="completed",
        results=tuple(
            boundary(name, f"second_{i}") for i, name in enumerate(("prepare", "train", "evaluate"))
        ),
    )
    return RunState(
        run_id=initial.run_id,
        spine_id=initial.spine_id,
        spine=initial.spine,
        mode=initial.mode,
        status="completed",
        cursor=6,
        wave=2,
        traversal=traversal,
        branches=(first, second),
        shared_results=(
            boundary("design", "design1"),
            boundary("overlap", "overlap1"),
            boundary("design", "design2"),
            boundary("tail", "tail1"),
        ),
    )


@pytest.mark.parametrize("target", ["prepare", "train", "evaluate"])
def test_candidate_rewind_consolidates_approved_waves_and_drops_retunes(target: str) -> None:
    source = wave_state()
    projected = project_frontier(source, target)
    assert projected.wave == 1 and len(projected.branches) == 2
    assert tuple(item.execution_id for item in projected.shared_results) == ("design2", "overlap1")
    count = ("prepare", "train", "evaluate").index(target)
    for original, branch in zip(source.branches, projected.branches, strict=True):
        assert branch.spec == original.spec and branch.results == original.results[:count]
        assert branch.wave == 1 and branch.retune_count == 0 and branch.status == "pending"
        assert branch.pending_result is None and branch.position == projected.cursor
    assert projected.fork_metadata["source_wave"] == 2
    assert projected.fork_metadata["projection"] == "first_pass_wave1"


def test_prefix_overlap_and_tail_frontiers_retain_exact_histories() -> None:
    source = wave_state()
    prefix = project_frontier(source, "design")
    assert prefix.shared_results == () and prefix.branches == () and prefix.cursor == 0
    overlap = project_frontier(source, "overlap")
    assert [item.execution_id for item in overlap.shared_results] == ["design2"]
    assert all(not branch.results and branch.position == 2 for branch in overlap.branches)
    tail = project_frontier(source, "tail")
    assert tail.branches == source.branches and tail.wave == 2
    assert tail.shared_results == source.shared_results[:-1]
    assert tail.fork_metadata["projection"] == "terminal_histories"


def test_tail_keeps_terminal_failure_diagnostic_and_rejects_live_gate() -> None:
    source = wave_state()
    original = source.branches[1]
    failed = original.model_copy(
        update={
            "position": 4,
            "status": "failed",
            "results": original.results[:2],
            "pending_result": BoundaryResult(
                node_name="evaluate",
                execution_id="failed_evaluation",
                result=NodeResult(
                    status="failed",
                    error=Failure(error_class="data", message="No eligible outcome"),
                ),
            ),
        }
    )
    state = RunState.model_validate(
        {**source.model_dump(), "branches": (source.branches[0], failed)}
    )
    retained = project_frontier(state, "tail")
    assert retained.branches[1].pending_result == failed.pending_result
    assert retained.branches[1].status == "failed" and retained.pending_result is None
    awaiting = failed.model_copy(
        update={
            "status": "awaiting_user",
            "pending_result": failed.pending_result.model_copy(
                update={"result": NodeResult(status="awaiting_user")}
            )
            if failed.pending_result
            else None,
        }
    )
    live = state.model_copy(
        update={
            "cursor": 2,
            "status": "awaiting_user",
            "branches": (source.branches[0], awaiting),
            "shared_results": source.shared_results[:-1],
        }
    )
    with pytest.raises(EngineError, match="terminal"):
        project_frontier(live, "tail")


def test_missing_first_pass_prefix_rejects_requested_frontier() -> None:
    source = wave_state()
    unfinished = source.branches[1].model_copy(
        update={"position": 2, "results": (), "status": "pending"}
    )
    state = RunState.model_validate(
        {
            **source.model_dump(),
            "cursor": 2,
            "status": "running",
            "branches": (source.branches[0], unfinished),
            "shared_results": source.shared_results[:-1],
        }
    )
    with pytest.raises(EngineError, match="first-pass"):
        project_frontier(state, "train")
    with pytest.raises(EngineError, match="declared"):
        project_frontier(source, "invented")


def immutable_inputs(world: World) -> None:
    world.paths.contract_snapshot_json.write_bytes(b"{}")
    world.paths.skill_snapshot_md.write_bytes(b"# Frozen policy\n")
    (world.paths.root / "uv.lock").write_bytes(b"version = 1\n")


def service_for(world: World, lifecycle: RunLifecycle, nodes: tuple[NodeSpec, ...]) -> ForkService:
    def resolve(paths: RunPaths, state: RunState) -> RunLifecycle:
        child = World(world.conn, paths, world.settings)
        return RunLifecycle(child.loop(nodes, complete, lifecycle.loop.tracker), state)

    return ForkService(lifecycle, resolver=resolve)


@pytest.mark.asyncio
async def test_completed_parent_fork_hardlinks_exact_refs_and_never_rewrites_ancestor(
    engine_world: World,
) -> None:
    nodes = (node("prefix"), node("target"), node("tail"))
    outputs: list[ArtifactMeta] = []

    # The actual producer belongs to a committed node attempt, not a fabricated checkpoint.

    async def producing(invocation: NodeInvocation) -> NodeResult:
        if invocation.spec.name == "prefix":
            output = artifact(engine_world)
            outputs.append(output)
            log_outputs(engine_world, invocation.execution.id, (output,))
            return NodeResult(status="completed", artifacts=(output.ref,))
        return await complete(invocation)

    lifecycle = RunLifecycle(engine_world.loop(nodes, producing), engine_world.initial(nodes))
    source = await lifecycle.start()
    (output,) = outputs
    immutable_inputs(engine_world)
    original_rows = list_node_executions(engine_world.conn, source.run_id)
    original_events = await lifecycle.loop.events.replay()
    original_checkpoint = engine_world.paths.state_json.read_bytes()
    fork = await service_for(engine_world, lifecycle, nodes).fork("target", request_id="g_request")
    assert fork.state.cursor == 1 and fork.state.shared_results == source.shared_results[:1]
    paths = fork.lifecycle.loop.checkpointer.paths
    assert (
        paths.artifact(output.ref, output.kind).stat().st_ino
        == engine_world.paths.artifact(output.ref, output.kind).stat().st_ino
    )
    assert (
        engine_world.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE run_id = ?", (fork.new_run_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        paths.contract_snapshot_json.read_bytes()
        == engine_world.paths.contract_snapshot_json.read_bytes()
    )
    assert (
        paths.contract_snapshot_json.stat().st_ino
        != engine_world.paths.contract_snapshot_json.stat().st_ino
    )
    assert engine_world.paths.state_json.read_bytes() == original_checkpoint
    assert list_node_executions(engine_world.conn, source.run_id) == original_rows
    assert len(await lifecycle.loop.events.replay()) == len(original_events) + 1
    assert list_artifacts(engine_world.conn, fork.new_run_id, checkpoint=fork.state)[0].id == str(
        output.ref
    )
    parent_row = get_run(engine_world.conn, source.run_id)
    assert parent_row is not None and parent_row.status == "completed"
    assert (await fork.lifecycle.audit_anchor()).run_id == fork.new_run_id

    def resolve(row: RunRow) -> RecoveryBinding:
        assert row.id == fork.new_run_id
        return RecoveryBinding(fork.lifecycle.loop, fork.state, fork.lifecycle)

    recovered = await RestartReconciler(
        conn=engine_world.conn, resolver=resolve, tracker=lifecycle.loop.tracker
    ).reconcile(parent_row.project_id)
    assert len(recovered.tasks) == 1 and (await recovered.tasks[0]).status == "completed"
    assert list_node_executions(engine_world.conn, source.run_id) == original_rows


@pytest.mark.asyncio
async def test_active_parent_drains_before_child_publication(engine_world: World) -> None:
    nodes = (node("prefix"), node("target"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(invocation: NodeInvocation) -> NodeResult:
        entered.set()
        await release.wait()
        return await complete(invocation)

    lifecycle = RunLifecycle(engine_world.loop(nodes, execute), engine_world.initial(nodes))
    immutable_inputs(engine_world)
    running = asyncio.create_task(lifecycle.start())
    await entered.wait()
    forking = asyncio.create_task(service_for(engine_world, lifecycle, nodes).fork("prefix"))
    await asyncio.sleep(0)
    assert not forking.done()
    release.set()
    await running
    fork = await forking
    assert fork.state.status == "pending" and fork.state.cursor == 0
    assert (await lifecycle.state()).status == "superseded"
    assert fork.lifecycle.loop.control is not lifecycle.loop.control


@pytest.mark.asyncio
async def test_missing_retained_file_rejects_before_child_creation(engine_world: World) -> None:
    nodes = (node("prefix"), node("target"))
    outputs: list[ArtifactMeta] = []

    async def execute(invocation: NodeInvocation) -> NodeResult:
        if invocation.spec.name == "prefix":
            output = artifact(engine_world)
            outputs.append(output)
            log_outputs(engine_world, invocation.execution.id, (output,))
            return NodeResult(status="completed", artifacts=(output.ref,))
        return await complete(invocation)

    lifecycle = RunLifecycle(engine_world.loop(nodes, execute), engine_world.initial(nodes))
    await lifecycle.start()
    (output,) = outputs
    immutable_inputs(engine_world)
    row = get_run(engine_world.conn, lifecycle.initial.run_id)
    assert row is not None
    before = list_runs(engine_world.conn, row.project_id)
    engine_world.paths.artifact(output.ref, output.kind).unlink()
    with pytest.raises(PersistenceError, match="missing"):
        await service_for(engine_world, lifecycle, nodes).fork("target")
    assert list_runs(engine_world.conn, row.project_id) == before


@pytest.mark.asyncio
async def test_failed_publication_leaves_child_dormant_and_parent_usable(
    engine_world: World,
) -> None:
    nodes = (node("prefix"), node("target"))
    lifecycle = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(engine_world)
    forks = service_for(engine_world, lifecycle, nodes)

    async def reject(parent: RunPaths, child: RunPaths, state: RunState) -> None:
        raise OSError("publication failed")

    broken = ForkService(lifecycle, resolver=forks.resolver, publish_domain=reject)
    with pytest.raises(PersistenceError, match="interrupted"):
        await broken.fork("target")
    row = get_run(engine_world.conn, lifecycle.initial.run_id)
    assert row is not None and row.status == "completed"
    children = tuple(
        item for item in list_runs(engine_world.conn, row.project_id) if item.id != row.id
    )
    assert len(children) == 1 and children[0].status == "cancelled"
    assert json.loads(children[0].config_json)["fork"]["published"] is False


@pytest.mark.asyncio
async def test_retained_split_inherits_fingerprint_without_inheriting_claim(
    engine_world: World,
) -> None:
    nodes = (node("split"), node("target"))
    metas: list[ArtifactMeta] = []
    roles: tuple[SplitRole, ...] = ("train", "valid", "test")
    fingerprint = "f" * 64

    async def execute(invocation: NodeInvocation) -> NodeResult:
        if invocation.spec.name == "split":
            for role in roles:
                meta = ArtifactMeta(ref=new_artifact_ref(), kind="dataframe", split=role)
                path = engine_world.paths.artifact(meta.ref, meta.kind)
                path.write_bytes(role.encode())
                create_artifact(
                    engine_world.conn,
                    ref=meta.ref,
                    run_id=engine_world.paths.run_id,
                    kind=meta.kind,
                    path=path,
                    meta=meta,
                    produced_by="fixture_split",
                    split=meta.split,
                    derived_from_test=role == "test",
                )
                metas.append(meta)
            set_test_fingerprint(engine_world.conn, engine_world.paths.run_id, fingerprint)
            assert claim_test_scoring(engine_world.conn, engine_world.paths.run_id)
            log_outputs(engine_world, invocation.execution.id, tuple(metas))
            return NodeResult(status="completed", artifacts=tuple(meta.ref for meta in metas))
        return await complete(invocation)

    lifecycle = RunLifecycle(engine_world.loop(nodes, execute), engine_world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(engine_world)
    fork = await service_for(engine_world, lifecycle, nodes).fork("target")
    child = get_run(engine_world.conn, fork.new_run_id)
    assert (
        child is not None and child.test_fingerprint == fingerprint and child.test_scored_at is None
    )
    assert (
        count_prior_test_scorings(
            engine_world.conn,
            project_id=child.project_id,
            fingerprint=fingerprint,
            exclude_run_id=child.id,
        )
        == 1
    )
    assert claim_test_scoring(engine_world.conn, child.id)
    assert not claim_test_scoring(engine_world.conn, child.id)
    parent = get_run(engine_world.conn, lifecycle.initial.run_id)
    assert parent is not None and parent.test_scored_at is not None


@pytest.mark.asyncio
async def test_fork_after_champion_keeps_score_provenance_and_independent_report(
    engine_world: World,
) -> None:
    nodes = (node("train", candidate=True), node("champion_selection"), node("preserve"))
    fitted_outputs: list[ArtifactMeta] = []
    score = new_artifact_ref()
    meta = ArtifactMeta(ref=score, kind="table")
    report = b"# Retained score report\n"
    (engine_world.paths.report / "report.md").write_bytes(report)
    selected_id: str | None = None

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal selected_id
        if invocation.spec.name == "train":
            fitted = artifact(engine_world, "model")
            fitted_outputs.append(fitted)
            record_tool_call(
                engine_world.conn,
                run_id=invocation.state.run_id,
                node_execution_id=invocation.execution.id,
                tool_name="train_estimator",
                args_json="{}",
                result_json=ToolSuccess(artifacts=(fitted,)).model_dump_json(),
                duration_ms=1,
            )
            return NodeResult(status="completed", artifacts=(fitted.ref,))
        if invocation.spec.name == "champion_selection":
            (fitted,) = fitted_outputs
            branch = invocation.state.candidate
            assert branch is not None
            selected = save_champion_model(
                engine_world.conn,
                run_id=invocation.state.run_id,
                candidate_id=branch.database_id(invocation.state.run_id),
                artifact_id=str(fitted.ref),
                name=branch.spec.estimator.name,
                parameters={},
            )
            selected_id = selected.id
            score_path = engine_world.paths.artifact(score, "table")
            score_path.write_bytes(b"locked score")
            create_artifact(
                engine_world.conn,
                ref=score,
                run_id=engine_world.paths.run_id,
                kind="table",
                path=score_path,
                meta=meta,
                produced_by="fixture_score",
                derived_from_test=True,
            )
            set_test_fingerprint(engine_world.conn, engine_world.paths.run_id, "f" * 64)
            assert claim_test_scoring(engine_world.conn, invocation.state.run_id)
            log_outputs(engine_world, invocation.execution.id, (meta,))
            return NodeResult(
                status="completed",
                artifacts=(score,),
                summary={
                    "model_id": selected.id,
                    "model": str(fitted.ref),
                    "test_score": str(score),
                    "report": {"sha256": hashlib.sha256(report).hexdigest()},
                },
            )
        return await complete(invocation)

    lifecycle = RunLifecycle(
        engine_world.loop(nodes, execute), engine_world.initial(nodes, candidate=True)
    )
    await lifecycle.start()
    immutable_inputs(engine_world)
    ordinary = service_for(engine_world, lifecycle, nodes)
    fork = await ForkService(
        lifecycle, resolver=ordinary.resolver, publish_domain=publish_retained_report
    ).fork("preserve")
    provenance, model = champion_provenance(engine_world.conn, fork.state)
    child = get_run(engine_world.conn, fork.new_run_id)
    assert child is not None and child.test_scored_at is None and child.champion_model_id is None
    assert provenance.id == lifecycle.initial.run_id and model.id == selected_id
    child_report = fork.lifecycle.loop.checkpointer.paths.report / "report.md"
    assert child_report.read_bytes() == report
    child_report.write_bytes(b"child report publication")
    assert (engine_world.paths.report / "report.md").read_bytes() == report


@pytest.mark.asyncio
async def test_fork_retry_rejects_a_different_target_without_side_effects(
    engine_world: World,
) -> None:
    nodes = (node("first"), node("second"))
    lifecycle = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(engine_world)
    forks = service_for(engine_world, lifecycle, nodes)
    parent = get_run(engine_world.conn, lifecycle.initial.run_id)
    assert parent is not None
    original = await forks.fork("first", request_id="retry")
    retried = await forks.fork("first", request_id="retry")
    assert retried.new_run_id == original.new_run_id
    runs_root = engine_world.paths.root.parent
    rows = list_runs(engine_world.conn, parent.project_id)
    executions = list_node_executions(engine_world.conn, lifecycle.initial.run_id)
    checkpoint = engine_world.paths.state_json.read_bytes()
    events = await lifecycle.loop.events.replay()
    entries = tuple(sorted(path.relative_to(runs_root).as_posix() for path in runs_root.rglob("*")))
    assert sum(row.forked_from_run_id == parent.id for row in rows) == 1
    with pytest.raises(EngineError, match="target"):
        await forks.fork("second", request_id="retry")
    assert list_runs(engine_world.conn, parent.project_id) == rows
    assert list_node_executions(engine_world.conn, lifecycle.initial.run_id) == executions
    assert engine_world.paths.state_json.read_bytes() == checkpoint
    assert await lifecycle.loop.events.replay() == events
    assert (
        tuple(sorted(path.relative_to(runs_root).as_posix() for path in runs_root.rglob("*")))
        == entries
    )


def test_projection_rejects_unreached_missing_shared_and_unapproved_frontiers() -> None:
    state = RunState.initial(
        run_id="r_frontier",
        spine_id="synthetic",
        nodes=(node("alpha"), node("beta")),
        mode="autonomous",
    )
    with pytest.raises(EngineError, match="rewind frontier has not been reached"):
        project_frontier(state, "beta")
    source = wave_state()
    running = CandidateProgress(
        public_id="c_01",
        spec=source.branches[0].spec,
        wave=1,
        position=3,
        status="running",
        results=(boundary("prepare", "p1"),),
    )
    state = RunState.model_validate(
        {
            **source.model_dump(),
            "status": "running",
            "cursor": 1,
            "wave": 1,
            "shared_results": (boundary("design", "design1"),),
            "branches": (running,),
        }
    )
    with pytest.raises(EngineError, match="required preceding shared boundary is unavailable"):
        project_frontier(state, "train")
    state = RunState.model_validate(
        {
            **source.model_dump(),
            "wave": 1,
            "branches": (),
            "shared_results": (
                boundary("design", "design1"),
                boundary("overlap", "overlap1"),
                boundary("tail", "tail1"),
            ),
        }
    )
    with pytest.raises(EngineError, match="candidate frontier has no approved specifications"):
        project_frontier(state, "prepare")


@pytest.mark.asyncio
async def test_fork_skips_unpublished_request_and_rejects_unloadable_published_fork(
    engine_world: World,
) -> None:
    nodes = (node("prefix"), node("target"), node("tail"))
    lifecycle = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    source = await lifecycle.start()
    immutable_inputs(engine_world)
    parent = get_run(engine_world.conn, source.run_id)
    assert parent is not None
    unpublished = create_run(
        engine_world.conn,
        project_id=parent.project_id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json=json.dumps(
            {"fork": {"request_id": "g_retry", "target": "target", "published": False}}
        ),
        forked_from_run_id=parent.id,
        status="cancelled",
    )
    result = await service_for(engine_world, lifecycle, nodes).fork("target", request_id="g_retry")
    assert result.new_run_id != unpublished.id
    create_run(
        engine_world.conn,
        project_id=parent.project_id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json=json.dumps(
            {"fork": {"request_id": "g_lost", "target": "target", "published": True}}
        ),
        forked_from_run_id=parent.id,
        status="cancelled",
    )
    with pytest.raises(PersistenceError, match="published fork checkpoint is unavailable"):
        await service_for(engine_world, lifecycle, nodes).fork("target", request_id="g_lost")


@pytest.mark.asyncio
async def test_fork_rejects_misbound_resolver_and_missing_immutable_inputs(
    engine_world: World,
) -> None:
    nodes = (node("prefix"), node("target"), node("tail"))
    lifecycle = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(engine_world)

    def resolve(paths: RunPaths, state: RunState) -> RunLifecycle:
        return lifecycle

    with pytest.raises(EngineError, match="fork resolver must return a fresh correctly bound"):
        await ForkService(lifecycle, resolver=resolve).fork("target")
    engine_world.paths.contract_snapshot_json.unlink()
    with pytest.raises(PersistenceError, match="fork requires immutable contract and skill inputs"):
        await service_for(engine_world, lifecycle, nodes).fork("target")


@pytest.mark.asyncio
async def test_fork_without_lockfile_copies_only_present_inputs(engine_world: World) -> None:
    nodes = (node("prefix"), node("target"), node("tail"))
    lifecycle = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(engine_world)
    (engine_world.paths.root / "uv.lock").unlink()
    fork = await service_for(engine_world, lifecycle, nodes).fork("target")
    paths = fork.lifecycle.loop.checkpointer.paths
    assert paths.contract_snapshot_json.is_file()
    assert paths.skill_snapshot_md.is_file()
    assert not (paths.root / "uv.lock").exists()


@pytest.mark.asyncio
async def test_fork_control_binding_mismatch(
    engine_world: World, minimal_contract: DataContract
) -> None:
    nodes = (node("prefix"), node("target"), node("tail"))
    lifecycle = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(engine_world)
    with pytest.raises(EngineError, match="fork control binding mismatch"):
        await service_for(engine_world, lifecycle, nodes).handle(
            "rewind",
            InterruptClass(category="qa"),
            context(engine_world.conn, minimal_contract, FakeClient()),
        )


async def _edit_world(
    world: World, contract: DataContract, client: FakeClient
) -> tuple[ForkService, ContractRow, ControlContext]:
    nodes = (node("prefix"), node("target"), node("tail"))
    lifecycle = RunLifecycle(world.loop(nodes, complete), world.initial(nodes))
    await lifecycle.start()
    immutable_inputs(world)
    row = create_contract(
        world.conn,
        project_id=require_run(world.conn, world.paths.run_id).project_id,
        path=world.paths.root / "edited.json",
        contract=DataContract(target=TargetSpec(column="edited")),
    )
    ctx = dataclasses.replace(
        context(world.conn, contract, client, current_node=None), state=await lifecycle.state()
    )
    return service_for(world, lifecycle, nodes), row, ctx


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent_target", "owner", "expected"),
    [("target", "tail", "target"), (None, "target", "target")],
)
async def test_handle_setting_edits_fork_with_new_contract(
    engine_world: World,
    minimal_contract: DataContract,
    intent_target: str | None,
    owner: str,
    expected: str,
) -> None:
    client = FakeClient(RewindIntent(target=intent_target, edits={"split.test_size": 0.3}))
    service, row, ctx = await _edit_world(engine_world, minimal_contract, client)
    seen: list[dict[str, JsonValue]] = []

    def editor(edits: dict[str, JsonValue]) -> tuple[str, ContractRow]:
        seen.append(edits)
        return owner, row

    response = await service.handle(
        "set the test size to 0.3",
        InterruptClass(category="change"),
        ctx,
        request_id="req-edit",
        edit_settings=editor,
    )
    assert seen == [{"split.test_size": 0.3}]
    assert isinstance(response.new_run_id, str)
    child_row = require_run(engine_world.conn, response.new_run_id)
    assert child_row.contract_id == row.id
    child_paths = RunPaths(engine_world.paths.data_dir, engine_world.paths.slug, child_row.id)
    assert child_paths.contract_snapshot_json.read_bytes() == row.contract_json.encode("utf-8")
    config = json.loads(child_row.config_json)
    assert config["fork"]["target"] == expected
    assert config["fork"]["edits"] == {"split.test_size": 0.3}
    assert response.text == (
        f"Changed split.test_size to 0.3. A new run starts from {expected}; "
        "earlier steps are reused."
    )
    assert "Current contract: " in client.calls[0][2][0]["content"]


def _invalid_contract_editor(edits: dict[str, JsonValue]) -> tuple[str, ContractRow]:
    DataContract.model_validate({"target": {"column": "a"}, "split": {"test_size": 1.5}})
    raise AssertionError("unreachable")


def _value_error_editor(edits: dict[str, JsonValue]) -> tuple[str, ContractRow]:
    raise ValueError("version cannot be changed from the chat")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("editor", "starts", "ends"),
    [
        (
            _invalid_contract_editor,
            "I could not change that setting, so nothing was changed: split.test_size",
            "",
        ),
        (_value_error_editor, "", "version cannot be changed from the chat"),
    ],
)
async def test_handle_setting_edit_rejected_without_fork(
    engine_world: World,
    minimal_contract: DataContract,
    editor: Callable[[dict[str, JsonValue]], tuple[str, ContractRow]],
    starts: str,
    ends: str,
) -> None:
    client = FakeClient(RewindIntent(edits={"split.test_size": 1.5}))
    service, _, ctx = await _edit_world(engine_world, minimal_contract, client)
    response = await service.handle(
        "set the test size to 1.5",
        InterruptClass(category="change"),
        ctx,
        edit_settings=editor,
    )
    assert response.new_run_id is None
    assert response.text.startswith(starts)
    assert response.text.endswith(ends)
    project_id = require_run(engine_world.conn, engine_world.paths.run_id).project_id
    assert not [
        run
        for run in list_runs(engine_world.conn, project_id)
        if run.forked_from_run_id == engine_world.paths.run_id
    ]


@pytest.mark.asyncio
async def test_handle_setting_edits_guards(
    engine_world: World, minimal_contract: DataContract
) -> None:
    client = FakeClient(
        RewindIntent(edits={"a": 1}), RewindIntent(target="invented", edits={"a": 1})
    )
    service, row, ctx = await _edit_world(engine_world, minimal_contract, client)
    change = InterruptClass(category="change")
    with pytest.raises(EngineError, match="setting changes are not configured"):
        await service.handle("x", change, ctx)
    with pytest.raises(EngineError, match="rewind target is not declared"):
        await service.handle("x", change, ctx, edit_settings=lambda edits: ("target", row))
