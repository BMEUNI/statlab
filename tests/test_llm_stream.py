"""Offline tests for provisional prose streaming and final typed validation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from litellm import ModelResponse
from litellm.exceptions import APIConnectionError
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage
from openai.types.completion_usage import PromptTokensDetails
from pydantic import BaseModel, Field
from statlab.errors import LlmCallError, LlmOutputError, PersistenceError
from statlab.llm.client import LlmClient, _partial_text

from tests import test_llm_client as llm_helpers

_response = llm_helpers._response
_rows = llm_helpers._rows
_settings = llm_helpers._settings
llm_context = llm_helpers.llm_context


class StreamResult(BaseModel):
    text: str = Field(min_length=1)


class NoText(BaseModel):
    value: str


class IntText(BaseModel):
    text: int


@dataclass(slots=True)
class FakeStream:
    chunks: tuple[ModelResponseStream, ...]
    closed: bool = False
    entered: asyncio.Event | None = None
    release: asyncio.Event | None = None
    error: Exception | None = None
    block_before: int | None = None

    async def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        for index, item in enumerate(self.chunks):
            block_index = (
                self.block_before if self.block_before is not None else len(self.chunks) - 1
            )
            if self.entered is not None and index == block_index:
                self.entered.set()
            if self.release is not None and index == block_index:
                await self.release.wait()
            yield item
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


def chunk(content: str | None, *, usage: bool = False) -> ModelResponseStream:
    if usage:
        return ModelResponseStream(
            choices=[],
            model="test-model",
            usage=Usage(
                prompt_tokens=3,
                completion_tokens=2,
                total_tokens=5,
                prompt_tokens_details=PromptTokensDetails(cached_tokens=0),
            ),
        )
    return ModelResponseStream(
        choices=[StreamingChoices(index=0, delta=Delta(content=content))],
        model="test-model",
    )


@pytest.mark.asyncio
async def test_prose_chunks_reset_validate_and_account(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    stream = FakeStream(
        (chunk('{"text":"Hello '), chunk("world\\n"), chunk('\\u00e9"}'), chunk(None, usage=True))
    )
    entered, release = asyncio.Event(), asyncio.Event()
    stream.entered, stream.release, stream.block_before = entered, release, 1
    calls: list[dict[str, object]] = []

    async def provider(**kwargs: object) -> object:
        calls.append(kwargs)
        return stream

    monkeypatch.setattr("litellm.supports_response_schema", lambda model: True)
    monkeypatch.setattr("litellm.completion_cost", lambda **kwargs: 0.2)
    seen: list[tuple[str, bool]] = []
    client = LlmClient(_settings(tmp_path), acompletion=provider)
    task = asyncio.create_task(
        client.complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=({"role": "user", "content": "say hello"},),
            output=StreamResult,
            sink=lambda text, reset: _record_sink(seen, text, reset),
        )
    )
    async with asyncio.timeout(2):
        await entered.wait()
    assert "".join(text for text, reset in seen if not reset) == "Hello "
    release.set()
    result = await task
    assert result.text == "Hello world\né"
    assert seen[0] == ("", True)
    assert "".join(text for text, reset in seen if not reset) == result.text
    assert stream.closed
    assert calls[0]["stream"] is True
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert calls[0]["timeout"] == 60 and calls[0]["num_retries"] == 0
    assert _rows(conn)[0] == ("StreamResult", "test-model", 3, 2, 0, 0.2)


async def _record_sink(seen: list[tuple[str, bool]], text: str, reset: bool) -> None:
    seen.append((text, reset))


@pytest.mark.asyncio
async def test_one_repair_resets_stream_and_keeps_attempt_rows(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    stream_list = [
        FakeStream((chunk('{"text":""}'), chunk(None, usage=True))),
        FakeStream((chunk('{"text":"fixed"}'), chunk(None, usage=True))),
    ]
    streams = iter(stream_list)
    prompts: list[object] = []

    async def provider(**kwargs: object) -> object:
        prompts.append(kwargs["messages"])
        return next(streams)

    monkeypatch.setattr("litellm.supports_response_schema", lambda model: True)
    client = LlmClient(_settings(tmp_path), acompletion=provider)
    seen: list[tuple[str, bool]] = []
    result = await client.complete_prose(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="test-model",
        messages=({"role": "user", "content": "answer"},),
        output=StreamResult,
        sink=lambda text, reset: _record_sink(seen, text, reset),
    )
    assert result.text == "fixed"
    assert seen.count(("", True)) == 2
    assert len(_rows(conn)) == 2
    assert "Repair the output" in str(prompts[1])
    assert all(stream.closed for stream in stream_list)


@pytest.mark.parametrize("output", (NoText, IntText))
@pytest.mark.asyncio
async def test_stream_output_without_text_field_rejects_before_provider(
    output: type[BaseModel],
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
) -> None:
    conn, run_id, execution_id = llm_context
    calls = 0

    async def provider(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _response("{}")

    with pytest.raises(LlmCallError, match="prose output has no text field"):
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=output,
            sink=_empty_sink,
        )
    assert calls == 0


@pytest.mark.asyncio
async def test_prose_rejects_wrong_run_before_provider_or_sink(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, _, execution_id = llm_context
    calls = 0
    seen: list[tuple[str, bool]] = []

    async def provider(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _response('{"text":"unexpected"}')

    with pytest.raises(PersistenceError):
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id="other-run",
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=lambda text, reset: _record_sink(seen, text, reset),
        )
    assert calls == 0
    assert seen == []
    assert _rows(conn) == []


async def _empty_sink(text: str, reset: bool) -> None:
    del text, reset


@pytest.mark.asyncio
async def test_complete_remains_nonstreaming(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    request: dict[str, object] = {}

    async def provider(**kwargs: object) -> ModelResponse:
        request.update(kwargs)
        return _response('{"text":"ordinary"}')

    result = await LlmClient(_settings(tmp_path), acompletion=provider).complete(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="test-model",
        messages=(),
        output=StreamResult,
    )
    assert result.text == "ordinary"
    assert request["stream"] is False
    assert "stream_options" not in request


@pytest.mark.parametrize("text", ('quote " here', "backslash \\", "line\n", "é"))
def test_partial_text_decodes_only_complete_string_values(
    text: str,
) -> None:
    content = json.dumps({"text": text})[:-1]
    assert _partial_text(content) == text


@pytest.mark.parametrize(
    ("content", "expected"),
    (('{"text":"\\u00', ""), ('{"other":"value"', None), ('["text"]', None)),
)
def test_partial_text_waits_for_incomplete_or_non_text_values(
    content: str, expected: str | None
) -> None:
    assert _partial_text(content) == expected


@pytest.mark.asyncio
async def test_prefix_change_is_rejected_and_stream_closed(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    stream = FakeStream((chunk('{"text":"first'), chunk('","text":"second"}')))

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    with pytest.raises(LlmCallError, match="prose prefix changed"):
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    assert stream.closed


@pytest.mark.asyncio
async def test_provider_midstream_failure_is_transient_and_closed(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    stream = FakeStream(
        (chunk('{"text":"part'),),
        error=APIConnectionError(message="offline", llm_provider="fixture", model="test-model"),
    )

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    assert error.value.failure.error_class == "transient"
    assert stream.closed
    assert _rows(conn)[0][2:5] == (0, 0, 0)


@pytest.mark.asyncio
async def test_sink_persistence_error_propagates_and_closes(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    stream = FakeStream((chunk('{"text":"visible"}'), chunk(None, usage=True)))
    failure = PersistenceError("event append failed")

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    async def sink(text: str, reset: bool) -> None:
        if text:
            raise failure

    with pytest.raises(PersistenceError) as error:
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=sink,
        )
    assert error.value is failure
    assert stream.closed
    assert len(_rows(conn)) == 1
    assert _rows(conn)[0][0] == "StreamResult"
    assert _rows(conn)[0][2:5] == (0, 0, 0)


@pytest.mark.asyncio
async def test_sink_and_record_persistence_failures_keep_sink_error(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn, run_id, execution_id = llm_context
    stream = FakeStream((chunk('{"text":"visible"}'), chunk(None, usage=True)))
    failure = PersistenceError("event append failed")

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    async def sink(text: str, reset: bool) -> None:
        if text:
            raise failure

    conn.execute(
        "CREATE TRIGGER reject_llm_call BEFORE INSERT ON llm_calls "
        "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
    )
    with pytest.raises(PersistenceError) as error:
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=sink,
        )
    assert error.value is failure
    assert stream.closed
    assert len(_rows(conn)) == 0
    assert "Could not record failed prose request" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_closes_stream_and_releases_semaphore(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    entered, release = asyncio.Event(), asyncio.Event()
    blocked = FakeStream((chunk('{"text":"late"}'),), entered=entered, release=release)
    next_stream = FakeStream((chunk('{"text":"ready"}'), chunk(None, usage=True)))
    streams = iter((blocked, next_stream))

    async def provider(**kwargs: object) -> object:
        del kwargs
        return next(streams)

    client = LlmClient(_settings(tmp_path, limit=1), acompletion=provider)
    pending = asyncio.create_task(
        client.complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    )
    async with asyncio.timeout(2):
        await entered.wait()
    try:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    assert blocked.closed
    result = await client.complete_prose(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="test-model",
        messages=(),
        output=StreamResult,
        sink=_empty_sink,
    )
    assert result.text == "ready"
    assert next_stream.closed
    assert len(_rows(conn)) == 2
    assert _rows(conn)[0][2:5] == (0, 0, 0)


@pytest.mark.asyncio
async def test_prose_timeout_uses_request_timeout_and_is_transient(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import statlab.llm.client as client_module

    conn, run_id, execution_id = llm_context
    release = asyncio.Event()
    stream = FakeStream((chunk('{"text":"blocked"}'),), release=release)

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    monkeypatch.setattr(client_module, "_REQUEST_TIMEOUT", 0.01)
    with pytest.raises(LlmCallError) as error:
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    assert error.value.failure.error_class == "transient"
    assert stream.closed


@pytest.mark.asyncio
async def test_prose_semaphore_covers_provider_stream_lifetime(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    entered, release = asyncio.Event(), asyncio.Event()
    first = FakeStream(
        (chunk('{"text":"first'), chunk('"}'), chunk(None, usage=True)),
        entered=entered,
        release=release,
        block_before=1,
    )
    second = FakeStream((chunk('{"text":"second"}'), chunk(None, usage=True)))
    streams = iter((first, second))
    calls = 0

    async def provider(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return next(streams)

    client = LlmClient(_settings(tmp_path, limit=1), acompletion=provider)

    def start() -> asyncio.Task[StreamResult]:
        return asyncio.create_task(
            client.complete_prose(
                conn,
                run_id=run_id,
                node_execution_id=execution_id,
                model="test-model",
                messages=(),
                output=StreamResult,
                sink=_empty_sink,
            )
        )

    first_task = start()
    second_task: asyncio.Task[StreamResult] | None = None
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        second_task = start()
        await asyncio.sleep(0)
        assert calls == 1
        assert not second_task.done()
        release.set()
        results = await asyncio.gather(first_task, second_task)
        assert [result.text for result in results] == ["first", "second"]
        assert calls == 2
        assert len(_rows(conn)) == 2
        assert first.closed and second.closed
    finally:
        release.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task is not None),
            return_exceptions=True,
        )


@pytest.mark.asyncio
async def test_prose_timeout_covers_blocked_reset_sink(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import statlab.llm.client as client_module

    conn, run_id, execution_id = llm_context
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def provider(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _response(str(kwargs))

    async def sink(text: str, reset: bool) -> None:
        del text
        if reset:
            entered.set()
            await release.wait()

    monkeypatch.setattr(client_module, "_REQUEST_TIMEOUT", 0.01)
    task = asyncio.create_task(
        LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=sink,
        )
    )
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        with pytest.raises(LlmCallError) as error:
            await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert error.value.failure.error_class == "transient"
    assert calls == 0
    assert len(_rows(conn)) == 1
    assert _rows(conn)[0][2:5] == (0, 0, 0)


def _padded_json(length: int) -> str:
    value = '{"text":"x"}'
    return value + " " * (length - len(value))


@pytest.mark.asyncio
async def test_prose_length_boundary(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    bounded = FakeStream((chunk(_padded_json(131072)), chunk(None, usage=True)))
    oversized = FakeStream((chunk(_padded_json(131073)),))
    streams = iter((bounded, oversized))

    async def provider(**kwargs: object) -> object:
        del kwargs
        return next(streams)

    client = LlmClient(_settings(tmp_path), acompletion=provider)
    result = await client.complete_prose(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="test-model",
        messages=(),
        output=StreamResult,
        sink=_empty_sink,
    )
    assert result.text == "x"
    with pytest.raises(LlmCallError, match="prose response exceeds limit"):
        await client.complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    assert oversized.closed


@pytest.mark.asyncio
async def test_prose_oversize_rejected_before_full_text_emission(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    stream = FakeStream((chunk(_padded_json(131073)),))

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    seen: list[tuple[str, bool]] = []
    with pytest.raises(LlmCallError, match="prose response exceeds limit"):
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=lambda text, reset: _record_sink(seen, text, reset),
        )
    assert seen == [("", True)]
    assert stream.closed


@pytest.mark.asyncio
async def test_two_invalid_streams_raise_output_error_after_one_repair(
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    stream_list = [
        FakeStream((chunk('{"text":""}'), chunk(None, usage=True))),
        FakeStream((chunk('{"text":""}'), chunk(None, usage=True))),
    ]
    streams = iter(stream_list)
    prompts: list[object] = []

    async def provider(**kwargs: object) -> object:
        prompts.append(kwargs["messages"])
        return next(streams)

    monkeypatch.setattr("litellm.supports_response_schema", lambda model: True)
    seen: list[tuple[str, bool]] = []
    with pytest.raises(LlmOutputError):
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=lambda text, reset: _record_sink(seen, text, reset),
        )
    assert seen.count(("", True)) == 2
    assert len(prompts) == len(_rows(conn)) == 2
    assert "Repair the output" in str(prompts[1])
    assert all(stream.closed for stream in stream_list)


@pytest.mark.parametrize(
    ("initial_chunks", "expected_usage"),
    (
        ((), (0, 0)),
        ((None,), (3, 2)),
        (('{"text": invalid}', None), (3, 2)),
    ),
)
@pytest.mark.asyncio
async def test_empty_usage_only_and_malformed_streams_repair(
    initial_chunks: tuple[str | None, ...],
    expected_usage: tuple[int, int],
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    initial = tuple(
        chunk(content, usage=content is None) if content is None else chunk(content)
        for content in initial_chunks
    )
    first = FakeStream(initial)
    second = FakeStream((chunk('{"text":"fixed"}'), chunk(None, usage=True)))
    streams = iter((first, second))

    async def provider(**kwargs: object) -> object:
        del kwargs
        return next(streams)

    monkeypatch.setattr("litellm.supports_response_schema", lambda model: True)
    result = await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="test-model",
        messages=(),
        output=StreamResult,
        sink=_empty_sink,
    )
    rows = _rows(conn)
    assert result.text == "fixed"
    assert len(rows) == 2
    assert rows[0][2:4] == expected_usage
    assert rows[0][4] == 0
    assert all(stream.closed for stream in (first, second))


@pytest.mark.asyncio
async def test_nonstream_response_for_prose_is_malformed_without_repair(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    calls = 0

    async def provider(**kwargs: object) -> ModelResponse:
        nonlocal calls
        calls += 1
        return _response('{"text":"unexpected"}')

    with pytest.raises(LlmCallError, match="LLM returned a malformed stream") as error:
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    assert error.value.failure.error_class == "bug"
    assert calls == 1
    assert len(_rows(conn)) == 1
    assert _rows(conn)[0][2:5] == (0, 0, 0)


@pytest.mark.asyncio
async def test_reasoning_content_never_reaches_prose_sink(
    llm_context: tuple[sqlite3.Connection, str, str], tmp_path: Path
) -> None:
    conn, run_id, execution_id = llm_context
    reasoning = ModelResponseStream(
        choices=[StreamingChoices(index=0, delta=Delta(reasoning_content="SECRET_REASONING"))],
        model="test-model",
    )
    stream = FakeStream((reasoning, chunk('{"text":"visible"}'), chunk(None, usage=True)))

    async def provider(**kwargs: object) -> object:
        del kwargs
        return stream

    seen: list[tuple[str, bool]] = []
    result = await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
        conn,
        run_id=run_id,
        node_execution_id=execution_id,
        model="test-model",
        messages=(),
        output=StreamResult,
        sink=lambda text, reset: _record_sink(seen, text, reset),
    )
    assert result.text == "visible"
    assert "".join(text for text, reset in seen if not reset) == "visible"
    assert "SECRET_REASONING" not in str(seen)
    assert stream.closed


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("foreign_item", "LLM returned a malformed stream"),
        ("second_choice", "LLM returned a malformed stream"),
        ("non_text_fragment", "LLM returned a malformed stream"),
        ("malformed_usage", "LLM returned malformed usage metadata"),
        ("builder_mismatch", "LLM returned a malformed stream"),
    ),
)
@pytest.mark.asyncio
async def test_malformed_stream_items_fail_without_repair(
    case: str,
    message: str,
    llm_context: tuple[sqlite3.Connection, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, run_id, execution_id = llm_context
    chunks: tuple[ModelResponseStream, ...]
    if case == "foreign_item":
        chunks = (cast(ModelResponseStream, object()),)
    elif case == "second_choice":
        chunks = (
            ModelResponseStream(
                choices=[StreamingChoices(index=1, delta=Delta(content='{"text":"x"}'))],
                model="test-model",
            ),
        )
    elif case == "non_text_fragment":
        chunks = (
            ModelResponseStream(
                choices=[StreamingChoices(index=0, delta=Delta(content=["x"]))],
                model="test-model",
            ),
        )
    elif case == "malformed_usage":
        chunks = (ModelResponseStream(choices=[], model="test-model", usage="bad"),)
    else:
        chunks = (chunk('{"text":"x"}'), chunk(None, usage=True))

        def no_response(chunks: object, messages: object) -> None:
            del chunks, messages

        monkeypatch.setattr("statlab.llm.client.stream_chunk_builder", no_response)
    stream = FakeStream(chunks)
    calls = 0

    async def provider(**kwargs: object) -> object:
        nonlocal calls
        del kwargs
        calls += 1
        return stream

    with pytest.raises(LlmCallError, match=message) as error:
        await LlmClient(_settings(tmp_path), acompletion=provider).complete_prose(
            conn,
            run_id=run_id,
            node_execution_id=execution_id,
            model="test-model",
            messages=(),
            output=StreamResult,
            sink=_empty_sink,
        )
    assert error.value.failure.error_class == "bug"
    assert calls == 1
    assert stream.closed
    assert len(_rows(conn)) == 1
