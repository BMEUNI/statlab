"""The backend's single MCP discovery and dispatch boundary (D12, D23, D32)."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import TYPE_CHECKING, Literal, cast

import httpx2
from jsonschema import Draft202012Validator, SchemaError
from pydantic import JsonValue, ValidationError
from statlab_contracts.artifacts import (
    ARTIFACT_ID_PATTERN,
    ArtifactKind,
    ArtifactMeta,
    ArtifactRef,
)
from statlab_contracts.contract import DataContract, DatasetOptions
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
    run_ancestry,
    set_test_fingerprint,
)
from statlab.storage import create_artifact, get_artifact, validate_checkpoint_ownership

if TYPE_CHECKING:
    from mcp import ClientSession

    from statlab.graph.state import RunState

Repair = Callable[[Failure], Awaitable[dict[str, JsonValue]]]
_LOGGER = logging.getLogger(__name__)


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

    async def _dispatch_direct(
        self, spec: ToolSpec, arguments: dict[str, JsonValue], *, read_timeout_seconds: int
    ) -> ToolSuccess:
        try:
            json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ToolCallError(
                spec.name,
                Failure(
                    error_class="tool_param",
                    message="tool parameters must be strict JSON",
                    field_errors={"params": "parameter values must be finite JSON values"},
                ),
                cause=exc,
            ) from exc
        errors = self._schema_errors(spec, arguments)
        if errors:
            raise ToolCallError(
                spec.name,
                Failure(
                    error_class="tool_param",
                    message="tool parameters do not match served schema",
                    field_errors=errors,
                ),
            )
        try:
            result = await cast("ClientSession", self._session).call_tool(
                spec.name, arguments, read_timeout_seconds=read_timeout_seconds
            )
        except asyncio.CancelledError:
            raise
        except (httpx2.HTTPError, OSError, TimeoutError) as exc:
            raise ToolCallError(
                spec.name,
                ToolFailure(error_class="transient", message="MCP dispatch failed"),
                cause=exc,
            ) from exc
        except Exception as exc:
            from mcp.shared.exceptions import MCPError
            from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

            transient = isinstance(exc, MCPError) and exc.code in (
                REQUEST_TIMEOUT,
                CONNECTION_CLOSED,
            )
            raise ToolCallError(
                spec.name,
                ToolFailure(
                    error_class="transient" if transient else "bug",
                    message="MCP dispatch failed",
                ),
                cause=exc,
            ) from exc
        from mcp.types import CallToolResult

        if not isinstance(result, CallToolResult) or result.structured_content is None:
            raise ToolCallError(
                spec.name, Failure(error_class="bug", message="malformed MCP tool result")
            )
        structured = result.structured_content
        raw = structured.get("result") if isinstance(structured, Mapping) else None
        try:
            validated = TOOL_RESULT_ADAPTER.validate_python(raw)
        except Exception as exc:
            raise ToolCallError(
                spec.name,
                Failure(error_class="bug", message="malformed tool envelope"),
                cause=exc,
            ) from exc
        if isinstance(validated, ToolFailure):
            raise ToolCallError(spec.name, validated)
        return validated

    async def setup_dataset(
        self,
        *,
        project: str,
        dataset: str,
        options: DatasetOptions,
        contract: DataContract | None = None,
    ) -> ToolSuccess:
        """Dispatch the one fixed pre-run setup tool without run-scoped accounting."""
        if self._session is None:
            raise EngineError("MCP client is not entered")
        if get_project_by_slug(self.conn, project) is None:
            raise EngineError("setup project does not exist")
        spec = self.tools.get("setup_dataset")
        if (
            spec is None
            or spec.phase != "setup"
            or spec.allow_in != ("setup",)
            or spec.heavy
            or spec.transform_only
        ):
            raise EngineError("setup_dataset registry metadata is invalid")
        arguments: dict[str, JsonValue] = {
            "project": project,
            "dataset": dataset,
            "options": options.model_dump(mode="json"),
        }
        if contract is not None:
            arguments["contract"] = contract.model_dump(mode="json")
        validated = await self._dispatch_direct(spec, arguments, read_timeout_seconds=900)
        if validated.artifacts:
            raise ToolCallError(
                "setup_dataset",
                Failure(error_class="bug", message="setup tool returned unexpected artifacts"),
            )
        return validated

    async def list_estimators(self) -> ToolSuccess:
        """Dispatch the read-only registry listing for the UI without run-scoped accounting."""
        if self._session is None:
            raise EngineError("MCP client is not entered")
        spec = self.tools.get("list_estimators")
        if spec is None or spec.phase != "training" or spec.heavy or spec.transform_only:
            raise EngineError("list_estimators registry metadata is invalid")
        validated = await self._dispatch_direct(spec, {}, read_timeout_seconds=60)
        if validated.artifacts:
            raise ToolCallError(
                "list_estimators",
                Failure(error_class="bug", message="registry tool returned unexpected artifacts"),
            )
        return validated

    async def _read_only(
        self, tool: str, project: str, arguments: dict[str, JsonValue]
    ) -> ToolSuccess:
        if self._session is None:
            raise EngineError("MCP client is not entered")
        if get_project_by_slug(self.conn, project) is None:
            raise EngineError(f"{tool} project does not exist")
        spec = self.tools.get(tool)
        if (
            spec is None
            or spec.phase != "artifacts"
            or spec.allow_in != ("*",)
            or spec.heavy
            or spec.transform_only
        ):
            raise EngineError(f"{tool} registry metadata is invalid")
        validated = await self._dispatch_direct(spec, arguments, read_timeout_seconds=60)
        if validated.artifacts:
            raise ToolCallError(
                tool,
                Failure(error_class="bug", message="read-only tool returned unexpected artifacts"),
            )
        return validated

    async def describe_artifact(
        self,
        *,
        project: str,
        run: str,
        artifact: str,
        kind: ArtifactKind,
        head_rows: int = 20,
    ) -> ToolSuccess:
        """Dispatch the read-only describe tool for a UI preview without run-scoped accounting."""
        return await self._read_only(
            "describe_artifact",
            project,
            {
                "project": project,
                "run": run,
                "artifact": artifact,
                "kind": kind,
                "head_rows": head_rows,
            },
        )

    async def validation_rows(
        self,
        *,
        project: str,
        run: str,
        frame: str,
        predictions: str,
        target: str,
        task: Literal["classification", "regression"],
        threshold: float | None,
        worst_first: bool,
        offset: int,
        limit: int,
    ) -> ToolSuccess:
        """Dispatch the read-only validation rows tool for the UI without run-scoped accounting."""
        return await self._read_only(
            "validation_rows",
            project,
            {
                "project": project,
                "run": run,
                "frame": frame,
                "predictions": predictions,
                "target": target,
                "task": task,
                "threshold": threshold,
                "worst_first": worst_first,
                "offset": offset,
                "limit": limit,
            },
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
        if row.run_id not in {ancestor.id for ancestor in run_ancestry(self.conn, current.id)}:
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
        _LOGGER.info(
            "Tool call run=%s node=%s execution=%s tool=%s ok=%s duration=%dms args=%s",
            ctx.paths.run_id,
            ctx.node,
            ctx.node_execution_id,
            tool,
            json.loads(result_json).get("ok"),
            duration_ms,
            json.dumps(sent, separators=(",", ":"), ensure_ascii=False),
        )
        _LOGGER.debug(
            "Tool result tool=%s execution=%s %s", tool, ctx.node_execution_id, result_json
        )


def logged_calls(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    fork_aware: bool = False,
    checkpoint: RunState | None = None,
) -> tuple[LoggedCall, ...]:
    """Rebuild reproducibility calls from durable rows in dispatch order."""
    try:
        if fork_aware:
            if checkpoint is None:
                raise PersistenceError("fork-aware audit requires a retained checkpoint")
            retained = validate_checkpoint_ownership(conn, run_id, checkpoint)
            ancestry = run_ancestry(conn, run_id)
            ids = tuple(run.id for run in ancestry)
            rows = conn.execute(
                "SELECT rowid AS dispatch_order, * FROM tool_calls "
                "WHERE run_id IN (SELECT value FROM json_each(?)) ORDER BY rowid",
                (json.dumps(ids),),
            )
        else:
            retained = ()
            rows = conn.execute(
                "SELECT rowid AS dispatch_order, * FROM tool_calls WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            )
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
    if not fork_aware:
        return tuple(calls)

    producers: dict[str, int] = {}
    for index, call in enumerate(calls):
        for ref in call.outputs:
            if ref in producers:
                raise PersistenceError("tool audit has duplicate artifact producers")
            producers[ref] = index
    selected: set[int] = set()

    def visit(ref: str, before: int) -> None:
        index = producers.get(ref)
        if index is None or index >= before:
            raise PersistenceError("tool audit is missing an earlier artifact producer")
        row, call = stored[index], calls[index]
        artifact = get_artifact(conn, ref)
        if (
            artifact is None
            or artifact.run_id != row["run_id"]
            or artifact.produced_by != call.tool
        ):
            raise PersistenceError("artifact producer ownership mismatch")
        execution = conn.execute(
            "SELECT run_id FROM node_executions WHERE id = ?", (row["node_execution_id"],)
        ).fetchone()
        if execution is None or execution["run_id"] != row["run_id"]:
            raise PersistenceError("producer execution ownership mismatch")
        if index in selected:
            return
        for value in call.arguments.values():
            for dependency in McpClient._refs_in(value):
                visit(dependency, index)
        selected.add(index)

    try:
        for artifact in retained:
            if artifact.run_id != run_id:
                visit(artifact.id, len(calls))
        current: list[int] = []
        for index, row in enumerate(stored):
            if row["run_id"] != run_id:
                continue
            current.append(index)
            for value in calls[index].arguments.values():
                for ref in McpClient._refs_in(value):
                    visit(ref, index)
    except (sqlite3.Error, ValidationError) as exc:
        raise PersistenceError("cannot compose ancestor producer audit", cause=exc) from exc
    inherited = tuple(
        calls[index] for index in sorted(selected) if stored[index]["run_id"] != run_id
    )
    return (*inherited, *(calls[index] for index in current))


__all__ = ["CallContext", "McpClient", "ToolSpec", "check_lineage", "logged_calls"]
