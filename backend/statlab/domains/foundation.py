"""Shared intake, pre-EDA scoping, descriptive profiling and verified splitting."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, JsonValue, ValidationError
from statlab_contracts.artifacts import ArtifactRef, parse_artifact_ref
from statlab_contracts.contract import DataContract, Objective, SplitRules, TargetSpec
from statlab_contracts.envelope import Failure, ToolSuccess
from statlab_contracts.vocabulary import TASK_FOR_TARGET_TYPE, Metric

from statlab.domains.base import NodeResult, NodeStatus
from statlab.domains.decisions import (
    IntakeDecision,
    MetricRule,
    ProblemDecision,
    ProblemSpec,
    ProfileDecision,
    ProfileFindings,
    QualityDecision,
    SplitDecision,
)
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError, StatlabError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import RunState
from statlab.llm.client import DecisionRejectedError
from statlab.persistence.repository import require_run, set_test_fingerprint


class _RefusalError(StatlabError):
    """Reachable data/decision refusal, distinct from malformed engine state."""


def _rejecting[T](validate: Callable[[T], None]) -> Callable[[T], None]:
    def check(decision: T) -> None:
        try:
            validate(decision)
        except _RefusalError as exc:
            raise DecisionRejectedError(str(exc)) from exc

    return check


def shared_result(state: RunState, node: str) -> NodeResult:
    for boundary in reversed(state.shared_results):
        if boundary.node_name == node and boundary.result.status == "completed":
            return boundary.result
    raise EngineError(f"missing successful shared boundary: {node}")


def _completed(state: RunState, node: str) -> NodeResult | None:
    for boundary in reversed(state.shared_results):
        if boundary.node_name == node and boundary.result.status == "completed":
            return boundary.result
    return None


def _object(value: JsonValue | None, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise EngineError(f"missing or malformed {label}")
    return value


def _strings(value: JsonValue | None, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EngineError(f"missing or malformed {label}")
    return tuple(item for item in value if isinstance(item, str))


def _ref(summary: dict[str, JsonValue], key: str) -> ArtifactRef:
    value = summary.get(key)
    if not isinstance(value, str):
        raise EngineError(f"missing or malformed {key} reference")
    try:
        return parse_artifact_ref(value)
    except ValueError as exc:
        raise EngineError(f"malformed {key} reference", cause=exc) from exc


def resolved_metric(problem: ProblemSpec) -> Metric:
    if problem.primary_metric is None:
        raise EngineError("automatic metric has not been resolved")
    return problem.primary_metric


def problem_from_state(state: RunState) -> ProblemSpec:
    """The sole effective-metric reader; goals always come from pre-EDA scoping."""
    summary = shared_result(state, "problem_definition").summary
    try:
        problem = ProblemSpec.model_validate(_object(summary.get("problem"), "problem spec"))
        if problem.primary_metric is None:
            profile = shared_result(state, "data_profiling").summary
            problem = ProblemSpec.model_validate(
                {**problem.model_dump(mode="json"), "primary_metric": profile.get("primary_metric")}
            )
            resolved_metric(problem)
        return problem
    except ValidationError as exc:
        raise EngineError("malformed shared problem output", cause=exc) from exc


def _tool_summary(result: NodeResult, tool: str) -> dict[str, JsonValue]:
    evidence = result.summary.get("tools")
    if isinstance(evidence, list):
        for entry in reversed(evidence):
            if isinstance(entry, dict) and entry.get("tool") == tool:
                output = _object(entry.get("result"), f"{tool} output")
                return _object(output.get("summary"), f"{tool} summary")
    raise EngineError(f"missing tool evidence: {tool}")


def problem_spec(
    contract: DataContract, intake: NodeResult, decision: ProblemDecision
) -> ProblemSpec:
    """Apply contract precedence, then reuse the contract's semantic validators."""
    columns = _strings(_tool_summary(intake, "load_csv").get("columns"), "intake columns")
    roles = _object(_tool_summary(intake, "infer_column_roles").get("roles"), "intake roles")
    target_type = contract.target.type
    if target_type is None:
        # Boolean evidence is sufficient; numeric alone cannot distinguish counts from
        # continuous outcomes, nor categorical roles binary from multiclass cardinality.
        if roles.get(contract.target.column) != "boolean":
            raise _RefusalError("target type remains ambiguous in contract and intake evidence")
        target_type = "binary"
    if decision.target_type is not None and decision.target_type != target_type:
        raise _RefusalError("proposed target type conflicts with contract or intake evidence")
    task = TASK_FOR_TARGET_TYPE[target_type]
    if (decision.task is not None and decision.task != task) or (
        contract.objective.task is not None and contract.objective.task != task
    ):
        raise _RefusalError("proposed task conflicts with the target")
    objective = contract.objective
    metric = objective.primary_metric
    rule: MetricRule
    override = None
    if metric is not None:
        rule = "explicit"
    elif decision.primary_metric is not None:
        if not decision.metric_override_rationale or not decision.metric_override_rationale.strip():
            raise _RefusalError("agent metric override needs a nonblank rationale")
        rule, metric, override = (
            "override",
            decision.primary_metric,
            decision.metric_override_rationale,
        )
    elif task == "classification":
        rule = "classification_auto"
    else:
        rule, metric = "regression_auto", "rmse"
    secondary = (
        objective.secondary_metrics
        if "secondary_metrics" in objective.model_fields_set
        else decision.secondary_metrics
    )
    goals = (
        objective.inference_goals
        if "inference_goals" in objective.model_fields_set
        else decision.inference_goals
    )
    threshold = (
        objective.success_threshold
        if objective.success_threshold is not None
        else decision.success_threshold
    )
    for goal in goals:
        if set(goal.predictors) - set(columns):
            raise _RefusalError("inference goal names an unknown predictor")
    accepted_objective = Objective(
        task=task,
        primary_metric=metric,
        secondary_metrics=secondary,
        success_threshold=threshold,
        inference_goals=goals,
    )
    # Reuse X1-X14 instead of maintaining a second contract validator.
    DataContract(
        target=TargetSpec(
            column=contract.target.column,
            type=target_type,
            positive_class=contract.target.positive_class,
            class_order=contract.target.class_order,
        ),
        dataset=contract.dataset,
        features=contract.features,
        objective=accepted_objective,
        split=contract.split,
        preprocessing=contract.preprocessing,
        modelling=contract.modelling,
        constraints=contract.constraints,
        notes=contract.notes,
    )
    return ProblemSpec(
        target=contract.target.column,
        target_type=target_type,
        task=task,
        primary_metric=metric,
        metric_rule=rule,
        metric_override_rationale=override,
        secondary_metrics=secondary,
        success_threshold=threshold,
        inference_goals=goals,
        constraints=decision.constraints,
        risks=decision.risks,
        rationale=decision.rationale,
    )


