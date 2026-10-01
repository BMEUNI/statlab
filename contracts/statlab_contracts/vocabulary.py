"""Closed vocabularies shared by the contract (chunk 03) and candidate specs (chunk 04)."""

from __future__ import annotations

from typing import Literal, get_args

TargetType = Literal["binary", "multiclass", "continuous", "count"]
Task = Literal["classification", "regression"]
ColumnRole = Literal["numeric", "categorical", "ordinal", "datetime", "boolean", "text"]

SplitStrategy = Literal["random", "stratified", "temporal", "grouped"]

SplitRole = Literal["train", "valid", "test"]
"""Which split a frame belongs to. Carried on `ArtifactMeta.split` so the D32 test-set lineage
lock keys on a closed vocabulary rather than a free-string `label` -- an implicit string
convention `WHATS_NEXT.md` forbids. Only chunk 10's split tools set it."""

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

FeatureSelectionMethod = Literal["none", "variance", "mutual_info"]

DatetimePart = Literal[
    "year",
    "month",
    "day",
    "dayofweek",
    "hour",
    "quarter",
    "weekofyear",
    "is_weekend",
]
"""Parts `add_datetime_parts` (chunk 17) can extract. Closed so a typo is a schema error."""

ParamValue = str | int | float | bool | None | tuple[str | int | float | bool | None, ...]
"""A single hyperparameter value. One level of nesting, deliberately non-recursive: sklearn
params are scalars or flat sequences, and an unbounded recursive type buys nothing while
defeating static typing (chunk 02's JsonValue lesson)."""

VariationDimension = Literal[
    "imputation",
    "outliers",
    "encoding",
    "scaling",
    "cardinality_handling",
    "derived_features",
    "feature_selection",
    "estimator",
    "hyperparameters",
    "class_weight",
    "calibration",
]
"""The axes along which one candidate may differ from the baseline (D26's
`varies_from_baseline`). A closed vocabulary rather than free strings, so the comparison table
can group by dimension and a typo cannot invent an axis."""

DIMENSION_FOR_FIELD: dict[str, VariationDimension] = {
    "preprocessing.missing": "imputation",
    "preprocessing.outliers": "outliers",
    "features.encoding": "encoding",
    "features.scaling": "scaling",
    "features.max_cardinality": "cardinality_handling",
    "features.interactions": "derived_features",
    "features.datetime_parts": "derived_features",
    "features.binned": "derived_features",
    "features.selection": "feature_selection",
    "estimator.name": "estimator",
    "estimator.params": "hyperparameters",
    "estimator.class_weight": "class_weight",
    "estimator.calibrate": "calibration",
}
"""Maps every hashed leaf field to the dimension it varies. Used to verify a candidate's
declared `varies_from_baseline` against the actual diff (chunk 04 §3.4). Three fields map to
`derived_features` because interactions, datetime parts and binning are one axis from a
reader's point of view -- "this candidate engineered features" -- even though they are three
mechanisms."""
