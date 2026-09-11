"""Chunk 07 §4.4: M1's acceptance, over a real MCP session. M1-M10.

M7 is the one that matters most: it proves the wire contract chunk 02 defined is the contract
the server actually speaks, by feeding `structured_content["result"]` straight into
`TOOL_RESULT_ADAPTER` -- the single parse/serialise entry point chunk 21 must also use.

`mcp_session` is a factory, not a session directly -- see its docstring in `conftest.py` for
why (a pytest-asyncio + anyio task-group teardown incompatibility). Every test opens its own
session with `async with mcp_session() as session:`, keeping the whole lifetime inside the
test's own task.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

import pytest
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER

if TYPE_CHECKING:
    from mcp import ClientSession

pytestmark = pytest.mark.asyncio

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]


async def _call(session: ClientSession, name: str, arguments: dict[str, Any]) -> Any:
    from mcp.types import CallToolResult

    result = await session.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    return result


# --- M1-M2: the handshake, verbatim from PLAN.md's M1 row --------------------------------------


async def test_m1_initialize_succeeds_over_real_http(mcp_session: SessionFactory) -> None:
    """M1: an external MCP client completes `tools/list` and a round-trip call. `initialize`
    already ran inside `mcp_session`'s `__aenter__`; reaching this line at all is the proof."""
    async with mcp_session() as session:
        result = await session.list_tools()
        assert result.tools


async def test_m2_tools_list_returns_both_artifact_tools(mcp_session: SessionFactory) -> None:
    async with mcp_session() as session:
        result = await session.list_tools()
        names = {t.name for t in result.tools}
        assert names == {"list_artifacts", "describe_artifact"}


# --- M3-M5: the D23 guarantee, over the real registry -------------------------------------------


async def test_m3_every_tool_has_a_nonempty_description(mcp_session: SessionFactory) -> None:
    """M3: iterates every registered tool, not one fixture example."""
    async with mcp_session() as session:
        result = await session.list_tools()
        assert result.tools
        for tool_obj in result.tools:
            assert tool_obj.description
            assert tool_obj.description.strip()


async def test_m4_every_parameter_of_every_tool_has_a_description(
    mcp_session: SessionFactory,
) -> None:
    """M4: the D23 guarantee, over the real registry rather than a fixture -- if a future tool
    (chunk 08 onward) slips past the decorator's own check somehow, this test still catches
    it at the schema level."""
    async with mcp_session() as session:
        result = await session.list_tools()
        assert result.tools
        for tool_obj in result.tools:
            properties = tool_obj.input_schema.get("properties", {})
            for param_name, param_schema in properties.items():
                assert param_schema.get("description"), (
                    f"{tool_obj.name}.{param_name} has no description in its served schema"
                )


async def test_m5_every_tool_meta_has_phase_allow_in_heavy(mcp_session: SessionFactory) -> None:
    async with mcp_session() as session:
        result = await session.list_tools()
        assert result.tools
        for tool_obj in result.tools:
            assert tool_obj.meta is not None
            assert "phase" in tool_obj.meta
            assert "allow_in" in tool_obj.meta
            assert "heavy" in tool_obj.meta


# --- M6-M7: the envelope round-trip ---------------------------------------------------------------


