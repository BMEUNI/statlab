"""Focused checks for the production MCP dispatch and durable lineage boundary."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult
from pydantic import JsonValue, SecretStr
from statlab.config import Settings
from statlab.domains.lineage import check_lineage
from statlab.errors import (
    ArtifactError,
    ArtifactLockedError,
    EngineError,
    PersistenceError,
    ToolCallError,
    ToolNotAllowedError,
)
from statlab.mcp_client import CallContext, McpClient, logged_calls
from statlab.persistence.db import connect
from statlab.persistence.models import RunRow
from statlab.persistence.repository import (
    claim_test_scoring,
    create_contract,
    create_node_execution,
    create_project,
    create_run,
    get_project_by_slug,
    get_run,
    record_tool_call,
)
from statlab.storage import create_artifact, ensure_run_layout, fork_artifacts, get_artifact
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract, DatasetOptions
from statlab_contracts.envelope import ORCHESTRATOR_SUPPLIED, Failure, ToolFailure, ToolSuccess
from statlab_contracts.layout import RunPaths


class FakeSession:
    def __init__(self, tools: tuple[object, ...]) -> None:
        self.tools = tools
        self.list_count = 0
        self.calls: list[tuple[str, dict[str, JsonValue], float]] = []
        self.envelope: dict[str, JsonValue] = ToolSuccess().model_dump(mode="json")
        self.envelopes: list[dict[str, JsonValue]] = []
        self.exception: BaseException | None = None
        self.malformed_result = False

    async def list_tools(self) -> object:
        self.list_count += 1
        return SimpleNamespace(tools=self.tools)

    async def call_tool(
        self, name: str, arguments: dict[str, JsonValue], *, read_timeout_seconds: float
    ) -> CallToolResult:
        self.calls.append((name, dict(arguments), read_timeout_seconds))
        if self.exception is not None:
            raise self.exception
        if self.malformed_result:
            return CallToolResult(content=[], structuredContent=None)
        envelope = self.envelopes.pop(0) if self.envelopes else self.envelope
        return CallToolResult(content=[], structuredContent={"result": envelope})


def served_tool(
    name: str,
    schema: dict[str, object],
    *,
    allow_in: list[str] | None = None,
    phase: str = "intake",
    transform_only: bool = False,
) -> object:
    return SimpleNamespace(
        name=name,
        input_schema=schema,
        meta={
            "allow_in": ["intake"] if allow_in is None else allow_in,
            "phase": phase,
            "heavy": False,
            "transform_only": transform_only,
        },
    )


@pytest.fixture
def world(
    tmp_path: Path,
    migrated_db: sqlite3.Connection,
    project: object,
    minimal_contract: DataContract,
) -> tuple[sqlite3.Connection, RunPaths, DataContract, str]:
    from statlab.persistence.models import ProjectRow

    root = tmp_path
    project_row = cast(ProjectRow, project)
    contract_path = root / "contract.json"
    contract_row = create_contract(
        migrated_db,
        project_id=project_row.id,
        path=contract_path,
        contract=minimal_contract,
    )
    run = create_run(
        migrated_db,
        project_id=project_row.id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    paths = RunPaths(root / "data", project_row.slug, run.id)
    ensure_run_layout(paths)
    execution = create_node_execution(migrated_db, run_id=run.id, node_name="intake", seq=0)
    return migrated_db, paths, minimal_contract, execution.id


def context(
    paths: RunPaths,
    contract: DataContract,
    execution_id: str,
    allowed: frozenset[str] = frozenset({"echo"}),
    *,
    node: str = "intake",
    champion_selected: bool = False,
) -> CallContext:
    return CallContext(paths, node, execution_id, allowed, contract, "0" * 64, champion_selected)


def settings(paths: RunPaths, tmp_path: Path) -> Settings:
    return Settings(
        mcp_token=SecretStr("fixture-token"),
        data_dir=paths.data_dir,
        db_path=tmp_path / "settings.sqlite",
    )


@pytest.mark.asyncio
async def test_setup_dataset_uses_fixed_tool_and_skips_lineage(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "project": {"type": "string"},
            "dataset": {"type": "string"},
            "options": {"type": "object"},
        },
        "required": ["project", "dataset", "options"],
        "additionalProperties": False,
    }
    session = FakeSession(
        (served_tool("setup_dataset", schema, allow_in=["setup"], phase="setup"),)
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        result = await client.setup_dataset(
            project=paths.slug, dataset="a" * 64, options=DatasetOptions()
        )
    assert result.summary == {}
    assert session.calls == [
        (
            "setup_dataset",
            {
                "project": paths.slug,
                "dataset": "a" * 64,
                "options": DatasetOptions().model_dump(mode="json"),
            },
            900,
        )
    ]
    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0


_SETUP_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},
        "dataset": {"type": "string"},
        "options": {"type": "object"},
    },
    "required": ["project", "dataset", "options"],
    "additionalProperties": False,
}


@pytest.mark.asyncio
async def test_setup_dataset_requires_entered_client_and_existing_project(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    client = McpClient(settings(paths, tmp_path), conn, session=FakeSession(()))
    with pytest.raises(EngineError, match="MCP client is not entered"):
        await client.setup_dataset(project=paths.slug, dataset="a" * 64, options=DatasetOptions())
    session = FakeSession(
        (served_tool("setup_dataset", _SETUP_SCHEMA, allow_in=["setup"], phase="setup"),)
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as entered:
        with pytest.raises(EngineError, match="setup project does not exist"):
            await entered.setup_dataset(
                project="missing-project", dataset="a" * 64, options=DatasetOptions()
            )
    assert session.calls == []


@pytest.mark.asyncio
async def test_setup_dataset_refuses_non_finite_contract(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, _ = world
    session = FakeSession(
        (served_tool("setup_dataset", _SETUP_SCHEMA, allow_in=["setup"], phase="setup"),)
    )
    bad = DataContract.model_validate(
        {**contract.model_dump(mode="json"), "objective": {"success_threshold": float("nan")}}
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as caught:
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions(), contract=bad
            )
    failure = caught.value.failure
    assert failure.error_class == "tool_param"
    assert failure.message == "tool parameters must be strict JSON"
    assert session.calls == []


@pytest.mark.asyncio
async def test_setup_dataset_refuses_wrong_registry_phase_before_dispatch(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    schema: dict[str, object] = {"type": "object", "properties": {}}
    session = FakeSession((served_tool("setup_dataset", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(EngineError):
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert session.calls == []


@pytest.mark.parametrize(
    ("allow_in", "phase"),
    [(["setup"], "intake"), (["intake"], "setup")],
)
@pytest.mark.asyncio
async def test_setup_dataset_requires_exact_metadata(
    allow_in: list[str],
    phase: str,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, _, _ = world
    session = FakeSession(
        (
            served_tool(
                "setup_dataset",
                {"type": "object", "properties": {}},
                allow_in=allow_in,
                phase=phase,
            ),
        )
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(EngineError):
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert not session.calls


@pytest.mark.asyncio
async def test_setup_dataset_schema_failure_and_tool_failure_are_preserved(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"project": {"type": "string"}},
        "required": ["project"],
        "additionalProperties": False,
    }
    session = FakeSession(
        (served_tool("setup_dataset", schema, allow_in=["setup"], phase="setup"),)
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError):
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert not session.calls

    session = FakeSession(
        (
            served_tool(
                "setup_dataset",
                {"type": "object", "properties": {}},
                allow_in=["setup"],
                phase="setup",
            ),
        )
    )
    session.envelope = ToolFailure(error_class="data", message="bad CSV").model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert failure.value.failure.error_class == "data"


@pytest.mark.asyncio
async def test_setup_dataset_preserves_cancellation(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    session = FakeSession(
        (
            served_tool(
                "setup_dataset",
                {"type": "object", "properties": {}},
                allow_in=["setup"],
                phase="setup",
            ),
        )
    )
    session.exception = asyncio.CancelledError()
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )


@pytest.mark.asyncio
async def test_setup_dataset_rejects_malformed_results_and_artifacts(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    tool = served_tool(
        "setup_dataset", {"type": "object", "properties": {}}, allow_in=["setup"], phase="setup"
    )
    session = FakeSession((tool,))
    session.malformed_result = True
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert failure.value.failure.error_class == "bug"

    session = FakeSession((tool,))
    session.envelope = {"unexpected": "secret"}
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert failure.value.failure.error_class == "bug"

    ref = new_artifact_ref()
    session = FakeSession((tool,))
    session.envelope = ToolSuccess(artifacts=(ArtifactMeta(ref=ref, kind="dataframe"),)).model_dump(
        mode="json"
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert failure.value.failure.error_class == "bug"


@pytest.mark.parametrize(
    ("exception", "error_class"),
    [
        (TimeoutError("setup timeout"), "transient"),
        (MCPError(-32000, "setup connection"), "transient"),
        (ValueError("setup bug"), "bug"),
    ],
)
@pytest.mark.asyncio
async def test_setup_dataset_maps_foreign_dispatch_failures_without_audit(
    exception: Exception,
    error_class: str,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, _, _ = world
    session = FakeSession(
        (
            served_tool(
                "setup_dataset",
                {"type": "object", "properties": {}},
                allow_in=["setup"],
                phase="setup",
            ),
        )
    )
    session.exception = exception
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert failure.value.failure.error_class == error_class
    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_setup_dataset_missing_tool_fails_before_dispatch(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    session = FakeSession((served_tool("other", {"type": "object", "properties": {}}),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(EngineError):
            await client.setup_dataset(
                project=paths.slug, dataset="a" * 64, options=DatasetOptions()
            )
    assert session.calls == []


@pytest.mark.asyncio
async def test_discovery_is_cached_and_orchestrator_values_replace_agent_values(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            name: {"type": "string", "description": ORCHESTRATOR_SUPPLIED}
            for name in ("project", "run", "dataset")
        }
        | {
            "seed": {"type": "integer", "description": ORCHESTRATOR_SUPPLIED},
            "value": {"type": "integer"},
        },
        "required": ["project", "run", "dataset", "seed", "value"],
        "additionalProperties": False,
    }
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        original: dict[str, JsonValue] = {
            "project": "spoof",
            "run": "spoof",
            "dataset": "spoof",
            "seed": 999,
            "value": 3,
        }
        await client.call(context(paths, contract, execution_id), "echo", original)
        await client.call(context(paths, contract, execution_id), "echo", {**original})
    sent = session.calls[0][1]
    assert sent["project"] == paths.slug
    assert sent["run"] == paths.run_id
    assert sent["dataset"] == "0" * 64
    assert sent["seed"] == contract.constraints.random_seed
    assert original["seed"] == 999
    assert session.list_count == 1
    assert all(timeout == 900 for _, _, timeout in session.calls)


@pytest.mark.asyncio
async def test_split_and_non_split_seed_sources_differ_and_are_logged(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="statlab.mcp_client")
    conn, paths, original_contract, _ = world
    original_run = get_run(conn, paths.run_id)
    assert original_run is not None
    contract = original_contract.model_copy(
        update={
            "split": original_contract.split.model_copy(update={"seed": 11}),
            "constraints": original_contract.constraints.model_copy(update={"random_seed": 73}),
        }
    )
    contract_row = create_contract(
        conn,
        project_id=original_run.project_id,
        path=tmp_path / "distinct-seeds.json",
        contract=contract,
    )
    run = create_run(
        conn,
        project_id=original_run.project_id,
        contract_id=contract_row.id,
        mode="autonomous",
        config_json="{}",
    )
    seeded_paths = RunPaths(paths.data_dir, paths.slug, run.id)
    ensure_run_layout(seeded_paths)
    split_execution = create_node_execution(conn, run_id=run.id, node_name="data_splitting", seq=3)
    model_execution = create_node_execution(conn, run_id=run.id, node_name="model_training", seq=8)
    seed_schema: dict[str, object] = {
        "type": "object",
        "properties": {"seed": {"type": "integer", "description": ORCHESTRATOR_SUPPLIED}},
        "required": ["seed"],
    }
    session = FakeSession(
        (
            served_tool(
                "split_random", seed_schema, allow_in=["data_splitting"], phase="splitting"
            ),
            served_tool(
                "train_estimator", seed_schema, allow_in=["model_training"], phase="training"
            ),
        )
    )
    async with McpClient(settings(seeded_paths, tmp_path), conn, session=session) as client:
        await client.call(
            context(
                seeded_paths,
                contract,
                split_execution.id,
                frozenset({"split_random"}),
                node="data_splitting",
            ),
            "split_random",
            {"seed": 900},
        )
        await client.call(
            context(
                seeded_paths,
                contract,
                model_execution.id,
                frozenset({"train_estimator"}),
                node="model_training",
            ),
            "train_estimator",
            {"seed": 900},
        )
    assert [arguments["seed"] for _, arguments, _ in session.calls] == [11, 73]
    assert [call.arguments["seed"] for call in logged_calls(conn, run.id)] == [11, 73]
    assert "Tool call run=" in caplog.text
    assert "Tool result tool=" in caplog.text


@pytest.mark.asyncio
async def test_both_tool_allow_lists_and_exact_wildcard_are_enforced(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession(
        (
            served_tool("echo", schema, allow_in=["*"]),
            served_tool("restricted", schema, allow_in=["intake"]),
        )
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        assert "echo" in client.allowed_tools("another-node")
        denied = context(paths, contract, execution_id, frozenset())
        with pytest.raises(ToolNotAllowedError):
            await client.call(denied, "echo", {})
        wildcard_ctx = context(paths, contract, execution_id, frozenset({"*"}))
        with pytest.raises(ToolNotAllowedError):
            await client.call(wildcard_ctx, "echo", {})
        split_execution = create_node_execution(
            conn, run_id=paths.run_id, node_name="data_splitting", seq=1
        )
        denied = context(
            paths, contract, split_execution.id, frozenset({"restricted"}), node="data_splitting"
        )
        with pytest.raises(ToolNotAllowedError):
            await client.call(denied, "restricted", {})
    assert not session.calls


@pytest.mark.asyncio
async def test_run_execution_and_contract_context_are_attributed_before_dispatch(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(EngineError, match="project"):
            await client.call(
                context(RunPaths(paths.data_dir, "other", paths.run_id), contract, execution_id),
                "echo",
                {},
            )
        altered = contract.model_copy(update={"notes": "different contract"})
        with pytest.raises(EngineError, match="contract"):
            await client.call(context(paths, altered, execution_id), "echo", {})
        unmatched_execution = create_node_execution(
            conn, run_id=paths.run_id, node_name="data_profiling", seq=2
        )
        with pytest.raises(EngineError, match="does not match"):
            await client.call(context(paths, contract, unmatched_execution.id), "echo", {})
        with pytest.raises(EngineError, match="does not belong"):
            await client.call(context(paths, contract, "ne_missing"), "echo", {})
    assert not session.calls


@pytest.mark.asyncio
async def test_removed_run_and_missing_session_fail_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    from statlab.persistence.repository import get_run as original_get_run

    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    calls = 0

    def removed_after_context(conn_arg: sqlite3.Connection, run_id: str) -> object:
        nonlocal calls
        calls += 1
        return original_get_run(conn_arg, run_id) if calls == 1 else None

    monkeypatch.setattr("statlab.mcp_client.get_run", removed_after_context)
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(EngineError, match="does not exist"):
            await client.call(context(paths, contract, execution_id), "echo", {})
    monkeypatch.undo()
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        client._session = None
        with pytest.raises(EngineError, match="not entered"):
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert not session.calls


@pytest.mark.asyncio
async def test_select_champion_is_refused_after_durable_test_claim(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, _ = world
    assert claim_test_scoring(conn, paths.run_id)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession(
        (served_tool("select_champion", schema, allow_in=["champion_selection"], phase="champion"),)
    )
    execution = create_node_execution(
        conn, run_id=paths.run_id, node_name="champion_selection", seq=1
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        ctx = context(
            paths,
            contract,
            execution.id,
            frozenset({"select_champion"}),
            node="champion_selection",
            champion_selected=True,
        )
        with pytest.raises(EngineError, match="after test scoring"):
            await client.call(ctx, "select_champion", {})
    assert not session.calls


@pytest.mark.asyncio
async def test_schema_refusal_precedes_dispatch_and_repair_reenters_client(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        ctx = context(paths, contract, execution_id)
        with pytest.raises(ToolCallError) as refused:
            await client.call(ctx, "echo", {})
        assert refused.value.failure.field_errors == {"params": "'value' is a required property"}

        async def repair(failure: Failure) -> dict[str, JsonValue]:
            assert failure.field_errors is not None and "params" in failure.field_errors
            return {"value": 7}

        await client.call(ctx, "echo", {}, repair=repair)
    assert len(session.calls) == 1
    rows = conn.execute("SELECT COUNT(*) FROM tool_calls WHERE run_id = ?", (paths.run_id,))
    assert rows.fetchone()[0] == 1


@pytest.mark.asyncio
async def test_tool_param_failure_repair_creates_two_independent_audit_rows(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }
    session = FakeSession((served_tool("echo", schema),))
    session.envelopes = [
        ToolFailure(
            error_class="tool_param",
            message="invalid value",
            field_errors={"value": "must be positive"},
        ).model_dump(mode="json"),
        ToolSuccess(summary={"accepted": True}).model_dump(mode="json"),
    ]
    repair_count = 0

    async def repair(failure: Failure) -> dict[str, JsonValue]:
        nonlocal repair_count
        repair_count += 1
        assert failure.field_errors == {"value": "must be positive"}
        return {"value": 2}

    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        await client.call(
            context(paths, contract, execution_id),
            "echo",
            {"value": 1},
            repair=repair,
        )
    rows = logged_calls(conn, paths.run_id)
    assert repair_count == 1 and len(rows) == 2
    assert rows[0].arguments == {"value": 1} and rows[0].outputs == ()
    assert rows[1].arguments == {"value": 2}


@pytest.mark.asyncio
async def test_schema_refusal_preserves_all_missing_root_and_nested_fields(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "first": {"type": "string"},
            "second": {"type": "string"},
            "options": {
                "type": "object",
                "properties": {"left": {"type": "integer"}, "right": {"type": "integer"}},
                "required": ["left", "right"],
            },
        },
        "required": ["first", "second", "options"],
    }
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as refused:
            await client.call(context(paths, contract, execution_id), "echo", {"options": {}})
    errors = refused.value.failure.field_errors
    assert errors is not None
    assert "first" in errors["params"] and "second" in errors["params"]
    assert "left" in errors["options"] and "right" in errors["options"]
    assert not session.calls


@pytest.mark.parametrize("code", [-32000, -32001])
@pytest.mark.asyncio
async def test_actual_mcp_closed_and_timeout_errors_are_transient(
    code: int,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    session.exception = MCPError(code, "transport error")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert failure.value.failure.error_class == "transient"
    logged = logged_calls(conn, paths.run_id)
    assert len(logged) == 1 and logged[0].outputs == ()


@pytest.mark.asyncio
async def test_builtin_timeout_maps_to_transient_and_is_logged(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    session.exception = TimeoutError("timed out")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert failure.value.failure.error_class == "transient"
    assert len(logged_calls(conn, paths.run_id)) == 1


@pytest.mark.parametrize("exception_type", [OSError, RuntimeError])
@pytest.mark.asyncio
async def test_dispatch_exception_text_is_not_persisted(
    exception_type: type[Exception],
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    configured = settings(paths, tmp_path)
    secret_text = configured.mcp_token.get_secret_value()
    session.exception = exception_type(f"request failed with bearer token {secret_text}")
    async with McpClient(configured, conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert secret_text not in failure.value.failure.message
    assert secret_text not in str(failure.value)
    stored = conn.execute(
        "SELECT result_json FROM tool_calls WHERE run_id = ?", (paths.run_id,)
    ).fetchone()["result_json"]
    assert secret_text not in stored
    assert "MCP dispatch failed" in stored


@pytest.mark.asyncio
async def test_missing_structured_result_becomes_logged_bug_failure(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    session.malformed_result = True
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as failure:
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert failure.value.failure.error_class == "bug"
    assert len(logged_calls(conn, paths.run_id)) == 1


@pytest.mark.asyncio
async def test_cancelled_dispatch_is_audited_then_cancellation_propagates(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    session.exception = asyncio.CancelledError()
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert len(logged_calls(conn, paths.run_id)) == 1


@pytest.mark.asyncio
async def test_cancelled_dispatch_survives_audit_failure_without_exposing_cause(
    monkeypatch: pytest.MonkeyPatch,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, contract, execution_id = world
    configured = settings(paths, tmp_path)
    secret_text = configured.mcp_token.get_secret_value()
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    cancellation = asyncio.CancelledError()
    session.exception = cancellation

    def fail_record_tool_call(
        conn_arg: sqlite3.Connection,
        *,
        run_id: str,
        node_execution_id: str,
        tool_name: str,
        args_json: str,
        result_json: str,
        duration_ms: int,
        ts: str | None = None,
    ) -> NoReturn:
        raise PersistenceError("cannot record tool call", cause=OSError(secret_text))

    monkeypatch.setattr("statlab.mcp_client.record_tool_call", fail_record_tool_call)
    async with McpClient(configured, conn, session=session) as client:
        with pytest.raises(asyncio.CancelledError) as raised:
            await client.call(context(paths, contract, execution_id), "echo", {})

    assert raised.value is cancellation
    assert "MCP dispatch cancellation audit logging failed" in raised.value.__notes__
    assert secret_text not in str(raised.value)
    assert all(secret_text not in note for note in raised.value.__notes__)


@pytest.mark.asyncio
async def test_invalid_discovery_metadata_and_schema_fail_context_entry(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    malformed = cast(SimpleNamespace, served_tool("echo", {"type": "object"}))
    malformed.meta["allow_in"] = "intake"
    with pytest.raises(EngineError):
        async with McpClient(settings(paths, tmp_path), conn, session=FakeSession((malformed,))):
            pass
    bad_schema = served_tool("echo", {"type": "not-a-json-schema-type", "properties": {}})
    with pytest.raises(EngineError):
        async with McpClient(settings(paths, tmp_path), conn, session=FakeSession((bad_schema,))):
            pass
    bad_meta = cast(SimpleNamespace, served_tool("echo", {"type": "object"}))
    bad_meta.meta = "not an object"
    with pytest.raises(EngineError, match="malformed metadata"):
        async with McpClient(settings(paths, tmp_path), conn, session=FakeSession((bad_meta,))):
            pass


@pytest.mark.asyncio
async def test_unexpected_discovery_error_is_wrapped_and_client_can_exit_unentered(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world

    class BrokenDiscovery:
        async def list_tools(self) -> object:
            raise OSError("list failed")

    with pytest.raises(EngineError, match="initialize or discover"):
        async with McpClient(settings(paths, tmp_path), conn, session=BrokenDiscovery()):
            pass
    unentered = McpClient(settings(paths, tmp_path), conn, session=BrokenDiscovery())
    await unentered.__aexit__(None, None, None)
    with pytest.raises(EngineError, match="not entered"):
        await unentered._discover()


@pytest.mark.asyncio
async def test_transport_cleanup_failure_without_body_error_is_engine_error(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    client = McpClient(settings(paths, tmp_path), conn, session=FakeSession(()))
    await client.__aenter__()

    class FailingStack:
        async def aclose(self) -> None:
            raise OSError("close failed token=credential-value")

    client._stack = cast(AsyncExitStack, FailingStack())
    with pytest.raises(EngineError, match="failed to close") as raised:
        await client.__aexit__(None, None, None)
    assert "credential-value" not in str(raised.value)


@pytest.mark.asyncio
async def test_schema_local_defs_work_and_broken_reference_is_engine_error(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "$defs": {"count": {"type": "integer", "minimum": 1, "maximum": 4}},
        "properties": {"value": {"$ref": "#/$defs/count"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    valid_session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=valid_session) as client:
        with pytest.raises(ToolCallError):
            await client.call(context(paths, contract, execution_id), "echo", {"value": 8})
        await client.call(context(paths, contract, execution_id), "echo", {"value": 3})
    assert len(valid_session.calls) == 1

    broken_schema: dict[str, object] = {
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/missing"}},
        "required": ["value"],
    }
    broken_session = FakeSession((served_tool("echo", broken_schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=broken_session) as client:
        with pytest.raises(EngineError, match="schema"):
            await client.call(context(paths, contract, execution_id), "echo", {"value": 1})
    assert not broken_session.calls


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf")])
@pytest.mark.asyncio
async def test_non_finite_json_values_refuse_before_claim_and_repair_once(
    non_finite: float,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, contract, _ = world
    ref = new_artifact_ref()
    path = paths.artifact(ref, "dataframe")
    path.write_bytes(b"test")
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="test")
    create_artifact(
        conn,
        ref=ref,
        run_id=paths.run_id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="fixture",
        split="test",
    )
    execution = create_node_execution(
        conn, run_id=paths.run_id, node_name="champion_selection", seq=1
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"truth": {"type": "string"}, "metric": {"type": "number"}},
        "required": ["truth", "metric"],
    }
    session = FakeSession(
        (served_tool("score_classification", schema, allow_in=["champion_selection"]),)
    )
    ctx = context(
        paths,
        contract,
        execution.id,
        frozenset({"score_classification"}),
        node="champion_selection",
        champion_selected=True,
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as refused:
            await client.call(
                ctx, "score_classification", {"truth": str(ref), "metric": non_finite}
            )
        assert refused.value.failure.error_class == "tool_param"
        run = get_run(conn, paths.run_id)
        assert run is not None and run.test_scored_at is None
        assert not session.calls
        repaired = False

        async def repair(failure: Failure) -> dict[str, JsonValue]:
            nonlocal repaired
            repaired = True
            assert failure.error_class == "tool_param"
            return {"truth": str(ref), "metric": 0.5}

        await client.call(
            ctx,
            "score_classification",
            {"truth": str(ref), "metric": non_finite},
            repair=repair,
        )
        assert repaired
    claimed = get_run(conn, paths.run_id)
    assert claimed is not None and claimed.test_scored_at is not None
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_closed_database_is_mapped_to_persistence_error_before_dispatch(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    conn.close()
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(PersistenceError):
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert not session.calls
    with pytest.raises(PersistenceError, match="cannot read tool call log"):
        logged_calls(conn, paths.run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [EngineError("body failed"), asyncio.CancelledError()])
async def test_transport_cleanup_does_not_mask_body_errors(
    failure: BaseException,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, _, _ = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    client = McpClient(
        settings(paths, tmp_path), conn, session=FakeSession((served_tool("echo", schema),))
    )
    await client.__aenter__()

    class FailingStack:
        async def aclose(self) -> None:
            raise OSError("close failed token=credential-value")

    client._stack = cast(AsyncExitStack, FailingStack())
    await client.__aexit__(type(failure), failure, failure.__traceback__)
    assert "MCP transport cleanup failed" in failure.__notes__
    assert all("credential-value" not in note for note in failure.__notes__)


@pytest.mark.asyncio
async def test_trusted_fields_are_separate_and_missing_trusted_data_refuses(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "calls": {
                "type": "array",
                "description": ORCHESTRATOR_SUPPLIED,
                "items": {"type": "object"},
            }
        },
        "required": ["calls"],
        "additionalProperties": False,
    }
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        ctx = context(paths, contract, execution_id)
        with pytest.raises(EngineError):
            await client.call(ctx, "echo", {})
        await client.call(ctx, "echo", {}, trusted={"calls": [{"tool": "ok"}]})
    assert session.calls[0][1] == {"calls": [{"tool": "ok"}]}


@pytest.mark.asyncio
async def test_test_artifact_taint_is_found_inside_nested_values_before_dispatch(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, _ = world
    ref = new_artifact_ref()
    path = paths.artifact(ref, "dataframe")
    path.write_bytes(b"artifact")
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="test")
    create_artifact(
        conn,
        ref=ref,
        run_id=paths.run_id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="fixture",
        split="test",
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"config": {"type": "object"}},
        "required": ["config"],
        "additionalProperties": False,
    }
    session = FakeSession((served_tool("fit_model", schema, allow_in=["model_training"]),))
    execution = create_node_execution(conn, run_id=paths.run_id, node_name="model_training", seq=1)
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        ctx = context(
            paths, contract, execution.id, frozenset({"fit_model"}), node="model_training"
        )
        with pytest.raises(ArtifactLockedError):
            await client.call(ctx, "fit_model", {"config": {"nested": [str(ref)]}})
    assert not session.calls
    run = get_run(conn, paths.run_id)
    assert run is not None and run.test_scored_at is None


@pytest.mark.asyncio
async def test_tainted_input_propagates_to_output_and_later_score_claim(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, _ = world
    test_ref = new_artifact_ref()
    test_path = paths.artifact(test_ref, "dataframe")
    test_path.write_bytes(b"test")
    test_meta = ArtifactMeta(ref=test_ref, kind="dataframe", split="test")
    create_artifact(
        conn,
        ref=test_ref,
        run_id=paths.run_id,
        kind="dataframe",
        path=test_path,
        meta=test_meta,
        produced_by="fixture",
        split="test",
    )
    output_ref = new_artifact_ref()
    output_path = paths.artifact(output_ref, "dataframe")
    output_path.write_bytes(b"transformed")
    output_meta = ArtifactMeta(ref=output_ref, kind="dataframe")
    execution = create_node_execution(
        conn, run_id=paths.run_id, node_name="champion_selection", seq=1
    )
    frame_schema: dict[str, object] = {
        "type": "object",
        "properties": {"frame": {"type": "string"}},
        "required": ["frame"],
    }
    session = FakeSession(
        (
            served_tool(
                "apply_scaler",
                frame_schema,
                allow_in=["champion_selection"],
                transform_only=True,
                phase="encoding",
            ),
            served_tool(
                "score_classification",
                frame_schema,
                allow_in=["champion_selection"],
                phase="evaluation",
            ),
            served_tool(
                "score_regression",
                frame_schema,
                allow_in=["champion_selection"],
                phase="evaluation",
            ),
        )
    )
    session.envelopes = [
        ToolSuccess(artifacts=(output_meta,)).model_dump(mode="json"),
        ToolSuccess().model_dump(mode="json"),
    ]
    ctx = context(
        paths,
        contract,
        execution.id,
        frozenset({"apply_scaler", "score_classification", "score_regression"}),
        node="champion_selection",
        champion_selected=True,
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        await client.call(ctx, "apply_scaler", {"frame": str(test_ref)})
        output_row = get_artifact(conn, str(output_ref))
        assert output_row is not None and output_row.derived_from_test
        await client.call(ctx, "score_classification", {"frame": str(output_ref)})
        with pytest.raises(ArtifactLockedError):
            await client.call(ctx, "score_regression", {"frame": str(output_ref)})
    run = get_run(conn, paths.run_id)
    assert run is not None and run.test_scored_at is not None
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_multiple_fork_ancestors_require_child_link_before_tainted_dispatch(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, parent_paths, contract, _ = world
    parent = get_run(conn, parent_paths.run_id)
    assert parent is not None
    ref = new_artifact_ref()
    artifact_path = parent_paths.artifact(ref, "dataframe")
    artifact_path.write_bytes(b"test-set")
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="test")
    create_artifact(
        conn,
        ref=ref,
        run_id=parent_paths.run_id,
        kind="dataframe",
        path=artifact_path,
        meta=meta,
        produced_by="fixture",
        split="test",
    )
    artifact = get_artifact(conn, str(ref))
    assert artifact is not None
    project = get_project_by_slug(conn, parent_paths.slug)
    assert project is not None

    middle = create_run(
        conn,
        project_id=project.id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=parent.id,
    )
    middle_paths = RunPaths(parent_paths.data_dir, parent_paths.slug, middle.id)
    ensure_run_layout(middle_paths)
    assert fork_artifacts(parent_paths, middle_paths, [artifact]) == (ref,)
    child = create_run(
        conn,
        project_id=project.id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=middle.id,
    )
    child_paths = RunPaths(parent_paths.data_dir, parent_paths.slug, child.id)
    ensure_run_layout(child_paths)
    assert fork_artifacts(middle_paths, child_paths, [artifact]) == (ref,)
    child_execution = create_node_execution(
        conn, run_id=child.id, node_name="champion_selection", seq=11
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"frame": {"type": "string"}},
        "required": ["frame"],
    }
    session = FakeSession(
        (
            served_tool(
                "score_classification", schema, allow_in=["champion_selection"], phase="evaluation"
            ),
        )
    )
    async with McpClient(settings(child_paths, tmp_path), conn, session=session) as client:
        ctx = context(
            child_paths,
            contract,
            child_execution.id,
            frozenset({"score_classification"}),
            node="champion_selection",
            champion_selected=True,
        )
        await client.call(ctx, "score_classification", {"frame": str(ref)})
    claimed = get_run(conn, child.id)
    assert claimed is not None and claimed.test_scored_at is not None
    assert len(session.calls) == 1

    unlinked = create_run(
        conn,
        project_id=project.id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=middle.id,
    )
    unlinked_paths = RunPaths(parent_paths.data_dir, parent_paths.slug, unlinked.id)
    ensure_run_layout(unlinked_paths)
    unlinked_execution = create_node_execution(
        conn, run_id=unlinked.id, node_name="champion_selection", seq=11
    )
    denied_session = FakeSession(
        (
            served_tool(
                "score_classification", schema, allow_in=["champion_selection"], phase="evaluation"
            ),
        )
    )
    async with McpClient(
        settings(unlinked_paths, tmp_path), conn, session=denied_session
    ) as client:
        ctx = context(
            unlinked_paths,
            contract,
            unlinked_execution.id,
            frozenset({"score_classification"}),
            node="champion_selection",
            champion_selected=True,
        )
        with pytest.raises(ArtifactError):
            await client.call(ctx, "score_classification", {"frame": str(ref)})
    assert not denied_session.calls


@pytest.mark.asyncio
async def test_cross_project_intermediate_fork_ancestor_is_rejected(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, parent_paths, contract, _ = world
    parent = get_run(conn, parent_paths.run_id)
    assert parent is not None
    ref = new_artifact_ref()
    artifact_path = parent_paths.artifact(ref, "dataframe")
    artifact_path.write_bytes(b"ancestor")
    meta = ArtifactMeta(ref=ref, kind="dataframe")
    create_artifact(
        conn,
        ref=ref,
        run_id=parent_paths.run_id,
        kind="dataframe",
        path=artifact_path,
        meta=meta,
        produced_by="fixture",
    )
    foreign_project = create_project(conn, slug="foreign", name="Foreign project")
    foreign_contract = create_contract(
        conn,
        project_id=foreign_project.id,
        path=tmp_path / "foreign-contract.json",
        contract=contract,
    )
    middle = create_run(
        conn,
        project_id=foreign_project.id,
        contract_id=foreign_contract.id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=parent.id,
    )
    child = create_run(
        conn,
        project_id=parent.project_id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=middle.id,
    )
    child_paths = RunPaths(parent_paths.data_dir, parent_paths.slug, child.id)
    ensure_run_layout(child_paths)
    artifact = get_artifact(conn, str(ref))
    assert artifact is not None
    assert fork_artifacts(parent_paths, child_paths, [artifact]) == (ref,)
    execution = create_node_execution(conn, run_id=child.id, node_name="intake", seq=0)
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"frame": {"type": "string"}},
        "required": ["frame"],
    }
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(child_paths, tmp_path), conn, session=session) as client:
        with pytest.raises(PersistenceError, match="ancestry crosses projects"):
            await client.call(
                context(child_paths, contract, execution.id), "echo", {"frame": str(ref)}
            )
    assert not session.calls


@pytest.mark.asyncio
async def test_missing_fork_ancestor_row_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    from statlab.persistence.repository import get_run as original_get_run

    conn, parent_paths, contract, _ = world
    parent = get_run(conn, parent_paths.run_id)
    assert parent is not None
    ref = new_artifact_ref()
    path = parent_paths.artifact(ref, "dataframe")
    path.write_bytes(b"ancestor")
    meta = ArtifactMeta(ref=ref, kind="dataframe")
    create_artifact(
        conn,
        ref=ref,
        run_id=parent.id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="fixture",
    )
    child = create_run(
        conn,
        project_id=parent.project_id,
        contract_id=parent.contract_id,
        mode="autonomous",
        config_json="{}",
        forked_from_run_id=parent.id,
    )
    child_paths = RunPaths(parent_paths.data_dir, parent_paths.slug, child.id)
    ensure_run_layout(child_paths)
    execution = create_node_execution(conn, run_id=child.id, node_name="intake", seq=0)

    def vanished_ancestor(conn_arg: sqlite3.Connection, run_id: str) -> RunRow | None:
        if run_id == parent.id:
            return None
        return original_get_run(conn_arg, run_id)

    monkeypatch.setattr("statlab.persistence.repository.get_run", vanished_ancestor)
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"frame": {"type": "string"}},
        "required": ["frame"],
    }
    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(child_paths, tmp_path), conn, session=session) as client:
        with pytest.raises(PersistenceError, match="ancestry is incomplete"):
            await client.call(
                context(child_paths, contract, execution.id), "echo", {"frame": str(ref)}
            )
    assert not session.calls


@pytest.mark.asyncio
async def test_unknown_pruned_foreign_and_unrelated_refs_are_refused(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"frame": {"type": "string"}},
        "required": ["frame"],
    }
    local_ref = new_artifact_ref()
    local_path = paths.artifact(local_ref, "dataframe")
    local_path.write_bytes(b"pruned")
    local_meta = ArtifactMeta(ref=local_ref, kind="dataframe")
    create_artifact(
        conn,
        ref=local_ref,
        run_id=paths.run_id,
        kind="dataframe",
        path=local_path,
        meta=local_meta,
        produced_by="fixture",
    )
    conn.execute("UPDATE artifacts SET pruned_at = 'now' WHERE id = ?", (str(local_ref),))

    run = get_run(conn, paths.run_id)
    assert run is not None
    unrelated = create_run(
        conn,
        project_id=run.project_id,
        contract_id=run.contract_id,
        mode="autonomous",
        config_json="{}",
    )
    unrelated_paths = RunPaths(paths.data_dir, paths.slug, unrelated.id)
    ensure_run_layout(unrelated_paths)
    unrelated_ref = new_artifact_ref()
    unrelated_path = unrelated_paths.artifact(unrelated_ref, "dataframe")
    unrelated_path.write_bytes(b"unrelated")
    unrelated_meta = ArtifactMeta(ref=unrelated_ref, kind="dataframe")
    create_artifact(
        conn,
        ref=unrelated_ref,
        run_id=unrelated.id,
        kind="dataframe",
        path=unrelated_path,
        meta=unrelated_meta,
        produced_by="fixture",
    )

    foreign_project = create_project(conn, slug="foreign", name="Foreign project")
    foreign_contract = create_contract(
        conn,
        project_id=foreign_project.id,
        path=tmp_path / "foreign-contract.json",
        contract=contract,
    )
    foreign_run = create_run(
        conn,
        project_id=foreign_project.id,
        contract_id=foreign_contract.id,
        mode="autonomous",
        config_json="{}",
    )
    foreign_paths = RunPaths(paths.data_dir, foreign_project.slug, foreign_run.id)
    ensure_run_layout(foreign_paths)
    foreign_ref = new_artifact_ref()
    foreign_path = foreign_paths.artifact(foreign_ref, "dataframe")
    foreign_path.write_bytes(b"foreign")
    foreign_meta = ArtifactMeta(ref=foreign_ref, kind="dataframe")
    create_artifact(
        conn,
        ref=foreign_ref,
        run_id=foreign_run.id,
        kind="dataframe",
        path=foreign_path,
        meta=foreign_meta,
        produced_by="fixture",
    )

    session = FakeSession((served_tool("echo", schema),))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        ctx = context(paths, contract, execution_id)
        with pytest.raises(ArtifactError, match="unknown or pruned"):
            await client.call(ctx, "echo", {"frame": str(new_artifact_ref())})
        with pytest.raises(ArtifactError, match="unknown or pruned"):
            await client.call(ctx, "echo", {"frame": str(local_ref)})
        with pytest.raises(ArtifactError, match="outside this run's fork lineage"):
            await client.call(ctx, "echo", {"frame": str(unrelated_ref)})
        with pytest.raises(ArtifactError, match="another project"):
            await client.call(ctx, "echo", {"frame": str(foreign_ref)})
    assert not session.calls


def test_domain_lineage_rejects_unknown_node_and_champion_test_without_selection() -> None:
    with pytest.raises(EngineError):
        check_lineage("unknown", "read", {}, False, transform_only=False, phase="intake")
    with pytest.raises(ArtifactLockedError):
        check_lineage(
            "champion_selection",
            "apply_scaler",
            {"frame": "a_test"},
            False,
            transform_only=True,
            phase="cleaning",
            champion_selected=False,
        )


def test_domain_lineage_rejects_train_read_after_champion_selection() -> None:
    with pytest.raises(ArtifactLockedError):
        check_lineage(
            "artifact_persistence",
            "read_artifact",
            {"train": "r"},
            False,
            transform_only=False,
            phase="artifact",
            champion_selected=True,
        )


@pytest.mark.parametrize(
    ("node", "tool", "tainted", "scored", "transform", "phase", "selected"),
    [
        ("preprocessing", "apply_imputer", {"frame": "r"}, False, True, "cleaning", False),
        ("champion_selection", "fit_scaler", {"train": "r"}, False, False, "encoding", True),
        ("preprocessing", "train_estimator", {"train": "r"}, False, False, "training", False),
        ("champion_selection", "train_estimator", {"frame": "r"}, False, False, "training", True),
        (
            "champion_selection",
            "score_classification",
            {"truth": "r"},
            True,
            False,
            "evaluation",
            True,
        ),
        ("champion_selection", "apply_imputer", {"train": "r"}, False, True, "cleaning", True),
        ("artifact_persistence", "fit_ols", {"matrix": "r"}, False, False, "inference", False),
        ("model_interpretation", "predict", {"frame": "r"}, False, True, "training", False),
        (
            "artifact_persistence",
            "score_regression",
            {"truth": "r"},
            True,
            False,
            "evaluation",
            False,
        ),
        (
            "model_evaluation",
            "render_metrics_table",
            {"scores": "r"},
            False,
            False,
            "reporting",
            False,
        ),
        (
            "model_interpretation",
            "assemble_report",
            {"test_score": "r"},
            False,
            False,
            "reporting",
            False,
        ),
        ("champion_selection", "select_champion", {"ranking": "r"}, False, False, "champion", True),
        (
            "champion_selection",
            "render_metrics_table",
            {"scores": "r"},
            False,
            False,
            "reporting",
            True,
        ),
    ],
)
def test_domain_lineage_refusals(
    node: str,
    tool: str,
    tainted: dict[str, str],
    scored: bool,
    transform: bool,
    phase: str,
    selected: bool,
) -> None:
    with pytest.raises(ArtifactLockedError):
        check_lineage(
            node,
            tool,
            tainted,
            scored,
            transform_only=transform,
            phase=phase,
            champion_selected=selected,
        )


@pytest.mark.parametrize(
    ("node", "tool", "tainted", "scored", "transform", "phase", "selected"),
    [
        ("data_splitting", "verify_split", {"test": "r"}, False, False, "splitting", False),
        ("champion_selection", "apply_scaler", {"frame": "r"}, False, True, "encoding", True),
        ("champion_selection", "predict_proba", {"frame": "r"}, False, True, "training", True),
        (
            "champion_selection",
            "score_classification",
            {"truth": "r"},
            False,
            False,
            "evaluation",
            True,
        ),
        (
            "artifact_persistence",
            "describe_artifact",
            {"artifact": "r"},
            True,
            False,
            "artifact",
            False,
        ),
        (
            "champion_selection",
            "render_metrics_table",
            {"scores": "r"},
            True,
            False,
            "reporting",
            True,
        ),
        (
            "champion_selection",
            "assemble_report",
            {"test_score": "r"},
            True,
            False,
            "reporting",
            True,
        ),
    ],
)
def test_domain_lineage_permits(
    node: str,
    tool: str,
    tainted: dict[str, str],
    scored: bool,
    transform: bool,
    phase: str,
    selected: bool,
) -> None:
    check_lineage(
        node,
        tool,
        tainted,
        scored,
        transform_only=transform,
        phase=phase,
        champion_selected=selected,
    )


@pytest.mark.asyncio
async def test_failed_test_score_claim_is_durable_and_survives_new_client(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, _ = world
    ref = new_artifact_ref()
    path = paths.artifact(ref, "dataframe")
    path.write_bytes(b"test")
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="test")
    create_artifact(
        conn,
        ref=ref,
        run_id=paths.run_id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="fixture",
        split="test",
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"truth": {"type": "string"}},
        "required": ["truth"],
        "additionalProperties": False,
    }
    execution = create_node_execution(
        conn, run_id=paths.run_id, node_name="champion_selection", seq=1
    )
    fake = FakeSession((served_tool("score_test", schema, allow_in=["champion_selection"]),))
    fake.envelope = ToolFailure(
        error_class="tool_param", message="invalid", field_errors={"truth": "invalid"}
    ).model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=fake) as client:
        ctx = context(
            paths,
            contract,
            execution.id,
            frozenset({"score_test"}),
            node="champion_selection",
            champion_selected=True,
        )
        with pytest.raises(ToolCallError):
            await client.call(ctx, "score_test", {"truth": str(ref)})
    claimed = get_run(conn, paths.run_id)
    assert claimed is not None and claimed.test_scored_at is not None
    assert len(logged_calls(conn, paths.run_id)) == 1

    db_file = Path(str(conn.execute("PRAGMA database_list").fetchone()["file"]))
    reopened = connect(db_file)
    reopened_fake = FakeSession(
        (served_tool("score_test", schema, allow_in=["champion_selection"]),)
    )
    async with McpClient(settings(paths, tmp_path), reopened, session=reopened_fake) as client:
        with pytest.raises(ArtifactLockedError):
            await client.call(ctx, "score_test", {"truth": str(ref)})
    assert not reopened_fake.calls
    reopened.close()


@pytest.mark.asyncio
async def test_two_clients_compete_for_one_durable_test_score_claim(
    monkeypatch: pytest.MonkeyPatch,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
    tmp_path: Path,
) -> None:
    conn, paths, contract, _ = world
    ref = new_artifact_ref()
    path = paths.artifact(ref, "dataframe")
    path.write_bytes(b"test")
    meta = ArtifactMeta(ref=ref, kind="dataframe", split="test")
    create_artifact(
        conn,
        ref=ref,
        run_id=paths.run_id,
        kind="dataframe",
        path=path,
        meta=meta,
        produced_by="fixture",
        split="test",
    )
    execution = create_node_execution(
        conn, run_id=paths.run_id, node_name="champion_selection", seq=1
    )
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"truth": {"type": "string"}},
        "required": ["truth"],
    }
    db_file = Path(str(conn.execute("PRAGMA database_list").fetchone()["file"]))
    conn.close()
    sessions = tuple(
        FakeSession((served_tool("score_classification", schema, allow_in=["champion_selection"]),))
        for _ in range(2)
    )
    ctx = context(
        paths,
        contract,
        execution.id,
        frozenset({"score_classification"}),
        node="champion_selection",
        champion_selected=True,
    )

    barrier = threading.Barrier(2)

    def simultaneous_claim(peer: sqlite3.Connection, run_id: str) -> bool:
        barrier.wait(timeout=20)
        return claim_test_scoring(peer, run_id)

    monkeypatch.setattr("statlab.mcp_client.claim_test_scoring", simultaneous_claim)

    def dispatch(session: FakeSession) -> object:
        peer = connect(db_file)

        async def call() -> object:
            async with McpClient(settings(paths, tmp_path), peer, session=session) as client:
                try:
                    return await client.call(ctx, "score_classification", {"truth": str(ref)})
                except ArtifactLockedError as exc:
                    return exc

        try:
            return asyncio.run(call())
        finally:
            peer.close()

    results = await asyncio.gather(*(asyncio.to_thread(dispatch, session) for session in sessions))
    assert sum(isinstance(result, ArtifactLockedError) for result in results) == 1
    assert sum(bool(session.calls) for session in sessions) == 1
    reopened = connect(db_file)
    claimed = get_run(reopened, paths.run_id)
    assert claimed is not None and claimed.test_scored_at is not None
    assert len(logged_calls(reopened, paths.run_id)) == 1
    reopened.close()


@pytest.mark.asyncio
async def test_malformed_result_is_logged_as_tool_failure_before_error(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    session.envelope = {"not": "an envelope"}
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ToolCallError) as error:
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert error.value.failure.error_class == "bug"
    assert len(logged_calls(conn, paths.run_id)) == 1
    result_json = conn.execute(
        "SELECT result_json FROM tool_calls WHERE run_id = ?", (paths.run_id,)
    ).fetchone()[0]
    assert ToolFailure.model_validate_json(result_json).error_class == "bug"


@pytest.mark.asyncio
async def test_output_refs_are_registered_in_order_and_invalid_ref_is_rejected(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    metas = [
        ArtifactMeta(ref=new_artifact_ref(), kind="table"),
        ArtifactMeta(ref=new_artifact_ref(), kind="plot"),
    ]
    for meta in metas:
        paths.artifact(meta.ref, meta.kind).write_bytes(b"output")
    session = FakeSession((served_tool("echo", schema),))
    session.envelope = ToolSuccess(artifacts=tuple(metas)).model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        result = await client.call(context(paths, contract, execution_id), "echo", {})
    assert [meta.ref for meta in result.artifacts] == [meta.ref for meta in metas]
    assert logged_calls(conn, paths.run_id)[0].outputs == tuple(str(meta.ref) for meta in metas)

    bad = ArtifactMeta(ref=ArtifactRef("../outside"), kind="table")
    bad_session = FakeSession((served_tool("echo", schema),))
    bad_session.envelope = ToolSuccess(artifacts=(bad,)).model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=bad_session) as client:
        with pytest.raises(ArtifactError, match="invalid artifact ref"):
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert len(logged_calls(conn, paths.run_id)) == 2


@pytest.mark.asyncio
async def test_missing_tool_output_file_is_rejected_after_logging_response(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, execution_id = world
    meta = ArtifactMeta(ref=new_artifact_ref(), kind="table")
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession((served_tool("echo", schema),))
    session.envelope = ToolSuccess(artifacts=(meta,)).model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        with pytest.raises(ArtifactError, match="tool output file is missing"):
            await client.call(context(paths, contract, execution_id), "echo", {})
    assert len(logged_calls(conn, paths.run_id)) == 1


@pytest.mark.asyncio
async def test_verify_split_fingerprint_is_persisted(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, contract, _ = world
    execution = create_node_execution(conn, run_id=paths.run_id, node_name="data_splitting", seq=1)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    session = FakeSession(
        (served_tool("verify_split", schema, allow_in=["data_splitting"], phase="splitting"),)
    )
    session.envelope = ToolSuccess(summary={"test_fingerprint": "fingerprint-1"}).model_dump(
        mode="json"
    )
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        await client.call(
            context(
                paths,
                contract,
                execution.id,
                frozenset({"verify_split"}),
                node="data_splitting",
            ),
            "verify_split",
            {},
        )
    run = get_run(conn, paths.run_id)
    assert run is not None and run.test_fingerprint == "fingerprint-1"


def test_repository_rejects_cross_run_call_attribution_and_bad_log_rows(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
) -> None:
    conn, paths, _, execution_id = world
    run = get_run(conn, paths.run_id)
    assert run is not None
    other = create_run(
        conn,
        project_id=run.project_id,
        contract_id=run.contract_id,
        mode="autonomous",
        config_json="{}",
    )
    foreign_execution = create_node_execution(conn, run_id=other.id, node_name="intake", seq=0)
    with pytest.raises(PersistenceError):
        record_tool_call(
            conn,
            run_id=paths.run_id,
            node_execution_id=foreign_execution.id,
            tool_name="echo",
            args_json="{}",
            result_json=ToolSuccess().model_dump_json(),
            duration_ms=0,
        )
    record_tool_call(
        conn,
        run_id=paths.run_id,
        node_execution_id=execution_id,
        tool_name="echo",
        args_json="[]",
        result_json=ToolSuccess().model_dump_json(),
        duration_ms=0,
    )
    with pytest.raises(PersistenceError, match="malformed stored tool call"):
        logged_calls(conn, paths.run_id)


def test_repository_wraps_node_execution_integrity_and_sqlite_errors(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
) -> None:
    conn, paths, _, _ = world
    with pytest.raises(PersistenceError, match="node_executions identity"):
        create_node_execution(conn, run_id=paths.run_id, node_name="intake", seq=0)
    conn.close()
    with pytest.raises(PersistenceError, match="cannot create node execution"):
        create_node_execution(conn, run_id=paths.run_id, node_name="data_profiling", seq=1)


def test_repository_wraps_tool_call_integrity_and_sqlite_errors(
    monkeypatch: pytest.MonkeyPatch,
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str],
) -> None:
    conn, paths, _, execution_id = world
    record_tool_call(
        conn,
        run_id=paths.run_id,
        node_execution_id=execution_id,
        tool_name="echo",
        args_json="{}",
        result_json=ToolSuccess().model_dump_json(),
        duration_ms=0,
    )
    existing_id = conn.execute(
        "SELECT id FROM tool_calls WHERE run_id = ?", (paths.run_id,)
    ).fetchone()["id"]
    monkeypatch.setattr("statlab.persistence.repository.new_id", lambda _: existing_id)
    with pytest.raises(PersistenceError, match="tool_calls attribution"):
        record_tool_call(
            conn,
            run_id=paths.run_id,
            node_execution_id=execution_id,
            tool_name="echo",
            args_json="{}",
            result_json=ToolSuccess().model_dump_json(),
            duration_ms=0,
        )
    conn.close()
    with pytest.raises(PersistenceError, match="cannot record tool call"):
        record_tool_call(
            conn,
            run_id=paths.run_id,
            node_execution_id=execution_id,
            tool_name="echo",
            args_json="{}",
            result_json=ToolSuccess().model_dump_json(),
            duration_ms=0,
        )


_DESCRIBE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "project": {"type": "string"},
        "run": {"type": "string"},
        "artifact": {"type": "string"},
        "kind": {"type": "string"},
        "head_rows": {"type": "integer"},
    },
    "required": ["project", "run", "artifact", "kind", "head_rows"],
    "additionalProperties": False,
}


@pytest.mark.asyncio
async def test_describe_artifact_dispatches_without_audit(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    session = FakeSession(
        (served_tool("describe_artifact", _DESCRIBE_SCHEMA, allow_in=["*"], phase="artifacts"),)
    )
    session.envelope = ToolSuccess(summary={"rows": 1}).model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as client:
        result = await client.describe_artifact(
            project=paths.slug, run=paths.run_id, artifact="a_" + "0" * 16, kind="dataframe"
        )
    assert result.summary == {"rows": 1}
    assert session.calls == [
        (
            "describe_artifact",
            {
                "project": paths.slug,
                "run": paths.run_id,
                "artifact": "a_" + "0" * 16,
                "kind": "dataframe",
                "head_rows": 20,
            },
            60,
        )
    ]
    assert conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_describe_artifact_guards(
    world: tuple[sqlite3.Connection, RunPaths, DataContract, str], tmp_path: Path
) -> None:
    conn, paths, _, _ = world
    artifact = "a_" + "0" * 16
    client = McpClient(settings(paths, tmp_path), conn, session=FakeSession(()))
    with pytest.raises(EngineError, match="MCP client is not entered"):
        await client.describe_artifact(
            project=paths.slug, run=paths.run_id, artifact=artifact, kind="dataframe"
        )
    good = served_tool("describe_artifact", _DESCRIBE_SCHEMA, allow_in=["*"], phase="artifacts")
    session = FakeSession((good,))
    async with McpClient(settings(paths, tmp_path), conn, session=session) as entered:
        with pytest.raises(EngineError):
            await entered.describe_artifact(
                project="missing-project", run=paths.run_id, artifact=artifact, kind="dataframe"
            )
    async with McpClient(settings(paths, tmp_path), conn, session=FakeSession(())) as missing:
        with pytest.raises(EngineError):
            await missing.describe_artifact(
                project=paths.slug, run=paths.run_id, artifact=artifact, kind="dataframe"
            )
    wrong = FakeSession((served_tool("describe_artifact", _DESCRIBE_SCHEMA, phase="intake"),))
    async with McpClient(settings(paths, tmp_path), conn, session=wrong) as bad:
        with pytest.raises(EngineError):
            await bad.describe_artifact(
                project=paths.slug, run=paths.run_id, artifact=artifact, kind="dataframe"
            )
    assert wrong.calls == []
    session.envelope = ToolSuccess(
        artifacts=(ArtifactMeta(ref=new_artifact_ref(), kind="table"),)
    ).model_dump(mode="json")
    async with McpClient(settings(paths, tmp_path), conn, session=session) as entered:
        with pytest.raises(ToolCallError):
            await entered.describe_artifact(
                project=paths.slug, run=paths.run_id, artifact=artifact, kind="dataframe"
            )
