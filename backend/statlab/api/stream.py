"""Durable run event streams over server-sent events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING

import anyio
from fastapi import APIRouter, Header, HTTPException, Query
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from statlab.events import Event, EventBus

if TYPE_CHECKING:
    from statlab.api.runs import RunRegistry

_HEARTBEAT_SECONDS = 15.0


def parse_cursor(since: int, last_event_id: str | None) -> int:
    if last_event_id is None:
        return since
    if not last_event_id or not last_event_id.isascii() or not last_event_id.isdecimal():
        raise HTTPException(422, "invalid request")
    try:
        return max(since, int(last_event_id))
    except ValueError:
        raise HTTPException(422, "invalid request") from None


def event_frame(event: Event) -> str:
    return f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"


async def stream_events(
    bus: EventBus, *, since: int, closing: asyncio.Event
) -> AsyncGenerator[str]:
    if closing.is_set():
        return
    subscription = bus.subscribe(since)
    next_event: asyncio.Task[Event] = asyncio.create_task(anext(subscription))
    close_wait: asyncio.Task[bool] = asyncio.create_task(closing.wait())
    try:
        while True:
            done, _ = await asyncio.wait(
                (next_event, close_wait),
                timeout=_HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if closing.is_set() or close_wait in done:
                break
            if next_event not in done:
                yield ": heartbeat\n\n"
                continue
            event = next_event.result()
            yield event_frame(event)
            next_event = asyncio.create_task(anext(subscription))
    finally:
        remaining = (next_event, close_wait)
        for task in remaining:
            if not task.done():
                task.cancel()
        await asyncio.gather(*remaining, return_exceptions=True)
        await subscription.aclose()


class SseResponse(StreamingResponse):
    def __init__(self, stream: AsyncGenerator[str]) -> None:
        self._stream = stream
        super().__init__(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self._stream.aclose()


def stream_router(registry: Callable[[], RunRegistry]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/runs/{run_id}/events")
    async def events_endpoint(
        run_id: str,
        since: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        owner = registry()
        cursor = parse_cursor(since, last_event_id)
        binding = owner.run(run_id)
        return SseResponse(
            stream_events(binding.loop.events, since=cursor, closing=owner.streams_closing)
        )

    return router