def resolve_metric(problem: ProblemSpec, target: dict[str, JsonValue]) -> Metric:
    if problem.primary_metric is not None:
        return problem.primary_metric
    if target.get("kind") != "class":
        raise _RefusalError("classification target summary is unavailable or contradictory")
    classes = target.get("classes")
    minority = target.get("minority_share")
    if not isinstance(classes, dict) or len(classes) < 2:
        raise _RefusalError("classification needs at least two observed classes")
    if problem.target_type == "binary" and len(classes) != 2:
        raise _RefusalError("binary target summary has a contradictory class count")
    shares: list[float] = []
    for entry in classes.values():
        if not isinstance(entry, dict):
            raise _RefusalError("class-share evidence is malformed")
        share = entry.get("share")
        if isinstance(share, bool) or not isinstance(share, (int, float)) or not 0 < share <= 1:
            raise _RefusalError("class-share evidence is unavailable")
        shares.append(float(share))
    if (
        isinstance(minority, bool)
        or not isinstance(minority, (int, float))
        or abs(sum(shares) - 1) > 1e-8
        or abs(min(shares) - minority) > 1e-8
    ):
        raise _RefusalError("class-share evidence is contradictory")
    return "pr_auc" if minority < 0.1 else "roc_auc"


@dataclass(slots=True)
class _Evidence:
    tools: list[JsonValue] = field(default_factory=list)
    refs: list[ArtifactRef] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, tool: str, output: ToolSuccess) -> None:
        self.tools.append({"tool": tool, "result": output.model_dump(mode="json")})
        self.refs.extend(artifact.ref for artifact in output.artifacts)
        self.warnings.extend(output.warnings)

    def result(
        self, status: NodeStatus, summary: dict[str, JsonValue], *, error: Failure | None = None
    ) -> NodeResult:
        return NodeResult(
            status=status,
            summary={**summary, "tools": self.tools},
            artifacts=tuple(dict.fromkeys(self.refs)) if error is None else (),
            warnings=tuple(self.warnings),
            error=error,
        )


