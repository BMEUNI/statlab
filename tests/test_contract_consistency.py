"""Tests for the 28 consistency rules (V1-V14, X1-X14) and D1-D6, per
plan/03_data_contract.md §3.4-3.5 and §4.4.

Every failing case is built by taking `full_contract_dict` and changing exactly one thing, then
calling `DataContract.model_validate` -- never a fresh hand-written minimal dict, so a failure
that fires for an unrelated reason cannot pass. Every failing case asserts on MESSAGE CONTENT via
`pytest.raises(ValidationError, match=...)`, not merely the exception type.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from statlab_contracts.contract import DataContract, _resolved_task

# --- V1-V14: intra-model rules --------------------------------------------------------------


def test_v1_positive_class_with_binary_type_passes(full_contract_dict: dict[str, Any]) -> None:
    """V1 pass: full.json already has positive_class=1 with type='binary'."""
    DataContract.model_validate(full_contract_dict)


def test_v1_positive_class_without_binary_type_fails(full_contract_dict: dict[str, Any]) -> None:
    """V1 fail."""
    updated = {**full_contract_dict["target"], "type": "multiclass", "class_order": ["0", "1"]}
    with pytest.raises(ValidationError, match="positive_class"):
        DataContract.model_validate({**full_contract_dict, "target": updated})


def test_v2_class_order_with_multiclass_type_passes(full_contract_dict: dict[str, Any]) -> None:
    """V2 pass."""
    updated = {
        "column": "churned",
        "type": "multiclass",
        "positive_class": None,
        "class_order": ["low", "medium", "high"],
    }
    DataContract.model_validate({**full_contract_dict, "target": updated})


def test_v2_class_order_without_multiclass_type_fails(
    full_contract_dict: dict[str, Any],
) -> None:
    """V2 fail: type stays 'binary', but class_order is set."""
    updated = {**full_contract_dict["target"], "class_order": ["a", "b"]}
    with pytest.raises(ValidationError, match="class_order"):
        DataContract.model_validate({**full_contract_dict, "target": updated})


def test_v3_class_order_unique_non_empty_passes(full_contract_dict: dict[str, Any]) -> None:
    """V3 pass."""
    updated = {
        "column": "churned",
        "type": "multiclass",
        "positive_class": None,
        "class_order": ["low", "medium", "high"],
    }
    DataContract.model_validate({**full_contract_dict, "target": updated})


def test_v3_class_order_duplicate_fails(full_contract_dict: dict[str, Any]) -> None:
    """V3 fail: duplicate entries."""
    updated = {
        "column": "churned",
        "type": "multiclass",
        "positive_class": None,
        "class_order": ["low", "low"],
    }
    with pytest.raises(ValidationError, match="unique"):
        DataContract.model_validate({**full_contract_dict, "target": updated})


def test_v3_class_order_empty_list_fails(full_contract_dict: dict[str, Any]) -> None:
    """V3 fail (other branch): class_order explicitly set to an empty list."""
    updated: dict[str, object] = {
        "column": "churned",
        "type": "multiclass",
        "positive_class": None,
        "class_order": [],
    }
    with pytest.raises(ValidationError, match="class_order must not be empty"):
        DataContract.model_validate({**full_contract_dict, "target": updated})


def test_v4_include_exclude_disjoint_passes(full_contract_dict: dict[str, Any]) -> None:
    """V4 pass: full.json's include and exclude do not overlap."""
    DataContract.model_validate(full_contract_dict)


