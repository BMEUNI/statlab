"""Record/replay of LLM answers at the LlmClient acompletion seam (chunk 89b)."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import litellm
from litellm import ModelResponse
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage
from openai.types.completion_usage import PromptTokensDetails

_ID: Final = re.compile(r"\b[a-z]{1,3}_[0-9a-f]{16}\b")
_LOG: Final = logging.getLogger(__name__)
JUDGE_SYSTEM: Final = "Evaluation judge. Answer with one JSON object only."


def _state(user: str) -> tuple[str | None, str | None]:
    try:
        obj = json.loads(user.split("\n\n", 1)[0])
    except ValueError:
        return None, None
    if not isinstance(obj, dict):
        return None, None
    node = obj.get("node")
    candidate = obj.get("candidate")
    public_id = candidate.get("public_id") if isinstance(candidate, dict) else None
    return (
        node if isinstance(node, str) else None,
        public_id if isinstance(public_id, str) else None,
    )


def slot_of(messages: list[dict[str, str]]) -> str:
    system = messages[0]["content"]
    marker = "\nSchema: "
    index = system.rfind(marker)
    if index == -1:
        raise RuntimeError("cassette: request has no schema")
    title = str(json.loads(system[index + len(marker) :])["title"])
    if system.startswith(JUDGE_SYSTEM):
        return f"{title}__judge__{prompt_sha256(messages)[:16]}"
    user = next((m["content"] for m in messages if m["role"] == "user"), "")
    node, candidate = _state(user)
    return f"{title}__{node or '-'}__{candidate or '-'}"


def prompt_sha256(messages: list[dict[str, str]]) -> str:
    text = json.dumps(messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(_ID.sub("<id>", text).encode("utf-8")).hexdigest()


def _reduce(chunk: ModelResponseStream) -> dict[str, object]:
    content = chunk.choices[0].delta.content if chunk.choices else None
    usage = getattr(chunk, "usage", None)
    stored: dict[str, int] | None = None
    if isinstance(usage, Usage):
        details = usage.prompt_tokens_details
        cached = details.cached_tokens if details is not None else 0
        stored = {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cached_tokens": cached if isinstance(cached, int) else 0,
        }
    return {"content": content, "usage": stored}


def _int(value: object) -> int:
    if not isinstance(value, int):
        raise RuntimeError("cassette: malformed recording")
    return value


class _Replayed:
    def __init__(self, chunks: list[dict[str, object]], model: str) -> None:
        self._chunks = chunks
        self._model = model

    async def __aiter__(self) -> AsyncIterator[ModelResponseStream]:
        for item in self._chunks:
            content = item.get("content")
            usage = item.get("usage")
            if content is not None:
                if not isinstance(content, str):
                    raise RuntimeError("cassette: malformed recording")
                yield ModelResponseStream(
                    choices=[StreamingChoices(index=0, delta=Delta(content=content))],
                    model=self._model,
                )
            if usage is not None:
                if not isinstance(usage, dict):
                    raise RuntimeError("cassette: malformed recording")
                yield ModelResponseStream(
                    choices=[],
                    model=self._model,
                    usage=Usage(
                        prompt_tokens=_int(usage.get("prompt_tokens")),
                        completion_tokens=_int(usage.get("completion_tokens")),
                        total_tokens=_int(usage.get("total_tokens")),
                        prompt_tokens_details=PromptTokensDetails(
                            cached_tokens=_int(usage.get("cached_tokens"))
                        ),
                    ),
                )

    async def aclose(self) -> None:
        return None


@dataclass
class CassetteProvider:
    mode: Literal["record", "replay"]
    root: Path
    scenario_id: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    drift: list[str] = field(default_factory=list)
    record_missing: bool = False
    acompletion: Callable[..., Awaitable[object]] = litellm.acompletion

    def start(self, scenario_id: str) -> None:
        self.scenario_id = scenario_id
        self.counts.clear()
        self.drift.clear()

    async def __call__(self, **kwargs: object) -> object:
        scenario_id = self.scenario_id
        if scenario_id is None:
            raise RuntimeError("cassette: no active scenario")
        raw_messages = kwargs["messages"]
        if not isinstance(raw_messages, list):
            raise RuntimeError("cassette: malformed messages")
        messages: list[dict[str, str]] = []
        for item in raw_messages:
            if not (
                isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("content"), str)
            ):
                raise RuntimeError("cassette: malformed messages")
            messages.append({"role": item["role"], "content": item["content"]})
        slot = slot_of(messages)
        n = self.counts.get(slot, 0)
        path = self.root / scenario_id / f"{slot}__{n}.json"
        if self.mode == "replay" and (path.is_file() or not self.record_missing):
            if not path.is_file():
                message = f"cassette miss: {scenario_id} {slot} #{n}; re-record"
                _LOG.error(message)
                raise RuntimeError(message)
            stored = json.loads(path.read_text(encoding="utf-8"))
            if stored["prompt_sha256"] != prompt_sha256(messages):
                self.drift.append(f"{slot} #{n}")
            self.counts[slot] = n + 1
            if stored["stream"]:
                return _Replayed(stored["chunks"], str(kwargs["model"]))
            return ModelResponse(**stored["response"])
        raw = await self.acompletion(**kwargs)
        body: dict[str, object]
        answer: object
        if kwargs.get("stream") is True:
            if not isinstance(raw, AsyncIterable):
                raise RuntimeError("cassette: unexpected response type")
            dumped: list[dict[str, object]] = []
            async for chunk in raw:
                dumped.append(_reduce(chunk))
            aclose = getattr(raw, "aclose", None)
            if callable(aclose):
                await aclose()
            body = {"stream": True, "chunks": dumped}
            answer = _Replayed(dumped, str(kwargs["model"]))
        else:
            if not isinstance(raw, ModelResponse):
                raise RuntimeError("cassette: unexpected response type")
            body = {"stream": False, "response": raw.model_dump()}
            answer = raw
        body.update(
            slot=slot,
            n=n,
            model=kwargs["model"],
            prompt_sha256=prompt_sha256(messages),
            messages=messages,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body, indent=1, default=str), encoding="utf-8")
        self.counts[slot] = n + 1
        return answer
