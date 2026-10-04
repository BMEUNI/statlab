"""Sequential declared-topology execution with durable node boundaries."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, Failure

from statlab.config import Settings
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import EngineError, LlmCallError, PersistenceError, ToolCallError
from statlab.events import EventBus
from statlab.graph.checkpoint import Checkpointer
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState, SpineEntry
from statlab.persistence.ids import utc_now
from statlab.persistence.models import NodeExecutionRow, RunRow
from statlab.persistence.repository import (
    create_node_execution,
    ensure_candidate,
    finish_node_execution,
    get_run,
    has_run_history,
    next_node_attempt,
    update_run_status,
)
from statlab.storage import get_artifact, prune_intermediates


class RunTracker:
    """Share this tracker across loops to count live runs and reject duplicate starts."""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def enter(self, run_id: str) -> None:
        if run_id in self._active:
            raise EngineError("run is already executing")
        self._active.add(run_id)

    def leave(self, run_id: str) -> None:
        self._active.remove(run_id)

    @property
    def concurrency(self) -> int:
        return len(self._active)


@dataclass(frozen=True, slots=True)
class NodeInvocation:
    state: RunState
    spec: NodeSpec
    execution: NodeExecutionRow
    candidate: CandidateProgress | None


type NodeExecutor = Callable[[NodeInvocation], Awaitable[NodeResult]]


class RunLoop:
    def __init__(
        self,
        *,
        conn: sqlite3.Connection,
        nodes: tuple[NodeSpec, ...],
        spine_id: str,
        executor: NodeExecutor,
        checkpointer: Checkpointer,
        events: EventBus,
        settings: Settings,
        tracker: RunTracker,
    ) -> None:
        self.conn, self.nodes, self.spine_id = conn, nodes, spine_id
        self.executor, self.checkpointer, self.events = executor, checkpointer, events
        self.settings, self.tracker = settings, tracker
        self.last_error: EngineError | None = None

    async def run(self, initial: RunState) -> RunState:
        run_id = self.checkpointer.paths.run_id
        self.tracker.enter(run_id)
        try:
            return await self._run(initial)
        except (sqlite3.Error, OSError) as exc:
            raise PersistenceError("run persistence operation failed", cause=exc) from exc
        finally:
            self.tracker.leave(run_id)

    async def _status(self, state: RunState, current_node: str | None) -> None:
        update_run_status(
            self.conn,
            state.run_id,
            status=state.status,
            current_node=current_node,
            ended_at=utc_now() if state.status in ("completed", "failed") else None,
        )
        await self.events.append(
            "run.status", {"status": state.status, "current_node": current_node}
        )

    async def load_state(self, initial: RunState, row: RunRow) -> RunState:
        """Validate a binding and load its durable boundary without changing persistence."""
        spine = tuple(SpineEntry.from_spec(node) for node in self.nodes)
        if (
            initial.run_id != self.checkpointer.paths.run_id
            or initial.spine_id != self.spine_id
            or initial.spine != spine
            or self.events.paths != self.checkpointer.paths
            or row.id != initial.run_id
        ):
            raise EngineError("run/spine/event identity mismatch")
        if initial.mode != row.mode:
            raise EngineError("run mode mismatch")
        state = self.checkpointer.load(
            spine_id=self.spine_id, spine=spine, allow_missing=row.status == "pending"
        )
        if state is None:
            if has_run_history(self.conn, row.id) or await self.events.replay():
                raise PersistenceError("missing checkpoint for run with durable history")
            if initial.status != "pending" or initial.cursor != 0:
                raise EngineError("new run requires pending initial state")
            state = initial
        if state.mode != row.mode:
            raise EngineError("run mode mismatch")
        return state

    async def _run(self, initial: RunState) -> RunState:
        row = get_run(self.conn, self.checkpointer.paths.run_id)
        if row is None:
            raise PersistenceError("run does not exist")
        state = await self.load_state(initial, row)
        if row.status not in ("pending", "running") and state.status in ("pending", "running"):
            raise EngineError("SQL run status refuses execution")
        if state.status not in ("pending", "running"):
            return state
        if state.candidate is not None:
            ensure_candidate(
                self.conn,
                candidate_id=state.candidate.database_id(state.run_id),
                run_id=state.run_id,
                spec=state.candidate.spec,
            )
        state = state.model_copy(update={"status": "running"})
        self.checkpointer.save(state)
        await self._status(
            state, self.nodes[state.cursor].name if state.cursor < len(self.nodes) else None
        )
        while state.cursor < len(self.nodes):
            spec = self.nodes[state.cursor]
            candidate = state.candidate if spec.kind == "PER_CANDIDATE" else None
            if spec.kind == "PER_CANDIDATE" and candidate is None:
                raise EngineError("per-candidate node requires a candidate")
            candidate_id = candidate.database_id(state.run_id) if candidate else None
            execution = create_node_execution(
                self.conn,
                run_id=state.run_id,
                node_name=spec.name,
                seq=state.cursor + 1,
                candidate_id=candidate_id,
                attempt=next_node_attempt(
                    self.conn, run_id=state.run_id, node_name=spec.name, candidate_id=candidate_id
                ),
                concurrency_at_start=self.tracker.concurrency,
            )
            update_run_status(self.conn, state.run_id, status="running", current_node=spec.name)
            await self.events.append(
                "node.started", {"node": spec.name, "execution_id": execution.id}
            )
            try:
                result = await self.executor(NodeInvocation(state, spec, execution, candidate))
            except (ToolCallError, LlmCallError) as exc:
                result = NodeResult(status="failed", error=exc.failure)
            except Exception as exc:
                self.last_error = EngineError("node executor failed", cause=exc)
                result = NodeResult(
                    status="failed",
                    error=Failure(error_class="bug", message="Node execution failed"),
                )
            if result.status == "skipped" and not spec.skippable:
                raise EngineError("non-skippable node returned skipped")
            finish_node_execution(self.conn, execution.id, result)
            boundary = BoundaryResult(node_name=spec.name, execution_id=execution.id, result=result)
            if result.status in ("failed", "awaiting_user"):
                state = state.model_copy(
                    update={"status": result.status, "pending_result": boundary}
                )
            else:
                updates: dict[str, object] = {"cursor": state.cursor + 1, "pending_result": None}
                if candidate is None:
                    updates["shared_results"] = (*state.shared_results, boundary)
                else:
                    updates["candidate"] = candidate.model_copy(
                        update={"results": (*candidate.results, boundary)}
                    )
                state = state.model_copy(update=updates)
            self.checkpointer.save(state)
            if result.status != "awaiting_user":
                await self.events.append(
                    f"node.{result.status}",
                    {
                        "node": spec.name,
                        "execution_id": execution.id,
                        "result": result.model_dump(mode="json"),
                    },
                )
            if result.status in ("failed", "awaiting_user"):
                await self._status(state, spec.name)
                return state
            if result.status == "completed":
                self._prune(execution.id, state)
        state = state.model_copy(update={"status": "completed"})
        self.checkpointer.save(state)
        await self._status(state, None)
        return state

    def _prune(self, execution_id: str, state: RunState) -> None:
        try:
            calls = self.conn.execute(
                "SELECT result_json FROM tool_calls WHERE node_execution_id = ? ORDER BY rowid",
                (execution_id,),
            ).fetchall()
            outputs = [TOOL_RESULT_ADAPTER.validate_json(call["result_json"]) for call in calls]
        except (sqlite3.Error, ValueError) as exc:
            raise PersistenceError("cannot read node output audit", cause=exc) from exc
        produced = []
        for output in outputs:
            if output.ok:
                for meta in output.artifacts:
                    row = get_artifact(self.conn, str(meta.ref))
                    if row is None:
                        raise PersistenceError("logged artifact is not registered")
                    if row.kind == "dataframe":
                        produced.append(row)
        prune_intermediates(
            self.conn,
            self.checkpointer.paths,
            node_status="completed",
            produced=produced,
            keep=state.retained_refs,
            keep_intermediates=self.settings.keep_intermediates,
        )
