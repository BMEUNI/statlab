"""Offline lifecycle and boundary proofs; intentionally authored without execution."""

from __future__ import annotations

import asyncio

import pytest
from statlab.domains.base import NodeResult
from statlab.errors import EngineError
from statlab.graph.candidates import RunControl, TraversalPlan
from statlab.graph.engine import NodeInvocation
from statlab.graph.lifecycle import RunLifecycle
from statlab.persistence.repository import get_run, list_node_executions
from statlab_contracts.envelope import Failure

from tests.test_candidate_fanout import initial as fanout_initial
from tests.test_candidate_fanout import loop as fanout_loop
from tests.test_graph_engine import World, artifact, complete, log_outputs
from tests.test_graph_engine import engine_world as engine_world
from tests.test_graph_state import node


@pytest.mark.asyncio
async def test_early_anchor_is_checkpointed_reused_and_promoted(engine_world: World) -> None:
    nodes = (node(), node("beta"))
    seen: list[str] = []

    service = RunLifecycle(engine_world.loop(nodes, complete), engine_world.initial(nodes))
    first, concurrent = await asyncio.gather(service.audit_anchor(), service.audit_anchor())
    assert first.id == concurrent.id and first.status == "pending"
    assert engine_world.paths.state_json.is_file()
    assert first.node_name == nodes[0].name and first.seq == 1
    state = await service.start()
    rows = list_node_executions(engine_world.conn, state.run_id)
    seen.extend(row.id for row in rows)
    assert seen[0] == first.id and [row.attempt for row in rows] == [1, 1]
    assert all(row.status == "completed" for row in rows)


@pytest.mark.asyncio
async def test_pause_drains_partial_node_then_same_id_resume(engine_world: World) -> None:
    nodes = (node(), node("beta"))
    entered, release = asyncio.Event(), asyncio.Event()
    control = RunControl()
    attempts = 0

    async def work(invocation: NodeInvocation) -> NodeResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            output = artifact(engine_world)
            log_outputs(engine_world, invocation.execution.id, (output,))
            entered.set()
            await release.wait()
            control.check()
        return NodeResult(status="completed")

    loop = engine_world.loop(nodes, work)
    loop.control = control
    service = RunLifecycle(loop, engine_world.initial(nodes))
    running = asyncio.create_task(service.start())
    await entered.wait()
    pausing = asyncio.create_task(service.pause())
    # Yield once to admit the request, without time-based sleeps or live calls.
    await asyncio.sleep(0)
    assert not pausing.done() and not running.done()
    release.set()
    paused, stopped = await asyncio.gather(pausing, running)
    assert paused.status == stopped.status == "paused" and paused.cursor == 0
    rows = list_node_executions(engine_world.conn, paused.run_id)
    assert rows[0].status == "failed" and rows[0].ended_at is not None
    assert engine_world.conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 1
    resumed = await service.resume()
    assert resumed.run_id == paused.run_id and resumed.status == "completed"
    rows = list_node_executions(engine_world.conn, paused.run_id)
    assert [row.attempt for row in rows] == [1, 2, 1]


@pytest.mark.asyncio
async def test_cancel_dominates_pause_and_duplicate_start(engine_world: World) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def work(invocation: NodeInvocation) -> NodeResult:
        entered.set()
        await release.wait()
        return NodeResult(status="completed")

    nodes = (node(), node("beta"))
    service = RunLifecycle(engine_world.loop(nodes, work), engine_world.initial(nodes))
    running = asyncio.create_task(service.start())
    await entered.wait()
    with pytest.raises(EngineError, match="already executing"):
        await service.start()
    pause, cancel = asyncio.create_task(service.pause()), asyncio.create_task(service.cancel())
    await asyncio.sleep(0)
    assert not pause.done() and not cancel.done()
    release.set()
    states = await asyncio.gather(running, pause, cancel)
    assert all(state.status == "cancelled" and state.cursor == 1 for state in states)
    assert len(list_node_executions(engine_world.conn, states[0].run_id)) == 1
    with pytest.raises(EngineError, match="only a paused"):
        await service.resume()


@pytest.mark.asyncio
async def test_pending_cancellation_never_launches_executor(engine_world: World) -> None:
    async def work(invocation: NodeInvocation) -> NodeResult:
        raise AssertionError("pending cancellation launched tools")

    nodes = (node(),)
    service = RunLifecycle(engine_world.loop(nodes, work), engine_world.initial(nodes))
    state = await service.cancel()
    assert state.status == "cancelled" and state.cursor == 0
    assert not list_node_executions(engine_world.conn, state.run_id)
    row = get_run(engine_world.conn, state.run_id)
    assert row is not None and row.status == "cancelled" and row.ended_at is not None
    with pytest.raises(EngineError, match="terminal"):
        await service.pause()


