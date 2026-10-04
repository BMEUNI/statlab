"""Domain-supplied traversal and audited fit admission at tool boundaries."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from statlab_contracts.candidate import CandidateSpec
from statlab_contracts.contract import Constraints
from statlab_contracts.envelope import Failure, ToolSuccess

from statlab.errors import EngineError, PersistenceError
from statlab.mcp_client import CallContext, McpClient, Repair

if TYPE_CHECKING:
    from statlab.graph.state import CandidateProgress, RunState, SpineEntry


class TraversalPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    overlap_shared: str | None = None
    design_node: str | None = None
    training_node: str | None = None
    evaluation_node: str | None = None

    @model_validator(mode="after")
    def _pair(self) -> TraversalPlan:
        if (self.training_node is None) != (self.evaluation_node is None):
            raise ValueError("training and evaluation must be paired")
        return self

    def region(self, spine: tuple[SpineEntry, ...]) -> tuple[int, int]:
        positions = [i for i, entry in enumerate(spine) if entry.kind == "PER_CANDIDATE"]
        start, end = (positions[0], positions[-1] + 1) if positions else (len(spine), len(spine))
        if positions != list(range(start, end)):
            raise EngineError("candidate region must be contiguous")
        names = {entry.name: i for i, entry in enumerate(spine)}
        for name in (
            self.overlap_shared,
            self.design_node,
            self.training_node,
            self.evaluation_node,
        ):
            if name is not None and name not in names:
                raise EngineError("traversal names an undeclared node")
        if self.overlap_shared is not None and (
            not positions or names[self.overlap_shared] != start - 1
        ):
            raise EngineError("overlap must be the final preceding shared node")
        if self.design_node is not None and (
            names[self.design_node] >= start or self.design_node == self.overlap_shared
        ):
            raise EngineError("design must precede overlap and candidates")
        if (
            self.training_node is not None
            and self.evaluation_node is not None
            and not (start <= names[self.training_node] < names[self.evaluation_node] < end)
        ):
            raise EngineError("training/evaluation order disagrees with candidate region")
        return start, end


type CandidatePlanner = Callable[[RunState, int], Awaitable[tuple[CandidateSpec, ...]]]
type RetuneDecider = Callable[[RunState, CandidateProgress], Awaitable[bool]]
type StopReason = Literal["paused", "cancelled", "budget_exhausted"]


class BoundaryStopError(Exception):
    """Internal lifecycle/admission signal; never a node failure."""

    def __init__(self, reason: StopReason) -> None:
        self.reason = reason
        super().__init__(reason)


class RunControl:
    def __init__(self) -> None:
        self._pause = False
        self._cancel = False

    def request_pause(self) -> None:
        self._pause = True

    def request_cancel(self) -> None:
        self._cancel = True

    @property
    def status(self) -> Literal["paused", "cancelled"] | None:
        return "cancelled" if self._cancel else "paused" if self._pause else None

    def check(self) -> None:
        if self.status is not None:
            raise BoundaryStopError(self.status)


class BudgetState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    total_fit_ms: int = Field(default=0, ge=0)
    limit_ms: int = Field(gt=0)

    @property
    def exhausted(self) -> bool:
        return self.total_fit_ms >= self.limit_ms

    @property
    def overshoot_ms(self) -> int:
        return max(0, self.total_fit_ms - self.limit_ms)


class FitBudget:
    """SQL audit is authoritative, including failed and interrupted attempts."""

    def __init__(self, conn: sqlite3.Connection, run_id: str, constraints: Constraints) -> None:
        self.conn, self.run_id = conn, run_id
        self.limit_ms = constraints.max_total_fit_seconds * 1000
        self._heavy: frozenset[str] = frozenset()

    def discover(self, client: McpClient) -> None:
        self._heavy = frozenset(name for name, tool in client.tools.items() if tool.heavy)

    def snapshot(self) -> BudgetState:
        try:
            total = sum(
                row["duration_ms"]
                for row in self.conn.execute(
                    "SELECT tool_name, duration_ms FROM tool_calls WHERE run_id = ?", (self.run_id,)
                )
                if row["tool_name"] in self._heavy
            )
        except sqlite3.Error as exc:
            raise PersistenceError("cannot read fit budget audit", cause=exc) from exc
        return BudgetState(total_fit_ms=total, limit_ms=self.limit_ms)

    def admit(self) -> None:
        if self.snapshot().exhausted:
            raise BoundaryStopError("budget_exhausted")


class ToolBoundary:
    """Use one already-entered client; preserve its dispatch and repair contract."""

    def __init__(self, client: McpClient, control: RunControl, budget: FitBudget) -> None:
        self.client, self.control, self.budget = client, control, budget
        budget.discover(client)

    async def call(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        repair: Repair | None = None,
    ) -> ToolSuccess:
        self.control.check()
        spec = self.client.tools.get(tool)
        if spec is not None and spec.heavy:
            self.budget.admit()

        guarded: Repair | None = None
        if repair is not None:
            callback = repair

            async def guarded_repair(failure: Failure) -> dict[str, JsonValue]:
                self.control.check()
                if spec is not None and spec.heavy:
                    self.budget.admit()
                corrected = await callback(failure)
                self.control.check()
                if spec is not None and spec.heavy:
                    self.budget.admit()
                return corrected

            guarded = guarded_repair

        try:
            result = await self.client.call(
                ctx,
                tool,
                params,
                trusted=trusted,
                repair=guarded,
            )
        except Exception:
            self.control.check()
            raise
        self.control.check()
        return result
