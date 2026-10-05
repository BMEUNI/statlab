"""Offline HTTP coverage for durable run creation, controls and snapshots."""

from __future__ import annotations

import asyncio
import sqlite3
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import litellm
import pytest
from fastapi import FastAPI
from litellm.exceptions import APIConnectionError
from pydantic import JsonValue, SecretStr, TypeAdapter
from statlab.api.projects import APIResources
from statlab.api.runs import RunConfig, RunRegistry
from statlab.config import Settings
from statlab.domains.base import NodeResult
from statlab.domains.decisions import IntakeDecision
from statlab.domains.runtime import DomainRuntime
from statlab.domains.tabular import TABULAR_DOMAIN
from statlab.errors import ConfigError, PersistenceError
from statlab.graph.control import ConversationTurn, QaAnswer
from statlab.graph.engine import NodeInvocation
from statlab.graph.gates import GateQuestion
from statlab.graph.lifecycle import RunLifecycle
from statlab.graph.router import InterruptClass
from statlab.graph.state import RunState
from statlab.persistence.db import connect as persistence_connect
from statlab.persistence.models import GateRow, MemoryRow, RunMode
from statlab.persistence.repository import (
    create_memory,
    create_message,
    get_contract,
    get_dataset,
    get_project,
    get_run,
    list_gates,
    list_messages,
    list_node_executions,
    list_runs,
)
from statlab_contracts.contract import DataContract
from statlab_contracts.gates import GateAction, GateAnswer, RewindIntent
from statlab_contracts.layout import RunPaths

from tests.test_api_projects import (
    SetupSession,
    _complete_contract,
    _model_response,
    _validation_profile,
)


@dataclass(slots=True)
class ExecutorFlags:
    before_block: bool = False
    after_gate: bool = False
    tool_block: bool = False


@dataclass(slots=True)
class FixtureRecords:
    replies: deque[str | BaseException] = field(default_factory=deque)
    requests: list[dict[str, object]] = field(default_factory=list)
    flags: deque[ExecutorFlags] = field(default_factory=deque)
    executors: dict[str, FixtureExecutor] = field(default_factory=dict)
    provider_checks: list[tuple[tuple[str, ...], dict[str, str]]] = field(default_factory=list)


@dataclass(slots=True)
class FixtureExecutor:
    runtime: DomainRuntime
    flags: ExecutorFlags
    first_entered: asyncio.Event = field(default_factory=asyncio.Event)
    first_release: asyncio.Event = field(default_factory=asyncio.Event)
    next_entered: asyncio.Event = field(default_factory=asyncio.Event)
    next_release: asyncio.Event = field(default_factory=asyncio.Event)
    body_count: int = 0

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        if invocation.spec.name == TABULAR_DOMAIN.nodes[0].name:
            if self.flags.before_block:
                self.first_entered.set()
                await self.first_release.wait()
                self.runtime.tools.control.check()
            decision = await self.runtime.decide(
                invocation, IntakeDecision, evidence=(), question="Fixture"
            )
            approved = await self.runtime.approve(invocation, decision, ())
            if approved is None:
                return NodeResult(status="awaiting_user", summary={"body_count": self.body_count})
            self.body_count += 1
            if self.flags.tool_block:
                await self.runtime.call(invocation, "load_csv", {})
            if self.flags.after_gate:
                approved_after = await self.runtime.approve(
                    invocation, approved, (), after_action=True
                )
                if approved_after is None:
                    return NodeResult(
                        status="awaiting_user", summary={"body_count": self.body_count}
                    )
            return NodeResult(status="completed", summary={"body_count": self.body_count})
        self.next_entered.set()
        await self.next_release.wait()
        self.runtime.tools.control.check()
        return NodeResult(status="completed", summary={"body_count": self.body_count})


@dataclass(frozen=True, slots=True)
class RunAPI:
    app: FastAPI
    client: httpx.AsyncClient
    settings: Settings
    session: SetupSession
    registry: RunRegistry
    conn: sqlite3.Connection
    records: FixtureRecords
    project_id: str
    dataset_id: str
    contract_id: str


