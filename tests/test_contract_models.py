"""Tests for statlab_contracts.contract: defaults, C1-C13, field bounds, enum values.

Per plan/03_data_contract.md §4.3. Every negative test uses `Model.model_validate({...})`,
never a deliberately-invalid kwarg -- `init_typed = true` type-checks constructor kwargs, so a
wrong-typed kwarg would be a mypy error, not a runtime ValidationError.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from statlab_contracts.contract import (
    Constraints,
    DataContract,
    DatasetOptions,
    FeatureRules,
    InferenceGoal,
    MissingPolicy,
    ModellingRules,
    Objective,
    PreprocessingRules,
    SplitRules,
    TargetSpec,
    Tuning,
)

# --- Defaults, one test per model (§3.3) ------------------------------------------------------


def test_dataset_options_defaults() -> None:
    opts = DatasetOptions()
    assert opts.delimiter == ","
    assert opts.encoding == "utf-8"
    assert opts.decimal == "."
    assert opts.na_values == ("", "NA", "N/A", "null", "NULL")
    assert opts.header_row == 0


def test_target_spec_defaults() -> None:
    target = TargetSpec(column="y")
    assert target.type is None
    assert target.positive_class is None
    assert target.class_order is None


def test_feature_rules_defaults() -> None:
    features = FeatureRules()
    assert features.include is None
    assert features.exclude == ()
    assert features.id_columns == ()
    assert features.roles == {}
    assert features.ordinal_levels == {}


def test_inference_goal_defaults() -> None:
    goal = InferenceGoal(question="q", predictors=("x",))
    assert goal.alpha == 0.05


def test_objective_defaults() -> None:
    objective = Objective()
    assert objective.task is None
    assert objective.primary_metric is None
    assert objective.secondary_metrics == ()
    assert objective.success_threshold is None
    assert objective.inference_goals == ()


def test_split_rules_defaults() -> None:
    split = SplitRules()
    assert split.strategy is None
    assert split.test_size == 0.2
    assert split.valid_size == 0.2
    assert split.time_column is None
    assert split.group_column is None
    assert split.seed == 42


def test_missing_policy_defaults() -> None:
    missing = MissingPolicy()
    assert missing.default == "median"
    assert missing.per_column == {}
    assert missing.drop_rows_above is None


def test_preprocessing_rules_defaults() -> None:
    preprocessing = PreprocessingRules()
    assert preprocessing.missing == MissingPolicy()
    assert preprocessing.outliers == "none"
    assert preprocessing.scaling == "standard"
    assert preprocessing.categorical_encoding == "onehot"
    assert preprocessing.max_cardinality == 30
    assert preprocessing.drop_constant is True
    assert preprocessing.drop_duplicates is True


def test_tuning_defaults() -> None:
    tuning = Tuning()
    assert tuning.method == "grid"
    assert tuning.cv_folds == 5
    assert tuning.max_fit_seconds == 300


def test_modelling_rules_defaults() -> None:
    modelling = ModellingRules()
    assert modelling.candidates == ()
    assert modelling.inferential_model is None
    assert modelling.tuning == Tuning()
    assert modelling.class_weight == "none"
    assert modelling.calibrate is False


def test_constraints_defaults() -> None:
    constraints = Constraints()
    assert constraints.max_runtime_seconds == 1800
    assert constraints.max_retune_loops == 2
    assert constraints.max_candidates == 8
    assert constraints.max_search_waves == 2
    assert constraints.max_total_fit_seconds == 1200
    assert constraints.random_seed == 42
    assert constraints.interpretability_required is False


# --- C1-C13 -------------------------------------------------------------------------------------


def test_minimal_contract_constructs(minimal_contract: DataContract) -> None:
    """C1: §3.6 -- the minimum contract must always construct, and every default must match
    §3.3 exactly. A default silently changing is a behaviour change to every run that omits
    the field."""
    c = minimal_contract
    assert c.version == 1
    assert c.target.column == "churned"
    assert c.target.type is None
    assert c.target.positive_class is None
    assert c.target.class_order is None
    assert c.dataset == DatasetOptions()
    assert c.features == FeatureRules()
    assert c.objective == Objective()
    assert c.split == SplitRules()
    assert c.preprocessing == PreprocessingRules()
    assert c.modelling == ModellingRules()
    assert c.constraints == Constraints()
    assert c.notes == ""


def test_full_json_constructs(full_contract: DataContract) -> None:
    """C2."""
    assert full_contract.target.column == "churned"


def test_minimal_contract_round_trip(minimal_contract: DataContract) -> None:
    """C3."""
    restored = DataContract.model_validate_json(minimal_contract.model_dump_json())
    assert restored == minimal_contract


def test_full_contract_round_trip(full_contract: DataContract) -> None:
    """C3."""
    restored = DataContract.model_validate_json(full_contract.model_dump_json())
    assert restored == full_contract


def test_version_defaults_to_one(minimal_contract: DataContract) -> None:
    """C4."""
    assert minimal_contract.version == 1


def test_version_two_rejected(full_contract_dict: dict[str, Any]) -> None:
    """C5: a future contract version must fail loudly, not be reinterpreted."""
    with pytest.raises(ValidationError, match="version"):
        DataContract.model_validate({**full_contract_dict, "version": 2})


def test_missing_target_rejected() -> None:
    """C6."""
    with pytest.raises(ValidationError, match="target"):
        DataContract.model_validate({})


def test_target_without_column_rejected() -> None:
    """C7."""
    with pytest.raises(ValidationError, match="column"):
        DataContract.model_validate({"target": {}})


def test_target_column_empty_rejected() -> None:
    """C8."""
    with pytest.raises(ValidationError):
        DataContract.model_validate({"target": {"column": ""}})


def test_unknown_top_level_key_rejected(full_contract_dict: dict[str, Any]) -> None:
    """C9: extra_forbidden."""
    with pytest.raises(ValidationError, match=r"extra_forbidden|Extra inputs"):
        DataContract.model_validate({**full_contract_dict, "targert": {"column": "x"}})


_NESTED_GROUPS = (
    "dataset",
    "target",
    "features",
    "objective",
    "split",
    "preprocessing",
    "modelling",
    "constraints",
)


@pytest.mark.parametrize("group", _NESTED_GROUPS)
def test_unknown_nested_key_rejected(full_contract_dict: dict[str, Any], group: str) -> None:
    """C10: parametrised over each of the 9 groups (8 nested BaseModel groups here; `notes`
    is a plain str field with no nested keys of its own, so it is not included)."""
    updated = {**full_contract_dict[group], "bogus": 1}
    with pytest.raises(ValidationError):
        DataContract.model_validate({**full_contract_dict, group: updated})


_ALL_MODELS: tuple[type[BaseModel], ...] = (
    DataContract,
    DatasetOptions,
    TargetSpec,
    FeatureRules,
    InferenceGoal,
    Objective,
    SplitRules,
    MissingPolicy,
    PreprocessingRules,
    Tuning,
    ModellingRules,
    Constraints,
)


@pytest.mark.parametrize("model_cls", _ALL_MODELS)
def test_every_model_frozen(
    model_cls: type[BaseModel], full_contract: DataContract, minimal_contract: DataContract
) -> None:
    """C11: mutation raises on every model. Uses whichever already-constructed instance of
    each model type is reachable from the two fixtures."""
    instance: object
    if model_cls is DataContract:
        instance = full_contract
        attr = "notes"
    elif model_cls is DatasetOptions:
        instance = full_contract.dataset
        attr = "delimiter"
    elif model_cls is TargetSpec:
        instance = full_contract.target
        attr = "column"
    elif model_cls is FeatureRules:
        instance = full_contract.features
        attr = "exclude"
    elif model_cls is InferenceGoal:
        instance = full_contract.objective.inference_goals[0]
        attr = "question"
    elif model_cls is Objective:
        instance = full_contract.objective
        attr = "task"
    elif model_cls is SplitRules:
        instance = full_contract.split
        attr = "seed"
    elif model_cls is MissingPolicy:
        instance = full_contract.preprocessing.missing
        attr = "default"
    elif model_cls is PreprocessingRules:
        instance = full_contract.preprocessing
        attr = "scaling"
    elif model_cls is Tuning:
        instance = full_contract.modelling.tuning
        attr = "method"
    elif model_cls is ModellingRules:
        instance = full_contract.modelling
        attr = "class_weight"
    else:
        instance = full_contract.constraints
        attr = "random_seed"
    with pytest.raises(ValidationError):
        setattr(instance, attr, getattr(instance, attr))


def test_collections_are_tuples_not_lists(full_contract: DataContract) -> None:
    """C12."""
    assert isinstance(full_contract.dataset.na_values, tuple)
    assert isinstance(full_contract.features.exclude, tuple)
    assert isinstance(full_contract.features.id_columns, tuple)
    assert isinstance(full_contract.objective.secondary_metrics, tuple)
    assert isinstance(full_contract.modelling.candidates, tuple)
    assert isinstance(full_contract.objective.inference_goals[0].predictors, tuple)
    class_order_target = TargetSpec.model_validate(
        {"column": "y", "type": "multiclass", "class_order": ["a", "b"]}
    )
    assert isinstance(class_order_target.class_order, tuple)


def test_list_input_coerces_to_tuple() -> None:
    """C13: via model_validate."""
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {"exclude": ["a", "b"]},
            "modelling": {"candidates": ["logistic_regression"]},
        }
    )
    assert contract.features.exclude == ("a", "b")
    assert isinstance(contract.features.exclude, tuple)
    assert contract.modelling.candidates == ("logistic_regression",)
    assert isinstance(contract.modelling.candidates, tuple)


# --- Field bounds --------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1])
def test_header_row_valid(value: int) -> None:
    DatasetOptions.model_validate({"header_row": value})


def test_header_row_invalid() -> None:
    with pytest.raises(ValidationError):
        DatasetOptions.model_validate({"header_row": -1})


@pytest.mark.parametrize("value", [".", ","])
def test_decimal_valid(value: str) -> None:
    DatasetOptions.model_validate({"decimal": value})


@pytest.mark.parametrize("value", ["", ".."])
def test_decimal_invalid(value: str) -> None:
    with pytest.raises(ValidationError):
        DatasetOptions.model_validate({"decimal": value})


@pytest.mark.parametrize("value", [",", "\t"])
def test_delimiter_valid(value: str) -> None:
    DatasetOptions.model_validate({"delimiter": value})


def test_delimiter_invalid() -> None:
    with pytest.raises(ValidationError):
        DatasetOptions.model_validate({"delimiter": ""})


@pytest.mark.parametrize("value", [0.01, 0.89])
def test_test_size_valid(value: float) -> None:
    """Held with valid_size=0 so V10 (test_size + valid_size < 0.9) does not interfere with
    this field-level boundary check."""
    SplitRules.model_validate({"test_size": value, "valid_size": 0.0})


@pytest.mark.parametrize("value", [0.0, 1.0])
def test_test_size_invalid(value: float) -> None:
    with pytest.raises(ValidationError):
        SplitRules.model_validate({"test_size": value})


@pytest.mark.parametrize("value", [0.0, 0.09])
def test_valid_size_valid(value: float) -> None:
    SplitRules.model_validate({"valid_size": value, "test_size": 0.01})


@pytest.mark.parametrize("value", [-0.1, 1.0])
def test_valid_size_invalid(value: float) -> None:
    with pytest.raises(ValidationError):
        SplitRules.model_validate({"valid_size": value})


def test_seed_valid() -> None:
    SplitRules.model_validate({"seed": 0})


def test_seed_invalid() -> None:
    with pytest.raises(ValidationError):
        SplitRules.model_validate({"seed": -1})


def test_random_seed_valid() -> None:
    Constraints.model_validate({"random_seed": 0})


def test_random_seed_invalid() -> None:
    with pytest.raises(ValidationError):
        Constraints.model_validate({"random_seed": -1})


@pytest.mark.parametrize("value", [0.001, 0.5])
def test_alpha_valid(value: float) -> None:
    InferenceGoal.model_validate({"question": "q", "predictors": ["x"], "alpha": value})


@pytest.mark.parametrize("value", [0.0, 0.51])
def test_alpha_invalid(value: float) -> None:
    with pytest.raises(ValidationError):
        InferenceGoal.model_validate({"question": "q", "predictors": ["x"], "alpha": value})


def test_max_cardinality_valid() -> None:
    PreprocessingRules.model_validate({"max_cardinality": 2})


def test_max_cardinality_invalid() -> None:
    with pytest.raises(ValidationError):
        PreprocessingRules.model_validate({"max_cardinality": 1})


@pytest.mark.parametrize("value", [2, 20])
def test_cv_folds_valid(value: int) -> None:
    Tuning.model_validate({"cv_folds": value})


@pytest.mark.parametrize("value", [1, 21])
def test_cv_folds_invalid(value: int) -> None:
    with pytest.raises(ValidationError):
        Tuning.model_validate({"cv_folds": value})


def test_max_fit_seconds_valid() -> None:
    Tuning.model_validate({"max_fit_seconds": 1})


def test_max_fit_seconds_invalid() -> None:
    with pytest.raises(ValidationError):
        Tuning.model_validate({"max_fit_seconds": 0})


@pytest.mark.parametrize("value", [1, 64])
def test_max_candidates_valid(value: int) -> None:
    Constraints.model_validate({"max_candidates": value})


@pytest.mark.parametrize("value", [0, 65])
def test_max_candidates_invalid(value: int) -> None:
    with pytest.raises(ValidationError):
        Constraints.model_validate({"max_candidates": value})


@pytest.mark.parametrize("value", [1, 10])
def test_max_search_waves_valid(value: int) -> None:
    Constraints.model_validate({"max_search_waves": value})


@pytest.mark.parametrize("value", [0, 11])
def test_max_search_waves_invalid(value: int) -> None:
    with pytest.raises(ValidationError):
        Constraints.model_validate({"max_search_waves": value})


def test_max_retune_loops_valid() -> None:
    Constraints.model_validate({"max_retune_loops": 0})


def test_max_retune_loops_invalid() -> None:
    with pytest.raises(ValidationError):
        Constraints.model_validate({"max_retune_loops": -1})


@pytest.mark.parametrize("value", [0.01, 1.0])
def test_drop_rows_above_valid(value: float) -> None:
    MissingPolicy.model_validate({"drop_rows_above": value})


@pytest.mark.parametrize("value", [0.0, 1.01])
def test_drop_rows_above_invalid(value: float) -> None:
    with pytest.raises(ValidationError):
        MissingPolicy.model_validate({"drop_rows_above": value})


def test_predictors_one_entry_valid() -> None:
    InferenceGoal.model_validate({"question": "q", "predictors": ["x"]})


def test_predictors_empty_invalid() -> None:
    with pytest.raises(ValidationError):
        InferenceGoal.model_validate({"question": "q", "predictors": []})


# --- Invalid enum values, parametrised over the nine vocabularies -------------------------------


@pytest.mark.parametrize(
    ("model_cls", "field", "bad_value"),
    [
        (TargetSpec, "type", "ternary"),
        (FeatureRules, "roles", {"x": "unknown"}),
        (SplitRules, "strategy", "chronological"),
        (MissingPolicy, "default", "backfill"),
        (PreprocessingRules, "outliers", "remove"),
        (PreprocessingRules, "scaling", "log"),
        (PreprocessingRules, "categorical_encoding", "hashing"),
        (Tuning, "method", "bayesian"),
        (ModellingRules, "class_weight", "auto"),
    ],
)
def test_invalid_enum_value_rejected(
    model_cls: type[BaseModel], field: str, bad_value: object
) -> None:
    """Assert the error message lists the permitted values, so a user can fix it."""
    base: dict[str, object]
    if model_cls is TargetSpec:
        base = {"column": "y"}
    elif model_cls is InferenceGoal:
        base = {"question": "q", "predictors": ["x"]}
    else:
        base = {}
    with pytest.raises(ValidationError, match=r"permitted|Input should be"):
        model_cls.model_validate({**base, field: bad_value})
