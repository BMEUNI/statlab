"""Caller-awaited lifecycle transitions for one bound run loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from statlab.errors import EngineError, PersistenceError
from statlab.graph.control import ControlContext, ControlResponse
from statlab.graph.engine import RunLoop
from statlab.graph.router import InterruptClass
from statlab.graph.state import RunState
from statlab.persistence.models import NodeExecutionRow
from statlab.persistence.repository import get_run


class RunLifecycle:
    """Callers schedule/await start and resume; this service creates no tasks."""

    def __init__(self, loop: RunLoop, initial: RunState) -> None:
        self.loop, self.initial = loop, initial
        self._transition = asyncio.Lock()
        self._owner: asyncio.Task[object] | None = None
        self._running = False
        self._stopping = 0
        self._drained = asyncio.Event()
        self._drained.set()

    async def state(self) -> RunState:
        row = get_run(self.loop.conn, self.initial.run_id)
        if row is None:
            raise PersistenceError("run does not exist")
        return await self.loop.load_state(self.initial, row)

    async def audit_anchor(self) -> NodeExecutionRow:
        return await self.loop.ensure_audit_anchor(self.initial)

    @asynccontextmanager
    async def _locked(self) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is self._owner and task is not None:
            yield
            return
        async with self._transition:
            self._owner = task
            try:
                yield
            finally:
                self._owner = None

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Control services share the lifecycle lock; execution and pending stops exclude them."""
        async with self._locked():
            if self._running or self._stopping:
                raise EngineError("run transition requires drained execution")
            yield

    async def halt_for_fork(self) -> RunState:
        if self._running:
            return await self.pause()
        async with self.exclusive():
            return await self.state()

    async def start(self) -> RunState:
        async with self._locked():
            if self._running or self._stopping:
                raise EngineError("run is already executing")
            state = await self.state()
            if state.status not in ("pending", "running"):
                raise EngineError("run cannot start from this status")
            self._running = True
            self._drained.clear()
        try:
            return await self.loop.run(state)
        finally:
            self._running = False
            self._drained.set()

    async def resume(self) -> RunState:
        async with self._locked():
            if self._running or self._stopping:
                raise EngineError("run is already executing")
            state = await self.state()
            if state.status != "paused":
                raise EngineError("only a paused run can resume")
            self.loop.control.resume()
            # A pause may drain into an existing gate or failure. Keep that committed
            # pending boundary dormant rather than silently authorizing a node replay.
            status = (
                state.pending_result.result.status
                if state.pending_result is not None
                else "awaiting_user"
                if any(branch.status == "awaiting_user" for branch in state.branches)
                else "running"
            )
            state = RunState.model_validate({**state.model_dump(), "status": status})
            self.loop.checkpointer.save(state)
            current = state.spine[state.cursor].name if state.cursor < len(state.spine) else None
            await self.loop._status(state, current)
            if status != "running":
                return state
            self._running = True
            self._drained.clear()
        try:
            return await self.loop.run(state)
        finally:
            self._running = False
            self._drained.set()

    async def _stop(self, action: Literal["pause", "cancel"]) -> RunState:
        async with self._locked():
            state = await self.state()
            if state.status not in ("pending", "running", "paused", "awaiting_user", "failed"):
                raise EngineError("terminal run rejects lifecycle transition")
            if action == "pause":
                if state.status not in ("pending", "running", "paused"):
                    raise EngineError("run cannot pause from this status")
                self.loop.control.request_pause()
            else:
                self.loop.control.request_cancel()
            self._stopping += 1
        # No cancellation/preemption: all admitted tools and sibling tasks drain.
        try:
            await self._drained.wait()
            async with self._locked():
                state = await self.state()
                requested = self.loop.control.status
                if requested is None:
                    raise EngineError("lifecycle request disappeared before drain")
                state = state.model_copy(update={"status": requested})
                self.loop.checkpointer.save(state)
                current = (
                    state.spine[state.cursor].name if state.cursor < len(state.spine) else None
                )
                await self.loop._status(state, current)
                return state
        finally:
            self._stopping -= 1

    async def pause(self) -> RunState:
        return await self._stop("pause")

    async def cancel(self) -> RunState:
        return await self._stop("cancel")

    async def handle(
        self, message: str, classified: InterruptClass, context: ControlContext
    ) -> ControlResponse:
        del message
        if context.state.run_id != self.initial.run_id or context.conn is not self.loop.conn:
            raise EngineError("lifecycle control binding mismatch")
        match classified.lifecycle_action:
            case "pause":
                state = await self.pause()
            case "cancel":
                state = await self.cancel()
            case "resume":
                state = await self.resume()
            case _:
                raise EngineError("lifecycle callback requires an action")
        return ControlResponse(category="lifecycle", text=f"Run {state.status}.")
