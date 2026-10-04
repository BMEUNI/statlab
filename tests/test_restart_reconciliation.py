"""Boot recovery, crash windows, dormant gates and durable audit preservation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError, PersistenceError
from statlab.graph import (
    BoundaryResult,
    Checkpointer,
    NodeExecutor,
    NodeInvocation,
    RecoveryBatch,
    RecoveryBinding,
    RestartReconciler,
    RunState,
    RunTracker,
)
from statlab.persistence.models import ProjectRow, RunRow, RunStatus
from statlab.persistence.repository import (
    claim_test_scoring,
    create_node_execution,
    ensure_candidate,
    finish_node_execution,
    get_run,
    interrupt_node_execution,
    list_node_executions,
    list_pending_gates,
    set_test_fingerprint,
    update_run_status,
)
from statlab.storage import get_artifact
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import Failure

from tests.test_graph_engine import (
    World,
    artifact,
    complete,
    log_outputs,
    new_world,
)
from tests.test_graph_engine import (
    engine_world as engine_world,
)
from tests.test_graph_state import candidate_spec, node


def service(
    world: World,
    nodes: tuple[NodeSpec, ...],
    executor: NodeExecutor = complete,
    *,
    candidate: bool = False,
) -> RestartReconciler:
    tracker = RunTracker()

    def resolve(row: RunRow) -> RecoveryBinding:
        assert row.id == world.paths.run_id
        return RecoveryBinding(
            world.loop(nodes, executor, tracker), world.initial(nodes, candidate=candidate)
        )

    return RestartReconciler(conn=world.conn, resolver=resolve, tracker=tracker)


async def recover(reconciler: RestartReconciler, world: World) -> RecoveryBatch:
    row = get_run(world.conn, world.paths.run_id)
    assert row is not None
    return await reconciler.reconcile(row.project_id)


def boundary(world: World, nodes: tuple[NodeSpec, ...], result: NodeResult) -> RunState:
    initial = world.initial(nodes)
    execution = create_node_execution(
        world.conn, run_id=initial.run_id, node_name=nodes[0].name, seq=1
    )
    retained = BoundaryResult(node_name=nodes[0].name, execution_id=execution.id, result=result)
    if result.status in ("completed", "skipped"):
        state = RunState(
            run_id=initial.run_id,
            spine_id=initial.spine_id,
            spine=initial.spine,
            mode=initial.mode,
            status="running",
            cursor=1,
            shared_results=(retained,),
        )
    else:
        state = RunState(
            run_id=initial.run_id,
            spine_id=initial.spine_id,
            spine=initial.spine,
            mode=initial.mode,
            status="failed" if result.status == "failed" else "awaiting_user",
            pending_result=retained,
        )
    Checkpointer(world.paths).save(state)
    update_run_status(world.conn, state.run_id, status="running")
    return state


@pytest.mark.asyncio
async def test_pending_start_and_running_without_execution(engine_world: World) -> None:
    nodes = (node(),)
    batch = await recover(service(engine_world, nodes), engine_world)
    assert not batch.dormant and len(batch.tasks) == 1
    assert (await batch.tasks[0]).status == "completed"


@pytest.mark.asyncio
async def test_running_input_without_node_resumes(engine_world: World) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    Checkpointer(engine_world.paths).save(initial)
    update_run_status(engine_world.conn, initial.run_id, status="running")
    batch = await recover(service(engine_world, nodes), engine_world)
    assert (await batch.tasks[0]).status == "completed"
    assert [row.attempt for row in list_node_executions(engine_world.conn, initial.run_id)] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "superseded"])
async def test_terminal_sql_is_untouched_without_resolver(
    engine_world: World, status: RunStatus
) -> None:
    update_run_status(engine_world.conn, engine_world.paths.run_id, status=status)
    engine_world.paths.state_json.write_text("corrupt")
    engine_world.paths.events_jsonl.write_text("corrupt\n")
    before = get_run(engine_world.conn, engine_world.paths.run_id)

    def resolve(row: RunRow) -> RecoveryBinding:
        raise AssertionError("terminal resolver must not run")

    reconciler = RestartReconciler(conn=engine_world.conn, resolver=resolve, tracker=RunTracker())
    batch = await recover(reconciler, engine_world)
    assert batch == RecoveryBatch(tasks=(), dormant=())
    assert get_run(engine_world.conn, engine_world.paths.run_id) == before
    assert engine_world.paths.state_json.read_text() == "corrupt"
    assert engine_world.paths.events_jsonl.read_text() == "corrupt\n"


@pytest.mark.asyncio
async def test_paused_sql_projects_old_running_checkpoint_and_stays_dormant(
    engine_world: World,
) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    Checkpointer(engine_world.paths).save(initial.model_copy(update={"status": "running"}))
    update_run_status(engine_world.conn, initial.run_id, status="paused")
    batch = await recover(service(engine_world, nodes), engine_world)
    assert not batch.tasks and batch.dormant[0].status == "paused"
    assert not list_node_executions(engine_world.conn, initial.run_id)
    assert (
        Checkpointer(engine_world.paths).load(spine_id=initial.spine_id, spine=initial.spine)
        == batch.dormant[0]
    )


def gate(
    world: World,
    execution_id: str,
    gate_id: str,
    *,
    question: str = '{"prompt":"Continue?"}',
    status: str = "pending",
    answer: str | None = None,
) -> None:
    world.conn.execute(
        "INSERT INTO gates VALUES (?, ?, ?, ?, ?, ?, ?)",
        (gate_id, world.paths.run_id, execution_id, question, answer, status, "now"),
    )


@pytest.mark.asyncio
async def test_awaiting_republishes_actual_pending_gates_only(engine_world: World) -> None:
    nodes = (node(),)
    state = boundary(engine_world, nodes, NodeResult(status="awaiting_user"))
    assert state.pending_result is not None
    execution_id = state.pending_result.execution_id
    finish_node_execution(engine_world.conn, execution_id, NodeResult(status="awaiting_user"))
    update_run_status(engine_world.conn, state.run_id, status="awaiting_user")
    Checkpointer(engine_world.paths).save(state.model_copy(update={"status": "running"}))
    gate(engine_world, execution_id, "actual")
    gate(engine_world, execution_id, "answered", answer="{}")
    gate(engine_world, execution_id, "closed", status="answered")
    stale = create_node_execution(
        engine_world.conn, run_id=state.run_id, node_name="alpha", seq=1, attempt=2
    )
    gate(engine_world, stale.id, "stale", question="invalid stale JSON")
    reconciler = service(engine_world, nodes)
    batch = await recover(reconciler, engine_world)
    assert not batch.tasks and batch.dormant[0].status == "awaiting_user"
    events = await reconciler.resolver(get_run_row(engine_world)).loop.events.replay()
    assert [(event.type, event.payload) for event in events] == [
        (
            "gate.opened",
            {
                "gate_id": "actual",
                "node_execution_id": execution_id,
                "question": {"prompt": "Continue?"},
            },
        )
    ]
    assert len(engine_world.conn.execute("SELECT * FROM gates").fetchall()) == 4
    assert list_node_executions(engine_world.conn, state.run_id)[1].status == "running"


def get_run_row(world: World) -> RunRow:
    row = get_run(world.conn, world.paths.run_id)
    assert row is not None
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["bad", "[]", '{"x":NaN}'])
async def test_malformed_gate_fails_closed(engine_world: World, question: str) -> None:
    nodes = (node(),)
    state = boundary(engine_world, nodes, NodeResult(status="awaiting_user"))
    assert state.pending_result is not None
    update_run_status(engine_world.conn, state.run_id, status="awaiting_user")
    gate(engine_world, state.pending_result.execution_id, "bad", question=question)
    with pytest.raises(PersistenceError, match="gate question"):
        await recover(service(engine_world, nodes), engine_world)
    assert not engine_world.paths.events_jsonl.exists()


@pytest.mark.asyncio
async def test_awaiting_without_pending_result_fails_closed(engine_world: World) -> None:
    nodes = (node(),)
    Checkpointer(engine_world.paths).save(engine_world.initial(nodes))
    update_run_status(engine_world.conn, engine_world.paths.run_id, status="awaiting_user")
    with pytest.raises(PersistenceError, match="preparation"):
        await recover(service(engine_world, nodes), engine_world)
    assert not list_node_executions(engine_world.conn, engine_world.paths.run_id)


@pytest.mark.asyncio
async def test_cancelled_mid_candidate_recovery_preserves_prefix_and_crash_audit(
    engine_world: World,
) -> None:
    nodes = (node(), node("beta", candidate=True), node("gamma", candidate=True), node("delta"))
    tracker = RunTracker()
    started = asyncio.Event()
    seen: list[str] = []
    crashed_ref = None
    retained_refs: list[ArtifactRef] = []

    async def crash(invocation: NodeInvocation) -> NodeResult:
        nonlocal crashed_ref
        seen.append(invocation.spec.name)
        if invocation.state.cursor == 2:
            meta = artifact(engine_world)
            crashed_ref = meta.ref
            log_outputs(engine_world, invocation.execution.id, (meta,))
            engine_world.conn.execute(
                "UPDATE node_executions SET summary = ? WHERE id = ?",
                ('{"partial":"preserved"}', invocation.execution.id),
            )
            started.set()
            await asyncio.Event().wait()
        retained = artifact(engine_world)
        retained_refs.append(retained.ref)
        log_outputs(engine_world, invocation.execution.id, (retained,))
        return NodeResult(status="completed", artifacts=(retained.ref,))

    initial = engine_world.initial(nodes, candidate=True)
    loop = engine_world.loop(nodes, crash, tracker)
    task = asyncio.create_task(loop.run(initial))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tracker.concurrency == 0 and seen == ["alpha", "beta", "gamma"]
    assert claim_test_scoring(engine_world.conn, initial.run_id)
    assert set_test_fingerprint(engine_world.conn, initial.run_id, "locked-test")
    before_run = get_run_row(engine_world)
    before_candidate = dict(engine_world.conn.execute("SELECT * FROM candidates").fetchone())
    before_tools = tuple(dict(row) for row in engine_world.conn.execute("SELECT * FROM tool_calls"))
    recovered: list[str] = []

    async def execute(invocation: NodeInvocation) -> NodeResult:
        recovered.append(invocation.spec.name)
        assert invocation.state.run_id == initial.run_id
        return NodeResult(status="completed")

    reconciler = service(engine_world, nodes, execute, candidate=True)
    batch = await recover(reconciler, engine_world)
    assert len(batch.tasks) == 1
    result = await batch.tasks[0]
    assert result.status == "completed" and recovered == ["gamma", "delta"]
    assert result.candidate is not None and result.candidate.public_id == "c_01"
    assert result.candidate.spec == candidate_spec() and len(result.candidate.results) == 2
    assert result.retained_refs == frozenset(retained_refs)
    assert all(engine_world.paths.artifact(ref, "dataframe").exists() for ref in retained_refs)
    rows = list_node_executions(engine_world.conn, initial.run_id)
    assert [(row.node_name, row.attempt, row.status) for row in rows] == [
        ("alpha", 1, "completed"),
        ("beta", 1, "completed"),
        ("gamma", 1, "failed"),
        ("gamma", 2, "completed"),
        ("delta", 1, "completed"),
    ]
    assert (
        rows[2].error == Failure(error_class="bug", message="process terminated").model_dump_json()
    )
    assert rows[2].ended_at and rows[2].summary == '{"partial":"preserved"}'
    assert (
        tuple(dict(row) for row in engine_world.conn.execute("SELECT * FROM tool_calls"))
        == before_tools
    )
    assert (
        dict(engine_world.conn.execute("SELECT * FROM candidates").fetchone()) == before_candidate
    )
    assert crashed_ref is not None
    artifact_row = get_artifact(engine_world.conn, str(crashed_ref))
    assert (
        artifact_row is not None
        and artifact_row.pruned_at is None
        and Path(artifact_row.path).exists()
    )
    after_run = get_run_row(engine_world)
    assert after_run.test_scored_at == before_run.test_scored_at
    assert (
        after_run.test_fingerprint == "locked-test"
        and after_run.forked_from_run_id == before_run.forked_from_run_id
    )
    events = await reconciler.resolver(after_run).loop.events.replay()
    assert (
        sum(
            event.type == "node.failed" and event.payload["execution_id"] == rows[2].id
            for event in events
        )
        == 1
    )


@pytest.mark.asyncio
async def test_sql_outcome_ahead_replays_new_attempt(engine_world: World) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    Checkpointer(engine_world.paths).save(initial.model_copy(update={"status": "running"}))
    execution = create_node_execution(
        engine_world.conn, run_id=initial.run_id, node_name="alpha", seq=1
    )
    finish_node_execution(engine_world.conn, execution.id, NodeResult(status="completed"))
    update_run_status(engine_world.conn, initial.run_id, status="running")
    batch = await recover(service(engine_world, nodes), engine_world)
    assert (await batch.tasks[0]).status == "completed"
    assert [
        (row.attempt, row.status) for row in list_node_executions(engine_world.conn, initial.run_id)
    ] == [(1, "completed"), (2, "completed")]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "skipped", "failed", "awaiting_user", "paused"])
async def test_checkpoint_ahead_repairs_outcome_status_and_missing_event(
    engine_world: World, outcome: str
) -> None:
    nodes = (node(skippable=True),)
    if outcome in ("completed", "paused"):
        result = NodeResult(status="completed")
    elif outcome == "skipped":
        result = NodeResult(status="skipped", skip_reason="unused")
    elif outcome == "failed":
        result = NodeResult(status="failed", error=Failure(error_class="data", message="bad data"))
    else:
        result = NodeResult(status="awaiting_user")
    state = boundary(engine_world, nodes, result)
    if outcome in ("completed", "skipped", "paused"):
        state = RunState.model_validate(
            {**state.model_dump(), "status": "paused" if outcome == "paused" else "completed"}
        )
        Checkpointer(engine_world.paths).save(state)
    reconciler = service(engine_world, nodes)
    batch = await recover(reconciler, engine_world)
    assert not batch.tasks and batch.dormant == (state,)
    assert get_run_row(engine_world).status == state.status
    row = list_node_executions(engine_world.conn, state.run_id)[0]
    assert row.status == result.status and row.ended_at
    events = await reconciler.resolver(get_run_row(engine_world)).loop.events.replay()
    assert [event.type for event in events] == (
        [] if result.status == "awaiting_user" else [f"node.{result.status}"]
    ) + ["run.status"]
    second = await recover(service(engine_world, nodes), engine_world)
    assert not second.tasks
    assert len(await reconciler.resolver(get_run_row(engine_world)).loop.events.replay()) == len(
        events
    )


@pytest.mark.asyncio
async def test_running_final_cursor_repairs_missing_event_without_dispatch(
    engine_world: World,
) -> None:
    nodes = (node(),)
    state = boundary(engine_world, nodes, NodeResult(status="completed"))
    reconciler = service(engine_world, nodes)
    batch = await recover(reconciler, engine_world)
    assert (await batch.tasks[0]).status == "completed"
    assert len(list_node_executions(engine_world.conn, state.run_id)) == 1
    events = await reconciler.resolver(get_run_row(engine_world)).loop.events.replay()
    assert sum(event.type == "node.completed" for event in events) == 1


@pytest.mark.asyncio
async def test_interruption_event_is_repaired_and_not_duplicated(engine_world: World) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    Checkpointer(engine_world.paths).save(initial)
    update_run_status(engine_world.conn, initial.run_id, status="running")
    execution = create_node_execution(
        engine_world.conn, run_id=initial.run_id, node_name="alpha", seq=1
    )
    interrupt_node_execution(engine_world.conn, execution.id)
    reconciler = service(engine_world, nodes)
    # Preparation then scheduling is atomic with respect to task dispatch.
    batch = await recover(reconciler, engine_world)
    batch.tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch.tasks[0]
    batch = await recover(service(engine_world, nodes), engine_world)
    await batch.tasks[0]
    events = await reconciler.resolver(get_run_row(engine_world)).loop.events.replay()
    assert sum(event.type == "node.failed" for event in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "corrupt",
        "wrong_spine",
        "wrong_run",
        "wrong_mode",
        "missing_execution",
        "conflicting_outcome",
        "conflicting_event",
        "candidate",
    ],
)
async def test_corruption_and_ownership_fail_closed(engine_world: World, defect: str) -> None:
    nodes = (node(),)
    state = boundary(engine_world, nodes, NodeResult(status="completed"))
    if defect == "missing":
        engine_world.paths.state_json.unlink()
    elif defect == "corrupt":
        engine_world.paths.state_json.write_text("bad")
    elif defect in ("wrong_spine", "wrong_run", "wrong_mode"):
        field, value = {
            "wrong_spine": ("spine_id", "wrong"),
            "wrong_run": ("run_id", "wrong"),
            "wrong_mode": ("mode", "guided"),
        }[defect]
        engine_world.paths.state_json.write_text(
            state.model_copy(update={field: value}).model_dump_json()
        )
    elif defect == "missing_execution":
        engine_world.conn.execute("DELETE FROM node_executions")
    elif defect == "conflicting_outcome":
        finish_node_execution(
            engine_world.conn,
            state.shared_results[0].execution_id,
            NodeResult(status="failed", error=Failure(error_class="data", message="bad")),
        )
    elif defect == "conflicting_event":
        loop = engine_world.loop(nodes, complete)
        await loop.events.append(
            "node.failed", {"execution_id": state.shared_results[0].execution_id}
        )
    else:
        candidate = engine_world.initial(nodes, candidate=True).candidate
        assert candidate is not None
        ensure_candidate(
            engine_world.conn,
            candidate_id=candidate.database_id(state.run_id),
            run_id=state.run_id,
            spec=candidate.spec,
        )
        engine_world.conn.execute(
            "UPDATE candidates SET spec_json = '{}' WHERE run_id = ?", (state.run_id,)
        )
        Checkpointer(engine_world.paths).save(state.model_copy(update={"candidate": candidate}))
    with pytest.raises((PersistenceError, EngineError)):
        await recover(service(engine_world, nodes), engine_world)
    assert len(list_node_executions(engine_world.conn, state.run_id)) <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["binding_run", "binding_spine", "binding_mode", "tracker"])
async def test_invalid_binding_refused_before_side_effects(
    engine_world: World, defect: str
) -> None:
    nodes = (node(),)
    tracker = RunTracker()
    loop = engine_world.loop(nodes, complete, tracker)
    initial = engine_world.initial(nodes)
    if defect == "binding_run":
        initial = initial.model_copy(update={"run_id": "wrong"})
    elif defect == "binding_spine":
        initial = initial.model_copy(update={"spine": ()})
    elif defect == "binding_mode":
        initial = initial.model_copy(update={"mode": "guided"})
    elif defect == "tracker":
        loop.tracker = RunTracker()
    reconciler = RestartReconciler(
        conn=engine_world.conn, resolver=lambda row: RecoveryBinding(loop, initial), tracker=tracker
    )
    with pytest.raises(EngineError):
        await recover(reconciler, engine_world)
    assert not engine_world.paths.state_json.exists() and not list_node_executions(
        engine_world.conn, engine_world.paths.run_id
    )


@pytest.mark.asyncio
async def test_later_preparation_error_leaks_no_task(
    engine_world: World, project: ProjectRow, minimal_contract: DataContract, tmp_path: Path
) -> None:
    second = new_world(engine_world.conn, project, minimal_contract, tmp_path)
    nodes = (node(),)
    tracker = RunTracker()
    invoked = False

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal invoked
        invoked = True
        return NodeResult(status="completed")

    # list_runs sorts newest first: second prepares successfully; first fails afterwards.
    update_run_status(engine_world.conn, engine_world.paths.run_id, status="running")

    def resolve(row: RunRow) -> RecoveryBinding:
        world = second if row.id == second.paths.run_id else engine_world
        return RecoveryBinding(world.loop(nodes, execute, tracker), world.initial(nodes))

    reconciler = RestartReconciler(conn=engine_world.conn, resolver=resolve, tracker=tracker)
    before = asyncio.all_tasks()
    with pytest.raises(PersistenceError, match="missing"):
        await reconciler.reconcile(project.id)
    await asyncio.sleep(0)
    assert asyncio.all_tasks() == before and not invoked
    assert not list_node_executions(engine_world.conn, second.paths.run_id)


@pytest.mark.asyncio
async def test_recovery_task_cancellation_propagates(engine_world: World) -> None:
    marker = asyncio.CancelledError("preserved")

    async def execute(invocation: NodeInvocation) -> NodeResult:
        raise marker

    batch = await recover(service(engine_world, (node(),), execute), engine_world)
    with pytest.raises(asyncio.CancelledError) as raised:
        await batch.tasks[0]
    assert raised.value is marker
    assert list_node_executions(engine_world.conn, engine_world.paths.run_id)[0].status == "running"


def test_gate_ownership_and_recovery_reader_boundaries(engine_world: World) -> None:
    with pytest.raises(PersistenceError, match="ownership"):
        list_pending_gates(
            engine_world.conn, run_id=engine_world.paths.run_id, execution_id="missing"
        )
    engine_world.conn.close()
    with pytest.raises(PersistenceError, match="executions"):
        list_node_executions(engine_world.conn, "run")
    with pytest.raises(PersistenceError, match="gates"):
        list_pending_gates(engine_world.conn, run_id="run", execution_id="node")
    with pytest.raises(PersistenceError, match="interrupt"):
        interrupt_node_execution(engine_world.conn, "node")


@pytest.mark.asyncio
async def test_scan_closed_database_is_domain_error(engine_world: World) -> None:
    reconciler = service(engine_world, ())
    engine_world.conn.close()
    with pytest.raises(PersistenceError, match="preparation"):
        await reconciler.reconcile("project")


@pytest.mark.asyncio
async def test_reset_pending_with_deleted_checkpoint_keeps_old_attempt(engine_world: World) -> None:
    nodes = (node(),)
    execution = create_node_execution(
        engine_world.conn, run_id=engine_world.paths.run_id, node_name="alpha", seq=1
    )
    before = list_node_executions(engine_world.conn, execution.run_id)
    with pytest.raises(PersistenceError, match="durable history"):
        await recover(service(engine_world, nodes), engine_world)
    assert list_node_executions(engine_world.conn, execution.run_id) == before
    assert get_run_row(engine_world).status == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("column,value", [("node_name", "foreign"), ("seq", 2)])
async def test_checkpoint_execution_topology_mismatch(
    engine_world: World, column: str, value: str | int
) -> None:
    nodes = (node(),)
    state = boundary(engine_world, nodes, NodeResult(status="completed"))
    if column == "node_name":
        engine_world.conn.execute("UPDATE node_executions SET node_name = ?", (value,))
    else:
        engine_world.conn.execute("UPDATE node_executions SET seq = ?", (value,))
    with pytest.raises(PersistenceError, match="ownership"):
        await recover(service(engine_world, nodes), engine_world)
    assert get_run_row(engine_world).status == "running"
    assert (
        Checkpointer(engine_world.paths).load(spine_id=state.spine_id, spine=state.spine) == state
    )


@pytest.mark.asyncio
async def test_foreign_execution_and_gate_ownership_refused(
    engine_world: World, project: ProjectRow, minimal_contract: DataContract, tmp_path: Path
) -> None:
    second = new_world(engine_world.conn, project, minimal_contract, tmp_path)
    nodes = (node(),)
    state = boundary(engine_world, nodes, NodeResult(status="awaiting_user"))
    assert state.pending_result is not None
    with pytest.raises(PersistenceError, match="ownership"):
        list_pending_gates(
            engine_world.conn,
            run_id=second.paths.run_id,
            execution_id=state.pending_result.execution_id,
        )
    foreign = create_node_execution(
        engine_world.conn, run_id=second.paths.run_id, node_name="alpha", seq=1
    )
    other_boundary = BoundaryResult(
        node_name="alpha", execution_id=foreign.id, result=NodeResult(status="awaiting_user")
    )
    Checkpointer(engine_world.paths).save(
        RunState(
            run_id=state.run_id,
            spine_id=state.spine_id,
            spine=state.spine,
            mode=state.mode,
            status="awaiting_user",
            pending_result=other_boundary,
        )
    )
    # Prepare only the target run; terminal rows do not call the resolver.
    update_run_status(engine_world.conn, second.paths.run_id, status="completed")
    with pytest.raises(PersistenceError, match="ownership"):
        await recover(service(engine_world, nodes), engine_world)


@pytest.mark.asyncio
async def test_malformed_interrupted_summary_fails_before_dispatch(engine_world: World) -> None:
    nodes = (node(),)
    initial = engine_world.initial(nodes)
    Checkpointer(engine_world.paths).save(initial)
    update_run_status(engine_world.conn, initial.run_id, status="running")
    create_node_execution(engine_world.conn, run_id=initial.run_id, node_name="alpha", seq=1)
    engine_world.conn.execute("UPDATE node_executions SET summary = '[]'")
    with pytest.raises(PersistenceError, match="preparation"):
        await recover(service(engine_world, nodes), engine_world)
    assert len(list_node_executions(engine_world.conn, initial.run_id)) == 1