@asynccontextmanager
async def _open_run_api(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    records: FixtureRecords | None = None,
    max_runs: int = 0,
    setup: bool = True,
    existing_run_id: str | None = None,
    reject_old_model: bool = False,
) -> AsyncIterator[RunAPI]:
    import statlab.api.runs as runs_module
    import statlab.domains.binding as binding_module
    import statlab.main as main_module

    selected = Settings(
        mcp_token=SecretStr("fixture-token"),
        data_dir=root / "data",
        db_path=root / "db.sqlite",
        max_concurrent_runs=max_runs,
        max_concurrent_llm=1,
    )
    selected_records = records if records is not None else FixtureRecords()
    registries: list[RunRegistry] = []
    connections: list[sqlite3.Connection] = []
    original_registry = RunRegistry
    original_connect = persistence_connect

    def capture_registry(resources: APIResources) -> RunRegistry:
        registry = original_registry(resources)
        registries.append(registry)
        return registry

    def capture_connect(path: Path) -> sqlite3.Connection:
        conn = original_connect(path)
        connections.append(conn)
        return conn

    def executor_factory(runtime: DomainRuntime) -> FixtureExecutor:
        flags = selected_records.flags.popleft() if selected_records.flags else ExecutorFlags()
        executor = FixtureExecutor(runtime, flags)
        selected_records.executors[runtime.paths.run_id] = executor
        return executor

    async def complete(**kwargs: object) -> object:
        selected_records.requests.append(dict(kwargs))
        if not selected_records.replies:
            raise AssertionError("no canned run LLM response remains")
        reply = selected_records.replies.popleft()
        if isinstance(reply, BaseException):
            raise reply
        if kwargs.get("stream") is True:
            from tests.test_llm_stream import FakeStream, chunk

            midpoint = len(reply) // 2
            return FakeStream(
                (chunk(reply[:midpoint]), chunk(reply[midpoint:]), chunk(None, usage=True))
            )
        return _model_response(reply)

    def check_bound_models(
        models: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        env_file: Path = Path(".env"),
    ) -> None:
        del env_file
        if not reject_old_model:
            return
        selected_records.provider_checks.append((tuple(models), dict(environment or {})))
        if "openai/old-test" in models:
            raise ConfigError("fixture old model unavailable")

    monkeypatch.setattr(main_module, "check_provider_environment", lambda *args, **kwargs: None)
    monkeypatch.setattr(runs_module, "check_provider_environment", check_bound_models)
    monkeypatch.setattr(main_module, "connect", capture_connect)
    monkeypatch.setattr(main_module, "RunRegistry", capture_registry)
    monkeypatch.setattr(binding_module, "TabularExecutor", executor_factory)
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    session = SetupSession()
    from statlab.llm.client import LlmClient
    from statlab.main import create_app

    app = create_app(
        selected, mcp_session=session, llm_client=LlmClient(selected, acompletion=complete)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        if setup:
            created = await client.post("/api/projects", json={"slug": "demo", "name": "Demo"})
            assert created.status_code == 201
            project_id = str(created.json()["id"])
            dataset = await client.post(
                f"/api/projects/{project_id}/datasets",
                files={"file": ("data.csv", b"a,b\n0,1\n1,2\n", "text/csv")},
            )
            assert dataset.status_code == 201
            dataset_id = str(dataset.json()["id"])
            uploaded = await client.post(
                f"/api/projects/{project_id}/contracts",
                files={
                    "file": (
                        "contract.json",
                        _complete_contract().model_dump_json(),
                        "application/json",
                    )
                },
            )
            assert uploaded.status_code == 201
            contract_id = str(uploaded.json()["id"])
        else:
            assert existing_run_id is not None
            assert connections and registries
            conn = connections[-1]
            row = get_run(conn, existing_run_id)
            assert row is not None
            project = get_project(conn, row.project_id)
            assert project is not None
            config = RunConfig.model_validate_json(row.config_json)
            dataset_row = get_dataset(conn, config.dataset_id)
            contract_row = get_contract(conn, row.contract_id)
            assert dataset_row is not None and dataset_row.project_id == project.id
            assert contract_row is not None and contract_row.project_id == project.id
            project_id, dataset_id, contract_id = project.id, dataset_row.id, contract_row.id
        assert len(connections) == 1 and len(registries) == 1
        yield RunAPI(
            app,
            client,
            selected,
            session,
            registries[0],
            connections[0],
            selected_records,
            project_id,
            dataset_id,
            contract_id,
        )


def _decision(text: str = "Fixture") -> str:
    return IntakeDecision(rationale=text).model_dump_json()


async def _create_run(
    api: RunAPI,
    *,
    mode: RunMode = "guided",
    flags: ExecutorFlags | None = None,
) -> httpx.Response:
    selected_flags = flags if flags is not None else ExecutorFlags()
    api.records.flags.append(selected_flags)
    response = await api.client.post(
        f"/api/projects/{api.project_id}/runs",
        json={"mode": mode, "dataset_ids": [api.dataset_id], "contract_id": api.contract_id},
    )
    if response.status_code != 201:
        queued = api.records.flags.pop()
        assert queued is selected_flags
    return response


async def _gate(api: RunAPI, run_id: str) -> GateRow:
    for _ in range(100):
        gates = tuple(gate for gate in list_gates(api.conn, run_id) if gate.status == "pending")
        task = api.registry.execution_tasks.get(run_id)
        if len(gates) == 1 and (task is None or task.done()):
            return gates[0]
        await asyncio.sleep(0)
    pytest.fail("pending SQL gate and completed task were not observed")


async def _pause_release(api: RunAPI, run_id: str, *, first: bool = False) -> None:
    executor = api.records.executors[run_id]
    request = asyncio.create_task(
        api.client.post(f"/api/runs/{run_id}/control", json={"action": "pause"})
    )
    for _ in range(100):
        if executor.runtime.tools.control.status == "paused":
            break
        await asyncio.sleep(0)
    assert executor.runtime.tools.control.status == "paused"
    (executor.first_release if first else executor.next_release).set()
    response = await request
    assert response.status_code == 200


def _mcp_session(api: RunAPI) -> object | None:
    return api.registry.resources.mcp._session


def _prompt_user(request: dict[str, object]) -> str:
    messages = request.get("messages")
    assert isinstance(messages, list) and messages
    user = messages[-1]
    assert isinstance(user, dict)
    assert user.get("role") == "user"
    content = user.get("content")
    assert isinstance(content, str)
    return content


@pytest.mark.asyncio
async def test_creation_snapshot_and_rejected_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision()]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        before = len(list_runs(api.conn, api.project_id))
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        snapshot = await api.client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200
        assert len(snapshot.json()["state"]["spine"]) == 13
        assert snapshot.json()["state"]["status"] == "awaiting_user"
        assert snapshot.json()["gates"][0]["id"] == gate.id
        assert len(list_runs(api.conn, api.project_id)) == before + 1
        row = get_run(api.conn, run_id)
        assert row is not None and row.mode == "guided"
        config = RunConfig.model_validate_json(row.config_json)
        assert config.dataset_id == api.dataset_id and config.dataset_sha256
        assert config.session_id.startswith("s_") and config.memory == ()
        paths = RunPaths(api.settings.data_dir, "demo", run_id)
        contract_row = get_contract(api.conn, api.contract_id)
        assert contract_row is not None
        assert paths.contract_snapshot_json.read_bytes() == contract_row.contract_json.encode()
        assert (paths.root / "uv.lock").read_bytes() == Path("uv.lock").read_bytes()
        assert paths.skill_snapshot_md.read_bytes() == api.registry.resources.policy.original_bytes
        missing = await api.client.get("/api/runs/r_0000000000000000")
        assert missing.status_code == 404 and missing.json()["detail"] == "run not found"
        for payload in (
            {"mode": "guided", "dataset_ids": [], "contract_id": api.contract_id},
            {
                "mode": "guided",
                "dataset_ids": [api.dataset_id, api.dataset_id],
                "contract_id": api.contract_id,
            },
            {
                "mode": "unknown",
                "dataset_ids": [api.dataset_id],
                "contract_id": api.contract_id,
            },
            {
                "mode": "guided",
                "dataset_ids": [api.dataset_id],
                "contract_id": api.contract_id,
                "extra": "x",
            },
        ):
            rejected = await api.client.post(f"/api/projects/{api.project_id}/runs", json=payload)
            assert rejected.status_code == 422
            assert "input" not in rejected.text and "ctx" not in rejected.text
        assert len(list_runs(api.conn, api.project_id)) == before + 1


@pytest.mark.asyncio
async def test_creation_ownership_partial_contract_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_run_api(tmp_path, monkeypatch) as api:
        other = await api.client.post("/api/projects", json={"slug": "other", "name": "Other"})
        assert other.status_code == 201
        other_id = str(other.json()["id"])
        upload = await api.client.post(
            f"/api/projects/{other_id}/datasets",
            files={"file": ("other.csv", b"a,b\n0,1\n1,2\n", "text/csv")},
        )
        assert upload.status_code == 201
        foreign_contract = await api.client.post(
            f"/api/projects/{other_id}/contracts",
            files={
                "file": (
                    "complete.json",
                    _complete_contract().model_dump_json(),
                    "application/json",
                )
            },
        )
        assert foreign_contract.status_code == 201
        from statlab_contracts.contract import TargetSpec

        partial_contract = DataContract(target=TargetSpec(column="a"))
        partial = await api.client.post(
            f"/api/projects/{api.project_id}/contracts",
            files={
                "file": (
                    "partial.json",
                    partial_contract.model_dump_json(),
                    "application/json",
                )
            },
        )
        assert partial.status_code == 201
        before = len(list_runs(api.conn, api.project_id))
        for dataset_id, contract_id in (
            (str(upload.json()["id"]), api.contract_id),
            (api.dataset_id, str(foreign_contract.json()["id"])),
            (api.dataset_id, str(partial.json()["id"])),
        ):
            response = await api.client.post(
                f"/api/projects/{api.project_id}/runs",
                json={"mode": "guided", "dataset_ids": [dataset_id], "contract_id": contract_id},
            )
            assert (
                response.status_code == 404
                if dataset_id != api.dataset_id or contract_id != str(partial.json()["id"])
                else response.status_code == 422
            )
        assert len(list_runs(api.conn, api.project_id)) == before
        api.session.responses.append(_validation_profile(valid=False, mismatches=[]))
        rejected = await _create_run(api)
        assert rejected.status_code == 422
        assert rejected.json()["detail"] == "contract does not match dataset"
        assert len(list_runs(api.conn, api.project_id)) == before


