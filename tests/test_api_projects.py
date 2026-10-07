"""HTTP setup routes over the real repositories and served MCP schema."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import deque
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace, TracebackType
from typing import Literal, cast

import httpx
import litellm
import pytest
from fastapi import HTTPException
from litellm import ModelResponse
from mcp.types import CallToolResult
from pydantic import JsonValue, SecretStr
from starlette.requests import ClientDisconnect, Request
from starlette.types import Message
from statlab.api.projects import bounded_file
from statlab.config import Settings
from statlab.domains.base import NodeSpec
from statlab.errors import ConfigError
from statlab.llm.client import LlmClient
from statlab.llm.skills import SkillPolicy
from statlab.main import create_app
from statlab.mcp_client import ToolSpec
from statlab_contracts.contract import (
    DataContract,
    FeatureRules,
    Objective,
    SplitRules,
    TargetSpec,
)
from statlab_contracts.envelope import ToolFailure, ToolSuccess
from statlab_contracts.layout import ProjectPaths


class SetupSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, JsonValue], float]] = []
        self.responses: deque[ToolSuccess | ToolFailure | BaseException] = deque()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block_next = False

    async def list_tools(self) -> object:
        import statlab_tools.tools
        from statlab_tools.tool import server

        assert statlab_tools.tools is not None
        return SimpleNamespace(tools=await server.list_tools())

    async def call_tool(
        self, name: str, arguments: dict[str, JsonValue], *, read_timeout_seconds: float
    ) -> CallToolResult:
        self.calls.append((name, arguments, read_timeout_seconds))
        if self.block_next:
            self.block_next = False
            self.entered.set()
            await self.release.wait()
        envelope: ToolSuccess | ToolFailure = ToolSuccess(
            summary={
                "rows": 3,
                "cols": 3,
                "columns": ["a", "b", "c"],
                "dtypes": {"a": "int64", "b": "float64", "c": "str"},
                "null_counts": {"a": 0, "b": 0, "c": 0},
                "n_unique": {"a": 3, "b": 3, "c": 3},
                "roles": {"a": "numeric", "b": "numeric", "c": "categorical"},
                "role_evidence": {},
                "all_null": [],
                **({"valid": True, "mismatches": []} if "contract" in arguments else {}),
            }
        )
        if self.responses:
            queued = self.responses.popleft()
            if isinstance(queued, BaseException):
                raise queued
            envelope = queued
        return CallToolResult(
            content=[], structuredContent={"result": envelope.model_dump(mode="json")}
        )


def _model_response(content: str) -> ModelResponse:
    return ModelResponse(
        choices=[
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
                "index": 0,
            }
        ],
        model="test-model",
        usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    )


def _contract() -> DataContract:
    return DataContract(
        target=TargetSpec(column="a", type="binary", positive_class=True),
        objective=Objective(task="classification", primary_metric="roc_auc"),
        split=SplitRules(strategy="stratified"),
        features=FeatureRules(),
    )


def _complete_contract(
    *, target: str = "a", seed: int = 7, features: FeatureRules | None = None
) -> DataContract:
    return DataContract(
        target=TargetSpec(column=target, type="binary", positive_class=True),
        objective=Objective(task="classification", primary_metric="roc_auc"),
        split=SplitRules(strategy="stratified", seed=seed),
        features=features if features is not None else FeatureRules(),
    )


def _validation_profile(
    *, valid: bool, mismatches: list[dict[str, JsonValue]], all_null: list[str] | None = None
) -> ToolSuccess:
    return ToolSuccess(
        summary={
            "rows": 3,
            "cols": 3,
            "columns": ["a", "b", "c"],
            "dtypes": {"a": "int64", "b": "float64", "c": "str"},
            "null_counts": {"a": 0, "b": 0, "c": 0},
            "n_unique": {"a": 3, "b": 3, "c": 3},
            "roles": {"a": "numeric", "b": "numeric", "c": "categorical"},
            "role_evidence": {},
            "all_null": cast(JsonValue, [] if all_null is None else all_null),
            "valid": valid,
            "mismatches": cast(JsonValue, mismatches),
        }
    )


def _fake_llm(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, contents: list[str]
) -> LlmClient:
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    async def acompletion(**kwargs: object) -> ModelResponse:
        return _model_response(contents.pop(0))

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return LlmClient(settings)


@asynccontextmanager
async def _open_project_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: SetupSession | None = None,
    answer: DataContract | None = None,
    max_upload_mb: int = 200,
) -> AsyncIterator[tuple[httpx.AsyncClient, Settings, SetupSession, dict[str, JsonValue]]]:
    import statlab.main as main_module

    monkeypatch.setattr(main_module, "check_provider_environment", lambda *args, **kwargs: None)
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        max_upload_mb=max_upload_mb,
        max_concurrent_llm=1,
    )
    responses = (
        [
            _model_response(
                json.dumps(
                    {
                        "contract": answer.model_dump(mode="json"),
                        "reasoning": "Typed profile-based proposal.",
                    }
                )
            )
        ]
        if answer is not None
        else []
    )

    async def complete(**kwargs: object) -> ModelResponse:
        del kwargs
        if not responses:
            raise AssertionError("no canned setup LLM response remains")
        return responses.pop(0)

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    fake_session = session if session is not None else SetupSession()
    app = create_app(
        settings, mcp_session=fake_session, llm_client=LlmClient(settings, acompletion=complete)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        created = await client.post("/api/projects", json={"slug": "demo", "name": "Demo"})
        assert created.status_code == 201
        yield client, settings, fake_session, cast(dict[str, JsonValue], created.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "cancel"])
async def test_multipart_parser_closes_spooled_file_on_stream_failure(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    created: list[tempfile.SpooledTemporaryFile[bytes]] = []

    def spool(max_size: int = 0, mode: str = "w+b") -> tempfile.SpooledTemporaryFile[bytes]:
        assert mode == "w+b"
        handle = tempfile.SpooledTemporaryFile(max_size=max_size, mode="w+b")  # noqa: SIM115
        created.append(handle)
        return handle

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", spool)
    events: list[Message] = [
        {
            "type": "http.request",
            "body": b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.csv"\r\n\r\n',
            "more_body": True,
        },
        {"type": "http.disconnect"},
    ]
    event_index = 0

    event_iterator = iter(events)

    async def receive() -> Message:
        nonlocal event_index
        next_event = next(event_iterator)
        event_index += 1
        if next_event["type"] == "http.disconnect" and failure == "cancel":
            raise asyncio.CancelledError
        return next_event

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
        },
        receive,
    )
    with pytest.raises((HTTPException, ClientDisconnect, asyncio.CancelledError)):
        async with bounded_file(request, limit=1024):
            pytest.fail("incomplete multipart stream must not yield a file")
    assert created and all(handle.closed for handle in created)


@pytest.mark.asyncio
async def test_create_list_detail_and_csv_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.main as main_module

    selected_models: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        main_module,
        "check_provider_environment",
        lambda models, **kwargs: selected_models.append(tuple(models)),
    )
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "statlab.db",
    )
    session = SetupSession()
    proposed_contract = _contract()
    answers = [
        json.dumps(
            {
                "contract": proposed_contract.model_dump(mode="json"),
                "reasoning": "The target and defaults match the profile.",
            }
        ),
        json.dumps(
            {
                "contract": proposed_contract.model_dump(mode="json")
                | {"target": {"column": "a", "type": "binary", "positive_class": 1}},
                "reasoning": "Typed scalar mismatch.",
            }
        ),
    ]

    async def canned_completion(**kwargs: object) -> ModelResponse:
        del kwargs
        return _model_response(answers.pop(0))

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    llm_client = LlmClient(settings, acompletion=canned_completion)
    app = create_app(settings, mcp_session=session, llm_client=llm_client)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        created = await client.post("/api/projects", json={"slug": "demo", "name": "Demo"})
        assert created.status_code == 201
        project = created.json()
        paths = ProjectPaths(settings.data_dir, "demo")
        assert json.loads(paths.project_json.read_text(encoding="utf-8")) == project
        assert all(path.is_dir() for path in (paths.datasets, paths.contracts, paths.runs))
        assert (await client.get("/api/projects")).json() == [project]
        detail = await client.get(f"/api/projects/{project['id']}")
        assert detail.status_code == 200
        assert detail.json()["project"] == project
        content = b"a,b,c\n1,2.5,x\n3,4.5,y\n4,6.5,z\n"
        upload = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.CSV", content, "text/plain")},
        )
        assert upload.status_code == 201
        row = upload.json()
        assert row["n_rows"] == 3 and row["n_cols"] == 3
        assert Path(row["path"]).read_bytes() == content
        repeated = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("other.csv", content, "application/octet-stream")},
        )
        assert repeated.status_code == 200
        assert repeated.json()["id"] == row["id"]
        assert len(session.calls) == 1

        semicolon = await client.post(
            f"/api/projects/{project['id']}/datasets?delimiter=%3B&na_values=NA&na_values=missing",
            files={"file": ("again.csv", b"a;b;c\n1;2;x\n3;4;y\n4;6;z\n", "text/plain")},
        )
        assert semicolon.status_code == 201
        options = session.calls[-1][1]["options"]
        assert isinstance(options, dict)
        assert options["delimiter"] == ";"
        assert options["na_values"] == ["NA", "missing"]

        for invalid in (
            {"slug": "Bad", "name": "Name"},
            {"slug": "bad/path", "name": "Name"},
            {"slug": "valid", "name": "   "},
        ):
            rejected = await client.post("/api/projects", json=invalid)
            assert rejected.status_code == 422
            assert invalid.get("name", "") not in rejected.text

        missing = await client.get("/api/projects/missing")
        assert missing.status_code == 404

        # A complete contract uploads immutably and validates through setup MCP only.
        contract = _contract()
        uploaded_contract = await client.post(
            f"/api/projects/{project['id']}/contracts",
            files={"file": ("contract.JSON", contract.model_dump_json(), "application/json")},
        )
        assert uploaded_contract.status_code == 201
        contract_row = uploaded_contract.json()
        assert Path(contract_row["path"]).read_text(encoding="utf-8") == contract.model_dump_json()
        validated = await client.post(
            f"/api/projects/{project['id']}/contracts/validate",
            json={"dataset_id": row["id"], "contract": contract.model_dump(mode="json")},
        )
        assert validated.status_code == 200 and validated.json()["valid"] is True
        invalid_contract = contract.model_dump(mode="json")
        invalid_contract["features"] = {"include": ["secret"], "exclude": ["secret"]}
        unsafe = await client.post(
            f"/api/projects/{project['id']}/contracts/validate",
            json={"dataset_id": row["id"], "contract": invalid_contract},
        )
        assert unsafe.status_code == 422
        assert (
            "secret" not in unsafe.text and "input" not in unsafe.text and "ctx" not in unsafe.text
        )
        assert (await client.get("/api/projects/missing")).status_code == 404

        proposal_payload = {"dataset_id": row["id"], "contract": contract.model_dump(mode="json")}
        proposal = await client.post(
            f"/api/projects/{project['id']}/contracts/propose", json=proposal_payload
        )
        assert proposal.status_code == 200
        assert proposal.json()["contract"]["target"]["positive_class"] is True
        calls_before_scalar_change = len(session.calls)
        scalar_change = await client.post(
            f"/api/projects/{project['id']}/contracts/propose", json=proposal_payload
        )
        assert scalar_change.status_code == 422
        assert len(session.calls) == calls_before_scalar_change + 1

        collision_paths = ProjectPaths(settings.data_dir, "metadata-conflict")
        collision_paths.root.mkdir(parents=True)
        collision_paths.project_json.write_bytes(b"unrelated metadata")
        conflict = await client.post(
            "/api/projects", json={"slug": "metadata-conflict", "name": "Conflict"}
        )
        assert conflict.status_code == 409
        assert collision_paths.project_json.read_bytes() == b"unrelated metadata"
        assert all(
            item["slug"] != "metadata-conflict"
            for item in (await client.get("/api/projects")).json()
        )

        dangling_paths = ProjectPaths(settings.data_dir, "dangling-conflict")
        dangling_paths.root.mkdir(parents=True)
        dangling_paths.project_json.symlink_to(dangling_paths.root / "missing.json")
        dangling = await client.post(
            "/api/projects", json={"slug": "dangling-conflict", "name": "Dangling"}
        )
        assert dangling.status_code == 409
        assert dangling_paths.project_json.is_symlink()
        assert all(
            item["slug"] != "dangling-conflict"
            for item in (await client.get("/api/projects")).json()
        )

        original_link = os.link

        def publish_race(_source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
            Path(target).write_bytes(b"racing metadata")
            raise FileExistsError

        monkeypatch.setattr(os, "link", publish_race)
        raced = await client.post(
            "/api/projects", json={"slug": "publication-race", "name": "Race"}
        )
        monkeypatch.setattr(os, "link", original_link)
        assert raced.status_code == 409
        race_paths = ProjectPaths(settings.data_dir, "publication-race")
        assert race_paths.project_json.read_bytes() == b"racing metadata"
        assert any(
            item["slug"] == "publication-race"
            for item in (await client.get("/api/projects")).json()
        )
    assert selected_models


def _readonly_count(
    db_path: Path, table: Literal["datasets", "projects", "runs", "node_executions", "contracts"]
) -> int:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        queries = {
            "datasets": "SELECT COUNT(*) FROM datasets",
            "projects": "SELECT COUNT(*) FROM projects",
            "runs": "SELECT COUNT(*) FROM runs",
            "node_executions": "SELECT COUNT(*) FROM node_executions",
            "contracts": "SELECT COUNT(*) FROM contracts",
        }
        return int(connection.execute(queries[table]).fetchone()[0])
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_concurrent_upload_deduplicates_after_setup_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SetupSession()
    session.block_next = True
    async with _open_project_api(tmp_path, monkeypatch, session=session) as (
        client,
        settings,
        _,
        project,
    ):
        content = b"a,b\n1,2\n3,4\n"
        path = f"/api/projects/{project['id']}/datasets"
        first_task = asyncio.create_task(
            client.post(path, files={"file": ("first.csv", content, "text/csv")})
        )
        await session.entered.wait()
        second = await client.post(path, files={"file": ("second.csv", content, "text/csv")})
        assert second.status_code == 201
        session.release.set()
        first = await first_task
        assert first.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert _readonly_count(settings.db_path, "datasets") == 1


@pytest.mark.asyncio
async def test_upload_limits_and_multipart_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch, max_upload_mb=1) as (
        client,
        settings,
        _,
        project,
    ):
        path = f"/api/projects/{project['id']}/datasets"
        accepted = b"a,b\n" + b"x" * (1024 * 1024 - 4)
        response = await client.post(path, files={"file": ("exact.csv", accepted, "text/csv")})
        assert response.status_code == 201
        count = _readonly_count(settings.db_path, "datasets")
        names_before = {
            item.name for item in ProjectPaths(settings.data_dir, "demo").datasets.iterdir()
        }
        oversized = await client.post(
            path,
            files={"file": ("large.csv", accepted + b"x", "text/csv")},
        )
        assert oversized.status_code == 413
        assert _readonly_count(settings.db_path, "datasets") == count
        assert {
            item.name for item in ProjectPaths(settings.data_dir, "demo").datasets.iterdir()
        } == names_before

        malformed = [
            await client.post(path, files={"file": ("empty.csv", b"", "text/csv")}),
            await client.post(path, files={"file": ("wrong.txt", b"a,b\n1,2\n", "text/csv")}),
            await client.post(path, files={"other": ("wrong.csv", b"a,b\n1,2\n", "text/csv")}),
            await client.post(
                path,
                files=[
                    ("file", ("one.csv", b"a,b\n1,2\n", "text/csv")),
                    ("file", ("two.csv", b"a,b\n1,2\n", "text/csv")),
                ],
            ),
            await client.post(path, data={"file": "not a file"}),
        ]
        assert all(item.status_code == 422 for item in malformed)

        body = (
            b'--x\r\nContent-Disposition: form-data; name="file"; filename="raw.csv"\r\n\r\n'
            + b"x" * (1024 + 65537)
            + b"\r\n--x--\r\n"
        )

        events: list[Message] = [
            {"type": "http.request", "body": body, "more_body": True},
            {"type": "http.request", "body": b"", "more_body": False},
        ]
        event_iterator = iter(events)

        async def receive() -> Message:
            return next(event_iterator)

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/",
                "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
            },
            receive,
        )
        with pytest.raises(HTTPException) as failure:
            async with bounded_file(request, limit=1024):
                pytest.fail("oversized streamed multipart body must not yield a file")
        assert failure.value.status_code == 413


@pytest.mark.asyncio
async def test_dataset_ownership_corruption_failures_and_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SetupSession()
    async with _open_project_api(tmp_path, monkeypatch, session=session) as (
        client,
        settings,
        _,
        project,
    ):
        path = f"/api/projects/{project['id']}/datasets"
        content = b"a,b\n1,2\n3,4\n"
        uploaded = await client.post(path, files={"file": ("data.csv", content, "text/csv")})
        row = uploaded.json()
        other = await client.post("/api/projects", json={"slug": "other", "name": "Other"})
        cross = await client.post(
            f"/api/projects/{other.json()['id']}/contracts/validate",
            json={"dataset_id": row["id"], "contract": _contract().model_dump(mode="json")},
        )
        assert cross.status_code == 404

        saved = Path(row["path"])
        saved.write_bytes(b"mutated")
        repeated = await client.post(path, files={"file": ("again.csv", content, "text/csv")})
        assert repeated.status_code == 500 and saved.read_bytes() == b"mutated"
        saved.unlink()
        missing = await client.post(path, files={"file": ("again.csv", content, "text/csv")})
        assert missing.status_code == 500

        for error_class, expected_status in (("data", 422), ("transient", 503), ("bug", 500)):
            fresh = f"a,b\n{error_class},2\n"
            session.responses.append(
                ToolFailure(
                    error_class=cast(
                        Literal["transient", "tool_param", "data", "bug"], error_class
                    ),
                    message="secret token",
                )
            )
            failed = await client.post(path, files={"file": ("failed.csv", fresh, "text/csv")})
            assert failed.status_code == expected_status
            assert "secret token" not in failed.text
        assert _readonly_count(settings.db_path, "datasets") == 1

        cancel_content = b"a,b\ncancel,2\n"
        session.block_next = True
        names_before_cancel = {
            item.name for item in ProjectPaths(settings.data_dir, "demo").datasets.iterdir()
        }
        cancel_task = asyncio.create_task(
            client.post(path, files={"file": ("cancel.csv", cancel_content, "text/csv")})
        )
        await session.entered.wait()
        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task
        cancel_hash = hashlib.sha256(cancel_content).hexdigest()
        project_paths = ProjectPaths(settings.data_dir, "demo")
        assert {item.name for item in project_paths.datasets.iterdir()} == names_before_cancel | {
            f"{cancel_hash}.csv"
        }
        assert _readonly_count(settings.db_path, "datasets") == 1

        demo_bytes = {
            item.name: item.read_bytes()
            for item in project_paths.datasets.iterdir()
            if item.is_file()
        }
        new_paths = ProjectPaths(settings.data_dir, "symlinked")
        new_paths.root.mkdir(parents=True)
        new_paths.datasets.symlink_to(project_paths.datasets, target_is_directory=True)
        before_projects = _readonly_count(settings.db_path, "projects")
        symlinked = await client.post("/api/projects", json={"slug": "symlinked", "name": "Link"})
        assert symlinked.status_code == 500
        assert not new_paths.project_json.exists()
        assert _readonly_count(settings.db_path, "projects") == before_projects
        assert demo_bytes == {
            item.name: item.read_bytes()
            for item in project_paths.datasets.iterdir()
            if item.is_file()
        }


@pytest.mark.asyncio
async def test_contract_upload_versions_validation_findings_and_no_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SetupSession()
    async with _open_project_api(tmp_path, monkeypatch, session=session) as (
        client,
        settings,
        _,
        project,
    ):
        dataset = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        contract = DataContract(target=TargetSpec(column="a"))
        body = contract.model_dump_json()
        path = f"/api/projects/{project['id']}/contracts"
        first = await client.post(path, files={"file": ("partial.json", body, "application/json")})
        first_row = first.json()
        first_bytes = Path(first_row["path"]).read_bytes()
        second = await client.post(path, files={"file": ("partial.json", body, "application/json")})
        assert first.status_code == second.status_code == 201
        assert first_row["version"] == 1 and second.json()["version"] == 2
        assert Path(first_row["path"]).read_bytes() == first_bytes

        session.responses.append(
            _validation_profile(
                valid=False,
                mismatches=[{"field": "target.column", "message": "missing target"}],
            )
        )

        errors = [
            await client.post(path, files={"file": ("empty.json", b"", "application/json")}),
            await client.post(path, files={"file": ("bad.json", b"{bad", "application/json")}),
            await client.post(path, files={"file": ("wrong.txt", body, "application/json")}),
            await client.post(
                path,
                files={"file": ("large.json", b" " * (1024 * 1024 + 1), "application/json")},
            ),
        ]
        assert [item.status_code for item in errors] == [422, 422, 422, 413]
        validation = await client.post(
            f"/api/projects/{project['id']}/contracts/validate",
            json={
                "dataset_id": dataset.json()["id"],
                "contract": contract.model_dump(mode="json"),
            },
        )
        assert validation.status_code == 200 and validation.json()["valid"] is False
        assert validation.json()["mismatches"][0]["field"] == "target.column"
        assert _readonly_count(settings.db_path, "runs") == 0
        assert _readonly_count(settings.db_path, "node_executions") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["seed", "include", "exclude", "target"])
async def test_proposal_rejects_changed_supplied_contract_fields(
    changed_field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if changed_field == "seed":
        supplied = _complete_contract(seed=7)
        proposed = _complete_contract(seed=8)
    elif changed_field == "include":
        supplied = _complete_contract(features=FeatureRules(include=None))
        proposed = _complete_contract(features=FeatureRules(include=("b",)))
    elif changed_field == "exclude":
        supplied = _complete_contract(features=FeatureRules(exclude=("b", "c")))
        proposed = _complete_contract(features=FeatureRules(exclude=("c", "b")))
    else:
        supplied = _complete_contract(target="a")
        proposed = _complete_contract(target="b")
    async with _open_project_api(tmp_path, monkeypatch, answer=proposed) as (
        client,
        _,
        session,
        project,
    ):
        dataset = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        before = len(session.calls)
        response = await client.post(
            f"/api/projects/{project['id']}/contracts/propose",
            json={
                "dataset_id": dataset.json()["id"],
                "contract": supplied.model_dump(mode="json", exclude_unset=True),
            },
        )
        assert response.status_code == 422
        assert len(session.calls) == before + 1


@pytest.mark.asyncio
async def test_proposal_partial_unresolved_mismatch_and_missing_target_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    partial = DataContract(target=TargetSpec(column="a"))
    inferred = _complete_contract()
    async with _open_project_api(tmp_path, monkeypatch, answer=inferred) as (
        client,
        settings,
        _session,
        project,
    ):
        dataset = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        dataset_id = dataset.json()["id"]
        success = await client.post(
            f"/api/projects/{project['id']}/contracts/propose",
            json={
                "dataset_id": dataset_id,
                "contract": partial.model_dump(mode="json", exclude_unset=True),
            },
        )
        assert success.status_code == 200
        assert _readonly_count(settings.db_path, "contracts") == 0
        assert _readonly_count(settings.db_path, "runs") == 0


@pytest.mark.asyncio
async def test_proposal_unresolved_and_data_mismatch_return_422(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incomplete = DataContract(target=TargetSpec(column="a"))
    async with _open_project_api(tmp_path, monkeypatch, answer=incomplete) as (
        client,
        _,
        session,
        project,
    ):
        dataset = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        before = len(session.calls)
        unresolved = await client.post(
            f"/api/projects/{project['id']}/contracts/propose",
            json={
                "dataset_id": dataset.json()["id"],
                "contract": incomplete.model_dump(mode="json", exclude_unset=True),
            },
        )
        assert unresolved.status_code == 422 and len(session.calls) == before + 1

    session = SetupSession()
    async with _open_project_api(
        tmp_path / "mismatch", monkeypatch, session=session, answer=_complete_contract()
    ) as (client, _, session, project):
        dataset = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        session.responses.extend(
            (
                _validation_profile(valid=True, mismatches=[]),
                _validation_profile(
                    valid=False,
                    mismatches=[{"field": "objective.task", "message": "mismatch"}],
                ),
            )
        )
        before = len(session.calls)
        mismatch = await client.post(
            f"/api/projects/{project['id']}/contracts/propose",
            json={
                "dataset_id": dataset.json()["id"],
                "contract": _complete_contract().model_dump(mode="json", exclude_unset=True),
            },
        )
        assert mismatch.status_code == 422 and len(session.calls) == before + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("all_null", [False, True])
async def test_proposal_missing_or_all_null_target_skips_llm(
    all_null: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = SetupSession()
    async with _open_project_api(tmp_path, monkeypatch, session=session) as (
        client,
        _,
        session,
        project,
    ):
        dataset = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        session.responses.append(
            _validation_profile(
                valid=False,
                mismatches=[
                    {
                        "field": "target.column",
                        "message": "all-null target" if all_null else "target unavailable",
                    }
                ],
                all_null=["a"] if all_null else [],
            )
        )
        before = len(session.calls)
        result = await client.post(
            f"/api/projects/{project['id']}/contracts/propose",
            json={
                "dataset_id": dataset.json()["id"],
                "contract": _complete_contract().model_dump(mode="json", exclude_unset=True),
            },
        )
        assert result.status_code == 422 and len(session.calls) == before + 1


def test_app_factory_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    import statlab.main as main_module

    def no_call(*args: object, **kwargs: object) -> None:
        raise AssertionError("factory must not load settings or connect")

    monkeypatch.setattr(main_module, "load_settings", no_call)
    monkeypatch.setattr(main_module, "connect", no_call)
    assert create_app() is not None


@pytest.mark.asyncio
async def test_startup_selects_role_models_and_preserves_effective_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.main as main_module
    from statlab.llm.skills import load_skill as original_load_skill

    environment = {
        "STATLAB_MODEL_DEFAULT": "openai/default-test",
        "STATLAB_MODEL_REASONING": "openai/reasoning-test",
        "STATLAB_MODEL_CLASSIFY": "openai/classify-test",
        "SETUP_MARKER_CREDENTIAL": "marker-secret",
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, os.environ.get(key, value))
    monkeypatch.setattr(main_module, "load_routing_environment", lambda: environment)
    captured_models: tuple[str, ...] = ()
    captured_environment: Mapping[str, str] = {}
    policies: list[SkillPolicy] = []

    def check(models: Sequence[str], *, environment: Mapping[str, str]) -> None:
        nonlocal captured_models, captured_environment
        captured_models = tuple(models)
        captured_environment = environment

    def load_policy(
        path: Path, *, spine: tuple[NodeSpec, ...], tools: Mapping[str, ToolSpec]
    ) -> SkillPolicy:
        policy = original_load_skill(path, spine=spine, tools=tools)
        policies.append(policy)
        return policy

    monkeypatch.setattr(main_module, "check_provider_environment", check)
    monkeypatch.setattr(main_module, "load_skill", load_policy)
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
    )
    app = create_app(settings, mcp_session=SetupSession())
    async with app.router.lifespan_context(app):
        models = captured_models
        assert {
            value for key, value in environment.items() if key.startswith("STATLAB_MODEL_")
        } <= set(models)
        policy = policies[0]
        literal_models = {item.model for item in policy.nodes if item.model is not None}
        assert literal_models <= set(models)
        assert captured_environment["SETUP_MARKER_CREDENTIAL"] == "marker-secret"
        assert os.environ["SETUP_MARKER_CREDENTIAL"] == "marker-secret"


@pytest.mark.asyncio
async def test_startup_failure_closes_mcp_and_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab.main as main_module
    from statlab.mcp_client import McpClient
    from statlab.persistence.db import connect as original_connect

    database_connections: list[sqlite3.Connection] = []
    exit_tasks: list[asyncio.Task[object] | None] = []
    original_exit = McpClient.__aexit__

    def tracked_connect(path: Path) -> sqlite3.Connection:
        connection = original_connect(path)
        database_connections.append(connection)
        return connection

    async def tracked_exit(
        self: McpClient,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        exit_tasks.append(asyncio.current_task())
        await original_exit(self, exc_type, exc, traceback)

    monkeypatch.setattr(main_module, "connect", tracked_connect)
    monkeypatch.setattr(
        main_module,
        "check_provider_environment",
        lambda *args, **kwargs: (_ for _ in ()).throw(ConfigError("startup failed")),
    )
    monkeypatch.setattr(McpClient, "__aexit__", tracked_exit)
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
    )
    app = create_app(settings, mcp_session=SetupSession())
    with pytest.raises(ConfigError):
        async with app.router.lifespan_context(app):
            pytest.fail("startup must fail at provider validation")
    assert len(database_connections) == 1 and len(exit_tasks) == 1
    assert exit_tasks[0] is asyncio.current_task()
    with pytest.raises(sqlite3.ProgrammingError):
        database_connections[0].execute("SELECT 1")


def _write(db_path: Path, sql: str, params: tuple[str, ...]) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(sql, params)
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "status"), [("abc", 422), ("-1", 422), (str(1024 + 65537), 413)])
async def test_bounded_file_rejects_bad_content_length(value: str, status: int) -> None:
    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [
                (b"content-type", b"multipart/form-data; boundary=x"),
                (b"content-length", value.encode()),
            ],
        },
        receive,
    )
    with pytest.raises(HTTPException) as failure:
        async with bounded_file(request, limit=1024):
            pytest.fail("bad content length must not yield a file")
    assert failure.value.status_code == status


@pytest.mark.asyncio
async def test_project_creation_conflicts_and_metadata_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _, _):
        duplicate = await client.post("/api/projects", json={"slug": "demo", "name": "Again"})
        assert duplicate.status_code == 409
        locked = ProjectPaths(settings.data_dir, "locked")
        for directory in (locked.root, locked.datasets, locked.contracts, locked.runs):
            directory.mkdir(parents=True, exist_ok=True)
        locked.root.chmod(0o500)
        try:
            failed = await client.post("/api/projects", json={"slug": "locked", "name": "Locked"})
        finally:
            locked.root.chmod(0o700)
        assert failed.status_code == 500
        assert failed.json()["detail"] == "project was created but metadata could not be written"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["slug", "escape"])
async def test_stored_project_path_corruption_returns_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _, project):
        if case == "slug":
            _write(
                settings.db_path,
                "UPDATE projects SET slug = ? WHERE id = ?",
                ("Bad Slug!", str(project["id"])),
            )
            detail = "stored project path is invalid"
        else:
            root = ProjectPaths(settings.data_dir, "demo").root
            outside = tmp_path / "outside"
            root.rename(outside)
            root.symlink_to(outside, target_is_directory=True)
            detail = "project path escapes data directory"
        response = await client.get(f"/api/projects/{project['id']}")
        assert response.status_code == 500
        assert response.json()["detail"] == detail


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["path", "missing"])
async def test_stored_dataset_corruption_and_missing_file_return_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _, project):
        uploaded = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        row = uploaded.json()
        if case == "path":
            _write(
                settings.db_path,
                "UPDATE datasets SET path = ? WHERE id = ?",
                (str(tmp_path / "elsewhere.csv"), row["id"]),
            )
            detail = "stored dataset path is invalid"
        else:
            Path(row["path"]).unlink()
            detail = "stored dataset file is missing"
        response = await client.post(
            f"/api/projects/{project['id']}/contracts/validate",
            json={"dataset_id": row["id"], "contract": _contract().model_dump(mode="json")},
        )
        assert response.status_code == 500
        assert response.json()["detail"] == detail


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["validate", "propose"])
async def test_malformed_setup_summaries_are_internal_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, _, session, project):
        uploaded = await client.post(
            f"/api/projects/{project['id']}/datasets",
            files={"file": ("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")},
        )
        row = uploaded.json()
        if endpoint == "validate":
            session.responses.append(
                ToolSuccess(summary={"valid": True, "mismatches": ["not-an-object"]})
            )
        else:
            session.responses.append(ToolSuccess(summary={"mismatches": "not-a-list"}))
        response = await client.post(
            f"/api/projects/{project['id']}/contracts/{endpoint}",
            json={"dataset_id": row["id"], "contract": _contract().model_dump(mode="json")},
        )
        assert response.status_code == 500
        assert response.json() == {"detail": "internal error"}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["symlink", "corrupt", "dimensions", "unwritable"])
async def test_upload_filesystem_corruption_and_bad_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, session, project):
        content = b"a,b\nz,9\n"
        sha = hashlib.sha256(content).hexdigest()
        datasets = ProjectPaths(settings.data_dir, "demo").datasets
        datasets.mkdir(parents=True, exist_ok=True)
        expected: dict[str, JsonValue] = {"detail": "internal error"}
        if case == "symlink":
            (tmp_path / "outside.csv").write_bytes(content)
            (datasets / f"{sha}.csv").symlink_to(tmp_path / "outside.csv")
            expected = {"detail": "dataset path escapes datasets directory"}
        elif case == "corrupt":
            (datasets / f"{sha}.csv").write_bytes(b"different")
            expected = {"detail": "content-addressed dataset is corrupt"}
        elif case == "dimensions":
            session.responses.append(ToolSuccess(summary={"rows": 0, "cols": 3}))
        else:
            expected = {"detail": "could not store uploaded dataset"}
            datasets.chmod(0o500)
        try:
            response = await client.post(
                f"/api/projects/{project['id']}/datasets",
                files={"file": ("data.csv", content, "text/csv")},
            )
        finally:
            datasets.chmod(0o700)
        assert response.status_code == 500
        assert response.json() == expected
        assert _readonly_count(settings.db_path, "datasets") == 0


@pytest.mark.asyncio
async def test_contract_write_failure_uses_os_error_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _open_project_api(tmp_path, monkeypatch) as (client, settings, _, project):
        contracts = ProjectPaths(settings.data_dir, "demo").contracts
        contracts.mkdir(parents=True, exist_ok=True)
        contracts.chmod(0o500)
        try:
            response = await client.post(
                f"/api/projects/{project['id']}/contracts",
                files={
                    "file": (
                        "partial.json",
                        DataContract(target=TargetSpec(column="a")).model_dump_json(),
                        "application/json",
                    )
                },
            )
        finally:
            contracts.chmod(0o700)
        assert response.status_code == 500
        assert response.json() == {"detail": "internal error"}


@pytest.mark.asyncio
async def test_routes_before_lifespan_are_unavailable(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            mcp_token=SecretStr("test-token"),
            data_dir=tmp_path / "data",
            db_path=tmp_path / "db.sqlite",
            max_concurrent_llm=1,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/api/projects")).status_code == 503
        assert (await client.get("/api/runs/r_missing")).status_code == 503
