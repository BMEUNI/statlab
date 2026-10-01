"""Chunk 11 §4: the three pre-split tools (`drop_duplicate_rows`, `drop_high_missing_rows`,
`cast_dtypes`). Every guard is tested by triggering the failure it prevents; inputs are built by
hand with known answers so no expected value is computed by the code under test.
"""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.cleaning import (
    cast_dtypes,
    drop_duplicate_rows,
    drop_high_missing_rows,
)
from statlab_tools.tools.intake import validate_contract


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _col(res: ToolSuccess, column: str) -> dict[str, JsonValue]:
    """The per-column cast summary for `column`, narrowed from the JsonValue union."""
    return _dict(_dict(res.summary["columns"])[column])


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded(paths: RunPaths, res: ToolSuccess) -> pd.DataFrame:
    return load_dataframe(paths.artifact(res.artifacts[0].ref, "dataframe"))


def _write_contract(paths: RunPaths, contract: DataContract) -> None:
    paths.contract_snapshot_json.write_text(contract.model_dump_json(), encoding="utf-8")


# --- drop_duplicate_rows -------------------------------------------------------------------------


def test_duplicates_dropped_labels_preserved_non_range_index(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]}, index=["r0", "r1", "r2"])
    res = drop_duplicate_rows(run_paths, _save(run_paths, frame))
    out = _loaded(run_paths, res)
    assert list(out.index) == ["r0", "r2"]
    assert res.summary["rows_before"] == 3
    assert res.summary["rows_dropped"] == 1
    assert res.summary["subset"] is None


def test_nan_equal_rows_are_duplicates(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1.0, 1.0], "b": [None, None]})
    res = drop_duplicate_rows(run_paths, _save(run_paths, frame))
    assert res.summary["rows_dropped"] == 1


def test_subset_excluding_id_finds_duplicates_full_check_misses(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"id": [1, 2, 3], "a": ["x", "x", "y"]})
    full = drop_duplicate_rows(run_paths, _save(run_paths, frame))
    assert full.summary["rows_dropped"] == 0
    sub = drop_duplicate_rows(run_paths, _save(run_paths, frame), subset=("a",))
    assert sub.summary["rows_dropped"] == 1
    assert sub.summary["subset"] == ["a"]


def test_unknown_subset_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ToolParamError) as exc:
        drop_duplicate_rows(run_paths, _save(run_paths, frame), subset=("nope",))
    assert "subset" in exc.value.field_errors


# --- drop_high_missing_rows ----------------------------------------------------------------------


def test_row_at_threshold_kept_above_dropped(run_paths: RunPaths) -> None:
    # Four columns: a row with 2 nulls is 0.5 null; one with 3 nulls is 0.75.
    frame = pd.DataFrame(
        {
            "a": [1, 1, None],
            "b": [1, None, None],
            "c": [1, None, None],
            "d": [1, 1, None],
        },
        index=["full", "half", "most"],
    )
    res = drop_high_missing_rows(run_paths, _save(run_paths, frame), threshold=0.5)
    out = _loaded(run_paths, res)
    assert list(out.index) == ["full", "half"]  # 0.5 kept, 0.75 dropped
    assert res.summary["rows_dropped"] == 1
    assert res.summary["threshold"] == 0.5


def test_high_missing_rows_labels_preserved(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1, None], "b": [1, None]}, index=["keep", "drop"])
    res = drop_high_missing_rows(run_paths, _save(run_paths, frame), threshold=0.5)
    out = _loaded(run_paths, res)
    assert list(out.index) == ["keep"]


# --- cast_dtypes: one per role row ---------------------------------------------------------------


def test_cast_numeric_from_strings(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["1", "2", "oops"]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "numeric"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_numeric_dtype(out["x"])
    assert _col(res, "x")["newly_null"] == 1


def test_cast_numeric_already_numeric_is_noop(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1.0, 2.0]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "numeric"})
    assert "x" not in _dict(res.summary["columns"])


def test_cast_categorical_integral_floats_give_integers_not_floats(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1.0, 2.0, None]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "categorical"})
    out = _loaded(run_paths, res)
    assert out["x"].dropna().tolist() == ["1", "2"]  # P3: "1" not "1.0"


