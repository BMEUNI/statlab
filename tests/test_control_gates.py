"""Offline gate regressions; authored but deliberately not executed in B12."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError, PersistenceError
from statlab.graph import TraversalPlan
from statlab.graph.engine import NodeInvocation
from statlab.graph.gates import GateQuestion, GateRewind, GateService
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.reconciliation import RecoveryBinding, RestartReconciler
from statlab.graph.router import InterruptClass
from statlab.graph.state import BoundaryResult, RunState
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
from statlab_contracts.artifacts import ArtifactMeta
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import Failure
from statlab_contracts.gates import GateAnswer, RewindIntent

from tests.test_candidate_fanout import initial as fanout_initial
from tests.test_candidate_fanout import loop as fanout_loop
from tests.test_control_router import FakeClient, context
from tests.test_graph_engine import World, artifact, log_outputs
from tests.test_graph_engine import engine_world as engine_world
from tests.test_graph_state import node


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rationale: str
    count: int = Field(gt=0)


class Composite(Proposal):
    outcome: str


class Unrelated(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rationale: str


def declared(*, skippable: bool = True) -> tuple[NodeSpec, ...]:
    return (NodeSpec("proposal", "SHARED", skippable, Composite, (), "always"),)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "instruct"},
        {"action": "skip_node", "reason": " "},
        {"action": "rewind"},
        {"action": "rewind", "target": " "},
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


def test_rewind_intent_rejects_blank_target() -> None:
    with pytest.raises(ValidationError):
        RewindIntent.model_validate({"target": " "})


def test_rewind_intent_needs_target_or_edits() -> None:
    with pytest.raises(ValidationError):
        RewindIntent.model_validate({})
    assert RewindIntent(edits={"split.test_size": 0.3}).target is None


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
    assert question.options == (
        "approve",
        "approve_with_edits",
        "instruct",
        "approve_all_remaining",
        "skip_node",
        "rewind",
        "abort",
    )
    answer = GateAnswer(
        gate_id=gate.id,
        action=action,
        edits={"count": 2} if action == "approve_with_edits" else {},
    )
    await service.answer(answer)
    assert (
        await service.override(
            replace(seen[0], spec=NodeSpec("other", "SHARED", True, Composite, (), "always")),
            Proposal,
        )
        is None
    )
    assert await service.override(seen[0], Composite) is None
    assert (await lifecycle.start()).status == "completed"
    assert await service.override(seen[0], Proposal) is None
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
    outputs: list[ArtifactMeta] = []

    async def execute(invocation: NodeInvocation) -> NodeResult:
        nonlocal calls
        calls += 1
        output = artifact(engine_world)
        outputs.append(output)
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
    (output,) = outputs
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


async def shared_gate(
    world: World, *, after: bool = False, rewind: GateRewind | None = None
) -> tuple[RunLifecycle, GateService, list[NodeInvocation]]:
    nodes = declared()
    update_run_mode(world.conn, world.paths.run_id, "guided")
    seen: list[NodeInvocation] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        seen.append(inv)
        accepted = await service.override(inv, Proposal)
        if accepted is not None:
            return NodeResult(status="completed")
        assert (
            await service.approval(
                replace(inv, after_action=after),
                Composite(rationale="Proposal", count=1, outcome="done")
                if after
                else Proposal(rationale="Proposal", count=1),
                (),
            )
            is None
        )
        return NodeResult(status="awaiting_user", summary={"waiting": True})

    lifecycle = RunLifecycle(
        world.loop(nodes, execute), world.initial(nodes).model_copy(update={"mode": "guided"})
    )
    service = GateService(lifecycle, rewind=rewind)
    assert (await lifecycle.start()).status == "awaiting_user"
    return (lifecycle, service, seen)


async def candidate_gate(
    world: World, count: int = 2
) -> tuple[RunLifecycle, GateService, list[str], RunState]:
    nodes = (
        node("foundation"),
        NodeSpec("prepare", "PER_CANDIDATE", True, Composite, (), "always"),
        node("compare"),
    )
    update_run_mode(world.conn, world.paths.run_id, "guided")
    calls: list[str] = []

    async def execute(inv: NodeInvocation) -> NodeResult:
        if (
            inv.spec.name == "prepare"
            and inv.candidate is not None
            and inv.candidate.public_id == "c_01"
        ):
            calls.append("c_01")
            accepted = await service.override(inv, Proposal)
            if accepted is None:
                assert (
                    await service.approval(
                        inv, Proposal(rationale="Candidate proposal", count=1), ()
                    )
                    is None
                )
                return NodeResult(status="awaiting_user")
            return NodeResult(
                status="completed",
                summary={"count": Proposal.model_validate(accepted.model_dump()).count},
            )
        return NodeResult(status="completed", summary={"node": inv.spec.name})

    lifecycle = RunLifecycle(
        fanout_loop(world, nodes, execute, traversal=TraversalPlan() if count == 2 else None),
        fanout_initial(world, nodes, count=count).model_copy(update={"mode": "guided"}),
    )
    service = GateService(lifecycle)
    first = await lifecycle.start()
    assert first.status == "awaiting_user"
    assert first.branches[0].public_id == "c_01"
    gate = list_gates(world.conn, first.run_id)[0]
    assert GateQuestion.model_validate(gate.question()).candidate_id == first.branches[
        0
    ].database_id(first.run_id)
    return (lifecycle, service, calls, first)


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2], ids=["single", "fanout"])
@pytest.mark.parametrize("action", ["approve", "skip_node"])
async def test_candidate_gate_continuation_updates_owner_branch(
    engine_world: World, action: Literal["approve", "skip_node"], count: int
) -> None:
    lifecycle, service, calls, first = await candidate_gate(engine_world, count)
    gate = list_gates(engine_world.conn, first.run_id)[0]
    if action == "approve":
        answer = GateAnswer(gate_id=gate.id, action="approve")
    else:
        answer = GateAnswer(
            gate_id=gate.id, action="skip_node", reason="Operator excluded candidate step"
        )
    await service.answer(answer)
    if action == "skip_node":
        mid_state = await lifecycle.state()
        mid = next(b for b in mid_state.branches if b.public_id == "c_01")
        if count == 2:
            assert mid.position == 2
            assert mid.status == "completed"
        else:
            assert mid_state.cursor == 2
        assert len([r for r in mid.results if r.node_name == "prepare"]) == 1
    final = await lifecycle.start()
    assert final.status == "completed"
    branch = next(b for b in final.branches if b.public_id == "c_01")
    if count == 2:
        assert branch.status == "completed"
    else:
        prepares = [r for r in branch.results if r.node_name == "prepare"]
        assert len(prepares) == 1 and final.cursor == 3
        assert prepares[0].result.status == ("completed" if action == "approve" else "skipped")
    boundary = next(r for r in branch.results if r.node_name == "prepare")
    if action == "approve":
        assert calls == ["c_01", "c_01"]
        assert boundary.result.summary == {"count": 1}
    else:
        assert calls == ["c_01"]
        assert boundary.result.status == "skipped"
        assert boundary.result.skip_reason == "Operator excluded candidate step"
        if count == 2:
            assert any(
                event.type == "node.skipped" and event.payload["candidate_id"] == "c_01"
                for event in await lifecycle.loop.events.replay()
            )
    applied = get_gate(engine_world.conn, gate.id)
    assert applied is not None and applied.status == "applied"


@pytest.mark.asyncio
async def test_restart_restores_candidate_gate_missing_from_checkpoint(
    engine_world: World,
) -> None:
    lifecycle, service, _calls, first = await candidate_gate(engine_world)
    state = await lifecycle.state()
    stripped = RunState.model_validate(
        {
            **state.model_dump(),
            "status": "running",
            "branches": [
                {**b.model_dump(), "pending_result": None, "status": "running"}
                if b.public_id == "c_01"
                else b.model_dump()
                for b in state.branches
            ],
        }
    )
    lifecycle.loop.checkpointer.save(stripped)
    restored = await service.reconcile(stripped)
    new = next(b for b in restored.branches if b.public_id == "c_01")
    old = next(b for b in state.branches if b.public_id == "c_01")
    assert new.status == "awaiting_user" and new.pending_result == old.pending_result
    assert restored.status == "awaiting_user"
    gate = list_gates(engine_world.conn, first.run_id)[0]
    await service.answer(GateAnswer(gate_id=gate.id, action="approve"))
    assert (await lifecycle.start()).status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["captured", "uncaptured_before", "uncaptured_after"])
async def test_restart_restores_unanswered_shared_gate_missing_from_checkpoint(
    engine_world: World, case: str
) -> None:
    lifecycle, service, seen = await shared_gate(engine_world, after=case == "uncaptured_after")
    conn = engine_world.conn
    gate = list_gates(conn, lifecycle.initial.run_id)[0]
    if case == "captured":
        assert gate.question()["awaiting_result"] is not None
    else:
        gate = transition_gate(
            conn,
            gate,
            status="pending",
            answer_json=None,
            question={**gate.question(), "awaiting_result": None},
        )
    state = await lifecycle.state()
    stripped = RunState.model_validate(
        {**state.model_dump(), "pending_result": None, "status": "running"}
    )
    lifecycle.loop.checkpointer.save(stripped)
    restored = await service.reconcile(stripped)
    assert restored.status == "awaiting_user"
    assert restored.pending_result is not None
    assert restored.pending_result.execution_id == gate.node_execution_id
    if case == "captured":
        assert restored.pending_result == state.pending_result
        await service.answer(GateAnswer(gate_id=gate.id, action="approve"))
        assert (await lifecycle.start()).status == "completed"
        assert len(seen) == 2
    elif case == "uncaptured_before":
        assert restored.pending_result.result.summary == {
            "decision": GateQuestion.model_validate(gate.question()).proposal,
            "gate_id": gate.id,
        }
        await service.answer(GateAnswer(gate_id=gate.id, action="approve"))
        assert (await lifecycle.start()).status == "completed"
        assert len(seen) == 2
    else:
        reread = get_gate(conn, gate.id)
        assert reread is not None
        question = GateQuestion.model_validate(reread.question())
        assert question.phase == "failure" and question.revision == 1
        assert question.options == ("abort", "rewind", "skip_node")
        assert question.default == "abort"
        assert question.failure is not None
        assert question.failure.error_class == "bug"
        assert question.failure.message == "Completed action outcome was not checkpointed"
        await service.answer(
            GateAnswer(gate_id=gate.id, revision=1, action="skip_node", reason="Outcome lost")
        )
        final = await lifecycle.start()
        assert final.status == "completed"
        assert final.shared_results[0].result.status == "skipped"
        assert len(seen) == 1
    opened = [e for e in await lifecycle.loop.events.replay() if e.type == "gate.opened"]
    assert len(opened) == (2 if case == "uncaptured_after" else 1)


@pytest.mark.asyncio
async def test_restart_rejects_unanswered_gate_beyond_checkpoint_frontier(
    engine_world: World,
) -> None:
    lifecycle, service, _seen = await shared_gate(engine_world)
    state = await lifecycle.state()
    stripped = RunState.model_validate(
        {
            **state.model_dump(),
            "pending_result": None,
            "status": "running",
            "cursor": 1,
            "shared_results": [
                BoundaryResult(
                    node_name="proposal",
                    execution_id="ne_other",
                    result=NodeResult(status="completed"),
                ).model_dump()
            ],
        }
    )
    lifecycle.loop.checkpointer.save(stripped)
    with pytest.raises(
        PersistenceError, match="unanswered gate disagrees with checkpoint frontier"
    ):
        await service.reconcile(stripped)


@pytest.mark.asyncio
async def test_restart_prepares_answered_gate_after_checkpoint_advanced(
    engine_world: World,
) -> None:
    lifecycle, service, seen = await shared_gate(engine_world)
    conn = engine_world.conn
    gate = list_gates(conn, lifecycle.initial.run_id)[0]
    transition_gate(
        conn,
        gate,
        status="answered",
        answer_json=GateAnswer(gate_id=gate.id, action="approve").model_dump_json(),
    )
    state = await lifecycle.state()
    stripped = RunState.model_validate(
        {**state.model_dump(), "pending_result": None, "status": "pending"}
    )
    lifecycle.loop.checkpointer.save(stripped)
    ready = await service.reconcile(stripped)
    assert ready.pending_result is None
    prepared = get_gate(conn, gate.id)
    assert prepared is not None and prepared.status == "prepared"
    assert (await lifecycle.start()).status == "completed"
    assert len(seen) == 2
    applied = get_gate(conn, gate.id)
    assert applied is not None and applied.status == "applied"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["abort", "rewind", "instruct"])
async def test_restart_external_answers(
    engine_world: World, action: Literal["abort", "rewind", "instruct"]
) -> None:
    lifecycle, service, _seen = await shared_gate(engine_world)
    conn = engine_world.conn
    gate = list_gates(conn, lifecycle.initial.run_id)[0]
    if action == "abort":
        answer = GateAnswer(gate_id=gate.id, action="abort")
    elif action == "rewind":
        answer = GateAnswer(gate_id=gate.id, action="rewind", target="proposal")
    else:
        answer = GateAnswer(gate_id=gate.id, action="instruct", instruction="Use two")
    transition_gate(conn, gate, status="answered", answer_json=answer.model_dump_json())
    state = await lifecycle.state()
    if action == "abort":
        await service.reconcile(state)
        assert (await lifecycle.state()).status == "cancelled"
        done = get_gate(conn, gate.id)
        assert done is not None and done.status == "applied"
        await service.reconcile(await lifecycle.state())
        closed = [e for e in await lifecycle.loop.events.replay() if e.type == "gate.closed"]
        assert len(closed) == 1
    elif action == "rewind":
        with pytest.raises(EngineError, match="rewind service is not bound"):
            await service.reconcile(state)
    else:
        with pytest.raises(
            EngineError, match="revision callback is required for accepted instruction"
        ):
            await service.reconcile(state)


@pytest.mark.asyncio
async def test_gate_service_rejects_corrupted_questions_and_bindings(
    engine_world: World, minimal_contract: DataContract
) -> None:
    lifecycle, service, seen = await shared_gate(engine_world)
    conn = engine_world.conn
    original = list_gates(conn, lifecycle.initial.run_id)[0]
    inv = seen[0]
    approve = GateAnswer(gate_id=original.id, action="approve")
    current = original
    cases: list[tuple[dict[str, JsonValue], type[Exception], str]] = [
        ({"decision_type": "tests.Unknown"}, EngineError, "outside declared schema inheritance"),
        ({"node": "other"}, PersistenceError, "gate execution ownership mismatch"),
        ({"decision_schema": {}}, EngineError, "gate decision schema changed"),
    ]
    for change, error, match in cases:
        mutated = transition_gate(
            conn,
            current,
            status="pending",
            answer_json=None,
            question={**original.question(), **change},
        )
        with pytest.raises(error, match=match):
            await service.answer(approve)
        current = transition_gate(
            conn, mutated, status="pending", answer_json=None, question=original.question()
        )
    mutated = transition_gate(
        conn,
        current,
        status="pending",
        answer_json=None,
        question={**original.question(), "history": ["noise"]},
    )
    await service.reconcile(await lifecycle.state())
    assert not any(e.type == "gate.answered" for e in await lifecycle.loop.events.replay())
    transition_gate(conn, mutated, status="pending", answer_json=None, question=original.question())
    with pytest.raises(EngineError, match="rewind requires a declared target and bound change"):
        await service.answer(GateAnswer(gate_id=original.id, action="rewind", target="proposal"))
    with pytest.raises(EngineError, match="gate revision callback is not configured"):
        await service.answer(
            GateAnswer(gate_id=original.id, action="instruct", instruction="Use two")
        )
    with pytest.raises(EngineError, match="gate invocation binding mismatch"):
        await service.approval(
            replace(inv, state=inv.state.model_copy(update={"run_id": "r_other"})),
            Proposal(rationale="x", count=1),
            (),
        )
    with pytest.raises(EngineError, match="gate proposal type disagrees with declared decision"):
        await service.approval(inv, Unrelated(rationale="Not declared"), ())
    with pytest.raises(EngineError, match="failure escalation requires structured failure"):
        await service.failure(inv, NodeResult(status="completed"))
    with pytest.raises(EngineError, match="gate control binding mismatch"):
        await service.handle(
            "approve",
            InterruptClass(category="qa"),
            context(conn, minimal_contract, FakeClient()),
        )
    execution = create_node_execution(conn, run_id=original.run_id, node_name="ghost", seq=2)
    ghost = create_gate(
        conn,
        run_id=original.run_id,
        execution_id=execution.id,
        question={**original.question(), "node": "ghost"},
    )
    with pytest.raises(EngineError, match="gate node is undeclared"):
        await service.answer(GateAnswer(gate_id=ghost.id, action="approve"))


@pytest.mark.asyncio
async def test_restart_repairs_mode_written_before_gate_preparation(engine_world: World) -> None:
    lifecycle, _service, seen = await shared_gate(engine_world)
    conn = engine_world.conn
    gate = list_gates(conn, lifecycle.initial.run_id)[0]
    transition_gate(
        conn,
        gate,
        status="answered",
        answer_json=GateAnswer(gate_id=gate.id, action="approve_all_remaining").model_dump_json(),
    )
    update_run_mode(conn, lifecycle.initial.run_id, "autonomous")
    lifecycle.initial = lifecycle.initial.model_copy(update={"mode": "autonomous"})
    final = await lifecycle.start()
    assert final.status == "completed" and final.mode == "autonomous"
    assert len(seen) == 2
    assert list_gates(conn, lifecycle.initial.run_id)[0].status == "applied"
