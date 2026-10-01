"""`_frame.py`: `json_scalar`, `json_list`, `is_numeric_column`, `iso8601_parse_share`,
`CLASS_TARGET_MAX_LEVELS`. Chunk 09 §3.1, §4.2.
"""

from __future__ import annotations

import datetime
import math

import numpy as np
import pandas as pd
import pytest
from statlab_tools.tools._frame import (
    CLASS_TARGET_MAX_LEVELS,
    is_numeric_column,
    iso8601_parse_share,
    json_list,
    json_scalar,
)

# --- json_scalar: value AND type, per case (§4.2's table) --------------------------------------


def test_json_scalar_np_int64_becomes_plain_int() -> None:
    result = json_scalar(np.int64(3))
    assert result == 3
    assert type(result) is int


def test_json_scalar_np_int32_becomes_plain_int() -> None:
    result = json_scalar(np.int32(7))
    assert result == 7
    assert type(result) is int


def test_json_scalar_np_float64_becomes_plain_float() -> None:
    result = json_scalar(np.float64(2.5))
    assert result == 2.5
    assert type(result) is float


def test_json_scalar_np_bool_true_becomes_plain_bool_not_int() -> None:
    """The ordering hazard the spec names explicitly: `np.bool_` must hit its own branch
    before any integer check, or `np.bool_(True)` would come back as `1`, not `True`."""
    result = json_scalar(np.bool_(True))
    assert result is True
    assert type(result) is bool


def test_json_scalar_np_bool_false_becomes_plain_bool() -> None:
    result = json_scalar(np.bool_(False))
    assert result is False
    assert type(result) is bool


def test_json_scalar_python_bool_true_stays_bool_not_one() -> None:
    """`bool` is an `int` subclass -- `True` must not become `1`."""
    result = json_scalar(True)
    assert result is True
    assert type(result) is bool


def test_json_scalar_python_bool_false_stays_bool_not_zero() -> None:
    result = json_scalar(False)
    assert result is False
    assert type(result) is bool


def test_json_scalar_nan_becomes_none() -> None:
    result = json_scalar(float("nan"))
    assert result is None


def test_json_scalar_np_nan_becomes_none() -> None:
    result = json_scalar(np.float64("nan"))
    assert result is None


def test_json_scalar_positive_inf_becomes_none() -> None:
    result = json_scalar(float("inf"))
    assert result is None


def test_json_scalar_negative_inf_becomes_none() -> None:
    """Tested separately from positive infinity -- a careless `value == float("inf")` check
    would miss this branch."""
    result = json_scalar(float("-inf"))
    assert result is None


def test_json_scalar_pd_na_becomes_none() -> None:
    result = json_scalar(pd.NA)
    assert result is None


def test_json_scalar_pd_nat_becomes_none() -> None:
    result = json_scalar(pd.NaT)
    assert result is None


def test_json_scalar_none_stays_none() -> None:
    assert json_scalar(None) is None


def test_json_scalar_pd_timestamp_becomes_isoformat_string() -> None:
    ts = pd.Timestamp("2024-03-01T12:30:00")
    result = json_scalar(ts)
    assert result == "2024-03-01T12:30:00"
    assert type(result) is str


def test_json_scalar_datetime_date_becomes_isoformat_string() -> None:
    result = json_scalar(datetime.date(2024, 3, 1))
    assert result == "2024-03-01"
    assert type(result) is str


def test_json_scalar_datetime_datetime_becomes_isoformat_string() -> None:
    result = json_scalar(datetime.datetime(2024, 3, 1, 12, 30))
    assert result == "2024-03-01T12:30:00"
    assert type(result) is str


def test_json_scalar_str_passes_through_unchanged() -> None:
    result = json_scalar("hello")
    assert result == "hello"
    assert type(result) is str


def test_json_scalar_plain_int_passes_through() -> None:
    result = json_scalar(5)
    assert result == 5
    assert type(result) is int


def test_json_scalar_plain_float_passes_through() -> None:
    result = json_scalar(2.5)
    assert result == 2.5
    assert type(result) is float


def test_json_scalar_finite_np_float_is_not_mistaken_for_non_finite() -> None:
    result = json_scalar(np.float64(3.5))
    assert result == 3.5
    assert not (isinstance(result, float) and math.isnan(result))


def test_json_scalar_unsupported_object_raises_type_error() -> None:
    """A bug, surfaced loudly rather than `str()`-ed into something misleading."""
    with pytest.raises(TypeError):
        json_scalar(object())


