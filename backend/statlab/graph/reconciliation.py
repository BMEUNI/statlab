"""Boot-only recovery at committed node boundaries; callers own returned tasks."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue, TypeAdapter
from statlab_contracts.envelope import Failure

from statlab.domains.base import NodeResult
from statlab.errors import EngineError, PersistenceError
from statlab.events import Event
from statlab.graph.engine import RunLoop, RunTracker
from statlab.graph.state import BoundaryResult, RunState
from statlab.persistence.models import NodeExecutionRow, RunRow
from statlab.persistence.repository import (
    finish_node_execution,
    interrupt_node_execution,
    list_node_executions,
    list_pending_gates,
    list_runs,
    update_candidate_status,
)


@dataclass(frozen=True, slots=True)
class RecoveryBinding:
    loop: RunLoop
    initial: RunState


type BindingResolver = Callable[[RunRow], RecoveryBinding]


@dataclass(frozen=True, slots=True)
class RecoveryBatch:
    tasks: tuple[asyncio.Task[RunState], ...]
    dormant: tuple[RunState, ...]


class RestartReconciler:
    def __init__(
        self, *, conn: sqlite3.Connection, resolver: BindingResolver, tracker: RunTracker
    ) -> None:
        self.conn, self.resolver, self.tracker = conn, resolver, tracker

    async def reconcile(self, project_id: str) -> RecoveryBatch:
        """Prepare the complete scan before creating even the first execution task."""
        runnable: list[RecoveryBinding] = []
        dormant: list[RunState] = []
        try:
            for row in list_runs(self.conn, project_id):
                if row.status in ("completed", "failed", "cancelled", "superseded"):
                    continue
                binding = self.resolver(row)
                loop = binding.loop
                if loop.tracker is not self.tracker:
                    raise EngineError("recovery requires shared run tracker")
                state = await loop.load_state(binding.initial, row)
                if row.status in ("paused", "awaiting_user"):
                    # Validate projection rather than bypassing RunState's pending-result rules.
                    state = RunState.model_validate({**state.model_dump(), "status": row.status})
                await self._repair_boundaries(loop, state)
                if row.status == "running":
                    await self._interrupt(loop, state)
                if state.status not in ("pending", "running"):
                    loop.checkpointer.save(state)
                    if row.status != state.status:
                        await loop._status(
                            state,
                            state.spine[state.cursor].name
                            if state.cursor < len(state.spine)
                            else None,
                        )
                    if state.status == "awaiting_user":
                        # RunState validates the awaiting boundary before this point.
                        pending_boundaries = (
                            state.pending_result,
                            *(
                                branch.pending_result
                                for branch in state.branches
                                if branch.status == "awaiting_user"
                            ),
                        )
                        for pending in pending_boundaries:
                            if pending is not None:
                                await self._gates(loop, state.run_id, pending.execution_id)
                    dormant.append(state)
                else:
                    runnable.append(binding)
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise PersistenceError("restart preparation failed", cause=exc) from exc
        return RecoveryBatch(
            tasks=tuple(
                asyncio.create_task(binding.loop.run(binding.initial)) for binding in runnable
            ),
            dormant=tuple(dormant),
        )

    async def _terminal_event(
        self,
        loop: RunLoop,
        boundary: BoundaryResult,
        events: tuple[Event, ...],
        candidate_id: str | None = None,
    ) -> None:
        if boundary.result.status == "awaiting_user":
            return
        matching = tuple(
            event
            for event in events
            if event.type in ("node.completed", "node.skipped", "node.failed")
            and event.payload.get("execution_id") == boundary.execution_id
        )
        payload: dict[str, JsonValue] = {
            "node": boundary.node_name,
            "execution_id": boundary.execution_id,
            "result": boundary.result.model_dump(mode="json"),
        }
        if candidate_id is not None:
            payload["candidate_id"] = candidate_id
        if matching:
            if any(
                event.type != f"node.{boundary.result.status}" or event.payload != payload
                for event in matching
            ):
                raise PersistenceError("conflicting node terminal event")
        else:
            await loop.events.append(f"node.{boundary.result.status}", payload)

    async def _repair_boundaries(self, loop: RunLoop, state: RunState) -> None:
        for branch in state.branches:
            candidate = self.conn.execute(
                "SELECT run_id, spec_json, wave FROM candidates WHERE id = ?",
                (branch.database_id(state.run_id),),
            ).fetchone()
            if candidate is not None and (
                candidate["run_id"] != state.run_id
                or candidate["spec_json"] != branch.spec.model_dump_json()
                or candidate["wave"] != branch.wave
            ):
                raise PersistenceError("candidate identity mismatch")
            if candidate is not None and state.traversal is not None:
                update_candidate_status(self.conn, branch.database_id(state.run_id), branch.status)
        rows = {row.id: row for row in list_node_executions(self.conn, state.run_id)}
        boundaries = (
            *state.shared_results,
            *(boundary for branch in state.branches for boundary in branch.results),
            *(
                branch.pending_result
                for branch in state.branches
                if branch.pending_result is not None
            ),
            *((state.pending_result,) if state.pending_result else ()),
        )
        events = await loop.events.replay()
        for boundary in boundaries:
            row = rows.get(boundary.execution_id)
            seq = next(
                i + 1 for i, entry in enumerate(state.spine) if entry.name == boundary.node_name
            )
            entry = state.spine[seq - 1]
            owner = next(
                (
                    branch
                    for branch in state.branches
                    if boundary in branch.results or boundary == branch.pending_result
                ),
                None,
            )
            candidate_id = (
                owner.database_id(state.run_id)
                if owner is not None and entry.kind == "PER_CANDIDATE"
                else None
            )
            if (
                row is None
                or row.node_name != boundary.node_name
                or row.seq != seq
                or row.candidate_id != candidate_id
            ):
                raise PersistenceError("checkpoint execution ownership mismatch")
            if row.status not in ("pending", "running", boundary.result.status):
                raise PersistenceError("conflicting checkpoint node outcome")
            if (
                row.status != boundary.result.status
                or row.summary != json.dumps(boundary.result.summary)
                or row.error
                != (boundary.result.error.model_dump_json() if boundary.result.error else None)
                or row.ended_at is None
            ):
                finish_node_execution(self.conn, row.id, boundary.result)
            await self._terminal_event(
                loop,
                boundary,
                events,
                owner.public_id if owner is not None and state.traversal is not None else None,
            )

    async def _interrupt(self, loop: RunLoop, state: RunState) -> None:
        failure = Failure(error_class="bug", message="process terminated")
        events = await loop.events.replay()
        for row in list_node_executions(self.conn, state.run_id):
            if row.status == "running":
                interrupt_node_execution(self.conn, row.id)
            elif row.status != "failed" or row.error != failure.model_dump_json():
                continue
            # Recover an interruption write whose event append died on the previous boot.
            summary = self._summary(row)
            await self._terminal_event(
                loop,
                BoundaryResult(
                    node_name=row.node_name,
                    execution_id=row.id,
                    result=NodeResult(status="failed", error=failure, summary=summary),
                ),
                events,
                next(
                    (
                        branch.public_id
                        for branch in state.branches
                        if branch.database_id(state.run_id) == row.candidate_id
                    ),
                    None,
                )
                if state.traversal is not None
                else None,
            )

    @staticmethod
    def _summary(row: NodeExecutionRow) -> dict[str, JsonValue]:
        return (
            TypeAdapter(dict[str, JsonValue]).validate_json(row.summary, strict=True)
            if row.summary is not None
            else {}
        )

    async def _gates(self, loop: RunLoop, run_id: str, execution_id: str) -> None:
        gates = list_pending_gates(self.conn, run_id=run_id, execution_id=execution_id)
        questions = tuple(gate.question() for gate in gates)
        for gate, question in zip(gates, questions, strict=True):
            await loop.events.append(
                "gate.opened",
                {
                    "gate_id": gate.id,
                    "node_execution_id": gate.node_execution_id,
                    "question": question,
                },
            )
