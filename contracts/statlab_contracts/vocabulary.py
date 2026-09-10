"""Closed vocabularies shared by the contract (chunk 03) and candidate specs (chunk 04)."""

from __future__ import annotations

from typing import Literal, get_args

TargetType = Literal["binary", "multiclass", "continuous", "count"]
Task = Literal["classification", "regression"]
ColumnRole = Literal["numeric", "categorical", "ordinal", "datetime", "boolean", "text"]

SplitStrategy = Literal["random", "stratified", "temporal", "grouped"]

ImputationStrategy = Literal["mean", "median", "most_frequent", "constant", "drop"]
OutlierPolicy = Literal["none", "clip_iqr", "winsorize", "flag_only"]
ScalingMethod = Literal["none", "standard", "minmax", "robust"]
CategoricalEncoding = Literal["onehot", "ordinal", "target", "frequency"]

ClassificationMetric = Literal[
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
    "roc_auc",
    "pr_auc",
    "brier",
    "log_loss",
]
RegressionMetric = Literal["rmse", "mae", "r2", "mape", "medae"]
Metric = ClassificationMetric | RegressionMetric

TuningMethod = Literal["none", "grid", "random"]
ClassWeight = Literal["none", "balanced"]

TASK_FOR_TARGET_TYPE: dict[TargetType, Task] = {
    "binary": "classification",
    "multiclass": "classification",
    "continuous": "regression",
    "count": "regression",
}
"""Single source of truth for the target-type <-> task relationship.

`task` is therefore *derivable* whenever `target.type` is known. It is still carried in the
contract because a user may state the task without stating the target type, and node 2 records
its own reasoning -- but the two must never disagree, which is what `_task_matches_target_type`
enforces. This is the "derive, don't duplicate" lesson applied where the duplication cannot be
removed: the check exists precisely because both may legitimately be absent.
"""

METRICS_FOR_TASK: dict[Task, frozenset[str]] = {
    "classification": frozenset(get_args(ClassificationMetric)),
    "regression": frozenset(get_args(RegressionMetric)),
}
"""Derived from the Literals via `get_args`, never hand-listed -- adding a metric to a Literal
without adding it here is impossible by construction."""
