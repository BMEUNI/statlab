"""`validate_contract`: `plan/08_tools_intake.md` section 3.4 (C1-C12), D-c, D-d.

Calls the tool through its decorated callable directly. The contract is written to
`seeded_run.contract_snapshot_json` via `write_snapshot`, matching D-c: the tool reads the
snapshot, never a parameter.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tools.intake import validate_contract

pytestmark = pytest.mark.usefixtures("tool_settings")


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _save(seeded_run: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(seeded_run.artifact(ref, "dataframe"), frame, ref=ref)
    return ref


def _fields(mismatches: JsonValue) -> list[str]:
    out = []
    for entry in _list(mismatches):
        out.append(str(_dict(entry)["field"]))
    return out


# --- V1: full.json against churn_frame -------------------------------------------------------


def test_v1_full_contract_against_churn_frame_is_valid(
    seeded_run: RunPaths,
    write_snapshot: Callable[[DataContract], None],
    full_contract: DataContract,
    churn_frame_fixture: pd.DataFrame,
) -> None:
    write_snapshot(full_contract)
    ref = _save(seeded_run, churn_frame_fixture)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True
    assert result.summary["mismatches"] == []
    assert result.summary["checked"] == 0


# --- V2: C1 gates the rest ---------------------------------------------------------------------


def test_v2_missing_column_produces_exactly_one_mismatch(
    seeded_run: RunPaths,
    write_snapshot: Callable[[DataContract], None],
    full_contract_dict: dict[str, JsonValue],
    churn_frame_fixture: pd.DataFrame,
) -> None:
    """Renaming `age` away must produce exactly one mismatch (naming `age`), not one per check
    that would otherwise have run against it (role, etc.)."""
    contract = DataContract.model_validate(full_contract_dict)
    write_snapshot(contract)
    renamed = churn_frame_fixture.rename(columns={"age": "age_renamed"})
    ref = _save(seeded_run, renamed)
    result = validate_contract(seeded_run, ref)
    mismatches = _list(result.summary["mismatches"])
    assert len(mismatches) == 1
    assert "age" in str(_dict(mismatches[0])["message"])


# --- V3: several mismatches reported together ---------------------------------------------------


def test_v3_three_planted_problems_produce_three_mismatches_in_one_call(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y", "type": "binary"},
            "features": {"roles": {"x1": "numeric", "x2": "datetime"}},
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame(
        {
            "y": [0, 0, 0, 0, 0],
            "x1": ["a", "b", "c", "d", "e"],
            "x2": ["not-a-date", "also-not", "nope", "no", "never"],
        }
    )
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    mismatches = _list(result.summary["mismatches"])
    assert len(mismatches) == 3
    fields = _fields(result.summary["mismatches"])
    assert "target.type" in fields
    assert "features.roles.x1" in fields
    assert "features.roles.x2" in fields


# --- V4: field is the dotted path ----------------------------------------------------------------


def test_v4_field_is_the_dotted_contract_path_for_a_role(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"age": "numeric"}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "age": ["x", "y"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "features.roles.age" in fields


def test_v4_field_is_the_dotted_path_for_inference_goal_predictors(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "objective": {
                "inference_goals": [{"question": "does x affect y?", "predictors": ["missing_col"]}]
            },
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "objective.inference_goals.0.predictors" in fields


# --- V5: stable order --------------------------------------------------------------------------


def test_v5_same_input_twice_gives_identical_mismatch_list(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"exclude": ["missing_a", "missing_b"]}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    first = validate_contract(seeded_run, ref)
    second = validate_contract(seeded_run, ref)
    assert first.summary["mismatches"] == second.summary["mismatches"]


# --- V6: minimal.json checks only what it sets --------------------------------------------------


def test_v6_minimal_contract_checks_only_target_column(
    seeded_run: RunPaths,
    write_snapshot: Callable[[DataContract], None],
    minimal_contract: DataContract,
) -> None:
    write_snapshot(minimal_contract)
    frame = pd.DataFrame({"churned": [0, 1, 0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True
    assert result.summary["mismatches"] == []


def test_v6_minimal_contract_with_missing_target_is_one_mismatch(
    seeded_run: RunPaths,
    write_snapshot: Callable[[DataContract], None],
    minimal_contract: DataContract,
) -> None:
    write_snapshot(minimal_contract)
    frame = pd.DataFrame({"other": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    mismatches = _list(result.summary["mismatches"])
    assert len(mismatches) == 1
    assert _dict(mismatches[0])["field"] == "target.column"


# --- V7: positive_class str() alignment ---------------------------------------------------------


def test_v7_positive_class_int_json_against_int64_frame_is_no_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": 1}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_v7_positive_class_not_present_is_a_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": 5}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.positive_class" in fields


# --- V8: no snapshot is transient ----------------------------------------------------------------


def test_v8_missing_snapshot_is_transient(seeded_run: RunPaths) -> None:
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    with pytest.raises(FileNotFoundError):
        validate_contract(seeded_run, ref)


# --- V9: valid is derived, not tracked independently --------------------------------------------


def test_v9_valid_true_only_when_mismatches_empty(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True
    assert result.summary["mismatches"] == []


def test_v9_valid_false_whenever_mismatches_nonempty(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "missing"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is False
    assert len(_list(result.summary["mismatches"])) >= 1


# --- C1: every named column exists --------------------------------------------------------------


def test_c1_all_named_columns_exist_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {"include": ["x1"], "exclude": ["x2"], "id_columns": ["id"]},
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "x1": [1, 2], "x2": [3, 4], "id": [1, 2]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c1_missing_named_column_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"exclude": ["ghost"]}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "features.exclude" in fields


def test_c1_lag_columns_must_exist(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {"lags": {"zz": [1]}},
            "split": {"strategy": "temporal", "time_column": "d"},
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "d": [1, 2]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert "features.lags.zz" in _fields(result.summary["mismatches"])


# --- C2: target not entirely null ----------------------------------------------------------------


def test_c2_target_with_values_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c2_entirely_null_target_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": pd.array([None, None], dtype="object")})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.column" in fields


# --- C3: binary target has exactly 2 distinct values ----------------------------------------------


def test_c3_binary_with_two_values_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "binary"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c3_binary_with_three_values_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "binary"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 2]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.type" in fields


# --- C4: multiclass target has >= 3 distinct values ----------------------------------------


def test_c4_multiclass_with_three_values_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "multiclass"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["a", "b", "c"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c4_multiclass_with_two_values_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "multiclass"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["a", "b"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.type" in fields


# --- C5: continuous/count target is numeric -------------------------------------------------------


def test_c5_continuous_numeric_target_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "continuous"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [1.5, 2.5, 3.5]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c5_continuous_non_numeric_target_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "continuous"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["a", "b", "c"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.type" in fields


# --- C6: count target non-negative integral -------------------------------------------------------


def test_c6_count_non_negative_integers_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "count"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 2, 3]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c6_count_with_negative_value_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "count"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, -1, 2]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.type" in fields


def test_c6_count_with_non_integral_value_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate({"target": {"column": "y", "type": "count"}})
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0.0, 1.5, 2.0]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.type" in fields


# --- C7: positive_class occurs in the target ------------------------------------------------------


def test_c7_positive_class_present_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": "yes"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["yes", "no", "yes"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c7_positive_class_absent_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": "maybe"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["yes", "no"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.positive_class" in fields


# --- C8: class_order equals the observed set ------------------------------------------------


def test_c8_class_order_matches_observed_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "multiclass", "class_order": ["a", "b", "c"]}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["a", "b", "c", "a"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c8_class_order_missing_a_level_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "multiclass", "class_order": ["a", "b"]}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["a", "b", "c"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.class_order" in fields


# --- C9: numeric role on a numeric column -----------------------------------------------------


def test_c9_numeric_role_on_numeric_column_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"age": "numeric"}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "age": [20, 30]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c9_numeric_role_on_string_column_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"age": "numeric"}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "age": ["young", "old"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "features.roles.age" in fields


# --- C10: datetime role parses -----------------------------------------------------------------


def test_c10_datetime_role_on_iso_strings_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"d": "datetime"}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "d": ["2024-01-01", "2024-06-15"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c10_datetime_role_on_non_iso_strings_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"d": "datetime"}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "d": ["not-a-date", "nope"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "features.roles.d" in fields


# --- C11: ordinal_levels covers the observed values -----------------------------------------------


def test_c11_ordinal_levels_covering_observed_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {
                "roles": {"satisfaction": "ordinal"},
                "ordinal_levels": {"satisfaction": ["low", "medium", "high"]},
            },
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "satisfaction": ["low", "high"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c11_ordinal_levels_missing_an_observed_value_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {
                "roles": {"satisfaction": "ordinal"},
                "ordinal_levels": {"satisfaction": ["low", "medium"]},
            },
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "satisfaction": ["low", "high"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "features.ordinal_levels.satisfaction" in fields


# --- C12: split.time_column parses as datetime ------------------------------------------------


def test_c12_time_column_iso_strings_passes(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "split": {"strategy": "temporal", "time_column": "d"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "d": ["2024-01-01", "2024-06-15"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_c12_time_column_non_date_strings_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "split": {"strategy": "temporal", "time_column": "d"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "d": ["not-a-date", "nope"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "split.time_column" in fields


def test_c12_time_column_numbers_and_datetimes_pass(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "split": {"strategy": "temporal", "time_column": "d"}}
    )
    write_snapshot(contract)
    numbers = _save(seeded_run, pd.DataFrame({"y": [0, 1], "d": [3, 1]}))
    assert validate_contract(seeded_run, numbers).summary["valid"] is True
    dates = _save(
        seeded_run,
        pd.DataFrame({"y": [0, 1], "d": pd.to_datetime(["2024-01-01", "2024-02-01"])}),
    )
    assert validate_contract(seeded_run, dates).summary["valid"] is True


# --- additional coverage ------------------------------------------------------------------------


def test_docstring_mentions_finding_not_failure() -> None:
    doc = validate_contract.__doc__
    assert doc is not None
    assert "finding" in doc.lower()


def test_snapshot_is_read_from_disk_not_a_parameter() -> None:
    """D-c: there is no `contract` parameter at all."""
    import inspect

    signature = inspect.signature(validate_contract)
    assert "contract" not in signature.parameters


def test_per_column_missing_policy_key_is_checked_under_c1(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "preprocessing": {"missing": {"per_column": {"ghost": "median"}}},
        }
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "preprocessing.missing.per_column.ghost" in fields


def test_group_column_named_in_contract_checked_under_c1(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "split": {"strategy": "grouped", "group_column": "ghost"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "split.group_column" in fields


def test_c10_datetime_role_on_entirely_null_feature_column_fails(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """An all-null feature column passes C1 (it exists) but has zero non-null values to parse
    -- `_iso8601_parse_share` returns `0.0` for it, which is below the threshold."""
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"d": "datetime"}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1], "d": pd.array([None, None], dtype="object")})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "features.roles.d" in fields


def test_c11_skips_an_ordinal_levels_key_that_c1_already_reported(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """`ordinal_levels` naming a column that does not exist is reported once by C1; C11 must
    not also try to inspect that column's observed values."""
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"ordinal_levels": {"ghost": ["a", "b"]}}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    mismatches = _list(result.summary["mismatches"])
    assert len(mismatches) == 1
    assert _dict(mismatches[0])["field"] == "features.ordinal_levels.ghost"


