"""Deterministic fan-out, durable joins, scheduling and branch recovery."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError, LlmCallError, PersistenceError, ToolCallError
from statlab.events import EventBus
from statlab.graph import (
    Checkpointer,
    NodeExecutor,
    NodeInvocation,
    RecoveryBinding,
    RestartReconciler,
    RunControl,
    RunLoop,
    RunState,
    RunTracker,
    TraversalPlan,
)
from statlab.graph.state import BoundaryResult
from statlab.persistence.db import transaction
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import get_run, list_node_executions
from statlab.storage import validate_checkpoint_ownership
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.contract import Constraints, DataContract
from statlab_contracts.envelope import Failure

from tests.test_graph_engine import World, new_world
from tests.test_graph_state import node


@pytest.fixture
def world(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    minimal_contract: DataContract,
    tmp_path: Path,
) -> World:
    return new_world(migrated_db, project, minimal_contract, tmp_path)


def specs(count: int = 3, offset: int = 0) -> tuple[CandidateSpec, ...]:
    return tuple(
        CandidateSpec(
            label=f"Candidate {i}",
            rationale="A distinct deterministic test candidate.",
            estimator=EstimatorSpec(name="fixture", params={"choice": i}),
            is_baseline=i == 0,
            varies_from_baseline=() if i == 0 else ("hyperparameters",),
        )
        for i in range(offset, offset + count)
    )


def initial(world: World, nodes: tuple[NodeSpec, ...], count: int = 3) -> RunState:
    return RunState.initial(
        run_id=world.paths.run_id,
        spine_id="synthetic",
        nodes=nodes,
        mode="autonomous",
        candidates=specs(count),
    )


def loop(
    world: World,
    nodes: tuple[NodeSpec, ...],
    executor: NodeExecutor,
    *,
    traversal: TraversalPlan | None = None,
    constraints: Constraints | None = None,
    control: RunControl | None = None,
    tracker: RunTracker | None = None,
) -> RunLoop:
    return RunLoop(
        conn=world.conn,
        nodes=nodes,
        spine_id="synthetic",
        executor=executor,
        checkpointer=Checkpointer(world.paths),
        events=EventBus(world.paths),
        settings=world.settings,
        tracker=tracker or RunTracker(),
        traversal=traversal,
        constraints=constraints,
        control=control,
    )


async def milestone[T](task: asyncio.Future[T], event: asyncio.Event) -> None:
    waiting = asyncio.create_task(event.wait())
    try:
        done, _ = await asyncio.wait((task, waiting), return_when=asyncio.FIRST_COMPLETED)
        assert waiting in done, await task
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.asyncio
async def test_independent_progress_and_overlap_snapshot(world: World) -> None:
    nodes = (
        node("foundation"),
        node("companion"),
        node("prepare", candidate=True),
        node("fit", candidate=True),
        node("compare"),
    )
    blocked, advanced, overlap_started, release = (asyncio.Event() for _ in range(4))
    snapshots: list[RunState] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        if inv.spec.name == "companion":
            assert inv.candidate is None
            assert [b.node_name for b in inv.state.shared_results] == ["foundation"]
            assert not any(b.results for b in inv.state.branches)
            overlap_started.set()
            await release.wait()
        elif inv.candidate is not None:
            if inv.candidate.public_id == "c_01" and inv.spec.name == "prepare":
                blocked.set()
                await release.wait()
            elif inv.candidate.public_id == "c_02" and inv.spec.name == "fit":
                await blocked.wait()
                advanced.set()
        elif inv.spec.name == "compare":
            assert release.is_set()
            assert all(b.status == "completed" and len(b.results) == 2 for b in inv.state.branches)
        snapshots.append(inv.state)
        return NodeResult(status="completed", summary={"node": inv.spec.name})

    runner = loop(world, nodes, execute, traversal=TraversalPlan(overlap_shared="companion"))
    task = asyncio.create_task(runner.run(initial(world, nodes)))
    await milestone(task, advanced)
    await milestone(task, overlap_started)
    saved = runner.checkpointer.load(spine_id="synthetic", spine=initial(world, nodes).spine)
    assert saved is not None and len(saved.branches[1].results) >= 1
    assert saved.branches[0].results == ()
    release.set()
    result = await task
    assert result.status == "completed" and result.cursor == len(nodes)
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result
    assert len(list_node_executions(world.conn, result.run_id)) == 9
    assert snapshots[0].shared_results == ()
    terminal = [e for e in await runner.events.replay() if e.type == "node.completed"]
    assert sum("candidate_id" in e.payload for e in terminal) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_node", ["companion", "fit", "compare"])
async def test_running_current_node_tracks_latest_dispatch(world: World, blocked_node: str) -> None:
    nodes = (
        node("foundation"),
        node("companion"),
        node("prepare", candidate=True),
        node("fit", candidate=True),
        node("compare"),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(inv: NodeInvocation) -> NodeResult:
        if inv.spec.name == blocked_node:
            entered.set()
            await release.wait()
        return NodeResult(status="completed")

    runner = loop(world, nodes, execute)
    task = asyncio.create_task(runner.run(initial(world, nodes)))
    try:
        await milestone(task, entered)
        row = get_run(world.conn, world.paths.run_id)
        assert row is not None and row.status == "running"
        assert row.current_node == blocked_node
    finally:
        release.set()
        result = await task
    assert result.status == "completed"
    row = get_run(world.conn, result.run_id)
    assert row is not None and row.current_node is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["failed", "awaiting_user"])
async def test_halted_sibling_isolated(world: World, failure: str) -> None:
    nodes = (node("prepare", candidate=True), node("fit", candidate=True), node("compare"))
    tail: list[str] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        if inv.candidate is not None and inv.candidate.public_id == "c_01":
            return (
                NodeResult(
                    status="failed", error=Failure(error_class="data", message="bad candidate")
                )
                if failure == "failed"
                else NodeResult(status="awaiting_user")
            )
        if inv.candidate is None:
            tail.append(inv.spec.name)
        return NodeResult(status="completed")

    runner = loop(world, nodes, execute)
    state = await runner.run(initial(world, nodes))
    assert state.branches[0].status == failure and state.branches[0].pending_result is not None
    assert all(b.status == "completed" for b in state.branches[1:])
    assert state.status == ("completed" if failure == "failed" else "awaiting_user")
    assert tail == (["compare"] if failure == "failed" else [])
    assert runner.checkpointer.load(spine_id="synthetic", spine=state.spine) == state


@pytest.mark.asyncio
async def test_no_viable_candidate_fails(world: World) -> None:
    nodes = (node("fit", candidate=True), node("compare"))

    async def execute(inv: NodeInvocation) -> NodeResult:
        assert inv.candidate is not None
        return NodeResult(status="failed", error=Failure(error_class="data", message="bad fit"))

    runner = loop(world, nodes, execute)
    state = await runner.run(initial(world, nodes))
    assert state.status == "failed"
    assert runner.checkpointer.load(spine_id="synthetic", spine=state.spine) == state


@pytest.mark.asyncio
async def test_search_and_retune_caps_append_attempts(world: World) -> None:
    nodes = (
        node("split"),
        node("design"),
        node("companion"),
        node("prepare", candidate=True),
        node("fit", candidate=True),
        node("evaluate", candidate=True),
        node("compare"),
    )
    counts: dict[str, int] = {}

    async def execute(inv: NodeInvocation) -> NodeResult:
        counts[inv.spec.name] = counts.get(inv.spec.name, 0) + 1
        return NodeResult(status="completed")

    runner = loop(
        world,
        nodes,
        execute,
        traversal=TraversalPlan(
            overlap_shared="companion",
            design_node="design",
            training_node="fit",
            evaluation_node="evaluate",
        ),
        constraints=Constraints(max_candidates=4, max_search_waves=2, max_retune_loops=1),
    )
    waves: list[int] = []

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        waves.append(wave)
        assert all(b.status == "completed" for b in state.branches)
        return specs(1, 10)

    async def retune(state: RunState, branch: object) -> bool:
        return True

    runner.candidate_planner = planner
    runner.retune_decider = retune
    result = await runner.run(initial(world, nodes))
    assert result.status == "completed" and result.wave == 2 and waves == [2]
    assert counts == {
        "split": 1,
        "design": 2,
        "companion": 1,
        "prepare": 4,
        "fit": 8,
        "evaluate": 8,
        "compare": 1,
    }
    assert [b.wave for b in result.branches] == [1, 1, 1, 2]
    assert all(b.retune_count == 1 for b in result.branches)
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result
    rows = list_node_executions(world.conn, result.run_id)
    assert all(row.attempt in (1, 2) for row in rows)
    assert world.conn.execute("SELECT wave FROM candidates ORDER BY rowid").fetchall()[-1][0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_cross_wave_guards_before_candidate_insert(world: World, duplicate: bool) -> None:
    nodes = (node("design"), node("fit", candidate=True))

    async def execute(inv: NodeInvocation) -> NodeResult:
        return NodeResult(status="completed")

    runner = loop(
        world,
        nodes,
        execute,
        traversal=TraversalPlan(design_node="design"),
        constraints=Constraints(max_candidates=4),
    )

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        return specs(1, 0) if duplicate else specs(2, 10)

    runner.candidate_planner = planner
    with pytest.raises(EngineError, match="duplicate" if duplicate else "max_candidates"):
        await runner.run(initial(world, nodes))
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 3
    assert len(list_node_executions(world.conn, world.paths.run_id)) == 4


@pytest.mark.asyncio
async def test_initial_cap_and_invalid_traversal_have_no_side_effects(world: World) -> None:
    nodes = (node("fit", candidate=True), node("compare"))

    async def execute(inv: NodeInvocation) -> NodeResult:
        raise AssertionError("no dispatch")

    runner = loop(world, nodes, execute, constraints=Constraints(max_candidates=2))
    with pytest.raises(EngineError, match="max_candidates"):
        await runner.run(initial(world, nodes))
    assert not world.paths.state_json.exists()
    assert not list_node_executions(world.conn, world.paths.run_id)
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
    assert await runner.events.replay() == ()
    runner = loop(world, nodes, execute, traversal=TraversalPlan(overlap_shared="compare"))
    with pytest.raises(EngineError, match="overlap"):
        await runner.run(initial(world, nodes))
    assert not world.paths.state_json.exists()
    assert not list_node_executions(world.conn, world.paths.run_id)
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
    assert await runner.events.replay() == ()
    runner = loop(world, nodes, execute)
    without_baseline = RunState.initial(
        run_id=world.paths.run_id,
        spine_id="synthetic",
        nodes=nodes,
        mode="autonomous",
        candidates=specs(3, 10),
    )
    with pytest.raises(ValidationError, match="baseline"):
        await runner.run(without_baseline)
    assert not world.paths.state_json.exists()
    assert not list_node_executions(world.conn, world.paths.run_id)
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0
    assert await runner.events.replay() == ()


@pytest.mark.asyncio
async def test_restart_only_unfinished_branches_and_overlap(
    world: World, project: ProjectRow
) -> None:
    nodes = (
        node("companion"),
        node("prepare", candidate=True),
        node("fit", candidate=True),
        node("compare"),
    )
    active, sibling_done = asyncio.Event(), asyncio.Event()

    async def crash(inv: NodeInvocation) -> NodeResult:
        if inv.spec.name == "companion":
            active.set()
            await asyncio.Event().wait()
        if (
            inv.candidate is not None
            and inv.candidate.public_id == "c_01"
            and inv.spec.name == "fit"
        ):
            await asyncio.Event().wait()
        if (
            inv.candidate is not None
            and inv.candidate.public_id == "c_03"
            and inv.spec.name == "fit"
        ):
            sibling_done.set()
        return NodeResult(status="completed")

    tracker = RunTracker()
    traversal = TraversalPlan(overlap_shared="companion")
    runner = loop(world, nodes, crash, traversal=traversal, tracker=tracker)
    start = initial(world, nodes)
    task = asyncio.create_task(runner.run(start))
    await milestone(task, active)
    await milestone(task, sibling_done)
    # The event is set before commit; acquire coordinator after executor yields back.
    async with runner._coordinator:
        pass
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    seen: list[tuple[str, str | None]] = []

    async def resumed(inv: NodeInvocation) -> NodeResult:
        seen.append((inv.spec.name, inv.candidate.public_id if inv.candidate else None))
        return NodeResult(status="completed")

    recovering = loop(world, nodes, resumed, traversal=traversal, tracker=tracker)
    service = RestartReconciler(
        conn=world.conn, resolver=lambda row: RecoveryBinding(recovering, start), tracker=tracker
    )
    batch = await service.reconcile(project.id)
    result = await batch.tasks[0]
    assert result.status == "completed"
    assert set(seen) == {("companion", None), ("fit", "c_01"), ("compare", None)}
    interrupted = [
        row for row in list_node_executions(world.conn, result.run_id) if row.status == "failed"
    ]
    assert len(interrupted) == 2 and all(
        json.loads(row.error or "{}")["message"] == "process terminated" for row in interrupted
    )
    assert recovering.checkpointer.load(spine_id="synthetic", spine=result.spine) == result


def test_b7_json_migration_and_bad_branch_progress(world: World) -> None:
    nodes = (node("prepare", candidate=True),)
    state = initial(world, nodes, 1)
    data = state.model_dump(mode="json")
    branches = data.pop("branches")
    assert isinstance(branches, list)
    data["candidate"] = branches[0]
    restored = RunState.model_validate_json(json.dumps(data), strict=True)
    assert restored.candidate == state.candidate and restored.branches == state.branches
    data = initial(world, nodes).model_dump(mode="json")
    branches = data["branches"]
    assert isinstance(branches, list) and isinstance(branches[0], dict)
    branches[0]["position"] = 1
    with pytest.raises(ValidationError, match="boundaries"):
        RunState.model_validate(data)
    world.paths.state_json.write_text(json.dumps(data))
    with pytest.raises(PersistenceError, match="invalid"):
        Checkpointer(world.paths).load(spine_id="synthetic", spine=state.spine)


@pytest.mark.asyncio
async def test_checkpoint_ahead_repairs_every_branch_and_is_idempotent(
    world: World, project: ProjectRow
) -> None:
    nodes = (node("companion"), node("fit", candidate=True), node("compare"))

    async def execute(inv: NodeInvocation) -> NodeResult:
        return NodeResult(status="completed", summary={"node": inv.spec.name})

    tracker = RunTracker()
    traversal = TraversalPlan(overlap_shared="companion")
    runner = loop(world, nodes, execute, traversal=traversal, tracker=tracker)
    start = initial(world, nodes)
    result = await runner.run(start)
    world.conn.execute("UPDATE runs SET status = 'running' WHERE id = ?", (result.run_id,))
    world.conn.execute(
        "UPDATE node_executions SET status = 'running', ended_at = NULL, summary = NULL "
        "WHERE run_id = ?",
        (result.run_id,),
    )
    world.conn.execute(
        "UPDATE candidates SET status = 'pending' WHERE run_id = ?", (result.run_id,)
    )
    world.conn.commit()
    world.paths.events_jsonl.write_text("")

    async def no_dispatch(inv: NodeInvocation) -> NodeResult:
        raise AssertionError("checkpoint already completed")

    recovering = loop(world, nodes, no_dispatch, traversal=traversal, tracker=tracker)
    service = RestartReconciler(
        conn=world.conn, resolver=lambda row: RecoveryBinding(recovering, start), tracker=tracker
    )
    batch = await service.reconcile(project.id)
    assert not batch.tasks and batch.dormant == (result,)
    rows = list_node_executions(world.conn, result.run_id)
    assert len(rows) == 5 and all(row.status == "completed" and row.ended_at for row in rows)
    assert {
        row[0]
        for row in world.conn.execute(
            "SELECT status FROM candidates WHERE run_id = ?", (result.run_id,)
        )
    } == {"completed"}
    events = await recovering.events.replay()
    terminal = [event for event in events if event.type == "node.completed"]
    assert len(terminal) == 5
    assert {
        event.payload["candidate_id"] for event in terminal if "candidate_id" in event.payload
    } == {"c_01", "c_02", "c_03"}
    second = await service.reconcile(project.id)
    assert not second.tasks and not second.dormant
    assert await recovering.events.replay() == events


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid", ["noncontiguous", "unknown", "design", "design_overlap", "training"]
)
async def test_public_fanout_rejects_invalid_declaration(world: World, invalid: str) -> None:
    nodes: tuple[NodeSpec, ...] = (
        node("design"),
        node("companion"),
        node("fit", candidate=True),
        node("evaluate", candidate=True),
        node("tail"),
    )
    traversal = TraversalPlan()
    if invalid == "noncontiguous":
        nodes = (node("fit", candidate=True), node("gap"), node("evaluate", candidate=True))
    elif invalid == "unknown":
        traversal = TraversalPlan(design_node="absent")
    elif invalid == "design":
        traversal = TraversalPlan(design_node="fit")
    elif invalid == "design_overlap":
        traversal = TraversalPlan(overlap_shared="companion", design_node="companion")
    else:
        traversal = TraversalPlan(training_node="evaluate", evaluation_node="fit")

    async def no_dispatch(inv: NodeInvocation) -> NodeResult:
        raise AssertionError("invalid declaration must not dispatch")

    runner = loop(world, nodes, no_dispatch, traversal=traversal)
    with pytest.raises(EngineError):
        await runner.run(initial(world, nodes, 1))
    assert not list_node_executions(world.conn, world.paths.run_id)
    assert not world.paths.state_json.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["design", "training", "candidate"])
async def test_fanout_missing_callback_nodes_or_candidate(world: World, missing: str) -> None:
    nodes = (node("fit", candidate=True),)

    async def execute(inv: NodeInvocation) -> NodeResult:
        raise AssertionError("no dispatch")

    runner = loop(world, nodes, execute, traversal=TraversalPlan())

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        return ()

    async def retune(state: RunState, branch: object) -> bool:
        return False

    if missing == "design":
        runner.candidate_planner = planner
    elif missing == "training":
        runner.retune_decider = retune
    start = RunState.initial(
        run_id=world.paths.run_id,
        spine_id="synthetic",
        nodes=nodes,
        mode="autonomous",
        candidates=() if missing == "candidate" else specs(),
    )
    with pytest.raises(EngineError, match="requires"):
        await runner.run(start)
    assert not list_node_executions(world.conn, start.run_id)


@pytest.mark.asyncio
async def test_restart_refuses_changed_traversal(world: World) -> None:
    nodes = (node("companion"), node("fit", candidate=True))
    entered = asyncio.Event()

    async def blocked(inv: NodeInvocation) -> NodeResult:
        entered.set()
        await asyncio.Event().wait()
        return NodeResult(status="completed")

    runner = loop(world, nodes, blocked, traversal=TraversalPlan(overlap_shared="companion"))
    start = initial(world, nodes)
    task = asyncio.create_task(runner.run(start))
    await milestone(task, entered)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    before = list_node_executions(world.conn, start.run_id)
    changed = loop(world, nodes, blocked, traversal=TraversalPlan())
    with pytest.raises(EngineError, match="traversal mismatch"):
        await changed.run(start)
    assert list_node_executions(world.conn, start.run_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tool", "llm", "unexpected", "skip"])
async def test_fanout_executor_failure_boundary(
    world: World, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    nodes = (node("fit", candidate=True), node("tail"))
    detail = Failure(error_class="data", message="Candidate failed safely")

    async def execute(inv: NodeInvocation) -> NodeResult:
        if inv.candidate is not None and inv.candidate.public_id == "c_01":
            if failure == "tool":
                raise ToolCallError("fixture", detail)
            if failure == "llm":
                raise LlmCallError(detail)
            if failure == "unexpected":
                raise ValueError("private diagnostic")
            return NodeResult(status="skipped", skip_reason="Fixture requests a skip")
        return NodeResult(status="completed")

    runner = loop(world, nodes, execute)
    if failure == "skip":
        with pytest.raises(EngineError, match="non-skippable"):
            await runner.run(initial(world, nodes))
        return
    result = await runner.run(initial(world, nodes))
    assert result.status == "completed" and result.branches[0].status == "failed"
    pending = result.branches[0].pending_result
    assert pending is not None and pending.result.error is not None
    assert pending.result.error == (
        Failure(error_class="bug", message="Node execution failed")
        if failure == "unexpected"
        else detail
    )
    assert (runner.last_error is not None) == (failure == "unexpected")
    assert ("executor failed with ValueError" in caplog.text) == (failure == "unexpected")
    assert "private diagnostic" not in caplog.text


@pytest.mark.asyncio
async def test_pre_dispatch_pause(world: World) -> None:
    nodes = (node("prefix"), node("tail"))
    seen: list[str] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append(inv.spec.name)
        return NodeResult(status="completed")

    control = RunControl()
    control.request_pause()
    runner = loop(world, nodes, execute, control=control)
    start = RunState.initial(
        run_id=world.paths.run_id, spine_id="synthetic", nodes=nodes, mode="autonomous"
    )
    result = await runner.run(start)
    assert result.status == "paused" and not seen
    assert not list_node_executions(world.conn, start.run_id)


@pytest.mark.asyncio
async def test_no_candidate_fanout(world: World) -> None:
    nodes = (node("prefix"), node("tail"))
    seen: list[str] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append(inv.spec.name)
        return NodeResult(status="completed")

    runner = loop(world, nodes, execute, traversal=TraversalPlan())
    start = RunState.initial(
        run_id=world.paths.run_id, spine_id="synthetic", nodes=nodes, mode="autonomous"
    )
    result = await runner.run(start)
    assert result.status == "completed" and not result.branches and seen == ["prefix", "tail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "awaiting_user"])
@pytest.mark.parametrize("overlap", [False, True])
async def test_repeated_design_halt_reload_and_reconcile(
    world: World, project: ProjectRow, outcome: str, overlap: bool
) -> None:
    nodes = (node("design"), node("companion"), node("fit", candidate=True), node("tail"))
    traversal = TraversalPlan(design_node="design", overlap_shared="companion" if overlap else None)
    seen: list[tuple[str, int]] = []
    planned: list[int] = []
    tracker = RunTracker()

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append((inv.spec.name, inv.execution.attempt))
        if inv.spec.name == "design" and inv.execution.attempt == 2:
            assert inv.candidate is None
            if outcome == "failed":
                return NodeResult(
                    status="failed", error=Failure(error_class="data", message="Design refused")
                )
            world.conn.execute(
                "INSERT INTO gates VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "wave2",
                    inv.state.run_id,
                    inv.execution.id,
                    '{"prompt":"Review new design?"}',
                    None,
                    "pending",
                    "now",
                ),
            )
            return NodeResult(status="awaiting_user")
        return NodeResult(status="completed")

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        planned.append(wave)
        return specs(1, 10)

    runner = loop(world, nodes, execute, traversal=traversal, tracker=tracker)
    runner.candidate_planner = planner
    start = initial(world, nodes)
    state = await runner.run(start)
    assert state.status == outcome and state.wave == 2 and planned == [2]
    assert state.cursor == (1 if overlap else 2)
    assert state.pending_result is not None and state.pending_result.node_name == "design"
    assert state.branches[-1].status == "pending" and state.branches[-1].results == ()
    assert seen.count(("companion", 1)) == 1 and ("tail", 1) not in seen
    assert seen.count(("fit", 1)) == 3 and ("fit", 2) not in seen
    assert runner.checkpointer.load(spine_id="synthetic", spine=start.spine) == state
    executions = list_node_executions(world.conn, state.run_id)
    pending = next(row for row in executions if row.id == state.pending_result.execution_id)
    assert pending.node_name == "design" and pending.attempt == 2 and pending.candidate_id is None
    # Persisted corruption cannot use the repeated-design exception for another shared node.
    for corruption in (
        "unrelated_node",
        "finished_design",
        "missing_wave",
        "started_branch",
        "undeclared_design",
    ):
        data = state.model_dump(mode="json")
        boundary = data["pending_result"]
        branches = data["branches"]
        assert isinstance(boundary, dict) and isinstance(branches, list)
        if corruption == "unrelated_node":
            boundary["node_name"] = "tail"
        elif corruption == "finished_design":
            history = data["shared_results"]
            assert isinstance(history, list)
            history.append(
                {**boundary, "result": NodeResult(status="completed").model_dump(mode="json")}
            )
        elif corruption == "missing_wave":
            data["branches"] = branches[:-1]
        elif corruption == "started_branch":
            branches[-1]["status"] = "running"
        else:
            declaration = data["traversal"]
            assert isinstance(declaration, dict)
            declaration["design_node"] = None
        with pytest.raises(ValidationError, match="pending boundary"):
            RunState.model_validate_json(json.dumps(data), strict=True)

    async def no_dispatch(inv: NodeInvocation) -> NodeResult:
        raise AssertionError("halted design must remain dormant")

    recovering = loop(world, nodes, no_dispatch, traversal=traversal, tracker=tracker)
    reconciler = RestartReconciler(
        conn=world.conn, resolver=lambda row: RecoveryBinding(recovering, start), tracker=tracker
    )
    batch = await reconciler.reconcile(project.id)
    assert not batch.tasks
    assert batch.dormant == (() if outcome == "failed" else (state,))
    assert list_node_executions(world.conn, state.run_id) == executions and planned == [2]
    if outcome == "awaiting_user":
        opened = [
            event for event in await recovering.events.replay() if event.type == "gate.opened"
        ]
        assert len(opened) == 1 and opened[0].payload["gate_id"] == "wave2"
    anchor = await recovering.ensure_audit_anchor(start)
    if outcome == "awaiting_user":
        assert anchor.id == state.pending_result.execution_id
    else:
        assert anchor.id == executions[-1].id


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", [False, True])
async def test_retune_callback_decline_or_pause(world: World, pause: bool) -> None:
    nodes = (node("fit", candidate=True), node("evaluate", candidate=True), node("tail"))
    control = RunControl()
    seen: list[str] = []
    calls: list[str] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append(inv.spec.name)
        return NodeResult(status="completed")

    async def decide(state: RunState, branch: object) -> bool:
        calls.append("decide")
        if pause:
            control.request_pause()
        return pause

    runner = loop(
        world,
        nodes,
        execute,
        traversal=TraversalPlan(training_node="fit", evaluation_node="evaluate"),
        control=control,
    )
    runner.retune_decider = decide
    result = await runner.run(initial(world, nodes, 1))
    assert result.status == ("paused" if pause else "completed")
    assert calls == ["decide"] and seen == (
        ["fit", "evaluate"] if pause else ["fit", "evaluate", "tail"]
    )
    assert result.branches[0].retune_count == 0
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("pause", [False, True])
async def test_planner_empty_or_pause(world: World, pause: bool) -> None:
    nodes = (node("design"), node("fit", candidate=True), node("tail"))
    control = RunControl()
    seen: list[str] = []
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append(inv.spec.name)
        return NodeResult(status="completed")

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        assert wave == 2
        entered.set()
        await release.wait()
        return specs(1, 10) if pause else ()

    runner = loop(
        world, nodes, execute, traversal=TraversalPlan(design_node="design"), control=control
    )
    runner.candidate_planner = planner
    task = asyncio.create_task(runner.run(initial(world, nodes)))
    await milestone(task, entered)
    if pause:
        control.request_pause()
    release.set()
    result = await task
    assert result.status == ("paused" if pause else "completed") and result.wave == 1
    assert len(result.branches) == 3 and seen.count("design") == 1
    assert ("tail" in seen) != pause
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 3
    assert runner.checkpointer.load(spine_id="synthetic", spine=result.spine) == result


@pytest.mark.asyncio
async def test_initial_planner_after_design_commits_branches_before_launch(world: World) -> None:
    nodes = (node("design"), node("companion"), node("fit", candidate=True), node("tail"))
    traversal = TraversalPlan(design_node="design", overlap_shared="companion")
    seen: list[str] = []
    proposals: list[int] = []
    runner: RunLoop

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append(inv.spec.name)
        if inv.spec.name in ("companion", "fit"):
            saved = runner.checkpointer.load(spine_id="synthetic", spine=inv.state.spine)
            assert saved is not None and [b.public_id for b in saved.branches] == ["c_01", "c_02"]
            assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 2
        return NodeResult(status="completed", summary={"approved": True})

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        assert [boundary.node_name for boundary in state.shared_results] == ["design"]
        assert not state.branches and seen == ["design"]
        proposals.append(wave)
        return specs(2)

    runner = loop(
        world, nodes, execute, traversal=traversal, constraints=Constraints(max_search_waves=1)
    )
    runner.candidate_planner = planner
    result = await runner.run(initial(world, nodes, 0))
    assert result.status == "completed" and proposals == [1]
    assert [branch.public_id for branch in result.branches] == ["c_01", "c_02"]
    assert all(branch.wave == 1 and branch.status == "completed" for branch in result.branches)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "awaiting_user"])
async def test_initial_failed_or_guided_prefix_never_plans(world: World, status: str) -> None:
    nodes = (node("design"), node("fit", candidate=True))

    async def execute(inv: NodeInvocation) -> NodeResult:
        assert inv.spec.name == "design"
        return (
            NodeResult(status="failed", error=Failure(error_class="data", message="refused"))
            if status == "failed"
            else NodeResult(status="awaiting_user")
        )

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        pytest.fail("halted prefix must not plan")

    runner = loop(world, nodes, execute, traversal=TraversalPlan(design_node="design"))
    runner.candidate_planner = planner
    result = await runner.run(initial(world, nodes, 0))
    assert result.status == status and not result.branches


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "max", "invalid", "pause"])
async def test_initial_proposal_refusal_and_control(world: World, case: str) -> None:
    nodes = (node("design"), node("fit", candidate=True))
    control = RunControl()

    async def execute(inv: NodeInvocation) -> NodeResult:
        assert inv.spec.name == "design"
        return NodeResult(status="completed")

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        assert wave == 1
        if case == "pause":
            control.request_pause()
        return (
            ()
            if case == "empty"
            else specs(3)
            if case == "max"
            else specs(1, 1)
            if case == "invalid"
            else specs(1)
        )

    runner = loop(
        world,
        nodes,
        execute,
        traversal=TraversalPlan(design_node="design"),
        constraints=Constraints(max_candidates=2),
        control=control,
    )
    runner.candidate_planner = planner
    if case == "pause":
        assert (await runner.run(initial(world, nodes, 0))).status == "paused"
    else:
        with pytest.raises(EngineError):
            await runner.run(initial(world, nodes, 0))
    assert world.conn.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("saved_branches", [False, True])
async def test_initial_restart_uses_saved_design_without_reexecution(
    world: World, saved_branches: bool
) -> None:
    nodes = (node("design"), node("fit", candidate=True), node("tail"))
    traversal = TraversalPlan(design_node="design")
    start = initial(world, nodes, 0)
    saved = RunState(
        run_id=start.run_id,
        spine_id=start.spine_id,
        spine=start.spine,
        mode=start.mode,
        status="running",
        cursor=1,
        traversal=traversal,
        branches=initial(world, nodes, 2).branches if saved_branches else (),
        shared_results=(
            BoundaryResult(
                node_name="design",
                execution_id="saved",
                result=NodeResult(status="completed", summary={"approved": True}),
            ),
        ),
    )
    Checkpointer(world.paths).save(saved)
    calls: list[int] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        assert inv.spec.name != "design"
        return NodeResult(status="completed")

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        calls.append(wave)
        assert state.shared_results[0].result.summary == {"approved": True}
        return specs(2)

    runner = loop(
        world, nodes, execute, traversal=traversal, constraints=Constraints(max_search_waves=1)
    )
    runner.candidate_planner = planner
    result = await runner.run(start)
    assert result.status == "completed" and calls == ([] if saved_branches else [1])
    assert [branch.spec for branch in result.branches] == list(specs(2))


@pytest.mark.asyncio
async def test_initial_bootstrap_requires_successful_declared_design(world: World) -> None:
    nodes = (node("design", skippable=True), node("fit", candidate=True))

    async def execute(inv: NodeInvocation) -> NodeResult:
        return NodeResult(status="skipped", skip_reason="synthetic")

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        pytest.fail("unapproved design must not plan")

    runner = loop(world, nodes, execute, traversal=TraversalPlan(design_node="design"))
    runner.candidate_planner = planner
    with pytest.raises(EngineError, match="approved design"):
        await runner.run(initial(world, nodes, 0))


@pytest.mark.asyncio
async def test_initial_stop_before_prefix_never_calls_planner(world: World) -> None:
    nodes = (node("design"), node("fit", candidate=True))
    control = RunControl()
    control.request_cancel()

    async def execute(inv: NodeInvocation) -> NodeResult:
        pytest.fail("stopped run must not execute prefix")

    async def planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
        pytest.fail("stopped run must not plan")

    runner = loop(
        world, nodes, execute, traversal=TraversalPlan(design_node="design"), control=control
    )
    runner.candidate_planner = planner
    result = await runner.run(initial(world, nodes, 0))
    assert result.status == "cancelled" and not result.branches


@pytest.mark.asyncio
async def test_checkpoint_candidate_spec_tamper_is_rejected(world: World) -> None:
    nodes = (node("fit", candidate=True),)

    async def execute(inv: NodeInvocation) -> NodeResult:
        return NodeResult(status="completed")

    state = await loop(world, nodes, execute, traversal=TraversalPlan()).run(
        initial(world, nodes, 1)
    )
    assert state.status == "completed"
    validate_checkpoint_ownership(world.conn, state.run_id, state)
    with transaction(world.conn):
        world.conn.execute(
            "UPDATE candidates SET spec_json = '{}' WHERE run_id = ?", (state.run_id,)
        )
    with pytest.raises(PersistenceError, match="checkpoint candidate ownership mismatch"):
        validate_checkpoint_ownership(world.conn, state.run_id, state)