@pytest.mark.asyncio
async def test_capacity_reservation_and_zero_unbounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision(), _decision(), _decision()]))
    async with _open_run_api(tmp_path, monkeypatch, records=records, max_runs=1) as api:
        first = await _create_run(api, flags=ExecutorFlags(before_block=True))
        assert first.status_code == 201
        first_id = str(first.json()["run_id"])
        await api.records.executors[first_id].first_entered.wait()
        before = len(list_runs(api.conn, api.project_id))
        denied = await _create_run(api)
        assert denied.status_code == 409 and denied.json()["detail"]["run_id"] is None
        assert len(list_runs(api.conn, api.project_id)) == before
        active_resume = await api.client.post(
            f"/api/runs/{first_id}/control", json={"action": "resume"}
        )
        assert active_resume.status_code == 409
        await _pause_release(api, first_id, first=True)
        second = await _create_run(api)
        assert second.status_code == 201
        await _gate(api, str(second.json()["run_id"]))
    async with _open_run_api(tmp_path / "unbounded", monkeypatch, max_runs=0) as api:
        first = await _create_run(api, flags=ExecutorFlags(before_block=True))
        second = await _create_run(api, flags=ExecutorFlags(before_block=True))
        assert first.status_code == second.status_code == 201
        await api.records.executors[str(first.json()["run_id"])].first_entered.wait()
        await api.records.executors[str(second.json()["run_id"])].first_entered.wait()
        await _pause_release(api, str(first.json()["run_id"]), first=True)
        await _pause_release(api, str(second.json()["run_id"]), first=True)


@pytest.mark.parametrize(
    "action",
    ("approve", "approve_all_remaining", "abort", "rewind"),
)
@pytest.mark.asyncio
async def test_gate_action_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: GateAction
) -> None:
    replies = [_decision()]
    if action == "rewind":
        replies.append(_decision("Child"))
    async with _open_run_api(
        tmp_path / action,
        monkeypatch,
        records=FixtureRecords(replies=deque(replies)),
    ) as api:
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        parent = get_run(api.conn, run_id)
        assert parent is not None
        parent_config = RunConfig.model_validate_json(parent.config_json)
        body: dict[str, object] = {"action": action}
        if action == "rewind":
            body["target"] = TABULAR_DOMAIN.nodes[0].name
        answered = await api.client.post(f"/api/runs/{run_id}/gates/{gate.id}", json=body)
        assert answered.status_code == 200
        assert answered.json()["category"] == "decision"
        executor = api.records.executors[run_id]
        binding = api.registry.bindings[run_id]
        if action in ("approve", "approve_all_remaining"):
            await executor.next_entered.wait()
            assert executor.body_count == 1
            if action == "approve_all_remaining":
                row = get_run(api.conn, run_id)
                assert row is not None and row.mode == "autonomous"
                assert binding.lifecycle.initial.mode == "autonomous"
                snapshot = await api.client.get(f"/api/runs/{run_id}")
                assert (
                    snapshot.status_code == 200 and snapshot.json()["state"]["mode"] == "autonomous"
                )
                assert len(api.records.requests) == 1
            await _pause_release(api, run_id)
        elif action == "abort":
            row = get_run(api.conn, run_id)
            assert row is not None and row.status == "cancelled"
            state = await binding.lifecycle.state()
            assert state.status == "cancelled"
            assert run_id not in api.registry.execution_tasks
            applied = next(
                gate_row for gate_row in list_gates(api.conn, run_id) if gate_row.id == gate.id
            )
            assert executor.body_count == 0 and applied.status == "applied"
        else:
            child_id = str(answered.json()["new_run_id"])
            child_gate = await _gate(api, child_id)
            parent_after = get_run(api.conn, run_id)
            child = get_run(api.conn, child_id)
            assert parent_after is not None and parent_after.status == "superseded"
            assert child is not None and child.forked_from_run_id == run_id
            child_config = RunConfig.model_validate_json(child.config_json)
            assert child_config.fork is not None
            assert child_config.fork["request_id"] == gate.id
            assert child_config.fork["published"] is True
            assert child_config.dataset_id == parent_config.dataset_id
            assert child_config.dataset_sha256 == parent_config.dataset_sha256
            assert child_config.session_id == parent_config.session_id
            assert child_config.memory == parent_config.memory
            assert child.contract_id == parent.contract_id
            assert child_gate.status == "pending"
            child_state = await api.registry.bindings[child_id].lifecycle.state()
            assert child_state.status == "awaiting_user"
            assert api.registry.bindings[child_id].loop.control is not binding.loop.control
            assert api.registry.bindings[child_id].loop.events is not binding.loop.events


@pytest.mark.asyncio
async def test_gate_ownership_and_nonskippable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision(), _decision("Second")]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        first = await _create_run(api)
        second = await _create_run(api)
        assert first.status_code == second.status_code == 201
        first_id, second_id = str(first.json()["run_id"]), str(second.json()["run_id"])
        first_gate = await _gate(api, first_id)
        second_gate = await _gate(api, second_id)
        wrong_owner = await api.client.post(
            f"/api/runs/{first_id}/gates/{second_gate.id}", json={"action": "approve"}
        )
        missing = await api.client.post(
            f"/api/runs/{first_id}/gates/g_0000000000000000", json={"action": "approve"}
        )
        skip = await api.client.post(
            f"/api/runs/{first_id}/gates/{first_gate.id}",
            json={"action": "skip_node", "reason": "fixture reason"},
        )
        assert wrong_owner.status_code == missing.status_code == 404
        assert skip.status_code == 409
        assert (await _gate(api, first_id)).status == "pending"
        assert (await _gate(api, second_id)).status == "pending"
        assert api.records.executors[first_id].body_count == 0
        assert api.records.executors[second_id].body_count == 0


@pytest.mark.asyncio
async def test_capacity_denied_direct_fork_is_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision(), _decision("Child")]))
    async with _open_run_api(tmp_path, monkeypatch, records=records, max_runs=1) as api:
        first = await _create_run(api)
        assert first.status_code == 201
        parent = str(first.json()["run_id"])
        await _gate(api, parent)
        blocked = await _create_run(api, flags=ExecutorFlags(before_block=True))
        assert blocked.status_code == 201
        blocked_id = str(blocked.json()["run_id"])
        await api.records.executors[blocked_id].first_entered.wait()
        request = {
            "action": "rewind",
            "target": TABULAR_DOMAIN.nodes[0].name,
            "request_id": "capacity-fork",
        }
        denied = await api.client.post(f"/api/runs/{parent}/control", json=request)
        assert denied.status_code == 409
        child = str(denied.json()["detail"]["run_id"])
        assert child
        child_row = get_run(api.conn, child)
        assert child_row is not None and child_row.status == "pending"
        child_config = RunConfig.model_validate_json(child_row.config_json)
        assert child_config.fork is not None
        assert child_config.fork["published"] is True
        assert child_config.fork["request_id"] == request["request_id"]
        parent_row = get_run(api.conn, parent)
        assert parent_row is not None and parent_row.status == "superseded"
        children = tuple(
            row for row in list_runs(api.conn, api.project_id) if row.forked_from_run_id == parent
        )
        assert len(children) == 1 and children[0].id == child
        assert child not in api.registry.execution_tasks
        await _pause_release(api, blocked_id, first=True)
        retry = await api.client.post(f"/api/runs/{parent}/control", json=request)
        assert retry.status_code == 201 and retry.json()["new_run_id"] == child
        await _gate(api, child)
        children = tuple(
            row for row in list_runs(api.conn, api.project_id) if row.forked_from_run_id == parent
        )
        assert len(children) == 1 and children[0].id == child
        assert api.records.executors[child].body_count == 0


