"""Unit tests for the integration record/replay cassette provider (chunk 89b)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path

import pytest
from litellm import ModelResponse
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage
from openai.types.completion_usage import PromptTokensDetails

from integration.cassette import CassetteProvider, prompt_sha256, slot_of


def system(title: str) -> str:
    return f"rules\nSchema: {json.dumps({'title': title})}"


def decide_user(node: str, candidate: str | None) -> str:
    state = json.dumps(
        {"candidate": {"public_id": candidate} if candidate else None, "node": node},
        sort_keys=True,
        separators=(",", ":"),
    )
    return state + "\n\n[]\n\nquestion"


def decide_messages(node: str = "fit", candidate: str | None = "c_01") -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system("T")},
        {"role": "user", "content": decide_user(node, candidate)},
    ]


def usage() -> Usage:
    return Usage(
        prompt_tokens=3,
        completion_tokens=2,
        total_tokens=5,
        prompt_tokens_details=PromptTokensDetails(cached_tokens=1),
    )


def answer() -> ModelResponse:
    return ModelResponse(
        model="m",
        choices=[
            {
                "message": {"role": "assistant", "content": '{"a": 1}'},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    )


class Counting:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, **kwargs: object) -> ModelResponse:
        self.calls += 1
        return answer()


class FakeStream:
    def __init__(self, items: list[ModelResponseStream]) -> None:
        self._items = items
        self.closed = False

    def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[ModelResponseStream]:
        for item in self._items:
            yield item

    async def aclose(self) -> None:
        self.closed = True


def stream_chunks() -> list[ModelResponseStream]:
    return [
        ModelResponseStream(
            choices=[StreamingChoices(index=0, delta=Delta(content="ab"))],
            model="m",
        ),
        ModelResponseStream(
            choices=[StreamingChoices(index=0, delta=Delta(content="c"))],
            model="m",
        ),
        ModelResponseStream(choices=[], model="m", usage=usage()),
    ]


class StreamingFake:
    def __init__(self) -> None:
        self.stream = FakeStream(stream_chunks())

    async def __call__(self, **kwargs: object) -> FakeStream:
        return self.stream


async def no_call(**kwargs: object) -> ModelResponse:
    raise AssertionError("replay must not call the network")


def test_slot_of_decide_and_other_calls() -> None:
    assert slot_of(decide_messages("fit", "c_02")) == "T__fit__c_02"
    assert slot_of(decide_messages("intake", None)) == "T__intake__-"
    plain = [{"role": "system", "content": system("T")}, {"role": "user", "content": "hello"}]
    assert slot_of(plain) == "T__-__-"
    with pytest.raises(RuntimeError, match="no schema"):
        slot_of([{"role": "user", "content": "x"}])


def test_prompt_hash_ignores_random_ids() -> None:
    first = [{"role": "user", "content": "item r_0123456789abcdef done"}]
    second = [{"role": "user", "content": "item r_fedcba9876543210 done"}]
    assert prompt_sha256(first) == prompt_sha256(second)
    other = [{"role": "user", "content": "item r_0123456789abcdef gone"}]
    assert prompt_sha256(first) != prompt_sha256(other)


@pytest.mark.asyncio
async def test_record_then_replay_round_trip(tmp_path: Path) -> None:
    fake = Counting()
    record = CassetteProvider(mode="record", root=tmp_path, acompletion=fake)
    record.start("S01")
    await record(model="m", messages=decide_messages(), stream=False, api_key="sk-secret")
    await record(model="m", messages=decide_messages(), stream=False, api_key="sk-secret")
    assert fake.calls == 2
    scenario_dir = tmp_path / "S01"
    assert (scenario_dir / "T__fit__c_01__0.json").is_file()
    assert (scenario_dir / "T__fit__c_01__1.json").is_file()
    for stored in scenario_dir.glob("*.json"):
        assert "sk-secret" not in stored.read_text(encoding="utf-8")

    replay = CassetteProvider(mode="replay", root=tmp_path, acompletion=no_call)
    replay.start("S01")
    first: object = await replay(model="m", messages=decide_messages(), stream=False)
    second: object = await replay(model="m", messages=decide_messages(), stream=False)
    for result in (first, second):
        assert isinstance(result, ModelResponse)
        assert result.choices[0].message.content == '{"a": 1}'
        assert result.usage.prompt_tokens == 3
    assert replay.drift == []


@pytest.mark.asyncio
async def test_replay_stream_and_drift(tmp_path: Path) -> None:
    fake = StreamingFake()
    record = CassetteProvider(mode="record", root=tmp_path, acompletion=fake)
    record.start("S01")
    await record(model="m", messages=decide_messages(), stream=True)
    assert fake.stream.closed

    replay = CassetteProvider(mode="replay", root=tmp_path, acompletion=no_call)
    replay.start("S01")
    changed = decide_messages()
    changed[1]["content"] = changed[1]["content"] + " changed"
    result: object = await replay(model="m", messages=changed, stream=True)
    assert hasattr(result, "aclose")
    assert isinstance(result, AsyncIterable)
    items: list[ModelResponseStream] = [item async for item in result]
    assert len(items) == 3
    assert all(isinstance(item, ModelResponseStream) for item in items)
    assert [item.choices[0].delta.content for item in items[:2]] == ["ab", "c"]
    assert items[2].usage is not None
    assert items[2].usage.prompt_tokens == 3
    assert replay.drift == ["T__fit__c_01 #0"]


@pytest.mark.asyncio
async def test_replay_miss_and_no_scenario(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    replay = CassetteProvider(mode="replay", root=tmp_path, acompletion=no_call)
    replay.start("S01")
    with pytest.raises(RuntimeError, match="cassette miss: S01 T__fit__c_01 #0"):
        await replay(model="m", messages=decide_messages(), stream=False)
    assert any(
        record.levelno == logging.ERROR
        and "cassette miss: S01 T__fit__c_01 #0" in record.getMessage()
        for record in caplog.records
    )
    unstarted = CassetteProvider(mode="replay", root=tmp_path, acompletion=no_call)
    with pytest.raises(RuntimeError, match="no active scenario"):
        await unstarted(model="m", messages=decide_messages(), stream=False)


@pytest.mark.asyncio
async def test_record_error_does_not_advance(tmp_path: Path) -> None:
    state = {"calls": 0}

    async def flaky(**kwargs: object) -> ModelResponse:
        state["calls"] += 1
        if state["calls"] == 1:
            raise ConnectionError("network down")
        return answer()

    record = CassetteProvider(mode="record", root=tmp_path, acompletion=flaky)
    record.start("S01")
    with pytest.raises(ConnectionError):
        await record(model="m", messages=decide_messages(), stream=False)
    await record(model="m", messages=decide_messages(), stream=False)
    assert (tmp_path / "S01" / "T__fit__c_01__0.json").is_file()
    assert record.counts == {"T__fit__c_01": 1}
