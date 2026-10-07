"""Offline cumulative search/checkpoint regressions; deliberately unexecuted."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from statlab.domains.base import NodeResult
from statlab.domains.candidate_models import CandidateModelExecutor
from statlab.domains.candidate_search import CandidateSearchPlanner
from statlab.domains.decisions import DesignDecision, SearchDecision
from statlab.domains.design_inference import DesignInferenceExecutor
from statlab.domains.foundation import _RefusalError
from statlab.domains.tabular import TABULAR_SPINE
from statlab.errors import EngineError, LlmOutputError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.llm.client import DecisionRejectedError
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import (
    create_node_execution,
    ensure_candidate,
    finish_node_execution,
)
from statlab_contracts.candidate import CandidateSet, CandidateSpec, EstimatorSpec
from statlab_contracts.contract import Constraints, DataContract
from statlab_contracts.envelope import Failure

from tests.test_nodes_candidate_pipeline import (
    candidate_harness,
    candidate_invocation,
    prepare_candidate,
    save_result,
)
from tests.test_nodes_foundation import Harness


def proposal(baseline: CandidateSpec) -> CandidateSpec:
    return CandidateSpec(
        label="Validation-motivated ridge",
        rationale="Compare regularization on validation",
        estimator=EstimatorSpec(name="ridge", params=baseline.estimator.params),
        preprocessing=baseline.preprocessing,
        features=baseline.features,
        varies_from_baseline=("estimator",),
    )


async def joined(h: Harness) -> None:
    await prepare_candidate(h)
    for index in (8, 9, 10):
        invocation = candidate_invocation(h, index)
        save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    branch = h.state.branches[0]
    completed = CandidateProgress(
        public_id=branch.public_id,
        spec=branch.spec,
        results=branch.results,
        position=11,
        status="completed",
    )
    design = next(item for item in h.state.shared_results if item.node_name == "experiment_design")
    row = create_node_execution(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_name="experiment_design",
        seq=5,
        attempt=1,
    )
    finish_node_execution(h.runtime.conn, row.id, design.result)
    shared = tuple(
        BoundaryResult(node_name=item.node_name, execution_id=row.id, result=item.result)
        if item.node_name == "experiment_design"
        else item
        for item in h.state.shared_results
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "cursor": 11,
            "branches": [completed.model_dump(mode="json")],
            "shared_results": [item.model_dump(mode="json") for item in shared],
        }
    )


@pytest.mark.asyncio
async def test_initial_approval_later_audit_and_saved_wave_never_replans(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    planner = CandidateSearchPlanner(h.runtime)
    assert await planner(h.state, 1) == ()
    initial = RunState.model_validate({**h.state.model_dump(mode="json"), "branches": []})
    assert await planner(initial, 1) == (h.state.branches[0].spec,)
    await joined(h)
    original = h.state.branches[0].spec
    new = proposal(original)
    h.llm.responses[SearchDecision] = SearchDecision(
        rationale="Validation comparison", candidates=(new,)
    )
    assert await planner(h.state, 2) == (new,)
    assert h.llm.calls[-1][0] is SearchDecision
    assert "retune_count" in str(h.llm.calls[-1][2])
    branch = CandidateProgress(public_id="c_02", spec=new, wave=2, position=6)
    ensure_candidate(
        h.runtime.conn,
        candidate_id=branch.database_id(h.state.run_id),
        run_id=h.state.run_id,
        spec=new,
        wave=2,
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "cursor": 6,
            "wave": 2,
            "branches": [
                h.state.branches[0].model_dump(mode="json"),
                branch.model_dump(mode="json"),
            ],
        }
    )
    calls = len(h.llm.calls)
    assert await planner(h.state, 2) == () and len(h.llm.calls) == calls
    h.llm.responses[DesignDecision] = DesignDecision(
        rationale="Confirm saved cumulative shortlist",
        candidate_set=CandidateSet(candidates=(original, new)),
    )
    execution = create_node_execution(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_name="experiment_design",
        seq=5,
        attempt=2,
    )
    output = await DesignInferenceExecutor(h.runtime)(
        NodeInvocation(h.state, TABULAR_SPINE[4], execution, None)
    )
    assert output.status == "completed"
    assert h.state.branches[0].spec == original and h.state.branches[1].spec == new
    h.llm.responses[DesignDecision] = DesignDecision(
        rationale="Wrong replacement drops checkpointed proposal",
        candidate_set=CandidateSet(candidates=(original,)),
    )
    rejected = await DesignInferenceExecutor(h.runtime)(
        NodeInvocation(h.state, TABULAR_SPINE[4], execution, None)
    )
    assert rejected.status == "failed"


@pytest.mark.asyncio
async def test_empty_limits_duplicate_and_missing_persisted_design(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    with pytest.raises(EngineError, match="joined"):
        await CandidateSearchPlanner(h.runtime)(h.state, 2)
    await joined(h)
    planner = CandidateSearchPlanner(h.runtime)
    h.llm.responses[SearchDecision] = SearchDecision(rationale="No validation improvement")
    assert await planner(h.state, 2) == ()
    h.llm.responses[SearchDecision] = SearchDecision(
        rationale="Duplicate rejected by cumulative model",
        candidates=(h.state.branches[0].spec,),
    )
    with pytest.raises(_RefusalError, match="cumulative"):
        await planner(h.state, 2)
    limited = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            features=h.runtime.contract.features,
            constraints=Constraints(max_search_waves=1),
        ),
    )
    calls = len(h.llm.calls)
    assert await CandidateSearchPlanner(limited)(h.state, 2) == () and len(h.llm.calls) == calls
    boundaries = tuple(
        BoundaryResult(
            node_name=item.node_name, execution_id="absent-design-row", result=item.result
        )
        if item.node_name == "experiment_design"
        else item
        for item in h.state.shared_results
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "shared_results": [item.model_dump(mode="json") for item in boundaries],
        }
    )
    with pytest.raises(EngineError, match="persisted"):
        await planner(h.state, 2)


@pytest.mark.asyncio
async def test_search_cancellation_propagates(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    await joined(h)
    h.llm.cancel = True
    with pytest.raises(asyncio.CancelledError):
        await CandidateSearchPlanner(h.runtime)(h.state, 2)


@pytest.mark.asyncio
async def test_search_wave_order_budget_and_no_completed_branch(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    await joined(h)
    runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            features=h.runtime.contract.features,
            constraints=Constraints(max_search_waves=3),
        ),
    )
    planner = CandidateSearchPlanner(runtime)
    calls = len(h.llm.calls)
    with pytest.raises(EngineError, match="search wave must follow the completed checkpoint wave"):
        await planner(h.state, 3)
    exhausted = RunState.model_validate(
        {**h.state.model_dump(mode="json"), "budget": {"total_fit_ms": 10, "limit_ms": 10}}
    )
    assert await planner(exhausted, 2) == ()
    b = h.state.branches[0]
    assert [item.node_name for item in b.results[:4]] == [
        "preprocessing",
        "feature_engineering",
        "model_training",
        "model_evaluation",
    ]
    halted = CandidateProgress(
        public_id=b.public_id,
        spec=b.spec,
        results=b.results[:4],
        position=10,
        status="failed",
        pending_result=BoundaryResult(
            node_name="model_interpretation",
            execution_id="failed-interpretation",
            result=NodeResult(
                status="failed",
                error=Failure(error_class="data", message="interpretation refused"),
            ),
        ),
    )
    failed = RunState.model_validate(
        {**h.state.model_dump(mode="json"), "branches": [halted.model_dump(mode="json")]}
    )
    assert await planner(failed, 2) == ()
    assert len(h.llm.calls) == calls


@pytest.mark.asyncio
async def test_search_check_feeds_repair_and_unrepairable_proposal_ends_search(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    await joined(h)
    planner = CandidateSearchPlanner(h.runtime)
    original = h.state.branches[0].spec
    new = proposal(original)
    h.llm.responses[SearchDecision] = SearchDecision(
        rationale="Validation comparison", candidates=(new,)
    )
    assert await planner(h.state, 2) == (new,)
    check = h.llm.checks[SearchDecision]
    assert check(SearchDecision(rationale="Nothing new")) is None
    assert check(SearchDecision(rationale="ok", candidates=(new,))) is None
    with pytest.raises(DecisionRejectedError, match="cumulative"):
        check(SearchDecision(rationale="dup", candidates=(original,)))

    async def unrepairable(*args: object, **kwargs: object) -> SearchDecision:
        raise LlmOutputError(
            Failure(
                error_class="tool_param",
                message="LLM output remained invalid after one repair",
                field_errors={"output": "still invalid"},
            )
        )

    monkeypatch.setattr(h.llm, "complete", unrepairable)
    assert await planner(h.state, 2) == ()
    assert "search ends" in caplog.text
