"""Offline gate regressions; authored but deliberately not executed in B12."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError
from statlab.graph.engine import NodeInvocation
from statlab.graph.gates import GateQuestion, GateService
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.reconciliation import RecoveryBinding, RestartReconciler
from statlab.persistence.models import RunRow
from statlab.persistence.repository import (
    create_gate,
    create_node_execution,
    create_run,
    get_gate,
    get_run,
    list_gates,
    transition_gate,
    update_run_mode,
)
from statlab_contracts.envelope import Failure
from statlab_contracts.gates import GateAnswer

from tests.test_graph_engine import World, artifact, log_outputs
from tests.test_graph_engine import engine_world as engine_world


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rationale: str
    count: int = Field(gt=0)


class Composite(Proposal):
    outcome: str


def declared(*, skippable: bool = True) -> tuple[NodeSpec, ...]:
    return (NodeSpec("proposal", "SHARED", skippable, Composite, (), "always"),)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "instruct"},
        {"action": "skip_node", "reason": " "},
        {"action": "rewind"},
        {"action": "approve", "edits": {"count": 2}},
        {"action": "abort", "instruction": "change"},
        {"action": "approve", "extra": True},
    ],
)
def test_seven_action_contract_rejects_missing_or_extraneous_fields(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GateAnswer.model_validate({"gate_id": "g_one", **payload})


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "approve_with_edits", "approve_all_remaining"])
async def test_pre_action_override_keeps_actual_mro_type_and_avoids_new_proposal(
    engine_world: World,
    action: Literal["approve", "approve_with_edits", "approve_all_remaining"],
) -> None:
    nodes = declared()
    update_run_mode(engine_world.conn, engine_world.paths.run_id, "guided")
    proposals = 0
    dispatched: list[int] = []
    seen: list[NodeInvocation] = []

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal proposals
        seen.append(invocation)
        accepted = await service.override(invocation, Proposal)
        if accepted is None:
            proposals += 1
            accepted = Proposal(rationale="Proposed once", count=1)
        approved = await service.approval(invocation, accepted, ())
        if approved is None:
            return NodeResult(
                status="awaiting_user", summary={"decision": accepted.model_dump(mode="json")}
            )
        dispatched.append(Proposal.model_validate(approved.model_dump()).count)
        return NodeResult(status="completed")

    initial = engine_world.initial(nodes).model_copy(update={"mode": "guided"})
    lifecycle = RunLifecycle(engine_world.loop(nodes, execute), initial)
    service = GateService(lifecycle)
    assert (await lifecycle.start()).status == "awaiting_user"
    gate = list_gates(engine_world.conn, initial.run_id)[0]
    question = GateQuestion.model_validate(gate.question())
    assert question.decision_type.endswith(".Proposal")
    answer = GateAnswer(
        gate_id=gate.id,
        action=action,
        edits={"count": 2} if action == "approve_with_edits" else {},
    )
    await service.answer(answer)
    assert await service.override(seen[0], Composite) is None
    assert (await lifecycle.start()).status == "completed"
    assert proposals == 1 and dispatched == [2 if action == "approve_with_edits" else 1]
    with pytest.raises(EngineError, match="stale"):
        await service.answer(answer)
    if action == "approve_all_remaining":
        row = get_run(engine_world.conn, initial.run_id)
        assert row is not None and row.mode == "autonomous"
        assert lifecycle.initial.mode == (await lifecycle.state()).mode == "autonomous"
    assert any(event.type == "gate.closed" for event in await lifecycle.loop.events.replay())


@pytest.mark.asyncio
async def test_edits_validate_before_persistence_and_instruction_keeps_revision_history(
    engine_world: World,
) -> None:
    nodes = declared()
    update_run_mode(engine_world.conn, engine_world.paths.run_id, "guided")

    async def execute(invocation: NodeInvocation) -> NodeResult:
        await service.approval(invocation, Proposal(rationale="Original", count=1), ())
        return NodeResult(status="awaiting_user")

    async def revise(
        question: GateQuestion, instruction: str, schema: type[BaseModel]
    ) -> BaseModel:
        assert instruction == "Use two" and schema is Proposal
        assert question.proposal["count"] == 1
        return Proposal(rationale="Revised", count=2)

    lifecycle = RunLifecycle(
        engine_world.loop(nodes, execute),
        engine_world.initial(nodes).model_copy(update={"mode": "guided"}),
    )
    service = GateService(lifecycle, revise=revise)
    await lifecycle.start()
    gate = list_gates(engine_world.conn, lifecycle.initial.run_id)[0]
    with pytest.raises(ValidationError):
        await service.answer(
            GateAnswer(gate_id=gate.id, action="approve_with_edits", edits={"count": 0})
        )
    assert get_gate(engine_world.conn, gate.id) == gate
    await service.answer(GateAnswer(gate_id=gate.id, action="instruct", instruction="Use two"))
    revised = get_gate(engine_world.conn, gate.id)
    assert revised is not None and revised.status == "pending" and revised.answer_json is None
    question = GateQuestion.model_validate(revised.question())
    assert question.revision == 1 and question.proposal["count"] == 2 and len(question.history) == 1
    with pytest.raises(EngineError, match="stale"):
        await service.answer(GateAnswer(gate_id=gate.id, action="approve"))
    await service.answer(
        GateAnswer(gate_id=gate.id, revision=1, action="skip_node", reason="Operator excluded")
    )
    state = await lifecycle.start()
    assert (
        state.status == "completed"
        and state.shared_results[0].result.skip_reason == "Operator excluded"
    )
    assert await service.reconcile(state) == state
    events = await lifecycle.loop.events.replay()
    assert sum(event.type == "gate.revised" for event in events) == 1
    assert sum(event.type == "gate.closed" for event in events) == 2


@pytest.mark.asyncio
async def test_after_action_acknowledgment_retains_artifacts_without_executor_replay(
    engine_world: World,
) -> None:
    nodes = declared()
    update_run_mode(engine_world.conn, engine_world.paths.run_id, "guided")
    calls = 0
    output = artifact(engine_world)

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal calls
        calls += 1
        log_outputs(engine_world, invocation.execution.id, (output,))
        await service.approval(
            replace(invocation, after_action=True),
            Composite(rationale="Already done", count=1, outcome="scored"),
            (),
        )
        return NodeResult(
            status="awaiting_user",
            artifacts=(output.ref,),
            metrics={"score": 0.8},
            summary={"done": True},
        )

    lifecycle = RunLifecycle(
        engine_world.loop(nodes, execute),
        engine_world.initial(nodes).model_copy(update={"mode": "guided"}),
    )
    service = GateService(lifecycle)
    await lifecycle.start()
    gate = list_gates(engine_world.conn, lifecycle.initial.run_id)[0]
    with pytest.raises(EngineError, match="rewind"):
        await service.answer(
            GateAnswer(gate_id=gate.id, action="approve_with_edits", edits={"count": 2})
        )
    await service.answer(GateAnswer(gate_id=gate.id, action="approve"))
    state = await lifecycle.start()
    assert state.status == "completed" and calls == 1
    assert state.shared_results[0].result.artifacts == (output.ref,)
    assert state.shared_results[0].result.metrics == {"score": 0.8}
    assert state.shared_results[0].result.summary == {"done": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["abort", "rewind"])
async def test_autonomous_failure_has_truthful_actions_and_never_replays(
    engine_world: World,
    action: Literal["abort", "rewind"],
) -> None:
    nodes = declared(skippable=False)
    calls = 0
    rewinds: list[tuple[str, str]] = []

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal calls
        calls += 1
        return NodeResult(
            status="failed",
            error=Failure(error_class="bug", message="Dispatched result unavailable"),
        )

    async def rewind(target: str, request_id: str) -> str:
        rewinds.append((target, request_id))
        return "r_child"

    lifecycle = RunLifecycle(engine_world.loop(nodes, execute), engine_world.initial(nodes))
    service = GateService(lifecycle, rewind=rewind)
    assert (await lifecycle.start()).status == "awaiting_user"
    gate = list_gates(engine_world.conn, lifecycle.initial.run_id)[0]
    question = GateQuestion.model_validate(gate.question())
    assert question.options == ("abort", "rewind") and question.failure is not None
    with pytest.raises(EngineError, match="safe"):
        await service.answer(GateAnswer(gate_id=gate.id, action="approve"))
    answer = GateAnswer(
        gate_id=gate.id,
        action=action,
        target="proposal" if action == "rewind" else None,
    )
    response = await service.answer(answer)
    assert calls == 1
    if action == "abort":
        assert (await lifecycle.state()).status == "cancelled"
    else:
        assert response.new_run_id == "r_child" and rewinds == [("proposal", gate.id)]


@pytest.mark.asyncio
async def test_unanswered_gate_restart_remains_dormant_without_duplicate_open(
    engine_world: World,
) -> None:
    nodes = declared()
    update_run_mode(engine_world.conn, engine_world.paths.run_id, "guided")
    calls = 0

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal calls
        calls += 1
        await service.approval(invocation, Proposal(rationale="Wait indefinitely", count=1), ())
        return NodeResult(status="awaiting_user")

    lifecycle = RunLifecycle(
        engine_world.loop(nodes, execute),
        engine_world.initial(nodes).model_copy(update={"mode": "guided"}),
    )
    service = GateService(lifecycle)
    await lifecycle.start()
    row = get_run(engine_world.conn, lifecycle.initial.run_id)
    assert row is not None

    def resolve(run: RunRow) -> RecoveryBinding:
        assert run.id == lifecycle.initial.run_id
        return RecoveryBinding(lifecycle.loop, lifecycle.initial, lifecycle)

    batch = await RestartReconciler(
        conn=engine_world.conn, resolver=resolve, tracker=lifecycle.loop.tracker
    ).reconcile(row.project_id)
    assert batch.tasks == () and batch.dormant[0].status == "awaiting_user" and calls == 1
    assert (
        len(
            [event for event in await lifecycle.loop.events.replay() if event.type == "gate.opened"]
        )
        == 1
    )
    gate = list_gates(engine_world.conn, row.id)[0]
    foreign = create_run(
        engine_world.conn,
        project_id=row.project_id,
        contract_id=row.contract_id,
        mode="guided",
        config_json="{}",
    )
    execution = create_node_execution(
        engine_world.conn, run_id=foreign.id, node_name="proposal", seq=1
    )
    alien = create_gate(
        engine_world.conn, run_id=foreign.id, execution_id=execution.id, question=gate.question()
    )
    with pytest.raises(EngineError, match="another run"):
        await service.answer(GateAnswer(gate_id=alien.id, action="approve"))
    with pytest.raises(EngineError, match="does not exist"):
        await service.answer(GateAnswer(gate_id="g_foreign", action="approve"))


@pytest.mark.asyncio
async def test_durable_accepted_answer_prepares_once_after_restart(engine_world: World) -> None:
    nodes = declared()
    update_run_mode(engine_world.conn, engine_world.paths.run_id, "guided")
    work = 0

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal work
        work += 1
        approved = await service.approval(
            replace(invocation, after_action=True),
            Composite(rationale="Completed work", count=1, outcome="done"),
            (),
        )
        assert approved is None
        return NodeResult(status="awaiting_user", metrics={"score": 0.9})

    lifecycle = RunLifecycle(
        engine_world.loop(nodes, execute),
        engine_world.initial(nodes).model_copy(update={"mode": "guided"}),
    )
    service = GateService(lifecycle)
    state = await lifecycle.start()
    gate = list_gates(engine_world.conn, state.run_id)[0]
    answer = GateAnswer(gate_id=gate.id, action="approve_all_remaining")
    transition_gate(
        engine_world.conn, gate, status="answered", answer_json=answer.model_dump_json()
    )
    ready = await service.reconcile(state)
    assert ready.cursor == 1 and ready.mode == "autonomous"
    assert await service.reconcile(ready) == ready
    assert (await lifecycle.start()).status == "completed" and work == 1
    closed = [
        event for event in await lifecycle.loop.events.replay() if event.type == "gate.closed"
    ]
    assert len(closed) == 1


@pytest.mark.asyncio
async def test_gate_answer_rejects_execution_and_pending_stop_races(engine_world: World) -> None:
    nodes = declared()
    entered, release = asyncio.Event(), asyncio.Event()

    async def execute(invocation: NodeInvocation) -> NodeResult:
        entered.set()
        await release.wait()
        return NodeResult(status="completed")

    lifecycle = RunLifecycle(engine_world.loop(nodes, execute), engine_world.initial(nodes))
    service = GateService(lifecycle)
    running = asyncio.create_task(lifecycle.start())
    await entered.wait()
    with pytest.raises(EngineError, match="drained"):
        await service.answer(GateAnswer(gate_id="g_foreign", action="abort"))
    stopping = asyncio.create_task(lifecycle.pause())
    await asyncio.sleep(0)
    with pytest.raises(EngineError, match="drained"):
        await service.answer(GateAnswer(gate_id="g_foreign", action="abort"))
    release.set()
    await asyncio.gather(running, stopping)
