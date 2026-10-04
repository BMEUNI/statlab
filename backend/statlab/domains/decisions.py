"""Typed proposals and checkpointed outputs for the shared foundation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from statlab_contracts.candidate import CandidateSet
from statlab_contracts.contract import InferenceGoal, Objective
from statlab_contracts.vocabulary import (
    METRICS_FOR_TASK,
    TASK_FOR_TARGET_TYPE,
    Metric,
    SplitStrategy,
    TargetType,
    Task,
)

MetricRule = Literal["explicit", "classification_auto", "regression_auto", "override"]


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rationale: str = Field(min_length=1)


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


class ProfileFindings(Decision):
    findings: tuple[str, ...] = ()
    exploratory_hypotheses: tuple[str, ...] = ()


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
