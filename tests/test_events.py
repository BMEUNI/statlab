"""Durable event order and replay/live subscription boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from statlab.errors import PersistenceError
from statlab.events import Event, EventBus
from statlab_contracts.layout import RunPaths


async def next_event(stream: AsyncIterator[Event]) -> Event:
    return await anext(stream)


@pytest.mark.asyncio
async def test_concurrent_order_reopen_exclusive_replay(run_paths: RunPaths) -> None:
    bus = EventBus(run_paths)
    events = await asyncio.gather(*(bus.append("tick", {"n": n}) for n in range(20)))
    assert [event.seq for event in events] == list(range(1, 21))
    reopened = EventBus(run_paths)
    assert await reopened.replay(19) == (events[-1],)
    assert await reopened.replay(20) == ()
    assert await reopened.replay(100) == ()
    assert (await reopened.append("tick", {})).seq == 21


@pytest.mark.asyncio
async def test_handover_and_cancellation_release(run_paths: RunPaths) -> None:
    bus = EventBus(run_paths)
    first = await bus.append("first", {})
    stream = bus.subscribe()
    assert await anext(stream) == first
    second = await bus.append("second", {})
    assert await anext(stream) == second
    waiting = asyncio.create_task(next_event(stream))
    await asyncio.sleep(0)
    third = await bus.append("third", {})
    assert await waiting == third
    waiting = asyncio.create_task(next_event(stream))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not bus._subscribers
    later = bus.subscribe(since=3)
    wait_later = asyncio.create_task(next_event(later))
    await asyncio.sleep(0)
    fourth = await bus.append("fourth", {})
    assert await wait_later == fourth
    await later.aclose()
    assert not bus._subscribers


@pytest.mark.asyncio
async def test_torn_tail_is_discarded(run_paths: RunPaths) -> None:
    first = await EventBus(run_paths).append("first", {})
    with run_paths.events_jsonl.open("ab") as stream:
        stream.write(b'{"seq":2')
    bus = EventBus(run_paths)
    assert await bus.replay() == (first,)
    assert (await bus.append("second", {})).seq == 2
    assert len(run_paths.events_jsonl.read_text().splitlines()) == 2
    run_paths.events_jsonl.write_bytes(b"torn")
    assert await EventBus(run_paths).replay() == ()


@pytest.mark.parametrize("data", [b"malformed\n", b"\xff\n", b"\n"])
def test_malformed_complete_record_fails_closed(run_paths: RunPaths, data: bytes) -> None:
    run_paths.events_jsonl.write_bytes(data)
    with pytest.raises(PersistenceError, match="events"):
        EventBus(run_paths)


@pytest.mark.parametrize("seq,run", [(2, None), (1, "wrong")])
def test_wrong_sequence_or_run(run_paths: RunPaths, seq: int, run: str | None) -> None:
    event = Event(seq=seq, run_id=run or run_paths.run_id, ts="now", type="tick", payload={})
    run_paths.events_jsonl.write_text(event.model_dump_json() + "\n")
    with pytest.raises(PersistenceError, match="sequence"):
        EventBus(run_paths)


@pytest.mark.asyncio
async def test_persistence_failure_does_not_notify(run_paths: RunPaths) -> None:
    bus = EventBus(run_paths)
    run_paths.events_jsonl.mkdir()
    with pytest.raises(PersistenceError, match="append"):
        await bus.append("tick", {})
    assert await bus.replay() == ()
    with pytest.raises(PersistenceError, match="reopen"):
        await bus.append("tick", {})
    with pytest.raises(PersistenceError, match="read"):
        EventBus(run_paths)