def _validate_quality(decision: QualityDecision, fixable: dict[str, JsonValue]) -> None:
    reported = _object(fixable.get("mark_missing"), "quality fixable mark_missing")
    for column, values in decision.mark_missing.items():
        options = reported.get(column)
        if not isinstance(options, list):
            raise _RefusalError(
                f"mark_missing column {column!r} has no reported values; "
                f"allowed columns: {sorted(reported)!r}"
            )
        bad = sorted(set(values) - {item for item in options if isinstance(item, str)})
        if bad:
            raise _RefusalError(
                f"mark_missing[{column!r}] values {bad!r} were not reported; allowed: {options!r}"
            )
    allowed_merge = _strings(fixable.get("merge_variants"), "quality fixable merge_variants")
    bad = sorted(set(decision.merge_variants) - set(allowed_merge))
    if bad:
        raise _RefusalError(
            f"merge_variants {bad!r} were not reported; allowed: {list(allowed_merge)!r}"
        )


def _frame(output: ToolSuccess) -> ArtifactRef:
    frames = tuple(artifact.ref for artifact in output.artifacts if artifact.kind == "dataframe")
    if len(frames) != 1:
        raise EngineError("tool did not return exactly one shared dataframe")
    return frames[0]


class FoundationExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        methods = {
            "intake": self._intake,
            "problem_definition": self._problem,
            "data_profiling": self._profile,
            "data_quality": self._quality,
            "data_splitting": self._split,
        }
        method = methods.get(invocation.spec.name)
        if method is None:
            raise EngineError("foundation executor cannot execute deferred node")
        self.runtime.context(invocation)
        evidence = _Evidence()
        try:
            return await method(invocation, evidence)
        except _RefusalError as exc:
            return evidence.result(
                "failed", {}, error=Failure(error_class="data", message=str(exc))
            )
        except ValidationError as exc:
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

    async def _approve[T: BaseModel](
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        decision: T,
    ) -> T | None:
        return await self.runtime.approve(invocation, decision, tuple(evidence.tools))

    async def _intake(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        contract = self.runtime.contract
        loaded = await self._call(
            invocation, evidence, "load_csv", contract.dataset.model_dump(mode="json")
        )
        raw = _frame(loaded)
        await self._call(invocation, evidence, "infer_column_roles", {"frame": str(raw)})
        validated = await self._call(invocation, evidence, "validate_contract", {"frame": str(raw)})
        ids = await self._call(invocation, evidence, "detect_id_columns", {"frame": str(raw)})
        leaks = await self._call(
            invocation,
            evidence,
            "detect_leaky_columns",
            {
                "frame": str(raw),
                "target": contract.target.column,
            },
        )
        if ids.summary.get("candidates"):
            evidence.warnings.append("ID candidates require review; no columns were removed")
        if leaks.summary.get("suspects"):
            evidence.warnings.append("suspected leakage requires review; flags are not proof")
        decision = await self.runtime.decide(
            invocation,
            IntakeDecision,
            evidence=tuple(evidence.tools),
            question="Read actual contract/data findings; propose review fixes. "
            "Never edit columns. Escalate only when the contract cannot run on this data "
            "(missing or ambiguous target, contract/data mismatch). Missing values are "
            "handled later: a mean/median default falls back to most_frequent for "
            "non-numeric columns, and inference reference levels default to the most "
            "frequent level. Record such points as findings, not escalation.",
        )
        summary: dict[str, JsonValue] = {
            "raw": str(raw),
            "decision": decision.model_dump(mode="json"),
        }
        if validated.summary.get("valid") is not True:
            return evidence.result(
                "failed",
                summary,
                error=Failure(
                    error_class="data",
                    message="contract does not match the uploaded data",
                ),
            )
        approved = await self._approve(invocation, evidence, decision)
        if approved is None:
            return evidence.result("awaiting_user", summary)
        summary["decision"] = approved.model_dump(mode="json")
        if approved.disposition == "escalate":
            return evidence.result(
                "failed",
                summary,
                error=Failure(
                    error_class="data",
                    message="intake requires data/contract review",
                ),
            )
        return evidence.result("completed", summary)

    async def _problem(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        intake = shared_result(invocation.state, "intake")
        decision = await self.runtime.decide(
            invocation,
            ProblemDecision,
            evidence=(intake.summary,),
            question="Fix task and inference goals before EDA. "
            "Leave automatic class metric unresolved; "
            "an override needs its own rationale. Contract values are authoritative.",
        )
        problem = problem_spec(self.runtime.contract, intake, decision)
        summary: dict[str, JsonValue] = {
            "decision": decision.model_dump(mode="json"),
            "problem": problem.model_dump(mode="json"),
        }
        approved = await self.runtime.approve(invocation, decision, (intake.summary,))
        if approved is None:
            return evidence.result("awaiting_user", summary)
        problem = problem_spec(self.runtime.contract, intake, approved)
        summary.update(
            decision=approved.model_dump(mode="json"), problem=problem.model_dump(mode="json")
        )
        return evidence.result("completed", summary)

    def _problem_before_profile(self, state: RunState) -> ProblemSpec:
        try:
            return ProblemSpec.model_validate(
                _object(
                    shared_result(state, "problem_definition").summary.get("problem"),
                    "problem spec",
                )
            )
        except ValidationError as exc:
            raise EngineError("malformed problem boundary", cause=exc) from exc

    def _profile_columns(
        self, schema: dict[str, JsonValue]
    ) -> tuple[dict[str, JsonValue], set[str]]:
        columns = _object(schema.get("columns"), "profile columns")
        numeric = {
            name
            for name, entry in columns.items()
            if isinstance(entry, dict)
            and isinstance(entry.get("dtype"), str)
            and str(entry["dtype"]).lower().startswith(("int", "uint", "float", "decimal"))
        }
        for column, role in self.runtime.contract.features.roles.items():
            if column in columns:
                if role == "numeric":
                    numeric.add(column)
                else:
                    numeric.discard(column)
        return columns, numeric

    def _validate_profile(
        self,
        invocation: NodeInvocation,
        decision: ProfileDecision,
        schema: dict[str, JsonValue],
        target: str,
    ) -> None:
        columns, numeric = self._profile_columns(schema)
        selections = (
            (decision.numeric, "summarize_numeric", numeric),
            (decision.categorical, "summarize_categorical", set(columns) - numeric),
            (decision.correlations, "compute_correlations", numeric),
            (decision.outliers, "detect_outliers", numeric),
        )
        allowed = self.runtime.skill.node(invocation.spec.name).allowed
        for selected, tool, compatible in selections:
            bad = sorted({entry for entry in selected if entry not in compatible})
            if bad:
                raise _RefusalError(
                    f"{tool} entries must be exact column names; rejected {bad!r} "
                    f"(unknown or incompatible); allowed: {sorted(compatible)!r}"
                )
            if selected and tool not in allowed:
                raise _RefusalError(f"{tool} is unavailable in this node skill")
        for plot in decision.plots:
            tool = f"plot_{plot.kind}"
            if plot.column not in columns or tool not in allowed:
                raise _RefusalError("plot names an unknown column or unavailable tool")
            if plot.kind == "target_relation" and plot.column == target:
                raise _RefusalError("target cannot be its own relation predictor")

    async def _profile(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        raw = _ref(shared_result(invocation.state, "intake").summary, "raw")
        problem = self._problem_before_profile(invocation.state)
        schema = await self._call(invocation, evidence, "profile_schema", {"frame": str(raw)})
        await self._call(invocation, evidence, "report_missingness", {"frame": str(raw)})
        target = await self._call(
            invocation,
            evidence,
            "summarize_target",
            {
                "frame": str(raw),
                "target": problem.target,
            },
        )
        metric = resolve_metric(problem, target.summary)
        ProblemSpec.model_validate({**problem.model_dump(mode="json"), "primary_metric": metric})
        columns, numeric = self._profile_columns(schema.summary)
        decision = await self.runtime.decide(
            invocation,
            ProfileDecision,
            evidence=tuple(evidence.tools),
            question="Choose relevant typed columns and at most four descriptive plots. "
            "Profile uses the whole input; goals remain fixed before EDA. "
            "summarize_categorical takes only non-numeric columns; numeric, correlations and "
            "outliers take only numeric columns (dtype or contract role). "
            "Every list entry is one exact column name from the schema, never a sentence, "
            "pair or comment."
            f" Numeric columns: {', '.join(sorted(numeric)) or 'none'}."
            f" Non-numeric columns: {', '.join(sorted(set(columns) - numeric)) or 'none'}.",
            check=_rejecting(
                lambda d: self._validate_profile(invocation, d, schema.summary, problem.target)
            ),
        )
        self._validate_profile(invocation, decision, schema.summary, problem.target)
        summary: dict[str, JsonValue] = {
            "frame": str(raw),
            "decision": decision.model_dump(mode="json"),
            "primary_metric": metric,
            "whole_input_eda": True,
        }
        approved = await self._approve(invocation, evidence, decision)
        if approved is None:
            return evidence.result("awaiting_user", summary)
        self._validate_profile(invocation, approved, schema.summary, problem.target)
        summary["decision"] = approved.model_dump(mode="json")
        frame = raw
        if self.runtime.contract.features.roles:
            cast = await self._call(
                invocation,
                evidence,
                "cast_dtypes",
                {
                    "frame": str(frame),
                    "target": problem.target,
                    "roles": self.runtime.contract.features.model_dump(mode="json")["roles"],
                },
            )
            frame = _frame(cast)
        for selected, tool in (
            (approved.numeric, "summarize_numeric"),
            (approved.categorical, "summarize_categorical"),
            (approved.correlations, "compute_correlations"),
            (approved.outliers, "detect_outliers"),
        ):
            if selected:
                await self._call(
                    invocation, evidence, tool, {"frame": str(frame), "columns": list(selected)}
                )
        for plot in approved.plots:
            params: dict[str, JsonValue] = {"frame": str(frame), "column": plot.column}
            if plot.kind == "target_relation":
                params["target"] = problem.target
            await self._call(invocation, evidence, f"plot_{plot.kind}", params)
        findings = await self.runtime.decide(
            invocation,
            ProfileFindings,
            evidence=tuple(evidence.tools),
            question="Explain only observed results; label whole-input EDA and new hypotheses "
            "exploratory. Do not revise the fixed goals.",
        )
        summary.update(frame=str(frame), findings=findings.model_dump(mode="json"))
        evidence.refs.append(frame)
        return evidence.result("completed", summary)

    async def _quality(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        problem = problem_from_state(invocation.state)
        profiled = _completed(invocation.state, "data_profiling")
        frame = (
            _ref(profiled.summary, "frame")
            if profiled is not None
            else _ref(shared_result(invocation.state, "intake").summary, "raw")
        )
        features = self.runtime.contract.features
        ignored: list[JsonValue] = [
            c
            for c in dict.fromkeys((*features.id_columns, *features.exclude))
            if c != problem.target
        ]
        check = await self._call(
            invocation,
            evidence,
            "check_data_quality",
            {
                "frame": str(frame),
                "target": problem.target,
                "id_columns": ignored,
                "min_class_rows": self.runtime.contract.modelling.tuning.cv_folds,
            },
        )
        fixable = _object(check.summary.get("fixable"), "quality fixable")
        decision = await self.runtime.decide(
            invocation,
            QualityDecision,
            evidence=tuple(evidence.tools),
            question="Review the data quality checks (whole input, before the split). "
            "Choose fixes only from the reported fixable lists: mark_missing maps a column to "
            "cell texts that stand for a missing value; merge_variants lists text columns whose "
            "spelling variants name the same category. Leave both empty to change nothing. "
            "Name every failing check in findings.",
            check=_rejecting(lambda d: _validate_quality(d, fixable)),
        )
        _validate_quality(decision, fixable)
        summary: dict[str, JsonValue] = {
            "frame": str(frame),
            "decision": decision.model_dump(mode="json"),
            "passed": check.summary.get("passed"),
            "checks": check.summary.get("checks"),
        }
        approved = await self._approve(invocation, evidence, decision)
        if approved is None:
            return evidence.result("awaiting_user", summary)
        _validate_quality(approved, fixable)
        summary["decision"] = approved.model_dump(mode="json")
        if approved.mark_missing or approved.merge_variants:
            fixed = await self._call(
                invocation,
                evidence,
                "fix_data_quality",
                {
                    "frame": str(frame),
                    "target": problem.target,
                    "mark_missing": {c: list(v) for c, v in approved.mark_missing.items()},
                    "merge_variants": list(approved.merge_variants),
                },
            )
            summary["frame"] = str(_frame(fixed))
        return evidence.result("completed", summary)

    def _split_rules(self, decision: SplitDecision, problem: ProblemSpec) -> SplitRules:
        contract = self.runtime.contract
        strategy = (
            contract.split.strategy
            or decision.strategy
            or ("stratified" if problem.task == "classification" else "random")
        )
        if strategy == "grouped" or decision.dependence == "grouped":
            raise _RefusalError("grouped rows have no supported split yet; review data")
        if strategy == "temporal" and contract.split.time_column is None:
            raise _RefusalError("a temporal split needs split.time_column in the contract")
        if decision.dependence == "temporal" and strategy != "temporal":
            raise _RefusalError(
                "rows depend on time: set split.strategy to temporal and split.time_column "
                "in the contract"
            )
        rules = SplitRules(
            strategy=strategy,
            test_size=contract.split.test_size,
            valid_size=contract.split.valid_size,
            seed=contract.split.seed,
            time_column=contract.split.time_column if strategy == "temporal" else None,
        )
        if strategy == "stratified" and problem.task != "classification":
            raise _RefusalError("regression cannot use a stratified split")
        return rules

    async def _split(self, invocation: NodeInvocation, evidence: _Evidence) -> NodeResult:
        problem = problem_from_state(invocation.state)
        decision = await self.runtime.decide(
            invocation,
            SplitDecision,
            evidence=tuple(boundary.result.summary for boundary in invocation.state.shared_results),
            question="Choose only an absent strategy. Flag temporal or grouped dependence: a "
            "temporal split needs split.time_column in the contract, and grouped rows cannot be "
            "split yet. Contract ratios, seed and structural policies remain fixed.",
        )
        rules = self._split_rules(decision, problem)
        summary: dict[str, JsonValue] = {
            "decision": decision.model_dump(mode="json"),
            "split": rules.model_dump(mode="json"),
        }
        approved = await self.runtime.approve(
            invocation,
            decision,
            tuple(boundary.result.summary for boundary in invocation.state.shared_results),
        )
        if approved is None:
            return evidence.result("awaiting_user", summary)
        rules = self._split_rules(approved, problem)
        summary.update(
            decision=approved.model_dump(mode="json"), split=rules.model_dump(mode="json")
        )
        intake = shared_result(invocation.state, "intake")
        raw = _ref(intake.summary, "raw")
        profiled = _completed(invocation.state, "data_profiling")
        quality = _completed(invocation.state, "data_quality")
        frame = (
            _ref(quality.summary, "frame")
            if quality is not None
            else (_ref(profiled.summary, "frame") if profiled is not None else raw)
        )
        contract = self.runtime.contract
        columns = _strings(_tool_summary(intake, "load_csv").get("columns"), "intake columns")
        if profiled is None and contract.features.roles:
            frame = _frame(
                await self._call(
                    invocation,
                    evidence,
                    "cast_dtypes",
                    {
                        "frame": str(frame),
                        "target": problem.target,
                        "roles": contract.features.model_dump(mode="json")["roles"],
                    },
                )
            )
        protected = {
            problem.target,
            *contract.features.id_columns,
            contract.split.time_column,
            contract.split.group_column,
        }
        excluded = tuple(column for column in contract.features.exclude if column not in protected)
        if contract.preprocessing.drop_duplicates:
            frame = _frame(
                await self._call(
                    invocation,
                    evidence,
                    "drop_duplicate_rows",
                    {
                        "frame": str(frame),
                        "subset": [
                            column
                            for column in columns
                            if column not in contract.features.id_columns and column not in excluded
                        ],
                    },
                )
            )
        threshold = contract.preprocessing.missing.drop_rows_above
        if threshold is not None:
            frame = _frame(
                await self._call(
                    invocation,
                    evidence,
                    "drop_high_missing_rows",
                    {
                        "frame": str(frame),
                        "threshold": threshold,
                    },
                )
            )
        if excluded:
            frame = _frame(
                await self._call(
                    invocation,
                    evidence,
                    "drop_columns",
                    {
                        "frame": str(frame),
                        "columns": list(excluded),
                    },
                )
            )
        arguments: dict[str, JsonValue] = {
            "frame": str(frame),
            "target": problem.target,
            "test_size": rules.test_size,
            "valid_size": rules.valid_size,
        }
        if rules.strategy == "temporal":
            arguments["time_column"] = rules.time_column
        else:
            arguments["seed"] = rules.seed
        splits = await self._call(
            invocation,
            evidence,
            f"split_{rules.strategy}",
            arguments,
        )
        refs = {
            artifact.split: str(artifact.ref) for artifact in splits.artifacts if artifact.split
        }
        if set(refs) != {"train", "valid", "test"} or len(splits.artifacts) != 3:
            raise EngineError("split tool returned malformed split artifacts")
        verification = await self._call(
            invocation,
            evidence,
            "verify_split",
            {
                "train": refs["train"],
                "valid": refs["valid"],
                "test": refs["test"],
                "target": problem.target,
            },
        )
        if verification.summary.get("passed") is not True:
            raise _RefusalError("split verification failed; no successful split published")
        fingerprint = splits.summary.get("test_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise EngineError("split tool omitted test fingerprint")
        set_test_fingerprint(self.runtime.conn, invocation.state.run_id, fingerprint)
        run = require_run(self.runtime.conn, invocation.state.run_id)
        if run.test_fingerprint != fingerprint:
            raise _RefusalError("run already records a different test fingerprint")
        summary.update(
            train=refs["train"],
            valid=refs["valid"],
            test=refs["test"],
            test_fingerprint=fingerprint,
            report=splits.summary,
        )
        return evidence.result("completed", summary)
