"""The backend's single MCP discovery and dispatch boundary (D12, D23, D32)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, cast

import httpx2
from jsonschema import Draft202012Validator, SchemaError
from pydantic import JsonValue, ValidationError
from statlab_contracts.artifacts import ARTIFACT_ID_PATTERN, ArtifactMeta, ArtifactRef
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import (
    ORCHESTRATOR_SUPPLIED,
    TOOL_RESULT_ADAPTER,
    Failure,
    ToolFailure,
    ToolSuccess,
)
from statlab_contracts.layout import RunPaths
from statlab_contracts.reporting import LoggedCall

from statlab.config import Settings
from statlab.domains.lineage import check_lineage
from statlab.errors import (
    ArtifactError,
    ArtifactLockedError,
    EngineError,
    PersistenceError,
    StatlabError,
    ToolCallError,
    ToolNotAllowedError,
)
from statlab.persistence.models import ArtifactRow
from statlab.persistence.repository import (
    claim_test_scoring,
    get_contract,
    get_project_by_slug,
    get_run,
    record_tool_call,
    set_test_fingerprint,
)
from statlab.storage import create_artifact, get_artifact

if TYPE_CHECKING:
    from mcp import ClientSession

Repair = Callable[[Failure], Awaitable[dict[str, JsonValue]]]


@dataclass(frozen=True, slots=True)
class CallContext:
    paths: RunPaths
    node: str
    node_execution_id: str
    allowed: frozenset[str]
    contract: DataContract
    dataset: str
    champion_selected: bool = False


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    schema: dict[str, object]
    properties: dict[str, object]
    required: tuple[str, ...]
    allow_in: tuple[str, ...]
    phase: str
    heavy: bool
    transform_only: bool
    validator: Draft202012Validator


class McpClient:
    """Cache served schemas and enforce every dispatch invariant in one place."""

    def __init__(
        self, settings: Settings, conn: sqlite3.Connection, *, session: object | None = None
    ) -> None:
        self.settings = settings
        self.conn = conn
        self._injected_session = session
        self._session: object | None = None
        self._stack: AsyncExitStack | None = None
        self.tools: dict[str, ToolSpec] = {}

    async def __aenter__(self) -> McpClient:
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._stack = stack
        try:
            if self._injected_session is not None:
                self._session = self._injected_session
            else:
                from mcp import ClientSession
                from mcp.client.streamable_http import streamable_http_client
                from mcp.shared._httpx_utils import create_mcp_http_client

                http_client = create_mcp_http_client(
                    headers={
                        "Authorization": f"Bearer {self.settings.mcp_token.get_secret_value()}"
                    }
                )
                await stack.enter_async_context(http_client)
                read, write = await stack.enter_async_context(
                    streamable_http_client(self.settings.mcp_url, http_client=http_client)
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self._session = session
            await self._discover()
            return self
        except BaseException as exc:
            await self.__aexit__(type(exc), exc, exc.__traceback__)
            if isinstance(exc, Exception) and not isinstance(exc, StatlabError):
                raise EngineError("could not initialize or discover MCP tools", cause=exc) from exc
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, traceback
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception as close_error:
                if exc is not None:
                    exc.add_note("MCP transport cleanup failed")
                else:
                    raise EngineError("failed to close MCP transport") from close_error
            finally:
                self._stack = None
                self._session = None

    async def _discover(self) -> None:
        if self._session is None:
            raise EngineError("MCP client is not entered")
        listing = await cast("ClientSession", self._session).list_tools()
        for tool in listing.tools:
            meta = tool.meta
            if not isinstance(meta, Mapping):
                raise EngineError(f"tool {tool.name!r} has malformed metadata")
            schema = cast(dict[str, object], tool.input_schema)
            props = schema.get("properties")
            required = schema.get("required", [])
            allow_in = meta.get("allow_in")
            if (
                not isinstance(props, dict)
                or not isinstance(required, list)
                or not all(isinstance(value, str) for value in required)
                or not isinstance(allow_in, list)
                or not all(isinstance(value, str) for value in allow_in)
                or not isinstance(meta.get("phase"), str)
                or not isinstance(meta.get("heavy"), bool)
                or not isinstance(meta.get("transform_only"), bool)
            ):
                raise EngineError(f"tool {tool.name!r} has malformed schema metadata")
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise EngineError(f"tool {tool.name!r} has invalid JSON Schema", cause=exc) from exc
            self.tools[tool.name] = ToolSpec(
                tool.name,
                schema,
                cast(dict[str, object], props),
                tuple(cast(list[str], required)),
                tuple(cast(list[str], allow_in)),
                cast(str, meta["phase"]),
                cast(bool, meta["heavy"]),
                cast(bool, meta["transform_only"]),
                Draft202012Validator(schema),
            )

    def allowed_tools(self, node: str) -> frozenset[str]:
        return frozenset(
            name
            for name, spec in self.tools.items()
            if node in spec.allow_in or spec.allow_in == ("*",)
        )

    async def call(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        repair: Repair | None = None,
    ) -> ToolSuccess:
        try:
            return await self._call_once(ctx, tool, params, trusted=trusted)
        except ToolCallError as exc:
            if repair is None or exc.failure.error_class != "tool_param":
                raise
            corrected = await repair(exc.failure)
            return await self.call(ctx, tool, corrected, trusted=trusted)
        except sqlite3.Error as exc:
            raise PersistenceError("MCP dispatch persistence failed", cause=exc) from exc

    async def _call_once(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
    ) -> ToolSuccess:
        spec = self.tools.get(tool)
        if spec is None or tool not in ctx.allowed:
            raise ToolNotAllowedError(tool, ctx.node, ctx.allowed)
        if ctx.node not in spec.allow_in and spec.allow_in != ("*",):
            raise ToolNotAllowedError(tool, ctx.node, frozenset(spec.allow_in))
        self._validate_context(ctx)
        sent = self._inject(ctx, spec, params, trusted)
        try:
            json.dumps(sent, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ToolCallError(
                tool,
                Failure(
                    error_class="tool_param",
                    message="tool parameters must be strict JSON",
                    field_errors={"params": "parameter values must be finite JSON values"},
                ),
                cause=exc,
            ) from exc
        errors = self._schema_errors(spec, sent)
        if errors:
            failure = Failure(
                error_class="tool_param",
                message="tool parameters do not match served schema",
                field_errors=errors,
            )
            raise ToolCallError(tool, failure)
        refs = self._artifact_refs(sent)
        rows = {ref: self._resolve_ref(ctx, ref) for ref in refs}
        tainted = {
            key: ref
            for key, value in sent.items()
            for ref in self._refs_in(value)
            if rows[ref].derived_from_test or rows[ref].split == "test"
        }
        run = get_run(self.conn, ctx.paths.run_id)
        if run is None:
            raise EngineError(f"run {ctx.paths.run_id!r} does not exist")
        check_lineage(
            ctx.node,
            tool,
            tainted,
            run.test_scored_at is not None,
            transform_only=spec.transform_only,
            phase=spec.phase,
            champion_selected=ctx.champion_selected,
        )
        if tool.startswith("select_champion") and run.test_scored_at is not None:
            raise EngineError("cannot select a champion after test scoring")
        execution = self.conn.execute(
            "SELECT run_id, node_name FROM node_executions WHERE id = ?",
            (ctx.node_execution_id,),
        ).fetchone()
        if execution is None or execution["run_id"] != ctx.paths.run_id:
            raise EngineError("node execution does not belong to this run")
        if execution["node_name"] != ctx.node:
            raise EngineError("node execution does not match CallContext node")
        if self._session is None:
            raise EngineError("MCP client is not entered")
        if (
            tool.startswith("score_")
            and tainted
            and not claim_test_scoring(self.conn, ctx.paths.run_id)
        ):
            raise ArtifactLockedError(next(iter(tainted.values())), ctx.node)
        started = time.monotonic()
        try:
            result = await cast("ClientSession", self._session).call_tool(
                tool, sent, read_timeout_seconds=900
            )
        except asyncio.CancelledError as exc:
            duration = int((time.monotonic() - started) * 1000)
            failure = ToolFailure(error_class="transient", message="MCP dispatch cancelled")
            try:
                self._record(ctx, tool, sent, failure.model_dump_json(), duration)
            except Exception:
                exc.add_note("MCP dispatch cancellation audit logging failed")
            raise
        except (httpx2.HTTPError, OSError, TimeoutError) as exc:
            duration = int((time.monotonic() - started) * 1000)
            failure = ToolFailure(error_class="transient", message="MCP dispatch failed")
            self._record(ctx, tool, sent, failure.model_dump_json(), duration)
            raise ToolCallError(tool, failure, cause=exc) from exc
        except Exception as exc:
            from mcp.shared.exceptions import MCPError
            from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

            transient = isinstance(exc, MCPError) and (
                exc.code in (REQUEST_TIMEOUT, CONNECTION_CLOSED)
            )
            duration = int((time.monotonic() - started) * 1000)
            failure = ToolFailure(
                error_class="transient" if transient else "bug",
                message="MCP dispatch failed",
            )
            self._record(ctx, tool, sent, failure.model_dump_json(), duration)
            raise ToolCallError(tool, failure, cause=exc) from exc
        duration = int((time.monotonic() - started) * 1000)
        from mcp.types import CallToolResult

        if not isinstance(result, CallToolResult) or result.structured_content is None:
            failure = ToolFailure(error_class="bug", message="malformed MCP tool result")
            self._record(ctx, tool, sent, failure.model_dump_json(), duration)
            raise ToolCallError(tool, failure)
        structured = result.structured_content
        raw = structured.get("result") if isinstance(structured, Mapping) else None
        try:
            validated = TOOL_RESULT_ADAPTER.validate_python(raw)
        except Exception as exc:
            failure = ToolFailure(error_class="bug", message="malformed tool envelope")
            self._record(ctx, tool, sent, failure.model_dump_json(), duration)
            raise ToolCallError(tool, failure, cause=exc) from exc
        self._record(ctx, tool, sent, validated.model_dump_json(), duration)
        if isinstance(validated, ToolFailure):
            raise ToolCallError(tool, validated)
        for meta in validated.artifacts:
            self._register(ctx, tool, meta, bool(tainted))
        fingerprint = validated.summary.get("test_fingerprint")
        if isinstance(fingerprint, str):
            set_test_fingerprint(self.conn, ctx.paths.run_id, fingerprint)
        return validated

    def _validate_context(self, ctx: CallContext) -> None:
        run = get_run(self.conn, ctx.paths.run_id)
        project = get_project_by_slug(self.conn, ctx.paths.slug)
        if run is None or project is None or run.project_id != project.id:
            raise EngineError("run and RunPaths project do not match")
        contract = get_contract(self.conn, run.contract_id)
        if (
            contract is None
            or contract.project_id != project.id
            or contract.contract() != ctx.contract
        ):
            raise EngineError("run contract does not match CallContext")

    def _inject(
        self,
        ctx: CallContext,
        spec: ToolSpec,
        params: Mapping[str, JsonValue],
        trusted: Mapping[str, JsonValue] | None,
    ) -> dict[str, JsonValue]:
        sent = dict(params)
        marked = {
            key
            for key, value in spec.properties.items()
            if isinstance(value, dict) and value.get("description") == ORCHESTRATOR_SUPPLIED
        }
        protected: dict[str, JsonValue] = {
            "project": ctx.paths.slug,
            "run": ctx.paths.run_id,
            "dataset": ctx.dataset,
            "seed": (
                ctx.contract.split.seed
                if spec.name.startswith("split_")
                else ctx.contract.constraints.random_seed
            ),
        }
        for key in marked:
            sent.pop(key, None)
            if key in protected and key in spec.properties:
                sent[key] = protected[key]
            elif trusted is not None and key in trusted:
                sent[key] = trusted[key]
            else:
                raise EngineError(f"orchestrator value for {spec.name}.{key} is missing")
        for key, value in protected.items():
            if key in spec.properties:
                sent[key] = value
        return sent

    @staticmethod
    def _schema_errors(spec: ToolSpec, sent: Mapping[str, JsonValue]) -> dict[str, str]:
        errors: dict[str, str] = {}
        try:
            for error in spec.validator.iter_errors(dict(sent)):
                path = ".".join(str(item) for item in error.absolute_path) or "params"
                errors[path] = (
                    f"{errors[path]}; {error.message}" if path in errors else error.message
                )
        except Exception as exc:
            raise EngineError("served tool schema could not be evaluated", cause=exc) from exc
        return errors

    @staticmethod
    def _refs_in(value: JsonValue) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,) if ARTIFACT_ID_PATTERN.match(value) else ()
        if isinstance(value, list):
            return tuple(ref for item in value for ref in McpClient._refs_in(item))
        if isinstance(value, dict):
            return tuple(ref for item in value.values() for ref in McpClient._refs_in(item))
        return ()

    def _artifact_refs(self, sent: Mapping[str, JsonValue]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ref for value in sent.values() for ref in self._refs_in(value)))

    def _resolve_ref(self, ctx: CallContext, ref: str) -> ArtifactRow:
        row = get_artifact(self.conn, ref)
        if row is None or row.pruned_at is not None:
            raise ArtifactError(ref, "unknown or pruned artifact")
        owner = get_run(self.conn, row.run_id)
        current = get_run(self.conn, ctx.paths.run_id)
        if owner is None or current is None or owner.project_id != current.project_id:
            raise ArtifactError(ref, "artifact belongs to another project")
        ancestor = current
        while ancestor.id != row.run_id and ancestor.forked_from_run_id is not None:
            next_run = get_run(self.conn, ancestor.forked_from_run_id)
            if next_run is None:
                break
            if next_run.project_id != current.project_id:
                raise ArtifactError(ref, "fork ancestry crosses projects")
            ancestor = next_run
        if ancestor.id != row.run_id:
            raise ArtifactError(ref, "artifact is outside this run's fork lineage")
        path = ctx.paths.artifact(ArtifactRef(ref), row.kind)
        if not path.is_file():
            raise ArtifactError(ref, f"missing run-local artifact link at {path}")
        return row

    def _register(self, ctx: CallContext, tool: str, meta: ArtifactMeta, tainted: bool) -> None:
        if not ARTIFACT_ID_PATTERN.match(str(meta.ref)):
            raise ArtifactError(str(meta.ref), "tool returned an invalid artifact ref")
        path = ctx.paths.artifact(meta.ref, meta.kind)
        if not path.is_file():
            raise ArtifactError(str(meta.ref), f"tool output file is missing at {path}")
        create_artifact(
            self.conn,
            ref=meta.ref,
            run_id=ctx.paths.run_id,
            kind=meta.kind,
            path=path,
            meta=meta,
            produced_by=tool,
            split=meta.split,
            derived_from_test=tainted or meta.split == "test",
        )

    def _record(
        self,
        ctx: CallContext,
        tool: str,
        sent: Mapping[str, JsonValue],
        result_json: str,
        duration_ms: int,
    ) -> None:
        record_tool_call(
            self.conn,
            run_id=ctx.paths.run_id,
            node_execution_id=ctx.node_execution_id,
            tool_name=tool,
            args_json=json.dumps(sent, separators=(",", ":"), ensure_ascii=False),
            result_json=result_json,
            duration_ms=duration_ms,
        )


def logged_calls(conn: sqlite3.Connection, run_id: str) -> tuple[LoggedCall, ...]:
    """Rebuild reproducibility calls from durable rows in dispatch order."""
    try:
        rows = conn.execute("SELECT * FROM tool_calls WHERE run_id = ? ORDER BY rowid", (run_id,))
        stored = rows.fetchall()
    except sqlite3.Error as exc:
        raise PersistenceError("cannot read tool call log", cause=exc) from exc
    calls: list[LoggedCall] = []
    for row in stored:
        try:
            arguments = cast(dict[str, JsonValue], json.loads(row["args_json"]))
            result = TOOL_RESULT_ADAPTER.validate_python(json.loads(row["result_json"]))
            outputs = tuple(str(meta.ref) for meta in result.artifacts) if result.ok else ()
            calls.append(LoggedCall(tool=row["tool_name"], arguments=arguments, outputs=outputs))
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise PersistenceError(
                f"malformed stored tool call row {row['id']!r}", cause=exc
            ) from exc
    return tuple(calls)


__all__ = ["CallContext", "McpClient", "ToolSpec", "check_lineage", "logged_calls"]