def test_json_scalar_unsupported_dict_raises_type_error() -> None:
    with pytest.raises(TypeError):
        json_scalar({"not": "a scalar"})


# --- json_list -----------------------------------------------------------------------------------


def test_json_list_maps_json_scalar_over_values() -> None:
    result = json_list([np.int64(1), np.float64(2.5), True, None])
    assert result == [1, 2.5, True, None]
    assert [type(v) for v in result] == [int, float, bool, type(None)]


def test_json_list_handles_pandas_index() -> None:
    index = pd.Index([np.int64(10), np.int64(20)])
    result = json_list(index.tolist())
    assert result == [10, 20]
    assert all(type(v) is int for v in result)


def test_json_list_empty_iterable_gives_empty_list() -> None:
    assert json_list([]) == []


# --- is_numeric_column -----------------------------------------------------------------------


def test_is_numeric_column_true_for_int() -> None:
    assert is_numeric_column(pd.Series([1, 2, 3], dtype="int64")) is True


def test_is_numeric_column_true_for_float() -> None:
    assert is_numeric_column(pd.Series([1.0, 2.0], dtype="float64")) is True


def test_is_numeric_column_false_for_bool() -> None:
    """P4: `is_numeric_dtype(bool)` is `True`, but this predicate must disagree."""
    assert is_numeric_column(pd.Series([True, False])) is False


def test_is_numeric_column_false_for_str() -> None:
    assert is_numeric_column(pd.Series(["a", "b"], dtype="str")) is False


def test_is_numeric_column_false_for_category() -> None:
    assert is_numeric_column(pd.Series(pd.Categorical(["a", "b"]))) is False


def test_is_numeric_column_false_for_datetime() -> None:
    assert is_numeric_column(pd.to_datetime(pd.Series(["2024-01-01", "2024-01-02"]))) is False


# --- iso8601_parse_share (moved unchanged from intake.py) --------------------------------------


def test_iso8601_parse_share_all_parseable_is_one() -> None:
    series = pd.Series(["2024-01-01", "2024-06-15"])
    assert iso8601_parse_share(series) == 1.0


def test_iso8601_parse_share_none_parseable_is_zero() -> None:
    series = pd.Series(["not a date", "also not"])
    assert iso8601_parse_share(series) == 0.0


def test_iso8601_parse_share_empty_non_null_is_zero() -> None:
    series = pd.Series([None, None], dtype="object")
    assert iso8601_parse_share(series) == 0.0


def test_iso8601_parse_share_mixed_is_the_fraction() -> None:
    series = pd.Series(["2024-01-01", "not a date", "2024-06-15", "nope"])
    assert iso8601_parse_share(series) == 0.5


# --- CLASS_TARGET_MAX_LEVELS -------------------------------------------------------------------


def test_class_target_max_levels_is_twenty() -> None:
    assert CLASS_TARGET_MAX_LEVELS == 20


# --- the move itself: these names are gone from intake.py --------------------------------------


def test_helpers_are_not_redefined_in_intake_module() -> None:
    """§3.1: `intake.py` must import the moved names, not define its own copies -- a second,
    slightly different definition is exactly what the move was meant to prevent."""
    import inspect

    import statlab_tools.tools.intake as intake_module

    source = inspect.getsource(intake_module)
    assert "def _iso8601_parse_share(" not in source
    assert "CLASS_TARGET_MAX_LEVELS: Final = 20" not in source


def test_helpers_importable_from_frame_module() -> None:
    import statlab_tools.tools._frame as frame_module

    assert hasattr(frame_module, "iso8601_parse_share")
    assert hasattr(frame_module, "CLASS_TARGET_MAX_LEVELS")
    assert hasattr(frame_module, "json_scalar")
    assert hasattr(frame_module, "json_list")
    assert hasattr(frame_module, "is_numeric_column")


def test_json_scalar_np_int16_becomes_plain_int() -> None:
    result = json_scalar(np.int16(42))
    assert result == 42
    assert type(result) is int


def test_json_scalar_np_float32_becomes_plain_float() -> None:
    result = json_scalar(np.float32(1.25))
    assert result == pytest.approx(1.25)
    assert type(result) is float


def test_is_numeric_column_true_for_nullable_int64() -> None:
    assert is_numeric_column(pd.Series([1, 2, None], dtype="Int64")) is True


def test_iso8601_parse_share_is_a_float_not_numpy_scalar() -> None:
    result = iso8601_parse_share(pd.Series(["2024-01-01"]))
    assert type(result) is float
