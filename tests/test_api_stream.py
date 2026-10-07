"""Offline tests for event replay, cursors and SSE response cleanup."""

from __future__ import annotations

import asyncio
import signal
import sys
from collections import deque
from pathlib import Path
from types import FrameType

import pytest
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr
from sse_starlette.sse import AppStatus
from starlette.types import ASGIApp, Message, Scope
from statlab.api.runs import RunConfig, RunRegistry
from statlab.api.stream import event_frame, parse_cursor, stream_events
from statlab.config import Settings
from statlab.errors import ConfigError
from statlab.events import Event, EventBus
from statlab.graph.control import ControlText, QaAnswer
from statlab.graph.router import InterruptClass
from statlab.persistence.repository import get_run, list_gates, list_messages
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.layout import RunPaths

from tests.test_api_runs import (
    FixtureRecords,
    RunAPI,
    _create_run,
    _decision,
    _gate,
    _mcp_session,
    _open_run_api,
)


@pytest.mark.parametrize(
    ("since", "last", "expected"),
    ((0, None, 0), (0, "12", 12), (4, "2", 4), (4, "8", 8)),
)
def test_parse_cursor(since: int, last: str | None, expected: int) -> None:
    assert parse_cursor(since, last) == expected


@pytest.mark.parametrize("value", ("", "-1", "+2", " 2", "2.0", "²"))
def test_parse_cursor_rejects_non_ascii_decimal(value: str) -> None:
    with pytest.raises(HTTPException) as error:
        parse_cursor(0, value)
    assert error.value.status_code == 422
    assert error.value.detail == "invalid request"


def test_parse_cursor_rejects_integer_digit_limit() -> None:
    maximum = sys.get_int_max_str_digits()
    if maximum == 0:
        pytest.skip("integer conversion digit limit is disabled")
    with pytest.raises(HTTPException) as error:
        parse_cursor(0, "1" * (maximum + 1))
    assert error.value.status_code == 422
    assert error.value.detail == "invalid request"


def test_event_frame_contains_one_json_data_line() -> None:
    event = Event(
        seq=2,
        run_id="r_0123456789abcdef",
        ts="now",
        type="tool.result",
        payload={"candidate_id": "c_a", "text": "line one\nline two"},
    )
    frame = event_frame(event)
    assert frame.startswith("id: 2\ndata: ")
    assert frame.endswith("\n\n")
    assert frame.count("\ndata: ") == 1
    assert "event:" not in frame
    encoded = frame.split("data: ", maxsplit=1)[1].removesuffix("\n\n")
    assert Event.model_validate_json(encoded) == event


@pytest.mark.asyncio
async def test_stream_replay_live_and_future_cursor(
    run_paths: RunPaths,
) -> None:
    bus = EventBus(run_paths)
    events = [
        await bus.append("tool.result", {"candidate_id": "c_a" if index % 2 == 0 else "c_b"})
        for index in range(6)
    ]
    closing = asyncio.Event()
    stream = stream_events(bus, since=3, closing=closing)
    assert [await anext(stream) for _ in range(3)] == [event_frame(event) for event in events[3:]]
    pending = asyncio.create_task(anext(stream))
    async with asyncio.timeout(2):
        while not bus._subscribers:
            await asyncio.sleep(0)
    seventh = await bus.append("tool.result", {"candidate_id": "c_b"})
    assert await asyncio.wait_for(pending, timeout=2) == event_frame(seventh)
    await stream.aclose()
    assert not bus._subscribers

    future = stream_events(bus, since=100, closing=closing)
    waiting = asyncio.create_task(anext(future))
    async with asyncio.timeout(2):
        while not bus._subscribers:
            await asyncio.sleep(0)
    eighth = await bus.append("tool.result", {"candidate_id": "c_a"})
    assert await asyncio.wait_for(waiting, timeout=2) == event_frame(eighth)
    await future.aclose()
    assert not bus._subscribers


@pytest.mark.asyncio
async def test_stream_closure_and_cancellation_release_subscriber(
    run_paths: RunPaths,
) -> None:
    bus = EventBus(run_paths)
    first = await bus.append("test.one", {})
    await bus.append("test.two", {})
    closing = asyncio.Event()
    stream = stream_events(bus, since=0, closing=closing)
    assert await anext(stream) == event_frame(first)
    closing.set()
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert not bus._subscribers

    closed = stream_events(bus, since=0, closing=closing)
    with pytest.raises(StopAsyncIteration):
        await anext(closed)
    assert not bus._subscribers

    fresh = EventBus(run_paths)
    latest = (await fresh.replay())[-1].seq
    cancelled_stream = stream_events(fresh, since=latest, closing=asyncio.Event())
    waiting = asyncio.create_task(anext(cancelled_stream))
    async with asyncio.timeout(2):
        while not fresh._subscribers:
            await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await cancelled_stream.aclose()
    assert not fresh._subscribers


