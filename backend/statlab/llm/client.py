"""Typed async LLM requests with capability fallbacks and per-attempt accounting."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Protocol

from pydantic import BaseModel, JsonValue, ValidationError

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm import ModelResponse
from litellm.exceptions import (
    APIConnectionError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from statlab_contracts.envelope import ErrorClass, Failure

from statlab.config import Settings
from statlab.errors import LlmCallError, LlmOutputError, PersistenceError
from statlab.persistence.repository import record_llm_call

_LOGGER = logging.getLogger(__name__)
_REQUEST_TIMEOUT = 60
_FLASH_MODELS = frozenset(
    {
        "deepseek/deepseek-flash",
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-flash-vision-exp",
    }
)
_TRANSIENT_ERRORS = (
    RateLimitError,
    Timeout,
    APIConnectionError,
    ServiceUnavailableError,
)


class CallRecovery(Protocol):
    def __call__[T](self, call: Callable[[], Awaitable[T]]) -> Awaitable[T]: ...


class LlmClient:
    """One configured request semaphore shared by all calls made through this client."""

    def __init__(
        self,
        settings: Settings,
        *,
        acompletion: Callable[..., Awaitable[object]] | None = None,
    ) -> None:
        litellm.suppress_debug_info = True
        self._acompletion = acompletion or litellm.acompletion
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_llm)

    async def complete[T: BaseModel](
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        node_execution_id: str,
        model: str,
        messages: Sequence[dict[str, str]],
        output: type[T],
        reasoning: bool = False,
        example: dict[str, JsonValue] | None = None,
        recovery: CallRecovery | None = None,
    ) -> T:
        """Return a validated model, repairing one invalid response exactly once."""
        try:
            execution = conn.execute(
                "SELECT run_id FROM node_executions WHERE id = ?", (node_execution_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError("cannot verify LLM call execution", cause=exc) from exc
        if execution is None or execution["run_id"] != run_id:
            raise PersistenceError("LLM call execution does not belong to run")

        tier = _capability_tier(model)
        base_messages = [message.copy() for message in messages]
        if tier != "schema":
            instruction = _schema_instruction(output, example, json_mode=tier == "json")
            base_messages = _with_instruction(base_messages, instruction)

        prior_content = ""
        validation_errors: dict[str, str] = {}
        repaired = False
        while True:
            request_messages = (
                _repair_messages(base_messages, prior_content, validation_errors)
                if repaired
                else base_messages
            )

            async def request(
                sent_messages: list[dict[str, str]] = request_messages,
            ) -> ModelResponse:
                return await self._request(
                    conn,
                    run_id=run_id,
                    node_execution_id=node_execution_id,
                    model=model,
                    output=output,
                    messages=sent_messages,
                    tier=tier,
                    reasoning=reasoning,
                )

            response = await request() if recovery is None else await recovery(request)
            prior_content = _content(response)
            try:
                if not prior_content:
                    raise ValueError("response content is empty")
                return output.model_validate_json(prior_content)
            except (ValidationError, ValueError) as exc:
                validation_errors = _validation_errors(exc)
                if repaired:
                    raise LlmOutputError(
                        Failure(
                            error_class="tool_param",
                            message="LLM output remained invalid after one repair",
                            field_errors=validation_errors,
                        ),
                        cause=exc,
                    ) from exc
                repaired = True
            except Exception as exc:
                raise LlmCallError(
                    Failure(error_class="bug", message="LLM output validation failed"),
                    cause=exc,
                ) from exc

    async def _request(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        node_execution_id: str,
        model: str,
        output: type[BaseModel],
        messages: list[dict[str, str]],
        tier: str,
        reasoning: bool,
    ) -> ModelResponse:
        kwargs: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "timeout": _REQUEST_TIMEOUT,
            "num_retries": 0,
        }
        if tier == "schema":
            kwargs["response_format"] = output
        elif tier == "json":
            kwargs["response_format"] = {"type": "json_object"}
        if model.lower().startswith("deepseek/"):
            kwargs["thinking"] = {"type": "enabled" if reasoning else "disabled"}
            if reasoning:
                kwargs["reasoning_effort"] = "high"

        async with self._semaphore:
            started = monotonic()
            try:
                raw_response = await self._acompletion(**kwargs)
            except asyncio.CancelledError:
                duration = _duration(started)
                try:
                    _record(
                        conn,
                        run_id=run_id,
                        node_execution_id=node_execution_id,
                        signature=output.__name__,
                        model=model,
                        duration_ms=duration,
                    )
                except PersistenceError:
                    _LOGGER.warning("Could not record cancelled LLM request")
                raise
            except Exception as exc:
                _record(
                    conn,
                    run_id=run_id,
                    node_execution_id=node_execution_id,
                    signature=output.__name__,
                    model=model,
                    duration_ms=_duration(started),
                )
                error_class: ErrorClass = (
                    "transient" if isinstance(exc, _TRANSIENT_ERRORS) else "bug"
                )
                label = (
                    "provider request failed" if error_class == "transient" else "LLM client failed"
                )
                raise LlmCallError(
                    Failure(error_class=error_class, message=label), cause=exc
                ) from exc

        if not isinstance(raw_response, ModelResponse):
            _record(
                conn,
                run_id=run_id,
                node_execution_id=node_execution_id,
                signature=output.__name__,
                model=model,
                duration_ms=_duration(started),
            )
            raise LlmCallError(
                Failure(error_class="bug", message="LLM returned a malformed response")
            )
        try:
            _record_response(
                conn,
                raw_response,
                run_id=run_id,
                node_execution_id=node_execution_id,
                signature=output.__name__,
                model=model,
                duration_ms=_duration(started),
            )
        except LlmCallError:
            _record(
                conn,
                run_id=run_id,
                node_execution_id=node_execution_id,
                signature=output.__name__,
                model=model,
                duration_ms=_duration(started),
            )
            raise
        except PersistenceError:
            raise
        except Exception as exc:
            _record(
                conn,
                run_id=run_id,
                node_execution_id=node_execution_id,
                signature=output.__name__,
                model=model,
                duration_ms=_duration(started),
            )
            raise LlmCallError(
                Failure(error_class="bug", message="LLM returned malformed usage metadata"),
                cause=exc,
            ) from exc
        return raw_response


def _capability_tier(model: str) -> str:
    lowered = model.lower()
    if lowered.startswith("deepseek/"):
        return "json"
    if lowered.startswith("ollama/"):
        return "prompt"
    try:
        if litellm.supports_response_schema(model):
            return "schema"
        params = litellm.get_supported_openai_params(model=model, custom_llm_provider=None)
        if params is None:
            _LOGGER.warning("LLM output capability is unknown; using prompt mode")
            return "prompt"
        return "json" if "response_format" in params else "prompt"
    except Exception as exc:
        _LOGGER.warning(
            "LLM output capability lookup failed; using prompt mode (%s)", type(exc).__name__
        )
        return "prompt"


def _schema_instruction(
    output: type[BaseModel], example: dict[str, JsonValue] | None, *, json_mode: bool
) -> str:
    try:
        schema = output.model_json_schema()
        schema_json = json.dumps(schema, separators=(",", ":"), allow_nan=False)
        prefix = "Return valid json matching this schema. " if json_mode else "Match this schema. "
        if not json_mode:
            return f"{prefix}{schema_json}"
        selected_example = example if example is not None else _model_example(schema)
        if selected_example is None:
            selected_example = {}
            example_json = json.dumps(selected_example, separators=(",", ":"), allow_nan=False)
            return (
                f"{prefix}Example (syntax only; schema defines required fields): "
                f"{example_json}\nSchema: {schema_json}"
            )
        example_json = json.dumps(selected_example, separators=(",", ":"), allow_nan=False)
        return f"{prefix}Example: {example_json}\nSchema: {schema_json}"
    except Exception as exc:
        raise LlmCallError(
            Failure(error_class="bug", message="LLM output schema could not be serialized"),
            cause=exc,
        ) from exc


def _model_example(schema: dict[str, object]) -> dict[str, JsonValue] | None:
    examples = schema.get("examples")
    if isinstance(examples, list) and examples and isinstance(examples[0], dict):
        return examples[0]
    return None


def _with_instruction(messages: list[dict[str, str]], instruction: str) -> list[dict[str, str]]:
    result = [message.copy() for message in messages]
    if result and result[0].get("role") == "system":
        result[0]["content"] = f"{result[0].get('content', '')}\n\n{instruction}"
    else:
        result.insert(0, {"role": "system", "content": instruction})
    return result


def _repair_messages(
    messages: list[dict[str, str]], prior_content: str, errors: dict[str, str]
) -> list[dict[str, str]]:
    result = [message.copy() for message in messages]
    result.append({"role": "assistant", "content": prior_content})
    result.append(
        {
            "role": "user",
            "content": "Repair the output. Validation errors: "
            + json.dumps(errors, sort_keys=True),
        }
    )
    return result


def _validation_errors(exc: ValidationError | ValueError) -> dict[str, str]:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_input=False, include_context=False)
        return {
            ".".join(str(part) for part in error["loc"]) or "output": str(error["msg"])
            for error in errors
        }
    return {"output": "response must contain non-empty valid JSON"}


def _content(response: ModelResponse) -> str:
    if not response.choices:
        raise LlmCallError(Failure(error_class="bug", message="LLM returned no choices"))
    return response.choices[0].message.content or ""


def _duration(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))


def _record(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    node_execution_id: str,
    signature: str,
    model: str,
    duration_ms: int,
) -> None:
    record_llm_call(
        conn,
        run_id=run_id,
        node_execution_id=node_execution_id,
        signature=signature,
        model=model,
        prompt_tokens=0,
        completion_tokens=0,
        cached_tokens=0,
        cost_usd=0.0,
        duration_ms=duration_ms,
    )


def _record_response(
    conn: sqlite3.Connection,
    response: ModelResponse,
    *,
    run_id: str,
    node_execution_id: str,
    signature: str,
    model: str,
    duration_ms: int,
) -> None:
    try:
        usage = response.usage
    except AttributeError:
        usage = None
    try:
        prompt = usage.prompt_tokens if usage is not None else None
        completion = usage.completion_tokens if usage is not None else None
        details = usage.prompt_tokens_details if usage is not None else None
        cached = details.cached_tokens if details is not None else None
    except AttributeError as exc:
        raise LlmCallError(
            Failure(error_class="bug", message="LLM returned malformed usage metadata"),
            cause=exc,
        ) from exc
    prompt_tokens = _token_count(prompt)
    completion_tokens = _token_count(completion)
    cached_tokens = _token_count(cached)
    if cached_tokens > prompt_tokens:
        raise LlmCallError(Failure(error_class="bug", message="LLM returned invalid token usage"))
    cost = _cost(response, model, prompt_tokens, completion_tokens, cached_tokens)
    record_llm_call(
        conn,
        run_id=run_id,
        node_execution_id=node_execution_id,
        signature=signature,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost,
        duration_ms=duration_ms,
    )


def _token_count(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LlmCallError(Failure(error_class="bug", message="LLM returned invalid token usage"))
    return value


def _cost(response: ModelResponse, model: str, prompt: int, completion: int, cached: int) -> float:
    if model.lower() in _FLASH_MODELS:
        # ponytail: peak rates are an upper estimate; add time-aware pricing if billing needs it.
        amount = ((prompt - cached) * 0.30 + cached * 0.006 + completion * 1.20) / 1_000_000
    else:
        try:
            raw_cost = litellm.completion_cost(completion_response=response, model=model)
        except Exception as exc:
            _LOGGER.warning("LLM cost lookup failed; recording zero (%s)", type(exc).__name__)
            return 0.0
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
            _LOGGER.warning("LLM cost lookup returned no numeric price; recording zero")
            return 0.0
        amount = float(raw_cost)
    if not math.isfinite(amount) or amount < 0:
        _LOGGER.warning("LLM cost lookup returned invalid price; recording zero")
        return 0.0
    return amount
