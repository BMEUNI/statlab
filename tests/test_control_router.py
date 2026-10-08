"""Offline control dispatch tests; execution intentionally deferred by user."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import litellm
import pytest
from pydantic import BaseModel, JsonValue, SecretStr, ValidationError
from statlab.config import Settings
from statlab.errors import EngineError, LlmCallError, PersistenceError
from statlab.graph.control import (
    ControlContext,
    ControlResponse,
    InterruptCategory,
    QaAnswer,
)
from statlab.graph.router import ControlRouter, InterruptClass
from statlab.graph.state import RunState
from statlab.llm.client import CallRecovery, LlmClient
from statlab.llm.routing import ModelRouter, Role
from statlab.llm.skills import NodeSkill, SkillPolicy
from statlab.persistence.repository import (
    create_contract,
    create_node_execution,
    create_project,
    create_run,
)
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import Failure

from tests.test_graph_state import node


class FakeClient(LlmClient):
    def __init__(self, *responses: BaseModel) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, bool, tuple[dict[str, str], ...]]] = []
        self.error: BaseException | None = None
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def complete[T: BaseModel](
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        node_execution_id: str,
        model: str,
        messages: Sequence[dict[str, str]],
        output: type[T],
        reasoning: bool = False,
        example: dict[str, JsonValue] | None = None,
        recovery: CallRecovery | None = None,
        check: Callable[[T], None] | None = None,
    ) -> T:
        self.calls.append((model, reasoning, tuple(messages)))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.error is not None:
            raise self.error
        return output.model_validate_json(self.responses.pop(0).model_dump_json())


def policy(model: str | None = None) -> SkillPolicy:
    return SkillPolicy(
        "Captured policy",
        (
            NodeSkill(
                "alpha",
                "## alpha\nCaptured section",
                "purpose",
                "heuristics",
                "",
                "outputs",
                "pitfalls",
                frozenset(),
                model,
            ),
        ),
        b"snapshot",
    )


def context(
    conn: sqlite3.Connection,
    contract: DataContract,
    client: LlmClient,
    *,
    current_node: str | None = "alpha",
    skill_model: str | None = None,
) -> ControlContext:
    return ControlContext(
        conn=conn,
        llm=client,
        router=ModelRouter(
            {
                "alpha": Role.REASONING,
                "interrupt_router": Role.CLASSIFY,
                "status_explainer": Role.CLASSIFY,
            },
            {
                "STATLAB_MODEL_DEFAULT": "default-model",
                "STATLAB_MODEL_REASONING": "reason-model",
                "STATLAB_MODEL_CLASSIFY": "classify-model",
            },
        ),
        state=RunState.initial(
            run_id="r_control", spine_id="synthetic", nodes=(node(),), mode="autonomous"
        ),
        contract=contract,
        skill=policy(skill_model),
        audit_execution_id="ne_control",
        current_node=current_node,
    )


@pytest.mark.parametrize("category", ["qa", "status", "decision", "change", "lifecycle", "noise"])
@pytest.mark.asyncio
async def test_all_dispatch_categories_preserve_original_context(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    category: InterruptCategory,
) -> None:
    classification = InterruptClass(
        category=category,
        lifecycle_action="pause" if category == "lifecycle" else None,
    )
    client = FakeClient(classification, QaAnswer(text="Captured answer"))
    ctx = context(migrated_db, minimal_contract, client)
    received: list[tuple[str, InterruptClass, ControlContext]] = []

    async def callback(
        raw: str, intent: InterruptClass, captured: ControlContext
    ) -> ControlResponse:
        received.append((raw, intent, captured))
        return ControlResponse(
            category=intent.category, text="Handled by caller", new_run_id="r_new"
        )

    raw = "  original message  "
    response = await ControlRouter(decision=callback, change=callback, lifecycle=callback).handle(
        raw, ctx
    )
    assert response.category == category
    assert client.calls[0][0:2] == ("classify-model", False)
    assert client.calls[0][2][-1]["content"] == raw
    assert len(client.calls) == (2 if category == "qa" else 1)
    if category in ("decision", "change", "lifecycle"):
        assert received == [(raw, classification, ctx)]
        assert received[0][2] is ctx
    else:
        assert received == []
    if category == "noise":
        assert response.text == (
            'I\'m here. Ask me anything about this run, for example "compare the models".'
        )


@pytest.mark.parametrize("category", ["decision", "change", "lifecycle"])
@pytest.mark.asyncio
async def test_mutating_handler_missing_fails_clearly(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    category: InterruptCategory,
) -> None:
    client = FakeClient(
        InterruptClass(
            category=category,
            lifecycle_action="resume" if category == "lifecycle" else None,
        )
    )
    with pytest.raises(EngineError, match="not configured"):
        await ControlRouter().handle("instruction", context(migrated_db, minimal_contract, client))


@pytest.mark.parametrize(
    "payload",
    [
        {"category": "other"},
        {"category": "lifecycle"},
        {"category": "qa", "lifecycle_action": "pause"},
        {"category": "lifecycle", "lifecycle_action": "stop"},
        {"category": "decision", "params": {}},
    ],
)
def test_classifier_schema_rejects_inconsistent_intent(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InterruptClass.model_validate(payload)


@pytest.mark.parametrize("action", ["pause", "resume", "cancel"])
def test_lifecycle_actions_validate(action: str) -> None:
    assert InterruptClass.model_validate({"category": "lifecycle", "lifecycle_action": action})


@pytest.mark.parametrize("raw", ["", "   ", "x" * 8001])
@pytest.mark.asyncio
async def test_invalid_message_never_calls_client(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    raw: str,
) -> None:
    client = FakeClient()
    with pytest.raises(ValidationError):
        await ControlRouter().handle(raw, context(migrated_db, minimal_contract, client))
    assert client.calls == []


@pytest.mark.asyncio
async def test_callback_category_mismatch_rejected(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    client = FakeClient(InterruptClass(category="decision"))

    async def callback(raw: str, intent: InterruptClass, ctx: ControlContext) -> ControlResponse:
        return ControlResponse(category="noise", text="Wrong route")

    with pytest.raises(EngineError, match="different category"):
        await ControlRouter(decision=callback).handle(
            "approve", context(migrated_db, minimal_contract, client)
        )


@pytest.mark.asyncio
async def test_llm_domain_error_and_cancellation_propagate(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    client = FakeClient()
    ctx = context(migrated_db, minimal_contract, client)
    failure = LlmCallError(Failure(error_class="transient", message="offline failure"))
    client.error = failure
    with pytest.raises(LlmCallError) as caught:
        await ControlRouter().handle("question", ctx)
    assert caught.value is failure
    cancelled = asyncio.CancelledError()
    client.error = cancelled
    with pytest.raises(asyncio.CancelledError) as cancel:
        await ControlRouter().handle("question", ctx)
    assert cancel.value is cancelled


@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.asyncio
async def test_real_client_absent_or_foreign_audit_anchor_rejected_before_completion(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    tmp_path: Path,
    foreign: bool,
) -> None:
    calls: list[object] = []

    async def offline(**kwargs: object) -> object:
        calls.append(kwargs)
        raise AssertionError("invalid anchor must precede provider dispatch")

    client = LlmClient(
        Settings(mcp_token=SecretStr("test-token"), data_dir=tmp_path, db_path=tmp_path / "db"),
        acompletion=offline,
    )
    ctx = context(migrated_db, minimal_contract, client)
    if foreign:
        project = create_project(migrated_db, slug="control-audit", name="Control audit")
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
        execution = create_node_execution(migrated_db, run_id=run.id, node_name="alpha", seq=0)
        ctx = replace(ctx, audit_execution_id=execution.id)
    with pytest.raises(PersistenceError, match="does not belong"):
        await ControlRouter().classify("question", ctx)
    assert calls == []


@pytest.mark.asyncio
async def test_same_run_anchor_uses_existing_execution_without_phantom_rows(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from litellm import ModelResponse

    project = create_project(migrated_db, slug="control-valid", name="Control valid")
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
    execution = create_node_execution(migrated_db, run_id=run.id, node_name="alpha", seq=0)
    before = migrated_db.execute("SELECT COUNT(*) FROM node_executions").fetchone()[0]
    monkeypatch.setattr(litellm, "supports_response_schema", lambda model: True)
    monkeypatch.setattr(litellm, "completion_cost", lambda **kwargs: 0.0)

    async def offline(**kwargs: object) -> object:
        return ModelResponse(
            choices=[
                {
                    "message": {"role": "assistant", "content": '{"category":"noise"}'},
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    client = LlmClient(
        Settings(mcp_token=SecretStr("test-token"), data_dir=tmp_path, db_path=tmp_path / "db"),
        acompletion=offline,
    )
    ctx = context(migrated_db, minimal_contract, client)
    ctx = replace(ctx, state=replace_state_run(ctx.state, run.id), audit_execution_id=execution.id)
    response = await ControlRouter().handle("thanks", ctx)
    assert response.category == "noise"
    assert migrated_db.execute("SELECT COUNT(*) FROM node_executions").fetchone()[0] == before
    rows = migrated_db.execute("SELECT run_id, node_execution_id FROM llm_calls").fetchall()
    assert [tuple(row) for row in rows] == [(run.id, execution.id)]


def replace_state_run(state: RunState, run_id: str) -> RunState:
    return RunState(run_id=run_id, spine_id=state.spine_id, spine=state.spine, mode=state.mode)