@pytest.mark.asyncio
async def test_fanout_drain_retains_completed_sibling(engine_world: World) -> None:
    nodes = (node("fit", candidate=True), node("tail"))
    entered, completed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    control = RunControl()

    async def work(invocation: NodeInvocation) -> NodeResult:
        candidate = invocation.candidate
        if candidate is None:
            return NodeResult(status="completed")
        if candidate.public_id == "c_01":
            entered.set()
            await release.wait()
            control.check()
        else:
            completed.set()
        return NodeResult(status="completed")

    loop = fanout_loop(engine_world, nodes, work, control=control, traversal=TraversalPlan())
    service = RunLifecycle(loop, fanout_initial(engine_world, nodes, count=2))
    running = asyncio.create_task(service.start())
    await entered.wait()
    await completed.wait()
    pause = asyncio.create_task(service.pause())
    await asyncio.sleep(0)
    release.set()
    paused, _ = await asyncio.gather(pause, running)
    assert paused.status == "paused" and paused.cursor == 0
    assert not paused.branches[0].results and paused.branches[1].results
    assert all(
        row.status != "running" for row in list_node_executions(engine_world.conn, paused.run_id)
    )
    retained = paused.branches[1].results[0].execution_id
    resumed = await service.resume()
    assert resumed.run_id == paused.run_id and resumed.status == "completed"
    assert resumed.branches[1].results[0].execution_id == retained
    rows = list_node_executions(engine_world.conn, resumed.run_id)
    first = resumed.branches[0].database_id(resumed.run_id)
    assert [row.attempt for row in rows if row.candidate_id == first] == [1, 2]


@pytest.mark.asyncio
async def test_pause_draining_into_gate_remains_dormant_on_resume(engine_world: World) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def work(invocation: NodeInvocation) -> NodeResult:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return NodeResult(status="awaiting_user")

    nodes = (node(),)
    service = RunLifecycle(engine_world.loop(nodes, work), engine_world.initial(nodes))
    running = asyncio.create_task(service.start())
    await entered.wait()
    pause = asyncio.create_task(service.pause())
    await asyncio.sleep(0)
    release.set()
    paused, _ = await asyncio.gather(pause, running)
    assert paused.status == "paused" and paused.pending_result is not None
    resumed = await service.resume()
    assert resumed.status == "awaiting_user" and resumed.pending_result == paused.pending_result
    assert calls == 1


@pytest.mark.asyncio
async def test_candidate_anchor_and_concurrent_start_share_first_attempt(
    engine_world: World,
) -> None:
    nodes = (node("fit", candidate=True), node("tail"))
    loop = fanout_loop(engine_world, nodes, complete, traversal=TraversalPlan())
    service = RunLifecycle(loop, fanout_initial(engine_world, nodes, count=2))
    anchor, state = await asyncio.gather(service.audit_anchor(), service.start())
    first = state.branches[0]
    assert anchor.candidate_id == first.database_id(state.run_id)
    assert first.results[0].execution_id == anchor.id
    rows = list_node_executions(engine_world.conn, state.run_id)
    assert len(rows) == 3 and all(row.attempt == 1 for row in rows)


@pytest.mark.asyncio
async def test_paused_failed_candidates_anchor_real_halted_execution(engine_world: World) -> None:
    nodes = (node("fit", candidate=True), node("tail"))
    entered, release = asyncio.Event(), asyncio.Event()
    admitted = 0

    async def work(invocation: NodeInvocation) -> NodeResult:
        nonlocal admitted
        assert invocation.candidate is not None
        admitted += 1
        if admitted == 2:
            entered.set()
        await release.wait()
        return NodeResult(status="failed", error=Failure(error_class="data", message="offline"))

    loop = fanout_loop(engine_world, nodes, work, traversal=TraversalPlan())
    service = RunLifecycle(loop, fanout_initial(engine_world, nodes, count=2))
    running = asyncio.create_task(service.start())
    await entered.wait()
    pause = asyncio.create_task(service.pause())
    await asyncio.sleep(0)
    release.set()
    paused, _ = await asyncio.gather(pause, running)
    anchor = await service.audit_anchor()
    assert paused.status == "paused" and anchor.status == "failed"
    assert anchor.node_name == "fit" and anchor.run_id == paused.run_id
    assert len(list_node_executions(engine_world.conn, paused.run_id)) == 2