@pytest.mark.asyncio
async def test_stream_heartbeat_keeps_subscription_and_writes_no_event(
    run_paths: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.api.stream as stream_module

    bus = EventBus(run_paths)
    monkeypatch.setattr(stream_module, "_HEARTBEAT_SECONDS", 0.01)
    closing = asyncio.Event()
    stream = stream_events(bus, since=0, closing=closing)
    assert await asyncio.wait_for(anext(stream), timeout=2) == ": heartbeat\n\n"
    assert len(bus._subscribers) == 1
    assert await bus.replay() == ()
    pending = asyncio.create_task(anext(stream))
    added = await bus.append("test.after-heartbeat", {"n": 1})
    assert await asyncio.wait_for(pending, timeout=2) == event_frame(added)
    closing.set()
    await stream.aclose()
    assert not bus._subscribers


def _active_tasks(api: RunAPI) -> frozenset[str]:
    return frozenset(
        run_id for run_id, task in api.registry.execution_tasks.items() if not task.done()
    )


async def _drive_request(
    app: ASGIApp,
    path: str,
    headers: list[tuple[bytes, bytes]],
    *,
    query_string: bytes = b"",
) -> tuple[asyncio.Task[None], asyncio.Event, asyncio.Queue[Message]]:
    messages: asyncio.Queue[Message] = asyncio.Queue()
    disconnected = asyncio.Event()
    first_request = True

    async def receive() -> Message:
        nonlocal first_request
        if first_request:
            first_request = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        await messages.put(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "method": "GET",
        "root_path": "",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query_string,
        "headers": headers,
    }

    async def invoke() -> None:
        await app(scope, receive, send)

    task: asyncio.Task[None] = asyncio.create_task(invoke())
    return task, disconnected, messages


@pytest.mark.asyncio
async def test_sse_route_replays_and_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_run_api(tmp_path, monkeypatch, records=FixtureRecords()) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        bus = api.registry.bindings[run_id].loop.events
        for latest_query, header_offset in ((True, -1), (False, 0)):
            latest = (await bus.replay())[-1].seq
            row_before = get_run(api.conn, run_id)
            gates_before = tuple(list_gates(api.conn, run_id))
            tasks_before = _active_tasks(api)
            query_since = latest if latest_query else 0
            query = f"since={query_since}".encode()
            task, disconnected, messages = await _drive_request(
                api.app,
                f"/api/runs/{run_id}/events",
                [(b"last-event-id", str(latest + header_offset).encode())],
                query_string=query,
            )
            try:
                start = await asyncio.wait_for(messages.get(), timeout=2)
                assert start["type"] == "http.response.start"
                assert start["status"] == 200
                headers = dict(start["headers"])
                assert headers[b"content-type"].startswith(b"text/event-stream")
                assert headers[b"cache-control"] == b"no-cache"
                assert headers[b"x-accel-buffering"] == b"no"
                async with asyncio.timeout(2):
                    while not bus._subscribers:
                        await asyncio.sleep(0)
                event = await bus.append("test.live", {"n": latest})
                body = await asyncio.wait_for(messages.get(), timeout=2)
                assert body["type"] == "http.response.body"
                assert body["body"].decode() == event_frame(event)
            finally:
                disconnected.set()
                await asyncio.wait_for(task, timeout=2)
            assert not bus._subscribers
            assert api.registry.requests_drained.is_set()
            assert get_run(api.conn, run_id) == row_before
            assert tuple(list_gates(api.conn, run_id)) == gates_before
            assert _active_tasks(api) == tasks_before


@pytest.mark.asyncio
async def test_sse_route_maps_unknown_and_invalid_cursors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_run_api(tmp_path, monkeypatch, records=FixtureRecords()) as api:
        missing = await api.client.get("/api/runs/r_0123456789abcdef/events")
        negative = await api.client.get("/api/runs/r_0123456789abcdef/events?since=-1")
        malformed = await api.client.get(
            "/api/runs/r_0123456789abcdef/events", headers={"Last-Event-ID": "+2"}
        )
        assert missing.status_code == 404
        assert negative.status_code == malformed.status_code == 422
        assert negative.json() == malformed.json() == {"detail": "invalid request"}
        api.registry.begin_shutdown()
        closed = await api.client.get("/api/runs/r_0123456789abcdef/events")
        assert closed.status_code == 503


@pytest.mark.asyncio
async def test_stream_shutdown_closes_subscriber_and_drains_admitted_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_run_api(tmp_path, monkeypatch, records=FixtureRecords()) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        binding = api.registry.bindings[run_id]
        run_before = get_run(api.conn, run_id)
        gates_before = tuple(list_gates(api.conn, run_id))
        tasks_before = _active_tasks(api)
        latest = (await binding.loop.events.replay())[-1].seq
        task, disconnected, messages = await _drive_request(
            api.app,
            f"/api/runs/{run_id}/events",
            [(b"last-event-id", str(latest).encode())],
        )
        try:
            start = await asyncio.wait_for(messages.get(), timeout=2)
            assert start["type"] == "http.response.start"
            async with asyncio.timeout(2):
                while not binding.loop.events._subscribers:
                    await asyncio.sleep(0)
            api.registry.begin_shutdown()
            await asyncio.wait_for(task, timeout=2)
            await asyncio.wait_for(api.registry.shutdown(), timeout=2)
            assert api.registry.requests_drained.is_set()
            assert not binding.loop.events._subscribers
            row = api.conn.execute("SELECT 1").fetchone()
            assert row is not None and row[0] == 1
            assert _mcp_session(api) is api.session
            assert api.registry.streams_closing.is_set()
            assert get_run(api.conn, run_id) == run_before
            assert run_before is not None and run_before.status == "awaiting_user"
            assert tuple(list_gates(api.conn, run_id)) == gates_before
            assert gate.status == "pending"
            assert _active_tasks(api) == tasks_before
            await asyncio.wait_for(api.registry.shutdown(), timeout=2)
        finally:
            disconnected.set()
            if not task.done():
                await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_server_signal_hook_signals_registry_before_uvicorn_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.main as main_module

    async with _open_run_api(tmp_path, monkeypatch, records=FixtureRecords()) as api:
        api.app.state.run_registry = api.registry
        calls: list[tuple[int, bool, bool]] = []

        def parent_exit(server: uvicorn.Server, sig: int, frame: FrameType | None) -> None:
            del frame
            server.should_exit = True
            calls.append((sig, api.registry.closing, api.registry.streams_closing.is_set()))

        monkeypatch.setattr(uvicorn.Server, "handle_exit", parent_exit)
        monkeypatch.setattr(main_module, "RunRegistry", RunRegistry)
        server = main_module._RunServer(api.app, api.settings)
        server.handle_exit(15, None)
        server.handle_exit(15, None)
        assert calls == [(15, True, True)]


@pytest.mark.asyncio
async def test_server_signal_hook_before_registry_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.main as main_module

    async with _open_run_api(tmp_path, monkeypatch, records=FixtureRecords()) as api:
        forwarded: list[int] = []

        def parent_exit(server: uvicorn.Server, sig: int, frame: FrameType | None) -> None:
            del server, frame
            forwarded.append(sig)

        monkeypatch.setattr(uvicorn.Server, "handle_exit", parent_exit)
        monkeypatch.setattr(main_module, "RunRegistry", RunRegistry)
        app = FastAPI()
        server = main_module._RunServer(app, api.settings)
        server.handle_exit(2, None)
        assert forwarded == [2]


def test_main_uses_supported_server_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.main as main_module

    settings = Settings(
        mcp_token=SecretStr("fixture-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        api_host="127.0.0.1",
        api_port=8111,
    )
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(main_module, "load_settings", lambda: settings)
    monkeypatch.setattr(main_module, "create_app", lambda selected: FastAPI())

    def run(server: main_module._RunServer) -> None:
        config = server.config
        seen.append((config.host, config.port))

    monkeypatch.setattr(main_module._RunServer, "run", run)
    assert main_module.main() == 0
    assert seen == [("127.0.0.1", 8111)]


def test_main_config_error_display_is_fixed_and_secret_free(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import statlab.main as main_module

    def fail_settings() -> Settings:
        raise ConfigError("SECRET_SENTINEL", cause=RuntimeError("SECRET_SENTINEL"))

    invoked = False

    def run(server: main_module._RunServer) -> None:
        nonlocal invoked
        invoked = True
        del server

    monkeypatch.setattr(main_module, "load_settings", fail_settings)
    monkeypatch.setattr(main_module._RunServer, "run", run)
    assert main_module.main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err == "Backend configuration is invalid. Check .env and configuration values.\n"
    )
    assert "SECRET_SENTINEL" not in captured.err
    assert invoked is False


def test_server_repeated_signals_preserve_lifespan_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(AppStatus, "should_exit", False)
    import statlab.main as main_module

    settings = Settings(
        mcp_token=SecretStr("fixture-token"),
        api_host="127.0.0.1",
        api_port=8111,
    )
    server = main_module._RunServer(FastAPI(), settings)
    server.handle_exit(signal.SIGINT, None)
    server.handle_exit(signal.SIGINT, None)
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit is True
    assert server.force_exit is False
    assert server._captured_signals == [signal.SIGINT]


@pytest.mark.asyncio
async def test_unavailable_citation_discards_streamed_draft(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(
        replies=deque(
            (
                _decision(),
                InterruptClass(category="qa").model_dump_json(),
                QaAnswer(
                    text="Draft answer.",
                    references=(ArtifactRef("a_0123456789abcdef"),),
                ).model_dump_json(),
            )
        )
    )
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        row_before = get_run(api.conn, run_id)
        gates_before = tuple(list_gates(api.conn, run_id))
        tasks_before = _active_tasks(api)
        response = await api.client.post(
            f"/api/runs/{run_id}/messages", json={"text": "what evidence?"}
        )
        assert response.status_code == 502
        assert response.json() == {"detail": "LLM output failed"}
        run_row = get_run(api.conn, run_id)
        assert run_row is not None
        config = RunConfig.model_validate_json(run_row.config_json)
        sql_messages = list_messages(api.conn, config.session_id)
        assert len(sql_messages) == 1 and sql_messages[0].role == "user"
        events = await api.registry.bindings[run_id].loop.events.replay()
        deltas = tuple(event for event in events if event.type == "message.assistant.delta")
        assert deltas[0].payload["text"] == "" and deltas[0].payload["reset"] is True
        assert all(event.payload["reset"] is False for event in deltas[1:])
        assert (
            "".join(str(event.payload["text"]) for event in deltas if not event.payload["reset"])
            == "Draft answer."
        )
        user_event = next(event for event in events if event.type == "message.user")
        error_event = next(event for event in events if event.type == "error")
        assert error_event.payload == {
            "reply_to": user_event.payload["message_id"],
            "message": "Assistant response failed.",
            "discard_draft": True,
        }
        assert not any(event.type == "message.assistant.complete" for event in events)
        assert len(sql_messages) == 1 and sql_messages[0].content == "what evidence?"
        assert "a_0123456789abcdef" not in str(error_event.payload)
        assert get_run(api.conn, run_id) == row_before
        assert tuple(list_gates(api.conn, run_id)) == gates_before
        assert _active_tasks(api) == tasks_before


@pytest.mark.asyncio
async def test_cancelled_control_discards_streamed_draft_and_drains_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_llm_stream import FakeStream, chunk

    records = FixtureRecords(
        replies=deque((_decision(), InterruptClass(category="qa").model_dump_json()))
    )
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        run_before = get_run(api.conn, run_id)
        gates_before = tuple(list_gates(api.conn, run_id))
        tasks_before = _active_tasks(api)
        llm = api.registry.resources.llm
        original = llm._acompletion
        entered, release = asyncio.Event(), asyncio.Event()
        stream = FakeStream(
            (
                chunk('{"text":"partial'),
                chunk(' answer","references":[]}'),
                chunk(None, usage=True),
            ),
            entered=entered,
            release=release,
            block_before=1,
        )

        async def provider(**kwargs: object) -> object:
            if kwargs.get("stream") is True:
                return stream
            return await original(**kwargs)

        monkeypatch.setattr(llm, "_acompletion", provider)

        async def send_message() -> object:
            async with api.registry.request():
                return await api.registry.message(run_id, ControlText(text="question"))

        task = asyncio.create_task(send_message())
        try:
            async with asyncio.timeout(2):
                await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert stream.closed
        events = await api.registry.bindings[run_id].loop.events.replay()
        deltas = tuple(event for event in events if event.type == "message.assistant.delta")
        assert deltas[0].payload["text"] == "" and deltas[0].payload["reset"] is True
        assert all(event.payload["reset"] is False for event in deltas[1:])
        assert (
            "".join(str(event.payload["text"]) for event in deltas if not event.payload["reset"])
            == "partial"
        )
        user_event = next(event for event in events if event.type == "message.user")
        error_event = next(event for event in events if event.type == "error")
        assert error_event.payload == {
            "reply_to": user_event.payload["message_id"],
            "message": "Assistant response cancelled.",
            "discard_draft": True,
        }
        assert not any(event.type == "message.assistant.complete" for event in events)
        run_row = get_run(api.conn, run_id)
        assert run_row is not None
        config = RunConfig.model_validate_json(run_row.config_json)
        sql_messages = list_messages(api.conn, config.session_id)
        assert len(sql_messages) == 1 and sql_messages[0].role == "user"
        assert api.registry.requests_drained.is_set()
        assert get_run(api.conn, run_id) == run_before
        assert run_before is not None and run_before.status == "awaiting_user"
        assert tuple(list_gates(api.conn, run_id)) == gates_before
        assert gate.status == "pending"
        assert _active_tasks(api) == tasks_before
        usage = api.conn.execute(
            "SELECT signature, prompt_tokens, completion_tokens, cached_tokens "
            "FROM llm_calls ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert usage is not None and tuple(usage) == ("QaAnswer", 0, 0, 0)


@pytest.mark.asyncio
async def test_events_route_rejects_while_closing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision()]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        api.registry.begin_shutdown()
        closed = await api.client.get(f"/api/runs/{run_id}/events")
        assert closed.status_code == 503
