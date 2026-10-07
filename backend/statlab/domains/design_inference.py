"""Approved predictive shortlist and one canonical inferential companion."""

from __future__ import annotations

from collections import Counter

from pydantic import JsonValue, ValidationError
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import CandidateSet, CandidateSpec
from statlab_contracts.envelope import Failure, ToolSuccess

from statlab.domains.base import NodeResult
from statlab.domains.decisions import DesignDecision, InferenceDecision, ProblemSpec
from statlab.domains.foundation import (
    _Evidence,
    _object,
    _ref,
    _RefusalError,
    _rejecting,
    _strings,
    _tool_summary,
    problem_from_state,
    shared_result,
)
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import RunState


def candidate_set_from_state(state: RunState) -> CandidateSet:
    summary = shared_result(state, "experiment_design").summary
    try:
        return CandidateSet.model_validate(_object(summary.get("candidate_set"), "candidate set"))
    except ValidationError as exc:
        raise EngineError("malformed shared candidate set", cause=exc) from exc


async def initial_candidate_planner(state: RunState, wave: int) -> tuple[CandidateSpec, ...]:
    if wave != 1 or state.branches:
        return ()
    return candidate_set_from_state(state).candidates


def _artifact(output: ToolSuccess, kind: str) -> ArtifactRef:
    refs = tuple(artifact.ref for artifact in output.artifacts if artifact.kind == kind)
    if len(refs) != 1:
        raise EngineError(f"tool did not return exactly one {kind} artifact")
    return refs[0]


class DesignInferenceExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        methods = {"experiment_design": self._design, "inferential_analysis": self._inference}
        method = methods.get(invocation.spec.name)
        if method is None:
            raise EngineError("design/inference executor cannot execute deferred node")
        self.runtime.context(invocation)
        evidence = _Evidence()
        try:
            return await method(invocation, evidence)
        except (_RefusalError, ValidationError) as exc:
            return evidence.result(
                "failed", {}, error=Failure(error_class="data", message=str(exc))
            )
        except ToolCallError as exc:
            return evidence.result("failed", {}, error=exc.failure)

    async def _call(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        tool: str,
        params: dict[str, JsonValue],
    ) -> ToolSuccess:
        output = await self.runtime.call(invocation, tool, params)
        evidence.add(tool, output)
        return output

    def _eligible(self, state: RunState, target: str) -> set[str]:
        intake = shared_result(state, "intake")
        columns = set(_strings(_tool_summary(intake, "load_csv").get("columns"), "columns"))
        rules = self.runtime.contract.features
        if rules.include is not None:
            columns &= set(rules.include)
        return columns - {target, *rules.exclude, *rules.id_columns}

    def validate_design(
        self,
        invocation: NodeInvocation,
        decision: DesignDecision,
        registry: dict[str, JsonValue],
        problem: ProblemSpec,
        *,
        require_saved: bool = True,
    ) -> None:
        contract = self.runtime.contract
        candidates = decision.candidate_set.candidates
        if len(candidates) > contract.constraints.max_candidates:
            raise _RefusalError("shortlist exceeds max_candidates")
        entries = _object(registry.get("estimators"), "estimator registry")
        eligible = self._eligible(invocation.state, problem.target)
        families: Counter[str] = Counter()
        for candidate in candidates:
            estimator = candidate.estimator
            entry = entries.get(estimator.name)
            if not isinstance(entry, dict) or entry.get("task") != problem.task:
                raise _RefusalError("candidate estimator is not registered for task")
            if (
                contract.modelling.candidates
                and estimator.name not in contract.modelling.candidates
            ):
                raise _RefusalError("candidate estimator is outside contract pool")
            family = entry.get("family")
            if not isinstance(family, str) or not family:
                raise EngineError("estimator registry omitted family")
            families[family] += 1
            if candidate.is_baseline and (
                family != "linear" or entry.get("interpretable") is not True
            ):
                raise _RefusalError("baseline must be an interpretable linear estimator")
            if estimator.class_weight != "none" and entry.get("supports_class_weight") is not True:
                raise _RefusalError("estimator does not support class weighting")
            if estimator.calibrate and problem.task != "classification":
                raise _RefusalError("calibration requires classification")
            for name in ("class_weight", "calibrate"):
                if name in contract.modelling.model_fields_set and getattr(
                    estimator, name
                ) != getattr(contract.modelling, name):
                    raise _RefusalError(f"candidate conflicts with explicit modelling.{name}")
            missing = candidate.preprocessing.missing
            rules = contract.preprocessing
            if missing.drop_rows_above != rules.missing.drop_rows_above:
                raise _RefusalError("candidate row threshold must equal shared pre-split threshold")
            for name in ("default", "per_column"):
                if name in rules.missing.model_fields_set and getattr(missing, name) != getattr(
                    rules.missing, name
                ):
                    raise _RefusalError(f"candidate conflicts with explicit missing.{name}")
            for contract_name, actual in (
                ("outliers", candidate.preprocessing.outliers),
                ("scaling", candidate.features.scaling),
                ("categorical_encoding", candidate.features.encoding),
                ("max_cardinality", candidate.features.max_cardinality),
            ):
                if contract_name in rules.model_fields_set and actual != getattr(
                    rules, contract_name
                ):
                    raise _RefusalError(f"candidate conflicts with explicit {contract_name}")
            named = {
                *missing.per_column,
                *candidate.features.datetime_parts,
                *candidate.features.binned,
                *(column for pair in candidate.features.interactions for column in pair),
            }
            if named - eligible:
                raise _RefusalError("candidate choices name unknown or ineligible columns")
        for family, count in families.items():
            if count > 2 and not decision.family_justifications.get(family, "").strip():
                raise _RefusalError(
                    f"more than two candidates in family {family!r} "
                    "need a family_justifications entry"
                )
        if (
            require_saved
            and invocation.state.branches
            and tuple(branch.spec.model_dump(mode="json") for branch in invocation.state.branches)
            != tuple(candidate.model_dump(mode="json") for candidate in candidates)
        ):
            raise _RefusalError("approved shortlist must match existing candidate specifications")

    async def _design(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        problem = problem_from_state(invocation.state)
        registry = await self._call(invocation, evidence, "list_estimators", {"task": problem.task})
        decision = await self.runtime.decide(
            invocation,
            DesignDecision,
            evidence=tuple(evidence.tools),
            question="Use actual profiling, problem and memory to propose normally 3-5 reasoned "
            "candidates within max_candidates. Include an interpretable linear baseline, vary "
            "recorded dimensions and justify families above two. Respect explicit contract "
            "choices and shared pre-split row threshold. If checkpointed candidates exist, "
            "confirm that exact cumulative set, preserving every specification. "
            "Do not fit or probe parameters. Estimator params hold single fixed values "
            "(empty for defaults); never put tuning grids or lists there, grids are chosen "
            "later at model_training.",
            check=_rejecting(
                lambda d: self.validate_design(invocation, d, registry.summary, problem)
            ),
        )
        self.validate_design(invocation, decision, registry.summary, problem)
        summary: dict[str, JsonValue] = {
            "decision": decision.model_dump(mode="json"),
            "candidate_set": decision.candidate_set.model_dump(mode="json"),
            "registry": registry.summary,
        }
        approved = await self.runtime.approve(invocation, decision, tuple(evidence.tools))
        if approved is None:
            return evidence.result("awaiting_user", summary)
        self.validate_design(invocation, approved, registry.summary, problem)
        summary.update(
            decision=approved.model_dump(mode="json"),
            candidate_set=approved.candidate_set.model_dump(mode="json"),
        )
        return evidence.result("completed", summary)

    def _validate_inference(
        self,
        invocation: NodeInvocation,
        decision: InferenceDecision,
        registry: dict[str, JsonValue],
        problem: ProblemSpec,
    ) -> tuple[dict[str, JsonValue], str]:
        chosen = self.runtime.contract.modelling.inferential_model
        default = {
            "continuous": "ols",
            "binary": "logit",
            "multiclass": "mnlogit",
            "count": "glm_poisson",
        }[problem.target_type]
        if decision.model != (chosen or default):
            raise _RefusalError("inference model conflicts with fixed target/contract choice")
        entries = _object(registry.get("inferential_models"), "inference registry")
        entry = entries.get(decision.model)
        if not isinstance(entry, dict):
            raise _RefusalError("inference model is not registered")
        if problem.target_type not in _strings(entry.get("target_types"), "inference target types"):
            raise _RefusalError("inference model is incompatible with target type")
        tool = entry.get("tool")
        if (
            not isinstance(tool, str)
            or tool not in self.runtime.skill.node(invocation.spec.name).allowed
        ):
            raise EngineError("registry inference fit tool is unavailable")
        predictors = set(decision.predictors)
        if len(predictors) != len(decision.predictors) or predictors - self._eligible(
            invocation.state, problem.target
        ):
            raise _RefusalError("inference predictors are repeated, unknown or ineligible")
        fixed = {column for goal in problem.inference_goals for column in goal.predictors}
        if fixed - predictors:
            raise _RefusalError("inference must include every fixed goal predictor")
        if set(decision.reference_levels) - predictors or any(
            not level.strip() for level in decision.reference_levels.values()
        ):
            raise _RefusalError(
                "reference levels must name selected predictors and nonblank levels"
            )
        intake = shared_result(invocation.state, "intake")
        roles = {
            **_object(_tool_summary(intake, "infer_column_roles").get("roles"), "column roles"),
            **self.runtime.contract.features.roles,
        }
        categorical = {
            column
            for column in predictors
            if roles.get(column) in ("categorical", "ordinal", "text", "datetime")
        }
        if categorical - set(decision.reference_levels):
            raise _RefusalError("categorical inference predictors need explicit reference levels")
        return entry, tool

    async def _inference(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        problem = problem_from_state(invocation.state)
        if not problem.inference_goals:
            return NodeResult(status="skipped", skip_reason="No fixed inference goals")
        registry = _object(
            shared_result(invocation.state, "experiment_design").summary.get("registry"),
            "design registry",
        )

        def check_inference(decision: InferenceDecision) -> None:
            self._validate_inference(invocation, decision, registry, problem)

        decision = await self.runtime.decide(
            invocation,
            InferenceDecision,
            evidence=(registry,),
            question="Choose the fixed compatible inference model, all original goal predictors "
            "and explicit categorical reference levels. Added controls are exploratory. Fit "
            "one canonical unscaled train+valid matrix, never test; request an optional residual "
            "plot only where supported. Do not revise goals or make causal claims.",
            check=_rejecting(check_inference),
        )
        self._validate_inference(invocation, decision, registry, problem)
        summary: dict[str, JsonValue] = {"decision": decision.model_dump(mode="json")}
        approved = await self.runtime.approve(invocation, decision, (registry,))
        if approved is None:
            return evidence.result("awaiting_user", summary)
        entry, tool = self._validate_inference(invocation, approved, registry, problem)
        summary["decision"] = approved.model_dump(mode="json")
        split = shared_result(invocation.state, "data_splitting").summary
        matrix_output = await self._call(
            invocation,
            evidence,
            "assemble_inference_matrix",
            {
                "train": str(_ref(split, "train")),
                "valid": str(_ref(split, "valid")),
                "target": problem.target,
                "predictors": list(approved.predictors),
                "reference_levels": approved.model_dump(mode="json")["reference_levels"],
            },
        )
        matrix, manifest = _artifact(matrix_output, "dataframe"), _artifact(matrix_output, "table")
        params: dict[str, JsonValue] = {"matrix": str(matrix), "target": problem.target}
        if entry.get("family") is not None:
            params["family"] = entry["family"]
        fit_output = await self._call(invocation, evidence, tool, params)
        fit = _artifact(fit_output, "inferential_fit")
        coefficient = await self._call(
            invocation,
            evidence,
            "coefficient_table",
            {
                "fit": str(fit),
                "manifest": str(manifest),
                "goals": [goal.model_dump(mode="json") for goal in problem.inference_goals],
            },
        )
        diagnostics: dict[str, JsonValue] = {}
        diagnostic_calls: tuple[tuple[str, dict[str, JsonValue]], ...] = (
            ("inferential_fit_summary", {"fit": str(fit)}),
            ("compute_vif", {"matrix": str(matrix), "target": problem.target}),
        )
        for name, arguments in diagnostic_calls:
            output = await self._call(invocation, evidence, name, arguments)
            diagnostics[name] = output.summary
        if (
            fit_output.summary.get("converged") is False
            or _object(diagnostics["inferential_fit_summary"], "fit summary").get("converged")
            is False
        ):
            evidence.warnings.append(
                "Inferential fit did not converge; conclusions remain unreliable"
            )
        if approved.model == "ols":
            tests: list[JsonValue] = ["normality", "heteroskedasticity"]
            residuals = await self._call(
                invocation, evidence, "test_residuals", {"fit": str(fit), "tests": tests}
            )
            diagnostics["test_residuals"] = residuals.summary
        if approved.model == "mnlogit":
            evidence.warnings.append(
                "MNLogit influence and residual plots are unsupported; omitted"
            )
        else:
            influence = await self._call(
                invocation, evidence, "compute_influence", {"fit": str(fit)}
            )
            diagnostics["compute_influence"] = influence.summary
            if approved.residual_plot:
                plot = await self._call(invocation, evidence, "plot_residuals", {"fit": str(fit)})
                diagnostics["plot_residuals"] = plot.summary
        summary.update(
            model=approved.model,
            matrix=str(matrix),
            manifest=str(manifest),
            fit=str(fit),
            coefficients=str(_artifact(coefficient, "table")),
            matrix_summary=matrix_output.summary,
            fit_summary=fit_output.summary,
            coefficient_summary=coefficient.summary,
            diagnostics=diagnostics,
        )
        return evidence.result("completed", summary)
