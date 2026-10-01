"""`infer_column_roles`: `plan/08_tools_intake.md` §3.3 (R1-R9).

Calls the tool through its decorated callable directly -- `infer_column_roles(paths, ref)` is
the undecorated body.
"""

from __future__ import annotations

from typing import get_args

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import ColumnRole
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tools.intake import (
    DATETIME_PARSE_SHARE,
    TEXT_MEAN_LENGTH,
    TEXT_UNIQUE_SHARE,
    infer_column_roles,
)

pytestmark = pytest.mark.usefixtures("tool_settings")


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _save(run_paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(run_paths.artifact(ref, "dataframe"), frame, ref=ref)
    return ref


# --- R1: all-null -------------------------------------------------------------------------------


def test_r1_all_null_column_is_listed_separately(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"empty": pd.array([None, None, None], dtype="object"), "x": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert "empty" in _list(result.summary["all_null"])
    assert "empty" not in _dict(result.summary["roles"])


# --- R2: bool dtype ------------------------------------------------------------------------------


def test_r2_bool_dtype_is_boolean(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"flag": pd.array([True, False, True], dtype="bool")})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["flag"] == "boolean"
    assert "bool dtype" in str(_dict(result.summary["evidence"])["flag"])


# --- R3: numeric 0/1 is boolean, not numeric (rule order) -----------------------------------------


def test_r3_numeric_01_overrides_r4_numeric(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"flag": [0, 1, 0, 1, 1]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["flag"] == "boolean"


def test_r3_requires_both_zero_and_one_present(run_paths: RunPaths) -> None:
    """A numeric column containing only `1`s (no `0`) is not boolean -- both values must be
    present, per R3's literal `{0, 1}` requirement."""
    frame = pd.DataFrame({"x": [1, 1, 1, 1]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["x"] == "numeric"


# --- R4: numeric ---------------------------------------------------------------------------------


def test_r4_numeric_dtype_is_numeric(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"age": [18, 25, 42, 67, 90]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["age"] == "numeric"
    assert "int64" in str(_dict(result.summary["evidence"])["age"])


def test_r4_float_dtype_is_numeric(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"price": [1.5, 2.5, 3.5]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["price"] == "numeric"


# --- R5: datetime dtype ---------------------------------------------------------------------------


def test_r5_datetime_dtype_is_datetime(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"dt": pd.to_datetime(["2024-01-01", "2024-06-15", "2024-12-31"])})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["dt"] == "datetime"
    assert "datetime dtype" in str(_dict(result.summary["evidence"])["dt"])


# --- R6: boolean-like strings ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "values", [["true", "false", "true"], ["yes", "no", "yes"], ["y", "n", "y"], ["Y", "N", "y"]]
)
def test_r6_two_boolean_like_strings_is_boolean(run_paths: RunPaths, values: list[str]) -> None:
    frame = pd.DataFrame({"flag": values})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["flag"] == "boolean"


def test_r6_three_distinct_values_is_not_boolean(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"flag": ["yes", "no", "maybe"]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["flag"] != "boolean"


# --- R7: ISO 8601 datetime strings -----------------------------------------------------------


def test_r7_iso_date_strings_are_datetime_not_categorical(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"d": ["2024-01-01", "2024-06-15", "2024-12-31", "2023-05-05"]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["d"] == "datetime"
    assert "ISO 8601" in str(_dict(result.summary["evidence"])["d"])


def test_r7_below_threshold_is_categorical(run_paths: RunPaths) -> None:
    """94% below the 95% threshold must NOT be recognised as datetime."""
    # 100 values, 94 valid ISO dates, 6 garbage -> 94% parse share, below DATETIME_PARSE_SHARE.
    values = ["2024-01-01"] * 94 + ["not-a-date"] * 6
    frame = pd.DataFrame({"d": values})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["d"] == "categorical"


def test_r7_non_iso_dates_are_not_datetime(run_paths: RunPaths) -> None:
    """`03/01/2026` is ambiguous between January and March; `format='ISO8601'` must reject it,
    not guess (D-e)."""
    frame = pd.DataFrame({"d": ["03/01/2026", "04/02/2026", "05/03/2026", "06/04/2026"]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["d"] != "datetime"


def test_r7_threshold_is_the_named_constant() -> None:
    assert DATETIME_PARSE_SHARE == 0.95


# --- R8: text --------------------------------------------------------------------------------


def test_r8_long_unique_strings_are_text(run_paths: RunPaths) -> None:
    values = [
        f"This is a long free-text note number {i} about customer behaviour." for i in range(50)
    ]
    frame = pd.DataFrame({"notes": values})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["notes"] == "text"
    evidence = str(_dict(result.summary["evidence"])["notes"])
    assert "mean length" in evidence
    assert "unique" in evidence


def test_r8_short_strings_even_if_unique_are_not_text(run_paths: RunPaths) -> None:
    """Mean length below `TEXT_MEAN_LENGTH` fails the first half of R8 even at 100% unique."""
    frame = pd.DataFrame({"code": [f"c{i}" for i in range(50)]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["code"] != "text"


def test_r8_long_but_low_cardinality_strings_are_not_text(run_paths: RunPaths) -> None:
    """Long strings repeated many times fail the unique-share half of R8."""
    long_value = "x" * 50
    frame = pd.DataFrame({"desc": [long_value] * 50})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["desc"] != "text"


def test_r8_thresholds_are_named_constants() -> None:
    assert TEXT_MEAN_LENGTH == 40
    assert TEXT_UNIQUE_SHARE == 0.5


# --- R9: categorical fallback -------------------------------------------------------------------


def test_r9_short_low_cardinality_strings_are_categorical(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"plan": ["basic", "standard", "premium", "basic", "standard"]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["plan"] == "categorical"
    assert "distinct values" in str(_dict(result.summary["evidence"])["plan"])


# --- ordinal is never inferred -------------------------------------------------------------------


def test_ordinal_is_never_a_role_for_any_input(run_paths: RunPaths) -> None:
    """D-e: `ordinal` needs a level order only the contract can supply. A broad frame covering
    every rule's input shape must never produce it."""
    frame = pd.DataFrame(
        {
            "all_null": pd.array([None, None, None], dtype="object"),
            "flag_bool": pd.array([True, False, True], dtype="bool"),
            "flag_01": [0, 1, 0],
            "num": [1.1, 2.2, 3.3],
            "dt_native": pd.to_datetime(["2024-01-01", "2024-06-15", "2024-12-31"]),
            "flag_str": ["yes", "no", "yes"],
            "dt_str": ["2024-01-01", "2024-06-15", "2024-12-31"],
            "text": [
                f"a long free text note about something important number {i}" for i in range(3)
            ],
            "cat": ["a", "b", "a"],
        }
    )
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    roles = _dict(result.summary["roles"])
    assert "ordinal" not in roles.values()


# --- derived exhaustiveness and evidence completeness --------------------------------------------


def test_every_role_value_is_a_member_of_column_role(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "flag_bool": pd.array([True, False], dtype="bool"),
            "flag_01": [0, 1],
            "num": [1.1, 2.2],
            "dt_native": pd.to_datetime(["2024-01-01", "2024-06-15"]),
            "flag_str": ["yes", "no"],
            "dt_str": ["2024-01-01", "2024-06-15"],
            "text": [f"a long free text note about something important {i}" * 2 for i in range(2)],
            "cat": ["a", "b"],
        }
    )
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    roles = _dict(result.summary["roles"])
    valid = set(get_args(ColumnRole))
    for value in roles.values():
        assert value in valid


def test_every_non_null_column_has_an_evidence_string(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    roles = _dict(result.summary["roles"])
    evidence = _dict(result.summary["evidence"])
    for column in roles:
        assert isinstance(evidence[column], str)
        assert evidence[column]


# --- churn_frame produces the roles its docstring promises --------------------------------------


def test_churn_frame_produces_the_promised_roles(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = infer_column_roles(run_paths, ref)
    roles = _dict(result.summary["roles"])
    assert roles["customer_id"] == "numeric"
    assert roles["age"] == "numeric"
    assert roles["plan"] == "categorical"
    assert roles["signup_date"] == "datetime"
    assert roles["satisfaction"] == "categorical"
    assert roles["notes_free_text"] == "text"
    assert roles["churned"] == "boolean"
    assert result.summary["all_null"] == []


def test_churn_frame_produces_no_ordinal(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = infer_column_roles(run_paths, ref)
    roles = _dict(result.summary["roles"])
    assert "ordinal" not in roles.values()


# --- additional coverage ---------------------------------------------------------------------


def test_r1_multiple_all_null_columns_all_listed(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "a": pd.array([None, None], dtype="object"),
            "b": pd.array([None, None], dtype="object"),
            "c": [1, 2],
        }
    )
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert sorted(str(v) for v in _list(result.summary["all_null"])) == ["a", "b"]


def test_r1_all_null_column_has_no_evidence_entry(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"empty": pd.array([None, None], dtype="object"), "x": [1, 2]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert "empty" not in _dict(result.summary["evidence"])


def test_r6_na_values_are_excluded_from_the_boolean_check(run_paths: RunPaths) -> None:
    """A null value must not be treated as a third distinct string -- `dropna()` happens
    before R6's distinct-value check."""
    frame = pd.DataFrame({"flag": ["yes", "no", "yes", None]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["flag"] == "boolean"


def test_r6_single_distinct_boolean_like_value_is_not_boolean(run_paths: RunPaths) -> None:
    """R6 requires exactly 2 distinct boolean-like values; one alone is categorical, not a
    meaningful boolean signal."""
    frame = pd.DataFrame({"flag": ["yes", "yes", "yes"]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["flag"] != "boolean"


def test_r9_many_distinct_values_is_still_categorical_not_text(run_paths: RunPaths) -> None:
    """Short strings with high cardinality fail R8's mean-length floor and fall through to
    R9 regardless of how many distinct values there are."""
    frame = pd.DataFrame({"code": [f"c{i}" for i in range(30)]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["code"] == "categorical"
    assert "30 distinct values" in str(_dict(result.summary["evidence"])["code"])


def test_r4_evidence_names_the_actual_dtype(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": pd.array([1.0, 2.0, 3.0], dtype="float64")})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["evidence"])["x"] == "float64"


def test_docstring_names_this_as_a_heuristic_fixed_order() -> None:
    doc = infer_column_roles.__doc__
    assert doc is not None
    assert "heuristic" in doc.lower()
    assert "ordinal" in doc.lower()


def test_summary_has_no_extra_keys_beyond_roles_evidence_all_null(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert set(result.summary) == {"roles", "evidence", "all_null"}


def test_no_artifact_is_produced(run_paths: RunPaths) -> None:
    """§3.3: the result is small, so `infer_column_roles` produces no artifact."""
    frame = pd.DataFrame({"x": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert result.artifacts == ()


def test_non_string_non_numeric_non_bool_non_datetime_dtype_falls_through_to_categorical(
    run_paths: RunPaths,
) -> None:
    """A `timedelta64` column fails all four dtype checks (bool, numeric, datetime64, string)
    and must still get a role via the R9 fallback rather than crashing or being silently
    skipped."""
    frame = pd.DataFrame({"dur": pd.to_timedelta(["1 day", "2 days", "3 days"])})
    ref = _save(run_paths, frame)
    result = infer_column_roles(run_paths, ref)
    assert _dict(result.summary["roles"])["dur"] == "categorical"
