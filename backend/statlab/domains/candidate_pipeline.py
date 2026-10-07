"""Train-only preparation and ordered held-out transform manifests."""

from __future__ import annotations

from pydantic import JsonValue, ValidationError
from statlab_contracts.envelope import Failure, ToolSuccess

from statlab.domains.base import NodeResult
from statlab.domains.decisions import (
    CandidateDecision,
    FeatureDecision,
    PipelineState,
    PreprocessingDecision,
    ReplayStep,
)
from statlab.domains.design_inference import _artifact
from statlab.domains.foundation import (
    _Evidence,
    _object,
    _ref,
    _RefusalError,
    _strings,
    _tool_summary,
    problem_from_state,
    shared_result,
)
from statlab.domains.runtime import DomainRuntime
from statlab.errors import EngineError, ToolCallError
from statlab.graph.engine import NodeInvocation
from statlab.graph.state import CandidateProgress, RunState


def candidate_result(branch: CandidateProgress, node: str) -> NodeResult:
    """Read only committed successful attempts, newest retune first."""
    for boundary in reversed(branch.results):
        if boundary.node_name == node and boundary.result.status == "completed":
            return boundary.result
    raise EngineError(f"missing successful candidate boundary: {node}")


def pipeline_from_state(
    state: RunState, branch: CandidateProgress, *, require: str | None = None
) -> PipelineState:
    if require is not None and not any(
        boundary.node_name == require and boundary.result.status in ("completed", "skipped")
        for boundary in branch.results
    ):
        raise EngineError(f"missing candidate preparation boundary: {require}")
    for node in ("feature_engineering", "preprocessing"):
        for boundary in reversed(branch.results):
            if boundary.node_name != node:
                continue
            if boundary.result.status == "skipped":
                # An optional skip passes through its predecessor, never partial artifacts.
                break
            if boundary.result.status == "completed":
                try:
                    pipeline = PipelineState.model_validate(
                        _object(boundary.result.summary.get("pipeline"), "candidate pipeline")
                    )
                except ValidationError as exc:
                    raise EngineError("malformed candidate pipeline", cause=exc) from exc
                if pipeline.methodology != branch.spec:
                    raise EngineError("pipeline methodology disagrees with approved candidate")
                return pipeline
    split = shared_result(state, "data_splitting").summary
    return PipelineState(
        train=_ref(split, "train"),
        valid=_ref(split, "valid"),
        methodology=branch.spec,
        feature_manifest={"source": "shared split", "transforms": []},
    )


async def exception_approval[T: CandidateDecision](
    runtime: DomainRuntime,
    invocation: NodeInvocation,
    decision: T,
    evidence: _Evidence,
    *,
    after_action: bool = False,
) -> T | None:
    if not decision.material_exceptions and not evidence.warnings:
        return decision
    approval_evidence: tuple[JsonValue, ...] = (
        *evidence.tools,
        {"warnings": list(evidence.warnings)},
    )
    approved = await runtime.approve(
        invocation, decision, approval_evidence, after_action=after_action
    )
    if after_action and approved is not None and approved != decision:
        raise _RefusalError("completed actions cannot be changed by exception acknowledgment")
    return approved


