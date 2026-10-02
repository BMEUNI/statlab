"""The one report wire type shared across the process boundary (chunk 19 D-c).

`ReportSection` is a narrative section the LLM contributes to the report between the Inference
and Figures parts. It lives here, not in `mcp_server/`, because node 12's LLM schema (chunk 29)
produces it and `assemble_report` consumes it -- a wire type crossing the boundary, so it belongs
in the dependency-free contracts package beside the envelope and artifact types. stdlib + pydantic
only; frozen and `extra="forbid"` like every other contract model, so a malformed section is a
parse error, not a surprise mid-report.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ReportSection(BaseModel):
    """One LLM-authored narrative section: a heading and a markdown body (chunk 19 D-c).

    `heading` is 1-80 characters; `assemble_report` sanitises it to a single line (D-e). `body`
    is markdown up to 8000 characters, rendered verbatim between the report's Inference and
    Figures parts. Frozen and `extra="forbid"`: the LLM cannot smuggle an extra field past the
    schema, and a built section cannot be mutated after validation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    heading: str = Field(min_length=1, max_length=80)
    body: str = Field(max_length=8000)


class LoggedCall(BaseModel):
    """One tool call as the orchestrator observed it, for chunk 20's reproducibility tools (D-b).

    The MCP server has no database, so the backend (chunk 29) builds a `LoggedCall` from each
    `tool_calls` row and passes `calls: tuple[LoggedCall, ...]` to `export_pipeline_script` and
    `snapshot_run`. `tool` is the registered tool name; `arguments` are the arguments **as sent**
    over the wire, with the orchestrator-injected `project`/`run`/`seed` included (the script and
    the run hash drop `project`/`run` themselves); `outputs` are the produced artifact refs in
    envelope order.

    It lives here beside `ReportSection`, not in `mcp_server/`, because it crosses the process
    boundary -- the backend produces it, the MCP tools consume it. Frozen and `extra="forbid"`
    like every other contract model: a malformed call log is a parse error, not a surprise
    mid-slice. `arguments` is `dict[str, JsonValue]` so only JSON types ever reach the renderer
    (D-d renders values with `repr` over JSON types only).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str = Field(min_length=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    outputs: tuple[str, ...] = ()
