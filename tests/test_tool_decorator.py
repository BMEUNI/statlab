"""Chunk 07 §4.2: the `@tool` decorator. T1-T24.

T3/T4 are the highest-value tests in this chunk: D23 makes the schema the agent's only
documentation, so an undocumented tool must fail the build, not ship. Both prove the check
fires at decoration time, inside `pytest.raises` wrapping the `@tool(...)` application itself
-- not at call time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
import pytest
from pydantic import Field
from statlab_contracts.envelope import ToolFailure, ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import ToolArtifactError
from statlab_tools.tool import ToolDataError, ToolParamError, tool


@pytest.fixture(autouse=True)
def _isolated_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here registers throwaway tools onto `tool.server`, the same module-level
    singleton the real `list_artifacts`/`describe_artifact` register onto in production. Left
    unpatched, those throwaway registrations leak into `test_server_mcp_session.py`'s M2
    assertion (`tools/list` returning exactly the two real tools) whenever both files run in
    the same session -- confirmed directly: running this file before that one made M2 see
    `t5_tool`, `dup_name_test`, and every other throwaway name this file ever registered.
    A fresh `MCPServer` per test keeps this file's registrations from ever reaching the real
    singleton at all.
    """
    from mcp.server.mcpserver import MCPServer
    from statlab_tools import tool as tool_module

    monkeypatch.setattr(tool_module, "server", MCPServer(name="test-isolated", version="0.0.0"))


@pytest.fixture(autouse=True)
def _fixed_permits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every test in this file uses `fit_permits=1` and a real data dir, so `heavy=True`
    tools resolve settings without touching a real environment."""
    monkeypatch.setenv("STATLAB_MCP_TOKEN", "decorator-test-token")
    monkeypatch.setenv("STATLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("STATLAB_FIT_PERMITS", "1")


@pytest.fixture
def run_dir(tmp_path: Path) -> RunPaths:
    """A run directory that exists on disk, matching `STATLAB_DATA_DIR` above."""
    paths = RunPaths(data_dir=tmp_path / "data", slug="proj", run_id="r_dec0000000000000")
    paths.root.mkdir(parents=True, exist_ok=True)
    return paths


async def _call(registered_name: str, **kwargs: object) -> dict[str, object]:
    """Invoke a registered tool through the real server, the way the MCP layer would.

    Returns the parsed `structured_content["result"]` dict, typed as `dict[str, object]` --
    every test that uses this reads specific known keys out of it, which mypy cannot verify
    against `JsonValue`'s recursive union any more precisely than that.
    """
    from mcp.types import CallToolResult
    from statlab_tools.tool import server

    result = await server.call_tool(registered_name, kwargs)
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    payload = result.structured_content["result"]
    assert isinstance(payload, dict)
    return payload


async def _get_input_schema(registered_name: str) -> dict[str, Any]:
    """`Tool.input_schema` is declared `dict[str, Any]` by the SDK itself (a JSON Schema
    document, not a shape we control) -- typed the same way here rather than fighting it level
    by level with casts. `Any` is permitted in test code (`test_no_explicit_any.py` scans only
    `backend/`, `mcp_server/`, `contracts/`)."""
    from statlab_tools.tool import server

    tools = await server.list_tools()
    return next(t for t in tools if t.name == registered_name).input_schema


# --- T1-T2: envelope shape and meta -----------------------------------------------------------


def test_t1_success_body_yields_ok_true(run_dir: RunPaths) -> None:
    @tool(phase="t1", allow_in=["*"])
    def t1_tool(paths: RunPaths) -> ToolSuccess:
        """A trivial success."""
        del paths
        return ToolSuccess(summary={"x": 1})

    import asyncio

    payload = asyncio.run(_call("t1_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is True
    assert payload["summary"] == {"x": 1}
    assert payload["artifacts"] == []


def test_t2_meta_carries_phase_allow_in_heavy() -> None:
    @tool(phase="t2", allow_in=["node_a", "node_b"], heavy=True)
    def t2_tool(paths: RunPaths) -> ToolSuccess:
        """A trivial success."""
        del paths
        return ToolSuccess()

    import asyncio

    from statlab_tools.tool import server

    async def get_meta() -> dict[str, object]:
        tools = await server.list_tools()
        matching = [t for t in tools if t.name == "t2_tool"]
        assert len(matching) == 1
        assert matching[0].meta is not None
        return matching[0].meta

    meta = asyncio.run(get_meta())
    assert meta["phase"] == "t2"
    assert meta["allow_in"] == ["node_a", "node_b"]
    assert meta["heavy"] is True


# --- T3-T4: decoration-time enforcement (D23) --------------------------------------------------


def test_t3_missing_docstring_raises_at_decoration_time() -> None:
    """T3. The `@tool(...)` application itself raises -- proven by wrapping it, not the call,
    in `pytest.raises`."""
    with pytest.raises(ValueError, match="no docstring"):

        @tool(phase="t3", allow_in=["*"])
        def undocumented(paths: RunPaths) -> ToolSuccess:
            del paths
            return ToolSuccess()


def test_t3b_error_names_the_function() -> None:
    with pytest.raises(ValueError, match="undocumented_named"):

        @tool(phase="t3", allow_in=["*"])
        def undocumented_named(paths: RunPaths) -> ToolSuccess:
            del paths
            return ToolSuccess()


def test_t4_missing_parameter_description_raises_at_decoration_time() -> None:
    """T4. Same shape as T3: the parameter has a type but no `Field(description=...)`."""
    with pytest.raises(ValueError, match="no Field"):

        @tool(phase="t4", allow_in=["*"])
        def bad_param(paths: RunPaths, x: int) -> ToolSuccess:
            """Has a docstring, but `x` has no description."""
            del paths, x
            return ToolSuccess()


def test_t4b_error_names_the_parameter() -> None:
    with pytest.raises(ValueError, match="'x'"):

        @tool(phase="t4", allow_in=["*"])
        def bad_param_named(paths: RunPaths, x: int) -> ToolSuccess:
            """Has a docstring, but `x` has no description."""
            del paths, x
            return ToolSuccess()


def test_t4c_field_without_description_also_raises() -> None:
    """A parameter that IS `Annotated[..., Field(...)]` but never sets `description=` is the
    same defect as a bare `int` -- both leave the agent with nothing to read."""
    with pytest.raises(ValueError, match="no Field"):

        @tool(phase="t4", allow_in=["*"])
        def bad_field(paths: RunPaths, x: Annotated[int, Field(ge=0)]) -> ToolSuccess:
            """Has a docstring; `x` has a Field but no description."""
            del paths, x
            return ToolSuccess()


def test_t4d_empty_string_description_also_raises() -> None:
    with pytest.raises(ValueError, match="no Field"):

        @tool(phase="t4", allow_in=["*"])
        def blank_description(
            paths: RunPaths, x: Annotated[int, Field(description="")]
        ) -> ToolSuccess:
            """Has a docstring; `x`'s description is blank."""
            del paths, x
            return ToolSuccess()


