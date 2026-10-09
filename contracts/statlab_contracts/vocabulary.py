"""Closed vocabularies shared by the contract (chunk 03) and candidate specs (chunk 04)."""

from __future__ import annotations

import math
from collections.abc import Mapping
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

# Owned by the tools: n_jobs (D17), random_state (D34e).
RESERVED_ESTIMATOR_PARAMS: frozenset[str] = frozenset({"n_jobs", "random_state"})

TunableKind = Literal[
    "positive_number",
    "nonnegative_number",
    "positive_int",
    "positive_int_or_none",
    "int_at_least_2",
    "int_at_least_2_or_none",
    "bool",
    "unit_interval",
    "shrinkage",
    "max_features",
]

TUNABLE_PARAM_KINDS: dict[str, TunableKind] = {
    "C": "positive_number",
    "tol": "positive_number",
    "max_iter": "positive_int",
    "fit_intercept": "bool",
    "positive": "bool",
    "alpha": "nonnegative_number",
    "n_estimators": "positive_int",
    "max_depth": "positive_int_or_none",
    "min_samples_split": "int_at_least_2",
    "min_samples_leaf": "positive_int",
    "max_features": "max_features",
    "bootstrap": "bool",
    "learning_rate": "positive_number",
    "max_leaf_nodes": "int_at_least_2_or_none",
    "l2_regularization": "nonnegative_number",
    "var_smoothing": "positive_number",
    "l1_ratio": "unit_interval",
    "shrinkage": "shrinkage",
}

# v1 tunable hyperparameters per registry estimator; combo-prone ones (penalty/solver/criterion)
# are excluded. tests/test_registry.py proves each is a real get_params() key.
TUNABLE_PARAMS: dict[str, frozenset[str]] = {
    "logistic_regression": frozenset({"C", "tol", "max_iter", "fit_intercept"}),
    "linear_regression": frozenset({"fit_intercept", "positive"}),
    "ridge": frozenset({"alpha", "fit_intercept", "tol"}),
    "random_forest_classifier": frozenset(
        {
            "n_estimators",
            "max_depth",
            "min_samples_split",
            "min_samples_leaf",
            "max_features",
            "bootstrap",
        }
    ),
    "random_forest_regressor": frozenset(
        {
            "n_estimators",
            "max_depth",
            "min_samples_split",
            "min_samples_leaf",
            "max_features",
            "bootstrap",
        }
    ),
    "hist_gb_classifier": frozenset(
        {
            "learning_rate",
            "max_iter",
            "max_leaf_nodes",
            "max_depth",
            "min_samples_leaf",
            "l2_regularization",
        }
    ),
    "hist_gb_regressor": frozenset(
        {
            "learning_rate",
            "max_iter",
            "max_leaf_nodes",
            "max_depth",
            "min_samples_leaf",
            "l2_regularization",
        }
    ),
    "gaussian_nb": frozenset({"var_smoothing"}),
    "lda": frozenset({"shrinkage"}),
    "extra_trees_classifier": frozenset(
        {
            "n_estimators",
            "max_depth",
            "min_samples_split",
            "min_samples_leaf",
            "max_features",
            "bootstrap",
        }
    ),
    "extra_trees_regressor": frozenset(
        {
            "n_estimators",
            "max_depth",
            "min_samples_split",
            "min_samples_leaf",
            "max_features",
            "bootstrap",
        }
    ),
    "decision_tree_classifier": frozenset(
        {"max_depth", "min_samples_split", "min_samples_leaf", "max_features", "max_leaf_nodes"}
    ),
    "decision_tree_regressor": frozenset(
        {"max_depth", "min_samples_split", "min_samples_leaf", "max_features", "max_leaf_nodes"}
    ),
    "lasso": frozenset({"alpha", "fit_intercept", "tol", "max_iter"}),
    "elastic_net": frozenset({"alpha", "l1_ratio", "fit_intercept", "tol", "max_iter"}),
}


def _number_at_least(value: ParamValue, floor: float, strict: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and (value > floor if strict else value >= floor)


def _int_at_least(value: ParamValue, floor: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= floor


def _unit(value: ParamValue) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def _matches_kind(value: ParamValue, kind: TunableKind) -> bool:
    match kind:
        case "positive_number":
            return _number_at_least(value, 0, True)
        case "nonnegative_number":
            return _number_at_least(value, 0, False)
        case "positive_int":
            return _int_at_least(value, 1)
        case "positive_int_or_none":
            return value is None or _int_at_least(value, 1)
        case "int_at_least_2":
            return _int_at_least(value, 2)
        case "int_at_least_2_or_none":
            return value is None or _int_at_least(value, 2)
        case "bool":
            return isinstance(value, bool)
        case "unit_interval":
            return _unit(value)
        case "shrinkage":
            return value is None or value == "auto" or _unit(value)
        case _:
            return (
                value is None
                or (isinstance(value, str) and value in ("sqrt", "log2"))
                or _int_at_least(value, 1)
                or (isinstance(value, float) and math.isfinite(value) and 0 < value <= 1)
            )


def tunable_param_errors(
    params: Mapping[str, ParamValue], allowed: frozenset[str] | None
) -> list[str]:
    errors: list[str] = []
    for key in sorted(params):
        value = params[key]
        if key not in TUNABLE_PARAM_KINDS or (allowed is not None and key not in allowed):
            options = sorted(allowed if allowed is not None else TUNABLE_PARAM_KINDS)
            errors.append(f"{key!r} is not a tunable parameter here; allowed: {options}")
        elif isinstance(value, tuple):
            errors.append(
                f"{key}={value!r} is a sequence; a parameter takes one value "
                "(tuning grids are chosen at model_training as param_grid)"
            )
        elif not _matches_kind(value, TUNABLE_PARAM_KINDS[key]):
            kind = TUNABLE_PARAM_KINDS[key].replace("_", " ")
            errors.append(f"{key}={value!r} is not a valid {kind}")
    return errors


MAX_GRID_POINTS = 50
"""Largest tuning grid, in combinations of values (chunk 58; enforced in decisions and by
`tune_estimator`)."""


def tunable_grid_errors(grid: Mapping[str, tuple[ParamValue, ...]]) -> list[str]:
    errors: list[str] = []
    for key in sorted(grid):
        for value in grid[key]:
            errors.extend(tunable_param_errors({key: value}, None))
    points = math.prod(len(values) for values in grid.values())
    if grid and points > MAX_GRID_POINTS:
        errors.append(
            f"param_grid has {points} combinations; use at most {MAX_GRID_POINTS} "
            "(fewer values per parameter)"
        )
    return list(dict.fromkeys(errors))
