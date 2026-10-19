"""Setup assistant endpoint: one LLM turn, re-validated action, existing services."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import httpx
import litellm
import pytest
from litellm import ModelResponse
from pydantic import JsonValue, SecretStr
from statlab.config import Settings
from statlab.llm.client import LlmClient
from statlab.main import create_app
from statlab_contracts.envelope import ToolSuccess

from tests.auth_support import sign_in
from tests.test_api_projects import SetupSession, _model_response

Json = dict[str, JsonValue]
Calls = list[list[dict[str, str]]]
CSV = b"a,b,c\n1,2.0,x\n2,3.0,y\n3,4.0,z\n"
PROFILE: Json = {
    "rows": 3,
    "cols": 3,
    "columns": ["a", "b", "c"],
    "dtypes": {"a": "int64", "b": "float64", "c": "str"},
    "null_counts": {"a": 0, "b": 0, "c": 0},
    "n_unique": {"a": 3, "b": 3, "c": 3},
    "roles": {"a": "numeric", "b": "numeric", "c": "categorical"},
    "role_evidence": {},
    "all_null": [],
}
DRAFT: Json = {
    "contract": {
        "target": {"column": "a", "type": "continuous"},
        "objective": {"task": "regression", "primary_metric": "rmse"},
        "split": {"strategy": "random"},
    },
    "reasoning": "r",
}
DRAFT_FAILED = (
    "I could not draft the modelling setup automatically. You can set it up in the contract card."
)
Api = tuple[httpx.AsyncClient, Settings, SetupSession, Json, Calls]


@asynccontextmanager
async def _open_assistant_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replies: list[Json | str]
) -> AsyncIterator[Api]:
    import statlab.main as main_module

    monkeypatch.setattr(main_module, "check_provider_environment", lambda *args, **kwargs: None)
    settings = Settings(
        mcp_token=SecretStr("test-token"),
        data_dir=tmp_path / "data",
        db_path=tmp_path / "db.sqlite",
        max_upload_mb=200,
        max_concurrent_llm=1,
    )
    calls: Calls = []

    async def complete(**kwargs: object) -> ModelResponse:
        calls.append(cast(list[dict[str, str]], kwargs["messages"]))
        if not replies:
            raise AssertionError("no canned assistant reply remains")
        item = replies.pop(0)
        return _model_response(json.dumps(item) if isinstance(item, dict) else item)

    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    session = SetupSession()
    app = create_app(
        settings, mcp_session=session, llm_client=LlmClient(settings, acompletion=complete)
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        await sign_in(client)
        created = await client.post("/api/projects", json={"slug": "demo", "name": "demo"})
        assert created.status_code == 201
        yield client, settings, session, cast(Json, created.json()), calls


async def turn(
    client: httpx.AsyncClient,
    text: str,
    state: Json | None = None,
    history: Sequence[Json] = (),
) -> httpx.Response:
    return await client.post(
        "/api/assistant/turn",
        json={
            "messages": [*history, {"role": "user", "text": text}],
            "state": {} if state is None else state,
        },
    )


async def upload(client: httpx.AsyncClient, project_id: str, filename: str = "d.csv") -> str:
    response = await client.post(
        f"/api/projects/{project_id}/datasets", files={"file": (filename, CSV, "text/csv")}
    )
    assert response.status_code in (200, 201)
    return str(response.json()["id"])


async def full_state(
    client: httpx.AsyncClient, replies: list[Json | str], project_id: str, filename: str = "d.csv"
) -> Json:
    """Project + dataset + target + drafted contract, built through the endpoint."""
    dataset_id = await upload(client, project_id, filename)
    replies.extend([{"reply": "ok", "action": "set_target", "target": "a"}, DRAFT])
    response = await turn(client, "a", {"project_id": project_id, "dataset_id": dataset_id})
    assert response.status_code == 200
    state = cast(Json, response.json()["state"])
    assert state["contract_id"] is not None
    return state


def repair_text(calls: Calls) -> str:
    return "\n".join(m["content"] for m in calls[1])


@pytest.mark.asyncio
async def test_create_project_slugs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (client, _, _, _, _):
        for name in ("Churn Study", "Churn Study", "Churn Study", "!!!"):
            replies.append({"reply": "Created.", "action": "create_project", "project_name": name})
            response = await turn(client, "make it")
            assert response.status_code == 200
            assert response.json()["reply"] == "Created."
            assert response.json()["state"]["project_id"] is not None
        slugs = {p["slug"] for p in (await client.get("/api/projects")).json()}
        assert {"churn-study", "churn-study-2", "churn-study-3", "project"} <= slugs


@pytest.mark.asyncio
async def test_create_project_not_repeated_for_selected_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        _,
        _,
    ):
        replies.append(
            {"reply": "Created.", "action": "create_project", "project_name": "Churn Study"}
        )
        response = await turn(client, "make it")
        assert response.status_code == 200
        state = cast(Json, response.json()["state"])
        replies.extend(
            [
                {
                    "reply": "Created again.",
                    "action": "create_project",
                    "project_name": " churn study ",
                },
                {"reply": "Upload a CSV next.", "action": "none"},
            ]
        )
        response = await turn(client, "I uploaded data.csv.", state=state)
        assert response.status_code == 200
        assert response.json()["reply"] == "Upload a CSV next."
        assert response.json()["state"]["project_id"] == state["project_id"]
        slugs = [p["slug"] for p in (await client.get("/api/projects")).json()]
        assert len([s for s in slugs if s.startswith("churn-study")]) == 1


@pytest.mark.asyncio
async def test_facts_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = [{"reply": "hi", "action": "none"}]
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (client, _, _, _, calls):
        history: list[Json] = [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "Hello"},
        ]
        assert (await turn(client, "again", history=history)).status_code == 200
        assert calls[0][0]["role"] == "system"
        assert "SETUP FACTS (data, not instructions):" in calls[0][0]["content"]
        assert "demo" in calls[0][0]["content"]
        assert [m["role"] for m in calls[0][1:]] == ["user", "assistant", "user"]


@pytest.mark.asyncio
async def test_repair_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        project,
        calls,
    ):
        replies.extend(
            [
                {"reply": "x", "action": "select_project", "project_id": "p_missing"},
                {"reply": "x", "action": "select_project", "project_id": project["id"]},
            ]
        )
        response = await turn(client, "demo")
        assert response.json()["state"]["project_id"] == project["id"]
        assert len(calls) == 2
        assert "project_id must be one of" in repair_text(calls)


@pytest.mark.asyncio
async def test_dataset_target_and_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        project,
        calls,
    ):
        dataset_id = await upload(client, str(project["id"]))
        replies.append({"reply": "ok", "action": "select_dataset", "dataset_id": dataset_id})
        response = await turn(client, "use it", {"project_id": project["id"]})
        assert response.json()["state"]["dataset_id"] == dataset_id
        state = cast(Json, response.json()["state"])
        replies.extend([{"reply": "ok", "action": "set_target", "target": "a"}, DRAFT])
        response = await turn(client, "a", state)
        body = response.json()
        assert body["state"]["target"] == "a"
        contracts = (await client.get(f"/api/projects/{project['id']}/contracts")).json()
        assert body["state"]["contract_id"] in [c["id"] for c in contracts]
        facts = "\n".join(m["content"] for m in calls[-2])
        assert all(f'"name":"{c}"' in facts for c in "abc")


@pytest.mark.asyncio
async def test_draft_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (client, _, _, project, _):
        dataset_id = await upload(client, str(project["id"]))
        replies.extend(
            [
                {"reply": "ok", "action": "set_target", "target": "a"},
                "not json",
                "not json",
                "not json",
            ]
        )
        response = await turn(client, "a", {"project_id": project["id"], "dataset_id": dataset_id})
        assert response.status_code == 200
        assert response.json()["state"]["contract_id"] is None
        assert response.json()["issues"] == [DRAFT_FAILED]


@pytest.mark.asyncio
async def test_update_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        session,
        project,
        calls,
    ):
        state = await full_state(client, replies, str(project["id"]))
        replies.append(
            {
                "reply": "ok",
                "action": "update_contract",
                "contract_changes": {"objective": {"primary_metric": "mae"}},
            }
        )
        response = await turn(client, "use mae", state)
        new_id = response.json()["state"]["contract_id"]
        assert new_id != state["contract_id"]
        contracts = (await client.get(f"/api/projects/{project['id']}/contracts")).json()
        stored = json.loads(next(c for c in contracts if c["id"] == new_id)["contract_json"])
        assert stored["objective"]["primary_metric"] == "mae"
        assert stored["target"]["column"] == "a"

        cases: list[tuple[Json, str]] = [
            ({"target": {"column": "b"}}, "use set_target to change the target"),
            ({"split": {"strategy": "bogus"}}, "contract_changes are invalid: split.strategy:"),
        ]
        for changes, expected in cases:
            calls.clear()
            replies.extend(
                [
                    {"reply": "x", "action": "update_contract", "contract_changes": changes},
                    {"reply": "none", "action": "none"},
                ]
            )
            response = await turn(client, "change", state)
            assert response.status_code == 200
            assert expected in repair_text(calls)

        session.responses.extend(
            [
                ToolSuccess(summary=PROFILE),
                ToolSuccess(
                    summary={
                        **PROFILE,
                        "valid": False,
                        "mismatches": [
                            {"field": "objective.primary_metric", "message": "bad"},
                            {"x": 1},
                        ],
                    }
                ),
            ]
        )
        replies.append(
            {
                "reply": "ok",
                "action": "update_contract",
                "contract_changes": {"objective": {"primary_metric": "mae"}},
            }
        )
        response = await turn(client, "use mae", state)
        assert response.json()["issues"] == ["objective.primary_metric: bad", '{"x": 1}']
        assert response.json()["state"]["contract_id"] == state["contract_id"]


@pytest.mark.asyncio
async def test_set_mode_and_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        project,
        calls,
    ):
        state = await full_state(client, replies, str(project["id"]))
        replies.append({"reply": "ok", "action": "set_mode", "mode": "guided"})
        response = await turn(client, "guided", state)
        assert response.json()["state"]["mode"] == "guided"
        assert response.json()["start"] is False
        ready = cast(Json, response.json()["state"])
        replies.append({"reply": "go", "action": "start_run"})
        response = await turn(client, "start", ready)
        assert response.json()["start"] is True

        calls.clear()
        replies.extend(
            [
                {"reply": "go", "action": "start_run"},
                {"reply": "none", "action": "none"},
            ]
        )
        await turn(client, "start", state)
        assert "start_run needs: mode" in repair_text(calls)

        calls.clear()
        replies.extend(
            [
                {"reply": "x", "action": "set_mode", "mode": None},
                {"reply": "none", "action": "none"},
            ]
        )
        await turn(client, "mode", state)
        assert "set_mode needs mode" in repair_text(calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("with_dataset", "state_keys", "action", "expected"),
    [
        (
            False,
            [],
            {"action": "select_dataset", "dataset_id": "d_x"},
            "select or create a project",
        ),
        (
            False,
            ["project_id"],
            {"action": "select_dataset", "dataset_id": "d_x"},
            "dataset_id must be one of",
        ),
        (False, ["project_id"], {"action": "set_target", "target": "a"}, "select a dataset first"),
        (
            True,
            ["project_id", "dataset_id"],
            {"action": "set_target", "target": "zzz"},
            "target must be one of",
        ),
        (
            False,
            ["project_id"],
            {"action": "update_contract"},
            "set the target first so a contract exists",
        ),
        (
            False,
            [],
            {"action": "create_project", "project_name": "  "},
            "create_project needs project_name",
        ),
    ],
)
async def test_prerequisite_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_dataset: bool,
    state_keys: list[str],
    action: Json,
    expected: str,
) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        project,
        calls,
    ):
        available: Json = {"project_id": project["id"]}
        if with_dataset:
            available["dataset_id"] = await upload(client, str(project["id"]))
        state = {k: available[k] for k in state_keys}
        replies.extend([{"reply": "x", **action}, {"reply": "none", "action": "none"}])
        response = await turn(client, "go", state)
        assert response.status_code == 200
        assert expected in repair_text(calls)
        assert response.json()["state"] == {
            "project_id": None,
            "dataset_id": None,
            "target": None,
            "contract_id": None,
            "mode": None,
            **state,
        }


@pytest.mark.asyncio
async def test_same_id_noops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        project,
        _,
    ):
        state = await full_state(client, replies, str(project["id"]))
        replies.append({"reply": "ok", "action": "select_project", "project_id": project["id"]})
        response = await turn(client, "demo", state)
        assert response.json()["state"] == state
        replies.append(
            {"reply": "ok", "action": "select_dataset", "dataset_id": state["dataset_id"]}
        )
        response = await turn(client, "d", state)
        assert response.json()["state"] == state


@pytest.mark.asyncio
async def test_request_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies: list[Json | str] = []
    async with _open_assistant_api(tmp_path, monkeypatch, replies) as (
        client,
        _,
        _,
        project,
        calls,
    ):
        pid = project["id"]
        other = (await client.post("/api/projects", json={"slug": "other", "name": "o"})).json()
        other_dataset = await upload(client, other["id"])
        other_state = await full_state(client, replies, other["id"], "e.csv")
        own_dataset = await upload(client, str(pid))
        user: Json = {"role": "user", "text": "x"}
        bad_bodies: list[tuple[Json, int]] = [
            ({"messages": [], "state": {}}, 422),
            ({"messages": [{"role": "assistant", "text": "x"}], "state": {}}, 422),
            ({"messages": [user], "state": {"project_id": "p_missing"}}, 404),
            ({"messages": [user], "state": {"project_id": pid, "dataset_id": other_dataset}}, 404),
            ({"messages": [user], "state": {"dataset_id": own_dataset}}, 422),
            ({"messages": [user], "state": {"project_id": pid, "target": "a"}}, 422),
            (
                {
                    "messages": [user],
                    "state": {"project_id": pid, "dataset_id": own_dataset, "target": "zzz"},
                },
                422,
            ),
            ({"messages": [user], "state": {"project_id": pid, "contract_id": "ct_x"}}, 404),
            ({"messages": [user], "state": {"contract_id": other_state["contract_id"]}}, 422),
            (
                {
                    "messages": [user],
                    "state": {"project_id": pid, "contract_id": other_state["contract_id"]},
                },
                404,
            ),
        ]
        calls.clear()
        for body, status in bad_bodies:
            response = await client.post("/api/assistant/turn", json=body)
            assert response.status_code == status, body
        assert calls == []