def test_t4e_annotated_without_any_field_metadata_also_raises() -> None:
    """`Annotated[int, "not a Field"]` -- genuinely `Annotated`, but with no `FieldInfo` among
    its metadata at all. Distinct from T4c (a real `Field` with no description): this closes
    `_field_info`'s loop-exhausted branch, where the metadata tuple is non-empty but nothing in
    it is a `FieldInfo`."""
    with pytest.raises(ValueError, match="no Field"):

        @tool(phase="t4", allow_in=["*"])
        def annotated_no_field(paths: RunPaths, x: Annotated[int, "not a Field"]) -> ToolSuccess:
            """Has a docstring; `x` is Annotated but carries no Field at all."""
            del paths, x
            return ToolSuccess()


def test_t3_does_not_register_the_broken_tool() -> None:
    """A decoration-time failure must not leave a half-registered tool behind."""
    from statlab_tools.tool import server

    with pytest.raises(ValueError, match="no docstring"):

        @tool(phase="t3", allow_in=["*"])
        def never_registered(paths: RunPaths) -> ToolSuccess:
            del paths
            return ToolSuccess()

    assert server._tool_manager.get_tool("never_registered") is None


# --- T5-T6: schema shape ------------------------------------------------------------------------


def test_t5_every_parameter_description_reaches_input_schema() -> None:
    @tool(phase="t5", allow_in=["*"])
    def t5_tool(
        paths: RunPaths, count: Annotated[int, Field(description="how many, exactly")]
    ) -> ToolSuccess:
        """Docstring present."""
        del paths, count
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("t5_tool"))
    assert schema["properties"]["count"]["description"] == "how many, exactly"


def test_t6_bounds_and_enums_reach_input_schema() -> None:
    @tool(phase="t6", allow_in=["*"])
    def t6_tool(
        paths: RunPaths,
        n: Annotated[int, Field(description="bounded", ge=1, le=5)],
        mode: Annotated[Literal["a", "b"], Field(description="mode")] = "a",
    ) -> ToolSuccess:
        """Docstring present."""
        del paths, n, mode
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("t6_tool"))
    assert schema["properties"]["n"]["minimum"] == 1
    assert schema["properties"]["n"]["maximum"] == 5
    assert schema["properties"]["mode"]["enum"] == ["a", "b"]
    assert schema["properties"]["mode"]["default"] == "a"


# --- T7-T11: exception classification -----------------------------------------------------------


