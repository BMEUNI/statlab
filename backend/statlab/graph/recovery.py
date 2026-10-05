"""Bounded recovery of one foreign call, never of a partially executed node."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from statlab_contracts.envelope import ErrorClass, retry_budget

from statlab.errors import EngineError, LlmCallError, LlmOutputError, StatlabError, ToolCallError

type Sleep = Callable[[float], Awaitable[None]]


class RecoveryError(EngineError):
    """Keep the original exception internal, including when formatted for a user."""

    def __str__(self) -> str:
        return self.message


def is_retryable(error_class: ErrorClass) -> bool:
    """Only transient failures are retried here; MCP owns parameter repair."""
    return error_class == "transient"


async def recover_call[T](
    call: Callable[[], Awaitable[T]],
    *,
    sleep: Sleep = asyncio.sleep,
    can_retry: Callable[[], bool] = lambda: True,
) -> T:
    attempt = 1
    while True:
        try:
            return await call()
        except LlmOutputError:
            # Its inner repair has already consumed the output-repair budget.
            raise
        except (ToolCallError, LlmCallError) as exc:
            if (
                not is_retryable(exc.error_class)
                or attempt >= retry_budget(exc.error_class)
                or not can_retry()
            ):
                raise
            await sleep(float(2 ** (attempt - 1)))
            attempt += 1
        except StatlabError:
            raise
        except Exception as exc:
            raise RecoveryError("Foreign call failed", cause=exc) from exc
