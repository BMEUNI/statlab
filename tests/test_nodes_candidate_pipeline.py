"""Offline candidate preparation regressions; deliberately unexecuted."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue, ValidationError
from statlab.domains.base import NodeResult
from statlab.domains.candidate_pipeline import (
    _CLEAN_PREPROCESSING_RATIONALE,
    _CLEAN_PREPROCESSING_SUMMARY,
    _FEATURE_RATIONALE,
    _FEATURE_SUMMARY,
    CandidatePipelineExecutor,
    pipeline_from_state,
)
from statlab.domains.decisions import (
    DesignDecision,
    EvaluationActions,
    FeatureDecision,
    InterpretationDecision,
    PipelineState,
    PreprocessingDecision,
    TrainingDecision,
    ValidationConclusion,
)
from statlab.domains.runtime import Approval
from statlab.domains.tabular import TABULAR_SPINE, TABULAR_TRAVERSAL
from statlab.errors import EngineError, LlmCallError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.llm.client import CallRecovery
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
from statlab_contracts.candidate import (
    CandidateSpec,
    EstimatorSpec,
    FeatureSelection,
    FeatureSpec,
    PreprocessingSpec,
)
from statlab_contracts.contract import DataContract, FeatureRules, PreprocessingRules
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.vocabulary import TargetType

from tests.test_nodes_design_inference import DesignTools, design, prepared
from tests.test_nodes_foundation import TEST, VALID, Harness, OfflineLlm


class CandidateTools(DesignTools):
    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.counter = 100
        self.warning: str | None = None
        self.metric: JsonValue = 0.8
        self.drops: tuple[str, ...] = ()
        self.threshold: JsonValue = 0.4
        self.best_params: dict[str, JsonValue] = {"fit_intercept": False}
        self.skipped: dict[str, JsonValue] = {}
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
            if tool == "fit_onehot_encoder":
                summary = {"skipped_high_cardinality": dict(self.skipped)}
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
            summary = {"threshold": self.threshold}
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
    preprocessing: PreprocessingSpec | None = None,
    estimator: EstimatorSpec | None = None,
    approval: Approval | None = None,
) -> tuple[Harness, CandidateTools]:
    h, design_tools = await prepared(conn, project, root, target_type=target_type, goals=False)
    assert (await design(h)).status == "completed"
    h.tools.calls.extend(design_tools.calls)
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
    if feature is not None or preprocessing is not None or estimator is not None:
        candidate = CandidateSpec(
            label=candidate.label,
            rationale=candidate.rationale,
            is_baseline=True,
            preprocessing=candidate.preprocessing if preprocessing is None else preprocessing,
            estimator=candidate.estimator if estimator is None else estimator,
            features=candidate.features if feature is None else feature,
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
async def test_onehot_skipped_high_cardinality_columns_are_target_encoded(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    tools.skipped = {"zip": 400, "city": 60}
    await prepare_candidate(h)
    calls = [(name, params) for _, name, params in tools.calls]
    names = [name for name, _ in calls]
    assert names.index("fit_target_encoder") > names.index("apply_onehot_encoder")
    fit = next(params for name, params in calls if name == "fit_target_encoder")
    assert fit["columns"] == ["city", "zip"]
    assert fit["seed"] == h.runtime.contract.constraints.random_seed
    pipeline = pipeline_from_state(h.state, h.state.branches[0], require="feature_engineering")
    assert [step.tool for step in pipeline.replay] == [
        "drop_columns",
        "apply_imputer",
        "apply_onehot_encoder",
        "apply_target_encoder",
        "apply_scaler",
    ]


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


@pytest.mark.asyncio
async def test_pipeline_from_state_skips_unsuccessful_and_rejects_malformed_boundaries(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    branch = h.state.branches[0]
    expected = pipeline_from_state(h.state, branch)

    def with_result(result: NodeResult, execution_id: str) -> CandidateProgress:
        return CandidateProgress(
            public_id=branch.public_id,
            spec=branch.spec,
            position=branch.position,
            status=branch.status,
            results=(
                *branch.results,
                BoundaryResult(
                    node_name="feature_engineering", execution_id=execution_id, result=result
                ),
            ),
        )

    failed = NodeResult(status="failed", error=Failure(error_class="data", message="refused"))
    assert pipeline_from_state(h.state, with_result(failed, "failed-feature")) == expected
    malformed = NodeResult(status="completed", summary={"pipeline": {"train": "not-a-ref"}})
    with pytest.raises(EngineError, match="malformed candidate pipeline"):
        pipeline_from_state(h.state, with_result(malformed, "malformed-feature"))
    foreign = PipelineState(
        train=expected.train,
        valid=expected.valid,
        replay=expected.replay,
        feature_manifest=expected.feature_manifest,
        methodology=CandidateSpec(
            label="Ridge branch",
            rationale="Validation regularization comparison",
            estimator=EstimatorSpec(name="ridge"),
            varies_from_baseline=("estimator", "hyperparameters"),
        ),
    )
    mismatch = NodeResult(status="completed", summary={"pipeline": foreign.model_dump(mode="json")})
    with pytest.raises(EngineError, match="disagrees with approved candidate"):
        pipeline_from_state(h.state, with_result(mismatch, "foreign-feature"))


@pytest.mark.asyncio
async def test_executor_rejects_unbound_invocations(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    shared = TABULAR_SPINE[0]
    execution = create_node_execution(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_name=shared.name,
        seq=1,
        attempt=next_node_attempt(
            h.runtime.conn, run_id=h.state.run_id, node_name=shared.name, candidate_id=None
        ),
    )
    with pytest.raises(EngineError, match="pipeline requires a candidate"):
        await CandidatePipelineExecutor(h.runtime)(NodeInvocation(h.state, shared, execution, None))
    with pytest.raises(EngineError, match="pipeline executor cannot execute this node"):
        await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 8))


@pytest.mark.asyncio
async def test_preprocessing_contract_conflict_threshold_drops_and_outliers(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        preprocessing=PreprocessingSpec(outliers="clip_iqr"),
    )
    contract = h.runtime.contract
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=contract.target,
            objective=contract.objective,
            features=contract.features,
            preprocessing=PreprocessingRules(drop_constant=False),
        ),
    )
    conflict = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert conflict.status == "failed" and conflict.error is not None
    assert conflict.error.message == "constant drop conflicts with explicit contract"
    assert tools.calls == []
    h.llm.responses[PreprocessingDecision] = PreprocessingDecision(
        rationale="Contract keeps constants; drop sparse columns",
        drop_constant=False,
        high_missing_threshold=0.5,
    )
    tools.drops = ("x",)
    invocation = candidate_invocation(h, 6)
    output = await CandidatePipelineExecutor(h.runtime)(invocation)
    assert output.status == "completed"
    save_result(h, invocation, output)
    assert "drop_constant_columns" not in [name for _, name, _ in tools.calls]
    sparse = [params for _, name, params in tools.calls if name == "drop_high_missing_columns"]
    assert len(sparse) == 1
    assert set(sparse[0]) == {"threshold", "train", "target"}
    assert sparse[0]["threshold"] == 0.5 and sparse[0]["target"] == "y"
    pipeline = pipeline_from_state(h.state, h.state.branches[0])
    assert [s.tool for s in pipeline.replay] == [
        "drop_columns",
        "drop_columns",
        "apply_imputer",
        "apply_outlier_clipper",
    ]
    assert pipeline.replay[1].parameters == {"columns": ["x"]}
    dropped = [
        params
        for _, name, params in tools.calls
        if name == "drop_columns" and params["columns"] == ["x"]
    ]
    assert len(dropped) == 1
    clipper = [params for _, name, params in tools.calls if name == "fit_outlier_clipper"]
    assert len(clipper) == 1 and clipper[0]["method"] == "clip_iqr"
    transforms = pipeline.feature_manifest["transforms"]
    assert isinstance(transforms, list)
    assert [t.get("tool") for t in transforms if isinstance(t, dict)] == [
        "fit_imputer",
        "fit_outlier_clipper",
    ]
    tools.drops = ()
    invocation = candidate_invocation(h, 7)
    output = await CandidatePipelineExecutor(h.runtime)(invocation)
    assert output.status == "completed"
    save_result(h, invocation, output)


def _set_null_counts(h: Harness, counts: dict[str, int]) -> None:
    dump = h.state.model_dump(mode="json")
    for boundary in dump["shared_results"]:
        if boundary["node_name"] != "intake":
            continue
        for entry in boundary["result"]["summary"]["tools"]:
            if entry["tool"] == "load_csv":
                entry["result"]["summary"]["null_counts"] = counts
    h.state = RunState.model_validate(dump)


@pytest.mark.asyncio
async def test_decide_prompt_carries_digest_in_system_prefix(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    output = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert output.status == "completed"
    call = next(c for c in h.llm.calls if c[0] is PreprocessingDecision)
    system, user = call[2][0]["content"], call[2][1]["content"]
    assert system.endswith("\n\nsection")
    assert '"node":"intake"' in system
    assert '"checkpointed_candidates"' in system
    assert '"tool":"load_csv"' not in system
    summary = json.loads(user.split("\n\n")[0])
    assert set(summary) == {"node", "candidate", "sibling_validation"}
    assert summary["node"] == "preprocessing"
    assert summary["sibling_validation"] == []


@pytest.mark.asyncio
async def test_preprocessing_skips_llm_without_missing_values(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    _set_null_counts(h, {"x": 0, "id": 0, "excluded": 0, "y": 0})
    h.llm.responses[PreprocessingDecision] = PreprocessingDecision(
        rationale="Unused", high_missing_threshold=0.5
    )
    before = len(h.llm.calls)
    output = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert output.status == "completed"
    assert len(h.llm.calls) == before
    names = [name for _, name, _ in tools.calls]
    assert "drop_constant_columns" in names
    assert "drop_high_missing_columns" not in names
    assert output.summary["decision"] == PreprocessingDecision(
        rationale=_CLEAN_PREPROCESSING_RATIONALE,
        user_summary=_CLEAN_PREPROCESSING_SUMMARY,
        drop_constant=True,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_preprocessing_asks_llm_when_values_are_missing(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    _set_null_counts(h, {"x": 3, "id": 0, "excluded": 0, "y": 0})
    h.llm.responses[PreprocessingDecision] = PreprocessingDecision(
        rationale="Unused", high_missing_threshold=0.5
    )
    before = len(h.llm.calls)
    output = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert output.status == "completed"
    assert len(h.llm.calls) == before + 1
    assert h.llm.calls[-1][0] is PreprocessingDecision
    assert '{"null_counts":{"excluded":0,"id":0,"x":3,"y":0}}' in h.llm.calls[-1][2][1]["content"]
    sparse = [params for _, name, params in tools.calls if name == "drop_high_missing_columns"]
    assert len(sparse) == 1 and sparse[0]["threshold"] == 0.5


@pytest.mark.asyncio
async def test_feature_stage_is_deterministic_and_refuses_skip(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    invocation = candidate_invocation(h, 6)
    output = await CandidatePipelineExecutor(h.runtime)(invocation)
    assert output.status == "completed"
    save_result(h, invocation, output)
    h.state = h.state.model_copy(update={"mode": "guided"})
    h.llm.responses[FeatureDecision] = FeatureDecision(
        rationale="Exception needs review", material_exceptions=("Rare level collapse",)
    )
    before = len(h.llm.calls)
    done = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 7))
    assert done.status == "completed"
    assert len(h.llm.calls) == before
    assert done.summary["decision"] == FeatureDecision(
        rationale=_FEATURE_RATIONALE, user_summary=_FEATURE_SUMMARY
    ).model_dump(mode="json")
    with pytest.raises(ValidationError):
        FeatureDecision.model_validate(
            {"rationale": "Skip requested", "skip_reason": "No engineered features needed"}
        )


@pytest.mark.asyncio
async def test_frequency_encoding_has_no_supported_tool(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(
        migrated_db, project, tmp_path, feature=FeatureSpec(encoding="frequency")
    )
    invocation = candidate_invocation(h, 6)
    output = await CandidatePipelineExecutor(h.runtime)(invocation)
    assert output.status == "completed"
    save_result(h, invocation, output)
    failed = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 7))
    assert failed.status == "failed" and failed.error is not None
    assert failed.error.message == "frequency encoding has no supported tool"


@pytest.mark.asyncio
async def test_feature_stage_replays_datetime_interactions_bins_and_ordinal_levels(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        feature=FeatureSpec(
            encoding="ordinal",
            scaling="none",
            datetime_parts={"id": ("year", "month")},
            interactions=(("x", "excluded"),),
            binned={"x": 3},
        ),
    )
    contract = h.runtime.contract
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=contract.target,
            objective=contract.objective,
            features=FeatureRules(
                include=("x", "excluded"),
                id_columns=("id",),
                roles={"excluded": "ordinal"},
                ordinal_levels={"excluded": ("lo", "hi"), "absent": ("a", "b")},
            ),
        ),
    )
    await prepare_candidate(h)
    calls = [(name, params) for _, name, params in tools.calls]

    def minus_frame(params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {key: value for key, value in params.items() if key != "frame"}

    parts = [params for name, params in calls if name == "add_datetime_parts"]
    assert len(parts) == 2
    for params in parts:
        assert minus_frame(params) == {
            "target": "y",
            "column": "id",
            "parts": ["year", "month"],
        }
    pairs = [params for name, params in calls if name == "add_interaction_terms"]
    assert len(pairs) == 2
    for params in pairs:
        assert minus_frame(params) == {"target": "y", "pairs": [["x", "excluded"]]}
    binner = [params for name, params in calls if name == "fit_binner"]
    assert len(binner) == 1
    assert set(binner[0]) == {"columns", "n_bins", "train", "target"}
    assert binner[0]["columns"] == ["x"]
    assert binner[0]["n_bins"] == 3 and binner[0]["target"] == "y"
    ordinal = [params for name, params in calls if name == "fit_ordinal_encoder"]
    assert len(ordinal) == 1
    assert ordinal[0]["columns"] == ["excluded", "x__bin"]
    assert ordinal[0]["level_order"] == {"excluded": ["lo", "hi"]}
    assert "seed" not in ordinal[0]
    assert "fit_scaler" not in [name for name, _ in calls]
    pipeline = pipeline_from_state(h.state, h.state.branches[0])
    assert [s.tool for s in pipeline.replay] == [
        "drop_columns",
        "apply_imputer",
        "add_datetime_parts",
        "add_interaction_terms",
        "apply_binner",
        "apply_ordinal_encoder",
    ]


@pytest.mark.asyncio
async def test_ordinal_without_categoricals_and_mutual_info_keep_selected_train(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        feature=FeatureSpec(
            encoding="ordinal",
            scaling="none",
            selection=FeatureSelection(method="mutual_info", k=1),
        ),
    )
    await prepare_candidate(h)
    assert "fit_ordinal_encoder" not in [name for _, name, _ in tools.calls]
    mutual = [params for _, name, params in tools.calls if name == "select_by_mutual_info"]
    assert len(mutual) == 1
    assert mutual[0]["k"] == 1 and mutual[0]["seed"] == 42 and mutual[0]["target"] == "y"
    assert "threshold" not in mutual[0]
    selected = next(out for name, out in tools.outputs if name == "select_by_mutual_info")
    pipeline = pipeline_from_state(h.state, h.state.branches[0])
    assert pipeline.train == selected.artifacts[0].ref
    assert [s.tool for s in pipeline.replay] == ["drop_columns", "apply_imputer"]


@pytest.mark.asyncio
async def test_after_action_acknowledgment_cannot_change_completed_decision(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    h.state = h.state.model_copy(update={"mode": "guided"})
    tools.warning = "Imputer filled a sparse column"

    async def approve(
        inv: NodeInvocation, decision: BaseModel, evidence: tuple[JsonValue, ...]
    ) -> BaseModel:
        assert inv.after_action
        return PreprocessingDecision(rationale="Changed after the actions ran")

    h.runtime = replace(h.runtime, approval=approve)
    failed = await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 6))
    assert failed.status == "failed" and failed.error is not None
    assert failed.error.message == "completed actions cannot be changed by exception acknowledgment"
    assert failed.artifacts == ()


@pytest.mark.asyncio
async def test_feature_stage_rejects_malformed_checkpointed_transform_manifest(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    invocation = candidate_invocation(h, 6)
    output = await CandidatePipelineExecutor(h.runtime)(invocation)
    assert output.status == "completed"
    save_result(h, invocation, output)
    branch = h.state.branches[0]
    old = branch.results[-1]
    pipeline = pipeline_from_state(h.state, branch)
    bad = PipelineState(
        train=pipeline.train,
        valid=pipeline.valid,
        replay=pipeline.replay,
        methodology=pipeline.methodology,
        feature_manifest={"input_features": ["x"], "transforms": "malformed"},
    )
    rebuilt = CandidateProgress(
        public_id=branch.public_id,
        spec=branch.spec,
        position=branch.position,
        status=branch.status,
        results=(
            *branch.results[:-1],
            BoundaryResult(
                node_name="preprocessing",
                execution_id=old.execution_id,
                result=NodeResult(
                    status="completed",
                    summary={**old.result.summary, "pipeline": bad.model_dump(mode="json")},
                    artifacts=old.result.artifacts,
                ),
            ),
        ),
    )
    h.state = RunState.model_validate(
        {**h.state.model_dump(mode="json"), "branches": [rebuilt.model_dump(mode="json")]}
    )
    with pytest.raises(EngineError, match="malformed feature transform manifest"):
        await CandidatePipelineExecutor(h.runtime)(candidate_invocation(h, 7))


@pytest.mark.asyncio
async def test_runtime_rejects_shared_identity_for_candidate_node(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    execution = create_node_execution(
        h.runtime.conn,
        run_id=h.state.run_id,
        node_name="preprocessing",
        seq=7,
        attempt=next_node_attempt(
            h.runtime.conn, run_id=h.state.run_id, node_name="preprocessing", candidate_id=None
        ),
    )
    with pytest.raises(EngineError, match="shared invocation identity mismatch"):
        h.runtime.context(NodeInvocation(h.state, TABULAR_SPINE[6], execution, None))


@pytest.mark.asyncio
@pytest.mark.parametrize("sleeping", (False, True))
async def test_decision_recovery_retries_transient_llm_failure(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, sleeping: bool
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)

    class FlakyLlm(OfflineLlm):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

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
            async def call() -> T:
                self.attempts += 1
                if self.attempts == 1:
                    raise LlmCallError(Failure(error_class="transient", message="provider hiccup"))
                return output.model_validate(
                    PreprocessingDecision(rationale="Recovered").model_dump(mode="json")
                )

            assert recovery is not None
            return await recovery(call)

    llm = FlakyLlm()
    h.runtime = replace(h.runtime, llm=llm)
    slept: list[float] = []
    if sleeping:

        async def sleep(delay: float) -> None:
            slept.append(delay)

        tools.sleep = sleep
    decision = await h.runtime.decide(
        candidate_invocation(h, 6), PreprocessingDecision, evidence=(), question="q"
    )
    assert decision.rationale == "Recovered"
    assert llm.attempts == 2
    if sleeping:
        assert slept == [1.0]