def test_t7_tool_param_error_classifies_with_field_errors(run_dir: RunPaths) -> None:
    @tool(phase="t7", allow_in=["*"])
    def t7_tool(paths: RunPaths) -> ToolSuccess:
        """Raises ToolParamError."""
        del paths
        raise ToolParamError("bad arg", field_errors={"x": "must be positive"})

    import asyncio

    payload = asyncio.run(_call("t7_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is False
    assert payload["error_class"] == "tool_param"
    assert payload["field_errors"] == {"x": "must be positive"}


def test_t8_tool_param_without_field_errors_is_rejected() -> None:
    """T8: the decorator cannot produce a tool_param Failure lacking field_errors --
    `ToolParamError.__init__` itself refuses an empty dict, which is what makes this
    unreachable through the decorator at all."""
    with pytest.raises(ValueError, match="field_errors"):
        ToolParamError("bad arg", field_errors={})


def test_t9_tool_data_error_classifies_as_data(run_dir: RunPaths) -> None:
    @tool(phase="t9", allow_in=["*"])
    def t9_tool(paths: RunPaths) -> ToolSuccess:
        """Raises ToolDataError."""
        del paths
        raise ToolDataError("no numeric column exists")

    import asyncio

    payload = asyncio.run(_call("t9_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is False
    assert payload["error_class"] == "data"
    assert payload["field_errors"] is None


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: ToolArtifactError("/p", "boom"),
        lambda: OSError("disk full"),
        lambda: TimeoutError("too slow"),
    ],
    ids=["ToolArtifactError", "OSError", "TimeoutError"],
)
def test_t10_transient_exceptions_classify_as_transient(
    run_dir: RunPaths, exc_factory: object
) -> None:
    name = f"t10_tool_{id(exc_factory)}"

    def body(paths: RunPaths) -> ToolSuccess:
        del paths
        raise exc_factory()  # type: ignore[operator]  # exc_factory is a zero-arg callable, mypy cannot see through the parametrize id

    body.__name__ = name
    body.__doc__ = "Raises a transient exception."
    tool(phase="t10", allow_in=["*"])(body)

    import asyncio

    payload = asyncio.run(_call(name, project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is False
    assert payload["error_class"] == "transient"


@pytest.mark.parametrize(
    "exc_factory",
    [lambda: ValueError("oops"), lambda: KeyError("missing"), lambda: ZeroDivisionError("boom")],
    ids=["ValueError", "KeyError", "ZeroDivisionError"],
)
def test_t11_unrecognised_exceptions_classify_as_bug(
    run_dir: RunPaths, exc_factory: object
) -> None:
    name = f"t11_tool_{id(exc_factory)}"

    def body(paths: RunPaths) -> ToolSuccess:
        del paths
        raise exc_factory()  # type: ignore[operator]

    body.__name__ = name
    body.__doc__ = "Raises an unrecognised exception."
    tool(phase="t11", allow_in=["*"])(body)

    import asyncio

    payload = asyncio.run(_call(name, project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is False
    assert payload["error_class"] == "bug"


# --- T12-T13: traceback discipline --------------------------------------------------------------


def test_t12_no_traceback_in_message(run_dir: RunPaths) -> None:
    """T12: raise with a distinctive frame; neither the filename nor the word Traceback may
    appear anywhere in the envelope."""

    def _distinctive_frame_marker_ohsxc932() -> None:
        raise ValueError("boom from a distinctive frame")

    @tool(phase="t12", allow_in=["*"])
    def t12_tool(paths: RunPaths) -> ToolSuccess:
        """Raises from a nested, distinctively-named frame."""
        del paths
        _distinctive_frame_marker_ohsxc932()
        return ToolSuccess()  # unreachable

    import asyncio
    import json

    payload = asyncio.run(_call("t12_tool", project=run_dir.slug, run=run_dir.run_id))
    envelope_text = json.dumps(payload)
    assert "_distinctive_frame_marker_ohsxc932" not in envelope_text
    assert "Traceback" not in envelope_text
    assert __file__ not in envelope_text


def test_t13_traceback_is_logged(run_dir: RunPaths, caplog: pytest.LogCaptureFixture) -> None:
    """T13: the other direction of T12 -- the traceback goes somewhere, just never the wire."""

    @tool(phase="t13", allow_in=["*"])
    def t13_tool(paths: RunPaths) -> ToolSuccess:
        """Raises an unrecognised exception."""
        del paths
        raise ValueError("boom for the log")

    import asyncio

    with caplog.at_level(logging.WARNING, logger="statlab_tools"):
        asyncio.run(_call("t13_tool", project=run_dir.slug, run=run_dir.run_id))
    assert any("Traceback" in record.exc_text for record in caplog.records if record.exc_text)


# --- T14: raising-only contract -----------------------------------------------------------------


def test_t14_body_returning_toolfailure_directly_is_rejected(run_dir: RunPaths) -> None:
    """T14: raising is the contract. A body that returns `ToolFailure` instead of raising is
    treated as a bug, not forwarded as-is."""

    @tool(phase="t14", allow_in=["*"])
    def t14_tool(paths: RunPaths) -> ToolSuccess:
        """Returns a ToolFailure directly, which it must not do."""
        del paths
        return ToolFailure(  # type: ignore[return-value]  # deliberately violating the return type to prove the decorator rejects it
            error_class="data", message="returned instead of raised"
        )

    import asyncio

    payload = asyncio.run(_call("t14_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is False
    assert payload["error_class"] == "bug"


def test_t14b_body_returning_neither_success_nor_failure_is_a_bug(run_dir: RunPaths) -> None:
    """The other half of the same contract: a body returning a bare dict (or anything else
    that is not `ToolSuccess`) is caught too, not just the `ToolFailure` case."""

    @tool(phase="t14", allow_in=["*"])
    def t14b_tool(paths: RunPaths) -> ToolSuccess:
        """Returns a bare dict, which it must not do."""
        del paths
        return {"ok": True}  # type: ignore[return-value]  # deliberately violating the return type to prove the decorator rejects it

    import asyncio

    payload = asyncio.run(_call("t14b_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["ok"] is False
    assert payload["error_class"] == "bug"
    message = payload["message"]
    assert isinstance(message, str)
    assert "dict" in message


# --- T15: duration in log, not summary -----------------------------------------------------------


def test_t15_duration_logged_absent_from_summary(
    run_dir: RunPaths, caplog: pytest.LogCaptureFixture
) -> None:
    @tool(phase="t15", allow_in=["*"])
    def t15_tool(paths: RunPaths) -> ToolSuccess:
        """A trivial success."""
        del paths
        return ToolSuccess(summary={"x": 1})

    import asyncio

    with caplog.at_level(logging.INFO, logger="statlab_tools"):
        payload = asyncio.run(_call("t15_tool", project=run_dir.slug, run=run_dir.run_id))
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert "duration" not in summary
    assert "ms" not in summary
    assert any("ms" in record.message for record in caplog.records)


# --- T16-T18: the D17 semaphore ------------------------------------------------------------------


def test_t16_heavy_tools_serialise_with_one_permit(run_dir: RunPaths) -> None:
    """T16: proven by ordering under controlled release, not by timing.

    A synchronous tool body cannot yield control mid-execution, so two such bodies calling
    each other through the dispatcher can never literally overlap on one event-loop thread --
    proving "serialisation" against that shape would hold trivially even with no semaphore at
    all. The real claim to prove is narrower and exactly what D17 needs: a second `heavy=True`
    call's *acquire* blocks while the permit is held. This test holds the one permit directly
    (`_fit_semaphore()`, the same module-level semaphore `heavy=True` acquires), starts a
    `heavy_two` call, and proves it cannot proceed until the test itself releases -- using
    `anyio.Event`/`anyio.sleep(0)` checkpoints for deterministic interleaving, not a real-time
    sleep tolerance.
    """
    from statlab_tools.tool import _fit_semaphore, server

    order: list[str] = []

    @tool(phase="t16", allow_in=["*"], heavy=True)
    def heavy_two(paths: RunPaths) -> ToolSuccess:
        """Records its entry; only reachable once the permit is free."""
        del paths
        order.append("two-entered")
        return ToolSuccess()

    async def main() -> None:
        semaphore = _fit_semaphore()
        await semaphore.acquire()
        order.append("test-holds-permit")

        async def run_two() -> None:
            order.append("two-about-to-acquire")
            await server.call_tool("heavy_two", {"project": run_dir.slug, "run": run_dir.run_id})

        async with anyio.create_task_group() as tg:
            tg.start_soon(run_two)
            # Yield twice: once so `run_two` starts and blocks on the semaphore, once more so
            # that block is definitely in effect before this assertion runs.
            await anyio.sleep(0)
            await anyio.sleep(0)
            assert order == ["test-holds-permit", "two-about-to-acquire"], (
                "heavy_two must still be blocked on the semaphore at this point"
            )
            order.append("test-releasing-permit")
            # Released with the plain method, not `async with`'s __aexit__, from inside a task
            # group that has a waiting acquirer -- empirically, releasing via `async with`'s
            # exit here deadlocks (verified directly against bare `anyio.Semaphore` before this
            # fix), while the plain call does not.
            semaphore.release()

    anyio.run(main)
    assert order == [
        "test-holds-permit",
        "two-about-to-acquire",
        "test-releasing-permit",
        "two-entered",
    ]


def test_t17_light_tools_never_block(run_dir: RunPaths) -> None:
    """T17: two `heavy=False` calls run without any ordering constraint from a semaphore --
    proven by both completing without either waiting on the other."""
    order: list[str] = []

    @tool(phase="t17", allow_in=["*"], heavy=False)
    def light_tool(paths: RunPaths) -> ToolSuccess:
        """A light call that records its entry."""
        del paths
        order.append("light-enter")
        return ToolSuccess()

    from statlab_tools.tool import server

    async def server_call_tool() -> None:
        await server.call_tool("light_tool", {"project": run_dir.slug, "run": run_dir.run_id})

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(server_call_tool)
            tg.start_soon(server_call_tool)

    anyio.run(main)
    assert order == ["light-enter", "light-enter"]


def test_t17b_light_tool_bodies_overlap_in_time(run_dir: RunPaths) -> None:
    """T17b: two light calls are inside their bodies AT THE SAME TIME (D17).

    T17 only proves both calls finish, which is also true if they run one after the other on
    the event loop -- and they did, until bodies were moved to a worker thread. Here each body
    waits on a two-party barrier: it can only pass if the other body is running concurrently.
    Serialised bodies would deadlock, so the barrier's timeout turns that into a failure.
    """
    import threading

    barrier = threading.Barrier(2, timeout=5)

    @tool(phase="t17b", allow_in=["*"], heavy=False)
    def overlapping(paths: RunPaths) -> ToolSuccess:
        """Passes only if a second call is inside its body concurrently."""
        del paths
        barrier.wait()
        return ToolSuccess()

    from statlab_tools.tool import server

    async def call() -> None:
        await server.call_tool("overlapping", {"project": run_dir.slug, "run": run_dir.run_id})

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(call)
            tg.start_soon(call)

    anyio.run(main)
    assert not barrier.broken


def test_t18_permit_released_on_exception(run_dir: RunPaths) -> None:
    """T18: run a failing heavy tool three times with one permit. If the permit leaked on the
    first failure, the second or third call would hang and the test would time out rather than
    fail cleanly -- this test itself is the proof that it did not leak, since it completes."""

    @tool(phase="t18", allow_in=["*"], heavy=True)
    def failing_heavy(paths: RunPaths) -> ToolSuccess:
        """Always raises."""
        del paths
        raise ToolDataError("always fails")

    async def call_once() -> dict[str, object]:
        return await _call("failing_heavy", project=run_dir.slug, run=run_dir.run_id)

    async def main() -> list[dict[str, object]]:
        return [await call_once() for _ in range(3)]

    results: list[dict[str, object]] = []

    async def guarded() -> None:
        nonlocal results
        with anyio.move_on_after(5) as scope:
            results = await main()
        assert not scope.cancelled_caught, "a leaked permit would hang the third call"

    anyio.run(guarded)
    assert len(results) == 3
    assert all(r["ok"] is False for r in results)


# --- T19-T21: run-param injection -----------------------------------------------------------------


def test_t19_needs_run_true_injects_project_and_run() -> None:
    @tool(phase="t19", allow_in=["*"])
    def t19_tool(paths: RunPaths) -> ToolSuccess:
        """Needs a run."""
        del paths
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("t19_tool"))
    assert "project" in schema["properties"]
    assert "run" in schema["properties"]
    assert "orchestrator" in schema["properties"]["project"]["description"].lower()
    assert "orchestrator" in schema["properties"]["run"]["description"].lower()
    assert "project" in schema["required"]
    assert "run" in schema["required"]


def test_t20_needs_run_false_omits_project_and_run() -> None:
    @tool(phase="t20", allow_in=["*"], needs_run=False)
    def t20_tool() -> ToolSuccess:
        """Does not need a run."""
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("t20_tool"))
    assert "project" not in schema["properties"]
    assert "run" not in schema["properties"]


def test_t21_body_receives_run_paths_never_a_raw_ref(run_dir: RunPaths) -> None:
    received: list[object] = []

    @tool(phase="t21", allow_in=["*"])
    def t21_tool(paths: RunPaths) -> ToolSuccess:
        """Records what it received."""
        received.append(paths)
        return ToolSuccess()

    import asyncio

    asyncio.run(_call("t21_tool", project=run_dir.slug, run=run_dir.run_id))
    assert len(received) == 1
    assert isinstance(received[0], RunPaths)
    assert received[0] == run_dir


def test_t22_unknown_run_directory_is_transient_and_names_the_path() -> None:
    @tool(phase="t22", allow_in=["*"])
    def t22_tool(paths: RunPaths) -> ToolSuccess:
        """Never reached."""
        del paths
        return ToolSuccess()

    import asyncio

    payload = asyncio.run(_call("t22_tool", project="proj", run="r_does_not_exist_at_all"))
    assert payload["ok"] is False
    assert payload["error_class"] == "transient"
    message = payload["message"]
    assert isinstance(message, str)
    assert "r_does_not_exist_at_all" in message


# --- addendum §8.2: the RunPaths parameter is found by type, not by the name "paths" --------------


def test_addendum_run_paths_param_found_by_type_not_name(run_dir: RunPaths) -> None:
    """A `RunPaths` parameter named `p`, not `paths`, is still found and injected correctly --
    proof that detection is genuinely type-based. Name matching would silently skip injection
    here instead of raising or working."""
    received: list[object] = []

    @tool(phase="addendum", allow_in=["*"])
    def renamed_run_paths_tool(p: RunPaths) -> ToolSuccess:
        """Uses a RunPaths parameter named something other than paths."""
        received.append(p)
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("renamed_run_paths_tool"))
    assert "p" not in schema["properties"]
    assert "project" in schema["properties"]
    assert "run" in schema["properties"]

    asyncio.run(_call("renamed_run_paths_tool", project=run_dir.slug, run=run_dir.run_id))
    assert len(received) == 1
    assert isinstance(received[0], RunPaths)


def test_addendum_str_parameter_named_paths_stays_on_the_wire() -> None:
    """A `str` parameter that happens to be named `paths` is not a `RunPaths` and must not be
    stripped from the schema -- the exact footgun name-matching had: it would have deleted a
    real wire parameter with no indication to the agent why the call failed."""

    @tool(phase="addendum", allow_in=["*"], needs_run=False)
    def str_paths_tool(
        paths: Annotated[str, Field(description="A literal string, not a RunPaths.")],
    ) -> ToolSuccess:
        """Takes a str parameter named paths."""
        return ToolSuccess(summary={"paths": paths})

    import asyncio

    schema = asyncio.run(_get_input_schema("str_paths_tool"))
    assert "paths" in schema["properties"]
    assert schema["properties"]["paths"]["type"] == "string"
    assert "paths" in schema["required"]

    payload = asyncio.run(_call("str_paths_tool", paths="a/b/c"))
    assert payload["ok"] is True
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert summary["paths"] == "a/b/c"


def test_addendum_needs_run_true_with_zero_run_paths_params_raises_at_decoration_time() -> None:
    with pytest.raises(ValueError, match="RunPaths-typed parameters"):

        @tool(phase="addendum", allow_in=["*"])
        def zero_run_paths_tool(
            x: Annotated[int, Field(description="An int, no RunPaths at all.")],
        ) -> ToolSuccess:
            """Never registered."""
            del x
            return ToolSuccess()


def test_addendum_needs_run_true_with_two_run_paths_params_raises_at_decoration_time() -> None:
    with pytest.raises(ValueError, match="RunPaths-typed parameters"):

        @tool(phase="addendum", allow_in=["*"])
        def two_run_paths_tool(a: RunPaths, b: RunPaths) -> ToolSuccess:
            """Never registered -- two RunPaths parameters is a conflict, not a choice."""
            del a, b
            return ToolSuccess()


def test_addendum_needs_run_false_with_one_run_paths_param_raises_at_decoration_time() -> None:
    with pytest.raises(ValueError, match="RunPaths-typed parameter"):

        @tool(phase="addendum", allow_in=["*"], needs_run=False)
        def stray_run_paths_tool(paths: RunPaths) -> ToolSuccess:
            """Never registered -- needs_run=False must not declare a RunPaths parameter."""
            del paths
            return ToolSuccess()


def test_addendum_broken_tools_are_never_registered() -> None:
    """The three decoration-time raises above (zero, two, and a stray RunPaths parameter) all
    leave the tool unregistered, the same property T3/T4 already prove for the docstring and
    parameter-description checks."""
    from statlab_tools import tool as tool_module

    for name in ("zero_run_paths_tool", "two_run_paths_tool", "stray_run_paths_tool"):
        assert tool_module.server._tool_manager.get_tool(name) is None


def test_addendum_inject_run_params_rejects_none_directly() -> None:
    """`_inject_run_params`'s own defensive `None` guard (only reachable if called incorrectly,
    since the decorator itself always passes a real name when `needs_run=True`) is exercised
    directly here rather than left untested."""
    from statlab_tools.tool import _inject_run_params

    def body(paths: RunPaths) -> ToolSuccess:
        del paths
        return ToolSuccess()

    with pytest.raises(ValueError, match="requires a resolved RunPaths parameter name"):
        _inject_run_params(body, run_paths_param=None)


# --- T23-T24: registration discipline -------------------------------------------------------------


def test_t23_no_module_holds_a_tool_name_list() -> None:
    """T23: registering a tool needs no central list. Source-scan `tools/__init__.py` and
    `tools/artifacts.py` for a list/tuple literal of tool-name-shaped strings -- the only
    "list" of tool names anywhere is the return value of `list_tools()` at runtime.

    Excludes a `Literal["a", "b"]` type annotation (`typing.Literal[...]`): that is a parameter's
    *allowed values*, declared once per parameter the normal Pydantic way, and is not a central
    tool registry in the sense this test guards against -- chunk 09's `method: Literal["pearson",
    "spearman"]` and `Literal["iqr", "zscore"]` are legitimate uses this regex must not flag.
    """
    import re

    tools_dir = Path(__file__).resolve().parent.parent / "mcp_server" / "statlab_tools" / "tools"
    name_list_pattern = re.compile(r"(?<!Literal)\[\s*[\"'][a-z_]+[\"']\s*,\s*[\"'][a-z_]+[\"']")
    for path in tools_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not name_list_pattern.search(text), f"{path} appears to hold a tool-name list"


def test_t24_duplicate_tool_name_raises() -> None:
    @tool(phase="t24", allow_in=["*"])
    def dup_name_test(paths: RunPaths) -> ToolSuccess:
        """First registration."""
        del paths
        return ToolSuccess()

    with pytest.raises(ValueError, match="already registered"):

        @tool(phase="t24", allow_in=["*"])
        def dup_name_test(paths: RunPaths) -> ToolSuccess:
            """Second registration, same name."""
            del paths
            return ToolSuccess()


# --- additional coverage: envelope details, multiple artifacts, warnings ------------------------


def test_success_with_warnings_round_trips(run_dir: RunPaths) -> None:
    @tool(phase="extra", allow_in=["*"])
    def warning_tool(paths: RunPaths) -> ToolSuccess:
        """Returns a warning alongside success."""
        del paths
        return ToolSuccess(warnings=("`plan` has 47 levels; above max_cardinality of 30",))

    import asyncio

    payload = asyncio.run(_call("warning_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["warnings"] == ["`plan` has 47 levels; above max_cardinality of 30"]


def test_success_with_multiple_artifacts_round_trips(run_dir: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactMeta, new_artifact_ref

    @tool(phase="extra", allow_in=["*"])
    def multi_artifact_tool(paths: RunPaths) -> ToolSuccess:
        """Returns two artifacts."""
        del paths
        return ToolSuccess(
            artifacts=(
                ArtifactMeta(ref=new_artifact_ref(), kind="dataframe", rows=1, cols=1),
                ArtifactMeta(ref=new_artifact_ref(), kind="model"),
            )
        )

    import asyncio

    payload = asyncio.run(_call("multi_artifact_tool", project=run_dir.slug, run=run_dir.run_id))
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) == 2


def test_needs_run_false_tool_never_resolves_a_run_directory(tmp_path: Path) -> None:
    """`needs_run=False` bodies take no `paths` argument at all -- confirmed by a body with no
    parameters succeeding even though no run directory exists anywhere."""
    del tmp_path

    @tool(phase="extra", allow_in=["*"], needs_run=False)
    def no_run_tool() -> ToolSuccess:
        """Never needs a run."""
        return ToolSuccess(summary={"ok": True})

    import asyncio

    from statlab_tools.tool import server

    async def call() -> dict[str, object]:
        from mcp.types import CallToolResult

        result = await server.call_tool("no_run_tool", {})
        assert isinstance(result, CallToolResult)
        assert result.structured_content is not None
        payload = result.structured_content["result"]
        assert isinstance(payload, dict)
        return payload

    payload = asyncio.run(call())
    assert payload["ok"] is True


def test_heavy_and_light_tools_do_not_share_a_permit_pool_incorrectly(run_dir: RunPaths) -> None:
    """A `heavy=False` tool never touches the semaphore at all -- calling it while the one
    permit is held elsewhere still succeeds immediately."""
    from statlab_tools.tool import _fit_semaphore, server

    @tool(phase="extra", allow_in=["*"], heavy=False)
    def light_while_heavy_held(paths: RunPaths) -> ToolSuccess:
        """A light call made while the heavy semaphore is fully held."""
        del paths
        return ToolSuccess()

    async def main() -> dict[str, object]:
        semaphore = _fit_semaphore()
        await semaphore.acquire()
        try:
            from mcp.types import CallToolResult

            with anyio.move_on_after(5) as scope:
                result = await server.call_tool(
                    "light_while_heavy_held", {"project": run_dir.slug, "run": run_dir.run_id}
                )
            assert not scope.cancelled_caught, "a light tool must never block on the semaphore"
            assert isinstance(result, CallToolResult)
            assert result.structured_content is not None
            payload = result.structured_content["result"]
            assert isinstance(payload, dict)
            return payload
        finally:
            semaphore.release()

    payload = anyio.run(main)
    assert payload["ok"] is True


# --- additional coverage: field_errors shape, multiple params, classification depth -------------


def test_tool_param_error_field_errors_survives_multiple_fields(run_dir: RunPaths) -> None:
    @tool(phase="extra2", allow_in=["*"])
    def multi_field_error_tool(paths: RunPaths) -> ToolSuccess:
        """Raises with two field errors at once."""
        del paths
        raise ToolParamError(
            "two problems",
            field_errors={"a": "must be positive", "b": "must be less than a"},
        )

    import asyncio

    payload = asyncio.run(_call("multi_field_error_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["field_errors"] == {"a": "must be positive", "b": "must be less than a"}


def test_tool_data_error_message_preserved_verbatim(run_dir: RunPaths) -> None:
    @tool(phase="extra2", allow_in=["*"])
    def data_error_tool(paths: RunPaths) -> ToolSuccess:
        """Raises ToolDataError with a specific message."""
        del paths
        raise ToolDataError("the target column is constant; no model can be fit")

    import asyncio

    payload = asyncio.run(_call("data_error_tool", project=run_dir.slug, run=run_dir.run_id))
    message = payload["message"]
    assert isinstance(message, str)
    assert message == "the target column is constant; no model can be fit"


def test_multiple_optional_params_all_reach_schema_with_defaults(run_dir: RunPaths) -> None:
    @tool(phase="extra2", allow_in=["*"])
    def multi_optional_tool(
        paths: RunPaths,
        a: Annotated[int, Field(description="first", ge=0)] = 1,
        b: Annotated[str, Field(description="second")] = "default",
        c: Annotated[bool, Field(description="third")] = False,
    ) -> ToolSuccess:
        """Three optional parameters, all with defaults."""
        del paths, a, b, c
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("multi_optional_tool"))
    assert schema["properties"]["a"]["default"] == 1
    assert schema["properties"]["b"]["default"] == "default"
    assert schema["properties"]["c"]["default"] is False
    assert "a" not in schema["required"]
    assert "b" not in schema["required"]
    assert "c" not in schema["required"]


def test_required_and_optional_params_mixed_correctly(run_dir: RunPaths) -> None:
    @tool(phase="extra2", allow_in=["*"])
    def mixed_required_tool(
        paths: RunPaths,
        required_field: Annotated[int, Field(description="must be provided")],
        optional_field: Annotated[int, Field(description="has a default")] = 5,
    ) -> ToolSuccess:
        """One required, one optional parameter."""
        del paths, required_field, optional_field
        return ToolSuccess()

    import asyncio

    schema = asyncio.run(_get_input_schema("mixed_required_tool"))
    assert "required_field" in schema["required"]
    assert "optional_field" not in schema["required"]


def test_light_tool_meta_heavy_is_explicitly_false(run_dir: RunPaths) -> None:
    """`heavy` defaults to `False` and is served as an explicit `False`, not omitted --
    `mcp_client` (chunk 21) must be able to read it without a default fallback of its own."""
    del run_dir

    @tool(phase="extra2", allow_in=["*"])
    def default_heavy_tool(paths: RunPaths) -> ToolSuccess:
        """Uses the default heavy value."""
        del paths
        return ToolSuccess()

    import asyncio

    from statlab_tools.tool import server

    async def get_meta() -> dict[str, object]:
        tools = await server.list_tools()
        matching = next(t for t in tools if t.name == "default_heavy_tool")
        assert matching.meta is not None
        return matching.meta

    meta = asyncio.run(get_meta())
    assert meta["heavy"] is False


def test_allow_in_wildcard_preserved_verbatim(run_dir: RunPaths) -> None:
    del run_dir

    @tool(phase="extra2", allow_in=["*"])
    def wildcard_tool(paths: RunPaths) -> ToolSuccess:
        """Allowed everywhere."""
        del paths
        return ToolSuccess()

    import asyncio

    from statlab_tools.tool import server

    async def get_meta() -> dict[str, object]:
        tools = await server.list_tools()
        matching = next(t for t in tools if t.name == "wildcard_tool")
        assert matching.meta is not None
        return matching.meta

    meta = asyncio.run(get_meta())
    assert meta["allow_in"] == ["*"]


def test_allow_in_specific_node_list_preserved(run_dir: RunPaths) -> None:
    del run_dir

    @tool(phase="extra2", allow_in=["data_splitting", "data_profiling"])
    def specific_nodes_tool(paths: RunPaths) -> ToolSuccess:
        """Allowed in two specific nodes."""
        del paths
        return ToolSuccess()

    import asyncio

    from statlab_tools.tool import server

    async def get_meta() -> dict[str, object]:
        tools = await server.list_tools()
        matching = next(t for t in tools if t.name == "specific_nodes_tool")
        assert matching.meta is not None
        return matching.meta

    meta = asyncio.run(get_meta())
    assert meta["allow_in"] == ["data_splitting", "data_profiling"]


def test_classify_bug_default_covers_unlisted_exception_types(run_dir: RunPaths) -> None:
    """A totally arbitrary custom exception type, never seen by `_classify`, still lands as
    `bug` -- the default branch, not a `KeyError` inside the classifier itself."""

    class SomeArbitraryError(Exception):
        pass

    @tool(phase="extra2", allow_in=["*"])
    def arbitrary_error_tool(paths: RunPaths) -> ToolSuccess:
        """Raises a type the classifier has never seen."""
        del paths
        raise SomeArbitraryError("never classified before")

    import asyncio

    payload = asyncio.run(_call("arbitrary_error_tool", project=run_dir.slug, run=run_dir.run_id))
    assert payload["error_class"] == "bug"


def test_tool_param_error_rejects_none_field_errors_via_type_system() -> None:
    """`ToolParamError.__init__` requires `field_errors` as a keyword argument -- confirms the
    signature itself, not just the empty-dict rejection T8 already covers."""
    import inspect

    signature = inspect.signature(ToolParamError.__init__)
    assert signature.parameters["field_errors"].kind == inspect.Parameter.KEYWORD_ONLY
