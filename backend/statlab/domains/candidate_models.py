"""Candidate fitting, validation and predictive interpretation adapters."""

from __future__ import annotations

import math

from pydantic import JsonValue, ValidationError
from statlab_contracts.candidate import EstimatorSpec
from statlab_contracts.contract import Tuning
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.vocabulary import MAX_GRID_POINTS, TUNABLE_PARAMS

from statlab.domains.base import NodeResult
from statlab.domains.candidate_pipeline import (
    candidate_result,
    pipeline_from_state,
)
from statlab.domains.decisions import (
    CandidateDecision,
    EvaluationActions,
    EvaluationDecision,
    InterpretationDecision,
    ProblemSpec,
    TrainingDecision,
    ValidationConclusion,
)
from statlab.domains.design_inference import _artifact
from statlab.domains.foundation import (
    _Evidence,
    _object,
    _ref,
    _RefusalError,
    _rejecting,
    _strings,
    problem_from_state,
    resolved_metric,
)
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import CandidateProgress, RunState

CV_CAVEAT = (
    "CV uses a prepared train frame; learned preprocessing/encoding/selection are not "
    "refitted inside folds. This is not leakage-free end-to-end or nested CV."
)
SELECTION_CAVEAT = (
    "Validation is used for candidate/parameter/threshold selection; validation scores "
    "and bootstrap intervals do not remove selection bias."
)
_COEFFICIENT_ESTIMATORS = frozenset(
    {"linear_regression", "ridge", "lasso", "elastic_net", "logistic_regression"}
)


def _validate_training(
    decision: TrainingDecision, *, tuning: Tuning, estimator: EstimatorSpec, retune: bool
) -> None:
    if "method" in tuning.model_fields_set and tuning.method != "random":
        required = "direct" if tuning.method == "none" else "grid"
        if decision.method != required:
            raise _RefusalError(
                f"training method {decision.method!r} conflicts with explicit tuning method "
                f"{tuning.method!r}; use {required!r}"
            )
    tunable = TUNABLE_PARAMS.get(estimator.name)
    if tunable is not None:
        unknown = sorted((set(decision.params) | set(decision.param_grid or {})) - tunable)
        if unknown:
            raise _RefusalError(
                f"parameters {unknown!r} are not tunable for {estimator.name}; "
                f"allowed: {sorted(tunable)!r}"
            )
    if (
        decision.params
        and not retune
        and decision.method == "direct"
        and decision.params != estimator.params
    ):
        raise _RefusalError("initial direct parameters differ from approved candidate")
    if decision.method == "grid":
        if decision.param_grid is None and estimator.params:
            raise _RefusalError("approved parameters require an explicit grid")
    elif decision.param_grid is not None:
        raise _RefusalError("direct fitting cannot accept a parameter grid")


def _validate_evaluation(problem: ProblemSpec, decision: EvaluationActions) -> None:
    if problem.task == "regression" and (decision.curves or decision.threshold_objective):
        raise _RefusalError("classification diagnostics require classification")
    if problem.target_type != "binary" and (
        decision.threshold_objective or set(decision.curves) & {"roc", "pr", "calibration"}
    ):
        raise _RefusalError(
            "threshold, roc, pr and calibration curves require binary classification; "
            "multiclass allows only the confusion curve"
        )


def _validate_interpretation(decision: InterpretationDecision) -> None:
    if decision.skip_reason is not None and (
        decision.coefficients or decision.partial_dependence or decision.error_by
    ):
        raise _RefusalError(
            "skip_reason skips the whole interpretation; leave it empty to run the requested "
            "diagnostics (keep coefficients false to leave out coefficients only)"
        )


def evaluation_decision(branch: CandidateProgress) -> EvaluationDecision:
    summary = candidate_result(branch, "model_evaluation").summary
    try:
        return EvaluationDecision.model_validate(
            _object(summary.get("decision"), "evaluation decision")
        )
    except ValidationError as exc:
        raise EngineError("malformed durable evaluation decision", cause=exc) from exc


async def candidate_retune_decider(state: RunState, branch: CandidateProgress) -> bool:
    if branch not in state.branches:
        raise EngineError("retune branch is not checkpointed")
    return evaluation_decision(branch).retune


class CandidateModelExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        self.runtime.context(invocation)
        if invocation.candidate is None:
            raise EngineError("model executor requires a candidate")
        methods = {
            "model_training": self._training,
            "model_evaluation": self._evaluation,
            "model_interpretation": self._interpretation,
        }
        method = methods.get(invocation.spec.name)
        if method is None:
            raise EngineError("model executor cannot execute this node")
        evidence = _Evidence()
        try:
            return await method(invocation, invocation.candidate, evidence)
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
        parameters: dict[str, JsonValue],
    ) -> ToolSuccess:
        output = await self.runtime.call(invocation, tool, parameters)
        evidence.add(tool, output)
        return output

    async def _render_curve(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        curve: ToolSuccess,
        title: str,
    ) -> dict[str, JsonValue]:
        ref = _artifact(curve, "curve")
        plot = await self._call(
            invocation, evidence, "plot_curve", {"curve": str(ref), "title": title}
        )
        return {"curve": str(ref), "plot": str(_artifact(plot, "plot")), "summary": curve.summary}

    async def _finish(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        decision: CandidateDecision,
        summary: dict[str, JsonValue],
    ) -> NodeResult:
        summary["decision"] = decision.model_dump(mode="json")
        return evidence.result("completed", summary)

    async def _training(
        self,
        invocation: NodeInvocation,
        branch: CandidateProgress,
        evidence: _Evidence,
    ) -> NodeResult:
        pipeline = pipeline_from_state(invocation.state, branch, require="feature_engineering")
        problem = problem_from_state(invocation.state)
        previous: tuple[JsonValue, ...] = ()
        if branch.retune_count:
            previous = (candidate_result(branch, "model_evaluation").summary,)
        tunable = TUNABLE_PARAMS.get(branch.spec.estimator.name)
        tuning = self.runtime.contract.modelling.tuning
        estimator = branch.spec.estimator
        question = (
            "Fit the approved estimator on original prepared train only. Choose direct "
            "fit or bounded grid. Keep estimator/class weight/calibration/methodology fixed. "
            "Use prior validation and retune reason for parameter retunes, never for fitting. "
            "Prepared-frame CV has leakage caveats; respect explicit tuning method. "
            "For the initial direct fit leave params empty; "
            "the approved candidate parameters are used."
        )
        if tunable is not None:
            question += (
                f" Tunable parameters for {branch.spec.estimator.name}: "
                f"{', '.join(sorted(tunable))}. Only these keys may appear in params or "
                f"param_grid (a param_grid has at most {MAX_GRID_POINTS} combinations in total); "
                "class_weight, calibrate, random_state and n_jobs are fixed "
                "elsewhere and never appear there."
            )
        if "method" in tuning.model_fields_set and tuning.method != "random":
            required = "direct" if tuning.method == "none" else "grid"
            question += (
                f" The contract fixes tuning method {tuning.method!r}: use method {required!r}"
            )
            if required == "grid":
                question += " with a param_grid over tunable keys and empty params"
            question += "."
        decision = await self.runtime.decide(
            invocation,
            TrainingDecision,
            evidence=(pipeline.model_dump(mode="json"), *previous),
            question=question,
            check=_rejecting(
                lambda d: _validate_training(
                    d, tuning=tuning, estimator=estimator, retune=bool(branch.retune_count)
                )
            ),
        )
        if tuning.method == "random":
            raise _RefusalError("random search has no supported tool")
        _validate_training(
            decision, tuning=tuning, estimator=estimator, retune=bool(branch.retune_count)
        )
        params = _object(estimator.model_dump(mode="json")["params"], "approved parameters")
        if decision.params:
            params = _object(decision.model_dump(mode="json")["params"], "training parameters")
        arguments: dict[str, JsonValue] = {
            "train": str(pipeline.train),
            "target": problem.target,
            "name": estimator.name,
            "class_weight": estimator.class_weight,
            "seed": self.runtime.contract.constraints.random_seed,
        }
        tune_summary: dict[str, JsonValue] | None = None
        if decision.method == "grid":
            grid = decision.model_dump(mode="json")["param_grid"]
            if isinstance(grid, dict):
                grid = {**{name: [value] for name, value in params.items()}, **grid}
            tuned = await self._call(
                invocation,
                evidence,
                "tune_estimator",
                {
                    **arguments,
                    "metric": problem.primary_metric,
                    "param_grid": grid,
                },
            )
            tune_summary = tuned.summary
            params = _object(tuned.summary.get("best_params"), "tuned parameters")
        fitted = await self._call(
            invocation,
            evidence,
            "train_estimator",
            {
                **arguments,
                "params": params,
                "calibrate": estimator.calibrate,
            },
        )
        model = _artifact(fitted, "model")
        effective = await self._call(
            invocation, evidence, "get_model_params", {"model": str(model)}
        )
        return await self._finish(
            invocation,
            evidence,
            decision,
            {
                "model": str(model),
                "train": str(pipeline.train),
                "valid": str(pipeline.valid),
                "effective_params": _object(
                    effective.summary.get("params"), "effective parameters"
                ),
                "selected_params": params,
                "model_summary": effective.summary,
                "fit_summary": fitted.summary,
                "tuning": tune_summary,
                "cv_caveat": CV_CAVEAT
                if decision.method == "grid" or estimator.calibrate
                else None,
                "wave": branch.wave,
                "retune_count": branch.retune_count,
                "previous_validation": list(previous),
                "methodology": branch.spec.model_dump(mode="json"),
            },
        )

    async def _evaluation(
        self,
        invocation: NodeInvocation,
        branch: CandidateProgress,
        evidence: _Evidence,
    ) -> NodeResult:
        pipeline = pipeline_from_state(invocation.state, branch, require="feature_engineering")
        trained = candidate_result(branch, "model_training").summary
        model = _ref(trained, "model")
        problem = problem_from_state(invocation.state)
        decision = await self.runtime.decide(
            invocation,
            EvaluationActions,
            evidence=(trained,),
            question="Score validation only using the resolved primary metric. Choose compatible "
            "curves and an optional binary threshold objective; train-only CV/learning curves are "
            "prepared-frame diagnostics. Choose actions now; conclude retuning after scoring. "
            "Selection/bootstrap estimates remain biased. Do not invent metric ranges "
            "from success_threshold. threshold_objective and roc/pr/calibration curves are "
            "binary-only; multiclass takes only the confusion curve; regression takes no "
            "curves or threshold.",
            check=_rejecting(lambda d: _validate_evaluation(problem, d)),
        )
        _validate_evaluation(problem, decision)
        predicted = await self._call(
            invocation, evidence, "predict", {"model": str(model), "frame": str(pipeline.valid)}
        )
        predictions = _artifact(predicted, "predictions")
        probabilities = None
        threshold: JsonValue = None
        curves: dict[str, JsonValue] = {}
        curve_outputs: dict[str, ToolSuccess] = {}
        if problem.task == "classification":
            probability_output = await self._call(
                invocation,
                evidence,
                "predict_proba",
                {"model": str(model), "frame": str(pipeline.valid)},
            )
            probabilities = _artifact(probability_output, "predictions")
            arguments: dict[str, JsonValue] = {
                "probabilities": str(probabilities),
                "truth": str(pipeline.valid),
                "target": problem.target,
            }
            if decision.threshold_objective:
                curve = await self._call(invocation, evidence, "compute_roc_curve", arguments)
                chosen = await self._call(
                    invocation,
                    evidence,
                    "select_threshold",
                    {
                        "curve": str(_artifact(curve, "curve")),
                        "objective": decision.threshold_objective,
                        "target_recall": decision.target_recall,
                        "cost_fn_to_fp": decision.cost_fn_to_fp,
                    },
                )
                threshold = chosen.summary.get("threshold")
                if (
                    isinstance(threshold, bool)
                    or not isinstance(threshold, (int, float))
                    or not 0 < threshold < 1
                ):
                    raise _RefusalError("selected binary threshold is unavailable or outside (0,1)")
                curve_outputs["roc"] = curve
                curves["roc"] = {"curve": str(_artifact(curve, "curve")), "summary": curve.summary}
            tools = {
                "confusion": "compute_confusion_matrix",
                "roc": "compute_roc_curve",
                "pr": "compute_pr_curve",
                "calibration": "compute_calibration_curve",
            }
            for kind in dict.fromkeys(decision.curves):
                requested_curve = curve_outputs.get(kind)
                if requested_curve is None:
                    extra = {"threshold": threshold} if kind == "confusion" else {}
                    requested_curve = await self._call(
                        invocation, evidence, tools[kind], {**arguments, **extra}
                    )
                if kind == "confusion":
                    curves[kind] = {
                        "table": str(_artifact(requested_curve, "table")),
                        "summary": requested_curve.summary,
                    }
                else:
                    curves[kind] = await self._render_curve(
                        invocation, evidence, requested_curve, f"Validation {kind} curve"
                    )
            scored = await self._call(
                invocation,
                evidence,
                "score_classification",
                {
                    **arguments,
                    "seed": self.runtime.contract.constraints.random_seed,
                    "threshold": threshold,
                },
            )
        else:
            scored = await self._call(
                invocation,
                evidence,
                "score_regression",
                {
                    "predictions": str(predictions),
                    "truth": str(pipeline.valid),
                    "target": problem.target,
                    "seed": self.runtime.contract.constraints.random_seed,
                },
            )
        metrics = _object(scored.summary.get("metrics"), "validation metrics")
        metric = resolved_metric(problem)
        entry = _object(metrics.get(metric), "primary metric")
        value = entry.get("value")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise _RefusalError("primary validation metric is unavailable or nonfinite")
        invalid_range = (
            (
                problem.primary_metric not in ("r2", "rmse", "mae", "medae", "mape", "log_loss")
                and not 0 <= value <= 1
            )
            or (
                problem.primary_metric in ("rmse", "mae", "medae", "mape", "log_loss") and value < 0
            )
            or (problem.primary_metric == "r2" and value > 1)
        )
        if invalid_range:
            evidence.warnings.append("Primary metric is outside its mathematical range")
        diagnostics: dict[str, JsonValue] = {}
        estimator = branch.spec.estimator
        for enabled, tool in (
            (decision.cross_validation, "cross_validate_estimator"),
            (decision.learning_curve, "compute_learning_curve"),
        ):
            if enabled:
                if estimator.calibrate:
                    raise _RefusalError(
                        "train diagnostic tool cannot reproduce calibrated estimator"
                    )
                diagnostic = await self._call(
                    invocation,
                    evidence,
                    tool,
                    {
                        "train": str(pipeline.train),
                        "target": problem.target,
                        "name": estimator.name,
                        "metric": problem.primary_metric,
                        "seed": self.runtime.contract.constraints.random_seed,
                        "params": _object(trained.get("selected_params"), "selected parameters"),
                        "class_weight": estimator.class_weight,
                    },
                )
                diagnostics[tool] = (
                    await self._render_curve(
                        invocation, evidence, diagnostic, "Train learning curve"
                    )
                    if tool == "compute_learning_curve"
                    else diagnostic.summary
                )
        summary: dict[str, JsonValue] = {
            "model": str(model),
            "predictions": str(predictions),
            "probabilities": str(probabilities) if probabilities else None,
            "threshold": threshold,
            "score": str(_artifact(scored, "table")),
            "metrics": metrics,
            "primary_metric": problem.primary_metric,
            "greater_is_better": problem.primary_metric
            not in ("rmse", "mae", "medae", "mape", "log_loss", "brier"),
            "selection_bias": SELECTION_CAVEAT,
            "curves": curves,
            "diagnostics": diagnostics,
            "cv_caveat": CV_CAVEAT if diagnostics else None,
            "wave": branch.wave,
            "retune_count": branch.retune_count,
            "actions": decision.model_dump(mode="json"),
        }
        conclusion = await self.runtime.decide(
            invocation,
            ValidationConclusion,
            evidence=(
                summary,
                {
                    "warnings": list(evidence.warnings),
                    "limits": self.runtime.contract.constraints.model_dump(mode="json"),
                    "budget": invocation.state.budget.model_dump(mode="json")
                    if invocation.state.budget
                    else None,
                },
                *evidence.tools,
            ),
            question="Conclude using the current validation metrics/CIs, selected threshold, "
            "model and performed diagnostics. Recommend a bounded parameter retune only with "
            "an observed validation reason and remaining loop/fit budget. Actions and approved "
            "methodology are fixed; do not request new diagnostics or transformations. "
            "Acknowledge warnings and selection bias; never use test evidence.",
        )
        final = EvaluationDecision(
            rationale=conclusion.rationale,
            material_exceptions=(*decision.material_exceptions, *conclusion.material_exceptions),
            curves=decision.curves,
            threshold_objective=decision.threshold_objective,
            target_recall=decision.target_recall,
            cost_fn_to_fp=decision.cost_fn_to_fp,
            cross_validation=decision.cross_validation,
            learning_curve=decision.learning_curve,
            retune=conclusion.retune,
            retune_reason=conclusion.retune_reason,
        )
        output = await self._finish(invocation, evidence, final, summary)
        return output.model_copy(update={"metrics": {metric: float(value)}})

    async def _interpretation(
        self,
        invocation: NodeInvocation,
        branch: CandidateProgress,
        evidence: _Evidence,
    ) -> NodeResult:
        pipeline = pipeline_from_state(invocation.state, branch, require="feature_engineering")
        trained = candidate_result(branch, "model_training").summary
        evaluated = candidate_result(branch, "model_evaluation").summary
        model = _ref(trained, "model")
        if _ref(evaluated, "model") != model:
            raise EngineError("latest evaluation/model disagree")
        problem = problem_from_state(invocation.state)
        decision = await self.runtime.decide(
            invocation,
            InterpretationDecision,
            evidence=(trained, evaluated),
            question="Use validation permutation importance and error segmentation for predictive "
            "diagnostics. Coefficients require supported uncalibrated linear estimators. "
            "Optional PDP uses declared model features; no inferential refits or test diagnostics. "
            "Skip only with an explicit reason; declare material exceptions."
            " Partial dependence columns and error segments must be entries of "
            "model_summary.feature_names."
            " skip_reason skips the whole step; to leave out coefficients only, keep "
            "coefficients false.",
            check=_rejecting(_validate_interpretation),
        )
        _validate_interpretation(decision)
        if decision.skip_reason is not None:
            return NodeResult(
                status="skipped",
                skip_reason=decision.skip_reason,
                summary={"decision": decision.model_dump(mode="json")},
            )
        if problem.target_type == "multiclass" and problem.primary_metric == "brier":
            raise _RefusalError("multiclass Brier permutation scorer is unsupported")
        features = set(
            _strings(
                _object(trained.get("model_summary"), "model summary").get("feature_names"),
                "model features",
            )
        )
        estimator = branch.spec.estimator
        linear = estimator.name in _COEFFICIENT_ESTIMATORS and not estimator.calibrate
        usable = decision.model_copy(
            update={
                "coefficients": decision.coefficients and linear,
                "partial_dependence": tuple(
                    c for c in decision.partial_dependence if c in features
                ),
                "error_by": tuple(c for c in decision.error_by if c in features),
            }
        )
        dropped = (
            int(decision.coefficients and not linear)
            + len(decision.partial_dependence)
            - len(usable.partial_dependence)
            + len(decision.error_by)
            - len(usable.error_by)
        )
        if dropped:
            # Unusable choices are dropped, not fatal (chunk 84; chunk 80 pattern).
            evidence.warnings.append(
                f"ignored {dropped} interpretation choice(s) the model cannot use"
            )
        decision = usable
        importance = await self._call(
            invocation,
            evidence,
            "permutation_importance",
            {
                "model": str(model),
                "frame": str(pipeline.valid),
                "metric": problem.primary_metric,
                "seed": self.runtime.contract.constraints.random_seed,
            },
        )
        diagnostics: dict[str, JsonValue] = {"permutation_importance": importance.summary}
        if decision.coefficients:
            coefficients = await self._call(
                invocation,
                evidence,
                "coefficient_importance",
                {"model": str(model), "train": str(pipeline.train)},
            )
            diagnostics["coefficient_importance"] = coefficients.summary
        for column in decision.partial_dependence:
            parameters: dict[str, JsonValue] = {
                "model": str(model),
                "frame": str(pipeline.valid),
                "column": column,
            }
            for step in reversed(pipeline.replay):
                if step.tool == "apply_scaler":
                    parameters["scaler"] = step.parameters["scaler"]
                    break
            pdp = await self._call(invocation, evidence, "partial_dependence", parameters)
            diagnostics[f"partial_dependence:{column}"] = await self._render_curve(
                invocation, evidence, pdp, "Validation partial dependence"
            )
        if decision.error_by:
            predictions = _ref(
                evaluated, "probabilities" if problem.task == "classification" else "predictions"
            )
            errors = await self._call(
                invocation,
                evidence,
                "analyze_errors",
                {
                    "predictions": str(predictions),
                    "truth": str(pipeline.valid),
                    "target": problem.target,
                    "by": list(decision.error_by),
                    "threshold": evaluated.get("threshold"),
                },
            )
            diagnostics["analyze_errors"] = errors.summary
        return await self._finish(
            invocation, evidence, decision, {"model": str(model), "diagnostics": diagnostics}
        )