class CandidatePipelineExecutor:
    def __init__(self, runtime: DomainRuntime) -> None:
        self.runtime = runtime

    async def __call__(self, invocation: NodeInvocation) -> NodeResult:
        self.runtime.context(invocation)
        if invocation.candidate is None:
            raise EngineError("pipeline requires a candidate")
        methods = {"preprocessing": self._preprocessing, "feature_engineering": self._features}
        method = methods.get(invocation.spec.name)
        if method is None:
            raise EngineError("pipeline executor cannot execute this node")
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
        params: dict[str, JsonValue],
    ) -> ToolSuccess:
        output = await self.runtime.call(invocation, tool, params)
        evidence.add(tool, output)
        return output

    async def _transform(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        pipeline: PipelineState,
        tool: str,
        parameters: dict[str, JsonValue],
        *,
        train_output: ToolSuccess | None = None,
    ) -> PipelineState:
        train = train_output or await self._call(
            invocation, evidence, tool, {**parameters, "frame": str(pipeline.train)}
        )
        valid = await self._call(
            invocation, evidence, tool, {**parameters, "frame": str(pipeline.valid)}
        )
        return PipelineState(
            train=_artifact(train, "dataframe"),
            valid=_artifact(valid, "dataframe"),
            methodology=pipeline.methodology,
            feature_manifest=pipeline.feature_manifest,
            replay=(*pipeline.replay, ReplayStep(tool=tool, parameters=parameters)),
        )

    async def _fit_apply(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        pipeline: PipelineState,
        fit: str,
        apply: str,
        key: str,
        parameters: dict[str, JsonValue],
    ) -> PipelineState:
        updated, _ = await self._fit_apply_output(
            invocation, evidence, pipeline, fit, apply, key, parameters
        )
        return updated

    async def _fit_apply_output(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        pipeline: PipelineState,
        fit: str,
        apply: str,
        key: str,
        parameters: dict[str, JsonValue],
    ) -> tuple[PipelineState, ToolSuccess]:
        output = await self._call(
            invocation,
            evidence,
            fit,
            {
                **parameters,
                "train": str(pipeline.train),
                "target": self.runtime.contract.target.column,
            },
        )
        prepared = await self._transform(
            invocation,
            evidence,
            pipeline,
            apply,
            {key: str(_artifact(output, "transformer"))},
            train_output=output if fit == "fit_target_encoder" else None,
        )
        transforms = prepared.feature_manifest.get("transforms")
        if not isinstance(transforms, list):
            raise EngineError("malformed feature transform manifest")
        updated = prepared.model_copy(
            update={
                "feature_manifest": {
                    **prepared.feature_manifest,
                    "transforms": [*transforms, {"tool": fit, "summary": output.summary}],
                }
            }
        )
        return updated, output

    async def _finish(
        self,
        invocation: NodeInvocation,
        evidence: _Evidence,
        pipeline: PipelineState,
        decision: CandidateDecision,
    ) -> NodeResult:
        summary: dict[str, JsonValue] = {
            "pipeline": pipeline.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        }
        # Carried fitted objects remain live across preparation and awaiting exception gates.
        evidence.refs.extend((pipeline.train, pipeline.valid))
        for step in pipeline.replay:
            for key in ("imputer", "clipper", "encoder", "scaler", "binner"):
                if key in step.parameters:
                    evidence.refs.append(_ref(step.parameters, key))
        approved = await exception_approval(
            self.runtime, invocation, decision, evidence, after_action=True
        )
        return evidence.result("awaiting_user" if approved is None else "completed", summary)

    async def _preprocessing(
        self,
        invocation: NodeInvocation,
        branch: CandidateProgress,
        evidence: _Evidence,
    ) -> NodeResult:
        pipeline = pipeline_from_state(invocation.state, branch)
        problem = problem_from_state(invocation.state)
        decision = await self.runtime.decide(
            invocation,
            PreprocessingDecision,
            evidence=(pipeline.model_dump(mode="json"),),
            question="Execute approved preprocessing. Decide train-only constant/high-missing "
            "column drops. Imputation/outliers are fixed by candidate; never drop rows or cast. "
            "Declare material exceptions. Explicit contract drop_constant is authoritative.",
        )
        approved = await exception_approval(self.runtime, invocation, decision, evidence)
        if approved is None:
            return evidence.result("awaiting_user", {"decision": decision.model_dump(mode="json")})
        decision = approved
        rules = self.runtime.contract.preprocessing
        if (
            "drop_constant" in rules.model_fields_set
            and decision.drop_constant != rules.drop_constant
        ):
            raise _RefusalError("constant drop conflicts with explicit contract")
        intake = shared_result(invocation.state, "intake")
        columns = _strings(_tool_summary(intake, "load_csv").get("columns"), "source columns")
        features = self.runtime.contract.features
        # Node 4 already drops exclusions except structurally protected columns.
        split_excluded = set(features.exclude) - {
            problem.target,
            *features.id_columns,
            self.runtime.contract.split.time_column,
            self.runtime.contract.split.group_column,
        }
        present = tuple(column for column in columns if column not in split_excluded)
        drop = tuple(
            column
            for column in present
            if column != problem.target
            and (
                column in features.exclude
                or column in features.id_columns
                or (features.include is not None and column not in features.include)
            )
        )
        if drop:
            pipeline = await self._transform(
                invocation, evidence, pipeline, "drop_columns", {"columns": list(drop)}
            )
        pipeline = pipeline.model_copy(
            update={
                "feature_manifest": {
                    "input_features": [
                        column
                        for column in present
                        if column != problem.target and column not in drop
                    ],
                    "transforms": [],
                }
            }
        )
        deciders: list[tuple[str, dict[str, JsonValue]]] = []
        if decision.drop_constant:
            deciders.append(("drop_constant_columns", {}))
        if decision.high_missing_threshold is not None:
            deciders.append(
                ("drop_high_missing_columns", {"threshold": decision.high_missing_threshold})
            )
        for tool, parameters in deciders:
            output = await self._call(
                invocation,
                evidence,
                tool,
                {
                    **parameters,
                    "train": str(pipeline.train),
                    "target": problem.target,
                },
            )
            dropped = _strings(output.summary.get("dropped_columns"), "dropped columns")
            if dropped:
                pipeline = await self._transform(
                    invocation,
                    evidence,
                    pipeline,
                    "drop_columns",
                    {"columns": list(dropped)},
                    train_output=output,
                )
            else:
                pipeline = pipeline.model_copy(update={"train": _artifact(output, "dataframe")})
        missing = branch.spec.preprocessing.missing
        pipeline = await self._fit_apply(
            invocation,
            evidence,
            pipeline,
            "fit_imputer",
            "apply_imputer",
            "imputer",
            {
                "default": missing.default,
                "per_column": missing.model_dump(mode="json")["per_column"],
            },
        )
        if branch.spec.preprocessing.outliers != "none":
            pipeline = await self._fit_apply(
                invocation,
                evidence,
                pipeline,
                "fit_outlier_clipper",
                "apply_outlier_clipper",
                "clipper",
                {"method": branch.spec.preprocessing.outliers},
            )
        return await self._finish(invocation, evidence, pipeline, decision)

    async def _features(
        self,
        invocation: NodeInvocation,
        branch: CandidateProgress,
        evidence: _Evidence,
    ) -> NodeResult:
        pipeline = pipeline_from_state(invocation.state, branch, require="preprocessing")
        feature = branch.spec.features
        decision = await self.runtime.decide(
            invocation,
            FeatureDecision,
            evidence=(pipeline.model_dump(mode="json"),),
            question="Execute fixed approved features, encoding, scaling and selection. Explain "
            "exceptions, never invent ratios/indicators. Approved actions must execute.",
        )
        approved = await exception_approval(self.runtime, invocation, decision, evidence)
        if approved is None:
            return evidence.result("awaiting_user", {"decision": decision.model_dump(mode="json")})
        decision = approved
        if decision.skip_reason is not None:
            raise _RefusalError("approved encoding must execute; feature stage cannot skip")
        if feature.encoding == "frequency":
            raise _RefusalError("frequency encoding has no supported tool")
        for column, parts in feature.datetime_parts.items():
            pipeline = await self._transform(
                invocation,
                evidence,
                pipeline,
                "add_datetime_parts",
                {
                    "target": self.runtime.contract.target.column,
                    "column": column,
                    "parts": list(parts),
                },
            )
        if feature.interactions:
            pipeline = await self._transform(
                invocation,
                evidence,
                pipeline,
                "add_interaction_terms",
                {
                    "target": self.runtime.contract.target.column,
                    "pairs": [list(pair) for pair in feature.interactions],
                },
            )
        for column, bins in feature.binned.items():
            pipeline = await self._fit_apply(
                invocation,
                evidence,
                pipeline,
                "fit_binner",
                "apply_binner",
                "binner",
                {
                    "columns": [column],
                    "n_bins": bins,
                },
            )
        roles = {
            **_object(
                _tool_summary(shared_result(invocation.state, "intake"), "infer_column_roles").get(
                    "roles"
                ),
                "column roles",
            ),
            **self.runtime.contract.features.roles,
        }
        available = set(_strings(pipeline.feature_manifest.get("input_features"), "input features"))
        for step in pipeline.replay:
            if step.tool == "drop_columns":
                available -= set(_strings(step.parameters.get("columns"), "dropped columns"))
        transforms = pipeline.feature_manifest.get("transforms")
        if isinstance(transforms, list):
            for entry in transforms:
                if isinstance(entry, dict) and entry.get("tool") == "fit_imputer":
                    available -= set(
                        _strings(
                            _object(entry.get("summary"), "imputer summary").get("dropped"),
                            "imputer drops",
                        )
                    )
        categorical = sorted(
            column
            for column in available
            if roles.get(column) in ("categorical", "ordinal", "text")
            and column not in feature.datetime_parts
            and column not in feature.binned
        )
        categorical.extend(f"{column}__bin" for column in feature.binned)
        if feature.encoding == "onehot":
            pipeline, output = await self._fit_apply_output(
                invocation,
                evidence,
                pipeline,
                "fit_onehot_encoder",
                "apply_onehot_encoder",
                "encoder",
                {"max_cardinality": feature.max_cardinality},
            )
            skipped = output.summary.get("skipped_high_cardinality")
            if isinstance(skipped, dict) and skipped:
                skipped_parameters: dict[str, JsonValue] = {
                    "columns": [*sorted(skipped)],
                    "seed": self.runtime.contract.constraints.random_seed,
                }
                pipeline = await self._fit_apply(
                    invocation,
                    evidence,
                    pipeline,
                    "fit_target_encoder",
                    "apply_target_encoder",
                    "encoder",
                    skipped_parameters,
                )
        elif categorical:
            parameters: dict[str, JsonValue] = {"columns": list(categorical)}
            if feature.encoding == "ordinal":
                parameters["level_order"] = {
                    column: list(levels)
                    for column, levels in self.runtime.contract.features.ordinal_levels.items()
                    if column in categorical
                }
            else:
                parameters["seed"] = self.runtime.contract.constraints.random_seed
            pipeline = await self._fit_apply(
                invocation,
                evidence,
                pipeline,
                f"fit_{feature.encoding}_encoder",
                f"apply_{feature.encoding}_encoder",
                "encoder",
                parameters,
            )
        if feature.scaling != "none":
            pipeline = await self._fit_apply(
                invocation,
                evidence,
                pipeline,
                "fit_scaler",
                "apply_scaler",
                "scaler",
                {"method": feature.scaling},
            )
        selection = feature.selection
        if selection.method != "none":
            params: dict[str, JsonValue] = {
                "train": str(pipeline.train),
                "target": self.runtime.contract.target.column,
            }
            if selection.method == "variance":
                params["threshold"] = selection.threshold
            else:
                params.update(k=selection.k, seed=self.runtime.contract.constraints.random_seed)
            output = await self._call(invocation, evidence, f"select_by_{selection.method}", params)
            dropped = _strings(output.summary.get("dropped_columns"), "selected drops")
            if dropped:
                pipeline = await self._transform(
                    invocation,
                    evidence,
                    pipeline,
                    "drop_columns",
                    {"columns": list(dropped)},
                    train_output=output,
                )
            else:
                pipeline = pipeline.model_copy(update={"train": _artifact(output, "dataframe")})
        return await self._finish(invocation, evidence, pipeline, decision)
