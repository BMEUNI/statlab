"""Offline candidate preparation regressions; deliberately unexecuted."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue
from statlab.domains.base import NodeResult
from statlab.domains.candidate_pipeline import CandidatePipelineExecutor, pipeline_from_state
from statlab.domains.decisions import (
    DesignDecision,
    EvaluationActions,
    FeatureDecision,
    InterpretationDecision,
    PreprocessingDecision,
    TrainingDecision,
    ValidationConclusion,
)
from statlab.domains.runtime import Approval
from statlab.domains.tabular import TABULAR_SPINE, TABULAR_TRAVERSAL
from statlab.errors import EngineError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.llm.skills import NodeSkill, SkillPolicy
from statlab.mcp_client import CallContext, Repair
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import (
    create_node_execution,
    ensure_candidate,
    finish_node_execution,
    next_node_attempt,
)
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, ArtifactRef
from statlab_contracts.candidate import CandidateSpec, EstimatorSpec, FeatureSpec
from statlab_contracts.contract import DataContract, FeatureRules
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.vocabulary import TargetType

from tests.test_nodes_design_inference import DesignTools, design, prepared
from tests.test_nodes_foundation import TEST, VALID, Harness


class CandidateTools(DesignTools):
    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.counter = 100
        self.warning: str | None = None
        self.metric: JsonValue = 0.8
        self.drops: tuple[str, ...] = ()
        self.best_params: dict[str, JsonValue] = {"fit_intercept": False}
        self.outputs: list[tuple[str, ToolSuccess]] = []

    def artifact(self, kind: ArtifactKind) -> ArtifactMeta:
        self.counter += 1
        return ArtifactMeta(ref=ArtifactRef(f"a_{self.counter:016x}"), kind=kind)

    async def call(
        self,
        ctx: CallContext,
        tool: str,
        params: dict[str, JsonValue],
        *,
        trusted: Mapping[str, JsonValue] | None = None,
        repair: Repair | None = None,
    ) -> ToolSuccess:
        assert tool in ctx.allowed and trusted is None and repair is None
        assert str(TEST) not in str(params)
        self.calls.append((ctx, tool, params.copy()))
        if tool == self.cancel_tool:
            raise asyncio.CancelledError
        if tool == self.failure_tool:
            raise ToolCallError(tool, Failure(error_class="data", message="candidate tool refused"))
        artifacts: tuple[ArtifactMeta, ...] = ()
        summary: dict[str, JsonValue] = {}
        if tool == "list_estimators":
            summary = self.registry
        elif tool.startswith("fit_"):
            artifacts = (self.artifact("transformer"),)
            if tool == "fit_target_encoder":
                artifacts += (self.artifact("dataframe"),)
            if tool == "fit_imputer":
                summary = {"dropped": [], "fills": {"x": 1}, "strategies": {"x": "median"}}
        elif (
            tool.startswith("apply_")
            or tool.startswith("add_")
            or tool.startswith("drop_")
            or tool.startswith("select_by_")
        ):
            artifacts = (self.artifact("dataframe"),)
            summary = {"dropped_columns": list(self.drops)}
        elif tool == "tune_estimator":
            summary = {"best_params": self.best_params, "points_total": 2, "points_evaluated": 2}
        elif tool == "train_estimator":
            artifacts = (self.artifact("model"),)
        elif tool == "get_model_params":
            summary = {"params": {"fit_intercept": False, "n_jobs": 1}}
        elif tool in ("predict", "predict_proba"):
            artifacts = (self.artifact("predictions"),)
        elif tool.startswith("score_"):
            artifacts = (self.artifact("table"),)
            summary = {
                "metrics": {
                    "rmse": {"value": self.metric, "ci_low": 0.6, "ci_high": 0.9},
                    "roc_auc": {"value": self.metric, "ci_low": 0.6, "ci_high": 0.9},
                    "mape": {"value": self.metric, "ci_low": 0.6, "ci_high": 0.9},
                }
            }
        elif tool == "plot_curve":
            artifacts = (self.artifact("plot"),)
        elif tool == "compute_confusion_matrix":
            artifacts = (self.artifact("table"),)
        elif tool.startswith("compute_") or tool == "partial_dependence":
            artifacts = (self.artifact("curve"),)
        elif tool == "select_threshold":
            summary = {"threshold": 0.4}
        else:
            artifacts = (self.artifact("table"),)
        output = ToolSuccess(
            summary=summary, artifacts=artifacts, warnings=(self.warning,) if self.warning else ()
        )
        self.outputs.append((tool, output))
        return output


async def candidate_harness(
    conn: sqlite3.Connection,
    project: ProjectRow,
    root: Path,
    *,
    target_type: TargetType = "continuous",
    feature: FeatureSpec | None = None,
    approval: Approval | None = None,
) -> tuple[Harness, CandidateTools]:
    h, _ = await prepared(conn, project, root, target_type=target_type, goals=False)
    assert (await design(h)).status == "completed"
    approved = h.llm.responses[DesignDecision]
    assert isinstance(approved, DesignDecision)
    candidate = approved.candidate_set.candidates[0]
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            features=FeatureRules(include=("x",), id_columns=("id",)),
        ),
    )
    if feature is not None:
        candidate = CandidateSpec(
            label=candidate.label,
            rationale=candidate.rationale,
            is_baseline=True,
            preprocessing=candidate.preprocessing,
            estimator=candidate.estimator,
            features=feature,
        )
    branch = CandidateProgress(public_id="c_01", spec=candidate, position=6, status="running")
    ensure_candidate(
        conn, candidate_id=branch.database_id(h.state.run_id), run_id=h.state.run_id, spec=candidate
    )
    h.state = RunState(
        run_id=h.state.run_id,
        spine_id=h.state.spine_id,
        spine=h.state.spine,
        mode="autonomous",
        cursor=6,
        traversal=TABULAR_TRAVERSAL,
        branches=(branch,),
        shared_results=(
            *h.state.shared_results,
            BoundaryResult(
                node_name="inferential_analysis",
                execution_id="skipped-inference",
                result=NodeResult(status="skipped", skip_reason="No fixed goals"),
            ),
        ),
    )
    tools = CandidateTools(conn)
    names = frozenset(
        {
            "list_estimators",
            "drop_columns",
            "drop_constant_columns",
            "drop_high_missing_columns",
            "fit_imputer",
            "apply_imputer",
            "fit_outlier_clipper",
            "apply_outlier_clipper",
            "add_datetime_parts",
            "add_interaction_terms",
            "fit_binner",
            "apply_binner",
            "fit_onehot_encoder",
            "apply_onehot_encoder",
            "fit_ordinal_encoder",
            "apply_ordinal_encoder",
            "fit_target_encoder",
            "apply_target_encoder",
            "fit_scaler",
            "apply_scaler",
            "select_by_variance",
            "select_by_mutual_info",
            "tune_estimator",
            "train_estimator",
            "get_model_params",
            "predict",
            "predict_proba",
            "score_classification",
            "score_regression",
            "compute_confusion_matrix",
            "compute_roc_curve",
            "compute_pr_curve",
            "compute_calibration_curve",
            "select_threshold",
            "plot_curve",
            "cross_validate_estimator",
            "compute_learning_curve",
            "permutation_importance",
            "coefficient_importance",
            "partial_dependence",
            "analyze_errors",
        }
    )
    skill = SkillPolicy(
        "immutable",
        tuple(
            NodeSkill(
                spec.name, "section", "purpose", "heuristics", "tools", "outputs", "pitfalls", names
            )
            for spec in TABULAR_SPINE
        ),
        b"immutable",
    )
    h.runtime = replace(h.runtime, tools=tools, skill=skill, approval=approval)
    h.llm.responses.update(
        {
            PreprocessingDecision: PreprocessingDecision(
                rationale="Approved train-only preparation"
            ),
            FeatureDecision: FeatureDecision(
                rationale="Replay the fixed approved feature specification"
            ),
            TrainingDecision: TrainingDecision(rationale="Direct fit of approved estimator"),
            EvaluationActions: EvaluationActions(rationale="Validation-only scoring actions"),
            ValidationConclusion: ValidationConclusion(
                rationale="Observed validation score supports stopping"
            ),
            InterpretationDecision: InterpretationDecision(
                rationale="Predictive validation diagnostics"
            ),
        }
    )
    return h, tools


def candidate_invocation(h: Harness, index: int) -> NodeInvocation:
    branch = h.state.branches[0]
    spec = TABULAR_SPINE[index]
    execution = create_node_execution(
        h.runtime.conn,
        run_id=h.state.run_id,
        candidate_id=branch.database_id(h.state.run_id),
        node_name=spec.name,
        seq=index + 1,
        attempt=next_node_attempt(
            h.runtime.conn,
            run_id=h.state.run_id,
            node_name=spec.name,
            candidate_id=branch.database_id(h.state.run_id),
        ),
    )
    return NodeInvocation(h.state, spec, execution, branch)


def save_result(h: Harness, invocation: NodeInvocation, output: NodeResult) -> None:
    assert output.status in ("completed", "skipped")
    branch = h.state.branches[0]
    finish_node_execution(h.runtime.conn, invocation.execution.id, output)
    updated = CandidateProgress(
        public_id=branch.public_id,
        spec=branch.spec,
        wave=branch.wave,
        position=branch.position + 1,
        status="running",
        retune_count=branch.retune_count,
        results=(
            *branch.results,
            BoundaryResult(
                node_name=invocation.spec.name,
                execution_id=invocation.execution.id,
                result=output,
            ),
        ),
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "branches": [
                updated.model_dump(mode="json")
                if item.public_id == branch.public_id
                else item.model_dump(mode="json")
                for item in h.state.branches
            ],
        }
    )


async def prepare_candidate(h: Harness) -> None:
    for index in (6, 7):
        invocation = candidate_invocation(h, index)
        output = await CandidatePipelineExecutor(h.runtime)(invocation)
        assert output.status == "completed"
        save_result(h, invocation, output)


@pytest.mark.asyncio
async def test_replay_fits_train_applies_both_and_locks_identity(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    pipeline = pipeline_from_state(h.state, h.state.branches[0], require="feature_engineering")
    assert pipeline.methodology == h.state.branches[0].spec
    assert [step.tool for step in pipeline.replay] == [
        "drop_columns",
        "apply_imputer",
        "apply_onehot_encoder",
        "apply_scaler",
    ]
    assert "test" not in pipeline.model_dump_json()
    impute = next(params for _, name, params in tools.calls if name == "fit_imputer")
    assert impute["train"] != str(VALID) and impute["train"] != str(TEST)
    assert all(params.get("train") != str(VALID) for _, _, params in tools.calls)
    invocation = candidate_invocation(h, 8)
    with pytest.raises(EngineError, match="identity"):
        h.runtime.context(
            replace(
                invocation, execution=invocation.execution.model_copy(update={"candidate_id": None})
            )
        )
    with pytest.raises(EngineError, match="identity"):
        h.runtime.context(
            replace(
                invocation,
                candidate=CandidateProgress(public_id="c_02", spec=h.state.branches[0].spec),
            )
        )


@pytest.mark.asyncio
async def test_target_encoding_keeps_oof_train_and_only_applies_to_validation(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db, project, tmp_path, feature=FeatureSpec(encoding="target", scaling="none")
    )
    tools.roles = {"x": "categorical", "y": "numeric"}
    # Intake role evidence is immutable; contract supplies the authoritative corrected role.
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            features=FeatureRules(roles={"x": "categorical"}),
        ),
    )
    await prepare_candidate(h)
    calls = [(name, params) for _, name, params in tools.calls]
    entries = h.state.branches[0].results[-1].result.summary["tools"]
    assert isinstance(entries, list)
    target_fit = next(
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("tool") == "fit_target_encoder"
    )
    result = target_fit["result"]
    assert isinstance(result, dict) and isinstance(result["artifacts"], list)
    oof = result["artifacts"][1]
    assert isinstance(oof, dict)
    pipeline = pipeline_from_state(h.state, h.state.branches[0])
    assert str(pipeline.train) == oof["ref"]
    applied = [params for name, params in calls if name == "apply_target_encoder"]
    assert len(applied) == 1 and applied[0]["frame"] != str(pipeline.train)


@pytest.mark.asyncio
async def test_train_decided_selection_replays_exact_drop_and_exception_waits(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    from statlab_contracts.candidate import FeatureSelection

    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        feature=FeatureSpec(selection=FeatureSelection(method="variance", threshold=0.0)),
    )
    invocation = candidate_invocation(h, 6)
    save_result(h, invocation, await CandidatePipelineExecutor(h.runtime)(invocation))
    tools.drops = ("x",)
    invocation = candidate_invocation(h, 7)
    result = await CandidatePipelineExecutor(h.runtime)(invocation)
    assert result.status == "completed"
    selector = next(params for _, name, params in tools.calls if name == "select_by_variance")
    replay = [
        params
        for _, name, params in tools.calls
        if name == "drop_columns" and params["columns"] == ["x"]
    ]
    assert len(replay) == 1 and replay[0]["frame"] != selector["train"]
    h.state = h.state.model_copy(update={"mode": "guided"})
    tools.warning = "Unseen validation category"
    awaiting = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 7))
    assert awaiting.status == "awaiting_user" and awaiting.summary["pipeline"]


@pytest.mark.asyncio
async def test_pipeline_cancellation_failure_and_missing_upstream(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    with pytest.raises(EngineError, match="preparation"):
        await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 7))
    tools.failure_tool = "fit_imputer"
    failed = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert failed.status == "failed" and failed.error is not None and not failed.artifacts
    tools.failure_tool, tools.cancel_tool = None, "fit_imputer"
    with pytest.raises(asyncio.CancelledError):
        await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))


@pytest.mark.asyncio
async def test_material_exception_gate_precedes_actions(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    h.state = h.state.model_copy(update={"mode": "guided"})
    h.llm.responses[PreprocessingDecision] = PreprocessingDecision(
        rationale="Drop request requires review", material_exceptions=("Sparse feature exclusion",)
    )
    output = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert output.status == "awaiting_user" and not tools.calls

    async def approve(
        inv: NodeInvocation, decision: BaseModel, evidence: tuple[JsonValue, ...]
    ) -> BaseModel:
        assert inv.candidate is not None
        return decision

    h.runtime = replace(h.runtime, approval=approve)
    assert (
        await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    ).status == "completed"


@pytest.mark.asyncio
async def test_branch_manifests_are_isolated_and_optional_skip_passes_predecessor(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    first = h.state.branches[0]
    pipeline = pipeline_from_state(h.state, first)
    second = CandidateProgress(
        public_id="c_02",
        position=6,
        status="running",
        spec=CandidateSpec(
            label="Ridge branch",
            rationale="Validation regularization comparison",
            estimator=EstimatorSpec(name="ridge"),
            varies_from_baseline=("estimator", "hyperparameters"),
        ),
    )
    ensure_candidate(
        h.runtime.conn,
        candidate_id=second.database_id(h.state.run_id),
        run_id=h.state.run_id,
        spec=second.spec,
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "branches": [second.model_dump(mode="json"), first.model_dump(mode="json")],
        }
    )
    offset = len(tools.calls)
    invocation = candidate_invocation(h, 6)
    save_result(h, invocation, await CandidatePipelineExecutor(h.runtime)(invocation))
    predecessor = pipeline_from_state(h.state, h.state.branches[0])
    assert predecessor.train != pipeline.train and predecessor.valid != pipeline.valid
    assert all(str(pipeline.train) not in str(params) for _, _, params in tools.calls[offset:])
    invocation = candidate_invocation(h, 7)
    save_result(h, invocation, NodeResult(status="skipped", skip_reason="Optional no-op stage"))
    assert (
        pipeline_from_state(h.state, h.state.branches[0], require="feature_engineering")
        == predecessor
    )
    assert pipeline_from_state(h.state, h.state.branches[1]) == pipeline