def test_cast_text_already_str_is_noop(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": pd.array(["a", "b"], dtype="str")})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "text"})
    assert "x" not in _dict(res.summary["columns"])


def test_cast_categorical_from_datetime_goes_to_str(run_paths: RunPaths) -> None:
    """A non-numeric, non-str column (here datetime) with a categorical role is stringified via
    the plain `astype('str')` fallback, not the Int64 integral-float path."""
    frame = pd.DataFrame({"x": pd.to_datetime(["2020-01-01", "2020-01-02"])})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "categorical"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_string_dtype(out["x"])
    assert out["x"].iloc[0].startswith("2020-01-01")


def test_cast_categorical_from_fractional_numeric_keeps_decimal(run_paths: RunPaths) -> None:
    """A numeric column with a fractional value is NOT routed through Int64 (that path is only
    for all-integral values), so it keeps its decimal under the plain str cast."""
    frame = pd.DataFrame({"x": [1.5, 2.0]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "categorical"})
    out = _loaded(run_paths, res)
    assert out["x"].tolist() == ["1.5", "2.0"]


def test_cast_ordinal_outside_levels_becomes_null(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["low", "high", "weird"]})
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {
                "roles": {"x": "ordinal"},
                "ordinal_levels": {"x": ["low", "medium", "high"]},
            },
        }
    )
    _write_contract(run_paths, contract)
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "ordinal"})
    out = _loaded(run_paths, res)
    assert isinstance(out["x"].dtype, pd.CategoricalDtype)
    assert out["x"].cat.ordered
    assert out["x"].isna().sum() == 1  # "weird" is outside the levels
    assert _col(res, "x")["source"] == "contract"


def test_cast_ordinal_no_levels_warns_and_unchanged(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["low", "high"]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "ordinal"})
    assert "x" not in _dict(res.summary["columns"])
    assert any("no ordinal_levels" in w for w in res.warnings)


def test_cast_datetime_from_iso_strings(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["2020-01-01", "2020-01-02"]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "datetime"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_datetime64_any_dtype(out["x"])


def test_cast_datetime_already_datetime_is_noop(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": pd.to_datetime(["2020-01-01", "2020-01-02"])})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "datetime"})
    assert "x" not in _dict(res.summary["columns"])


def test_cast_datetime_mixed_offsets_retry_utc_and_warn(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["2020-01-01T00:00:00+01:00", "2020-01-01T00:00:00+02:00"]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "datetime"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_datetime64_any_dtype(out["x"])
    assert out["x"].dt.tz is not None  # utc=True retry
    assert any("mixed UTC offsets" in w for w in res.warnings)


def test_cast_boolean_from_strings(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["Yes", "no", "T", "F", "maybe"]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "boolean"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_bool_dtype(out["x"])
    assert out["x"].tolist()[:4] == [True, False, True, False]
    assert out["x"].isna().sum() == 1  # "maybe" became NaN
    assert _col(res, "x")["newly_null"] == 1


def test_cast_boolean_already_bool_is_noop(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [True, False]})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "boolean"})
    assert "x" not in _dict(res.summary["columns"])


# --- cast_dtypes: roles / contract / errors ------------------------------------------------------


def test_contract_role_overrides_agent_role(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": ["1", "2"]})
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"x": "numeric"}}}
    )
    _write_contract(run_paths, contract)
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "categorical"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_numeric_dtype(out["x"])  # contract's numeric won
    assert _col(res, "x")["role"] == "numeric"
    assert _col(res, "x")["source"] == "contract"


def test_unknown_agent_column_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1, 2]})
    with pytest.raises(ToolParamError) as exc:
        cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"nope": "numeric"})
    assert "roles" in exc.value.field_errors


def test_unknown_contract_column_only_warns(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1, 2]})
    contract = DataContract.model_validate(
        {"target": {"column": "y"}, "features": {"roles": {"ghost": "numeric"}}}
    )
    _write_contract(run_paths, contract)
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y")
    assert any("ghost" in w and "ignored" in w for w in res.warnings)


