"""Typed proposals and checkpointed outputs for the shared foundation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.candidate import CandidateSet, CandidateSpec
from statlab_contracts.contract import InferenceGoal, Objective
from statlab_contracts.reporting import ReportSection
from statlab_contracts.vocabulary import (
    METRICS_FOR_TASK,
    RESERVED_ESTIMATOR_PARAMS,
    TASK_FOR_TARGET_TYPE,
    Metric,
    ParamValue,
    SplitStrategy,
    TargetType,
    Task,
    tunable_grid_errors,
    tunable_param_errors,
)

MetricRule = Literal["explicit", "classification_auto", "regression_auto", "override"]


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rationale: str = Field(min_length=1)
    user_summary: str | None = Field(
        default=None,
        min_length=1,
        max_length=600,
        description=(
            "Always fill this: one to three short plain-language sentences for the user's"
            " chat saying what you did at this step and what you found. Name the columns"
            " and numbers that matter. No internal ids, no jargon, no markdown."
        ),
    )

    @field_validator("user_summary", mode="before")
    @classmethod
    def _shorten_summary(cls, value: object) -> object:
        """Shorten an over-long chat summary at a sentence end
        instead of failing the step (chunk 79)."""
        if not isinstance(value, str) or len(value) <= 600:
            return value
        end = value[:601].rfind(". ")
        if end >= 0:
            return value[: end + 1]
        return value[:599].rstrip() + "…"


class ChampionChoice(Decision):
    candidate_id: str = Field(pattern=r"^c_[0-9]{2,}\z")
    rationale: str = Field(min_length=1, max_length=2000)
    sections: tuple[ReportSection, ...] = Field(default=(), max_length=6)


class PersistenceDecision(Decision):
    preserve: Literal[True] = True


class IntakeDecision(Decision):
    findings: tuple[str, ...] = ()
    proposed_fixes: tuple[str, ...] = ()
    disposition: Literal["proceed", "escalate"] = "proceed"


class ProblemDecision(Decision):
    target_type: TargetType | None = None
    task: Task | None = None
    primary_metric: Metric | None = None
    metric_override_rationale: str | None = Field(default=None, min_length=1)
    secondary_metrics: tuple[Metric, ...] = ()
    success_threshold: float | None = None
    inference_goals: tuple[InferenceGoal, ...] = ()
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _override_reason(self) -> ProblemDecision:
        if (
            self.metric_override_rationale is not None
            and not self.metric_override_rationale.strip()
        ):
            raise ValueError("metric override rationale must be nonblank")
        return self


class ProblemSpec(Decision):
    target: str = Field(min_length=1)
    target_type: TargetType
    task: Task
    primary_metric: Metric | None
    metric_rule: MetricRule
    metric_override_rationale: str | None = None
    secondary_metrics: tuple[Metric, ...] = ()
    success_threshold: float | None = None
    inference_goals: tuple[InferenceGoal, ...] = ()
    constraints: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> ProblemSpec:
        if self.task != TASK_FOR_TARGET_TYPE[self.target_type]:
            raise ValueError("target type and task disagree")
        if (
            self.primary_metric is not None
            and self.primary_metric not in METRICS_FOR_TASK[self.task]
        ):
            raise ValueError("primary metric is incompatible with task")
        Objective(
            task=self.task,
            primary_metric=self.primary_metric,
            secondary_metrics=self.secondary_metrics,
            success_threshold=self.success_threshold,
            inference_goals=self.inference_goals,
        )
        if any(metric not in METRICS_FOR_TASK[self.task] for metric in self.secondary_metrics):
            raise ValueError("secondary metric is incompatible with task")
        if self.metric_rule == "classification_auto":
            if self.task != "classification":
                raise ValueError("classification rule requires classification")
        elif self.primary_metric is None:
            raise ValueError("resolved metric is required")
        if self.metric_rule == "override" and (
            self.metric_override_rationale is None or not self.metric_override_rationale.strip()
        ):
            raise ValueError("metric override needs a nonblank rationale")
        return self


class ProfilePlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["distribution", "target_relation"]
    column: str = Field(min_length=1)


class ProfileDecision(Decision):
    numeric: tuple[str, ...] = ()
    categorical: tuple[str, ...] = ()
    correlations: tuple[str, ...] = ()
    outliers: tuple[str, ...] = ()
    plots: tuple[ProfilePlot, ...] = Field(default=(), max_length=4)

    @field_validator("numeric", "categorical", "correlations", "outliers")
    @classmethod
    def _unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Repeated column names are dropped; the first occurrence is kept (chunk 69)."""
        return tuple(dict.fromkeys(value))