@pytest.mark.asyncio
async def test_gate_actions_and_direct_rewind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision()]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api, flags=ExecutorFlags(after_gate=True))
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        approved = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}",
            json={"action": "approve_with_edits", "edits": {"rationale": "Edited"}},
        )
        assert approved.status_code == 200
        executor = api.records.executors[run_id]
        post = await _gate(api, run_id)
        assert executor.body_count == 1
        assert post.question()["phase"] == "after"
        rejected = await api.client.post(
            f"/api/runs/{run_id}/gates/{post.id}",
            json={"action": "approve_with_edits", "edits": {"rationale": "again"}},
        )
        assert rejected.status_code == 409 and executor.body_count == 1
        approved_post = await api.client.post(
            f"/api/runs/{run_id}/gates/{post.id}", json={"action": "approve"}
        )
        assert approved_post.status_code == 200
        await executor.next_entered.wait()
        assert executor.body_count == 1 and len(records.requests) == 1
        await _pause_release(api, run_id)

    async with _open_run_api(
        tmp_path / "fork",
        monkeypatch,
        records=FixtureRecords(
            replies=deque(
                [
                    _decision(),
                    _decision("Child"),
                    InterruptClass(category="status").model_dump_json(),
                ]
            )
        ),
    ) as api:
        created = await _create_run(api)
        parent = str(created.json()["run_id"])
        await _gate(api, parent)
        before_invalid = len(list_runs(api.conn, api.project_id))
        invalid = (
            {"action": "rewind", "target": TABULAR_DOMAIN.nodes[0].name},
            {"action": "rewind", "request_id": "missing-target"},
            {"action": "rewind", "target": TABULAR_DOMAIN.nodes[0].name, "request_id": " "},
            {"action": "rewind", "target": " ", "request_id": "blank-target"},
            {"action": "pause", "target": TABULAR_DOMAIN.nodes[0].name, "request_id": "unrelated"},
        )
        for payload in invalid:
            response = await api.client.post(f"/api/runs/{parent}/control", json=payload)
            assert response.status_code == 422
            assert response.json()["detail"] == "invalid request"
            assert len(list_runs(api.conn, api.project_id)) == before_invalid
        unknown = await api.client.post(
            f"/api/runs/{parent}/control",
            json={"action": "rewind", "target": "unknown-node", "request_id": "unknown-request"},
        )
        assert (
            unknown.status_code == 409
            and len(list_runs(api.conn, api.project_id)) == before_invalid
        )
        forked = await api.client.post(
            f"/api/runs/{parent}/control",
            json={"action": "rewind", "target": TABULAR_DOMAIN.nodes[0].name, "request_id": "r1"},
        )
        assert forked.status_code == 201
        child = str(forked.json()["new_run_id"])
        child_gate = await _gate(api, child)
        child_binding = api.registry.bindings[child]
        child_state = await child_binding.lifecycle.state()
        child_events = await child_binding.loop.events.replay()
        child_executions = list_node_executions(api.conn, child)
        retry = await api.client.post(
            f"/api/runs/{parent}/control",
            json={"action": "rewind", "target": TABULAR_DOMAIN.nodes[0].name, "request_id": "r1"},
        )
        assert retry.status_code == 201 and retry.json()["new_run_id"] == child
        assert child_gate.id == list_gates(api.conn, child)[0].id
        assert await child_binding.lifecycle.state() == child_state
        assert await child_binding.loop.events.replay() == child_events
        assert list_node_executions(api.conn, child) == child_executions
        assert child_state.status == "awaiting_user"
        child_task = api.registry.execution_tasks.get(child)
        assert child_task is None or child_task.done()
        assert child_binding.loop.control is not api.registry.bindings[parent].loop.control
        assert child_binding.loop.events is not api.registry.bindings[parent].loop.events
        children = tuple(
            row for row in list_runs(api.conn, api.project_id) if row.forked_from_run_id == parent
        )
        assert len(children) == 1 and children[0].id == child
        child_row = get_run(api.conn, child)
        parent_row_before = get_run(api.conn, parent)
        assert child_row is not None and parent_row_before is not None
        child_config = RunConfig.model_validate_json(child_row.config_json)
        parent_config = RunConfig.model_validate_json(parent_row_before.config_json)
        assert child_config.fork != parent_config.fork
        assert child_config.dataset_id == parent_config.dataset_id
        assert child_config.dataset_sha256 == parent_config.dataset_sha256
        assert child_config.session_id == parent_config.session_id
        assert child_config.memory == parent_config.memory
        parent_paths = RunPaths(api.settings.data_dir, "demo", parent)
        child_paths = RunPaths(api.settings.data_dir, "demo", child)
        for filename in ("contract.snapshot.json", "skill.snapshot.md", "uv.lock"):
            parent_bytes = (parent_paths.root / filename).read_bytes()
            child_bytes = (child_paths.root / filename).read_bytes()
            assert child_bytes == parent_bytes
        message = await api.client.post(f"/api/runs/{child}/messages", json={"text": "status"})
        assert message.status_code == 200
        joined = api.conn.execute(
            "SELECT llm_calls.run_id, node_executions.run_id "
            "FROM llm_calls JOIN node_executions "
            "ON node_executions.id = llm_calls.node_execution_id "
            "WHERE llm_calls.run_id = ?",
            (child,),
        ).fetchall()
        assert joined and all(
            call_run_id == child and execution_run_id == child
            for call_run_id, execution_run_id in joined
        )
        mismatch = await api.client.post(
            f"/api/runs/{parent}/control",
            json={"action": "rewind", "target": TABULAR_DOMAIN.nodes[1].name, "request_id": "r1"},
        )
        assert mismatch.status_code == 409
        parent_row = get_run(api.conn, parent)
        assert parent_row is not None and parent_row.status == "superseded"


@pytest.mark.asyncio
async def test_gate_input_validation_and_stale_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision(), _decision("Revised")]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        cases = (
            {"action": "approve", "extra": "x"},
            {"action": "approve", "revision": -1},
            {"action": "approve_with_edits", "edits": {"rationale": 7}},
            {"action": "skip_node"},
        )
        for body in cases:
            response = await api.client.post(f"/api/runs/{run_id}/gates/{gate.id}", json=body)
            assert response.status_code == 422
        guarded = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}",
            json={"action": "approve_with_edits", "edits": {"unknown_field": "x"}},
        )
        assert guarded.status_code == 409
        instructed = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}",
            json={"action": "instruct", "instruction": "Revised"},
        )
        assert instructed.status_code == 200
        pending = await _gate(api, run_id)
        question = GateQuestion.model_validate(pending.question())
        assert question.revision == 1 and len(question.history) == 1
        assert question.proposal["rationale"] == "Revised"
        stale = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}",
            json={"action": "approve", "revision": 0},
        )
        assert stale.status_code == 409
        approved = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}",
            json={"action": "approve", "revision": 1},
        )
        assert approved.status_code == 200
        executor = api.records.executors[run_id]
        await executor.next_entered.wait()
        assert executor.body_count == 1 and len(records.requests) == 2
        await _pause_release(api, run_id)