# --- R1: numeric target values compared as floats, not strings ------------------------------


def test_r1_positive_class_int_against_int64_target_no_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """int64 0/1 target, positive_class=1: the str() path already handled this, kept as the
    baseline that must stay green after the float comparison replaces it."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": 1}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, 1]})
    assert frame["y"].dtype == "int64"
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_r1_positive_class_int_against_float64_target_with_null_no_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """A 0/1 target with one missing value loads as float64 ({0.0, 1.0}); positive_class=1 must
    still match. This is the exact false mismatch R1 fixes -- it FAILED on the str() code,
    which compared "1" against {"0.0", "1.0"}."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": 1}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, None]})
    assert frame["y"].dtype == "float64"
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_r1_class_order_against_numeric_multiclass_with_null_no_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """A numeric multiclass target with a null loads as float64 ({0.0, 1.0, 2.0}); the declared
    class_order is always strings (typed tuple[str, ...]). They must still match -- this FAILED
    on the str() code (C8), which compared {"0","1","2"} against {"0.0","1.0","2.0"}."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "multiclass", "class_order": ["0", "1", "2"]}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 2, None]})
    assert frame["y"].dtype == "float64"
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_r1_positive_class_string_against_string_target_no_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """A string target compares as strings, unchanged by R1."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": "yes"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": ["yes", "no", "yes"]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    assert result.summary["valid"] is True


def test_r1_positive_class_absent_from_numeric_target_is_a_mismatch(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """positive_class=2 against a 0/1 target: one mismatch, as before -- the float path must
    not accidentally make everything match."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": 2}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.positive_class" in fields


def test_r1_unparseable_declared_value_against_numeric_target_is_a_mismatch_not_a_crash(
    seeded_run: RunPaths, write_snapshot: Callable[[DataContract], None]
) -> None:
    """positive_class="abc" against a numeric target cannot parse as a number. R1 requires this
    to be a mismatch, never an exception. The old str() code happened not to crash, but the
    new float path must handle the un-parseable value as "absent", not raise ValueError."""
    contract = DataContract.model_validate(
        {"target": {"column": "y", "type": "binary", "positive_class": "abc"}}
    )
    write_snapshot(contract)
    frame = pd.DataFrame({"y": [0, 1, 0, 1]})
    ref = _save(seeded_run, frame)
    result = validate_contract(seeded_run, ref)
    fields = _fields(result.summary["mismatches"])
    assert "target.positive_class" in fields