class ProfileFindings(Decision):
    findings: tuple[str, ...] = ()
    exploratory_hypotheses: tuple[str, ...] = ()


class QualityDecision(Decision):
    """Which reported quality fixes to apply (chunk 63); values come only from
    `check_data_quality`'s `fixable` lists."""

    findings: tuple[str, ...] = ()
    mark_missing: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    merge_variants: tuple[str, ...] = ()


class SplitDecision(Decision):
    strategy: SplitStrategy | None = None
    dependence: Literal["independent", "temporal", "grouped"] = "independent"
    concerns: tuple[str, ...] = ()


class UnavailableDecision(BaseModel):
    """Declaration only: later stages must replace this schema before execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    unavailable: Literal[True]


class DesignDecision(Decision):
    candidate_set: CandidateSet
    family_justifications: dict[str, str] = Field(default_factory=dict)


class InferenceDecision(Decision):
    model: str = Field(min_length=1)
    predictors: tuple[str, ...] = Field(min_length=1)
    reference_levels: dict[str, str] = Field(default_factory=dict)
    residual_plot: bool = False


class CandidateDecision(Decision):
    material_exceptions: tuple[str, ...] = ()


class PreprocessingDecision(CandidateDecision):
    drop_constant: bool = True
    high_missing_threshold: float | None = Field(default=None, gt=0, le=1)


class FeatureDecision(CandidateDecision):
    """Feature engineering executes the approved specification; it never skips."""


class TrainingDecision(CandidateDecision):
    method: Literal["direct", "grid"] = "direct"
    params: dict[str, ParamValue] = Field(default_factory=dict)
    param_grid: dict[str, tuple[ParamValue, ...]] | None = None

    @model_validator(mode="after")
    def _no_tool_fixed_params(self) -> TrainingDecision:
        keys = set(self.params) | set(self.param_grid or {})
        reserved = sorted(keys & RESERVED_ESTIMATOR_PARAMS)
        if reserved:
            raise ValueError(
                f"training params {reserved!r} are fixed by the tools "
                "(n_jobs by D17, random_state by D34e); omit them"
            )
        return self

    @model_validator(mode="after")
    def _tunable_params(self) -> TrainingDecision:
        errors = [
            *tunable_param_errors(self.params, None),
            *tunable_grid_errors(self.param_grid or {}),
        ]
        if errors:
            raise ValueError("; ".join(errors))
        return self


class EvaluationActions(CandidateDecision):
    curves: tuple[Literal["confusion", "roc", "pr", "calibration"], ...] = ()
    threshold_objective: Literal["f1", "target_recall", "min_cost"] | None = None
    target_recall: float = Field(default=0.8, gt=0, le=1)
    cost_fn_to_fp: float = Field(default=1, gt=0)
    cross_validation: bool = False
    learning_curve: bool = False


class ValidationConclusion(CandidateDecision):
    retune: bool = False
    retune_reason: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _retune_reason(self) -> ValidationConclusion:
        if self.retune and (self.retune_reason is None or not self.retune_reason.strip()):
            raise ValueError("retune requires a nonblank reason")
        return self


class EvaluationDecision(EvaluationActions, ValidationConclusion):
    """Durable actions plus the conclusion drawn after their observed validation results."""


class InterpretationDecision(CandidateDecision):
    skip_reason: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Set only to skip this whole step: then no permutation importance, partial "
            "dependence or error analysis is produced. Leave it null to run the diagnostics. "
            "To leave out coefficients for a non-linear model, keep coefficients false"
            " instead of skipping."
        ),
    )
    coefficients: bool = False
    partial_dependence: tuple[str, ...] = ()
    error_by: tuple[str, ...] = ()


class SearchDecision(Decision):
    candidates: tuple[CandidateSpec, ...] = ()
    family_justifications: dict[str, str] = Field(default_factory=dict)


class ReplayStep(BaseModel):
    """A held-out transform; frame is bound by the replay caller, never saved here."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    tool: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class PipelineState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    train: ArtifactRef
    valid: ArtifactRef
    replay: tuple[ReplayStep, ...] = ()
    feature_manifest: dict[str, JsonValue] = Field(default_factory=dict)
    methodology: CandidateSpec
