"""One-process durable event order, replay and lossless live handover."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError
from statlab_contracts.layout import RunPaths

from statlab.errors import PersistenceError
from statlab.persistence.ids import utc_now


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seq: int = Field(ge=1)
    run_id: str
    ts: str
    type: str
    payload: dict[str, JsonValue]


class EventBus:
    def __init__(self, paths: RunPaths) -> None:
        self.paths = paths
        self._lock = asyncio.Lock()
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._failed = False
        self._events = self._read()

    def _read(self) -> list[Event]:
        try:
            data = self.paths.events_jsonl.read_bytes()
            end = data.rfind(b"\n") + 1
            if end != len(data):
                with self.paths.events_jsonl.open("r+b") as stream:
                    stream.truncate(end)
            events = [
                Event.model_validate_json(line, strict=True) for line in data[:end].splitlines()
            ]
        except FileNotFoundError:
            return []
        except (OSError, ValidationError) as exc:
            raise PersistenceError("cannot read run events", cause=exc) from exc
        for seq, event in enumerate(events, 1):
            if event.seq != seq or event.run_id != self.paths.run_id:
                raise PersistenceError("run events have invalid identity or sequence")
        return events

    async def append(self, event_type: str, payload: dict[str, JsonValue]) -> Event:
        async with self._lock:
            if self._failed:
                raise PersistenceError("event append previously failed; reopen the bus")
            event = Event(
                seq=len(self._events) + 1,
                run_id=self.paths.run_id,
                ts=utc_now(),
                type=event_type,
                payload=payload,
            )
            try:
                with self.paths.events_jsonl.open("ab") as stream:
                    stream.write((event.model_dump_json() + "\n").encode("utf-8"))
                    stream.flush()
            except OSError as exc:
                self._failed = True
                raise PersistenceError("cannot append run event", cause=exc) from exc
            self._events.append(event)
            for queue in self._subscribers:
                queue.put_nowait(event)
            return event

    async def replay(self, since: int = 0) -> tuple[Event, ...]:
        async with self._lock:
            return tuple(event for event in self._events if event.seq > since)

    async def subscribe(self, since: int = 0) -> AsyncGenerator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        async with self._lock:
            for event in self._events:
                if event.seq > since:
                    queue.put_nowait(event)
            self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)
