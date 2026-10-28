"""Unit tests for the DeepEval judge over the integration LLM seam (chunk 89c)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from litellm import ModelResponse
from pydantic import BaseModel

from integration.cassette import JUDGE_SYSTEM
from integration.judge import StatlabJudge, offline_acompletion


class Verdict(BaseModel):
    verdict: str


class Fixed:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.last: dict[str, object] = {}

    async def __call__(self, **kwargs: object) -> object:
        self.last = kwargs
        return self.answer


def verdict_response() -> ModelResponse:
    return ModelResponse(
        model="m",
        choices=[
            {
                "message": {"role": "assistant", "content": '{"verdict": "yes"}'},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    )


@pytest.mark.asyncio
async def test_a_generate_sends_schema_and_counts() -> None:
    fake = Fixed(verdict_response())
    judge = StatlabJudge("m", fake)
    result = await judge.a_generate("p", schema=Verdict)
    assert result == '{"verdict": "yes"}'
    assert fake.last["model"] == "m"
    assert fake.last["response_format"] == {"type": "json_object"}
    messages = fake.last["messages"]
    assert isinstance(messages, list)
    system = messages[0]["content"]
    assert isinstance(system, str)
    assert JUDGE_SYSTEM in system
    assert "Verdict" in system
    assert judge.calls == 1
    assert judge.prompt_tokens == 3
    assert judge.completion_tokens == 2


def test_generate_is_async_only() -> None:
    judge = StatlabJudge("m", Fixed(verdict_response()))
    with pytest.raises(RuntimeError):
        judge.generate("p")


@pytest.mark.asyncio
async def test_non_text_prompt_raises() -> None:
    judge = StatlabJudge("m", Fixed(verdict_response()))
    with pytest.raises(TypeError):
        await judge.a_generate(3)


@pytest.mark.asyncio
async def test_offline_acompletion_raises() -> None:
    with pytest.raises(RuntimeError, match="judge unavailable offline"):
        await offline_acompletion()


@pytest.mark.asyncio
async def test_non_model_response_raises() -> None:
    fake: Callable[..., Awaitable[object]] = Fixed({"verdict": "yes"})
    judge = StatlabJudge("m", fake)
    with pytest.raises(RuntimeError, match="judge: unexpected response"):
        await judge.a_generate("p")
