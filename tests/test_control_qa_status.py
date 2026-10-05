"""Offline captured Q&A/status tests; execution deferred by user."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace

import pytest
from pydantic import ValidationError
from statlab.domains.base import NodeResult
from statlab.errors import EngineError, LlmOutputError
from statlab.graph.candidates import BudgetState, TraversalPlan
from statlab.graph.control import (
    ArtifactSummary,
    ControlText,
    ConversationTurn,
    QaAnswer,
    answer_question,
    explain_status,
    status_facts,
)
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.memory import read_memory, remember
from statlab.persistence.repository import create_project
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec
from statlab_contracts.contract import Constraints, DataContract
from statlab_contracts.envelope import Failure

from tests.test_control_router import FakeClient, context
from tests.test_graph_state import candidate_spec, node


@pytest.mark.parametrize(
    ("skill_model", "current", "expected", "reasoning"),
    [
        (None, "alpha", "reason-model", True),
        ("default", "alpha", "default-model", False),
        ("literal-model", "alpha", "literal-model", True),
        ("classify", "alpha", "classify-model", False),
        (None, None, "default-model", False),
    ],
)
@pytest.mark.asyncio
async def test_qa_current_role_and_skill_precedence(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    skill_model: str | None,
    current: str | None,
    expected: str,
    reasoning: bool,
) -> None:
    client = FakeClient(QaAnswer(text="Evidence answer"))
    ctx = context(
        migrated_db, minimal_contract, client, current_node=current, skill_model=skill_model
    )
    result = await answer_question("Why?", ctx)
    assert result.text == "Evidence answer"
    assert client.calls[0][:2] == (expected, reasoning)
    messages = client.calls[0][2]
    assert messages[0]["content"].startswith("Captured policy\n\n## alpha")
    assert "Never recommend test-based selection" in messages[1]["content"]


@pytest.mark.parametrize("visible_source", ["boundary", "summary", "pending"])
@pytest.mark.asyncio
async def test_qa_citations_only_visible_captured_refs(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    visible_source: str,
) -> None:
    ref = ArtifactRef("a_1111111111111111")
    client = FakeClient(QaAnswer(text="Cited evidence", references=(ref,)))
    ctx = context(migrated_db, minimal_contract, client)
    boundary = BoundaryResult(
        node_name="alpha",
        execution_id="ne_alpha",
        result=NodeResult(status="completed", artifacts=(ref,)),
    )
    if visible_source == "boundary":
        state = RunState(
            run_id=ctx.state.run_id,
            spine_id=ctx.state.spine_id,
            spine=ctx.state.spine,
            mode=ctx.state.mode,
            cursor=1,
            shared_results=(boundary,),
        )
        ctx = replace(ctx, state=state)
    elif visible_source == "pending":
        pending = BoundaryResult(
            node_name="alpha",
            execution_id="ne_alpha",
            result=NodeResult(status="awaiting_user", artifacts=(ref,)),
        )
        state = RunState(
            run_id=ctx.state.run_id,
            spine_id=ctx.state.spine_id,
            spine=ctx.state.spine,
            mode=ctx.state.mode,
            status="awaiting_user",
            pending_result=pending,
        )
        ctx = replace(ctx, state=state)
    else:
        ctx = replace(ctx, artifact_summaries=(ArtifactSummary(ref=ref, text="Small summary"),))
    response = await answer_question("Explain citation", ctx)
    assert response.references == (ref,)
    assert ref in client.calls[0][2][-1]["content"]


@pytest.mark.asyncio
async def test_qa_invented_well_formed_reference_rejected_without_repair_layer(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    client = FakeClient(QaAnswer(text="Invented", references=(ArtifactRef("a_2222222222222222"),)))
    with pytest.raises(LlmOutputError, match="unavailable artifacts"):
        await answer_question("Explain", context(migrated_db, minimal_contract, client))
    assert len(client.calls) == 1


def test_qa_reference_syntax_and_nonblank_text() -> None:
    with pytest.raises(ValidationError):
        QaAnswer.model_validate({"text": "Answer", "references": ["invalid"]})
    with pytest.raises(ValidationError):
        QaAnswer.model_validate({"text": "   "})


@pytest.mark.asyncio
async def test_status_template_unknown_eta_and_polish_cannot_replace_facts(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    client = FakeClient(ControlText(text="Brief extra context"))
    ctx = context(migrated_db, minimal_contract, client)
    response = await explain_status("status", ctx)
    polished = await explain_status("status", ctx, polish=True)
    assert response.facts == polished.facts == status_facts(ctx)
    assert response.template == polished.template == response.text
    assert "Remaining time: unknown." in response.text
    assert polished.text == "Brief extra context"
    assert len(client.calls) == 1
    assert client.calls[0][:2] == ("classify-model", False)


@pytest.mark.asyncio
async def test_large_run_status_bounds_presentation_retains_all_canonical_facts(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    contract = DataContract(
        target=minimal_contract.target, constraints=Constraints(max_candidates=64)
    )
    client = FakeClient()
    ctx = context(migrated_db, contract, client)
    initial = RunState.initial(
        run_id=ctx.state.run_id,
        spine_id=ctx.state.spine_id,
        nodes=(node(), node("beta", candidate=True)),
        mode="autonomous",
    )
    branches = tuple(
        CandidateProgress(
            public_id=f"c_{index:02d}",
            position=1,
            status="failed",
            spec=CandidateSpec(
                label="L" * 120,
                rationale="A distinct candidate for bounded status presentation.",
                estimator=EstimatorSpec(name="synthetic", params={"variant": index}),
            ),
            pending_result=BoundaryResult(
                node_name="beta",
                execution_id=f"ne_{index}",
                result=NodeResult(
                    status="failed",
                    error=Failure(
                        error_class="data",
                        message=f"Candidate {index} could not fit.",
                    ),
                ),
            ),
        )
        for index in range(1, contract.constraints.max_candidates + 1)
    )
    state = RunState(
        run_id=initial.run_id,
        spine_id=initial.spine_id,
        spine=initial.spine,
        cursor=1,
        status="failed",
        mode=initial.mode,
        shared_results=(boundary("alpha"),),
        branches=branches,
        traversal=TraversalPlan(),
        budget=BudgetState(total_fit_ms=101, limit_ms=100),
    )
    response = await explain_status("status", replace(ctx, state=state, current_node=None))
    assert response.template == response.text
    assert len(response.text) <= 8000
    assert "Details truncated; complete status facts are included." in response.text
    assert "Candidates: 64 (failed=64). Pending boundaries: 64 (failed=64)." in response.text
    assert "Candidate c_01" in response.text
    assert "Fit budget: 101/100 ms; exhausted: True." in response.text
    assert response.facts is not None
    assert len(response.facts.candidates) == len(response.facts.pending) == 64
    assert response.facts.candidates[-1].public_id == "c_64"
    assert response.facts.candidates[-1].label == "L" * 120
    assert branches[-1].pending_result is not None
    assert response.facts.pending[-1].error == branches[-1].pending_result.result.error
    assert client.calls == []


@pytest.mark.parametrize("seconds", [0.0, 12.5])
@pytest.mark.asyncio
async def test_status_caller_eta_explicitly_approximate(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    seconds: float,
) -> None:
    ctx = replace(
        context(migrated_db, minimal_contract, FakeClient()),
        estimated_remaining_seconds=seconds,
        current_node=None,
    )
    response = await explain_status("status", ctx)
    assert response.facts is not None
    assert response.facts.approximate_remaining_seconds == seconds
    assert "approximately" in response.text
    assert "between/after nodes" in response.text


@pytest.mark.parametrize("seconds", [-1.0, float("inf"), float("nan")])
def test_status_invalid_estimate_rejected(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    seconds: float,
) -> None:
    with pytest.raises(EngineError, match="finite and nonnegative"):
        replace(
            context(migrated_db, minimal_contract, FakeClient()),
            estimated_remaining_seconds=seconds,
        )


def boundary(name: str, *, skipped: bool = False) -> BoundaryResult:
    return BoundaryResult(
        node_name=name,
        execution_id="ne_" + name,
        result=NodeResult(
            status="skipped" if skipped else "completed",
            skip_reason="Not needed" if skipped else None,
        ),
    )


def test_status_retune_history_counts_declared_positions_once(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    ctx = context(migrated_db, minimal_contract, FakeClient())
    initial = RunState.initial(
        run_id="r_control",
        spine_id="synthetic",
        nodes=(node(), node("beta", candidate=True), node("gamma", candidate=True), node("delta")),
        mode="guided",
    )
    branch = CandidateProgress(
        public_id="c_01",
        spec=candidate_spec(),
        position=3,
        retune_count=1,
        status="completed",
        results=(boundary("beta"), boundary("gamma"), boundary("beta"), boundary("gamma")),
    )
    state = RunState(
        run_id=initial.run_id,
        spine_id=initial.spine_id,
        spine=initial.spine,
        mode=initial.mode,
        cursor=3,
        status="running",
        shared_results=(boundary("alpha", skipped=True),),
        branches=(branch,),
        traversal=TraversalPlan(training_node="beta", evaluation_node="gamma"),
    )
    facts = status_facts(replace(ctx, state=state, current_node=None))
    assert facts.completed_shared == ()
    assert facts.skipped_shared == ("alpha",)
    assert facts.remaining_shared == ("delta",)
    assert facts.candidates[0].completed == ("beta", "gamma")
    assert facts.candidates[0].remaining == ()
    assert facts.candidates[0].retune_count == 1


@pytest.mark.parametrize("budget_exhausted", [False, True])
def test_status_candidate_failure_gate_and_budget_facts(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
    budget_exhausted: bool,
) -> None:
    ctx = context(migrated_db, minimal_contract, FakeClient())
    initial = RunState.initial(
        run_id="r_control",
        spine_id="synthetic",
        nodes=(node(), node("beta", candidate=True)),
        mode="autonomous",
    )
    pending = BoundaryResult(
        node_name="beta",
        execution_id="ne_beta",
        result=NodeResult(status="failed", error=Failure(error_class="data", message="No fit")),
    )
    branch = CandidateProgress(
        public_id="c_01",
        spec=candidate_spec(),
        position=1,
        status="budget_exhausted" if budget_exhausted else "failed",
        pending_result=pending,
    )
    state = RunState(
        run_id=initial.run_id,
        spine_id=initial.spine_id,
        spine=initial.spine,
        mode=initial.mode,
        cursor=1,
        status="failed",
        shared_results=(boundary("alpha"),),
        branches=(branch,),
        traversal=TraversalPlan(),
        budget=BudgetState(total_fit_ms=101, limit_ms=100),
    )
    facts = status_facts(replace(ctx, state=state, current_node=None))
    assert facts.candidates[0].status == branch.status
    assert facts.candidates[0].current_node == "beta"
    assert facts.pending[0].error == pending.result.error
    assert facts.budget is not None and facts.budget.exhausted
    assert facts.approximate_remaining_seconds is None


@pytest.mark.asyncio
async def test_context_memory_snapshot_nonmutation_and_concurrent_read_only_calls(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    project = create_project(migrated_db, slug="control-memory", name="Control memory")
    remember(migrated_db, project_id=project.id, key="before", value=1)
    client = FakeClient(QaAnswer(text="First"), QaAnswer(text="Second"))
    client.entered, client.release = asyncio.Event(), asyncio.Event()
    ctx = replace(
        context(migrated_db, minimal_contract, client),
        memory=read_memory(migrated_db, project.id),
        conversation=(ConversationTurn(role="user", text="Earlier question"),),
    )
    before = ctx.state.model_dump_json()
    remember(migrated_db, project_id=project.id, key="after", value=2)
    first = asyncio.create_task(answer_question("One", ctx))
    second = asyncio.create_task(answer_question("Two", ctx))
    try:
        await client.entered.wait()
        response = await explain_status("status", ctx)
        assert response.facts is not None
        assert not first.done() and not second.done()
        client.release.set()
        results = await asyncio.gather(first, second)
    finally:
        client.release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert {result.text for result in results} == {"First", "Second"}
    assert ctx.state.model_dump_json() == before
    assert len(ctx.memory) == 1
    assert all(
        '"before"' in call[2][0]["content"] and '"after"' not in call[2][0]["content"]
        for call in client.calls
    )


def test_context_bounds_and_current_node_membership(
    migrated_db: sqlite3.Connection,
    minimal_contract: DataContract,
) -> None:
    ctx = context(migrated_db, minimal_contract, FakeClient())
    with pytest.raises(EngineError, match="outside captured spine"):
        replace(ctx, current_node="undeclared")
    with pytest.raises(EngineError, match="conversation"):
        replace(ctx, conversation=(ConversationTurn(role="user", text="x"),) * 21)
    with pytest.raises(EngineError, match="artifact summaries"):
        replace(
            ctx,
            artifact_summaries=(ArtifactSummary(ref=ArtifactRef("a_1111111111111111"), text="x"),)
            * 33,
        )