async def test_m6_full_call_tool_round_trip(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """M6: `is_error` false, `structured_content["result"]["ok"]` true."""
    async with mcp_session() as session:
        result = await _call(
            session, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["result"]["ok"] is True


async def test_m7_envelope_validates_as_tool_result(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """M7: feed `structured_content["result"]` to `TOOL_RESULT_ADAPTER` -- proves server and
    client agree on chunk 02's contract, not just that the server produced *some* JSON."""
    async with mcp_session() as session:
        result = await _call(
            session, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
        assert result.structured_content is not None
        validated = TOOL_RESULT_ADAPTER.validate_python(result.structured_content["result"])
        assert validated.ok is True


async def test_m7b_failure_envelope_also_validates_as_tool_result(
    mcp_session: SessionFactory,
) -> None:
    """The failure half of M7's claim: a `ToolFailure` envelope round-trips through the same
    adapter too, not just the success shape."""
    async with mcp_session() as session:
        result = await _call(
            session,
            "list_artifacts",
            {"project": "proj", "run": "r_does_not_exist_anywhere"},
        )
        assert result.structured_content is not None
        validated = TOOL_RESULT_ADAPTER.validate_python(result.structured_content["result"])
        assert validated.ok is False
        assert validated.error_class == "transient"


# --- M8: schema-invalid arguments ---------------------------------------------------------------


async def test_m8_schema_invalid_argument_is_repairable_feedback_not_a_traceback(
    mcp_session: SessionFactory,
) -> None:
    """M8: missing a required field. `is_error` true, and the message is pydantic's own
    per-field validation report -- repairable feedback, not a Python traceback."""
    async with mcp_session() as session:
        result = await _call(session, "describe_artifact", {"project": "proj"})
        assert result.is_error is True
        text = result.content[0].text if result.content else ""
        assert "Traceback" not in text
        assert "validation error" in text.lower()
        assert "run" in text
        assert "artifact" in text
        assert "kind" in text


# --- M9-M10: statelessness and resilience -------------------------------------------------------


async def test_m9_two_sequential_calls_do_not_leak_state(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """M9: statelessness -- calling the same tool twice with the same arguments produces the
    same shape both times, with nothing accumulated between calls."""
    async with mcp_session() as session:
        first = await _call(
            session, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
        second = await _call(
            session, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
        assert first.structured_content == second.structured_content


async def test_m10_server_survives_a_tool_raising(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """M10: a tool call that fails must not take the session down -- a subsequent call on the
    same session still works."""
    async with mcp_session() as session:
        failing = await _call(
            session,
            "list_artifacts",
            {"project": "proj", "run": "r_totally_missing_run_id"},
        )
        assert failing.is_error is False
        assert failing.structured_content is not None
        assert failing.structured_content["result"]["ok"] is False

        healthy = await _call(
            session, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
        assert healthy.is_error is False
        assert healthy.structured_content is not None
        assert healthy.structured_content["result"]["ok"] is True


# --- additional coverage: describe_artifact over the wire, schema shape details -----------------


async def test_describe_artifact_full_round_trip_over_the_wire(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """The second tool, exercised end to end through a real session, not just `list_artifacts`."""
    import pandas as pd
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_contracts.layout import RunPaths
    from statlab_tools.artifacts import save_dataframe
    from statlab_tools.settings import load_tool_settings

    settings = load_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    ref = ArtifactRef("a_0000000000000099")
    save_dataframe(paths.artifact(ref, "dataframe"), pd.DataFrame({"x": [1, 2, 3]}), ref=ref)

    async with mcp_session() as session:
        result = await _call(
            session,
            "describe_artifact",
            {
                "project": "proj",
                "run": run_paths_over_mcp,
                "artifact": str(ref),
                "kind": "dataframe",
            },
        )
        assert result.is_error is False
        assert result.structured_content is not None
        summary = result.structured_content["result"]["summary"]
        assert summary["rows"] == 3


async def test_list_tools_input_schema_has_no_additional_properties_leak(
    mcp_session: SessionFactory,
) -> None:
    """A schema sanity check: every declared property in `list_artifacts`'s schema is exactly
    `project` and `run` -- no stray parameter leaked through from the body's own signature
    (`paths` in particular must never appear, since it is an implementation detail)."""
    async with mcp_session() as session:
        result = await session.list_tools()
        list_artifacts_tool = next(t for t in result.tools if t.name == "list_artifacts")
        assert set(list_artifacts_tool.input_schema["properties"]) == {"project", "run"}


async def test_describe_artifact_schema_required_fields(mcp_session: SessionFactory) -> None:
    """`head_rows` has a default and is therefore not required; everything else is."""
    async with mcp_session() as session:
        result = await session.list_tools()
        describe_tool = next(t for t in result.tools if t.name == "describe_artifact")
        assert set(describe_tool.input_schema["required"]) == {"project", "run", "artifact", "kind"}
        assert "head_rows" not in describe_tool.input_schema["required"]


async def test_initialize_result_reports_configured_server_name(
    live_server: str,
) -> None:
    """The server's `name`/`version`, set once in `tool.py`'s `MCPServer(...)` construction,
    reach the client through `initialize()`'s own result -- confirmed directly here rather
    than through the `mcp_session` fixture, since the fixture discards the `initialize()`
    return value."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    # See the matching comment in conftest.py's mcp_session fixture: the public-looking
    # `mcp.client.streamable_http.create_mcp_http_client` fails `mypy --strict`'s
    # `no_implicit_reexport` because that module never lists it in an `__all__`, even though
    # it is the same object at runtime. Importing from its defining module is the only
    # mypy-clean option that doesn't add a fourth `# type: ignore` shape.
    from mcp.shared._httpx_utils import create_mcp_http_client

    from tests.conftest import MCP_TEST_TOKEN as LIVE_TOKEN

    http_client = create_mcp_http_client(headers={"Authorization": f"Bearer {LIVE_TOKEN}"})
    async with (
        http_client,
        streamable_http_client(f"{live_server}/mcp", http_client=http_client) as (read, write),
        ClientSession(read, write) as session,
    ):
        init_result = await session.initialize()
        assert init_result.server_info.name == "statlab-tools"


async def test_two_independent_sessions_do_not_interfere(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """Two separate sessions (not two calls on one session, per M9) also do not leak state --
    the server is stateless across sessions, not just across calls within one."""
    async with mcp_session() as session_a:
        result_a = await _call(
            session_a, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
    async with mcp_session() as session_b:
        result_b = await _call(
            session_b, "list_artifacts", {"project": "proj", "run": run_paths_over_mcp}
        )
    assert result_a.structured_content == result_b.structured_content
