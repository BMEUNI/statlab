"""DeepEval judge over the integration LLM seam (chunk 89c)."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable

from litellm import ModelResponse
from litellm.types.utils import Choices, Usage
from pydantic import BaseModel

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from deepeval.models.base_model import DeepEvalBaseLLM

from integration.cassette import JUDGE_SYSTEM


class StatlabJudge(DeepEvalBaseLLM):
    def __init__(self, model: str, acompletion: Callable[..., Awaitable[object]]) -> None:
        self._acompletion = acompletion
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        super().__init__(model)

    def load_model(self, *args: object, **kwargs: object) -> StatlabJudge:
        return self

    def get_model_name(self, *args: object, **kwargs: object) -> str:
        return str(self.name)

    def generate(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("StatlabJudge is async only")

    async def a_generate(self, *args: object, **kwargs: object) -> str:
        if not args or not isinstance(args[0], str):
            raise TypeError("judge prompt must be text")
        prompt = args[0]
        schema = kwargs.get("schema")
        schema_json = json.dumps(
            schema.model_json_schema()
            if isinstance(schema, type) and issubclass(schema, BaseModel)
            else {"title": "Text"},
            sort_keys=True,
        )
        messages = [
            {"role": "system", "content": f"{JUDGE_SYSTEM}\nSchema: {schema_json}"},
            {"role": "user", "content": prompt},
        ]
        raw = await self._acompletion(
            model=self.name,
            messages=messages,
            response_format={"type": "json_object"},
            timeout=120,
            num_retries=2,
        )
        if not isinstance(raw, ModelResponse) or not isinstance(raw.choices[0], Choices):
            raise RuntimeError("judge: unexpected response")
        if isinstance(raw.usage, Usage):
            self.prompt_tokens += raw.usage.prompt_tokens
            self.completion_tokens += raw.usage.completion_tokens
        self.calls += 1
        return raw.choices[0].message.content or ""


async def offline_acompletion(**kwargs: object) -> object:
    raise RuntimeError("judge unavailable offline")
