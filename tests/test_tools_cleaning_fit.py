"""Chunk 11 §4: the decide-on-train tools, `drop_columns`, and the two `fit`/`apply` pairs.

The core leak test (train median 10, valid median 99; same for clipper bounds) is built by hand
so that a tool refitting on the applied frame would give a different, detectable answer. Every
guard is tested by triggering the failure it prevents.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.cleaning import (
    apply_imputer,
    apply_outlier_clipper,
    cast_dtypes,
    drop_columns,
    drop_constant_columns,
    drop_duplicate_rows,
    drop_high_missing_columns,
    drop_high_missing_rows,
    fit_imputer,
    fit_outlier_clipper,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _at(res: ToolSuccess, key: str, inner: str) -> JsonValue:
    """`res.summary[key][inner]`, narrowed from the JsonValue union."""
    return _dict(res.summary[key])[inner]


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded(paths: RunPaths, res: ToolSuccess) -> pd.DataFrame:
    return load_dataframe(paths.artifact(res.artifacts[0].ref, "dataframe"))


# --- the core leak test --------------------------------------------------------------------------


def test_imputer_fill_learned_on_train_not_recomputed_on_apply(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [8.0, 10.0, 12.0, None], "y": [0, 1, 0, 1]})  # median 10
    valid = pd.DataFrame({"x": [97.0, 99.0, 101.0, None], "y": [1, 0, 1, 0]})  # median 99
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y", default="median")
    assert _at(fit, "fills", "x") == 10.0
    res = apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert out["x"].iloc[3] == 10.0  # the train median, NOT valid's 99


def test_clipper_bounds_learned_on_train_not_recomputed_on_apply(run_paths: RunPaths) -> None:
    # train: evenly spread 0..10, bounds well inside valid's range.
    train = pd.DataFrame({"x": list(range(0, 11)), "y": [0, 1] * 5 + [0]})
    valid = pd.DataFrame({"x": [1000, 2000, 3000], "y": [1, 0, 1]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    _, upper = _list(_at(fit, "bounds", "x"))
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    # every valid value is far above train's upper bound, so all clip to `upper`.
    assert (out["x"] == upper).all()
    assert out["x"].max() == upper


# --- imputer behaviour ---------------------------------------------------------------------------


def test_imputer_learns_fill_for_column_with_no_train_nulls(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [2.0, 4.0, 6.0], "y": [0, 1, 0]})  # no nulls, median 4
    valid = pd.DataFrame({"x": [1.0, None, 3.0], "y": [1, 0, 1]})  # has a null
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y", default="median")
    assert _at(fit, "fills", "x") == 4.0
    res = apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert out["x"].iloc[1] == 4.0
    assert res.summary["filled"] == {"x": 1}


def test_median_default_on_string_falls_back_to_most_frequent(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["a", "a", "b", None], "y": [0, 1, 0, 1]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y", default="median")
    assert _at(fit, "strategies", "s") == "most_frequent"
    assert _at(fit, "fills", "s") == "a"
    assert any("falls back to most_frequent" in w for w in fit.warnings)


def test_explicit_per_column_median_on_string_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["a", "b"], "y": [0, 1]})
    with pytest.raises(ToolParamError) as exc:
        fit_imputer(run_paths, _save(run_paths, train), target="y", per_column={"s": "median"})
    assert "per_column" in exc.value.field_errors


def test_all_null_train_column_becomes_drop_with_warning(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"gone": [None, None, None], "y": [0, 1, 0]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y")
    assert _at(fit, "strategies", "gone") == "drop"
    assert "gone" in _list(fit.summary["dropped"])
    assert any("entirely null" in w for w in fit.warnings)


def test_drop_removes_column_keeps_row_count(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"gone": [None, None, None], "keep": [1.0, 2.0, 3.0], "y": [0, 1, 0]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y")
    valid = pd.DataFrame({"gone": [None, None], "keep": [5.0, None], "y": [1, 0]})
    res = apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert "gone" not in out.columns
    assert len(out) == 2  # no rows dropped


def test_constant_on_categorical_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": pd.Categorical(["x", "y"]), "y": [0, 1]})
    with pytest.raises(ToolParamError) as exc:
        fit_imputer(run_paths, _save(run_paths, train), target="y", per_column={"c": "constant"})
    assert "per_column" in exc.value.field_errors


def test_int64_fractional_median_imputes_to_float64_every_frame(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": pd.array([1, 2, 3, 4], dtype="Int64"), "y": [0, 1, 0, 1]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y", default="median")
    assert _at(fit, "fills", "x") == 2.5  # fractional median of an Int64 column
    valid = pd.DataFrame({"x": pd.array([1, None, 3], dtype="Int64"), "y": [1, 0, 1]})
    res = apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert str(out["x"].dtype) == "float64"  # P1: cast to float64 before filling
    assert out["x"].iloc[1] == 2.5


def test_target_not_imputed_even_when_mostly_null(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [None, None, 1]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y")
    assert "y" not in _dict(fit.summary["strategies"])  # target excluded (D-e)


def test_apply_imputer_target_absent_succeeds(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, None, 3.0], "y": [0, 1, 0]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y", default="median")
    predict_frame = pd.DataFrame({"x": [None, 5.0]})  # no target column
    res = apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, predict_frame))
    out = _loaded(run_paths, res)
    assert out["x"].iloc[0] == 2.0  # train median of [1,3]


def test_apply_imputer_unseen_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0], "y": [0, 1]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y")
    frame = pd.DataFrame({"x": [1.0, 2.0], "extra": [9.0, 9.0], "y": [0, 1]})
    with pytest.raises(ToolParamError) as exc:
        apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, frame))
    assert "frame" in exc.value.field_errors


def test_apply_imputer_missing_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0], "z": [3.0, 4.0], "y": [0, 1]})
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y")
    frame = pd.DataFrame({"x": [1.0, 2.0], "y": [0, 1]})  # missing z
    with pytest.raises(ToolParamError) as exc:
        apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, frame))
    assert "frame" in exc.value.field_errors


# --- wrong-artifact type checks ------------------------------------------------------------------


def test_datetime_mean_and_median_fill(run_paths: RunPaths) -> None:
    """`mean`/`median` on a datetime column fill with a Timestamp (the non-numeric branch)."""
    train = pd.DataFrame(
        {"d": pd.to_datetime(pd.Series(["2020-01-01", "2020-01-03", None])), "y": [0, 1, 0]}
    )
    fit_mean = fit_imputer(run_paths, _save(run_paths, train), target="y", per_column={"d": "mean"})
    assert _at(fit_mean, "strategies", "d") == "mean"
    valid = pd.DataFrame({"d": pd.to_datetime(pd.Series([None, "2021-06-01"])), "y": [1, 0]})
    out_mean = _loaded(
        run_paths, apply_imputer(run_paths, fit_mean.artifacts[0].ref, _save(run_paths, valid))
    )
    assert out_mean["d"].iloc[0] == pd.Timestamp("2020-01-02")  # mean of the two train dates
    fit_med = fit_imputer(
        run_paths, _save(run_paths, train), target="y", per_column={"d": "median"}
    )
    out_med = _loaded(
        run_paths, apply_imputer(run_paths, fit_med.artifacts[0].ref, _save(run_paths, valid))
    )
    assert out_med["d"].iloc[0] == pd.Timestamp("2020-01-02")


def test_constant_numeric_and_string_fills(run_paths: RunPaths) -> None:
    """`constant` fills 0.0 for numeric and the sentinel for string columns."""
    train = pd.DataFrame(
        {"n": [1.0, None, 3.0], "s": pd.array(["a", None, "c"], dtype="str"), "y": [0, 1, 0]}
    )
    fit = fit_imputer(
        run_paths,
        _save(run_paths, train),
        target="y",
        per_column={"n": "constant", "s": "constant"},
    )
    assert _at(fit, "fills", "n") == 0.0
    assert _at(fit, "fills", "s") == "__missing__"


def test_clipper_explicit_valid_columns(run_paths: RunPaths) -> None:
    """Naming only one of two numeric columns fits bounds for that one alone."""
    train = pd.DataFrame(
        {"a": [float(v) for v in range(1, 10)], "b": [1.0] * 9, "y": [0, 1] * 4 + [0]}
    )
    fit = fit_outlier_clipper(
        run_paths, _save(run_paths, train), target="y", method="clip_iqr", columns=("a",)
    )
    assert "a" in _dict(fit.summary["bounds"])
    assert "b" not in _dict(fit.summary["bounds"])  # not named, so never considered


def test_clipper_passed_to_apply_imputer_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": list(range(11)), "y": [0, 1] * 5 + [0]})
    clip = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    frame = pd.DataFrame({"x": [1.0], "y": [0]})
    with pytest.raises(ToolParamError) as exc:
        apply_imputer(run_paths, clip.artifacts[0].ref, _save(run_paths, frame))
    assert "imputer" in exc.value.field_errors


def test_imputer_passed_to_apply_clipper_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0], "y": [0, 1]})
    imp = fit_imputer(run_paths, _save(run_paths, train), target="y")
    frame = pd.DataFrame({"x": [1.0], "y": [0]})
    with pytest.raises(ToolParamError) as exc:
        apply_outlier_clipper(run_paths, imp.artifacts[0].ref, _save(run_paths, frame))
    assert "clipper" in exc.value.field_errors


# --- clipper behaviour ---------------------------------------------------------------------------


def test_clip_iqr_bounds_match_hand_computation(run_paths: RunPaths) -> None:
    # values 1..9: Q1=3, Q3=7, IQR=4, fence 1.5*4=6 -> bounds (-3, 13).
    train = pd.DataFrame({"x": [float(v) for v in range(1, 10)], "y": [0, 1] * 4 + [0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    assert _at(fit, "bounds", "x") == [-3.0, 13.0]


def test_winsorize_uses_1_and_99_percentiles(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [float(v) for v in range(101)], "y": [0, 1] * 50 + [0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="winsorize")
    lower, upper = _list(_at(fit, "bounds", "x"))
    assert lower == 1.0 and upper == 99.0  # the 0.01 and 0.99 quantiles of 0..100


def test_flag_only_adds_bool_column_leaves_values(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [float(v) for v in range(1, 10)], "y": [0, 1] * 4 + [0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="flag_only")
    valid = pd.DataFrame({"x": [5.0, 100.0], "y": [0, 1]})
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert out["x"].tolist() == [5.0, 100.0]  # values unchanged
    assert out["x__outlier"].tolist() == [False, True]
    assert res.summary["affected"] == {"x": 1}


def test_flag_only_name_collision_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [float(v) for v in range(1, 10)], "y": [0, 1] * 4 + [0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="flag_only")
    valid = pd.DataFrame({"x": [5.0], "x__outlier": [True], "y": [0]})
    with pytest.raises(ToolParamError) as exc:
        apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    assert "frame" in exc.value.field_errors


def test_constant_column_skipped_with_warning(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": [5.0] * 6, "x": [float(v) for v in range(6)], "y": [0, 1] * 3})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    assert "c" in _list(fit.summary["skipped"])
    assert "c" not in _dict(fit.summary["bounds"])
    assert any("zero-width" in w for w in fit.warnings)


def test_nan_survives_clipping(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [float(v) for v in range(1, 10)], "y": [0, 1] * 4 + [0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    valid = pd.DataFrame({"x": [5.0, None, 1000.0], "y": [0, 1, 0]})
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert pd.isna(out["x"].iloc[1])  # P7: NaN survives


def test_clipper_non_numeric_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["a", "b"], "y": [0, 1]})
    with pytest.raises(ToolParamError) as exc:
        fit_outlier_clipper(
            run_paths, _save(run_paths, train), target="y", method="clip_iqr", columns=("s",)
        )
    assert "columns" in exc.value.field_errors


def test_clipper_target_not_clipped(run_paths: RunPaths) -> None:
    # y is numeric and has an extreme value; it must never appear in bounds.
    train = pd.DataFrame({"x": [float(v) for v in range(1, 10)], "y": [1.0] * 8 + [999.0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    assert "y" not in _dict(fit.summary["bounds"])
    assert "y" not in _list(fit.summary["skipped"])


# --- deciders ------------------------------------------------------------------------------------


def test_drop_constant_columns_excludes_target(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"const": [5] * 4, "allnull": [None] * 4, "x": [1, 2, 3, 4], "y": [7] * 4})
    res = drop_constant_columns(run_paths, _save(run_paths, train), target="y")
    assert set(_list(res.summary["dropped_columns"])) == {"const", "allnull"}  # not y
    out = _loaded(run_paths, res)
    assert "y" in out.columns and "x" in out.columns


def test_drop_high_missing_columns_excludes_target_and_reports_share(run_paths: RunPaths) -> None:
    train = pd.DataFrame(
        {
            "sparse": [None, None, None, 1.0],  # 0.75 null
            "dense": [1.0, 2.0, None, 4.0],  # 0.25 null
            "y": [None, None, None, 1],  # mostly null target, must survive
        }
    )
    res = drop_high_missing_columns(run_paths, _save(run_paths, train), threshold=0.5, target="y")
    assert res.summary["dropped_columns"] == ["sparse"]
    assert _at(res, "null_share", "sparse") == 0.75
    out = _loaded(run_paths, res)
    assert "y" in out.columns


def test_decider_list_applied_with_drop_columns_matches_train(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"const": [5] * 4, "x": [1, 2, 3, 4], "y": [0, 1, 0, 1]})
    valid = pd.DataFrame({"const": [9, 9], "x": [10, 11], "y": [1, 0]})
    dec = drop_constant_columns(run_paths, _save(run_paths, train), target="y")
    train_out = _loaded(run_paths, dec)
    dropped = tuple(str(c) for c in _list(dec.summary["dropped_columns"]))
    applied = drop_columns(run_paths, _save(run_paths, valid), columns=dropped)
    valid_out = _loaded(run_paths, applied)
    assert list(train_out.columns) == list(valid_out.columns)


def test_drop_columns_unknown_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ToolParamError) as exc:
        drop_columns(run_paths, _save(run_paths, frame), columns=("nope",))
    assert "columns" in exc.value.field_errors


# --- index preservation --------------------------------------------------------------------------


def test_apply_imputer_keeps_labels_non_range_index(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": [0, 1, 0]}, index=["t0", "t1", "t2"])
    fit = fit_imputer(run_paths, _save(run_paths, train), target="y", default="median")
    valid = pd.DataFrame({"x": [None, 9.0], "y": [1, 0]}, index=["v9", "v8"])
    res = apply_imputer(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert list(out.index) == ["v9", "v8"]  # order and labels preserved, no reset_index


def test_clipper_keeps_labels_non_range_index(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [float(v) for v in range(1, 10)], "y": [0, 1] * 4 + [0]})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    valid = pd.DataFrame({"x": [1000.0, 5.0], "y": [1, 0]}, index=["z", "a"])
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert list(out.index) == ["z", "a"]


# --- cross-cutting: envelope JSON on real fixtures -----------------------------------------------


def _dumpable(res: ToolSuccess) -> None:
    json.dumps(res.model_dump(mode="json"), allow_nan=False)


def test_every_tool_envelope_is_strict_json_on_churn(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    _dumpable(drop_duplicate_rows(run_paths, ref, subset=("age",)))
    _dumpable(drop_high_missing_rows(run_paths, ref, threshold=0.5))
    _dumpable(cast_dtypes(run_paths, ref, target="churned", roles={"age": "numeric"}))
    _dumpable(drop_constant_columns(run_paths, ref, target="churned"))
    _dumpable(drop_high_missing_columns(run_paths, ref, threshold=0.5, target="churned"))
    fit = fit_imputer(run_paths, ref, target="churned")
    _dumpable(fit)
    _dumpable(apply_imputer(run_paths, fit.artifacts[0].ref, ref))
    clip = fit_outlier_clipper(run_paths, ref, target="churned", method="clip_iqr")
    _dumpable(clip)
    _dumpable(apply_outlier_clipper(run_paths, clip.artifacts[0].ref, ref))
    _dumpable(drop_columns(run_paths, ref, columns=("age",)))


def test_every_tool_envelope_is_strict_json_on_messy(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    _dumpable(drop_duplicate_rows(run_paths, ref))
    _dumpable(drop_high_missing_rows(run_paths, ref, threshold=0.5))
    _dumpable(cast_dtypes(run_paths, ref, target="target", roles={"with_outliers": "numeric"}))
    _dumpable(drop_constant_columns(run_paths, ref, target="target"))
    _dumpable(drop_high_missing_columns(run_paths, ref, threshold=0.5, target="target"))
    fit = fit_imputer(run_paths, ref, target="target")
    _dumpable(fit)
    _dumpable(apply_imputer(run_paths, fit.artifacts[0].ref, ref))
    clip = fit_outlier_clipper(run_paths, ref, target="target", method="winsorize")
    _dumpable(clip)
    _dumpable(apply_outlier_clipper(run_paths, clip.artifacts[0].ref, ref))


# --- MCP session ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_fit_then_apply_imputer_round_trips(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """fit_imputer then apply_imputer over a real session, and one tool from each group carries
    the right allow_in in its served meta."""
    from mcp.types import CallToolResult
    from statlab_tools.settings import get_tool_settings

    settings = get_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    train = pd.DataFrame({"x": [2.0, 4.0, None], "y": [0, 1, 0]})
    ref = _save(paths, train)

    async with mcp_session() as session:
        listed = await session.list_tools()
        allow_in = {t.name: t.meta["allow_in"] for t in listed.tools if t.meta is not None}
        assert allow_in["cast_dtypes"] == ["data_profiling", "data_splitting"]  # pre-split group
        assert allow_in["fit_imputer"] == ["preprocessing"]  # decide-on-train group
        assert allow_in["apply_imputer"] == ["preprocessing", "champion_selection"]  # apply group

        fit_result = await session.call_tool(
            "fit_imputer",
            {"project": "proj", "run": run_paths_over_mcp, "train": ref, "target": "y"},
        )
        assert isinstance(fit_result, CallToolResult)
        assert fit_result.structured_content is not None
        fit_validated = TOOL_RESULT_ADAPTER.validate_python(fit_result.structured_content["result"])
        assert fit_validated.ok is True
        imputer_ref = fit_validated.artifacts[0].ref

        apply_result = await session.call_tool(
            "apply_imputer",
            {
                "project": "proj",
                "run": run_paths_over_mcp,
                "imputer": imputer_ref,
                "frame": ref,
            },
        )
        assert isinstance(apply_result, CallToolResult)
        assert apply_result.structured_content is not None
        apply_validated = TOOL_RESULT_ADAPTER.validate_python(
            apply_result.structured_content["result"]
        )
        assert apply_validated.ok is True
        out = load_dataframe(paths.artifact(apply_validated.artifacts[0].ref, "dataframe"))
        assert out["x"].iloc[2] == 3.0  # train median of [2, 4]


# --- review corrections (2026-10-01): Int64 clipping -------------------------------------------


def test_clip_iqr_on_int64_casts_to_float64_and_clips(run_paths: RunPaths) -> None:
    """Clipping an Int64 column to a fractional IQR bound must not crash (P1): the column is cast
    to float64 first, on every frame, and the outlier is clipped to the learned upper bound."""
    train = pd.DataFrame({"x": pd.array([*range(1, 40), 500], dtype="Int64"), "y": [0, 1] * 20})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="clip_iqr")
    _, upper = _list(_at(fit, "bounds", "x"))
    valid = pd.DataFrame({"x": pd.array([1, 500, None], dtype="Int64"), "y": [0, 1, 0]})
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert str(out["x"].dtype) == "float64"  # P1: cast before clip
    assert out["x"].iloc[1] == upper  # the 500 outlier clipped
    assert pd.isna(out["x"].iloc[2])  # NaN survives (P7)


def test_winsorize_on_int64_casts_to_float64_and_clips(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": pd.array([*range(1, 40), 500], dtype="Int64"), "y": [0, 1] * 20})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="winsorize")
    _, upper = _list(_at(fit, "bounds", "x"))
    valid = pd.DataFrame({"x": pd.array([1, 500], dtype="Int64"), "y": [0, 1]})
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert str(out["x"].dtype) == "float64"
    assert out["x"].iloc[1] == upper


def test_flag_only_on_int64_with_na_gives_bool_false(run_paths: RunPaths) -> None:
    """flag_only on an Int64 column must give a plain bool flag column (not nullable BooleanDtype),
    with a null value flagged False."""
    train = pd.DataFrame({"x": pd.array([*range(1, 40), 500], dtype="Int64"), "y": [0, 1] * 20})
    fit = fit_outlier_clipper(run_paths, _save(run_paths, train), target="y", method="flag_only")
    valid = pd.DataFrame({"x": pd.array([5, 500, None], dtype="Int64"), "y": [0, 1, 0]})
    res = apply_outlier_clipper(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert str(out["x__outlier"].dtype) == "bool"  # plain bool, not boolean
    assert out["x__outlier"].tolist() == [False, True, False]  # NA row flagged False
