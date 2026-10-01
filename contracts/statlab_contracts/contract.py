"""The data contract: PLAN.md §4.1, as a validated Pydantic tree.

This is the object the user uploads, the setup wizard produces, node 1 re-validates, and every
later node reads. It validates *against itself* -- checking it against an actual CSV (do these
columns exist, is the target really binary) is chunk 08's `validate_contract` MCP tool, which is
where pandas lives. Nothing here imports pandas, or anything beyond stdlib and pydantic.

Deviation from PLAN.md §3.2: PLAN.md places this module at `backend/statlab/contract.py`. It
lives here, in `contracts/statlab_contracts/`, instead, because the contract crosses the process
boundary -- chunk 08's `validate_contract(df, contract)` tool receives it, and chunk 27's intake
node sends it. That is the same argument chunk 02 used for `ToolResult`: two definitions would
drift, and a tool validating a different shape than the backend parses fails as silent
misparsing. A Pydantic model with validators is still "types and validation, not logic", so this
stays consistent with chunk 02's dependency-free rule for this package.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from statlab_contracts.vocabulary import (
    METRICS_FOR_TASK,
    TASK_FOR_TARGET_TYPE,
    CategoricalEncoding,
    ClassWeight,
    ColumnRole,
    ImputationStrategy,
    Metric,
    OutlierPolicy,
    ScalingMethod,
    SplitStrategy,
    TargetType,
    Task,
    TuningMethod,
)

# --- Dataset ---------------------------------------------------------------------------------


class DatasetOptions(BaseModel):
    """CSV parsing options. Node 1 copies these into `load_csv` verbatim (PLAN.md §4.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    delimiter: str = Field(default=",", min_length=1, max_length=4)
    encoding: str = Field(default="utf-8", min_length=1)
    decimal: str = Field(default=".", min_length=1, max_length=1)
    na_values: tuple[str, ...] = ("", "NA", "N/A", "null", "NULL")
    header_row: int = Field(default=0, ge=0)


# --- Target ------------------------------------------------------------------------------------


class TargetSpec(BaseModel):
    """The one place the target is declared. `column` is the only required field in the
    entire contract (D8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str = Field(min_length=1)
    type: TargetType | None = None
    positive_class: str | int | bool | None = None
    class_order: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _positive_class_requires_binary(self) -> TargetSpec:
        """V1: `positive_class` requires `type == "binary"`."""
        if self.positive_class is not None and self.type is not None and self.type != "binary":
            raise ValueError(
                f"positive_class requires target.type == 'binary', got type={self.type!r}"
            )
        return self

    @model_validator(mode="after")
    def _class_order_requires_multiclass(self) -> TargetSpec:
        """V2: `class_order` requires `type == "multiclass"`."""
        if self.class_order is not None and self.type is not None and self.type != "multiclass":
            raise ValueError(
                f"class_order requires target.type == 'multiclass', got type={self.type!r}"
            )
        return self

    @model_validator(mode="after")
    def _class_order_entries_unique_and_non_empty(self) -> TargetSpec:
        """V3: `class_order` entries unique and non-empty."""
        if self.class_order is not None:
            if not self.class_order:
                raise ValueError("class_order must not be empty when set")
            if len(set(self.class_order)) != len(self.class_order):
                raise ValueError(f"class_order entries must be unique, got {self.class_order!r}")
        return self


# --- Features ------------------------------------------------------------------------------------


class FeatureRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    include: tuple[str, ...] | None = None  # None = everything not excluded
    exclude: tuple[str, ...] = ()
    id_columns: tuple[str, ...] = ()
    roles: dict[str, ColumnRole] = Field(default_factory=dict)
    ordinal_levels: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _include_exclude_disjoint(self) -> FeatureRules:
        """V4: `include` and `exclude` are disjoint."""
        if self.include is not None:
            overlap = set(self.include) & set(self.exclude)
            if overlap:
                raise ValueError(
                    f"features.include and features.exclude overlap on {sorted(overlap)!r}"
                )
        return self

    @model_validator(mode="after")
    def _id_columns_disjoint_from_include(self) -> FeatureRules:
        """V5: `id_columns` disjoint from `include`."""
        if self.include is not None:
            overlap = set(self.id_columns) & set(self.include)
            if overlap:
                raise ValueError(
                    f"features.id_columns and features.include overlap on {sorted(overlap)!r}"
                )
        return self

    @model_validator(mode="after")
    def _ordinal_levels_keys_are_ordinal_role(self) -> FeatureRules:
        """V6: every `ordinal_levels` key that also appears in `roles` must be `"ordinal"`.

        A key absent from `roles` is allowed -- the role may be inferred later.
        """
        for column in self.ordinal_levels:
            role = self.roles.get(column)
            if role is not None and role != "ordinal":
                raise ValueError(
                    f"features.ordinal_levels has key {column!r} but "
                    f"features.roles[{column!r}] == {role!r}, expected 'ordinal'"
                )
        return self

    @model_validator(mode="after")
    def _ordinal_levels_values_have_two_unique_entries(self) -> FeatureRules:
        """V7: each `ordinal_levels` value has >= 2 unique entries."""
        for column, levels in self.ordinal_levels.items():
            if len(set(levels)) < 2:
                raise ValueError(
                    f"features.ordinal_levels[{column!r}] must have at least 2 unique "
                    f"entries, got {levels!r}"
                )
        return self


# --- Objective -----------------------------------------------------------------------------------


class InferenceGoal(BaseModel):
    """One question the D7/D31 inferential track must answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    predictors: tuple[str, ...] = Field(min_length=1)
    alpha: float = Field(default=0.05, gt=0.0, le=0.5)


class Objective(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: Task | None = None
    primary_metric: Metric | None = None
    secondary_metrics: tuple[Metric, ...] = ()
    success_threshold: float | None = None
    inference_goals: tuple[InferenceGoal, ...] = ()

    @model_validator(mode="after")
    def _primary_metric_not_in_secondary(self) -> Objective:
        """V8: `primary_metric` not repeated in `secondary_metrics`."""
        if self.primary_metric is not None and self.primary_metric in self.secondary_metrics:
            raise ValueError(
                f"objective.primary_metric {self.primary_metric!r} must not also appear in "
                f"objective.secondary_metrics {self.secondary_metrics!r}"
            )
        return self

    @model_validator(mode="after")
    def _secondary_metrics_unique(self) -> Objective:
        """V9: `secondary_metrics` entries unique."""
        if len(set(self.secondary_metrics)) != len(self.secondary_metrics):
            raise ValueError(
                f"objective.secondary_metrics entries must be unique, got "
                f"{self.secondary_metrics!r}"
            )
        return self


# --- Split ---------------------------------------------------------------------------------------


class SplitRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: SplitStrategy | None = None
    test_size: float = Field(default=0.2, gt=0.0, lt=1.0)
    valid_size: float = Field(default=0.2, ge=0.0, lt=1.0)
    time_column: str | None = None
    group_column: str | None = None
    seed: int = Field(default=42, ge=0)

    @model_validator(mode="after")
    def _sizes_leave_room_for_train(self) -> SplitRules:
        """V10: `test_size + valid_size < 0.9`."""
        total = self.test_size + self.valid_size
        if not total < 0.9:
            raise ValueError(
                f"split.test_size + split.valid_size must be < 0.9, got "
                f"{self.test_size!r} + {self.valid_size!r} = {total!r}"
            )
        return self

    @model_validator(mode="after")
    def _time_column_iff_temporal(self) -> SplitRules:
        """V11: `time_column` set iff `strategy == "temporal"`."""
        if self.strategy is not None:
            if self.strategy == "temporal" and self.time_column is None:
                raise ValueError("split.time_column is mandatory when split.strategy == 'temporal'")
            if self.strategy != "temporal" and self.time_column is not None:
                raise ValueError(
                    f"split.time_column is forbidden when split.strategy == {self.strategy!r} "
                    f"(only allowed for 'temporal')"
                )
        return self

    @model_validator(mode="after")
    def _group_column_iff_grouped(self) -> SplitRules:
        """V12: `group_column` set iff `strategy == "grouped"`."""
        if self.strategy is not None:
            if self.strategy == "grouped" and self.group_column is None:
                raise ValueError("split.group_column is mandatory when split.strategy == 'grouped'")
            if self.strategy != "grouped" and self.group_column is not None:
                raise ValueError(
                    f"split.group_column is forbidden when split.strategy == {self.strategy!r} "
                    f"(only allowed for 'grouped')"
                )
        return self


# --- Preprocessing -------------------------------------------------------------------------------


class MissingPolicy(BaseModel):
    """Restructured from PLAN.md §4.1's illustrative JSON, which used one dict mixing a
    `default` key, per-column keys and a `drop_rows_above` key. That shape requires the
    implicit convention "any key that is not one of two reserved names is a column name" --
    the same class of implicit string convention the plan rejected elsewhere. Three explicit
    fields carry the same information with no convention to remember."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    default: ImputationStrategy = "median"
    per_column: dict[str, ImputationStrategy] = Field(default_factory=dict)
    drop_rows_above: float | None = Field(default=None, gt=0.0, le=1.0)


class PreprocessingRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    missing: MissingPolicy = Field(default_factory=MissingPolicy)
    outliers: OutlierPolicy = "none"
    scaling: ScalingMethod = "standard"
    categorical_encoding: CategoricalEncoding = "onehot"
    max_cardinality: int = Field(default=30, ge=2)
    drop_constant: bool = True
    drop_duplicates: bool = True


# --- Modelling -----------------------------------------------------------------------------------


class Tuning(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    method: TuningMethod = "grid"
    cv_folds: int = Field(default=5, ge=2, le=20)
    max_fit_seconds: int = Field(default=300, gt=0)


class ModellingRules(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[str, ...] = ()
    """Estimator names. **Not validated here** -- the registry lives in chunk 13, and the
    available estimators depend on the running MCP server, not on this schema. Chunk 27's
    intake node validates these against `list_estimators`. Carrying a Literal here would
    make the contract the source of truth for the model registry, which is wrong."""

    inferential_model: str | None = None
    """Same reasoning as `candidates`; chunk 15 owns the registry."""

    tuning: Tuning = Field(default_factory=Tuning)
    class_weight: ClassWeight = "none"
    calibrate: bool = False

    @model_validator(mode="after")
    def _candidates_unique(self) -> ModellingRules:
        """V13: `candidates` entries unique."""
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError(
                f"modelling.candidates entries must be unique, got {self.candidates!r}"
            )
        return self


# --- Constraints ---------------------------------------------------------------------------------


class Constraints(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_runtime_seconds: int = Field(default=1800, gt=0)
    max_retune_loops: int = Field(default=2, ge=0)
    max_candidates: int = Field(default=8, ge=1, le=64)
    max_search_waves: int = Field(default=2, ge=1, le=10)
    max_total_fit_seconds: int = Field(default=1200, gt=0)
    random_seed: int = Field(default=42, ge=0)
    interpretability_required: bool = False


# --- The contract --------------------------------------------------------------------------------


def _resolved_task(contract: DataContract) -> Task | None:
    """`objective.task` if set, else derived from `target.type`, else `None`.

    This is the ONLY place the "which task is this?" question is answered. X2, X3, X10 and X11
    all call it, so the derivation cannot drift between rules.
    """
    if contract.objective.task is not None:
        return contract.objective.task
    if contract.target.type is not None:
        return TASK_FOR_TARGET_TYPE[contract.target.type]
    return None


class DataContract(BaseModel):
    """PLAN.md §4.1. Every field is optional except `target.column` (D8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    target: TargetSpec  # the only required member
    dataset: DatasetOptions = Field(default_factory=DatasetOptions)
    features: FeatureRules = Field(default_factory=FeatureRules)
    objective: Objective = Field(default_factory=Objective)
    split: SplitRules = Field(default_factory=SplitRules)
    preprocessing: PreprocessingRules = Field(default_factory=PreprocessingRules)
    modelling: ModellingRules = Field(default_factory=ModellingRules)
    constraints: Constraints = Field(default_factory=Constraints)
    notes: str = ""

    @model_validator(mode="after")
    def _task_matches_target_type(self) -> DataContract:
        """X1: `objective.task` agrees with `TASK_FOR_TARGET_TYPE[target.type]`.

        Fires only when both `objective.task` and `target.type` are set -- absent means "the
        agent will infer this", not "invalid". Must PASS when either side is None, which is
        what makes `DataContract.model_validate({"target": {"column": "x"}})` constructible.
        """
        if self.objective.task is not None and self.target.type is not None:
            expected = TASK_FOR_TARGET_TYPE[self.target.type]
            if self.objective.task != expected:
                raise ValueError(
                    f"objective.task {self.objective.task!r} disagrees with target.type "
                    f"{self.target.type!r}, which implies task {expected!r}"
                )
        return self

    @model_validator(mode="after")
    def _primary_metric_matches_task(self) -> DataContract:
        """X2: `objective.primary_metric` is in `METRICS_FOR_TASK[task]`, where `task` is
        `objective.task` or derived from `target.type` via `_resolved_task`."""
        metric = self.objective.primary_metric
        task = _resolved_task(self)
        if metric is not None and task is not None and metric not in METRICS_FOR_TASK[task]:
            raise ValueError(f"objective.primary_metric {metric!r} is not valid for task {task!r}")
        return self

    @model_validator(mode="after")
    def _secondary_metrics_match_task(self) -> DataContract:
        """X3: every `objective.secondary_metrics` entry is in the same task's metric set."""
        task = _resolved_task(self)
        if task is not None:
            for metric in self.objective.secondary_metrics:
                if metric not in METRICS_FOR_TASK[task]:
                    raise ValueError(
                        f"objective.secondary_metrics entry {metric!r} is not valid for task "
                        f"{task!r}"
                    )
        return self

    @model_validator(mode="after")
    def _stratified_requires_classification_target(self) -> DataContract:
        """X4: `split.strategy == "stratified"` requires `target.type` in
        `{binary, multiclass}`."""
        if (
            self.split.strategy == "stratified"
            and self.target.type is not None
            and self.target.type not in ("binary", "multiclass")
        ):
            raise ValueError(
                f"split.strategy == 'stratified' requires target.type in "
                f"{{'binary', 'multiclass'}}, got target.type={self.target.type!r} "
                f"(stratifying a continuous or count target is a type error, not a "
                f"strategy choice)"
            )
        return self

    @model_validator(mode="after")
    def _target_column_not_in_feature_lists(self) -> DataContract:
        """X5: `target.column` appears in none of `features.include`, `features.exclude`,
        `features.id_columns`."""
        column = self.target.column
        if self.features.include is not None and column in self.features.include:
            raise ValueError(f"target.column {column!r} must not appear in features.include")
        if column in self.features.exclude:
            raise ValueError(f"target.column {column!r} must not appear in features.exclude")
        if column in self.features.id_columns:
            raise ValueError(f"target.column {column!r} must not appear in features.id_columns")
        return self

    @model_validator(mode="after")
    def _target_column_not_in_roles(self) -> DataContract:
        """X6: `target.column` is not a key in `features.roles` -- the target's nature is
        declared by `target.type`, not by a feature role."""
        column = self.target.column
        if column in self.features.roles:
            raise ValueError(
                f"target.column {column!r} must not be a key in features.roles; the target's "
                f"nature is declared by target.type, not a feature role"
            )
        return self

    @model_validator(mode="after")
    def _time_column_not_target(self) -> DataContract:
        """X7: `split.time_column != target.column`."""
        if self.split.time_column is not None and self.split.time_column == self.target.column:
            raise ValueError(
                f"split.time_column must not equal target.column ({self.target.column!r})"
            )
        return self

    @model_validator(mode="after")
    def _group_column_not_target(self) -> DataContract:
        """X8: `split.group_column != target.column`."""
        if self.split.group_column is not None and self.split.group_column == self.target.column:
            raise ValueError(
                f"split.group_column must not equal target.column ({self.target.column!r})"
            )
        return self

    @model_validator(mode="after")
    def _target_column_not_in_missing_per_column(self) -> DataContract:
        """X9: `target.column` is not a key in `preprocessing.missing.per_column`."""
        column = self.target.column
        if column in self.preprocessing.missing.per_column:
            raise ValueError(
                f"target.column {column!r} must not be a key in preprocessing.missing.per_column"
            )
        return self

    @model_validator(mode="after")
    def _class_weight_requires_classification(self) -> DataContract:
        """X10: `modelling.class_weight == "balanced"` requires a classification task."""
        if self.modelling.class_weight == "balanced":
            task = _resolved_task(self)
            if task is not None and task != "classification":
                raise ValueError(
                    f"modelling.class_weight == 'balanced' requires a classification task, "
                    f"resolved task is {task!r}"
                )
        return self

    @model_validator(mode="after")
    def _calibrate_requires_classification(self) -> DataContract:
        """X11: `modelling.calibrate` requires a classification task."""
        if self.modelling.calibrate:
            task = _resolved_task(self)
            if task is not None and task != "classification":
                raise ValueError(
                    f"modelling.calibrate requires a classification task, resolved task is {task!r}"
                )
        return self

    @model_validator(mode="after")
    def _inference_predictors_exclude_target(self) -> DataContract:
        """X12: no `inference_goals[].predictors` entry equals `target.column`."""
        column = self.target.column
        for goal in self.objective.inference_goals:
            if column in goal.predictors:
                raise ValueError(
                    f"objective.inference_goals question {goal.question!r} has predictors "
                    f"containing target.column {column!r}"
                )
        return self

    @model_validator(mode="after")
    def _inference_predictors_not_excluded(self) -> DataContract:
        """X13: every `inference_goals[].predictors` entry is not in `features.exclude`."""
        excluded = set(self.features.exclude)
        for goal in self.objective.inference_goals:
            overlap = set(goal.predictors) & excluded
            if overlap:
                raise ValueError(
                    f"objective.inference_goals question {goal.question!r} has predictors "
                    f"{sorted(overlap)!r} in features.exclude"
                )
        return self

    @model_validator(mode="after")
    def _inference_predictors_in_include(self) -> DataContract:
        """X14: `features.include`, if set, contains every `inference_goals[].predictors`
        entry."""
        if self.features.include is not None:
            included = set(self.features.include)
            for goal in self.objective.inference_goals:
                missing = set(goal.predictors) - included
                if missing:
                    raise ValueError(
                        f"objective.inference_goals question {goal.question!r} has predictors "
                        f"{sorted(missing)!r} not in features.include"
                    )
        return self
