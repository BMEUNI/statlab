"""Offline stage-27b cases; runtime execution is withheld by user instruction."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import BaseModel, JsonValue, ValidationError
from statlab.domains.base import NodeResult
from statlab.domains.decisions import DesignDecision, InferenceDecision
from statlab.domains.design_inference import (
    DesignInferenceExecutor,
    _artifact,
    candidate_set_from_state,
    initial_candidate_planner,
)
from statlab.domains.runtime import Approval
from statlab.domains.tabular import TABULAR_SPINE, TABULAR_TRAVERSAL
from statlab.errors import EngineError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import BoundaryResult, CandidateProgress
from statlab.llm.client import DecisionRejectedError
from statlab.llm.skills import NodeSkill, SkillPolicy
from statlab.mcp_client import CallContext, Repair
from statlab.persistence.models import ProjectRow, RunMode
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef
from statlab_contracts.candidate import (
    CandidateSet,
    CandidateSpec,
    EstimatorSpec,
    PreprocessingSpec,
)
from statlab_contracts.contract import (
    DataContract,
    FeatureRules,
    InferenceGoal,
    MissingPolicy,
    ModellingRules,
    Objective,
    PreprocessingRules,
    TargetSpec,
)
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.vocabulary import ParamValue, TargetType

from tests.test_nodes_foundation import TEST, TRAIN, VALID, Harness, OfflineTools, harness

MATRIX = ArtifactRef("a_0000000000000010")
MANIFEST = ArtifactRef("a_0000000000000011")
FIT = ArtifactRef("a_0000000000000012")
COEFFICIENTS = ArtifactRef("a_0000000000000013")
VIF = ArtifactRef("a_0000000000000014")
INFLUENCE = ArtifactRef("a_0000000000000015")
PLOT = ArtifactRef("a_0000000000000016")


def registry() -> dict[str, JsonValue]:
    return {
        "estimators": {
            "linear_regression": {
                "task": "regression",
                "family": "linear",
                "interpretable": True,
                "supports_class_weight": False,
            },
            "ridge": {
                "task": "regression",
                "family": "linear",
                "interpretable": True,
                "supports_class_weight": False,
            },
            "random_forest_regressor": {
                "task": "regression",
                "family": "random_forest",
                "interpretable": False,
                "supports_class_weight": False,
            },
            "logistic_regression": {
                "task": "classification",
                "family": "linear",
                "interpretable": True,
                "supports_class_weight": True,
            },
        },
        "inferential_models": {
            "ols": {"tool": "fit_ols", "target_types": ["continuous"], "family": None},
            "logit": {"tool": "fit_logit", "target_types": ["binary"], "family": None},
            "mnlogit": {"tool": "fit_mnlogit", "target_types": ["multiclass"], "family": None},
            "glm_poisson": {"tool": "fit_glm", "target_types": ["count"], "family": "poisson"},
            "glm_gamma": {"tool": "fit_glm", "target_types": ["continuous"], "family": "gamma"},
        },
    }


def _variant(name: str, i: int) -> dict[str, ParamValue]:
    if i == 0:
        return {}
    if name == "logistic_regression":
        return {"C": float(i)}
    if name == "ridge":
        return {"alpha": float(i)}
    if name == "linear_regression":
        variants: tuple[dict[str, ParamValue], ...] = (
            {},
            {"fit_intercept": False},
            {"positive": True},
            {"fit_intercept": False, "positive": True},
        )
        return variants[i]
    return {"n_estimators": 100 + i}


def shortlist(name: str = "linear_regression", count: int = 1) -> DesignDecision:
    return DesignDecision(
        rationale="Compare interpretable approaches using observed profiling.",
        candidate_set=CandidateSet(
            candidates=tuple(
                CandidateSpec(
                    label=f"Approach {i}",
                    rationale="Observed numeric feature supports this approach.",
                    is_baseline=i == 0,
                    estimator=EstimatorSpec(name=name, params=_variant(name, i)),
                    varies_from_baseline=() if i == 0 else ("hyperparameters",),
                )
                for i in range(count)
            )
        ),
    )


class DesignTools(OfflineTools):
    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.registry = registry()
        self.converged = True

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
        self.calls.append((ctx, tool, params.copy()))
        if tool == self.cancel_tool:
            raise asyncio.CancelledError
        if tool == self.failure_tool:
            raise ToolCallError(tool, Failure(error_class="data", message="fit refused"))
        if tool == "list_estimators":
            return ToolSuccess(summary=self.registry, warnings=("Registry warning",))
        if tool == "assemble_inference_matrix":
            assert params["train"] == str(TRAIN) and params["valid"] == str(VALID)
            return ToolSuccess(
                summary={"terms": ["x"], "references": {}, "n_obs": 190},
                artifacts=(
                    ArtifactMeta(ref=MATRIX, kind="dataframe", label="matrix"),
                    ArtifactMeta(ref=MANIFEST, kind="table", label="manifest"),
                ),
            )
        if tool.startswith("fit_"):
            return ToolSuccess(
                summary={"converged": self.converged},
                artifacts=(ArtifactMeta(ref=FIT, kind="inferential_fit", label="fit"),),
            )
        if tool in ("coefficient_table", "compute_vif", "compute_influence"):
            ref = {
                "coefficient_table": COEFFICIENTS,
                "compute_vif": VIF,
                "compute_influence": INFLUENCE,
            }[tool]
            return ToolSuccess(
                summary={"actual": tool, "goals": params.get("goals", [])},
                artifacts=(ArtifactMeta(ref=ref, kind="table", label=tool),),
            )
        if tool == "plot_residuals":
            return ToolSuccess(artifacts=(ArtifactMeta(ref=PLOT, kind="plot", label="residuals"),))
        return ToolSuccess(summary={"actual": tool, "converged": self.converged})


async def prepared(
    conn: sqlite3.Connection,
    project: ProjectRow,
    root: Path,
    *,
    target_type: TargetType = "continuous",
    goals: bool = True,
    modelling: ModellingRules | None = None,
    mode: RunMode = "autonomous",
    approval: Approval | None = None,
) -> tuple[Harness, DesignTools]:
    contract = DataContract(
        target=TargetSpec(column="y", type=target_type),
        objective=Objective(
            primary_metric="roc_auc" if target_type in ("binary", "multiclass") else "rmse",
            inference_goals=(InferenceGoal(question="Does x relate to y?", predictors=("x",)),)
            if goals
            else (),
        ),
        modelling=modelling or ModellingRules(),
    )
    result = harness(conn, project, root, contract=contract)
    await result.advance(4)
    tools = DesignTools(conn)
    allowed = frozenset(
        {
            "list_estimators",
            "assemble_inference_matrix",
            "fit_ols",
            "fit_logit",
            "fit_mnlogit",
            "fit_glm",
            "coefficient_table",
            "inferential_fit_summary",
            "compute_vif",
            "test_residuals",
            "compute_influence",
            "plot_residuals",
        }
    )
    skill = SkillPolicy(
        "immutable",
        tuple(
            NodeSkill(
                spec.name,
                "section",
                "purpose",
                "heuristics",
                "tools",
                "outputs",
                "pitfalls",
                allowed,
            )
            for spec in TABULAR_SPINE
        ),
        b"immutable",
    )
    result.runtime = replace(result.runtime, tools=tools, skill=skill, approval=approval)
    result.state = result.state.model_copy(update={"mode": mode})
    result.llm.responses[DesignDecision] = shortlist(
        "logistic_regression" if target_type in ("binary", "multiclass") else "linear_regression"
    )
    result.llm.responses[InferenceDecision] = InferenceDecision(
        model={
            "continuous": "ols",
            "binary": "logit",
            "multiclass": "mnlogit",
            "count": "glm_poisson",
        }[target_type],
        predictors=("x",),
        rationale="Predeclared association; no causal claim.",
        residual_plot=True,
    )
    return result, tools


async def design(h: Harness) -> NodeResult:
    invocation = h.invocation(4)
    output = await DesignInferenceExecutor(h.runtime)(invocation)
    if output.status == "completed":
        h.state = h.state.model_copy(
            update={
                "cursor": 5,
                "shared_results": (
                    *h.state.shared_results,
                    BoundaryResult(
                        node_name=invocation.spec.name,
                        execution_id=invocation.execution.id,
                        result=output,
                    ),
                ),
            }
        )
    return output


@pytest.mark.asyncio
async def test_approved_design_and_durable_initial_planner(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path)
    output = await design(h)
    assert output.status == "completed" and output.warnings == ("Registry warning",)
    assert candidate_set_from_state(h.state) == shortlist().candidate_set
    assert await initial_candidate_planner(h.state, 1) == shortlist().candidate_set.candidates
    assert await initial_candidate_planner(h.state, 2) == ()
    branch = CandidateProgress(public_id="c_01", spec=shortlist().candidate_set.candidates[0])
    assert (
        await initial_candidate_planner(h.state.model_copy(update={"branches": (branch,)}), 1) == ()
    )
    assert [name for _, name, _ in tools.calls] == ["list_estimators"]
    assert TABULAR_TRAVERSAL.region(h.state.spine) == (6, 11)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "violation",
    [
        "unknown",
        "task",
        "baseline",
        "weight",
        "calibrate",
        "row_threshold",
        "column",
        "max",
        "family",
        "explicit",
        "pool",
    ],
)
async def test_candidate_set_valid_external_refusals(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, violation: str
) -> None:
    h, tools = await prepared(
        migrated_db,
        project,
        tmp_path,
        target_type="binary" if violation == "explicit" else "continuous",
    )
    proposed = shortlist(
        name="logistic_regression"
        if violation == "explicit"
        else "ridge"
        if violation == "max"
        else "linear_regression",
        count=9 if violation == "max" else 3 if violation == "family" else 1,
    ).model_dump(mode="json")
    candidates = proposed["candidate_set"]["candidates"]
    estimator = candidates[0]["estimator"]
    if violation == "unknown":
        estimator["name"] = "unregistered"
    elif violation == "task":
        estimator["name"] = "logistic_regression"
    elif violation == "baseline":
        estimator["name"] = "random_forest_regressor"
    elif violation == "weight":
        estimator["class_weight"] = "balanced"
    elif violation == "calibrate":
        estimator["calibrate"] = True
    elif violation == "row_threshold":
        candidates[0]["preprocessing"]["missing"]["drop_rows_above"] = 0.5
    elif violation == "column":
        candidates[0]["features"]["interactions"] = [["x", "y"]]
    elif violation == "explicit":
        h.runtime = replace(
            h.runtime,
            contract=DataContract(
                target=h.runtime.contract.target,
                objective=h.runtime.contract.objective,
                modelling=ModellingRules(calibrate=False),
            ),
        )
        estimator["calibrate"] = True
    elif violation == "pool":
        h.runtime = replace(
            h.runtime,
            contract=DataContract(
                target=h.runtime.contract.target,
                objective=h.runtime.contract.objective,
                modelling=ModellingRules(candidates=("ridge",)),
            ),
        )
    h.llm.responses[DesignDecision] = DesignDecision.model_validate(proposed)
    output = await design(h)
    assert (
        output.status == "failed"
        and output.error is not None
        and output.error.error_class == "data"
    )
    assert len(tools.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "default",
        "per_column",
        "scaling",
        "categorical_encoding",
        "max_cardinality",
        "outliers",
        "feature_pool",
    ],
)
async def test_explicit_preprocessing_and_feature_pool_constrain_design(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, field: str
) -> None:
    h, _ = await prepared(migrated_db, project, tmp_path)
    preprocessing = PreprocessingRules(
        missing=MissingPolicy(default="most_frequent")
        if field == "default"
        else MissingPolicy(per_column={"x": "most_frequent"})
        if field == "per_column"
        else MissingPolicy(),
        scaling="none" if field == "scaling" else "standard",
        categorical_encoding="ordinal" if field == "categorical_encoding" else "onehot",
        max_cardinality=50 if field == "max_cardinality" else 30,
        outliers="winsorize" if field == "outliers" else "none",
    )
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=Objective(primary_metric="rmse")
            if field == "feature_pool"
            else h.runtime.contract.objective,
            preprocessing=preprocessing,
            features=FeatureRules(include=()) if field == "feature_pool" else FeatureRules(),
        ),
    )
    if field == "feature_pool":
        proposal = shortlist().model_dump(mode="json")
        proposal["candidate_set"]["candidates"][0]["features"]["binned"] = {"x": 3}
        h.llm.responses[DesignDecision] = DesignDecision.model_validate(proposal)
    assert (await design(h)).status == "failed"


@pytest.mark.asyncio
async def test_guided_design_and_edited_approval_revalidate(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path, mode="guided")
    assert (await design(h)).status == "awaiting_user"

    async def approve(
        inv: NodeInvocation, proposal: BaseModel, evidence: tuple[JsonValue, ...]
    ) -> BaseModel:
        assert inv.candidate is None and evidence and isinstance(proposal, DesignDecision)
        return shortlist("random_forest_regressor")

    h.runtime = replace(h.runtime, approval=approve)
    assert (await design(h)).status == "failed"
    assert all(name == "list_estimators" for _, name, _ in tools.calls)


@pytest.mark.asyncio
async def test_family_justification_and_supplied_branches(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await prepared(migrated_db, project, tmp_path)
    proposal = DesignDecision(
        candidate_set=shortlist(count=3).candidate_set,
        rationale="Different regularization justified by profile.",
        family_justifications={"linear": "Three explicit regularization comparisons"},
    )
    h.llm.responses[DesignDecision] = proposal
    h.state = h.state.model_copy(
        update={
            "branches": tuple(
                CandidateProgress(public_id=f"c_{i:02d}", spec=spec)
                for i, spec in enumerate(proposal.candidate_set.candidates, 1)
            )
        }
    )
    assert (await design(h)).status == "completed"
    h.llm.responses[DesignDecision] = shortlist()
    assert (await design(h)).status == "failed"


@pytest.mark.asyncio
async def test_shared_row_threshold_can_be_preserved(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await prepared(migrated_db, project, tmp_path)
    missing = MissingPolicy(drop_rows_above=0.5)
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            preprocessing=PreprocessingRules(missing=missing),
        ),
    )
    h.llm.responses[DesignDecision] = DesignDecision(
        rationale="Preserve the shared structural row policy.",
        candidate_set=CandidateSet(
            candidates=(
                CandidateSpec(
                    label="Shared rows",
                    rationale="Observed numeric structure supports baseline.",
                    is_baseline=True,
                    estimator=EstimatorSpec(name="linear_regression"),
                    preprocessing=PreprocessingSpec(missing=missing),
                ),
            )
        ),
    )
    assert (await design(h)).status == "completed"


@pytest.mark.asyncio
async def test_explicit_gamma_and_reference_levels(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await prepared(
        migrated_db, project, tmp_path, modelling=ModellingRules(inferential_model="glm_gamma")
    )
    await design(h)
    h.runtime = replace(
        h.runtime,
        contract=DataContract(
            target=h.runtime.contract.target,
            objective=h.runtime.contract.objective,
            modelling=h.runtime.contract.modelling,
            features=FeatureRules(roles={"x": "categorical"}),
        ),
    )
    h.llm.responses[InferenceDecision] = InferenceDecision(
        model="glm_gamma", predictors=("x",), rationale="Explicit compatible Gamma choice."
    )
    assert (await DesignInferenceExecutor(h.runtime)(h.invocation(5))).status == "failed"
    h.llm.responses[InferenceDecision] = InferenceDecision(
        model="glm_gamma",
        predictors=("x",),
        reference_levels={"x": "reference"},
        rationale="Explicit compatible Gamma choice.",
    )
    output = await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    assert output.status == "completed"
    assert next(params["family"] for _, name, params in tools.calls if name == "fit_glm") == "gamma"
    assert next(
        params["reference_levels"]
        for _, name, params in tools.calls
        if name == "assemble_inference_matrix"
    ) == {"x": "reference"}


@pytest.mark.asyncio
async def test_no_goals_skips_without_llm_or_tools(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path, goals=False)
    count = len(h.llm.calls)
    output = await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    assert output.status == "skipped" and output.skip_reason
    assert not tools.calls and len(h.llm.calls) == count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_type", "fit_tool", "family"),
    [
        ("continuous", "fit_ols", None),
        ("binary", "fit_logit", None),
        ("multiclass", "fit_mnlogit", None),
        ("count", "fit_glm", "poisson"),
    ],
)
async def test_one_fit_original_goals_no_test_and_applicable_diagnostics(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    target_type: TargetType,
    fit_tool: str,
    family: str | None,
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path, target_type=target_type)
    assert (await design(h)).status == "completed"
    output = await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    assert output.status == "completed"
    names = [name for _, name, _ in tools.calls]
    assert names.count("assemble_inference_matrix") == 1
    assert [name for name in names if name.startswith("fit_")] == [fit_tool]
    fit_params = next(params for _, name, params in tools.calls if name == fit_tool)
    assert fit_params.get("family") == family
    assert all(str(TEST) not in str(params) for _, _, params in tools.calls)
    goals = next(params["goals"] for _, name, params in tools.calls if name == "coefficient_table")
    assert goals == [
        goal.model_dump(mode="json") for goal in h.runtime.contract.objective.inference_goals
    ]
    assert (
        MATRIX in output.artifacts
        and MANIFEST in output.artifacts
        and COEFFICIENTS in output.artifacts
    )
    assert ("test_residuals" in names) == (target_type == "continuous")
    if target_type == "continuous":
        assert next(
            params["tests"] for _, name, params in tools.calls if name == "test_residuals"
        ) == ["normality", "heteroskedasticity"]
    assert ("compute_influence" in names) == (target_type != "multiclass")
    assert ("plot_residuals" in names) == (target_type != "multiclass")
    if target_type == "multiclass":
        assert any("MNLogit" in warning for warning in output.warnings)


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["model", "missing_goal", "duplicate", "target", "reference"])
async def test_inference_decision_refused_before_fit(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path, violation: str
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path)
    await design(h)
    raw = InferenceDecision(
        model="ols", predictors=("x",), rationale="Fixed goals", reference_levels={}
    ).model_dump(mode="json")
    if violation == "model":
        raw["model"] = "logit"
    elif violation == "missing_goal":
        raw["predictors"] = ["id"]
    elif violation == "duplicate":
        raw["predictors"] = ["x", "x"]
    elif violation == "target":
        raw["predictors"] = ["x", "y"]
    else:
        raw["reference_levels"] = {"unknown": "level"}
    h.llm.responses[InferenceDecision] = InferenceDecision.model_validate(raw)
    assert (await DesignInferenceExecutor(h.runtime)(h.invocation(5))).status == "failed"
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_inference_gate_refusal_cancellation_and_nonconvergence(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path)
    await design(h)
    h.state = h.state.model_copy(update={"mode": "guided"})
    assert (await DesignInferenceExecutor(h.runtime)(h.invocation(5))).status == "awaiting_user"
    assert len(tools.calls) == 1
    h.state = h.state.model_copy(update={"mode": "autonomous"})
    tools.failure_tool = "fit_ols"
    refused = await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    assert refused.error is not None and refused.error.error_class == "data"
    tools.failure_tool, tools.cancel_tool = None, "fit_ols"
    with pytest.raises(asyncio.CancelledError):
        await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    tools.cancel_tool, tools.converged = None, False
    output = await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    assert output.status == "completed" and any(
        "did not converge" in warning for warning in output.warnings
    )


@pytest.mark.asyncio
async def test_missing_malformed_boundaries_and_deferred_nodes(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await prepared(migrated_db, project, tmp_path)
    with pytest.raises(EngineError):
        candidate_set_from_state(h.state)
    await design(h)
    last = h.state.shared_results[-1]
    malformed: tuple[JsonValue, ...] = (None, {"candidates": []})
    for value in malformed:
        broken = h.state.model_copy(
            update={
                "shared_results": (
                    *h.state.shared_results[:-1],
                    last.model_copy(
                        update={
                            "result": NodeResult(
                                status="completed", summary={"candidate_set": value}
                            )
                        }
                    ),
                )
            }
        )
        with pytest.raises(EngineError):
            candidate_set_from_state(broken)
    with pytest.raises(EngineError):
        await DesignInferenceExecutor(h.runtime)(h.invocation(6))
    with pytest.raises(ValidationError):
        InferenceDecision.model_validate({"model": "ols", "predictors": [], "rationale": "invalid"})


def test_artifact_helper_requires_exactly_one_kind() -> None:
    with pytest.raises(EngineError, match="tool did not return exactly one table artifact"):
        _artifact(ToolSuccess(), "table")


@pytest.mark.asyncio
async def test_registry_without_family_fails_closed(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path)
    entries = tools.registry["estimators"]
    assert isinstance(entries, dict)
    entry = entries["linear_regression"]
    assert isinstance(entry, dict)
    del entry["family"]
    with pytest.raises(EngineError, match="estimator registry omitted family"):
        await design(h)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("missing", "inference model is not registered"),
        ("target", "inference model is incompatible with target type"),
        ("tool", "registry inference fit tool is unavailable"),
    ],
)
async def test_inference_registry_refusals(
    migrated_db: sqlite3.Connection,
    project: ProjectRow,
    tmp_path: Path,
    change: str,
    message: str,
) -> None:
    h, tools = await prepared(migrated_db, project, tmp_path)
    models = tools.registry["inferential_models"]
    assert isinstance(models, dict)
    if change == "missing":
        del models["ols"]
    elif change == "target":
        models["ols"] = {"tool": "fit_ols", "target_types": ["binary"], "family": None}
    else:
        models["ols"] = {"tool": "fit_unknown", "target_types": ["continuous"], "family": None}
    assert (await design(h)).status == "completed"
    if change == "tool":
        with pytest.raises(EngineError, match=message):
            await DesignInferenceExecutor(h.runtime)(h.invocation(5))
        return
    output = await DesignInferenceExecutor(h.runtime)(h.invocation(5))
    assert output.status == "failed"
    assert output.error is not None and output.error.message == message


@pytest.mark.asyncio
async def test_design_and_inference_checks_feed_llm_repair(
    migrated_db: sqlite3.Connection, project: ProjectRow, tmp_path: Path
) -> None:
    h, _ = await prepared(migrated_db, project, tmp_path)
    assert (await design(h)).status == "completed"
    design_check = h.llm.checks[DesignDecision]
    assert design_check(shortlist()) is None
    with pytest.raises(DecisionRejectedError):
        design_check(shortlist("logistic_regression"))
    assert (await DesignInferenceExecutor(h.runtime)(h.invocation(5))).status == "completed"
    inference_check = h.llm.checks[InferenceDecision]
    valid = InferenceDecision(model="ols", predictors=("x",), rationale="r")
    assert inference_check(valid) is None
    with pytest.raises(DecisionRejectedError):
        inference_check(InferenceDecision(model="logit", predictors=("x",), rationale="r"))
