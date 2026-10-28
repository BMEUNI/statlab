"""Sequential declared-topology execution with durable node boundaries."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import JsonValue, ValidationError
from statlab_contracts.candidate import CandidateSet
from statlab_contracts.contract import Constraints
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, Failure

from statlab.config import Settings
from statlab.domains.base import NodeResult, NodeSpec
from statlab.errors import (
    EngineError,
    LlmCallError,
    PersistenceError,
    ToolCallError,
    traceback_frames,
)
from statlab.events import EventBus
from statlab.graph.candidates import (
    BoundaryStopError,
    CandidatePlanner,
    FitBudget,
    RetuneDecider,
    RunControl,
    TraversalPlan,
)
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
    list_node_executions,
    next_node_attempt,
    update_candidate_status,
    update_run_status,
)
from statlab.storage import get_artifact, prune_intermediates

_LOGGER = logging.getLogger(__name__)


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
    after_action: bool = False


type NodeExecutor = Callable[[NodeInvocation], Awaitable[NodeResult]]
type FailureHook = Callable[[NodeInvocation, NodeResult], Awaitable[NodeResult]]
type GateReconciler = Callable[[RunState], Awaitable[RunState]]
type GateBoundaryHook = Callable[[NodeInvocation, NodeResult], Awaitable[None]]


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
        constraints: Constraints | None = None,
        traversal: TraversalPlan | None = None,
        candidate_planner: CandidatePlanner | None = None,
        retune_decider: RetuneDecider | None = None,
        control: RunControl | None = None,
        failure_hook: FailureHook | None = None,
    ) -> None:
        self.conn, self.nodes, self.spine_id = conn, nodes, spine_id
        self.executor, self.checkpointer, self.events = executor, checkpointer, events
        self.settings, self.tracker = settings, tracker
        self.last_error: EngineError | None = None
        self.constraints = constraints or Constraints()
        self.traversal = traversal
        self.candidate_planner, self.retune_decider = candidate_planner, retune_decider
        self.control = control or RunControl()
        self.failure_hook = failure_hook
        self.gate_reconciler: GateReconciler | None = None
        self.gate_boundary_hook: GateBoundaryHook | None = None
        self.budget = FitBudget(conn, checkpointer.paths.run_id, self.constraints)
        self._announced: tuple[str, str | None] | None = None
        self._coordinator = asyncio.Lock()
        self._state: RunState
        self._lifecycle_stop = False
        self._fanout_enabled = any(
            value is not None
            for value in (traversal, candidate_planner, retune_decider, control, constraints)
        )

    async def run(self, initial: RunState) -> RunState:
        run_id = self.checkpointer.paths.run_id
        self.tracker.enter(run_id)
        self._lifecycle_stop = False
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
            ended_at=utc_now()
            if state.status in ("completed", "failed", "cancelled", "superseded")
            else None,
        )
        announced = (state.status, current_node)
        if announced == self._announced:
            return
        self._announced = announced
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
            if self.gate_reconciler is not None:
                state = await self.gate_reconciler(state)
            if state.mode != row.mode:
                raise EngineError("run mode mismatch")
        return state

    async def ensure_audit_anchor(self, initial: RunState) -> NodeExecutionRow:
        """Save the input before creating/reusing a real pending node execution."""
        async with self._coordinator:
            row = get_run(self.conn, initial.run_id)
            if row is None:
                raise PersistenceError("run does not exist")
            state = await self.load_state(initial, row)
            self.checkpointer.save(state)
            executions = list_node_executions(self.conn, state.run_id)
            if state.status in ("completed", "failed", "cancelled", "superseded"):
                if not executions:
                    raise EngineError("run has no declared execution for control audit")
                return executions[-1]
            index = state.cursor
            candidate: CandidateProgress | None = None
            traversal = self.traversal or state.traversal
            if traversal is not None:
                start, end = traversal.region(state.spine)
                if (
                    traversal.design_node is not None
                    and state.wave > 1
                    and sum(
                        boundary.node_name == traversal.design_node
                        for boundary in state.shared_results
                    )
                    < state.wave
                ):
                    index = next(
                        i for i, spec in enumerate(self.nodes) if spec.name == traversal.design_node
                    )
                if start <= index < end or (
                    index == start - 1
                    and traversal.overlap_shared is not None
                    and any(
                        boundary.node_name == traversal.overlap_shared
                        for boundary in state.shared_results
                    )
                ):
                    branch = next(
                        (
                            branch
                            for branch in state.branches
                            if branch.status not in ("completed", "failed", "budget_exhausted")
                        ),
                        None,
                    )
                    if branch is not None:
                        candidate = branch
                        index = max(start, branch.position)
                    elif state.branches:
                        viable = any(
                            branch.status == "completed"
                            or (
                                branch.status == "budget_exhausted"
                                and any(
                                    boundary.node_name == traversal.evaluation_node
                                    for boundary in branch.results
                                )
                            )
                            for branch in state.branches
                        )
                        if viable:
                            index = end
                        else:
                            candidate = next(
                                branch
                                for branch in state.branches
                                if branch.pending_result is not None
                            )
                            index = max(start, candidate.position)
            if index >= len(self.nodes):
                if not executions:
                    raise EngineError("run has no declared execution for control audit")
                return executions[-1]
            spec = self.nodes[index]
            if spec.kind == "PER_CANDIDATE":
                candidate = candidate or state.candidate
                if candidate is None:
                    raise EngineError("control anchor requires current candidate")
            candidate_id = candidate.database_id(state.run_id) if candidate else None
            active = tuple(
                execution
                for execution in executions
                if execution.node_name == spec.name
                and execution.candidate_id == candidate_id
                and execution.status in ("pending", "running")
            )
            if len(active) > 1:
                raise PersistenceError("duplicate active execution anchors")
            if active:
                return active[0]
            pending = state.pending_result or (
                candidate.pending_result if candidate is not None else None
            )
            if pending is not None:
                existing = next(
                    (execution for execution in executions if execution.id == pending.execution_id),
                    None,
                )
                if existing is not None:
                    return existing
                raise PersistenceError("control audit requires a same-run pending execution")
            if candidate is not None:
                ensure_candidate(
                    self.conn,
                    candidate_id=candidate.database_id(state.run_id),
                    run_id=state.run_id,
                    spec=candidate.spec,
                    wave=candidate.wave,
                )
            return create_node_execution(
                self.conn,
                run_id=state.run_id,
                node_name=spec.name,
                seq=index + 1,
                candidate_id=candidate_id,
                attempt=next_node_attempt(
                    self.conn, run_id=state.run_id, node_name=spec.name, candidate_id=candidate_id
                ),
                status="pending",
                reuse_pending=True,
            )

    async def _stop_attempt(
        self, execution: NodeExecutionRow, reason: str, payload: dict[str, JsonValue]
    ) -> None:
        result = NodeResult(
            status="failed",
            error=Failure(error_class="bug", message="Node interrupted by lifecycle request"),
            summary={"lifecycle": reason},
        )
        finish_node_execution(self.conn, execution.id, result)
        await self.events.append(
            "node.failed", {**payload, "result": result.model_dump(mode="json")}
        )

    async def _run(self, initial: RunState) -> RunState:
        row = get_run(self.conn, self.checkpointer.paths.run_id)
        if row is None:
            raise PersistenceError("run does not exist")
        state = await self.load_state(initial, row)
        if row.status not in ("pending", "running") and state.status in ("pending", "running"):
            raise EngineError("SQL run status refuses execution")
        if state.status not in ("pending", "running"):
            self.checkpointer.save(state)
            return state
        requested = self.control.status
        if requested is not None:
            state = state.model_copy(update={"status": requested})
            self.checkpointer.save(state)
            await self._status(
                state, self.nodes[state.cursor].name if state.cursor < len(self.nodes) else None
            )
            return state
        if state.traversal is not None or self._fanout_enabled or len(state.branches) > 1:
            return await self._fanout(state)
        self.checkpointer.save(state)
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
            requested = self.control.status
            if requested is not None:
                state = state.model_copy(update={"status": requested})
                self.checkpointer.save(state)
                await self._status(state, spec.name)
                return state
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
                reuse_pending=True,
            )
            update_run_status(self.conn, state.run_id, status="running", current_node=spec.name)
            await self.events.append(
                "node.started", {"node": spec.name, "execution_id": execution.id}
            )
            try:
                result = await self.executor(NodeInvocation(state, spec, execution, candidate))
            except BoundaryStopError as exc:
                if exc.reason == "budget_exhausted":
                    result = NodeResult(
                        status="failed",
                        error=Failure(error_class="data", message="Fit budget exhausted"),
                    )
                else:
                    await self._stop_attempt(
                        execution, exc.reason, {"node": spec.name, "execution_id": execution.id}
                    )
                    state = state.model_copy(update={"status": self.control.status or exc.reason})
                    self.checkpointer.save(state)
                    await self._status(state, spec.name)
                    return state
            except (ToolCallError, LlmCallError) as exc:
                result = NodeResult(status="failed", error=exc.failure)
            except Exception as exc:
                self.last_error = EngineError("node executor failed", cause=exc)
                _LOGGER.error(
                    "Node %s executor failed with %s\n%s",
                    spec.name,
                    type(exc).__name__,
                    traceback_frames(exc),
                )
                result = NodeResult(
                    status="failed",
                    error=Failure(error_class="bug", message="Node execution failed"),
                )
            if result.status == "skipped" and not spec.skippable:
                raise EngineError("non-skippable node returned skipped")
            if result.status == "failed" and self.failure_hook is not None:
                result = await self.failure_hook(
                    NodeInvocation(state, spec, execution, candidate), result
                )
            if self.gate_boundary_hook is not None:
                await self.gate_boundary_hook(
                    NodeInvocation(state, spec, execution, candidate), result
                )
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
                    updates["branches"] = (
                        candidate.model_copy(
                            update={
                                "results": (*candidate.results, boundary),
                                "position": state.cursor + 1,
                            }
                        ),
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
        requested = self.control.status
        if requested is not None:
            state = state.model_copy(update={"status": requested})
            self.checkpointer.save(state)
            await self._status(state, None)
            return state
        state = state.model_copy(update={"status": "completed"})
        self.checkpointer.save(state)
        await self._status(state, None)
        return state

    @property
    def _latest(self) -> RunState:
        return self._state

    def _save_latest(self) -> None:
        self._state = self._latest.model_copy(update={"budget": self.budget.snapshot()})
        self.checkpointer.save(self._latest)

    def _branch(self, public_id: str) -> CandidateProgress:
        return next(branch for branch in self._latest.branches if branch.public_id == public_id)

    def _replace_branch(self, branch: CandidateProgress) -> None:
        self._state = self._latest.model_copy(
            update={
                "branches": tuple(
                    branch if item.public_id == branch.public_id else item
                    for item in self._latest.branches
                )
            }
        )
        update_candidate_status(self.conn, branch.database_id(self._latest.run_id), branch.status)

    async def _boundary(
        self,
        index: int,
        public_id: str | None = None,
        *,
        overlap: bool = False,
        design: bool = False,
    ) -> bool:
        async with self._coordinator:
            try:
                self.control.check()
            except BoundaryStopError:
                self._lifecycle_stop = True
                return False
            snapshot = self._latest
            candidate = self._branch(public_id) if public_id is not None else None
            if overlap:
                snapshot = snapshot.model_copy(
                    update={
                        "branches": tuple(
                            item.model_copy(
                                update={
                                    "results": (),
                                    "pending_result": None,
                                    "position": 0,
                                    "retune_count": 0,
                                    "status": "pending",
                                }
                            )
                            for item in snapshot.branches
                        )
                    }
                )
            spec = self.nodes[index]
            candidate_id = candidate.database_id(snapshot.run_id) if candidate else None
            execution = create_node_execution(
                self.conn,
                run_id=snapshot.run_id,
                node_name=spec.name,
                seq=index + 1,
                candidate_id=candidate_id,
                attempt=next_node_attempt(
                    self.conn,
                    run_id=snapshot.run_id,
                    node_name=spec.name,
                    candidate_id=candidate_id,
                ),
                concurrency_at_start=self.tracker.concurrency,
                reuse_pending=True,
            )
            update_run_status(self.conn, snapshot.run_id, status="running", current_node=spec.name)
            payload: dict[str, JsonValue] = {"node": spec.name, "execution_id": execution.id}
            if candidate is not None:
                payload["candidate_id"] = candidate.public_id
            await self.events.append("node.started", payload)
        stop: BoundaryStopError | None = None
        try:
            result = await self.executor(NodeInvocation(snapshot, spec, execution, candidate))
        except BoundaryStopError as exc:
            stop = exc
        except (ToolCallError, LlmCallError) as exc:
            result = NodeResult(status="failed", error=exc.failure)
        except Exception as exc:
            self.last_error = EngineError("node executor failed", cause=exc)
            _LOGGER.error(
                "Node %s executor failed with %s\n%s",
                spec.name,
                type(exc).__name__,
                traceback_frames(exc),
            )
            result = NodeResult(
                status="failed", error=Failure(error_class="bug", message="Node execution failed")
            )
        if stop is None and result.status == "failed" and self.failure_hook is not None:
            result = await self.failure_hook(
                NodeInvocation(snapshot, spec, execution, candidate), result
            )
        if stop is None and self.gate_boundary_hook is not None:
            await self.gate_boundary_hook(
                NodeInvocation(snapshot, spec, execution, candidate), result
            )
        async with self._coordinator:
            if stop is not None:
                if stop.reason == "budget_exhausted":
                    result = NodeResult(
                        status="failed",
                        error=Failure(error_class="data", message="Fit budget exhausted"),
                    )
                    boundary = BoundaryResult(
                        node_name=spec.name, execution_id=execution.id, result=result
                    )
                    finish_node_execution(self.conn, execution.id, result)
                    if candidate is not None:
                        self._replace_branch(
                            self._branch(candidate.public_id).model_copy(
                                update={"status": "budget_exhausted", "pending_result": boundary}
                            )
                        )
                    else:
                        self._state = self._latest.model_copy(
                            update={
                                "status": "failed",
                                "pending_result": boundary,
                            }
                        )
                    await self.events.append(
                        "node.failed", {**payload, "result": result.model_dump(mode="json")}
                    )
                else:
                    await self._stop_attempt(execution, stop.reason, payload)
                    self._lifecycle_stop = True
                self._save_latest()
                return False
            if result.status == "skipped" and not spec.skippable:
                raise EngineError("non-skippable node returned skipped")
            boundary = BoundaryResult(node_name=spec.name, execution_id=execution.id, result=result)
            finish_node_execution(self.conn, execution.id, result)
            if candidate is not None:
                latest = self._branch(candidate.public_id)
                if result.status in ("failed", "awaiting_user"):
                    latest = latest.model_copy(
                        update={"status": result.status, "pending_result": boundary}
                    )
                else:
                    latest = latest.model_copy(
                        update={
                            "status": "running",
                            "results": (*latest.results, boundary),
                            "position": index + 1,
                            "pending_result": None,
                        }
                    )
                self._replace_branch(latest)
            elif result.status in ("failed", "awaiting_user"):
                self._state = self._latest.model_copy(
                    update={"status": result.status, "pending_result": boundary}
                )
            else:
                updates: dict[str, object] = {
                    "shared_results": (*self._latest.shared_results, boundary),
                    "pending_result": None,
                }
                if not overlap and not design:
                    updates["cursor"] = index + 1
                self._state = self._latest.model_copy(update=updates)
            self._save_latest()
            if result.status != "awaiting_user":
                await self.events.append(
                    f"node.{result.status}", {**payload, "result": result.model_dump(mode="json")}
                )
            if result.status == "completed":
                self._prune(execution.id, self._latest)
            return result.status in ("completed", "skipped")

    async def _advance_branch(
        self, public_id: str, start: int, end: int, traversal: TraversalPlan
    ) -> None:
        while True:
            branch = self._branch(public_id)
            if branch.status in ("failed", "awaiting_user", "budget_exhausted", "completed"):
                return
            index = max(start, branch.position)
            if index == end:
                async with self._coordinator:
                    self._replace_branch(
                        branch.model_copy(update={"position": end, "status": "completed"})
                    )
                    self._save_latest()
                return
            if not await self._boundary(index, public_id):
                return
            branch = self._branch(public_id)
            if (
                self.retune_decider is not None
                and self.nodes[index].name == traversal.evaluation_node
                and branch.retune_count < self.constraints.max_retune_loops
                and not self.budget.snapshot().exhausted
            ):
                retune = await self.retune_decider(self._latest, branch)
                if retune:
                    async with self._coordinator:
                        if self.control.status is not None:
                            self._lifecycle_stop = True
                            return
                        if not self.budget.snapshot().exhausted:
                            training_index = next(
                                i
                                for i, spec in enumerate(self.nodes)
                                if spec.name == traversal.training_node
                            )
                            self._replace_branch(
                                branch.model_copy(
                                    update={
                                        "position": training_index,
                                        "retune_count": branch.retune_count + 1,
                                    }
                                )
                            )
                            self._save_latest()

    async def _join(self, tasks: tuple[asyncio.Task[bool] | asyncio.Task[None], ...]) -> None:
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _fanout(self, state: RunState) -> RunState:
        traversal = self.traversal or state.traversal or TraversalPlan()
        if (
            state.status != "pending"
            and state.traversal is not None
            and state.traversal != traversal
        ):
            raise EngineError("checkpoint traversal mismatch")
        start, end = traversal.region(state.spine)
        if self.candidate_planner is not None and traversal.design_node is None:
            raise EngineError("candidate planner requires design node")
        if self.retune_decider is not None and traversal.training_node is None:
            raise EngineError("retune decider requires training/evaluation")
        if len(state.branches) > self.constraints.max_candidates:
            raise EngineError("max_candidates exceeded")
        if len(state.branches) > 1:
            CandidateSet(candidates=tuple(branch.spec for branch in state.branches))
        if start < end and not state.branches and self.candidate_planner is None:
            raise EngineError("per-candidate node requires a candidate")
        self.checkpointer.save(state)
        for branch in state.branches:
            ensure_candidate(
                self.conn,
                candidate_id=branch.database_id(state.run_id),
                run_id=state.run_id,
                spec=branch.spec,
                wave=branch.wave,
            )
        self._state = state.model_copy(update={"status": "running", "traversal": traversal})
        self._save_latest()
        await self._status(
            self._latest, self.nodes[state.cursor].name if state.cursor < len(self.nodes) else None
        )
        prefix_end = start - 1 if traversal.overlap_shared else start
        while self._latest.cursor < prefix_end:
            if not await self._boundary(self._latest.cursor):
                return await self._finish_fanout()
        if (
            start < end
            and not self._latest.branches
            and not await self._bootstrap_candidates(start, traversal)
        ):
            return await self._finish_fanout()
        if self._latest.cursor < end and start < end:
            while True:
                # A saved new wave precedes its design attempt, so restart never replans it.
                if traversal.design_node is not None:
                    designs = sum(
                        boundary.node_name == traversal.design_node
                        for boundary in self._latest.shared_results
                    )
                    if designs < self._latest.wave:
                        design_index = next(
                            i
                            for i, spec in enumerate(self.nodes)
                            if spec.name == traversal.design_node
                        )
                        if not await self._boundary(design_index, design=True):
                            return await self._finish_fanout()
                work: list[asyncio.Task[bool] | asyncio.Task[None]] = []
                if traversal.overlap_shared is not None and not any(
                    boundary.node_name == traversal.overlap_shared
                    for boundary in self._latest.shared_results
                ):
                    work.append(asyncio.create_task(self._boundary(start - 1, overlap=True)))
                work.extend(
                    asyncio.create_task(
                        self._advance_branch(branch.public_id, start, end, traversal)
                    )
                    for branch in self._latest.branches
                    if branch.wave == self._latest.wave
                )
                await self._join(tuple(work))
                if self._lifecycle_stop or self._latest.status != "running":
                    return await self._finish_fanout()
                if any(branch.status == "awaiting_user" for branch in self._latest.branches):
                    self._state = self._latest.model_copy(update={"status": "awaiting_user"})
                    return await self._finish_fanout()
                viable = any(
                    branch.status == "completed"
                    or (
                        branch.status == "budget_exhausted"
                        and any(
                            boundary.node_name == traversal.evaluation_node
                            for boundary in branch.results
                        )
                    )
                    for branch in self._latest.branches
                )
                if not viable:
                    self._state = self._latest.model_copy(update={"status": "failed"})
                    return await self._finish_fanout()
                if (
                    self.candidate_planner is None
                    or self._latest.wave >= self.constraints.max_search_waves
                    or len(self._latest.branches) >= self.constraints.max_candidates
                    or self.budget.snapshot().exhausted
                ):
                    break
                wave = self._latest.wave + 1
                await self.events.append("search.started", {"wave": wave})
                try:
                    proposal = await self.candidate_planner(self._latest, wave)
                except BoundaryStopError:
                    self._lifecycle_stop = True
                    return await self._finish_fanout()
                await self.events.append("search.finished", {"wave": wave, "added": len(proposal)})
                if not proposal:
                    break
                if len(self._latest.branches) + len(proposal) > self.constraints.max_candidates:
                    raise EngineError("max_candidates exceeded")
                hashes = {branch.spec.spec_hash for branch in self._latest.branches}
                if any(spec.spec_hash in hashes for spec in proposal):
                    raise EngineError("duplicate candidate specification across waves")
                if self.control.status is not None:
                    self._lifecycle_stop = True
                    return await self._finish_fanout()
                if self.budget.snapshot().exhausted:
                    break
                CandidateSet(
                    candidates=tuple(branch.spec for branch in self._latest.branches) + proposal
                )
                new = tuple(
                    CandidateProgress(
                        public_id=f"c_{len(self._latest.branches) + i:02d}",
                        spec=spec,
                        wave=wave,
                        position=start,
                    )
                    for i, spec in enumerate(proposal, 1)
                )
                for branch in new:
                    ensure_candidate(
                        self.conn,
                        candidate_id=branch.database_id(self._latest.run_id),
                        run_id=self._latest.run_id,
                        spec=branch.spec,
                        wave=wave,
                    )
                self._state = self._latest.model_copy(
                    update={"wave": wave, "branches": (*self._latest.branches, *new)}
                )
                self._save_latest()
            self._state = self._latest.model_copy(update={"cursor": end})
            self._save_latest()
        while self._latest.cursor < len(self.nodes):
            if not await self._boundary(self._latest.cursor):
                return await self._finish_fanout()
        self._state = self._latest.model_copy(update={"status": "completed"})
        return await self._finish_fanout()

    async def _bootstrap_candidates(self, start: int, traversal: TraversalPlan) -> bool:
        planner = self.candidate_planner
        if planner is None or not any(
            boundary.node_name == traversal.design_node and boundary.result.status == "completed"
            for boundary in self._latest.shared_results
        ):
            raise EngineError("initial candidates require an approved design boundary")
        try:
            self.control.check()
        except BoundaryStopError:
            self._lifecycle_stop = True
            return False
        if self.budget.snapshot().exhausted:
            raise EngineError("initial candidate bootstrap has no remaining fit budget")
        proposal = await planner(self._latest, 1)
        try:
            self.control.check()
        except BoundaryStopError:
            self._lifecycle_stop = True
            return False
        if self.budget.snapshot().exhausted:
            raise EngineError("initial candidate bootstrap has no remaining fit budget")
        if not proposal or len(proposal) > self.constraints.max_candidates:
            raise EngineError("initial candidate proposal is empty or exceeds max_candidates")
        try:
            CandidateSet(candidates=proposal)
        except ValidationError as exc:
            raise EngineError("invalid initial candidate proposal", cause=exc) from exc
        branches = tuple(
            CandidateProgress(public_id=f"c_{i:02d}", spec=spec, wave=1, position=start)
            for i, spec in enumerate(proposal, 1)
        )
        for branch in branches:
            ensure_candidate(
                self.conn,
                candidate_id=branch.database_id(self._latest.run_id),
                run_id=self._latest.run_id,
                spec=branch.spec,
                wave=1,
            )
        self._state = self._latest.model_copy(update={"wave": 1, "branches": branches})
        self._save_latest()
        return True

    async def _finish_fanout(self) -> RunState:
        if self._lifecycle_stop or self.control.status is not None:
            self._state = self._latest.model_copy(
                update={"status": self.control.status or "paused"}
            )
        self._save_latest()
        current = (
            self.nodes[self._latest.cursor].name if self._latest.cursor < len(self.nodes) else None
        )
        await self._status(self._latest, current)
        return self._latest

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
