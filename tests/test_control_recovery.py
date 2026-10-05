"""Offline recovery proofs; runtime execution deferred by the user's test stop."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping

import pytest
from pydantic import JsonValue
from statlab.errors import (
    EngineError,
    LlmCallError,
    LlmOutputError,
    PersistenceError,
    ToolCallError,
)
from statlab.graph.candidates import FitBudget, RunControl, ToolBoundary
from statlab.graph.recovery import is_retryable, recover_call
from statlab.mcp_client import CallContext, McpClient, Repair, ToolSpec
from statlab.persistence.repository import claim_test_scoring
from statlab_contracts.contract import Constraints, DataContract
from statlab_contracts.envelope import ErrorClass, Failure, ToolSuccess

from tests.test_graph_engine import World
from tests.test_graph_engine import engine_world as engine_world


@pytest.mark.asyncio
async def test_transient_attempt_cap_and_exponential_backoff() -> None:
    attempts = 0
    delays: list[float] = []
    failure = ToolCallError("fixture", Failure(error_class="transient", message="offline"))

    async def call() -> int:
        nonlocal attempts
        attempts += 1
        raise failure

    async def sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(ToolCallError) as caught:
        await recover_call(call, sleep=sleep)
    assert caught.value is failure
    assert attempts == 3 and delays == [1.0, 2.0]


@pytest.mark.asyncio
async def test_transient_success_preserves_result() -> None:
    attempts = 0

    async def call() -> int:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise LlmCallError(Failure(error_class="transient", message="offline"))
        return 7

    async def sleep(delay: float) -> None:
        assert delay in (1.0, 2.0)

    assert await recover_call(call, sleep=sleep) == 7
    assert attempts == 3


@pytest.mark.parametrize("category", ("tool_param", "data", "bug"))
@pytest.mark.asyncio
async def test_nontransient_has_no_outer_retry(category: ErrorClass) -> None:
    failure = Failure(
        error_class=category,
        message="offline",
        field_errors={"x": "invalid"} if category == "tool_param" else None,
    )
    calls = 0

    async def call() -> None:
        nonlocal calls
        calls += 1
        raise ToolCallError("fixture", failure)

    with pytest.raises(ToolCallError):
        await recover_call(call)
    assert calls == 1 and not is_retryable(category)
    assert is_retryable("transient")


@pytest.mark.asyncio
async def test_cancellation_typed_error_and_unknown_cause() -> None:
    async def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await recover_call(cancelled)
    original = PersistenceError("offline storage failure")

    async def typed() -> None:
        raise original

    with pytest.raises(PersistenceError) as typed_error:
        await recover_call(typed)
    assert typed_error.value is original
    secret = RuntimeError("API_KEY=hidden")

    async def unknown() -> None:
        raise secret

    with pytest.raises(EngineError) as bug:
        await recover_call(unknown)
    assert bug.value.cause is secret
    assert str(bug.value) == "Foreign call failed"


@pytest.mark.asyncio
async def test_exhausted_output_repair_is_never_reentered() -> None:
    original = LlmOutputError(
        Failure(
            error_class="tool_param", message="already repaired output", field_errors={"x": "bad"}
        )
    )
    calls = 0

    async def call() -> None:
        nonlocal calls
        calls += 1
        raise original

    with pytest.raises(LlmOutputError) as caught:
        await recover_call(call)
    assert caught.value is original and calls == 1


class ClaimedScoreClient(McpClient):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tools: dict[str, ToolSpec] = {}
        self.calls = 0

    async def call(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        repair: Repair | None = None,
    ) -> ToolSuccess:
        self.calls += 1
        assert claim_test_scoring(self.conn, ctx.paths.run_id)
        raise ToolCallError(tool, Failure(error_class="transient", message="ambiguous score"))


@pytest.mark.asyncio
async def test_dispatched_score_claim_refuses_outer_retry(
    engine_world: World, minimal_contract: DataContract
) -> None:
    client = ClaimedScoreClient(engine_world.conn)
    boundary = ToolBoundary(
        client,
        RunControl(),
        FitBudget(engine_world.conn, engine_world.paths.run_id, Constraints()),
    )
    context = CallContext(
        paths=engine_world.paths,
        node="fixture",
        node_execution_id="offline",
        allowed=frozenset({"score_fixture"}),
        contract=minimal_contract,
        dataset="offline",
    )
    with pytest.raises(ToolCallError, match="ambiguous score"):
        await boundary.call(context, "score_fixture", {})
    assert client.calls == 1


class RepairSequenceClient(McpClient):
    def __init__(self, conn: sqlite3.Connection, failures: tuple[ErrorClass, ...]) -> None:
        self.conn = conn
        self.tools: dict[str, ToolSpec] = {}
        self.failures = failures
        self.calls = 0

    async def _call_once(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
    ) -> ToolSuccess:
        self.calls += 1
        category = self.failures[self.calls - 1]
        raise ToolCallError(
            tool,
            Failure(
                error_class=category,
                message="offline sequence",
                field_errors={"x": "invalid"} if category == "tool_param" else None,
            ),
        )


@pytest.mark.parametrize(
    ("failures", "expected_calls", "expected_repairs", "expected_delays"),
    (
        (("tool_param", "transient"), 2, 1, []),
        (("transient", "tool_param", "transient"), 3, 1, [1.0]),
        (("transient", "transient", "tool_param"), 3, 0, [1.0, 2.0]),
    ),
)
@pytest.mark.asyncio
async def test_tool_dispatch_attempts_include_internal_repair(
    engine_world: World,
    minimal_contract: DataContract,
    failures: tuple[ErrorClass, ...],
    expected_calls: int,
    expected_repairs: int,
    expected_delays: list[float],
) -> None:
    client = RepairSequenceClient(engine_world.conn, failures)
    repairs = 0
    delays: list[float] = []

    async def repair(failure: Failure) -> dict[str, JsonValue]:
        nonlocal repairs
        repairs += 1
        return {"x": "corrected"}

    async def sleep(delay: float) -> None:
        delays.append(delay)

    boundary = ToolBoundary(
        client,
        RunControl(),
        FitBudget(engine_world.conn, engine_world.paths.run_id, Constraints()),
        sleep=sleep,
    )
    context = CallContext(
        paths=engine_world.paths,
        node="fixture",
        node_execution_id="offline",
        allowed=frozenset({"fixture"}),
        contract=minimal_contract,
        dataset="offline",
    )
    with pytest.raises(ToolCallError):
        await boundary.call(context, "fixture", {}, repair=repair)
    assert client.calls == expected_calls and repairs == expected_repairs
    assert delays == expected_delays
