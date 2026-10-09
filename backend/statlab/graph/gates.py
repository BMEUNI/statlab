"""Durable proposals and caller-scheduled gate continuation under one lifecycle lock."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from statlab_contracts.envelope import Failure
from statlab_contracts.gates import GateAction, GateAnswer

from statlab.domains.base import NodeResult
from statlab.errors import EngineError, PersistenceError
from statlab.graph.control import ControlContext, ControlResponse
from statlab.graph.engine import NodeInvocation
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.router import InterruptClass
from statlab.graph.state import BoundaryResult, RunState
from statlab.persistence.models import GateRow
from statlab.persistence.repository import (
    create_gate,
    finish_node_execution,
    get_gate,
    list_gates,
    transition_gate,
    update_candidate_status,
    update_run_mode,
)


class GateQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node: str
    candidate_id: str | None
    phase: Literal["before", "after", "failure"]
    proposal: dict[str, JsonValue]
    decision_schema: dict[str, JsonValue]
    decision_type: str
    evidence: tuple[JsonValue, ...]
    reasoning: str
    editable: tuple[str, ...]
    default: GateAction = "approve"
    options: tuple[GateAction, ...]
    context: tuple[str, ...]
    revision: int = Field(default=0, ge=0)
    history: tuple[JsonValue, ...] = ()
    failure: Failure | None = None
    awaiting_result: NodeResult | None = None


type Revision = Callable[[GateQuestion, str, type[BaseModel]], Awaitable[BaseModel]]
type GateRewind = Callable[[str, str], Awaitable[str]]


class GateService:
    """Bind approval/decision_override/failure hooks before starting the bound lifecycle."""

    def __init__(
        self,
        lifecycle: RunLifecycle,
        *,
        revise: Revision | None = None,
        rewind: GateRewind | None = None,
    ) -> None:
        self.lifecycle, self.revise, self.rewind = lifecycle, revise, rewind
        self.loop = lifecycle.loop
        self.conn = self.loop.conn
        self.run_id = lifecycle.initial.run_id
        self.loop.failure_hook = self.failure
        self.loop.gate_reconciler = self.reconcile
        self.loop.gate_boundary_hook = self.capture

    def _schema(self, question: GateQuestion) -> type[BaseModel]:
        declared = next(
            spec.decision_schema for spec in self.loop.nodes if spec.name == question.node
        )
        for model in declared.__mro__:
            if (
                issubclass(model, BaseModel)
                and f"{model.__module__}.{model.__qualname__}" == question.decision_type
            ):
                return model
        raise EngineError("gate decision type is outside declared schema inheritance")

    @staticmethod
    def _context(invocation: NodeInvocation) -> tuple[str, ...]:
        candidate = invocation.candidate.results if invocation.candidate is not None else ()
        overlap = (
            invocation.state.traversal.overlap_shared
            if invocation.state.traversal is not None
            else None
        )
        shared = tuple(
            boundary
            for boundary in invocation.state.shared_results
            if boundary.node_name != overlap or invocation.candidate is None
        )
        return tuple(boundary.execution_id for boundary in (*shared, *candidate))

    def _validate(self, gate: GateRow, *, current_schema: bool = True) -> GateQuestion:
        if gate.run_id != self.run_id:
            raise EngineError("gate belongs to another run")
        row = self.conn.execute(
            "SELECT * FROM node_executions WHERE id = ?", (gate.node_execution_id,)
        ).fetchone()
        question = GateQuestion.model_validate(gate.question())
        if (
            row is None
            or row["run_id"] != self.run_id
            or row["node_name"] != question.node
            or row["candidate_id"] != question.candidate_id
        ):
            raise PersistenceError("gate execution ownership mismatch")
        if question.node not in {spec.name for spec in self.loop.nodes}:
            raise EngineError("gate node is undeclared")
        if (
            current_schema
            and question.decision_schema != self._schema(question).model_json_schema()
        ):
            raise EngineError("gate decision schema changed")
        return question

    def check_edits(self, gate: GateRow, edits: dict[str, JsonValue]) -> None:
        """Validate approve_with_edits edits without answering.

        Raises ValidationError or EngineError.
        """
        question = self._validate(gate)
        if not set(edits) <= set(question.editable):
            raise EngineError("edits include a noneditable field")
        self._schema(question).model_validate({**question.proposal, **edits})

    async def _event(self, name: str, payload: dict[str, JsonValue]) -> None:
        if not any(
            event.type == name and event.payload == payload
            for event in await self.loop.events.replay()
        ):
            await self.loop.events.append(name, payload)

    async def _opened(self, gate: GateRow) -> None:
        if any(
            event.type == "gate.opened"
            and event.payload.get("gate_id") == gate.id
            and isinstance(opened := event.payload.get("question"), dict)
            and opened.get("revision") == gate.question().get("revision")
            for event in await self.loop.events.replay()
        ):
            return
        await self._event(
            "gate.opened",
            {
                "gate_id": gate.id,
                "node_execution_id": gate.node_execution_id,
                "question": gate.question(),
            },
        )

    async def _revised(self, gate: GateRow) -> None:
        if any(
            event.type == "gate.revised"
            and event.payload.get("gate_id") == gate.id
            and isinstance(revised := event.payload.get("question"), dict)
            and revised.get("revision") == gate.question().get("revision")
            for event in await self.loop.events.replay()
        ):
            return
        await self._event("gate.revised", {"gate_id": gate.id, "question": gate.question()})

    async def override(
        self, invocation: NodeInvocation, output: type[BaseModel]
    ) -> BaseModel | None:
        """Only an accepted pre-action proposal at the exact original input frontier applies."""
        for gate in reversed(list_gates(self.conn, self.run_id)):
            if gate.status != "prepared" or gate.answer_json is None:
                continue
            question = self._validate(gate)
            if (
                question.phase == "before"
                and question.node == invocation.spec.name
                and question.candidate_id == invocation.execution.candidate_id
                and question.context == self._context(invocation)
            ):
                if output is not self._schema(question):
                    continue
                return output.model_validate(question.proposal)
        return None

    async def approval(
        self,
        invocation: NodeInvocation,
        decision: BaseModel,
        evidence: tuple[JsonValue, ...],
    ) -> BaseModel | None:
        if invocation.state.run_id != self.run_id or invocation.execution.run_id != self.run_id:
            raise EngineError("gate invocation binding mismatch")
        if invocation.state.mode == "autonomous" or invocation.spec.gate_policy == "never":
            return decision
        if not invocation.after_action:
            accepted = await self.override(invocation, type(decision))
            if accepted is not None:
                return accepted
        if type(decision) not in invocation.spec.decision_schema.__mro__:
            raise EngineError("gate proposal type disagrees with declared decision")
        question = GateQuestion(
            node=invocation.spec.name,
            candidate_id=invocation.execution.candidate_id,
            phase="after" if invocation.after_action else "before",
            proposal=decision.model_dump(mode="json"),
            decision_schema=decision.model_json_schema(),
            decision_type=f"{type(decision).__module__}.{type(decision).__qualname__}",
            evidence=evidence,
            reasoning=str(getattr(decision, "rationale", "Review proposal")),
            editable=()
            if invocation.after_action
            else tuple(name for name in type(decision).model_fields if name != "user_summary"),
            options=get_args(GateAction.__value__),
            context=self._context(invocation),
        )
        gate = create_gate(
            self.conn,
            run_id=self.run_id,
            execution_id=invocation.execution.id,
            question=question.model_dump(mode="json"),
        )
        await self._opened(gate)
        return None

    async def failure(self, invocation: NodeInvocation, result: NodeResult) -> NodeResult:
        if result.error is None:
            raise EngineError("failure escalation requires structured failure")
        if invocation.candidate is not None:
            # One model's failure ends only its branch; siblings continue (chunk 58).
            return result
        question = GateQuestion(
            node=invocation.spec.name,
            candidate_id=invocation.execution.candidate_id,
            phase="failure",
            proposal={},
            decision_schema=invocation.spec.decision_schema.model_json_schema(),
            decision_type=f"{invocation.spec.decision_schema.__module__}.{invocation.spec.decision_schema.__qualname__}",
            evidence=(result.model_dump(mode="json"),),
            reasoning=result.error.message,
            editable=(),
            default="abort",
            options=("abort", "rewind", "skip_node")
            if invocation.spec.skippable
            else ("abort", "rewind"),
            context=self._context(invocation),
            failure=result.error,
        )
        gate = create_gate(
            self.conn,
            run_id=self.run_id,
            execution_id=invocation.execution.id,
            question=question.model_dump(mode="json"),
        )
        await self._opened(gate)
        return NodeResult(
            status="awaiting_user",
            summary={
                **result.summary,
                "failure": result.error.model_dump(mode="json"),
                "gate_id": gate.id,
            },
            warnings=result.warnings,
        )

    async def capture(self, invocation: NodeInvocation, result: NodeResult) -> None:
        if result.status != "awaiting_user":
            for gate in list_gates(self.conn, self.run_id):
                if (
                    gate.status == "prepared"
                    and gate.answer_json is not None
                    and (question := self._validate(gate)).phase == "before"
                    and question.node == invocation.spec.name
                    and question.candidate_id == invocation.execution.candidate_id
                    and question.context == self._context(invocation)
                ):
                    transition_gate(self.conn, gate, status="applied", answer_json=gate.answer_json)
            return
        for gate in list_gates(self.conn, self.run_id):
            if gate.node_execution_id == invocation.execution.id and gate.status == "pending":
                question = self._validate(gate).model_copy(update={"awaiting_result": result})
                transition_gate(
                    self.conn,
                    gate,
                    status="pending",
                    answer_json=None,
                    question=question.model_dump(mode="json"),
                )

    def _pending(self, state: RunState, gate: GateRow) -> BoundaryResult | None:
        return next(
            (
                boundary
                for boundary in (
                    state.pending_result,
                    *(branch.pending_result for branch in state.branches),
                )
                if boundary is not None and boundary.execution_id == gate.node_execution_id
            ),
            None,
        )

    async def answer(self, answer: GateAnswer) -> ControlResponse:
        async with self.lifecycle.exclusive():
            gate = get_gate(self.conn, answer.gate_id)
            if gate is None:
                raise EngineError("gate does not exist")
            question = self._validate(gate)
            state = await self.lifecycle.state()
            if (
                gate.status != "pending"
                or gate.answer_json is not None
                or answer.revision != question.revision
                or self._pending(state, gate) is None
                or state.status not in ("awaiting_user", "paused")
            ):
                raise EngineError("gate answer is stale or already applied")
            if answer.action not in question.options:
                raise EngineError("gate action has no safe continuation")
            spec = next(spec for spec in self.loop.nodes if spec.name == question.node)
            if answer.action == "skip_node" and not spec.skippable:
                raise EngineError("node cannot be skipped")
            if answer.action in ("approve_with_edits", "instruct") and question.phase != "before":
                raise EngineError("completed actions require rewind for changes")
            if answer.action == "rewind" and (
                self.rewind is None or answer.target not in {spec.name for spec in self.loop.nodes}
            ):
                raise EngineError("rewind requires a declared target and bound change service")
            if answer.action == "instruct" and self.revise is None:
                raise EngineError("gate revision callback is not configured")
            proposal = question.proposal
            if answer.action in ("approve", "approve_with_edits", "approve_all_remaining"):
                if not set(answer.edits) <= set(question.editable):
                    raise EngineError("edits include a noneditable field")
                validated = self._schema(question).model_validate({**proposal, **answer.edits})
                question = question.model_copy(
                    update={"proposal": validated.model_dump(mode="json")}
                )
            gate = transition_gate(
                self.conn,
                gate,
                status="answered",
                answer_json=answer.model_dump_json(),
                question=question.model_dump(mode="json"),
            )
            await self._answer_event(gate, answer)
            if answer.action not in ("abort", "rewind"):
                await self._prepare(gate, state, answer)
                return ControlResponse(
                    category="decision",
                    text="Proposal revised."
                    if answer.action == "instruct"
                    else "Gate answer applied; continuation ready.",
                )
            return await self._external(gate, answer)

    async def _answer_event(self, gate: GateRow, answer: GateAnswer) -> None:
        await self._event(
            "gate.answered", {"gate_id": gate.id, "answer": answer.model_dump(mode="json")}
        )

    async def _external(self, gate: GateRow, answer: GateAnswer) -> ControlResponse:
        async with self.lifecycle.exclusive():
            if answer.action == "abort":
                if (await self.lifecycle.state()).status != "cancelled":
                    await self.lifecycle.cancel()
                text, child = "Run cancelled.", None
            else:
                if self.rewind is None or answer.target is None:
                    raise EngineError("rewind service is not bound")
                child = await self.rewind(answer.target, gate.id)
                text = "Rewind fork published; continuation ready."
            current = get_gate(self.conn, gate.id)
            if current is not None and current.status == "answered":
                transition_gate(
                    self.conn, current, status="applied", answer_json=current.answer_json
                )
            await self._event(
                "gate.closed",
                {"gate_id": gate.id, "revision": answer.revision, "new_run_id": child},
            )
        return ControlResponse(category="decision", text=text, new_run_id=child)

    async def _prepare(self, gate: GateRow, state: RunState, answer: GateAnswer) -> RunState:
        question = self._validate(gate)
        await self._answer_event(gate, answer)
        if answer.action == "instruct":
            if self.revise is None or answer.instruction is None:
                raise EngineError("revision callback is required for accepted instruction")
            revised = await self.revise(question, answer.instruction, self._schema(question))
            proposal = self._schema(question).model_validate(revised.model_dump(mode="json"))
            question = question.model_copy(
                update={
                    "proposal": proposal.model_dump(mode="json"),
                    "revision": question.revision + 1,
                    "reasoning": str(getattr(proposal, "rationale", "Revised proposal")),
                    "history": (
                        *question.history,
                        {"answer": answer.model_dump(mode="json"), "proposal": question.proposal},
                    ),
                }
            )
            gate = transition_gate(
                self.conn,
                gate,
                status="pending",
                answer_json=None,
                question=question.model_dump(mode="json"),
            )
            await self._revised(gate)
            await self._event(
                "gate.closed", {"gate_id": gate.id, "revision": answer.revision, "new_run_id": None}
            )
            await self._opened(gate)
            return state
        if answer.action == "approve_all_remaining":
            # Durable answer is authority for repairing either side of a mode-write interruption.
            update_run_mode(self.conn, self.run_id, "autonomous")
            self.lifecycle.initial = self.lifecycle.initial.model_copy(
                update={"mode": "autonomous"}
            )
            state = state.model_copy(update={"mode": "autonomous"})
        pending = self._pending(state, gate)
        if pending is not None:
            boundary: BoundaryResult | None = None
            if answer.action == "skip_node":
                boundary = pending.model_copy(
                    update={"result": NodeResult(status="skipped", skip_reason=answer.reason)}
                )
            elif question.phase == "after":
                boundary = pending.model_copy(
                    update={"result": pending.result.model_copy(update={"status": "completed"})}
                )
            owner = next(
                (
                    branch
                    for branch in state.branches
                    if branch.database_id(self.run_id) == question.candidate_id
                ),
                None,
            )
            if owner is None:
                repeated_design = (
                    state.traversal is not None
                    and state.traversal.design_node == question.node
                    and state.spine[state.cursor].name != question.node
                )
                state = state.model_copy(
                    update={
                        "pending_result": None,
                        "cursor": state.cursor + (1 if boundary and not repeated_design else 0),
                        "shared_results": (*state.shared_results, boundary)
                        if boundary
                        else state.shared_results,
                    }
                )
            else:
                if state.traversal is not None:
                    start, end = state.traversal.region(state.spine)
                    position = max(start, owner.position)
                else:
                    position, end = state.cursor, len(state.spine)
                changed = owner.model_copy(
                    update={
                        "pending_result": None,
                        "position": position + (1 if boundary else 0),
                        "status": "completed" if boundary and position + 1 == end else "running",
                        "results": (*owner.results, boundary) if boundary else owner.results,
                    }
                )
                state = state.model_copy(
                    update={
                        "branches": tuple(
                            changed if branch.public_id == owner.public_id else branch
                            for branch in state.branches
                        ),
                        "cursor": state.cursor + (1 if boundary and state.traversal is None else 0),
                        "pending_result": None if state.traversal is None else state.pending_result,
                    }
                )
            status = (
                "paused"
                if state.status == "paused"
                else "awaiting_user"
                if (
                    state.pending_result is not None
                    or any(branch.status == "awaiting_user" for branch in state.branches)
                )
                else "pending"
            )
            state = RunState.model_validate({**state.model_dump(), "status": status})
            # Checkpoint first; reconciliation can finish SQL/event writes without node replay.
            self.loop.checkpointer.save(state)
            if boundary is not None:
                finish_node_execution(self.conn, boundary.execution_id, boundary.result)
                payload: dict[str, JsonValue] = {
                    "node": boundary.node_name,
                    "execution_id": boundary.execution_id,
                    "result": boundary.result.model_dump(mode="json"),
                }
                if owner is not None and state.traversal is not None:
                    payload["candidate_id"] = owner.public_id
                await self._event(f"node.{boundary.result.status}", payload)
            if owner is not None:
                changed = next(
                    branch for branch in state.branches if branch.public_id == owner.public_id
                )
                update_candidate_status(self.conn, changed.database_id(self.run_id), changed.status)
        self.loop.checkpointer.save(state)
        await self.loop._status(
            state, state.spine[state.cursor].name if state.cursor < len(state.spine) else None
        )
        transition_gate(
            self.conn,
            gate,
            status="prepared"
            if question.phase == "before" and answer.action != "skip_node"
            else "applied",
            answer_json=gate.answer_json,
        )
        await self._event(
            "gate.closed", {"gate_id": gate.id, "revision": answer.revision, "new_run_id": None}
        )
        return state

    async def reconcile(self, state: RunState) -> RunState:
        """Boot preparation seam: no task creation; unanswered gates remain dormant."""
        for gate in list_gates(self.conn, self.run_id):
            question = self._validate(gate, current_schema=gate.status != "applied")
            for entry in question.history:
                if isinstance(entry, dict) and "answer" in entry:
                    prior = GateAnswer.model_validate(entry["answer"])
                    await self._answer_event(gate, prior)
                    await self._event(
                        "gate.closed",
                        {"gate_id": gate.id, "revision": prior.revision, "new_run_id": None},
                    )
            if question.history:
                await self._revised(gate)
            if gate.status == "pending":
                if self._pending(state, gate) is None:
                    state = self._restore_pending(state, gate, question)
                    self.loop.checkpointer.save(state)
                    gate = get_gate(self.conn, gate.id) or gate
                await self._opened(gate)
            elif gate.status == "answered":
                answer = GateAnswer.model_validate_json(gate.answer_json or "null")
                if answer.action in ("abort", "rewind"):
                    await self._external(gate, answer)
                    state = await self.lifecycle.state()
                else:
                    state = await self._prepare(gate, state, answer)
            else:
                answer = GateAnswer.model_validate_json(gate.answer_json or "null")
                await self._answer_event(gate, answer)
                if answer.action in ("abort", "rewind"):
                    await self._external(gate, answer)
                else:
                    await self._event(
                        "gate.closed",
                        {"gate_id": gate.id, "revision": answer.revision, "new_run_id": None},
                    )
        return state

    def _restore_pending(self, state: RunState, gate: GateRow, question: GateQuestion) -> RunState:
        index = next(i for i, spec in enumerate(self.loop.nodes) if spec.name == question.node)
        owner = next(
            (
                branch
                for branch in state.branches
                if branch.database_id(self.run_id) == question.candidate_id
            ),
            None,
        )
        position = (
            max(state.traversal.region(state.spine)[0], owner.position)
            if owner is not None and state.traversal is not None
            else state.cursor
        )
        repeated_design = (
            state.traversal is not None
            and state.traversal.design_node == question.node
            and state.wave > 1
            and sum(item.node_name == question.node for item in state.shared_results)
            == state.wave - 1
        )
        if index != position and not repeated_design:
            raise PersistenceError("unanswered gate disagrees with checkpoint frontier")
        result = question.awaiting_result
        if result is None:
            # A process may stop after an after-action gate opens but before the node's
            # full result is captured. There is no truthful acknowledgment/replay path.
            if question.phase == "after":
                question = question.model_copy(
                    update={
                        "phase": "failure",
                        "failure": Failure(
                            error_class="bug",
                            message="Completed action outcome was not checkpointed",
                        ),
                        "options": ("abort", "rewind", "skip_node")
                        if self.loop.nodes[index].skippable
                        else ("abort", "rewind"),
                        "default": "abort",
                        "revision": question.revision + 1,
                    }
                )
                transition_gate(
                    self.conn,
                    gate,
                    status="pending",
                    answer_json=None,
                    question=question.model_dump(mode="json"),
                )
            result = NodeResult(
                status="awaiting_user", summary={"decision": question.proposal, "gate_id": gate.id}
            )
        boundary = BoundaryResult(
            node_name=question.node, execution_id=gate.node_execution_id, result=result
        )
        if owner is not None and state.traversal is not None:
            changed = owner.model_copy(
                update={"status": "awaiting_user", "pending_result": boundary}
            )
            state = state.model_copy(
                update={
                    "branches": tuple(
                        changed if branch.public_id == owner.public_id else branch
                        for branch in state.branches
                    )
                }
            )
        else:
            state = state.model_copy(update={"pending_result": boundary})
        return RunState.model_validate(
            {
                **state.model_dump(),
                "status": "paused" if state.status == "paused" else "awaiting_user",
            }
        )

    async def handle(
        self, message: str, classified: InterruptClass, context: ControlContext
    ) -> ControlResponse:
        if (
            classified.category != "decision"
            or context.conn is not self.conn
            or context.state.run_id != self.run_id
        ):
            raise EngineError("gate control binding mismatch")
        answer = await context.llm.complete(
            context.conn,
            run_id=self.run_id,
            node_execution_id=context.audit_execution_id,
            model=context.router.model_for("interrupt_router"),
            reasoning=False,
            output=GateAnswer,
            messages=(
                {
                    "role": "system",
                    "content": "Parse an explicit gate answer only. Use the exact gate_id/revision "
                    "and declared target. Never invent edits or approval. Pending gates: "
                    + str(
                        [
                            gate.model_dump(mode="json")
                            for gate in list_gates(self.conn, self.run_id)
                            if gate.status == "pending"
                        ]
                    ),
                },
                {"role": "user", "content": message},
            ),
            recovery=context.recovery,
        )
        return await self.answer(answer)