@pytest.mark.asyncio
async def test_continuation_capacity_preserves_answered_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision(), _decision()]))
    async with _open_run_api(tmp_path, monkeypatch, records=records, max_runs=1) as api:
        first = await _create_run(api)
        first_id = str(first.json()["run_id"])
        gate = await _gate(api, first_id)
        second = await _create_run(api, flags=ExecutorFlags(before_block=True))
        second_id = str(second.json()["run_id"])
        await api.records.executors[second_id].first_entered.wait()
        accepted = await api.client.post(
            f"/api/runs/{first_id}/gates/{gate.id}", json={"action": "approve"}
        )
        assert accepted.status_code == 409
        first_row = get_run(api.conn, first_id)
        assert first_row is not None and first_row.status in ("pending", "running")
        assert accepted.json()["detail"]["run_id"] == first_id
        assert list_gates(api.conn, first_id)[0].status == "prepared"
        await _pause_release(api, second_id, first=True)
        resumed = await api.client.post(f"/api/runs/{first_id}/control", json={"action": "resume"})
        assert resumed.status_code == 200
        executor = api.records.executors[first_id]
        await executor.next_entered.wait()
        assert executor.body_count == 1
        duplicate = await api.client.post(
            f"/api/runs/{first_id}/control", json={"action": "resume"}
        )
        assert duplicate.status_code == 409
        await _pause_release(api, first_id)


