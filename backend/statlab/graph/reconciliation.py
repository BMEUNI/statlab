"""Boot-only recovery at committed node boundaries; callers own returned tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue, TypeAdapter
from statlab_contracts.envelope import Failure

from statlab.domains.base import NodeResult
from statlab.errors import EngineError, PersistenceError
from statlab.events import Event
from statlab.graph.engine import RunLoop, RunTracker
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.state import BoundaryResult, RunState
from statlab.persistence.models import NodeExecutionRow, RunRow
from statlab.persistence.repository import (
    ensure_candidate,
    finish_node_execution,
    get_run,
    interrupt_node_execution,
    list_gates,
    list_node_executions,
    list_pending_gates,
    list_runs,
    update_candidate_status,
)
from statlab.storage import validate_checkpoint_ownership

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecoveryBinding:
    loop: RunLoop
    initial: RunState
    lifecycle: RunLifecycle | None = None


type BindingResolver = Callable[[RunRow], RecoveryBinding | None]


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
        return await self.reconcile_projects((project_id,))

    async def reconcile_projects(
        self, project_ids: tuple[str, ...], *, max_concurrent_runs: int = 0
    ) -> RecoveryBatch:
        """Prepare every supplied project before creating any execution task."""
        if max_concurrent_runs < 0:
            raise EngineError("run capacity must not be negative")
        runnable: list[RecoveryBinding] = []
        dormant: list[RunState] = []
        seen: set[str] = set()
        projects = tuple(dict.fromkeys(project_ids))
        try:
            while True:
                found_unseen = False
                for project_id in projects:
                    for row in list_runs(self.conn, project_id):
                        if row.id in seen:
                            continue
                        found_unseen = True
                        seen.add(row.id)
                        if row.status in (
                            "completed",
                            "failed",
                            "cancelled",
                            "superseded",
                        ) and not any(
                            gate.status in ("answered", "prepared", "applied")
                            for gate in list_gates(self.conn, row.id)
                        ):
                            continue
                        binding = self.resolver(row)
                        if binding is None:
                            # A view-only run is left as stored (chunk 63).
                            continue
                        loop = binding.loop
                        if loop.tracker is not self.tracker:
                            raise EngineError("recovery requires shared run tracker")
                        state = await loop.load_state(binding.initial, row)
                        if loop.gate_reconciler is not None:
                            try:
                                state = await loop.gate_reconciler(state)
                            except EngineError as exc:
                                _LOGGER.warning(
                                    "Run %s gate reconciliation failed: %s", row.id, exc.message
                                )
                                continue
                            row = get_run(self.conn, row.id) or row
                        if row.status in ("paused", "awaiting_user", "cancelled", "superseded"):
                            state = RunState.model_validate(
                                {**state.model_dump(), "status": row.status}
                            )
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
                            runnable.append(RecoveryBinding(loop, state, binding.lifecycle))
                if not found_unseen:
                    break
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise PersistenceError("restart preparation failed", cause=exc) from exc
        if max_concurrent_runs and len(runnable) > max_concurrent_runs:
            raise EngineError("run capacity exhausted")
        return RecoveryBatch(
            tasks=tuple(
                asyncio.create_task(
                    (binding.lifecycle or RunLifecycle(binding.loop, binding.initial)).start(),
                    name="statlab-run:" + binding.initial.run_id,
                )
                for binding in runnable
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
            ensure_candidate(
                self.conn,
                candidate_id=branch.database_id(state.run_id),
                run_id=state.run_id,
                spec=branch.spec,
                wave=branch.wave,
            )
            if state.traversal is not None:
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
            if row is None:
                # Only verified immutable ancestor boundaries can bypass same-run repair.
                validate_checkpoint_ownership(self.conn, state.run_id, state)
                inherited = self.conn.execute(
                    "SELECT run_id, status FROM node_executions WHERE id = ?",
                    (boundary.execution_id,),
                ).fetchone()
                if (
                    inherited is None
                    or inherited["run_id"] == state.run_id
                    or boundary.result.status not in ("completed", "skipped", "failed")
                ):
                    raise PersistenceError("checkpoint execution ownership mismatch")
                continue
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
            if any(
                event.type == "gate.opened"
                and event.payload.get("gate_id") == gate.id
                and isinstance(opened := event.payload.get("question"), dict)
                and opened.get("revision") == question.get("revision")
                for event in await loop.events.replay()
            ):
                continue
            await loop.events.append(
                "gate.opened",
                {
                    "gate_id": gate.id,
                    "node_execution_id": gate.node_execution_id,
                    "question": question,
                },
            )