def test_cast_outputs_survive_parquet_roundtrip(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "num": ["1", "2"],
            "ord": ["low", "high"],
            "dt": ["2020-01-01", "2020-01-02"],
            "flag": ["yes", "no"],
        }
    )
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {
                "roles": {"ord": "ordinal"},
                "ordinal_levels": {"ord": ["low", "medium", "high"]},
            },
        }
    )
    _write_contract(run_paths, contract)
    res = cast_dtypes(
        run_paths,
        _save(run_paths, frame),
        target="y",
        roles={"num": "numeric", "dt": "datetime", "flag": "boolean"},
    )
    out = _loaded(run_paths, res)  # already a parquet round-trip via save+load (P6)
    assert pd.api.types.is_numeric_dtype(out["num"])
    assert isinstance(out["ord"].dtype, pd.CategoricalDtype) and out["ord"].cat.ordered
    assert pd.api.types.is_datetime64_any_dtype(out["dt"])
    assert pd.api.types.is_bool_dtype(out["flag"])


# --- review corrections (2026-10-01) -------------------------------------------------------------


def test_cast_target_role_from_agent_ignored_with_warning(run_paths: RunPaths) -> None:
    """D-e: an agent role for the target is ignored (never cast) and warns. The 0/1 target stays
    an integer column, not a nullable boolean."""
    frame = pd.DataFrame({"x": ["1", "2"], "churned": [0, 1]})
    res = cast_dtypes(
        run_paths,
        _save(run_paths, frame),
        target="churned",
        roles={"x": "numeric", "churned": "boolean"},
    )
    out = _loaded(run_paths, res)
    assert out["churned"].tolist() == [0, 1]  # unchanged values
    assert pd.api.types.is_integer_dtype(out["churned"])  # not nullable boolean
    assert "churned" not in _dict(res.summary["columns"])  # never cast
    assert any("target" in w and "churned" in w for w in res.warnings)


def test_cast_then_validate_contract_positive_class_still_valid(run_paths: RunPaths) -> None:
    """End-to-end symptom (D-e): infer_column_roles returns churned: 'boolean', and the agent
    passes that in `roles`. If cast_dtypes cast the 0/1 target to nullable boolean,
    validate_contract would report positive_class '1' absent. With the target role ignored, the
    contract stays valid."""
    frame = pd.DataFrame({"x": ["1", "2"], "churned": [0, 1]})
    contract = DataContract.model_validate(
        {
            "target": {"column": "churned", "type": "binary", "positive_class": "1"},
            "features": {"roles": {"x": "numeric"}},
        }
    )
    _write_contract(run_paths, contract)
    res = cast_dtypes(
        run_paths,
        _save(run_paths, frame),
        target="churned",
        roles={"x": "numeric", "churned": "boolean"},  # agent's inferred boolean for the target
    )
    assert any("target" in w and "churned" in w for w in res.warnings)
    checked = validate_contract(run_paths, res.artifacts[0].ref)
    assert checked.summary["valid"] is True


def test_cast_boolean_from_integral_float64_nulls_nothing(run_paths: RunPaths) -> None:
    """Integer-coded 0/1 columns with a null load as float64; the boolean cast must route through
    Int64 (via _as_str) so '1.0' does not fall outside the boolean token map and null every row."""
    frame = pd.DataFrame({"x": [1.0, 0.0, None, 1.0] * 10})
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "boolean"})
    out = _loaded(run_paths, res)
    assert pd.api.types.is_bool_dtype(out["x"])
    assert _col(res, "x")["newly_null"] == 0  # no value destroyed
    assert out["x"].iloc[0] is True or bool(out["x"].iloc[0]) is True
    assert bool(out["x"].iloc[1]) is False
    assert pd.isna(out["x"].iloc[2])


def test_cast_ordinal_from_integral_float64_nulls_nothing(run_paths: RunPaths) -> None:
    """A float64-coded ordinal column compared against string levels must route through Int64 too,
    so '1.0' matches level '1'."""
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, None] * 10})
    contract = DataContract.model_validate(
        {
            "target": {"column": "y"},
            "features": {
                "roles": {"x": "ordinal"},
                "ordinal_levels": {"x": ["1", "2", "3"]},
            },
        }
    )
    _write_contract(run_paths, contract)
    res = cast_dtypes(run_paths, _save(run_paths, frame), target="y", roles={"x": "ordinal"})
    out = _loaded(run_paths, res)
    assert isinstance(out["x"].dtype, pd.CategoricalDtype) and out["x"].cat.ordered
    assert list(out["x"].cat.categories) == ["1", "2", "3"]
    assert _col(res, "x")["newly_null"] == 0  # every coded value matched its level
    assert out["x"].dropna().astype("str").tolist()[:3] == ["1", "2", "3"]