def test_v4_include_exclude_overlap_fails(full_contract_dict: dict[str, Any]) -> None:
    """V4 fail."""
    updated = {**full_contract_dict["features"], "exclude": ["age"]}
    with pytest.raises(ValidationError, match=r"include and features\.exclude overlap"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_v5_id_columns_disjoint_from_include_passes(full_contract_dict: dict[str, Any]) -> None:
    """V5 pass."""
    DataContract.model_validate(full_contract_dict)


def test_v5_id_columns_overlap_include_fails(full_contract_dict: dict[str, Any]) -> None:
    """V5 fail."""
    updated = {**full_contract_dict["features"], "id_columns": ["age"]}
    with pytest.raises(ValidationError, match=r"id_columns and features\.include overlap"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_v6_ordinal_levels_key_matches_ordinal_role_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """V6 pass: full.json's roles['satisfaction'] == 'ordinal'."""
    DataContract.model_validate(full_contract_dict)


def test_v6_ordinal_levels_key_role_mismatch_fails(full_contract_dict: dict[str, Any]) -> None:
    """V6 fail: roles['satisfaction'] changed to 'numeric', but it is still a key in
    ordinal_levels."""
    updated_roles = {**full_contract_dict["features"]["roles"], "satisfaction": "numeric"}
    updated = {**full_contract_dict["features"], "roles": updated_roles}
    with pytest.raises(ValidationError, match="ordinal_levels"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_v7_ordinal_levels_two_unique_entries_passes(full_contract_dict: dict[str, Any]) -> None:
    """V7 pass."""
    DataContract.model_validate(full_contract_dict)


def test_v7_ordinal_levels_one_entry_fails(full_contract_dict: dict[str, Any]) -> None:
    """V7 fail."""
    updated_levels = {"satisfaction": ["low"]}
    updated = {**full_contract_dict["features"], "ordinal_levels": updated_levels}
    with pytest.raises(ValidationError, match="ordinal_levels"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_v8_primary_metric_not_in_secondary_passes(full_contract_dict: dict[str, Any]) -> None:
    """V8 pass: full.json's primary is 'roc_auc', secondary does not include it."""
    DataContract.model_validate(full_contract_dict)


def test_v8_primary_metric_repeated_in_secondary_fails(
    full_contract_dict: dict[str, Any],
) -> None:
    """V8 fail."""
    updated = {**full_contract_dict["objective"], "secondary_metrics": ["roc_auc"]}
    with pytest.raises(ValidationError, match="primary_metric"):
        DataContract.model_validate({**full_contract_dict, "objective": updated})


def test_v9_secondary_metrics_unique_passes(full_contract_dict: dict[str, Any]) -> None:
    """V9 pass."""
    DataContract.model_validate(full_contract_dict)


def test_v9_secondary_metrics_duplicate_fails(full_contract_dict: dict[str, Any]) -> None:
    """V9 fail."""
    updated = {**full_contract_dict["objective"], "secondary_metrics": ["f1", "f1"]}
    with pytest.raises(ValidationError, match="secondary_metrics"):
        DataContract.model_validate({**full_contract_dict, "objective": updated})


def test_v10_split_sizes_leave_room_for_train_passes(full_contract_dict: dict[str, Any]) -> None:
    """V10 pass: 0.2 + 0.2 == 0.4 < 0.9."""
    DataContract.model_validate(full_contract_dict)


def test_v10_split_sizes_too_large_fails(full_contract_dict: dict[str, Any]) -> None:
    """V10 fail."""
    updated = {**full_contract_dict["split"], "test_size": 0.5, "valid_size": 0.5}
    with pytest.raises(ValidationError, match=r"test_size \+ split\.valid_size"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v11_time_column_iff_temporal_passes(full_contract_dict: dict[str, Any]) -> None:
    """V11 pass: strategy='temporal' with time_column set."""
    updated = {
        **full_contract_dict["split"],
        "strategy": "temporal",
        "time_column": "signup_date",
        "group_column": None,
    }
    DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v11_temporal_without_time_column_fails(full_contract_dict: dict[str, Any]) -> None:
    """V11 fail: temporal strategy but no time_column."""
    updated = {
        **full_contract_dict["split"],
        "strategy": "temporal",
        "group_column": None,
    }
    with pytest.raises(ValidationError, match="time_column"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v11_time_column_with_non_temporal_strategy_fails(
    full_contract_dict: dict[str, Any],
) -> None:
    """V11 fail (other direction): time_column set but strategy stays 'stratified'."""
    updated = {**full_contract_dict["split"], "time_column": "signup_date"}
    with pytest.raises(ValidationError, match="time_column"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v12_group_column_iff_grouped_passes(full_contract_dict: dict[str, Any]) -> None:
    """V12 pass: strategy='grouped' with group_column set."""
    updated = {
        **full_contract_dict["split"],
        "strategy": "grouped",
        "group_column": "customer_id",
        "time_column": None,
    }
    DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v12_grouped_without_group_column_fails(full_contract_dict: dict[str, Any]) -> None:
    """V12 fail."""
    updated = {**full_contract_dict["split"], "strategy": "grouped", "time_column": None}
    with pytest.raises(ValidationError, match="group_column"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v12_group_column_with_non_grouped_strategy_fails(
    full_contract_dict: dict[str, Any],
) -> None:
    """V12 fail (other direction)."""
    updated = {**full_contract_dict["split"], "group_column": "customer_id"}
    with pytest.raises(ValidationError, match="group_column"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_v13_candidates_unique_passes(full_contract_dict: dict[str, Any]) -> None:
    """V13 pass."""
    DataContract.model_validate(full_contract_dict)


def test_v13_candidates_duplicate_fails(full_contract_dict: dict[str, Any]) -> None:
    """V13 fail."""
    updated = {
        **full_contract_dict["modelling"],
        "candidates": ["logistic_regression", "logistic_regression"],
    }
    with pytest.raises(ValidationError, match="candidates"):
        DataContract.model_validate({**full_contract_dict, "modelling": updated})


def test_v14_per_column_valid_strategies_passes(full_contract_dict: dict[str, Any]) -> None:
    """V14 pass: full.json's per_column={'plan': 'most_frequent'} is a valid strategy."""
    DataContract.model_validate(full_contract_dict)


def test_v14_per_column_invalid_strategy_fails(full_contract_dict: dict[str, Any]) -> None:
    """V14 fail: enforced by field type (dict[str, ImputationStrategy])."""
    updated_missing = {
        **full_contract_dict["preprocessing"]["missing"],
        "per_column": {"plan": "backfill"},
    }
    updated = {**full_contract_dict["preprocessing"], "missing": updated_missing}
    with pytest.raises(ValidationError, match="per_column"):
        DataContract.model_validate({**full_contract_dict, "preprocessing": updated})


# --- X1-X14: cross-group rules on DataContract ------------------------------------------------


def test_x1_task_matches_target_type_passes(full_contract_dict: dict[str, Any]) -> None:
    """X1 pass: full.json's task='classification' agrees with target.type='binary'."""
    DataContract.model_validate(full_contract_dict)


def test_x1_task_disagrees_with_target_type_fails(full_contract_dict: dict[str, Any]) -> None:
    """X1 fail: task says regression but target.type says binary."""
    updated_objective = {
        **full_contract_dict["objective"],
        "task": "regression",
        "primary_metric": "rmse",
        "secondary_metrics": [],
    }
    with pytest.raises(ValidationError, match=r"objective\.task.*target\.type"):
        DataContract.model_validate({**full_contract_dict, "objective": updated_objective})


def test_x2_primary_metric_matches_task_passes(full_contract_dict: dict[str, Any]) -> None:
    """X2 pass: full.json's primary_metric='roc_auc' is valid for classification."""
    DataContract.model_validate(full_contract_dict)


def test_x2_primary_metric_wrong_for_task_fails(full_contract_dict: dict[str, Any]) -> None:
    """X2 fail: primary_metric='rmse' (a regression metric) with a classification task."""
    updated = {**full_contract_dict["objective"], "primary_metric": "rmse"}
    with pytest.raises(ValidationError, match=r"primary_metric.*not valid for task"):
        DataContract.model_validate({**full_contract_dict, "objective": updated})


def test_x3_secondary_metrics_match_task_passes(full_contract_dict: dict[str, Any]) -> None:
    """X3 pass."""
    DataContract.model_validate(full_contract_dict)


def test_x3_secondary_metric_wrong_for_task_fails(full_contract_dict: dict[str, Any]) -> None:
    """X3 fail: 'rmse' (regression) added to secondary_metrics under a classification task."""
    updated = {**full_contract_dict["objective"], "secondary_metrics": ["f1", "rmse"]}
    with pytest.raises(ValidationError, match="secondary_metrics entry 'rmse'"):
        DataContract.model_validate({**full_contract_dict, "objective": updated})


def test_x4_stratified_with_classification_target_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """X4 pass: strategy='stratified' with target.type='binary'."""
    DataContract.model_validate(full_contract_dict)


def test_x4_stratified_with_continuous_target_fails(full_contract_dict: dict[str, Any]) -> None:
    """X4 fail: stratifying a continuous target is a type error."""
    updated_target = {**full_contract_dict["target"], "type": "continuous", "positive_class": None}
    updated_objective = {
        **full_contract_dict["objective"],
        "task": "regression",
        "primary_metric": "rmse",
        "secondary_metrics": [],
    }
    with pytest.raises(ValidationError, match=r"stratified.*target\.type"):
        DataContract.model_validate(
            {**full_contract_dict, "target": updated_target, "objective": updated_objective}
        )


def test_x5_target_column_absent_from_feature_lists_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """X5 pass."""
    DataContract.model_validate(full_contract_dict)


def test_x5_target_column_in_exclude_fails(full_contract_dict: dict[str, Any]) -> None:
    """X5 fail."""
    updated = {**full_contract_dict["features"], "exclude": ["churned"]}
    with pytest.raises(ValidationError, match=r"target\.column 'churned'.*features\.exclude"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_x5_target_column_in_include_fails(full_contract_dict: dict[str, Any]) -> None:
    """X5 fail (include branch)."""
    updated = {**full_contract_dict["features"], "include": ["churned"]}
    with pytest.raises(ValidationError, match=r"target\.column 'churned'.*features\.include"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_x5_target_column_in_id_columns_fails(full_contract_dict: dict[str, Any]) -> None:
    """X5 fail (id_columns branch)."""
    updated = {**full_contract_dict["features"], "id_columns": ["churned"]}
    with pytest.raises(ValidationError, match=r"target\.column 'churned'.*features\.id_columns"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_x6_target_column_absent_from_roles_passes(full_contract_dict: dict[str, Any]) -> None:
    """X6 pass."""
    DataContract.model_validate(full_contract_dict)


def test_x6_target_column_in_roles_fails(full_contract_dict: dict[str, Any]) -> None:
    """X6 fail."""
    updated_roles = {**full_contract_dict["features"]["roles"], "churned": "categorical"}
    updated = {**full_contract_dict["features"], "roles": updated_roles}
    with pytest.raises(ValidationError, match=r"target\.column 'churned'.*features\.roles"):
        DataContract.model_validate({**full_contract_dict, "features": updated})


def test_x7_time_column_not_target_passes(full_contract_dict: dict[str, Any]) -> None:
    """X7 pass: time_column is None in full.json."""
    DataContract.model_validate(full_contract_dict)


def test_x7_time_column_equals_target_fails(full_contract_dict: dict[str, Any]) -> None:
    """X7 fail."""
    updated = {**full_contract_dict["split"], "strategy": "temporal", "time_column": "churned"}
    with pytest.raises(ValidationError, match=r"time_column must not equal target\.column"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_x8_group_column_not_target_passes(full_contract_dict: dict[str, Any]) -> None:
    """X8 pass: group_column is None in full.json."""
    DataContract.model_validate(full_contract_dict)


def test_x8_group_column_equals_target_fails(full_contract_dict: dict[str, Any]) -> None:
    """X8 fail."""
    updated = {**full_contract_dict["split"], "strategy": "grouped", "group_column": "churned"}
    with pytest.raises(ValidationError, match=r"group_column must not equal target\.column"):
        DataContract.model_validate({**full_contract_dict, "split": updated})


def test_x9_target_column_absent_from_missing_per_column_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """X9 pass."""
    DataContract.model_validate(full_contract_dict)


def test_x9_target_column_in_missing_per_column_fails(full_contract_dict: dict[str, Any]) -> None:
    """X9 fail."""
    updated_missing = {
        **full_contract_dict["preprocessing"]["missing"],
        "per_column": {"churned": "most_frequent"},
    }
    updated = {**full_contract_dict["preprocessing"], "missing": updated_missing}
    with pytest.raises(
        ValidationError, match=r"target\.column 'churned'.*preprocessing\.missing\.per_column"
    ):
        DataContract.model_validate({**full_contract_dict, "preprocessing": updated})


def test_x10_balanced_class_weight_with_classification_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """X10 pass: full.json's class_weight='balanced' with a classification task."""
    DataContract.model_validate(full_contract_dict)


def test_x10_balanced_class_weight_with_regression_fails(
    full_contract_dict: dict[str, Any],
) -> None:
    """X10 fail."""
    updated_target = {**full_contract_dict["target"], "type": "continuous", "positive_class": None}
    updated_objective = {
        **full_contract_dict["objective"],
        "task": "regression",
        "primary_metric": "rmse",
        "secondary_metrics": [],
    }
    updated_split = {**full_contract_dict["split"], "strategy": "random"}
    with pytest.raises(ValidationError, match=r"class_weight == 'balanced'.*classification"):
        DataContract.model_validate(
            {
                **full_contract_dict,
                "target": updated_target,
                "objective": updated_objective,
                "split": updated_split,
            }
        )


def test_x11_calibrate_with_classification_passes(full_contract_dict: dict[str, Any]) -> None:
    """X11 pass: full.json's calibrate=True with a classification task."""
    DataContract.model_validate(full_contract_dict)


def test_x11_calibrate_with_regression_fails(full_contract_dict: dict[str, Any]) -> None:
    """X11 fail."""
    updated_target = {**full_contract_dict["target"], "type": "continuous", "positive_class": None}
    updated_objective = {
        **full_contract_dict["objective"],
        "task": "regression",
        "primary_metric": "rmse",
        "secondary_metrics": [],
    }
    updated_split = {**full_contract_dict["split"], "strategy": "random"}
    updated_modelling = {**full_contract_dict["modelling"], "class_weight": "none"}
    with pytest.raises(ValidationError, match="calibrate requires a classification task"):
        DataContract.model_validate(
            {
                **full_contract_dict,
                "target": updated_target,
                "objective": updated_objective,
                "split": updated_split,
                "modelling": updated_modelling,
            }
        )


def test_x12_inference_predictors_exclude_target_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """X12 pass: full.json's inference goal predictors=['plan'], not the target."""
    DataContract.model_validate(full_contract_dict)


def test_x12_inference_predictor_equals_target_fails(full_contract_dict: dict[str, Any]) -> None:
    """X12 fail."""
    updated_goals = [
        {**full_contract_dict["objective"]["inference_goals"][0], "predictors": ["churned"]}
    ]
    updated_objective = {**full_contract_dict["objective"], "inference_goals": updated_goals}
    with pytest.raises(ValidationError, match=r"predictors containing target\.column 'churned'"):
        DataContract.model_validate({**full_contract_dict, "objective": updated_objective})


def test_x13_inference_predictors_not_excluded_passes(
    full_contract_dict: dict[str, Any],
) -> None:
    """X13 pass: full.json's predictor 'plan' is not in exclude=['notes_free_text']."""
    DataContract.model_validate(full_contract_dict)


def test_x13_inference_predictor_in_exclude_fails(full_contract_dict: dict[str, Any]) -> None:
    """X13 fail. `plan` must also be dropped from `include` in this same edit, or V4
    (include/exclude disjoint) would fire first for an unrelated reason."""
    updated_features = {
        **full_contract_dict["features"],
        "include": ["age", "signup_date", "satisfaction"],
        "exclude": ["notes_free_text", "plan"],
    }
    with pytest.raises(ValidationError, match=r"predictors \['plan'\] in features\.exclude"):
        DataContract.model_validate({**full_contract_dict, "features": updated_features})


def test_x14_inference_predictors_in_include_passes(full_contract_dict: dict[str, Any]) -> None:
    """X14 pass: full.json's include contains 'plan', the inference goal's predictor."""
    DataContract.model_validate(full_contract_dict)


def test_x14_inference_predictor_not_in_include_fails(full_contract_dict: dict[str, Any]) -> None:
    """X14 fail: include is narrowed to exclude 'plan', but the inference goal still asks
    about it."""
    updated_features = {
        **full_contract_dict["features"],
        "include": ["age", "signup_date", "satisfaction"],
    }
    with pytest.raises(ValidationError, match=r"predictors \['plan'\] not in features\.include"):
        DataContract.model_validate({**full_contract_dict, "features": updated_features})


# --- D1-D6 -----------------------------------------------------------------------------------


def test_d1_minimal_contract_passes_all_cross_group_rules(
    minimal_contract: DataContract,
) -> None:
    """D1: absent fields do not trigger cross-group rules -- the minimal contract passes all
    of X1-X14 (it already constructed, which is the strongest possible assertion of this)."""
    assert minimal_contract.target.column == "churned"


def test_d2_task_alone_without_target_type_is_valid() -> None:
    """D2: objective.task set, no target.type -- X1 does not fire."""
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "objective": {"task": "regression"}}
    )
    assert contract.objective.task == "regression"
    assert contract.target.type is None


def test_d3_target_type_alone_still_constrains_metric_via_derived_task() -> None:
    """D3: target.type set, no objective.task -- valid, and X2 still constrains the metric
    via the derived task."""
    contract = DataContract.model_validate(
        {
            "target": {"column": "y", "type": "continuous"},
            "objective": {"primary_metric": "rmse"},
        }
    )
    assert contract.objective.task is None
    assert _resolved_task(contract) == "regression"

    with pytest.raises(ValidationError, match="primary_metric 'roc_auc' is not valid"):
        DataContract.model_validate(
            {
                "target": {"column": "y", "type": "continuous"},
                "objective": {"primary_metric": "roc_auc"},
            }
        )


def test_d4_resolved_task_prefers_explicit_over_derived(
    full_contract: DataContract,
) -> None:
    """D4: when both are present and equal, _resolved_task returns the explicit value."""
    assert full_contract.objective.task == "classification"
    assert full_contract.target.type == "binary"
    assert _resolved_task(full_contract) == "classification"


def test_d5_resolved_task_none_when_neither_set(minimal_contract: DataContract) -> None:
    """D5."""
    assert minimal_contract.objective.task is None
    assert minimal_contract.target.type is None
    assert _resolved_task(minimal_contract) is None


def test_d6_one_deviation_produces_one_error(full_contract_dict: dict[str, Any]) -> None:
    """D6: changing a single field in full.json produces exactly one validation error, so
    messages stay diagnosable."""
    updated = {**full_contract_dict["objective"], "secondary_metrics": ["roc_auc"]}
    with pytest.raises(ValidationError) as exc_info:
        DataContract.model_validate({**full_contract_dict, "objective": updated})
    assert exc_info.value.error_count() == 1