@pytest.mark.asyncio
async def test_messages_status_noise_qa_and_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = InterruptClass(category="status").model_dump_json()
    noise = InterruptClass(category="noise").model_dump_json()
    qa = InterruptClass(category="qa").model_dump_json()
    answer = QaAnswer(text="Evidence unavailable.", references=()).model_dump_json()
    records = FixtureRecords(replies=deque([_decision(), status, noise, qa, answer]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        responses = []
        for text in ("status", "thanks", "what evidence?"):
            response = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": text})
            assert response.status_code == 200
            responses.append(response.json())
        assert responses[0]["facts"]["run_id"] == run_id
        assert responses[1]["text"] == "Acknowledged."
        assert responses[2]["text"] == "Evidence unavailable."
        assert len(api.records.requests) == 5
        run_row = get_run(api.conn, run_id)
        assert run_row is not None
        config = RunConfig.model_validate_json(run_row.config_json)
        messages = list_messages(api.conn, config.session_id)
        assert len(messages) == 6
        assert tuple(row.role for row in messages) == (
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
            "assistant",
        )
        assert tuple(row.content for row in messages[::2]) == (
            "status",
            "thanks",
            "what evidence?",
        )
        assert tuple(row.content for row in messages[1::2]) == tuple(
            response["text"] for response in responses
        )
        assert tuple(row.metadata()["category"] for row in messages[1::2]) == (
            "status",
            "noise",
            "qa",
        )
        events = tuple(
            event
            for event in await api.registry.bindings[run_id].loop.events.replay()
            if event.type in ("message.user", "message.assistant.complete")
        )
        user_events = tuple(event for event in events if event.type == "message.user")
        assistant_events = tuple(
            event for event in events if event.type == "message.assistant.complete"
        )
        assert len(user_events) == len(assistant_events) == 3
        assert tuple(event.payload["message_id"] for event in user_events) == tuple(
            row.id for row in messages[::2]
        )
        assert tuple(event.payload["message_id"] for event in assistant_events) == tuple(
            row.id for row in messages[1::2]
        )
        assert tuple(event.payload["reply_to"] for event in assistant_events) == tuple(
            row.id for row in messages[::2]
        )
        deltas = tuple(
            event
            for event in await api.registry.bindings[run_id].loop.events.replay()
            if event.type == "message.assistant.delta"
        )
        assert len(deltas) >= 1
        assert (
            "".join(str(event.payload["text"]) for event in deltas if not event.payload["reset"])
            == responses[2]["text"]
        )
        assert all(event.payload["reply_to"] == messages[4].id for event in deltas)
        assert api.records.requests[3]["stream"] is False
        assert api.records.requests[4]["stream"] is True
        assert all(event.payload["session_id"] == config.session_id for event in events)
        assert all(event.run_id == run_id for event in events)
        joined = api.conn.execute(
            "SELECT llm_calls.run_id, node_executions.run_id "
            "FROM llm_calls JOIN node_executions "
            "ON node_executions.id = llm_calls.node_execution_id "
            "WHERE llm_calls.run_id = ?",
            (run_id,),
        ).fetchall()
        assert len(joined) == 5
        assert all(
            call_run_id == run_id and execution_run_id == run_id
            for call_run_id, execution_run_id in joined
        )

    provider_error = APIConnectionError(
        message="offline transient", llm_provider="fixture", model="x"
    )
    async with _open_run_api(
        tmp_path / "transient",
        monkeypatch,
        records=FixtureRecords(replies=deque([_decision(), qa, provider_error])),
    ) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        failed = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": "question"})
        assert failed.status_code == 503
        assert failed.json()["detail"] == "LLM request failed"
        run_row = get_run(api.conn, run_id)
        assert run_row is not None
        config = RunConfig.model_validate_json(run_row.config_json)
        rows = list_messages(api.conn, config.session_id)
        assert len(rows) == 1 and rows[0].role == "user" and rows[0].content == "question"
        events = tuple(
            event
            for event in await api.registry.bindings[run_id].loop.events.replay()
            if event.type in ("message.user", "message.assistant.complete", "error")
        )
        assert tuple(event.type for event in events) == (
            "message.user",
            "error",
        )
        assert events[1].payload == {
            "reply_to": events[0].payload["message_id"],
            "message": "Assistant response failed.",
            "discard_draft": True,
        }
        assert events[0].payload["message_id"] == rows[0].id
        assert events[0].run_id == run_id and events[0].payload["session_id"] == config.session_id
        joined = api.conn.execute(
            "SELECT llm_calls.run_id, node_executions.run_id, llm_calls.signature, "
            "llm_calls.prompt_tokens, llm_calls.completion_tokens, llm_calls.cost_usd "
            "FROM llm_calls JOIN node_executions "
            "ON node_executions.id = llm_calls.node_execution_id "
            "WHERE llm_calls.run_id = ?",
            (run_id,),
        ).fetchall()
        assert len(api.records.requests) == 3
        assert all(
            call_run_id == run_id and execution_run_id == run_id
            for call_run_id, execution_run_id, *_ in joined
        )
        qa_rows = tuple(row for row in joined if row[2] == "QaAnswer")
        assert len(qa_rows) == 1
        assert qa_rows[0][3] == qa_rows[0][4] == qa_rows[0][5] == 0

    async with _open_run_api(
        tmp_path / "failure",
        monkeypatch,
        records=FixtureRecords(replies=deque([_decision(), qa, ValueError("offline failure")])),
    ) as api:
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        failed = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": "question"})
        assert failed.status_code == 502
        assert failed.json()["detail"] == "LLM request failed"
        run_row = get_run(api.conn, run_id)
        assert run_row is not None
        config = RunConfig.model_validate_json(run_row.config_json)
        rows = list_messages(api.conn, config.session_id)
        assert len(rows) == 1 and rows[0].role == "user" and rows[0].content == "question"
        events = tuple(
            event
            for event in await api.registry.bindings[run_id].loop.events.replay()
            if event.type in ("message.user", "message.assistant.complete", "error")
        )
        assert tuple(event.type for event in events) == (
            "message.user",
            "error",
        )
        assert events[1].payload == {
            "reply_to": events[0].payload["message_id"],
            "message": "Assistant response failed.",
            "discard_draft": True,
        }
        assert events[0].payload["message_id"] == rows[0].id
        assert events[0].run_id == run_id and events[0].payload["session_id"] == config.session_id
        joined = api.conn.execute(
            "SELECT llm_calls.run_id, node_executions.run_id, llm_calls.signature, "
            "llm_calls.prompt_tokens, llm_calls.completion_tokens, llm_calls.cost_usd "
            "FROM llm_calls JOIN node_executions "
            "ON node_executions.id = llm_calls.node_execution_id "
            "WHERE llm_calls.run_id = ?",
            (run_id,),
        ).fetchall()
        assert len(api.records.requests) == 3
        assert all(
            call_run_id == run_id and execution_run_id == run_id
            for call_run_id, execution_run_id, *_ in joined
        )
        qa_rows = tuple(row for row in joined if row[2] == "QaAnswer")
        assert len(qa_rows) == 1
        assert qa_rows[0][3] == qa_rows[0][4] == qa_rows[0][5] == 0


@pytest.mark.asyncio
async def test_message_lifecycle_actual_pause_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pause = InterruptClass(category="lifecycle", lifecycle_action="pause").model_dump_json()
    resume = InterruptClass(category="lifecycle", lifecycle_action="resume").model_dump_json()
    records = FixtureRecords(replies=deque([_decision(), pause, pause, resume]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        failed_pause = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": "pause"})
        assert failed_pause.status_code == 409
        assert failed_pause.json()["detail"] == "run transition rejected"
        binding = api.registry.bindings[run_id]
        assert (await binding.lifecycle.state()).status == "awaiting_user"
        snapshot = await api.client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200 and snapshot.json()["state"]["status"] == "awaiting_user"
        run_row = get_run(api.conn, run_id)
        assert run_row is not None
        failed_config = RunConfig.model_validate_json(run_row.config_json)
        failed_rows = list_messages(api.conn, failed_config.session_id)
        assert len(failed_rows) == 1 and failed_rows[0].role == "user"
        approved = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}", json={"action": "approve"}
        )
        assert approved.status_code == 200
        executor = api.records.executors[run_id]
        await executor.next_entered.wait()
        assert executor.body_count == 1
        pause_task = asyncio.create_task(
            api.client.post(f"/api/runs/{run_id}/messages", json={"text": "pause"})
        )
        for _ in range(100):
            if executor.runtime.tools.control.status == "paused":
                break
            await asyncio.sleep(0)
        assert executor.runtime.tools.control.status == "paused"
        executor.next_release.set()
        paused = await pause_task
        assert paused.status_code == 200 and paused.json()["category"] == "lifecycle"
        assert paused.json()["text"] == "Run paused."
        assert (await binding.lifecycle.state()).status == "paused"
        paused_row = get_run(api.conn, run_id)
        assert paused_row is not None and paused_row.status == "paused"
        executor.next_entered.clear()
        executor.next_release.clear()
        resumed = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": "resume"})
        assert resumed.status_code == 200 and resumed.json()["category"] == "lifecycle"
        assert resumed.json()["text"] == "Run continuation scheduled."
        await executor.next_entered.wait()
        assert (await binding.lifecycle.state()).status == "running"
        assert executor.body_count == 1
        await _pause_release(api, run_id)
        row = get_run(api.conn, run_id)
        assert row is not None
        config = RunConfig.model_validate_json(row.config_json)
        messages = list_messages(api.conn, config.session_id)
        assert tuple(item.role for item in messages) == (
            "user",
            "user",
            "assistant",
            "user",
            "assistant",
        )
        assert tuple(item.content for item in messages if item.role == "user") == (
            "pause",
            "pause",
            "resume",
        )
        assistants = tuple(item for item in messages if item.role == "assistant")
        assert tuple(item.metadata()["category"] for item in assistants) == (
            "lifecycle",
            "lifecycle",
        )
        assert tuple(item.content for item in assistants) == (
            "Run paused.",
            "Run continuation scheduled.",
        )
        events = tuple(
            event
            for event in await binding.loop.events.replay()
            if event.type in ("message.user", "message.assistant.complete")
        )
        assert sum(event.type == "message.user" for event in events) == 3
        assistant_events = tuple(
            event for event in events if event.type == "message.assistant.complete"
        )
        assert len(assistant_events) == 2
        assert {event.payload["message_id"] for event in assistant_events} == {
            item.id for item in assistants
        }
        assert all(event.run_id == run_id for event in events)
        assert len(api.records.requests) == 4
        audit = api.conn.execute(
            "SELECT llm_calls.run_id, node_executions.run_id, llm_calls.signature "
            "FROM llm_calls JOIN node_executions "
            "ON node_executions.id = llm_calls.node_execution_id "
            "WHERE llm_calls.run_id = ? ORDER BY llm_calls.rowid",
            (run_id,),
        ).fetchall()
        assert len(audit) == 4
        assert all(row[0] == run_id and row[1] == run_id for row in audit)
        assert tuple(item[2] for item in audit) == (
            "IntakeDecision",
            "InterruptClass",
            "InterruptClass",
            "InterruptClass",
        )


@pytest.mark.asyncio
async def test_message_bounded_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = FixtureRecords(
        replies=deque(
            [
                _decision(),
                InterruptClass(category="qa").model_dump_json(),
                QaAnswer(text="Evidence unavailable.", references=()).model_dump_json(),
            ]
        )
    )
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        row = get_run(api.conn, run_id)
        assert row is not None
        config = RunConfig.model_validate_json(row.config_json)
        original = tuple(
            create_message(
                api.conn,
                session_id=config.session_id,
                run_id=run_id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"turn-{index:02}-" + "x" * 1000,
            )
            for index in range(23)
        )
        incoming = "bounded question"
        response = await api.client.post(f"/api/runs/{run_id}/messages", json={"text": incoming})
        assert response.status_code == 200
        assert _prompt_user(api.records.requests[1]) == incoming
        qa_prompt = _prompt_user(api.records.requests[2])
        state_summary_json = qa_prompt.split("\n\n", maxsplit=1)[0]
        state_summary = TypeAdapter(dict[str, JsonValue]).validate_json(state_summary_json)
        raw_turns = state_summary["conversation"]
        assert isinstance(raw_turns, list)
        turns = tuple(ConversationTurn.model_validate(turn) for turn in raw_turns)
        assert len(turns) == 16
        assert turns[0].role == "user" and turns[0].text == original[8].content
        assert turns[-2].text == original[22].content
        assert turns[-1].role == "user" and turns[-1].text == incoming
        assert sum(len(turn.text) for turn in turns) <= 16000 and len(turns) <= 20
        assert tuple(turn.text for turn in turns[:-1]) == tuple(
            item.content for item in original[8:]
        )
        assert all(turn.text != original[index].content for turn in turns for index in range(8))
        assert tuple(turn.role for turn in turns[:-1]) == tuple(
            "user" if index % 2 == 0 else "assistant" for index in range(8, 23)
        )


@pytest.mark.parametrize("category", ("decision", "change"))
@pytest.mark.asyncio
async def test_message_decision_and_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, category: Literal["decision", "change"]
) -> None:
    replies: deque[str | BaseException] = deque([_decision()])
    message_text = "approve" if category == "decision" else "rewind to intake"
    async with _open_run_api(
        tmp_path / category, monkeypatch, records=FixtureRecords(replies=replies)
    ) as api:
        created = await _create_run(api)
        assert created.status_code == 201
        parent = str(created.json()["run_id"])
        gate = await _gate(api, parent)
        if category == "decision":
            replies.extend(
                (
                    InterruptClass(category="decision").model_dump_json(),
                    GateAnswer(gate_id=gate.id, action="approve").model_dump_json(),
                )
            )
        else:
            replies.extend(
                (
                    InterruptClass(category="change").model_dump_json(),
                    RewindIntent(target=TABULAR_DOMAIN.nodes[0].name).model_dump_json(),
                    _decision("Child"),
                )
            )
        response = await api.client.post(
            f"/api/runs/{parent}/messages", json={"text": message_text}
        )
        assert response.status_code == 200
        assert response.json()["category"] == category
        parent_row = get_run(api.conn, parent)
        assert parent_row is not None
        parent_config = RunConfig.model_validate_json(parent_row.config_json)
        parent_messages = tuple(
            row for row in list_messages(api.conn, parent_config.session_id) if row.run_id == parent
        )
        assert tuple(row.role for row in parent_messages) == ("user", "assistant")
        user_message, assistant_message = parent_messages
        assert user_message.content == message_text
        assert assistant_message.metadata()["category"] == category
        if category == "decision":
            executor = api.records.executors[parent]
            await executor.next_entered.wait()
            assert executor.body_count == 1
            applied = next(row for row in list_gates(api.conn, parent) if row.id == gate.id)
            assert applied.status == "applied"
            audit = api.conn.execute(
                "SELECT llm_calls.run_id, node_executions.run_id, llm_calls.signature "
                "FROM llm_calls JOIN node_executions "
                "ON node_executions.id = llm_calls.node_execution_id "
                "WHERE llm_calls.run_id = ? ORDER BY llm_calls.rowid",
                (parent,),
            ).fetchall()
            assert tuple(row[2] for row in audit) == (
                "IntakeDecision",
                "InterruptClass",
                "GateAnswer",
            )
            assert all(row[0] == parent and row[1] == parent for row in audit)
            assert len(api.records.requests) == 3
            await _pause_release(api, parent)
        else:
            child = response.json().get("new_run_id")
            assert isinstance(child, str)
            await _gate(api, child)
            parent_after = get_run(api.conn, parent)
            child_row = get_run(api.conn, child)
            assert parent_after is not None and parent_after.status == "superseded"
            assert child_row is not None and child_row.forked_from_run_id == parent
            children = tuple(
                row
                for row in list_runs(api.conn, api.project_id)
                if row.forked_from_run_id == parent
            )
            assert len(children) == 1 and children[0].id == child
            child_config = RunConfig.model_validate_json(child_row.config_json)
            assert child_config.session_id == parent_config.session_id
            assert child_config.dataset_id == parent_config.dataset_id
            assert child_config.dataset_sha256 == parent_config.dataset_sha256
            assert child_config.memory == parent_config.memory
            assert child_row.contract_id == parent_row.contract_id
            assert child_config.fork is not None
            assert child_config.fork["published"] is True
            assert child_config.fork["request_id"] == user_message.id
            assert assistant_message.metadata()["new_run_id"] == child
            parent_audit = api.conn.execute(
                "SELECT llm_calls.run_id, node_executions.run_id, llm_calls.signature "
                "FROM llm_calls JOIN node_executions "
                "ON node_executions.id = llm_calls.node_execution_id "
                "WHERE llm_calls.run_id = ? ORDER BY llm_calls.rowid",
                (parent,),
            ).fetchall()
            child_audit = api.conn.execute(
                "SELECT llm_calls.run_id, node_executions.run_id, llm_calls.signature "
                "FROM llm_calls JOIN node_executions "
                "ON node_executions.id = llm_calls.node_execution_id "
                "WHERE llm_calls.run_id = ? ORDER BY llm_calls.rowid",
                (child,),
            ).fetchall()
            assert tuple(row[2] for row in parent_audit) == (
                "IntakeDecision",
                "InterruptClass",
                "RewindIntent",
            )
            assert tuple(row[2] for row in child_audit) == ("IntakeDecision",)
            assert all(row[0] == parent and row[1] == parent for row in parent_audit)
            assert all(row[0] == child and row[1] == child for row in child_audit)


@pytest.mark.asyncio
async def test_memory_snapshot_restart_and_shutdown_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from statlab.graph.control import QaAnswer
    from statlab.graph.router import InterruptClass

    records = FixtureRecords(
        replies=deque(
            [
                _decision(),
                InterruptClass(category="qa").model_dump_json(),
                QaAnswer(text="Evidence unavailable.", references=()).model_dump_json(),
                _decision("Second"),
            ]
        )
    )
    run_id = ""
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        old = create_memory(
            api.conn, project_id=api.project_id, key="old", value="captured-old-fact"
        )
        created = await _create_run(api)
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        row = get_run(api.conn, run_id)
        assert row is not None
        captured = RunConfig.model_validate_json(row.config_json)
        assert captured.memory == (old,)
        create_memory(api.conn, project_id=api.project_id, key="new", value="uncaptured-new-fact")
        message = await api.client.post(
            f"/api/runs/{run_id}/messages", json={"text": "what is in memory?"}
        )
        assert message.status_code == 200
        prompt = api.records.requests[-1]["messages"]
        assert isinstance(prompt, list)
        assert "captured-old-fact" in str(prompt) and "uncaptured-new-fact" not in str(prompt)
        new_run = await _create_run(api)
        assert new_run.status_code == 201
        new_row = get_run(api.conn, str(new_run.json()["run_id"]))
        assert new_row is not None
        new_config = RunConfig.model_validate_json(new_row.config_json)
        assert tuple(item.key for item in new_config.memory) == ("old", "new")
        second_id = str(new_run.json()["run_id"])
        await _gate(api, second_id)
    async with _open_run_api(
        tmp_path, monkeypatch, records=FixtureRecords(), setup=False, existing_run_id=run_id
    ) as api:
        assert run_id not in api.registry.execution_tasks
        assert run_id not in api.records.executors
        assert second_id not in api.registry.execution_tasks
        assert second_id not in api.records.executors
        assert (await api.registry.bindings[run_id].lifecycle.state()).status == "awaiting_user"
        assert tuple(item.id for item in api.registry.bindings[run_id].runtime.memory) == tuple(
            item.id for item in captured.memory
        )


@pytest.mark.asyncio
async def test_autonomous_paused_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = FixtureRecords(replies=deque([_decision()]))
    run_id = ""
    captured_memory: tuple[MemoryRow, ...] = ()
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        old = create_memory(
            api.conn, project_id=api.project_id, key="old", value="captured-old-fact"
        )
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        gate = await _gate(api, run_id)
        answered = await api.client.post(
            f"/api/runs/{run_id}/gates/{gate.id}",
            json={"action": "approve_all_remaining"},
        )
        assert answered.status_code == 200
        executor = api.records.executors[run_id]
        await executor.next_entered.wait()
        assert executor.body_count == 1
        row = get_run(api.conn, run_id)
        assert row is not None and row.mode == "autonomous"
        snapshot = await api.client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["state"]["mode"] == "autonomous"
        await _pause_release(api, run_id)
        row = get_run(api.conn, run_id)
        assert row is not None
        captured_memory = RunConfig.model_validate_json(row.config_json).memory
        assert captured_memory == (old,)
    async with _open_run_api(
        tmp_path,
        monkeypatch,
        records=FixtureRecords(),
        setup=False,
        existing_run_id=run_id,
    ) as restarted:
        row = get_run(restarted.conn, run_id)
        assert row is not None and row.status == "paused" and row.mode == "autonomous"
        state = await restarted.registry.bindings[run_id].lifecycle.state()
        assert state.status == "paused" and state.mode == "autonomous"
        assert restarted.registry.bindings[run_id].lifecycle.initial.mode == "autonomous"
        assert restarted.registry.bindings[run_id].runtime.memory == captured_memory
        assert run_id not in restarted.registry.execution_tasks
        assert run_id not in restarted.records.executors


@pytest.mark.parametrize("failure", ("old_model", "missing_skill", "changed_contract"))
@pytest.mark.asyncio
async def test_historical_restart_rejects_invalid_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["old_model", "missing_skill", "changed_contract"],
) -> None:
    import statlab.main as main_module

    def fixture_environment(
        env_file: Path = Path(".env"),
        *,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        del env_file, environment
        return {"B13_CONFIG_MARKER": "captured-env"}

    monkeypatch.setenv("B13_CONFIG_MARKER", "captured-env")
    monkeypatch.setattr(main_module, "load_routing_environment", fixture_environment)
    records = FixtureRecords(replies=deque([_decision()]))
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        await _gate(api, run_id)
        row = get_run(api.conn, run_id)
        assert row is not None
        paths = RunPaths(api.settings.data_dir, "demo", run_id)
        skill_path = paths.skill_snapshot_md
    if failure == "old_model":
        original_skill = skill_path.read_bytes()
        assert original_skill.count(b"## problem_definition") == 1
        changed_skill = original_skill.replace(
            b"## problem_definition",
            b"### Model\n\nopenai/old-test\n\n## problem_definition",
        )
        skill_path.write_bytes(changed_skill)
    elif failure == "missing_skill":
        skill_path.unlink()
    else:
        contract_bytes = paths.contract_snapshot_json.read_bytes()
        changed_contract = contract_bytes + b" "
        paths.contract_snapshot_json.write_bytes(changed_contract)
    restart_records = FixtureRecords()
    if failure == "old_model":
        error_type: type[Exception] = ConfigError
        error_message = "fixture old model unavailable"
    else:
        error_type = PersistenceError
        error_message = (
            "run immutable snapshot is missing"
            if failure == "missing_skill"
            else "run contract snapshot differs from SQL"
        )
    with pytest.raises(error_type, match=error_message):
        async with _open_run_api(
            tmp_path,
            monkeypatch,
            records=restart_records,
            setup=False,
            existing_run_id=run_id,
            reject_old_model=failure == "old_model",
        ):
            pytest.fail("invalid historical snapshot unexpectedly yielded an API")
    assert restart_records.executors == {}
    assert restart_records.requests == []
    if failure == "old_model":
        checks = restart_records.provider_checks
        assert checks
        models, environment = checks[-1]
        assert "openai/old-test" in models
        assert environment == {"B13_CONFIG_MARKER": "captured-env"}
        assert skill_path.read_bytes() == changed_skill
    elif failure == "missing_skill":
        assert not skill_path.exists()
    else:
        assert paths.contract_snapshot_json.read_bytes() == changed_contract


@pytest.mark.asyncio
async def test_shutdown_drains_blocked_setup_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision()]))
    ready, finish = asyncio.Event(), asyncio.Event()
    holder: list[RunAPI] = []

    async def lifespan_owner() -> None:
        async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
            holder.append(api)
            ready.set()
            await finish.wait()

    owner = asyncio.create_task(lifespan_owner())
    await ready.wait()
    api = holder[0]
    run = await _create_run(
        api, mode="autonomous", flags=ExecutorFlags(before_block=True, tool_block=True)
    )
    assert run.status_code == 201
    run_id = str(run.json()["run_id"])
    executor = records.executors[run_id]
    await executor.first_entered.wait()
    api.session.block_next = True
    executor.first_release.set()
    await api.session.entered.wait()
    finish.set()
    for _ in range(100):
        if api.registry.closing and executor.runtime.tools.control.status == "paused":
            break
        await asyncio.sleep(0)
    assert api.registry.closing
    assert executor.runtime.tools.control.status == "paused"
    assert api.conn.execute("SELECT 1").fetchone()[0] == 1
    assert _mcp_session(api) is api.session
    api.session.release.set()
    await owner
    assert _mcp_session(api) is None
    with pytest.raises(sqlite3.ProgrammingError):
        api.conn.execute("SELECT 1")
    conn = persistence_connect(api.settings.db_path)
    try:
        paused_row = get_run(conn, run_id)
        assert paused_row is not None and paused_row.status == "paused"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_shutdown_drains_setup_upload_and_retrieves_failed_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready, finish = asyncio.Event(), asyncio.Event()
    holder: list[RunAPI] = []

    async def lifespan_owner() -> None:
        async with _open_run_api(tmp_path, monkeypatch) as api:
            holder.append(api)
            ready.set()
            await finish.wait()

    owner = asyncio.create_task(lifespan_owner())
    await ready.wait()
    api = holder[0]
    api.session.block_next = True
    upload = asyncio.create_task(
        api.client.post(
            f"/api/projects/{api.project_id}/datasets",
            files={"file": ("blocked.csv", b"a,b\n0,1\n", "text/csv")},
        )
    )
    await api.session.entered.wait()
    finish.set()
    for _ in range(100):
        if api.registry.closing:
            break
        await asyncio.sleep(0)
    assert api.conn.execute("SELECT 1").fetchone()[0] == 1
    assert _mcp_session(api) is api.session
    rejected = await api.client.get("/api/projects")
    assert rejected.status_code == 503
    api.session.release.set()
    assert (await upload).status_code == 201
    await owner
    assert _mcp_session(api) is None
    with pytest.raises(sqlite3.ProgrammingError):
        api.conn.execute("SELECT 1")


