"""Stable node-local context and accepted offline client accounting."""

from __future__ import annotations

import json
import sqlite3
import traceback
from pathlib import Path

import litellm
import pytest
from litellm import ModelResponse
from pydantic import BaseModel, JsonValue, SecretStr, TypeAdapter
from statlab.config import Settings
from statlab.errors import EngineError
from statlab.llm import LlmClient
from statlab.llm.prompts import PromptContext
from statlab.llm.skills import NodeSkill
from statlab.memory import read_memory, remember
from statlab.persistence.repository import (
    create_contract,
    create_node_execution,
    create_project,
    create_run,
)
from statlab_contracts.contract import DataContract


def _skill() -> NodeSkill:
    return NodeSkill(
        "node",
        "## node\nOwn section",
        "purpose",
        "heuristics",
        "",
        "outputs",
        "pitfalls",
        frozenset(),
    )


def test_prefix_tail_order_and_start_snapshot(
    migrated_db: sqlite3.Connection, minimal_contract: DataContract
) -> None:
    project = create_project(migrated_db, slug="context", name="Context")
    row = remember(migrated_db, project_id=project.id, key="first_fact", value=1)
    context = PromptContext(
        "Shared policy", _skill(), minimal_contract, read_memory(migrated_db, project.id)
    )
    remember(migrated_db, project_id=project.id, key="later_fact", value=2)
    messages = context.messages(
        state_summary={"step": 1}, recent_tool_results=({"value": 2},), question="First question"
    )
    later = context.messages(state_summary={"step": 3}, recent_tool_results=(), question="Next")
    canonical_contract = json.dumps(
        minimal_contract.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    canonical_memory = json.dumps(
        [{"key": row.key, "value": row.value(), "source_run_id": row.source_run_id}],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert messages[0] == {
        "role": "system",
        "content": "\n\n".join(
            ("Shared policy", canonical_contract, canonical_memory, "null", "## node\nOwn section")
        ),
    }
    assert messages[0] == later[0]
    assert messages[1] == {
        "role": "user",
        "content": '{"step":1}\n\n[{"value":2}]\n\nFirst question',
    }
    assert "later_fact" not in messages[0]["content"]
    assert context.memory == (row,)


def test_shared_digest_precedes_skill_section(minimal_contract: DataContract) -> None:
    context = PromptContext("P", _skill(), minimal_contract, (), {"b": 1})
    messages = context.messages(state_summary={}, recent_tool_results=(), question="Q")
    assert messages[0]["content"].endswith('\n\n{"b":1}\n\n## node\nOwn section')


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("in_tool_result", [False, True])
def test_nonfinite_prompt_json_is_safe_engine_error(
    minimal_contract: DataContract, nonfinite: float, in_tool_result: bool
) -> None:
    payload: JsonValue = TypeAdapter(JsonValue).validate_python(
        {"private-payload-detail": nonfinite}, strict=True
    )
    context = PromptContext("Policy", _skill(), minimal_contract)
    with pytest.raises(EngineError, match="prompt JSON could not be serialized") as caught:
        context.messages(
            state_summary={} if in_tool_result else payload,
            recent_tool_results=(payload,) if in_tool_result else (),
            question="Question",
        )
    assert caught.value.__cause__ is None
    rendered = "".join(traceback.format_exception(caught.value))
    assert "private-payload-detail" not in rendered
    assert "ValueError" not in rendered


class Decision(BaseModel):
    value: str


@pytest.mark.asyncio
async def test_real_client_offline_mock_and_audit(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = create_project(migrated_db, slug="prompt-client", name="Prompt client")
    contract = create_contract(
        migrated_db,
        project_id=project.id,
        path=tmp_path / "contract.json",
        contract=minimal_contract,
    )
    run = create_run(
        migrated_db,
        project_id=project.id,
        contract_id=contract.id,
        mode="autonomous",
        config_json="{}",
    )
    execution = create_node_execution(migrated_db, run_id=run.id, node_name="node", seq=0)
    context = PromptContext("Policy", _skill(), minimal_contract)
    messages = context.messages(state_summary={}, recent_tool_results=(), question="Return value")
    real_completion = litellm.acompletion
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)

    async def offline(**kwargs: object) -> ModelResponse:
        assert kwargs["messages"] == list(messages)
        response = await real_completion(**kwargs, mock_response='{"value":"assembled"}')
        assert isinstance(response, ModelResponse)
        return response

    settings = Settings(
        mcp_token=SecretStr("test-token"), data_dir=tmp_path, db_path=tmp_path / "db"
    )
    result = await LlmClient(settings, acompletion=offline).complete(
        migrated_db,
        run_id=run.id,
        node_execution_id=execution.id,
        model="openai/gpt-4o-mini",
        messages=messages,
        output=Decision,
    )
    assert result == Decision(value="assembled")
    rows = migrated_db.execute("SELECT run_id, node_execution_id, model FROM llm_calls").fetchall()
    assert [tuple(row) for row in rows] == [(run.id, execution.id, "openai/gpt-4o-mini")]
