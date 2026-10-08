"""Offline model/validation/retune regressions; deliberately unexecuted."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError
from statlab.domains.base import NodeResult
from statlab.domains.candidate_models import (
    CV_CAVEAT,
    CandidateModelExecutor,
    candidate_retune_decider,
    evaluation_decision,
)
from statlab.domains.candidate_pipeline import candidate_result, pipeline_from_state
from statlab.domains.decisions import (
    EvaluationActions,
    InterpretationDecision,
    ProblemSpec,
    TrainingDecision,
    ValidationConclusion,
)
from statlab.domains.foundation import problem_from_state
from statlab.domains.tabular import TABULAR_SPINE
from statlab.errors import EngineError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, CandidateProgress, RunState
from statlab.llm.client import DecisionRejectedError
from statlab.persistence.models import ProjectRow
from statlab.persistence.repository import create_node_execution, next_node_attempt
from statlab_contracts.candidate import EstimatorSpec, FeatureSpec
from statlab_contracts.contract import DataContract, ModellingRules, Tuning
from statlab_contracts.vocabulary import TargetType

from tests.test_nodes_candidate_pipeline import (
    candidate_harness,
    candidate_invocation,
    prepare_candidate,
    save_result,
)
from tests.test_nodes_foundation import TEST, Harness


@pytest.mark.asyncio
@pytest.mark.parametrize("target_type", ["continuous", "binary", "multiclass"])
async def test_training_validation_scoring_and_task_diagnostics(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    target_type: TargetType,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path, target_type=target_type)
    await prepare_candidate(h)
    train = candidate_invocation(h, 8)
    fitted = await CandidateModelExecutor(h.runtime)(train)
    assert fitted.status == "completed"
    save_result(h, train, fitted)
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Task-compatible held-out diagnostics",
        curves=("confusion", "roc", "pr", "calibration")
        if target_type == "binary"
        else ("confusion",)
        if target_type == "multiclass"
        else (),
        threshold_objective="f1" if target_type == "binary" else None,
    )
    evaluation = candidate_invocation(h, 9)
    scored = await CandidateModelExecutor(h.runtime)(evaluation)
    assert scored.status == "completed" and scored.metrics
    save_result(h, evaluation, scored)
    pipeline = pipeline_from_state(h.state, h.state.branches[0])
    training = next(params for _, name, params in tools.calls if name == "train_estimator")
    assert training["train"] == str(pipeline.train)
    predictions = [
        params for _, name, params in tools.calls if name in ("predict", "predict_proba")
    ]
    assert all(params["frame"] == str(pipeline.valid) for params in predictions)
    names = [name for _, name, _ in tools.calls]
    assert ("predict_proba" in names) == (target_type != "continuous")
    assert ("score_regression" in names) == (target_type == "continuous")
    assert ("select_threshold" in names) == (target_type == "binary")
    assert scored.summary["threshold"] == (0.4 if target_type == "binary" else None)
    assert all(str(TEST) not in str(params) for _, _, params in tools.calls)
    assert "selection" in str(scored.summary["selection_bias"])
    plots = [params for _, name, params in tools.calls if name == "plot_curve"]
    assert len(plots) == (3 if target_type == "binary" else 0)
    assert names.count("compute_roc_curve") == (1 if target_type == "binary" else 0)
    assert all("confusion" not in str(params) for params in plots)
    curves = scored.summary["curves"]
    assert isinstance(curves, dict)
    for kind in ("roc", "pr", "calibration"):
        if kind in curves:
            entry = curves[kind]
            assert isinstance(entry, dict)
            assert entry["curve"] in {str(ref) for ref in scored.artifacts}
            assert entry["plot"] in {str(ref) for ref in scored.artifacts}


@pytest.mark.asyncio
async def test_retune_uses_original_train_latest_model_and_durable_decision(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    original = pipeline_from_state(h.state, h.state.branches[0]).train
    for index in (8, 9):
        if index == 9:
            h.llm.responses[ValidationConclusion] = ValidationConclusion(
                rationale="Validation supports parameter retune",
                retune=True,
                retune_reason="Try intercept sensitivity",
            )
        invocation = candidate_invocation(h, index)
        save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    branch = h.state.branches[0]
    first_model = candidate_result(branch, "model_training").summary["model"]
    assert await candidate_retune_decider(h.state, branch)
    rewound = CandidateProgress(
        public_id=branch.public_id,
        spec=branch.spec,
        results=branch.results,
        wave=branch.wave,
        position=8,
        retune_count=1,
        status="running",
    )
    h.state = RunState.model_validate(
        {**h.state.model_dump(mode="json"), "branches": [rewound.model_dump(mode="json")]}
    )
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Retune intercept using validation; fit only original prepared train",
        params={"fit_intercept": False},
    )
    h.llm.responses[ValidationConclusion] = ValidationConclusion(
        rationale="Final held-out validation score supports stopping"
    )
    for index in (8, 9):
        invocation = candidate_invocation(h, index)
        save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    branch = h.state.branches[0]
    latest = candidate_result(branch, "model_training").summary["model"]
    assert latest != first_model and not await candidate_retune_decider(h.state, branch)
    assert not evaluation_decision(branch).retune
    fits = [params for _, name, params in tools.calls if name == "train_estimator"]
    assert all(params["train"] == str(original) for params in fits)
    h.llm.responses[InterpretationDecision] = InterpretationDecision(
        rationale="Interpret latest validation model",
        coefficients=True,
        error_by=("x",),
        partial_dependence=("x",),
    )
    interpreted = await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 10))
    assert interpreted.status == "completed" and interpreted.summary["model"] == latest
    important = next(params for _, name, params in tools.calls if name == "permutation_importance")
    assert important["model"] == latest
    pdp = next(output for name, output in tools.outputs if name == "partial_dependence")
    assert pdp.artifacts[0].ref in interpreted.artifacts
    plotted = next(params for _, name, params in tools.calls if name == "plot_curve")
    assert plotted["curve"] == str(pdp.artifacts[0].ref)
    assert any(
        meta.kind == "plot" and meta.ref in interpreted.artifacts
        for name, output in tools.outputs
        if name == "plot_curve"
        for meta in output.artifacts
    )


@pytest.mark.asyncio
async def test_bounded_grid_and_cv_keep_reserved_effective_parameters_out_of_calls(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Bounded intercept comparison",
        method="grid",
        param_grid={"fit_intercept": (True, False)},
    )
    invocation = candidate_invocation(h, 8)
    fit = await CandidateModelExecutor(h.runtime)(invocation)
    messages = next(m for output, _, m in h.llm.calls if output is TrainingDecision)
    assert (
        "Tunable parameters for linear_regression: fit_intercept, positive."
        in messages[-1]["content"]
    )
    assert "class_weight, calibrate, random_state and n_jobs are fixed" in messages[-1]["content"]
    save_result(h, invocation, fit)
    assert fit.summary["cv_caveat"]
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Train diagnostics supplement validation",
        cross_validation=True,
        learning_curve=True,
    )
    score = await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))
    assert score.status == "completed"
    learning = next(output for name, output in tools.outputs if name == "compute_learning_curve")
    plot = next(output for name, output in tools.outputs if name == "plot_curve")
    assert learning.artifacts[0].ref in score.artifacts and plot.artifacts[0].ref in score.artifacts
    for _, name, params in tools.calls:
        if name in ("cross_validate_estimator", "compute_learning_curve"):
            assert params["params"] == {"fit_intercept": False}
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            modelling=ModellingRules(tuning=Tuning(method="none")),
        ),
    )
    assert (await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 8))).status == "failed"


@pytest.mark.asyncio
async def test_unavailable_primary_metric_is_real_failure_and_cancellation_propagates(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    invocation = candidate_invocation(h, 8)
    save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    tools.metric = None
    failed = await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))
    assert failed.status == "failed" and failed.error is not None and not failed.metrics
    tools.metric, tools.cancel_tool = 0.8, "predict"
    with pytest.raises(asyncio.CancelledError):
        await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))
    tools.cancel_tool, tools.failure_tool = None, "predict"
    assert (await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))).status == "failed"


@pytest.mark.asyncio
async def test_task_refusal_and_latest_evaluation_mismatch_are_not_partial_inputs(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    invocation = candidate_invocation(h, 8)
    save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Wrong task diagnostic", curves=("roc",)
    )
    assert (await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))).status == "failed"
    with pytest.raises(EngineError, match="boundary"):
        await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 10))
    h.llm.responses[EvaluationActions] = EvaluationActions(rationale="Compatible validation")
    invocation = candidate_invocation(h, 9)
    save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    branch = h.state.branches[0]
    last = branch.results[-1]
    altered = BoundaryResult(
        node_name=last.node_name,
        execution_id=last.execution_id,
        result=NodeResult(
            status="completed", summary={**last.result.summary, "model": "a_000000000000ffff"}
        ),
    )
    changed = CandidateProgress(
        public_id=branch.public_id,
        spec=branch.spec,
        results=(*branch.results[:-1], altered),
        position=10,
        status="running",
    )
    h.state = RunState.model_validate(
        {**h.state.model_dump(mode="json"), "branches": [changed.model_dump(mode="json")]}
    )
    with pytest.raises(EngineError, match="disagree"):
        await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 10))


@pytest.mark.asyncio
async def test_post_score_conclusion_sees_current_evidence_and_controls_durable_retune(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    invocation = candidate_invocation(h, 8)
    fitted = await CandidateModelExecutor(h.runtime)(invocation)
    save_result(h, invocation, fitted)
    tools.metric = 0.73
    tools.warning = "Current validation warning"
    actions = EvaluationActions(rationale="Choose actions before current score")
    h.llm.responses[EvaluationActions] = actions
    h.llm.responses[ValidationConclusion] = ValidationConclusion(
        rationale="Current validation RMSE is 0.73 with observed interval",
        retune=True,
        retune_reason="Observed score supports one intercept sensitivity retune",
    )
    invocation = candidate_invocation(h, 9)
    scored = await CandidateModelExecutor(h.runtime)(invocation)
    assert scored.status == "completed"
    assert h.llm.calls[-2][0] is EvaluationActions
    assert h.llm.calls[-1][0] is ValidationConclusion
    # Fresh evidence is supplied explicitly; the immutable invocation has no current evaluation.
    assert invocation.candidate is not None
    assert not any(item.node_name == "model_evaluation" for item in invocation.candidate.results)
    prompt = h.llm.calls[-1][2][-1]["content"]
    observed = json.loads(prompt.split("\n\n")[1])
    assert observed[0]["metrics"]["rmse"] == {"value": 0.73, "ci_low": 0.6, "ci_high": 0.9}
    assert observed[0]["model"] == fitted.summary["model"]
    assert observed[0]["threshold"] is None
    assert observed[0]["wave"] == 1 and observed[0]["retune_count"] == 0
    assert (
        observed[1]["limits"]["max_retune_loops"] == h.runtime.contract.constraints.max_retune_loops
    )
    assert "Current validation warning" in observed[1]["warnings"]
    assert scored.summary["actions"] == actions.model_dump(mode="json")
    save_result(h, invocation, scored)
    assert await candidate_retune_decider(h.state, h.state.branches[0])
    final = evaluation_decision(h.state.branches[0])
    assert final.retune and final.curves == actions.curves and not final.cross_validation
    assert "retune" not in EvaluationActions.model_fields
    # The post-score schema cannot request diagnostic actions that were never performed.
    with pytest.raises(ValidationError):
        ValidationConclusion.model_validate({"rationale": "Unperformed action", "curves": ["roc"]})


@pytest.mark.asyncio
async def test_threshold_only_roc_is_retained_without_unrequested_plot(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path, target_type="binary")
    await prepare_candidate(h)
    invocation = candidate_invocation(h, 8)
    save_result(h, invocation, await CandidateModelExecutor(h.runtime)(invocation))
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Choose threshold without requesting curve display",
        threshold_objective="f1",
    )
    scored = await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))
    assert scored.status == "completed"
    assert not any(name == "plot_curve" for _, name, _ in tools.calls)
    roc = next(output for name, output in tools.outputs if name == "compute_roc_curve")
    assert roc.artifacts[0].ref in scored.artifacts
    curves = scored.summary["curves"]
    assert isinstance(curves, dict) and isinstance(curves["roc"], dict)
    assert curves["roc"]["curve"] == str(roc.artifacts[0].ref) and "plot" not in curves["roc"]
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Requested curve", curves=("roc",)
    )
    tools.failure_tool = "plot_curve"
    failed = await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 9))
    assert failed.status == "failed" and not failed.artifacts


async def _run(h: Harness, index: int) -> NodeResult:
    return await CandidateModelExecutor(h.runtime)(candidate_invocation(h, index))


async def _run_save(h: Harness, index: int) -> NodeResult:
    invocation = candidate_invocation(h, index)
    output = await CandidateModelExecutor(h.runtime)(invocation)
    save_result(h, invocation, output)
    return output


def _failed(output: NodeResult, message: str) -> None:
    assert output.status == "failed"
    assert output.error is not None
    assert output.error.message == message


def _mode(h: Harness, mode: Literal["guided", "autonomous"]) -> None:
    h.state = h.state.model_copy(update={"mode": mode})


@pytest.mark.asyncio
async def test_durable_decision_and_executor_binding_guards(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, _ = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    await _run_save(h, 8)
    await _run_save(h, 9)
    branch = h.state.branches[0]
    last = branch.results[-1]
    bad = CandidateProgress(
        public_id=branch.public_id,
        spec=branch.spec,
        position=branch.position,
        status=branch.status,
        results=(
            *branch.results[:-1],
            BoundaryResult(
                node_name="model_evaluation",
                execution_id=last.execution_id,
                result=NodeResult(
                    status="completed",
                    summary={**last.result.summary, "decision": {"rationale": ""}},
                ),
            ),
        ),
    )
    with pytest.raises(EngineError, match="malformed durable evaluation decision"):
        evaluation_decision(bad)
    with pytest.raises(EngineError, match="retune branch is not checkpointed"):
        await candidate_retune_decider(h.state, bad)
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
    with pytest.raises(EngineError, match="model executor requires a candidate"):
        await CandidateModelExecutor(h.runtime)(NodeInvocation(h.state, shared, execution, None))
    with pytest.raises(EngineError, match="model executor cannot execute this node"):
        await CandidateModelExecutor(h.runtime)(candidate_invocation(h, 6))


@pytest.mark.asyncio
async def test_training_refusals_and_exception_wait(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        estimator=EstimatorSpec(name="linear_regression", params={"fit_intercept": True}),
    )
    await prepare_candidate(h)
    assert h.state.branches[0].spec.estimator.params == {"fit_intercept": True}
    contract = h.runtime.contract
    _mode(h, "guided")
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Needs review", material_exceptions=("Unusual grid",)
    )
    offset = len(tools.calls)
    assert (await _run(h, 8)).status == "awaiting_user"
    assert len(tools.calls) == offset
    _mode(h, "autonomous")
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=contract.target,
            objective=contract.objective,
            features=contract.features,
            modelling=ModellingRules(tuning=Tuning(method="random")),
        ),
    )
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Direct fit of approved estimator"
    )
    _failed(await _run(h, 8), "random search has no supported tool")
    h.runtime = replace(h.runtime, contract=contract)
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Change params directly", params={"fit_intercept": False}
    )
    _failed(await _run(h, 8), "initial direct parameters differ from approved candidate")
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Grid with fixed params", method="grid", params={"fit_intercept": True}
    )
    _failed(await _run(h, 8), "grid decision must put fixed parameters in param_grid")
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Grid without explicit grid", method="grid"
    )
    _failed(await _run(h, 8), "approved parameters require an explicit grid")
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Direct with grid", param_grid={"positive": (True, False)}
    )
    _failed(await _run(h, 8), "direct fitting cannot accept a parameter grid")
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Grid over a foreign parameter", method="grid", param_grid={"C": (0.5, 1.0)}
    )
    _failed(
        await _run(h, 8),
        "parameters ['C'] are not tunable for linear_regression; "
        "allowed: ['fit_intercept', 'positive']",
    )
    names = [name for _, name, _ in tools.calls]
    assert "train_estimator" not in names and "tune_estimator" not in names


@pytest.mark.parametrize(
    "payload",
    [
        {"rationale": "r", "params": {"penalty": "l1"}},
        {"rationale": "r", "method": "grid", "param_grid": {"C": (0.5, -1.0)}},
    ],
)
def test_training_decision_rejects_untunable_params(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TrainingDecision.model_validate(payload)


@pytest.mark.asyncio
async def test_multiclass_threshold_and_brier_refusals(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path, target_type="multiclass")
    await prepare_candidate(h)
    await _run_save(h, 8)
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Threshold on multiclass", threshold_objective="f1"
    )
    _failed(
        await _run(h, 9),
        "threshold, roc, pr and calibration curves require binary classification; "
        "multiclass allows only the confusion curve",
    )
    check = h.llm.checks[EvaluationActions]
    assert check(EvaluationActions(rationale="r", curves=("confusion",))) is None
    for rejected in (
        EvaluationActions(rationale="r", curves=("roc",)),
        EvaluationActions(rationale="r", curves=("pr",)),
        EvaluationActions(rationale="r", threshold_objective="f1"),
    ):
        with pytest.raises(DecisionRejectedError, match="binary"):
            check(rejected)
    h.llm.responses[EvaluationActions] = EvaluationActions(rationale="Compatible validation")
    await _run_save(h, 9)
    index = next(
        i for i, b in enumerate(h.state.shared_results) if b.node_name == "problem_definition"
    )
    old = h.state.shared_results[index]
    problem = problem_from_state(h.state)
    brier = ProblemSpec.model_validate(
        {**problem.model_dump(mode="json"), "primary_metric": "brier", "metric_rule": "explicit"}
    )
    new = BoundaryResult(
        node_name=old.node_name,
        execution_id=old.execution_id,
        result=NodeResult(
            status="completed",
            summary={**old.result.summary, "problem": brier.model_dump(mode="json")},
            artifacts=old.result.artifacts,
        ),
    )
    h.state = RunState.model_validate(
        {
            **h.state.model_dump(mode="json"),
            "shared_results": [
                new.model_dump(mode="json") if i == index else b.model_dump(mode="json")
                for i, b in enumerate(h.state.shared_results)
            ],
        }
    )
    _failed(await _run(h, 10), "multiclass Brier permutation scorer is unsupported")
    assert "permutation_importance" not in [name for _, name, _ in tools.calls]


@pytest.mark.asyncio
async def test_binary_threshold_range_and_calibrated_diagnostic_refusals(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        target_type="binary",
        estimator=EstimatorSpec(name="logistic_regression", calibrate=True),
    )
    await prepare_candidate(h)
    await _run_save(h, 8)
    tools.threshold = 1.0
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Choose threshold", threshold_objective="f1"
    )
    _failed(await _run(h, 9), "selected binary threshold is unavailable or outside (0,1)")
    tools.threshold = 0.4
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="CV requested", cross_validation=True
    )
    _failed(await _run(h, 9), "train diagnostic tool cannot reproduce calibrated estimator")
    assert "cross_validate_estimator" not in [name for _, name, _ in tools.calls]


@pytest.mark.asyncio
async def test_evaluation_exception_wait_and_out_of_range_after_action_wait(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(migrated_db, project, tmp_path)
    await prepare_candidate(h)
    await _run_save(h, 8)
    _mode(h, "guided")
    h.llm.responses[EvaluationActions] = EvaluationActions(
        rationale="Review actions", material_exceptions=("Skip CV",)
    )
    offset = len(tools.calls)
    assert (await _run(h, 9)).status == "awaiting_user"
    assert len(tools.calls) == offset
    h.llm.responses[EvaluationActions] = EvaluationActions(rationale="Score validation")
    tools.metric = -1.0
    output = await _run(h, 9)
    assert output.status == "awaiting_user"
    assert output.metrics == {}
    assert "Primary metric is outside its mathematical range" in output.warnings
    assert "score_regression" in [name for _, name, _ in tools.calls]


@pytest.mark.asyncio
async def test_interpretation_wait_skip_coefficients_and_unscaled_pdp(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        feature=FeatureSpec(scaling="none"),
        estimator=EstimatorSpec(name="decision_tree"),
    )
    await prepare_candidate(h)
    await _run_save(h, 8)
    await _run_save(h, 9)
    _mode(h, "guided")
    h.llm.responses[InterpretationDecision] = InterpretationDecision(
        rationale="Review", material_exceptions=("Leakage suspicion",)
    )
    assert (await _run(h, 10)).status == "awaiting_user"
    _mode(h, "autonomous")
    h.llm.responses[InterpretationDecision] = InterpretationDecision(
        rationale="Skip", skip_reason="Not needed for this candidate"
    )
    skipped = await _run(h, 10)
    assert skipped.status == "skipped"
    assert skipped.skip_reason == "Not needed for this candidate"
    decision = skipped.summary["decision"]
    assert isinstance(decision, dict)
    assert decision["skip_reason"] == skipped.skip_reason
    h.llm.responses[InterpretationDecision] = InterpretationDecision(
        rationale="Coefficients", coefficients=True
    )
    _failed(
        await _run(h, 10), "coefficient importance requires supported uncalibrated linear estimator"
    )
    h.llm.responses[InterpretationDecision] = InterpretationDecision(
        rationale="PDP", partial_dependence=("x",)
    )
    assert (await _run(h, 10)).status == "completed"
    pdp = [params for _, name, params in tools.calls if name == "partial_dependence"]
    assert len(pdp) == 1
    assert set(pdp[0]) == {"model", "frame", "column"}


@pytest.mark.asyncio
async def test_grid_without_explicit_grid_uses_registry_default(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
) -> None:
    h, tools = await candidate_harness(
        migrated_db,
        project,
        tmp_path,
        estimator=EstimatorSpec(name="linear_regression"),
    )
    await prepare_candidate(h)
    h.llm.responses[TrainingDecision] = TrainingDecision(
        rationale="Registry default grid", method="grid"
    )
    output = await _run(h, 8)
    assert output.status == "completed"
    tune = [params for _, name, params in tools.calls if name == "tune_estimator"]
    train = [params for _, name, params in tools.calls if name == "train_estimator"]
    assert len(tune) == 1 and len(train) == 1
    assert tune[0]["param_grid"] is None
    assert tune[0]["metric"] == "rmse"
    assert train[0]["params"] == {"fit_intercept": False}
    assert output.summary["cv_caveat"] == CV_CAVEAT


@pytest.mark.parametrize(
    "payload",
    (
        {"rationale": "r", "params": {"random_state": 1}},
        {"rationale": "r", "method": "grid", "param_grid": {"n_jobs": [1]}},
    ),
)
def test_training_decision_rejects_tool_fixed_params(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="fixed by the tools"):
        TrainingDecision.model_validate(payload)