@pytest.mark.asyncio
async def test_failed_start_is_recorded_and_shutdown_closes_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = FixtureRecords(replies=deque([_decision()]))
    api_ref: list[RunAPI] = []
    original_start = RunLifecycle.start
    async with _open_run_api(tmp_path, monkeypatch, records=records) as api:
        api_ref.append(api)

        async def fail_start(self: RunLifecycle) -> RunState:
            del self
            raise PersistenceError("fixture start failed")

        monkeypatch.setattr(RunLifecycle, "start", fail_start)
        created = await _create_run(api)
        assert created.status_code == 201
        run_id = str(created.json()["run_id"])
        for _ in range(100):
            if run_id in api.registry.failed_tasks:
                break
            await asyncio.sleep(0)
        snapshot = await api.client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["execution_failed"] is True
        failed_row = get_run(api.conn, run_id)
        assert failed_row is not None and failed_row.status in ("pending", "running")
    api = api_ref[0]
    assert _mcp_session(api) is None
    with pytest.raises(sqlite3.ProgrammingError):
        api.conn.execute("SELECT 1")
    monkeypatch.setattr(RunLifecycle, "start", original_start)
    async with _open_run_api(
        tmp_path,
        monkeypatch,
        records=FixtureRecords(replies=deque([_decision()])),
        setup=False,
        existing_run_id=run_id,
    ) as restarted:
        await _gate(restarted, run_id)
        snapshot = await restarted.client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200 and snapshot.json()["execution_failed"] is False
