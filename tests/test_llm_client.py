"""Offline tests for typed LLM calls, accounting, and bounded concurrency."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sqlite3
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest
from litellm import ModelResponse
from litellm.exceptions import (
    APIConnectionError,
    BadGatewayError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from litellm.types.utils import Choices, Message
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from statlab.config import Settings
from statlab.errors import LlmCallError, LlmOutputError, PersistenceError
from statlab.graph.recovery import recover_call
from statlab.llm import LlmClient
from statlab.llm import client as client_module
from statlab.llm.client import DecisionRejectedError
from statlab.persistence.repository import (
    create_contract,
    create_node_execution,
    create_project,
    create_run,
    record_llm_call,
)
from statlab_contracts.contract import DataContract


class Result(BaseModel):
    value: str


class CheckedResult(BaseModel):
    value: str = Field(min_length=3)

    @model_validator(mode="after")
    def _reject_marker(self) -> CheckedResult:
        if self.value == "reject":
            raise ValueError("semantic validation failed")
        return self


class ExampleResult(BaseModel):
    model_config = {"json_schema_extra": {"examples": [{"value": "shown"}]}}

    value: str


class NonFiniteSchemaResult(BaseModel):
    model_config = ConfigDict(json_schema_extra={"example": math.nan})

    value: str


class UnexpectedResult(BaseModel):
    value: str

    @model_validator(mode="after")
    def _raise_unexpected(self) -> UnexpectedResult:
        raise TypeError("API_KEY=hidden-validator-detail")


@pytest.fixture
def llm_context(
    migrated_db: sqlite3.Connection, minimal_contract: DataContract, tmp_path: Path
) -> tuple[sqlite3.Connection, str, str]:
    project = create_project(migrated_db, slug="llm-test", name="LLM test")
    contract = create_contract(
        migrated_db,
        project_id=project.id,
        path=tmp_path / "contract.json",
        contract=minimal_contract,
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract.id,
        mode="autonomous",
        config_json="{}",
    )
    execution = create_node_execution(migrated_db, run_id=run.id, node_name="intake", seq=0)
    return migrated_db, run.id, execution.id


@pytest.fixture(autouse=True)
def _peak_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module, "_utc_now", lambda: datetime(2026, 10, 6, 7, 0, tzinfo=UTC))


def _settings(tmp_path: Path, limit: int = 4) -> Settings:
    token = SecretStr("test-token")
    return Settings(
        mcp_token=token,
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        max_concurrent_llm=limit,
    )


def _response(
    content: str | None, *, prompt: int = 3, completion: int = 2, cached: int = 0
) -> ModelResponse:
    return ModelResponse(
        choices=[
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        model="test-model",
        usage={
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
        },
    )


def _rows(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(
            "SELECT signature, model, prompt_tokens, completion_tokens, cached_tokens, cost_usd "
            "FROM llm_calls ORDER BY rowid"
        ).fetchall()
    ]


@pytest.mark.asyncio
async def test_setup_completion_repairs_once_without_run_accounting(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, _, _ = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []
    answers = iter([_response("{bad"), _response('{"value":"fixed"}')])

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return next(answers)

    client = LlmClient(_settings(tmp_path), acompletion=fake)
    before = _rows(conn)
    result = await client.complete_setup(
        model="deepseek/deepseek-flash",
        messages=[{"role": "user", "content": "setup"}],
        output=Result,
    )
    assert result == Result(value="fixed")
    assert len(calls) == 2
    assert all(call["num_retries"] == 0 and call["timeout"] == 180 for call in calls)
    assert _rows(conn) == before


@pytest.mark.asyncio
async def test_setup_completion_rejects_two_invalid_answers_without_audit(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, _, _ = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls = 0

    async def fake(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _response("{bad")

    before = _rows(conn)
    with pytest.raises(LlmOutputError):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete_setup(
            model="deepseek/deepseek-flash",
            messages=[{"role": "user", "content": "setup"}],
            output=Result,
        )
    assert calls == 2 and _rows(conn) == before


@pytest.mark.asyncio
async def test_setup_completion_cancellation_propagates(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, _, _ = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    entered = asyncio.Event()

    async def fake(**kwargs: object) -> object:
        del kwargs
        entered.set()
        await asyncio.Event().wait()
        return _response('{"value":"never"}')

    before = _rows(conn)
    task = asyncio.create_task(
        LlmClient(_settings(tmp_path), acompletion=fake).complete_setup(
            model="deepseek/deepseek-flash",
            messages=[{"role": "user", "content": "setup"}],
            output=Result,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _rows(conn) == before


@pytest.mark.asyncio
async def test_setup_completion_rejects_invalid_usage_without_audit(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, _, _ = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    async def fake(**kwargs: object) -> object:
        del kwargs
        return _response('{"value":"ok"}', prompt=2, cached=3)

    before = _rows(conn)
    with pytest.raises(LlmCallError):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete_setup(
            model="deepseek/deepseek-flash",
            messages=[{"role": "user", "content": "setup"}],
            output=Result,
        )
    assert _rows(conn) == before


@pytest.mark.asyncio
async def test_run_and_setup_share_one_llm_semaphore(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    entries: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        entries.append(kwargs)
        if len(entries) == 1:
            first_entered.set()
            await release_first.wait()
        return _response('{"value":"ok"}')

    client = LlmClient(_settings(tmp_path, limit=1), acompletion=fake)
    run_task = asyncio.create_task(
        client.complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[{"role": "user", "content": "run"}],
            output=Result,
        )
    )
    await first_entered.wait()
    setup_task = asyncio.create_task(
        client.complete_setup(
            model="deepseek/deepseek-flash",
            messages=[{"role": "user", "content": "setup"}],
            output=Result,
        )
    )
    await asyncio.sleep(0)
    assert len(entries) == 1
    release_first.set()
    assert await run_task == Result(value="ok")
    assert await setup_task == Result(value="ok")
    assert len(entries) == 2
    assert all(item["timeout"] == 180 and item["num_retries"] == 0 for item in entries)
    assert len(_rows(conn)) == 1


@pytest.mark.asyncio
async def test_json_tier_repairs_once_and_records_cached_flash_cost(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []
    answers = iter(
        [_response("{bad"), _response('{"value":"fixed"}', prompt=100, completion=20, cached=40)]
    )

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return next(answers)

    messages = [
        {"role": "system", "content": "Keep invariant prefix."},
        {"role": "user", "content": "Go"},
    ]
    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="deepseek/deepseek-flash",
        messages=messages,
        output=Result,
    )

    assert result == Result(value="fixed")
    assert messages[0]["content"] == "Keep invariant prefix."
    assert len(calls) == 2
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["num_retries"] == 0 and calls[0]["timeout"] == 180
    assert calls[0]["thinking"] == {"type": "disabled"}
    first_messages = calls[0]["messages"]
    second_messages = calls[1]["messages"]
    assert isinstance(first_messages, list) and isinstance(second_messages, list)
    assert isinstance(first_messages[0], dict) and isinstance(second_messages[-2], dict)
    assert "json" in first_messages[0]["content"]
    assert "syntax only" in first_messages[0]["content"]
    assert "{bad" in second_messages[-2]["content"]
    assert second_messages[0]["content"].startswith("Keep invariant prefix.")
    rows = _rows(conn)
    assert len(rows) == 2
    assert rows[0][2:] == (3, 2, 0, (3 * 0.30 + 2 * 1.20) / 1_000_000)
    assert rows[1][2:] == (100, 20, 40, (60 * 0.30 + 40 * 0.006 + 20 * 1.20) / 1_000_000)


@pytest.mark.asyncio
async def test_native_schema_and_reasoning_only_for_deepseek(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="openai/test",
        messages=[{"role": "user", "content": "Go"}],
        output=Result,
    )
    assert result.value == "ok"
    assert len(_rows(conn)) == 1
    assert calls[0]["response_format"] is Result
    assert "thinking" not in calls[0] and "reasoning_effort" not in calls[0]


@pytest.mark.asyncio
async def test_schema_tier_custom_validator_gets_exactly_one_repair(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"reject"}' if len(calls) == 1 else '{"value":"accepted"}')

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="openai/test",
        messages=[],
        output=CheckedResult,
    )
    assert result.value == "accepted"
    assert len(calls) == len(_rows(conn)) == 2


@pytest.mark.asyncio
async def test_semantic_check_rejection_gets_one_repair(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="statlab.llm.transcript")
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"bad"}' if len(calls) == 1 else '{"value":"good"}')

    def check(decision: Result) -> None:
        if decision.value == "bad":
            raise DecisionRejectedError("value is not allowed; allowed: ['good']")

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="openai/test",
        messages=[],
        output=Result,
        check=check,
    )
    assert result.value == "good"
    assert len(calls) == 2
    second_messages = calls[1]["messages"]
    assert isinstance(second_messages, list) and isinstance(second_messages[-1], dict)
    assert "value is not allowed; allowed: ['good']" in second_messages[-1]["content"]
    assert "LLM request" in caplog.text
    assert "attempt=2" in caplog.text
    assert '{"value":"bad"}' in caplog.text
    assert "LLM output rejected" in caplog.text


async def _run_scripted(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replies: list[str],
) -> tuple[Result, int]:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response(replies[min(len(calls), len(replies)) - 1])

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="openai/test",
        messages=[],
        output=Result,
    )
    return result, len(calls)


@pytest.mark.asyncio
async def test_trailing_closing_brackets_are_tolerated(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, count = await _run_scripted(llm_context, tmp_path, monkeypatch, ['{"value":"ok"}}\n'])
    assert result.value == "ok"
    assert count == 1


@pytest.mark.asyncio
async def test_other_trailing_text_still_repairs(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, count = await _run_scripted(
        llm_context, tmp_path, monkeypatch, ['{"value":"ok"} trailing', '{"value":"ok"}']
    )
    assert result.value == "ok"
    assert count == 2


@pytest.mark.asyncio
async def test_undecodable_json_still_repairs(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, count = await _run_scripted(
        llm_context, tmp_path, monkeypatch, ['{"value":', '{"value":"ok"}']
    )
    assert result.value == "ok"
    assert count == 2


@pytest.mark.asyncio
async def test_semantic_check_rejected_twice_is_output_error(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"bad"}')

    def check(decision: Result) -> None:
        raise DecisionRejectedError("never")

    with pytest.raises(LlmOutputError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="openai/test",
            messages=[],
            output=Result,
            check=check,
        )
    assert error.value.failure.field_errors == {"output": "never"}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_unexpected_validator_error_is_safe_bug_without_repair(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls = 0
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    async def fake(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _response('{"value":"ok"}', prompt=8, completion=3)

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="vendor/schema",
            messages=[],
            output=UnexpectedResult,
        )
    assert error.value.error_class == "bug"
    assert isinstance(error.value.cause, TypeError)
    assert "API_KEY" not in str(error.value)
    assert calls == 1
    assert _rows(conn)[0][2:5] == (8, 3, 0)


@pytest.mark.asyncio
async def test_legacy_flash_peak_cost_and_explicit_reasoning(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}', prompt=10, completion=5, cached=4)

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="deepseek/deepseek-v4-flash-vision-exp",
        messages=[],
        output=Result,
        reasoning=True,
    )
    assert calls[0]["thinking"] == {"type": "enabled"}
    assert calls[0]["reasoning_effort"] == "high"
    assert _rows(conn) == [
        (
            "Result",
            "deepseek/deepseek-v4-flash-vision-exp",
            10,
            5,
            4,
            (6 * 0.30 + 4 * 0.006 + 5 * 1.20) / 1_000_000,
        )
    ]


@pytest.mark.asyncio
async def test_generic_cost_uses_requested_model(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    cost_args: list[dict[str, object]] = []
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    def cost(**kwargs: object) -> float:
        cost_args.append(kwargs)
        return 0.125

    monkeypatch.setattr(litellm, "completion_cost", cost)

    async def fake(**kwargs: object) -> object:
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/specific-model",
        messages=[],
        output=Result,
    )
    assert cost_args[0]["model"] == "vendor/specific-model"
    assert _rows(conn)[0][-1] == 0.125


@pytest.mark.asyncio
async def test_missing_usage_and_cost_price_record_zero(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kwargs: None)

    async def fake(**kwargs: object) -> object:
        return ModelResponse(
            choices=[
                {
                    "message": {"role": "assistant", "content": '{"value":"ok"}'},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            model="vendor/no-cost",
        )

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/no-cost",
        messages=[],
        output=Result,
    )
    assert result.value == "ok"
    assert _rows(conn) == [("Result", "vendor/no-cost", 0, 0, 0, 0.0)]


@pytest.mark.parametrize(
    "cost_value",
    [
        pytest.param(None, id="missing"),
        pytest.param(True, id="boolean"),
        pytest.param(-0.1, id="negative"),
        pytest.param(math.inf, id="infinite"),
        pytest.param(math.nan, id="nan"),
        pytest.param(RuntimeError("API_KEY=private"), id="provider-error"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_unavailable_generic_cost_records_zero(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cost_value: object,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    def price(**kwargs: object) -> object:
        if isinstance(cost_value, BaseException):
            raise cost_value
        return cost_value

    monkeypatch.setattr(litellm, "completion_cost", price)

    async def fake(**kwargs: object) -> object:
        return _response('{"value":"ok"}')

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/unknown-price",
        messages=[],
        output=Result,
    )
    assert result.value == "ok"
    assert _rows(conn)[0][-1] == 0.0
    assert "recording zero" in caplog.text
    assert "API_KEY=private" not in caplog.text


@pytest.mark.asyncio
async def test_cached_tokens_cannot_exceed_prompt_and_failed_usage_is_logged(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context

    async def fake(**kwargs: object) -> object:
        return _response('{"value":"ok"}', prompt=2, completion=1, cached=3)

    with pytest.raises(LlmCallError, match="invalid token usage"):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[],
            output=Result,
        )
    assert _rows(conn) == [("Result", "deepseek/deepseek-flash", 0, 0, 0, 0.0)]


@pytest.mark.parametrize(
    "usage",
    [
        pytest.param(
            SimpleNamespace(prompt_tokens=-1, completion_tokens=1, prompt_tokens_details=None),
            id="negative-count",
        ),
        pytest.param(
            SimpleNamespace(prompt_tokens="3", completion_tokens=1, prompt_tokens_details=None),
            id="noninteger-count",
        ),
        pytest.param(
            SimpleNamespace(prompt_tokens=3, completion_tokens=1, prompt_tokens_details=object()),
            id="malformed-details",
        ),
    ],
)
@pytest.mark.asyncio
async def test_malformed_usage_is_wrapped_and_accounted_as_zero(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    usage: object,
) -> None:
    conn, run_id, execution_id = llm_context

    async def fake(**kwargs: object) -> object:
        return ModelResponse(
            choices=_response('{"value":"ok"}').choices,
            model="deepseek/deepseek-flash",
            usage=usage,
        )

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/test",
            messages=[],
            output=Result,
        )
    assert error.value.error_class == "bug"
    assert _rows(conn) == [("Result", "deepseek/test", 0, 0, 0, 0.0)]


@pytest.mark.asyncio
async def test_second_invalid_answer_is_tool_param_without_foreign_text(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context

    async def fake(**kwargs: object) -> object:
        return _response("{}")

    with pytest.raises(LlmOutputError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[],
            output=Result,
        )
    assert error.value.error_class == "tool_param"
    assert error.value.retry_budget == 2
    assert error.value.failure.field_errors
    assert len(_rows(conn)) == 2


@pytest.mark.asyncio
async def test_empty_content_is_repaired(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    calls = 0

    async def fake(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _response(None if calls == 1 else '{"value":"repaired"}')

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="deepseek/test",
        messages=[],
        output=Result,
    )
    assert result.value == "repaired"
    assert calls == len(_rows(conn)) == 2


@pytest.mark.parametrize(
    ("error_name", "expected_class"),
    [
        ("rate_limit", "transient"),
        ("timeout", "transient"),
        ("connection", "transient"),
        ("service", "transient"),
        ("internal", "transient"),
        ("bad_gateway", "transient"),
        ("bug", "bug"),
    ],
)
@pytest.mark.asyncio
async def test_provider_exception_is_safely_wrapped_and_logged(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    error_name: str,
    expected_class: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn, run_id, execution_id = llm_context
    credential = "SECRET_KEY=do-not-show"

    cause: Exception
    if error_name == "rate_limit":
        cause = RateLimitError(credential, "provider", "request")
    elif error_name == "timeout":
        cause = Timeout(credential, "request", "provider")
    elif error_name == "connection":
        cause = APIConnectionError(credential, "provider", "request")
    elif error_name == "service":
        cause = ServiceUnavailableError(credential, "provider", "request")
    elif error_name == "internal":
        cause = InternalServerError(credential, "provider", "request")
    elif error_name == "bad_gateway":
        cause = BadGatewayError(credential, "provider", "request")
    else:
        cause = RuntimeError(credential)

    async def fake(**kwargs: object) -> object:
        raise cause

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/test",
            messages=[],
            output=Result,
        )
    assert error.value.error_class == expected_class
    assert error.value.retry_budget == (3 if expected_class == "transient" else 0)
    assert "SECRET_KEY" not in str(error.value)
    assert error.value.cause is cause
    assert f"LLM request failed: {type(cause).__name__}" in caplog.text
    assert "SECRET_KEY" not in caplog.text
    if error_name == "rate_limit":
        assert "status=429" in caplog.text
    assert _rows(conn) == [("Result", "deepseek/test", 0, 0, 0, 0.0)]


@pytest.mark.asyncio
async def test_bad_response_logged_then_wrapped(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context

    async def fake(**kwargs: object) -> object:
        return {"choices": []}

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="unknown/model",
            messages=[],
            output=Result,
        )
    assert error.value.error_class == "bug"
    assert len(_rows(conn)) == 1


@pytest.mark.asyncio
async def test_empty_choices_are_logged_and_wrapped(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context

    async def fake(**kwargs: object) -> object:
        return ModelResponse(
            choices=[],
            model="deepseek/deepseek-flash",
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 5,
                "total_tokens": 16,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        )

    with pytest.raises(LlmCallError, match="no choices"):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[],
            output=Result,
        )
    assert _rows(conn) == [
        (
            "Result",
            "deepseek/deepseek-flash",
            11,
            5,
            3,
            (8 * 0.30 + 3 * 0.006 + 5 * 1.20) / 1_000_000,
        )
    ]


@pytest.mark.asyncio
async def test_flash_off_peak_cost_is_half(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    monkeypatch.setattr(client_module, "_utc_now", lambda: datetime(2026, 10, 8, 12, 0, tzinfo=UTC))

    async def fake(**kwargs: object) -> object:
        return ModelResponse(
            choices=[],
            model="deepseek/deepseek-flash",
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 5,
                "total_tokens": 16,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        )

    with pytest.raises(LlmCallError, match="no choices"):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[],
            output=Result,
        )
    assert _rows(conn) == [
        (
            "Result",
            "deepseek/deepseek-flash",
            11,
            5,
            3,
            (8 * 0.30 + 3 * 0.006 + 5 * 1.20) / 1_000_000 * 0.5,
        )
    ]


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime(2026, 10, 5, 0, 59, tzinfo=UTC), False),
        (datetime(2026, 10, 5, 1, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 5, 3, 59, tzinfo=UTC), True),
        (datetime(2026, 10, 5, 4, 0, tzinfo=UTC), False),
        (datetime(2026, 10, 5, 5, 59, tzinfo=UTC), False),
        (datetime(2026, 10, 5, 6, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 5, 9, 59, tzinfo=UTC), True),
        (datetime(2026, 10, 5, 10, 0, tzinfo=UTC), False),
        (datetime(2026, 10, 9, 7, 0, tzinfo=UTC), True),
        (datetime(2026, 10, 10, 7, 0, tzinfo=UTC), False),
        (datetime(2026, 10, 11, 2, 0, tzinfo=UTC), False),
    ],
)
def test_flash_peak_window(at: datetime, expected: bool) -> None:
    assert client_module._flash_peak(at) is expected


@pytest.mark.parametrize("malformed_shape", ["choice", "content"])
@pytest.mark.asyncio
async def test_malformed_chat_shapes_are_safe_bug_errors(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    malformed_shape: str,
) -> None:
    conn, run_id, execution_id = llm_context
    usage = _response("ok").usage

    async def fake(**kwargs: object) -> object:
        choices: object
        if malformed_shape == "choice":
            choices = [SimpleNamespace(delta=SimpleNamespace(content="streamed"))]
        else:
            choices = [{"message": {"content": ["not text"]}}]
        return ModelResponse.model_validate(
            {"choices": choices, "model": "deepseek/deepseek-flash", "usage": usage}
        )

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[],
            output=Result,
        )
    assert error.value.error_class == "bug"
    assert isinstance(error.value.cause, ValidationError)
    assert len(_rows(conn)) == 1
    assert _rows(conn)[0][2:5] == (0, 0, 0)


@pytest.mark.asyncio
async def test_wrong_run_attribution_rejected_before_dispatch(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, _, execution_id = llm_context
    called = False

    async def fake(**kwargs: object) -> object:
        nonlocal called
        called = True
        return _response('{"value":"ok"}')

    with pytest.raises(PersistenceError, match="does not belong to run"):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id="r_wrong",
            node_execution_id=execution_id,
            model="deepseek/test",
            messages=[],
            output=Result,
        )
    assert not called
    assert not _rows(conn)


def test_repository_rejects_wrong_run_and_wraps_integrity_error(
    llm_context: tuple[sqlite3.Connection, str, str],
) -> None:
    conn, run_id, execution_id = llm_context
    with pytest.raises(PersistenceError, match="does not belong to run"):
        record_llm_call(
            conn,
            run_id="r_other",
            node_execution_id=execution_id,
            signature="Result",
            model="provider/model",
            prompt_tokens=1,
            completion_tokens=1,
            cached_tokens=0,
            cost_usd=0.0,
            duration_ms=1,
        )

    conn.execute(
        "CREATE TRIGGER reject_llm_call BEFORE INSERT ON llm_calls "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    with pytest.raises(PersistenceError, match="llm_calls attribution") as error:
        record_llm_call(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            signature="Result",
            model="provider/model",
            prompt_tokens=1,
            completion_tokens=1,
            cached_tokens=0,
            cost_usd=0.0,
            duration_ms=1,
        )
    assert isinstance(error.value.cause, sqlite3.IntegrityError)


def test_repository_wraps_closed_database(
    llm_context: tuple[sqlite3.Connection, str, str],
) -> None:
    conn, run_id, execution_id = llm_context
    conn.close()
    with pytest.raises(PersistenceError, match="cannot record LLM call") as error:
        record_llm_call(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            signature="Result",
            model="provider/model",
            prompt_tokens=1,
            completion_tokens=1,
            cached_tokens=0,
            cost_usd=0.0,
            duration_ms=1,
        )
    assert isinstance(error.value.cause, sqlite3.ProgrammingError)


@pytest.mark.asyncio
async def test_client_wraps_closed_database_before_dispatch(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    conn.close()
    called = False

    async def fake(**kwargs: object) -> object:
        nonlocal called
        called = True
        return _response('{"value":"ok"}')

    with pytest.raises(PersistenceError, match="cannot verify LLM call execution"):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/model",
            messages=[],
            output=Result,
        )
    assert not called


@pytest.mark.asyncio
async def test_response_audit_failure_is_persistence_error_without_retry(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    calls = 0

    async def fake(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        conn.close()
        return _response('{"value":"ok"}')

    with pytest.raises(PersistenceError, match="cannot record LLM call") as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/model",
            messages=[],
            output=Result,
        )
    assert isinstance(error.value.cause, sqlite3.ProgrammingError)
    assert calls == 1


@pytest.mark.asyncio
async def test_usage_getter_failure_is_safe_bug_and_logs_zero_once(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    credential = "API_KEY=private-usage-detail"
    calls = 0

    class BrokenUsage:
        @property
        def prompt_tokens(self) -> int:
            raise RuntimeError(credential)

    async def fake(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return ModelResponse(
            choices=_response('{"value":"ok"}').choices,
            model="deepseek/model",
            usage=BrokenUsage(),
        )

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/model",
            messages=[],
            output=Result,
        )
    assert error.value.error_class == "bug"
    assert isinstance(error.value.cause, RuntimeError)
    assert credential not in str(error.value)
    assert calls == 1
    assert _rows(conn) == [("Result", "deepseek/model", 0, 0, 0, 0.0)]


@pytest.mark.asyncio
async def test_semaphore_bounds_overlap_and_releases_after_cancel(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    entered = 0
    peak = 0
    release = asyncio.Event()
    both_entered = asyncio.Event()

    async def fake(**kwargs: object) -> object:
        nonlocal entered, peak
        entered += 1
        peak = max(peak, entered)
        if entered == 2:
            both_entered.set()
        await release.wait()
        entered -= 1
        return _response('{"value":"ok"}')

    client = LlmClient(_settings(tmp_path, limit=2), acompletion=fake)
    jobs = [
        asyncio.create_task(
            client.complete(
                conn,
                run_id=run_id,
                node_execution_id=execution_id,
                model="deepseek/test",
                messages=[],
                output=Result,
            )
        )
        for _ in range(3)
    ]
    await asyncio.wait_for(both_entered.wait(), timeout=2)
    assert peak == 2
    release.set()
    await asyncio.gather(*jobs)
    assert peak == 2
    assert len(_rows(conn)) == 3


@pytest.mark.asyncio
async def test_cancelled_attempt_is_logged_and_releases_permit(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    started = asyncio.Event()
    count = 0

    async def fake(**kwargs: object) -> object:
        nonlocal count
        count += 1
        if count == 1:
            started.set()
            await asyncio.Event().wait()
        return _response('{"value":"ok"}')

    client = LlmClient(_settings(tmp_path, limit=1), acompletion=fake)
    first = asyncio.create_task(
        client.complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/test",
            messages=[],
            output=Result,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert _rows(conn) == [("Result", "deepseek/test", 0, 0, 0, 0.0)]

    result = await asyncio.wait_for(
        client.complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/test",
            messages=[],
            output=Result,
        ),
        timeout=2,
    )
    assert result.value == "ok"
    assert count == 2
    assert len(_rows(conn)) == 2


@pytest.mark.asyncio
async def test_failed_cancel_audit_preserves_original_cancellation(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn, run_id, execution_id = llm_context
    original = asyncio.CancelledError("request cancelled API_KEY=private")
    audit_error = PersistenceError("audit failed", cause=RuntimeError("TOKEN=private-cause"))

    async def fake(**kwargs: object) -> object:
        raise original

    def fail_record(
        connection: sqlite3.Connection,
        *,
        run_id: str,
        node_execution_id: str,
        signature: str,
        model: str,
        duration_ms: int,
    ) -> None:
        raise audit_error

    monkeypatch.setattr("statlab.llm.client._record", fail_record)
    with pytest.raises(asyncio.CancelledError) as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/model",
            messages=[],
            output=Result,
        )
    assert error.value is original
    assert "API_KEY=private" not in caplog.text
    assert "TOKEN=private-cause" not in caplog.text


@pytest.mark.asyncio
async def test_prompt_fallback_omits_response_format(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda **kwargs: [])

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/model",
        messages=[],
        output=Result,
    )
    assert "response_format" not in calls[0]
    prompt_messages = calls[0]["messages"]
    assert isinstance(prompt_messages, list) and isinstance(prompt_messages[0], dict)
    assert '"properties"' in prompt_messages[0]["content"]


@pytest.mark.asyncio
async def test_prompt_repair_keeps_invariant_prefix_first(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    responses = iter([_response("invalid"), _response('{"value":"fixed"}')])
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda **kwargs: [])

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return next(responses)

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/prompt",
        messages=[
            {"role": "system", "content": "Stable invariant."},
            {"role": "user", "content": "Variable question."},
        ],
        output=Result,
    )
    assert result.value == "fixed"
    first = calls[0]["messages"]
    second = calls[1]["messages"]
    assert isinstance(first, list) and isinstance(second, list)
    assert first[0]["content"].startswith("Stable invariant.")
    assert second[0]["content"] == first[0]["content"]
    assert second[-2]["content"] == "invalid"
    assert second[-1]["content"].startswith("Repair the output.")


@pytest.mark.asyncio
async def test_reasoning_content_is_ignored_and_not_persisted(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    hidden_reasoning = "PRIVATE_REASONING_DO_NOT_STORE"

    async def fake(**kwargs: object) -> object:
        message = Message(content='{"value":"visible"}', reasoning_content=hidden_reasoning)
        return ModelResponse(
            choices=[Choices(message=message)],
            model="openai/test",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    result = await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="openai/test",
        messages=[],
        output=Result,
    )
    assert result.value == "visible"
    stored = conn.execute("SELECT * FROM llm_calls").fetchone()
    assert hidden_reasoning not in str(tuple(stored))


@pytest.mark.asyncio
async def test_supported_json_mode_includes_model_example(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(
        litellm, "get_supported_openai_params", lambda **kwargs: ["response_format"]
    )

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/json",
        messages=[],
        output=ExampleResult,
    )
    assert calls[0]["response_format"] == {"type": "json_object"}
    request_messages = calls[0]["messages"]
    assert isinstance(request_messages, list) and isinstance(request_messages[0], dict)
    assert '{"value":"shown"}' in request_messages[0]["content"]


@pytest.mark.asyncio
async def test_explicit_empty_example_is_preserved(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(
        litellm, "get_supported_openai_params", lambda **kwargs: ["response_format"]
    )

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="vendor/json",
        messages=[],
        output=ExampleResult,
        example={},
    )
    request_messages = calls[0]["messages"]
    assert isinstance(request_messages, list) and isinstance(request_messages[0], dict)
    assert "Example: {}" in request_messages[0]["content"]
    assert "syntax only" not in request_messages[0]["content"]


@pytest.mark.asyncio
async def test_invalid_example_serialization_fails_before_dispatch(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    called = False
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(
        litellm, "get_supported_openai_params", lambda **kwargs: ["response_format"]
    )

    async def fake(**kwargs: object) -> object:
        nonlocal called
        called = True
        return _response('{"value":"ok"}')

    with pytest.raises(LlmCallError, match="schema could not be serialized") as error:
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="vendor/json",
            messages=[],
            output=Result,
            example={"value": math.nan},
        )
    assert error.value.error_class == "bug"
    assert not called and not _rows(conn)


@pytest.mark.asyncio
async def test_invalid_generated_schema_serialization_fails_before_dispatch(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    called = False
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda **kwargs: [])

    async def fake(**kwargs: object) -> object:
        nonlocal called
        called = True
        return _response('{"value":"ok"}')

    with pytest.raises(LlmCallError, match="schema could not be serialized"):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="vendor/prompt",
            messages=[],
            output=NonFiniteSchemaResult,
        )
    assert not called and not _rows(conn)


@pytest.mark.asyncio
async def test_ollama_prefix_forces_prompt_mode(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="ollama/local",
        messages=[],
        output=Result,
    )
    assert "response_format" not in calls[0]
    assert "thinking" not in calls[0]


@pytest.mark.asyncio
async def test_unknown_capability_falls_back_to_prompt(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context

    def unsupported(model: str) -> bool:
        raise RuntimeError("foreign detail")

    monkeypatch.setattr(litellm, "supports_response_schema", unsupported)
    calls: list[dict[str, object]] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="unknown/model",
        messages=[],
        output=Result,
    )
    assert "response_format" not in calls[0]


@pytest.mark.asyncio
async def test_missing_capability_metadata_falls_back_to_prompt(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda **kwargs: None)

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response('{"value":"ok"}')

    await LlmClient(_settings(tmp_path), acompletion=fake).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="unknown/model",
        messages=[],
        output=Result,
    )
    assert "response_format" not in calls[0]


def test_provider_names_are_local_to_llm_package() -> None:
    package = Path(__file__).parents[1] / "backend" / "statlab"
    provider_terms = ("deepseek", "openai", "anthropic", "claude", "gpt-", "ollama")
    foreign_mentions = [
        f"{path.relative_to(package)}:{term}"
        for path in package.rglob("*.py")
        if "llm" not in path.relative_to(package).parts
        for term in provider_terms
        if term in path.read_text(encoding="utf-8").lower()
    ]
    assert foreign_mentions == []


@pytest.mark.asyncio
async def test_litellm_offline_mock_round_trip() -> None:
    response = await litellm.acompletion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Return JSON."}],
        mock_response='{"value":"offline"}',
    )
    assert isinstance(response, ModelResponse)
    assert response.choices[0].message.content == '{"value":"offline"}'


@pytest.mark.asyncio
async def test_default_client_path_uses_real_litellm_offline_mock(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    real_completion = litellm.acompletion
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    async def offline_mock(**kwargs: object) -> ModelResponse:
        response = await real_completion(**kwargs, mock_response='{"value":"client-mock"}')
        assert isinstance(response, ModelResponse)
        return response

    monkeypatch.setattr(litellm, "acompletion", offline_mock)
    result = await LlmClient(_settings(tmp_path)).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "Return a value."}],
        output=Result,
    )
    assert result == Result(value="client-mock")
    assert len(_rows(conn)) == 1


def test_import_sets_local_cost_map_before_litellm_without_socket_attempts(tmp_path: Path) -> None:
    script = (
        "import os,socket; os.environ.pop('LITELLM_LOCAL_MODEL_COST_MAP',None); "
        "calls=[]; original=socket.socket.connect; "
        "socket.socket.connect=lambda self,address: calls.append(address) or "
        "(_ for _ in ()).throw(OSError('blocked')); "
        "import statlab.llm.client; assert os.environ['LITELLM_LOCAL_MODEL_COST_MAP']=='True'; "
        "assert calls==[], calls"
    )
    completed = subprocess.run(  # noqa: S603 - executable and script are fixed test inputs.
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env={
            key: value for key, value in os.environ.items() if key != "LITELLM_LOCAL_MODEL_COST_MAP"
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.asyncio
async def test_request_recovery_does_not_restart_output_repair(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    calls: list[dict[str, object]] = []
    delays: list[float] = []

    async def fake(**kwargs: object) -> object:
        calls.append(kwargs)
        if len(calls) == 2:
            raise APIConnectionError(message="offline transient", llm_provider="fixture", model="x")
        return _response("{invalid")

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def recovery[T](call: Callable[[], Awaitable[T]]) -> T:
        return await recover_call(call, sleep=sleep)

    with pytest.raises(LlmOutputError):
        await LlmClient(_settings(tmp_path), acompletion=fake).complete(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="deepseek/deepseek-flash",
            messages=[{"role": "user", "content": "offline"}],
            output=Result,
            recovery=recovery,
        )
    assert len(calls) == 3 and delays == [1.0]
    assert calls[1]["messages"] == calls[2]["messages"]
    assert calls[0]["messages"] != calls[1]["messages"]
    assert len(_rows(conn)) == 3
