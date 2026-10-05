"""Offline foundation regressions; execution currently withheld by user instruction."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, JsonValue, ValidationError
from statlab.domains.base import NodeResult
from statlab.domains.decisions import (
    DesignDecision,
    EvaluationDecision,
    FeatureDecision,
    InferenceDecision,
    IntakeDecision,
    InterpretationDecision,
    PreprocessingDecision,
    ProblemDecision,
    ProblemSpec,
    ProfileDecision,
    ProfileFindings,
    ProfilePlot,
    SplitDecision,
    TrainingDecision,
    UnavailableDecision,
)
from statlab.domains.foundation import (
    FoundationExecutor,
    problem_from_state,
    problem_spec,
    resolve_metric,
    shared_result,
)
from statlab.domains.routing import NODE_ROLES
from statlab.domains.runtime import Approval, DomainRuntime
from statlab.domains.tabular import TABULAR_DOMAIN, TABULAR_SPINE
from statlab.errors import EngineError, ToolCallError
from statlab.graph.candidates import ToolBoundary
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, RunState
from statlab.llm.client import LlmClient
from statlab.llm.routing import ModelRouter, Role
from statlab.llm.skills import NodeSkill, SkillPolicy
from statlab.mcp_client import CallContext, Repair
from statlab.persistence.models import NodeExecutionRow, ProjectRow, RunMode
from statlab.persistence.repository import get_run, set_test_fingerprint
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef
from statlab_contracts.contract import (
    DataContract,
    DatasetOptions,
    FeatureRules,
    InferenceGoal,
    MissingPolicy,
    Objective,
    PreprocessingRules,
    SplitRules,
    TargetSpec,
)
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.vocabulary import Metric, SplitStrategy

from tests.test_graph_engine import World, new_world

RAW = ArtifactRef("a_0000000000000001")
FRAME = ArtifactRef("a_0000000000000002")
TRAIN = ArtifactRef("a_0000000000000003")
VALID = ArtifactRef("a_0000000000000004")
TEST = ArtifactRef("a_0000000000000005")
PLOT = ArtifactRef("a_0000000000000006")
FINGERPRINT = "f" * 64


class OfflineTools(ToolBoundary):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.calls: list[tuple[CallContext, str, dict[str, JsonValue]]] = []
        self.valid_contract = True
        self.verify_passed = True
        self.minority = 0.5
        self.fingerprint = FINGERPRINT
        self.failure_tool: str | None = None
        self.cancel_tool: str | None = None
        self.target_kind = "class"
        self.roles: dict[str, JsonValue] = {"x": "numeric", "y": "boolean"}
        self.rows_dropped = 2
        self.x_dtype = "float64"

    async def call(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        repair: Repair | None = None,
    ) -> ToolSuccess:
        assert tool in ctx.allowed
        assert trusted is None and repair is None
        self.calls.append((ctx, tool, params.copy()))
        if tool == self.cancel_tool:
            raise asyncio.CancelledError
        if tool == self.failure_tool:
            raise ToolCallError(tool, Failure(error_class="data", message="split too small"))
        if tool == "load_csv":
            return ToolSuccess(
                summary={"rows": 240, "columns": ["x", "id", "excluded", "y"]},
                artifacts=(ArtifactMeta(ref=RAW, kind="dataframe", label="raw", rows=240),),
                warnings=("fixture intake warning",),
            )
        if tool == "infer_column_roles":
            return ToolSuccess(summary={"roles": self.roles})
        if tool == "validate_contract":
            return ToolSuccess(summary={"valid": self.valid_contract, "mismatches": []})
        if tool == "detect_id_columns":
            return ToolSuccess(summary={"candidates": [{"column": "id"}]})
        if tool == "detect_leaky_columns":
            return ToolSuccess(summary={"suspects": [{"column": "x"}]})
        if tool == "profile_schema":
            return ToolSuccess(
                summary={
                    "columns": {
                        "x": {"dtype": self.x_dtype},
                        "id": {"dtype": "str"},
                        "excluded": {"dtype": "str"},
                        "y": {"dtype": "bool"},
                    }
                }
            )
        if tool == "summarize_target":
            return ToolSuccess(
                summary={
                    "kind": self.target_kind,
                    "classes": {
                        "0": {"count": 120, "share": self.minority},
                        "1": {"count": 120, "share": 1 - self.minority},
                    },
                    "minority_share": self.minority,
                }
            )
        if tool in ("cast_dtypes", "drop_duplicate_rows", "drop_high_missing_rows", "drop_columns"):
            return ToolSuccess(
                summary={"rows_before": 240, "rows_dropped": self.rows_dropped},
                artifacts=(ArtifactMeta(ref=FRAME, kind="dataframe"),),
            )
        if tool in ("split_random", "split_stratified"):
            # Accepted B6 dispatch records/locks the produced test artifact before verification.
            set_test_fingerprint(self.conn, ctx.paths.run_id, self.fingerprint)
            return ToolSuccess(
                summary={
                    "rows": {"train": 140, "valid": 48, "test": 48},
                    "rows_dropped_null_target": 2,
                    "test_fingerprint": self.fingerprint,
                },
                artifacts=(
                    ArtifactMeta(ref=TRAIN, kind="dataframe", split="train"),
                    ArtifactMeta(ref=VALID, kind="dataframe", split="valid"),
                    ArtifactMeta(ref=TEST, kind="dataframe", split="test"),
                ),
            )
        if tool == "verify_split":
            return ToolSuccess(summary={"passed": self.verify_passed, "checks": {}})
        if tool.startswith("plot_"):
            return ToolSuccess(artifacts=(ArtifactMeta(ref=PLOT, kind="plot"),))
        return ToolSuccess(summary={"fixture": tool})


class OfflineLlm(LlmClient):
    def __init__(self) -> None:
        self.responses: dict[type[BaseModel], BaseModel] = {}
        self.calls: list[tuple[type[BaseModel], bool, Sequence[dict[str, str]]]] = []
        self.cancel = False

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
    ) -> T:
        assert run_id and node_execution_id and model
        assert example is None
        self.calls.append((output, reasoning, messages))
        if self.cancel:
            raise asyncio.CancelledError
        defaults: dict[type[BaseModel], BaseModel] = {
            IntakeDecision: IntakeDecision(rationale="fixed evidence reviewed"),
            ProblemDecision: ProblemDecision(rationale="contract task and goals"),
            ProfileDecision: ProfileDecision(
                rationale="inspect actual numeric feature",
                numeric=("x",),
                plots=(ProfilePlot(kind="distribution", column="x"),),
            ),
            ProfileFindings: ProfileFindings(
                rationale="whole input described",
                findings=("Observed evidence only",),
                exploratory_hypotheses=("Post-EDA question",),
            ),
            SplitDecision: SplitDecision(rationale="independent rows"),
        }
        response = self.responses.get(output, defaults.get(output))
        assert response is not None
        return output.model_validate(response.model_dump(mode="json"))


def policy() -> SkillPolicy:
    allowed = frozenset(
        {
            "load_csv",
            "infer_column_roles",
            "validate_contract",
            "detect_id_columns",
            "detect_leaky_columns",
            "profile_schema",
            "report_missingness",
            "summarize_target",
            "summarize_numeric",
            "summarize_categorical",
            "compute_correlations",
            "detect_outliers",
            "plot_distribution",
            "plot_target_relation",
            "cast_dtypes",
            "drop_duplicate_rows",
            "drop_high_missing_rows",
            "drop_columns",
            "split_random",
            "split_stratified",
            "verify_split",
        }
    )
    return SkillPolicy(
        "fixed preamble",
        tuple(
            NodeSkill(
                spec.name,
                "fixed section",
                "purpose",
                "heuristics",
                "tools",
                "outputs",
                "pitfalls",
                allowed,
            )
            for spec in TABULAR_SPINE
        ),
        b"fixed policy",
    )


@dataclass
class Harness:
    world: World
    runtime: DomainRuntime
    tools: OfflineTools
    llm: OfflineLlm
    state: RunState

    def invocation(self, index: int | None = None) -> NodeInvocation:
        position = self.state.cursor if index is None else index
        spec = TABULAR_SPINE[position]
        execution = NodeExecutionRow(
            id=f"execution-{position}",
            run_id=self.state.run_id,
            candidate_id=None,
            node_name=spec.name,
            seq=position + 1,
            attempt=1,
            status="running",
            summary=None,
            error=None,
            concurrency_at_start=1,
            started_at="now",
            ended_at=None,
        )
        return NodeInvocation(self.state, spec, execution, None)

    async def step(self) -> NodeResult:
        invocation = self.invocation()
        result = await FoundationExecutor(self.runtime)(invocation)
        if result.status == "completed":
            self.state = RunState(
                run_id=self.state.run_id,
                spine_id=self.state.spine_id,
                spine=self.state.spine,
                mode=self.state.mode,
                cursor=self.state.cursor + 1,
                shared_results=(
                    *self.state.shared_results,
                    BoundaryResult(
                        node_name=invocation.spec.name,
                        execution_id=invocation.execution.id,
                        result=result,
                    ),
                ),
            )
        return result

    async def advance(self, count: int) -> None:
        for _ in range(count):
            assert (await self.step()).status == "completed"


def harness(
    conn: sqlite3.Connection,
    project: ProjectRow,
    root: Path,
    *,
    contract: DataContract | None = None,
    mode: RunMode = "autonomous",
    approval: Approval | None = None,
) -> Harness:
    selected = contract or DataContract(target=TargetSpec(column="y", type="binary"))
    world = new_world(conn, project, selected, root)
    tools, llm = OfflineTools(conn), OfflineLlm()
    runtime = DomainRuntime(
        conn=conn,
        paths=world.paths,
        dataset="d" * 64,
        contract=selected,
        tools=tools,
        llm=llm,
        skill=policy(),
        router=ModelRouter(NODE_ROLES, {}, models=dict.fromkeys(Role, "fixture")),
        approval=approval,
    )
    return Harness(
        world,
        runtime,
        tools,
        llm,
        RunState.initial(
            run_id=world.paths.run_id,
            spine_id=TABULAR_DOMAIN.name,
            nodes=TABULAR_SPINE,
            mode=mode,
        ),
    )


@pytest.fixture
def foundation(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> Harness:
    return harness(migrated_db, project, tmp_path)


def test_declared_spine_and_deferred_schema() -> None:
    assert TABULAR_DOMAIN.nodes == TABULAR_SPINE
    assert len(TABULAR_SPINE) == 13
    assert tuple(spec.name for spec in TABULAR_SPINE[:4]) == (
        "intake",
        "problem_definition",
        "data_profiling",
        "data_splitting",
    )
    assert all(spec.gate_policy == "always" for spec in TABULAR_SPINE if spec.kind == "SHARED")
    assert all(
        spec.gate_policy == "exception" for spec in TABULAR_SPINE if spec.kind == "PER_CANDIDATE"
    )
    assert TABULAR_SPINE[4].decision_schema is DesignDecision
    assert TABULAR_SPINE[5].decision_schema is InferenceDecision
    assert tuple(spec.decision_schema for spec in TABULAR_SPINE[6:11]) == (
        PreprocessingDecision,
        FeatureDecision,
        TrainingDecision,
        EvaluationDecision,
        InterpretationDecision,
    )
    assert all(spec.decision_schema is UnavailableDecision for spec in TABULAR_SPINE[11:])
    assert not TABULAR_SPINE[0].skippable and TABULAR_SPINE[2].skippable


@pytest.mark.parametrize(
    "schema,payload",
    [
        (
            ProfileDecision,
            {
                "rationale": "bounded",
                "plots": [{"kind": "distribution", "column": "x"} for _ in range(5)],
            },
        ),
        (SplitDecision, {"rationale": "bad", "seed": 7}),
        (ProblemDecision, {"rationale": "bad", "metric_override_rationale": "  "}),
        (
            ProblemSpec,
            {
                "rationale": "bad",
                "target": "y",
                "target_type": "binary",
                "task": "regression",
                "primary_metric": "rmse",
                "metric_rule": "explicit",
            },
        ),
    ],
)
def test_typed_decision_rejection(schema: type[BaseModel], payload: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.asyncio
async def test_first_four_flow_preserves_evidence_and_test_lock_boundary(
    foundation: Harness,
) -> None:
    await foundation.advance(4)
    result = shared_result(foundation.state, "data_splitting")
    assert result.summary["train"] == TRAIN and result.summary["test"] == TEST
    assert result.summary["report"] == {
        "rows": {"train": 140, "valid": 48, "test": 48},
        "rows_dropped_null_target": 2,
        "test_fingerprint": FINGERPRINT,
    }
    intake = shared_result(foundation.state, "intake")
    assert RAW in intake.artifacts
    assert len(intake.warnings) == 3
    profile = shared_result(foundation.state, "data_profiling")
    assert PLOT in profile.artifacts and profile.summary["whole_input_eda"] is True
    run = get_run(foundation.world.conn, foundation.state.run_id)
    assert run is not None and run.test_fingerprint == FINGERPRINT
    calls = foundation.tools.calls
    assert [tool for _, tool, _ in calls[:5]] == [
        "load_csv",
        "infer_column_roles",
        "validate_contract",
        "detect_id_columns",
        "detect_leaky_columns",
    ]
    assert not any(tool == "list_estimators" for _, tool, _ in calls)
    assert all(tool == "verify_split" for _, tool, params in calls if TEST in params.values())
    assert calls[-1][2] == {
        "train": str(TRAIN),
        "valid": str(VALID),
        "test": str(TEST),
        "target": "y",
    }
    assert all(
        ctx.allowed is foundation.runtime.skill.node(ctx.node).allowed for ctx, _, _ in calls
    )
    assert [
        reasoning for output, reasoning, _ in foundation.llm.calls if output is ProblemDecision
    ] == [True]


@pytest.mark.parametrize("minority,metric", [(0.5, "roc_auc"), (0.1, "roc_auc"), (0.09, "pr_auc")])
@pytest.mark.asyncio
async def test_automatic_metric_resolves_after_frozen_goals(
    foundation: Harness,
    minority: float,
    metric: Metric,
) -> None:
    goal = InferenceGoal(question="Predeclared relationship", predictors=("x",))
    foundation.llm.responses[ProblemDecision] = ProblemDecision(
        rationale="pre-EDA goals",
        inference_goals=(goal,),
    )
    await foundation.advance(2)
    problem = ProblemSpec.model_validate(
        shared_result(foundation.state, "problem_definition").summary["problem"]
    )
    assert problem.primary_metric is None and problem.metric_rule == "classification_auto"
    assert problem.inference_goals == (goal,)
    assert not any(tool == "summarize_target" for _, tool, _ in foundation.tools.calls)
    foundation.tools.minority = minority
    await foundation.advance(1)
    effective = problem_from_state(foundation.state)
    assert effective.primary_metric == metric and effective.inference_goals == (goal,)
    assert shared_result(foundation.state, "problem_definition").summary[
        "problem"
    ] == problem.model_dump(mode="json")


@pytest.mark.asyncio
async def test_regression_automatic_default(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    env = harness(
        migrated_db,
        project,
        tmp_path,
        contract=DataContract(
            target=TargetSpec(column="y", type="continuous"),
        ),
    )
    env.tools.target_kind = "continuous"
    await env.advance(4)
    assert problem_from_state(env.state).primary_metric == "rmse"
    assert any(tool == "split_random" for _, tool, _ in env.tools.calls)


@pytest.mark.asyncio
async def test_contract_metric_empty_goals_and_dataset_options_are_authoritative(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    contract = DataContract(
        target=TargetSpec(column="y", type="binary"),
        dataset=DatasetOptions(delimiter=";", encoding="latin-1", na_values=("MISSING",)),
        objective=Objective(
            primary_metric="f1", secondary_metrics=(), inference_goals=(), success_threshold=0.8
        ),
    )
    env = harness(migrated_db, project, tmp_path, contract=contract)
    env.llm.responses[ProblemDecision] = ProblemDecision(
        rationale="agent suggestions",
        primary_metric="accuracy",
        secondary_metrics=("recall",),
        success_threshold=0.1,
        inference_goals=(InferenceGoal(question="agent", predictors=("x",)),),
    )
    await env.advance(3)
    effective = problem_from_state(env.state)
    assert effective.primary_metric == "f1" and effective.inference_goals == ()
    assert effective.secondary_metrics == () and effective.success_threshold == 0.8
    assert env.tools.calls[0][2] == contract.dataset.model_dump(mode="json")


@pytest.mark.parametrize(
    "reason,expected", [(None, "failed"), ("Accuracy needed for deployed labels", "completed")]
)
@pytest.mark.asyncio
async def test_agent_metric_override_requires_recorded_rationale(
    foundation: Harness,
    reason: str | None,
    expected: str,
) -> None:
    foundation.llm.responses[ProblemDecision] = ProblemDecision(
        rationale="metric choice",
        primary_metric="accuracy",
        metric_override_rationale=reason,
    )
    await foundation.advance(1)
    result = await foundation.step()
    assert result.status == expected
    if expected == "completed":
        spec = problem_from_state(foundation.state)
        assert spec.primary_metric == "accuracy" and spec.metric_override_rationale == reason


@pytest.mark.parametrize(
    "decision",
    [
        ProblemDecision(rationale="wrong target", target_type="continuous"),
        ProblemDecision(rationale="wrong task", task="regression"),
        ProblemDecision(
            rationale="wrong metric", primary_metric="rmse", metric_override_rationale="reason"
        ),
        ProblemDecision(
            rationale="unknown predictor",
            inference_goals=(InferenceGoal(question="why", predictors=("missing",)),),
        ),
        ProblemDecision(
            rationale="target predictor",
            inference_goals=(InferenceGoal(question="why", predictors=("y",)),),
        ),
    ],
)
@pytest.mark.asyncio
async def test_problem_semantic_refusals(foundation: Harness, decision: ProblemDecision) -> None:
    foundation.llm.responses[ProblemDecision] = decision
    await foundation.advance(1)
    calls = len(foundation.tools.calls)
    result = await foundation.step()
    assert (
        result.status == "failed"
        and result.error is not None
        and result.error.error_class == "data"
    )
    assert len(foundation.tools.calls) == calls


@pytest.mark.asyncio
async def test_unresolved_target_does_not_invent_type(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    env = harness(
        migrated_db, project, tmp_path, contract=DataContract(target=TargetSpec(column="y"))
    )
    env.tools.roles["y"] = "numeric"
    env.llm.responses[ProblemDecision] = ProblemDecision(
        rationale="guess", target_type="continuous"
    )
    await env.advance(1)
    result = await env.step()
    assert (
        result.status == "failed"
        and result.error is not None
        and "ambiguous" in result.error.message
    )


@pytest.mark.asyncio
async def test_invalid_contract_cannot_be_approved(foundation: Harness) -> None:
    foundation.tools.valid_contract = False
    called = False

    async def approve(
        invocation: NodeInvocation,
        decision: BaseModel,
        evidence: tuple[JsonValue, ...],
    ) -> BaseModel:
        nonlocal called
        called = True
        return decision

    foundation.runtime = replace(foundation.runtime, approval=approve)
    result = await foundation.step()
    assert result.status == "failed" and not called
    assert result.artifacts == () and result.summary["raw"] == RAW


@pytest.mark.parametrize(
    "decision",
    [
        ProfileDecision(rationale="unknown", numeric=("missing",)),
        ProfileDecision(rationale="wrong dtype", numeric=("id",)),
        ProfileDecision(
            rationale="target mismatch", plots=(ProfilePlot(kind="target_relation", column="y"),)
        ),
    ],
)
@pytest.mark.asyncio
async def test_profile_selection_refuses_before_optional_tools(
    foundation: Harness,
    decision: ProfileDecision,
) -> None:
    await foundation.advance(2)
    foundation.llm.responses[ProfileDecision] = decision
    result = await foundation.step()
    assert result.status == "failed"
    assert [tool for _, tool, _ in foundation.tools.calls[-3:]] == [
        "profile_schema",
        "report_missingness",
        "summarize_target",
    ]


@pytest.mark.asyncio
async def test_contradictory_target_summary_escalates(foundation: Harness) -> None:
    await foundation.advance(2)
    foundation.tools.target_kind = "continuous"
    result = await foundation.step()
    assert (
        result.status == "failed"
        and result.error is not None
        and result.error.error_class == "data"
    )


@pytest.mark.parametrize("stage", [0, 1, 2, 3])
@pytest.mark.asyncio
async def test_guided_hold_retains_evidence_before_actions(foundation: Harness, stage: int) -> None:
    await foundation.advance(stage)
    foundation.state = RunState(
        run_id=foundation.state.run_id,
        spine_id=foundation.state.spine_id,
        spine=foundation.state.spine,
        mode="guided",
        cursor=foundation.state.cursor,
        shared_results=foundation.state.shared_results,
    )
    before = len(foundation.tools.calls)
    result = await foundation.step()
    assert result.status == "awaiting_user" and "decision" in result.summary
    if stage == 0:
        assert RAW in result.artifacts and len(foundation.tools.calls) == before + 5
    elif stage == 2:
        assert result.summary["frame"] == RAW and len(foundation.tools.calls) == before + 3
    else:
        assert len(foundation.tools.calls) == before


@pytest.mark.asyncio
async def test_guided_approval_revalidates_semantics_before_edits(foundation: Harness) -> None:
    await foundation.advance(3)
    foundation.state = RunState(
        run_id=foundation.state.run_id,
        spine_id=foundation.state.spine_id,
        spine=foundation.state.spine,
        mode="guided",
        cursor=3,
        shared_results=foundation.state.shared_results,
    )

    async def refuse_temporal(
        invocation: NodeInvocation,
        decision: BaseModel,
        evidence: tuple[JsonValue, ...],
    ) -> BaseModel:
        return SplitDecision(rationale="review establishes dependence", dependence="temporal")

    foundation.runtime = replace(foundation.runtime, approval=refuse_temporal)
    calls = len(foundation.tools.calls)
    result = await foundation.step()
    assert result.status == "failed" and len(foundation.tools.calls) == calls


@pytest.mark.asyncio
async def test_guided_valid_approval_and_wrong_schema(foundation: Harness) -> None:
    await foundation.advance(3)
    foundation.state = RunState(
        run_id=foundation.state.run_id,
        spine_id=foundation.state.spine_id,
        spine=foundation.state.spine,
        mode="guided",
        cursor=3,
        shared_results=foundation.state.shared_results,
    )

    async def wrong_schema(
        invocation: NodeInvocation,
        decision: BaseModel,
        evidence: tuple[JsonValue, ...],
    ) -> BaseModel:
        return ProblemDecision(rationale="wrong kind", target_type="binary")

    foundation.runtime = replace(foundation.runtime, approval=wrong_schema)
    assert (await foundation.step()).status == "failed"

    async def valid(
        invocation: NodeInvocation,
        decision: BaseModel,
        evidence: tuple[JsonValue, ...],
    ) -> BaseModel:
        return decision

    foundation.runtime = replace(foundation.runtime, approval=valid)
    assert (await foundation.step()).status == "completed"


@pytest.mark.parametrize("strategy", ["temporal", "grouped"])
@pytest.mark.asyncio
async def test_unsupported_contract_strategy_has_no_actions(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    strategy: SplitStrategy,
) -> None:
    rules = SplitRules(
        strategy=strategy,
        time_column="x" if strategy == "temporal" else None,
        group_column="id" if strategy == "grouped" else None,
    )
    env = harness(
        migrated_db,
        project,
        tmp_path,
        contract=DataContract(
            target=TargetSpec(column="y", type="binary"),
            split=rules,
        ),
    )
    await env.advance(3)
    env.llm.responses[SplitDecision] = SplitDecision(rationale="try random", strategy="random")
    calls = len(env.tools.calls)
    result = await env.step()
    assert result.status == "failed" and len(env.tools.calls) == calls


@pytest.mark.parametrize("dependence", ["temporal", "grouped"])
@pytest.mark.asyncio
async def test_decision_dependence_has_no_actions(
    foundation: Harness,
    dependence: Literal["temporal", "grouped"],
) -> None:
    await foundation.advance(3)
    foundation.llm.responses[SplitDecision] = SplitDecision(
        rationale="dependent rows", dependence=dependence
    )
    calls = len(foundation.tools.calls)
    assert (await foundation.step()).status == "failed"
    assert len(foundation.tools.calls) == calls


@pytest.mark.asyncio
async def test_profile_selection_uses_declared_structural_casts(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    contract = DataContract(
        target=TargetSpec(column="y", type="binary"),
        features=FeatureRules(roles={"x": "numeric"}),
    )
    env = harness(migrated_db, project, tmp_path, contract=contract)
    env.tools.x_dtype = "str"
    env.llm.responses[ProfileDecision] = ProfileDecision(rationale="numeric cast", numeric=("x",))
    await env.advance(3)
    numeric = next(params for _, tool, params in env.tools.calls if tool == "summarize_numeric")
    assert numeric == {"frame": str(FRAME), "columns": ["x"]}


@pytest.mark.asyncio
async def test_automatic_metric_cannot_conflict_with_frozen_secondary(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    contract = DataContract(
        target=TargetSpec(column="y", type="binary"),
        objective=Objective(secondary_metrics=("roc_auc",)),
    )
    env = harness(migrated_db, project, tmp_path, contract=contract)
    await env.advance(2)
    result = await env.step()
    assert result.status == "failed" and result.error is not None
    assert result.error.error_class == "data"
    assert env.state.cursor == 2


@pytest.mark.asyncio
async def test_contract_structural_options_and_traceability_survive(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    contract = DataContract(
        target=TargetSpec(column="y", type="binary"),
        features=FeatureRules(
            exclude=("excluded", "id"), id_columns=("id",), roles={"x": "numeric"}
        ),
        split=SplitRules(strategy="random", test_size=0.25, valid_size=0.3, seed=17),
        preprocessing=PreprocessingRules(missing=MissingPolicy(drop_rows_above=0.6)),
    )
    env = harness(migrated_db, project, tmp_path, contract=contract)
    env.llm.responses[SplitDecision] = SplitDecision(
        rationale="suggest stratification", strategy="stratified"
    )
    await env.advance(4)
    cast_calls = [params for _, tool, params in env.tools.calls if tool == "cast_dtypes"]
    assert cast_calls == [{"frame": str(RAW), "target": "y", "roles": {"x": "numeric"}}]
    row_drop = next(
        params for _, tool, params in env.tools.calls if tool == "drop_high_missing_rows"
    )
    assert row_drop["threshold"] == 0.6
    dropped = next(params for _, tool, params in env.tools.calls if tool == "drop_columns")
    assert dropped["columns"] == ["excluded"]
    dedupe = next(params for _, tool, params in env.tools.calls if tool == "drop_duplicate_rows")
    assert dedupe["subset"] == ["x", "excluded", "y"]
    split = next(params for _, tool, params in env.tools.calls if tool == "split_random")
    assert split == {
        "frame": str(FRAME),
        "target": "y",
        "seed": 17,
        "test_size": 0.25,
        "valid_size": 0.3,
    }


@pytest.mark.parametrize("failure", ["split", "verify"])
@pytest.mark.asyncio
async def test_split_refusal_and_verification_do_not_publish_success(
    foundation: Harness,
    failure: str,
) -> None:
    await foundation.advance(3)
    if failure == "split":
        foundation.tools.failure_tool = "split_stratified"
    else:
        foundation.tools.verify_passed = False
    result = await foundation.step()
    assert result.status == "failed" and result.artifacts == ()
    run = get_run(foundation.world.conn, foundation.state.run_id)
    assert run is not None
    assert run.test_fingerprint == (None if failure == "split" else FINGERPRINT)
    assert foundation.state.cursor == 3
    assert all(
        boundary.node_name != "data_splitting" for boundary in foundation.state.shared_results
    )


@pytest.mark.parametrize("fingerprint,expected", [(FINGERPRINT, "completed"), ("e" * 64, "failed")])
@pytest.mark.asyncio
async def test_fingerprint_idempotence_and_conflict(
    foundation: Harness,
    fingerprint: str,
    expected: str,
) -> None:
    await foundation.advance(3)
    set_test_fingerprint(foundation.world.conn, foundation.state.run_id, fingerprint)
    result = await foundation.step()
    assert result.status == expected
    run = get_run(foundation.world.conn, foundation.state.run_id)
    assert run is not None and run.test_fingerprint == fingerprint
    if expected == "failed":
        assert result.artifacts == ()


@pytest.mark.asyncio
async def test_missing_boundary_and_deferred_executor_are_package_errors(
    foundation: Harness,
) -> None:
    with pytest.raises(EngineError, match="missing successful shared boundary"):
        shared_result(foundation.state, "intake")
    with pytest.raises(EngineError):
        await FoundationExecutor(foundation.runtime)(foundation.invocation(1))
    with pytest.raises(EngineError, match="deferred"):
        await FoundationExecutor(foundation.runtime)(foundation.invocation(4))


@pytest.mark.asyncio
async def test_malformed_upstream_reference_is_package_error(foundation: Harness) -> None:
    malformed = NodeResult(status="completed", summary={"raw": "wrong"})
    foundation.state = RunState(
        run_id=foundation.state.run_id,
        spine_id=foundation.state.spine_id,
        spine=foundation.state.spine,
        mode="autonomous",
        cursor=2,
        shared_results=(
            BoundaryResult(node_name="intake", execution_id="a", result=malformed),
            BoundaryResult(
                node_name="problem_definition",
                execution_id="b",
                result=NodeResult(
                    status="completed",
                    summary={},
                ),
            ),
        ),
    )
    with pytest.raises(EngineError, match="malformed raw reference"):
        await foundation.step()


@pytest.mark.parametrize("boundary", ["tool", "llm"])
@pytest.mark.asyncio
async def test_cancellation_propagates(foundation: Harness, boundary: str) -> None:
    if boundary == "tool":
        foundation.tools.cancel_tool = "load_csv"
    else:
        foundation.llm.cancel = True
    with pytest.raises(asyncio.CancelledError):
        await foundation.step()


@pytest.mark.asyncio
async def test_problem_reader_reuses_only_pre_eda_goal_spec(foundation: Harness) -> None:
    await foundation.advance(3)
    before = problem_from_state(foundation.state)
    assert before.inference_goals == ()
    assert resolve_metric(before, {}) == before.primary_metric
    assert (
        problem_spec(
            foundation.runtime.contract,
            shared_result(foundation.state, "intake"),
            ProblemDecision(rationale="same boundary"),
        ).inference_goals
        == ()
    )
