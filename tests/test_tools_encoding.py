"""Chunk 12 §4: the eight encoding/scaling tools.

Inputs are built by hand with known answers. The leak tests are the heart of the suite:
- a scaler/encoder learned on train replays unchanged onto a valid whose own statistics differ;
- `fit_target_encoder` must equal `TargetEncoder.fit_transform` (out-of-fold) AND differ from
  `fit(...).transform(train)` -- equality alone would pass an in-sample implementation;
- `apply_target_encoder` must refuse a frame sharing even ONE label with train.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import TargetEncoder
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.cleaning import apply_imputer, cast_dtypes, fit_imputer
from statlab_tools.tools.encoding import (
    apply_onehot_encoder,
    apply_ordinal_encoder,
    apply_scaler,
    apply_target_encoder,
    fit_onehot_encoder,
    fit_ordinal_encoder,
    fit_scaler,
    fit_target_encoder,
)
from statlab_tools.tools.splitting import split_stratified

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded(paths: RunPaths, res: ToolSuccess, index: int = 0) -> pd.DataFrame:
    return load_dataframe(paths.artifact(res.artifacts[index].ref, "dataframe"))


# --- one-hot: learned on train, replayed ---------------------------------------------------------


def test_onehot_levels_learned_on_train_replayed_on_valid(run_paths: RunPaths) -> None:
    # train has levels {a, b}; valid introduces a brand-new level and a value train never saw.
    train = pd.DataFrame({"c": ["a", "b", "a", "b"], "y": [0, 1, 0, 1]})
    valid = pd.DataFrame({"c": ["a", "zzz", "b"], "y": [1, 0, 1]})  # zzz unseen
    fit = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y")
    assert _dict(fit.summary["columns"]) == {"c": 2}
    res = apply_onehot_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    # only train's levels become columns; the unseen row is all-False.
    assert list(out.columns) == ["c__a", "c__b", "y"]
    assert out["c__a"].tolist() == [True, False, False]
    assert out["c__b"].tolist() == [False, False, True]


def test_onehot_dummies_are_bool_named_and_replace_source(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["x", "y"], "n": [1.0, 2.0], "t": [0, 1]})
    fit = fit_onehot_encoder(run_paths, _save(run_paths, train), target="t", columns=("c",))
    out = _loaded(
        run_paths, apply_onehot_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, train))
    )
    # c replaced in place by bool dummies; n and t untouched; order preserved.
    assert list(out.columns) == ["c__x", "c__y", "n", "t"]
    assert str(out["c__x"].dtype) == "bool"


def test_onehot_unseen_and_null_counted(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["a", "b"], "y": [0, 1]})
    fit = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y")
    valid = pd.DataFrame({"c": ["a", "zzz", None, "qqq"], "y": [1, 0, 1, 0]})
    res = apply_onehot_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert res.summary["unseen"] == {"c": 2}  # zzz, qqq
    assert res.summary["null"] == {"c": 1}  # the None row
    # both the unseen rows AND the null row are all-False.
    dummies = out[["c__a", "c__b"]]
    assert dummies.iloc[1].tolist() == [False, False]
    assert dummies.iloc[2].tolist() == [False, False]


def test_onehot_explicit_column_above_max_cardinality_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": [f"v{i}" for i in range(5)], "y": [0, 1, 0, 1, 0]})
    with pytest.raises(ToolParamError) as exc:
        fit_onehot_encoder(
            run_paths, _save(run_paths, train), target="y", columns=("c",), max_cardinality=3
        )
    assert "columns" in exc.value.field_errors
    assert "target-encode" in exc.value.field_errors["columns"]


def test_onehot_columns_none_skips_high_cardinality_lists_it(run_paths: RunPaths) -> None:
    train = pd.DataFrame(
        {"small": ["a", "b", "a", "b"], "big": [f"v{i}" for i in range(4)], "y": [0, 1, 0, 1]}
    )
    fit = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y", max_cardinality=3)
    assert "small" in _dict(fit.summary["columns"])
    assert _dict(fit.summary["skipped_high_cardinality"]) == {"big": 4}
    assert any("big" in w and "skipped" in w for w in fit.warnings)


def test_onehot_categorical_with_unused_categories_counts_observed_only(
    run_paths: RunPaths,
) -> None:
    # a categorical declaring 3 levels but only 2 observed -- observed_counts drops the unused one.
    cat = pd.Categorical(["a", "b", "a"], categories=["a", "b", "c"])
    train = pd.DataFrame({"c": cat, "y": [0, 1, 0]})
    fit = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",))
    assert _dict(fit.summary["columns"]) == {"c": 2}  # not 3
    out = _loaded(
        run_paths, apply_onehot_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, train))
    )
    assert "c__c" not in out.columns


def test_onehot_dummy_name_collision_is_tool_param(run_paths: RunPaths) -> None:
    # a dummy name c__a would collide with an existing column of the same name.
    train = pd.DataFrame({"c": ["a", "b"], "c__a": [1.0, 2.0], "y": [0, 1]})
    with pytest.raises(ToolParamError) as exc:
        fit_onehot_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",))
    assert "columns" in exc.value.field_errors


def test_onehot_columns_none_selects_only_non_numeric_non_bool_non_datetime(
    run_paths: RunPaths,
) -> None:
    train = pd.DataFrame(
        {
            "s": ["a", "b", "a"],
            "n": [1.0, 2.0, 3.0],
            "flag": [True, False, True],
            "dt": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
            "y": [0, 1, 0],
        }
    )
    fit = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y")
    assert list(_dict(fit.summary["columns"])) == ["s"]  # numeric/bool/datetime skipped (P4)


# --- ordinal -------------------------------------------------------------------------------------


def test_ordinal_explicit_order_respected(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["low", "high", "medium"], "y": [0, 1, 0]})
    fit = fit_ordinal_encoder(
        run_paths,
        _save(run_paths, train),
        target="y",
        columns=("s",),
        level_order={"s": ("low", "medium", "high")},
    )
    assert _dict(_dict(fit.summary["ranks"])["s"]) == {"low": 0, "medium": 1, "high": 2}


def test_ordinal_learned_on_train_replayed_on_valid(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["low", "medium", "high"], "y": [0, 1, 0]})
    fit = fit_ordinal_encoder(
        run_paths,
        _save(run_paths, train),
        target="y",
        columns=("s",),
        level_order={"s": ("low", "medium", "high")},
    )
    valid = pd.DataFrame({"s": ["high", "low"], "y": [1, 0]})
    out = _loaded(
        run_paths, apply_ordinal_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    )
    assert out["s"].tolist() == [2.0, 0.0]
    assert str(out["s"].dtype) == "float64"


def test_ordinal_uses_ordered_categorical_categories(run_paths: RunPaths) -> None:
    # an ordered Categorical from cast_dtypes supplies the order.
    cat = pd.Categorical(["b", "a", "c"], categories=["a", "b", "c"], ordered=True)
    train = pd.DataFrame({"s": cat, "y": [0, 1, 0]})
    fit = fit_ordinal_encoder(run_paths, _save(run_paths, train), target="y", columns=("s",))
    assert _dict(_dict(fit.summary["ranks"])["s"]) == {"a": 0, "b": 1, "c": 2}


def test_ordinal_incomplete_level_order_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["low", "medium", "high"], "y": [0, 1, 0]})
    with pytest.raises(ToolParamError) as exc:
        fit_ordinal_encoder(
            run_paths,
            _save(run_paths, train),
            target="y",
            columns=("s",),
            level_order={"s": ("low", "high")},  # medium omitted
        )
    assert "level_order" in exc.value.field_errors


def test_ordinal_float_coded_levels_match_string_form(run_paths: RunPaths) -> None:
    # integral floats 1.0/2.0 must match declared "1"/"2", not "1.0"/"2.0" (P3).
    train = pd.DataFrame({"s": [1.0, 2.0, 1.0], "y": [0, 1, 0]})
    fit = fit_ordinal_encoder(
        run_paths,
        _save(run_paths, train),
        target="y",
        columns=("s",),
        level_order={"s": ("1", "2")},
    )
    out = _loaded(
        run_paths, apply_ordinal_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, train))
    )
    assert out["s"].tolist() == [0.0, 1.0, 0.0]  # mapped, not all-NaN


def test_ordinal_default_order_is_str_sorted(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["c", "a", "b"], "y": [0, 1, 0]})
    fit = fit_ordinal_encoder(run_paths, _save(run_paths, train), target="y", columns=("s",))
    assert _dict(_dict(fit.summary["ranks"])["s"]) == {"a": 0, "b": 1, "c": 2}


def test_ordinal_unseen_becomes_nan_and_counted(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["a", "b"], "y": [0, 1]})
    fit = fit_ordinal_encoder(run_paths, _save(run_paths, train), target="y", columns=("s",))
    valid = pd.DataFrame({"s": ["a", "zzz", None], "y": [0, 1, 0]})
    res = apply_ordinal_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert out["s"].iloc[0] == 0.0
    assert pd.isna(out["s"].iloc[1])  # unseen -> NaN
    assert pd.isna(out["s"].iloc[2])  # null stays NaN
    assert res.summary["unseen"] == {"s": 1}  # only the unseen value, not the null


# --- target encoder (D34c) -----------------------------------------------------------------------


def _binary_train() -> pd.DataFrame:
    # a two-level feature with a target that correlates differently per level, 30 rows so each
    # class has >= 5 members per fold.
    rng = np.random.default_rng(7)
    cats = (["a"] * 15) + (["b"] * 15)
    y = ([0] * 10 + [1] * 5) + ([1] * 10 + [0] * 5)  # a mostly-0, b mostly-1
    frame = pd.DataFrame({"c": cats, "y": y}, index=[f"t{i}" for i in range(30)])
    return frame.iloc[rng.permutation(30)]


def test_target_encoder_oof_equals_fit_transform_and_differs_from_insample(
    run_paths: RunPaths,
) -> None:
    train = _binary_train()
    fit = fit_target_encoder(
        run_paths, _save(run_paths, train), target="y", columns=("c",), seed=42
    )
    tool_oof = _loaded(run_paths, fit, index=1)["c__te"].to_numpy()

    # reproduce sklearn's own out-of-fold encoding with the SAME seeded splitter.
    x = pd.DataFrame({"c": train["c"].astype("str").to_numpy()})
    y = train["y"].to_numpy()
    sk = TargetEncoder(
        target_type="binary", smooth="auto", cv=StratifiedKFold(5, shuffle=True, random_state=42)
    )
    sk_oof = np.asarray(sk.fit_transform(x, y)).ravel()
    assert np.allclose(tool_oof, sk_oof)  # EQUALS fit_transform (out-of-fold)

    sk2 = TargetEncoder(
        target_type="binary", smooth="auto", cv=StratifiedKFold(5, shuffle=True, random_state=42)
    )
    sk2.fit(x, y)
    insample = np.asarray(sk2.transform(x)).ravel()
    assert not np.allclose(tool_oof, insample)  # DIFFERS from fit().transform() -- not in-sample


def test_target_encoder_same_seed_same_output_different_seed_differs(run_paths: RunPaths) -> None:
    train = _binary_train()
    a = _loaded(
        run_paths,
        fit_target_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",), seed=1),
        1,
    )
    b = _loaded(
        run_paths,
        fit_target_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",), seed=1),
        1,
    )
    c = _loaded(
        run_paths,
        fit_target_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",), seed=2),
        1,
    )
    assert np.allclose(a["c__te"].to_numpy(), b["c__te"].to_numpy())
    assert not np.allclose(a["c__te"].to_numpy(), c["c__te"].to_numpy())


def test_apply_target_encoder_on_train_frame_is_tool_param(run_paths: RunPaths) -> None:
    train = _binary_train()
    fit = fit_target_encoder(
        run_paths, _save(run_paths, train), target="y", columns=("c",), seed=42
    )
    with pytest.raises(ToolParamError) as exc:
        apply_target_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, train))
    assert "frame" in exc.value.field_errors


def test_apply_target_encoder_frame_sharing_one_label_is_refused(run_paths: RunPaths) -> None:
    train = _binary_train()
    fit = fit_target_encoder(
        run_paths, _save(run_paths, train), target="y", columns=("c",), seed=42
    )
    # a valid whose index shares exactly ONE label with train must still be refused.
    valid = pd.DataFrame({"c": ["a", "b"], "y": [0, 1]}, index=["t0", "v_new"])
    with pytest.raises(ToolParamError) as exc:
        apply_target_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    assert "frame" in exc.value.field_errors
    assert "1 rows" in exc.value.field_errors["frame"]


def test_apply_target_encoder_on_valid_succeeds(run_paths: RunPaths) -> None:
    train = _binary_train()
    fit = fit_target_encoder(
        run_paths, _save(run_paths, train), target="y", columns=("c",), seed=42
    )
    valid = pd.DataFrame({"c": ["a", "b"], "y": [0, 1]}, index=["v0", "v1"])
    res = apply_target_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert list(out.columns) == ["c__te", "y"]
    assert str(out["c__te"].dtype) == "float64"


def test_apply_target_encoder_unseen_level_is_target_mean(run_paths: RunPaths) -> None:
    train = _binary_train()
    fit = fit_target_encoder(
        run_paths, _save(run_paths, train), target="y", columns=("c",), seed=42
    )
    valid = pd.DataFrame({"c": ["unseen_level"], "y": [1]}, index=["v0"])
    res = apply_target_encoder(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    assert out["c__te"].iloc[0] == pytest.approx(float(train["y"].mean()))  # P3
    assert res.summary["unseen"] == {"c": 1}


def test_target_encoder_multiclass_output_naming(run_paths: RunPaths) -> None:
    rng = np.random.default_rng(3)
    frame = pd.DataFrame(
        {"c": (["a", "b", "c"] * 10), "y": (["x", "y", "z"] * 10)},
        index=[f"t{i}" for i in range(30)],
    ).iloc[rng.permutation(30)]
    fit = fit_target_encoder(run_paths, _save(run_paths, frame), target="y", columns=("c",), seed=5)
    assert fit.summary["target_type"] == "multiclass"
    out = _loaded(run_paths, fit, index=1)
    assert list(out.columns) == ["c__te_x", "c__te_y", "c__te_z", "y"]
    assert _list(fit.summary["output_columns"]) == ["c__te_x", "c__te_y", "c__te_z"]


def test_target_encoder_class_with_four_rows_is_data_error(run_paths: RunPaths) -> None:
    # class 1 has only 4 rows, fewer than the 5 folds -> ToolDataError, not an sklearn warning.
    frame = pd.DataFrame(
        {"c": ["a", "b"] * 12, "y": [0] * 20 + [1] * 4}, index=[f"t{i}" for i in range(24)]
    )
    with pytest.raises(ToolDataError):
        fit_target_encoder(run_paths, _save(run_paths, frame), target="y", columns=("c",), seed=1)


def test_target_encoder_continuous_target_uses_kfold(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {"c": ["a", "b", "c"] * 10, "y": np.linspace(0.0, 100.0, 30)},
        index=[f"t{i}" for i in range(30)],
    )
    fit = fit_target_encoder(run_paths, _save(run_paths, frame), target="y", columns=("c",), seed=9)
    assert fit.summary["target_type"] == "continuous"
    assert list(_loaded(run_paths, fit, index=1).columns) == ["c__te", "y"]


def test_target_encoder_negative_smoothing_is_tool_param(run_paths: RunPaths) -> None:
    train = _binary_train()
    with pytest.raises(ToolParamError) as exc:
        fit_target_encoder(
            run_paths, _save(run_paths, train), target="y", columns=("c",), seed=1, smoothing=-1.0
        )
    assert "smoothing" in exc.value.field_errors


def test_target_encoder_target_absent_from_train_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["a", "b"] * 15}, index=[f"t{i}" for i in range(30)])
    with pytest.raises(ToolParamError) as exc:
        fit_target_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",), seed=1)
    assert "target" in exc.value.field_errors


# --- scaler ([v1]) -------------------------------------------------------------------------------


def test_scaler_standard_learned_on_train_replayed_turns_14_into_2(run_paths: RunPaths) -> None:
    # population std of [8,8,12,12] is exactly 2, mean 10 -> valid value 14 scales to (14-10)/2=2.
    train = pd.DataFrame({"x": [8.0, 8.0, 12.0, 12.0], "y": [0, 1, 0, 1]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="standard")
    center, scale = _list(_dict(fit.summary["params"])["x"])
    assert center == 10.0 and scale == 2.0
    valid = pd.DataFrame({"x": [14.0, 6.0], "y": [1, 0]})  # own mean 10, own std 4
    out = _loaded(run_paths, apply_scaler(run_paths, fit.artifacts[0].ref, _save(run_paths, valid)))
    assert out["x"].iloc[0] == 2.0  # the TRAIN scale, not valid's
    assert out["x"].iloc[1] == -2.0


def test_scaler_standard_params_match_hand_computation(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [0, 1, 0, 1]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="standard")
    center, scale = _list(_dict(fit.summary["params"])["x"])
    assert center == 2.5
    assert scale == pytest.approx(float(np.std([1, 2, 3, 4])))  # ddof=0


def test_scaler_minmax_params_match_hand_computation(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [2.0, 4.0, 10.0], "y": [0, 1, 0]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="minmax")
    assert _list(_dict(fit.summary["params"])["x"]) == [2.0, 8.0]  # min 2, max-min 8


def test_scaler_robust_params_match_hand_computation(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [0, 1, 0, 1, 0]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="robust")
    center, scale = _list(_dict(fit.summary["params"])["x"])
    assert center == 3.0  # median
    assert scale == pytest.approx(
        float(np.quantile([1, 2, 3, 4, 5], 0.75) - np.quantile([1, 2, 3, 4, 5], 0.25))
    )


def test_scaler_zero_spread_column_skipped_with_warning(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"const": [5.0] * 4, "x": [1.0, 2.0, 3.0, 4.0], "y": [0, 1, 0, 1]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="standard")
    assert "const" in _list(fit.summary["skipped"])
    assert "const" not in _dict(fit.summary["params"])
    assert any("zero spread" in w for w in fit.warnings)


def test_scaler_int64_input_works(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": pd.array([1, 2, 3, 4], dtype="Int64"), "y": [0, 1, 0, 1]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="minmax")
    valid = pd.DataFrame({"x": pd.array([1, None, 4], dtype="Int64"), "y": [0, 1, 0]})
    out = _loaded(run_paths, apply_scaler(run_paths, fit.artifacts[0].ref, _save(run_paths, valid)))
    assert str(out["x"].dtype) == "float64"  # P1: Int64 cast to float64, no crash
    assert out["x"].iloc[0] == 0.0  # (1-1)/3
    assert pd.isna(out["x"].iloc[1])


def test_scaler_columns_none_skips_bool_dummies(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "dummy": [True, False, True], "y": [0, 1, 0]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="standard")
    assert list(_dict(fit.summary["params"])) == ["x"]  # dummy (bool) skipped (P4)


def test_scaler_explicit_non_numeric_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": ["a", "b"], "y": [0, 1]})
    with pytest.raises(ToolParamError) as exc:
        fit_scaler(
            run_paths, _save(run_paths, train), target="y", method="standard", columns=("s",)
        )
    assert "columns" in exc.value.field_errors


# --- invariants ----------------------------------------------------------------------------------


def test_target_never_encoded_when_named_in_columns(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["a", "b"], "y": ["p", "q"]})
    with pytest.raises(ToolParamError) as e_oh:
        fit_onehot_encoder(run_paths, _save(run_paths, train), target="y", columns=("y",))
    assert "columns" in e_oh.value.field_errors
    with pytest.raises(ToolParamError) as e_od:
        fit_ordinal_encoder(run_paths, _save(run_paths, train), target="y", columns=("y",))
    assert "columns" in e_od.value.field_errors
    with pytest.raises(ToolParamError) as e_te:
        fit_target_encoder(run_paths, _save(run_paths, train), target="y", columns=("y",), seed=1)
    assert "columns" in e_te.value.field_errors


def test_target_never_scaled_even_as_string_column(run_paths: RunPaths) -> None:
    # a string target must not be selected by columns=None, and the output must keep it untouched.
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "y": ["p", "q", "p"]})
    fit = fit_scaler(run_paths, _save(run_paths, train), target="y", method="standard")
    assert "y" not in _dict(fit.summary["params"])
    out = _loaded(run_paths, apply_scaler(run_paths, fit.artifacts[0].ref, _save(run_paths, train)))
    assert out["y"].tolist() == ["p", "q", "p"]  # untouched


def test_labels_and_order_preserved_on_non_range_index(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["a", "b"], "x": [1.0, 2.0], "y": [0, 1]}, index=["t0", "t1"])
    oh = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",))
    valid = pd.DataFrame({"c": ["b", "a"], "x": [9.0, 8.0], "y": [1, 0]}, index=["z", "a"])
    out = _loaded(
        run_paths, apply_onehot_encoder(run_paths, oh.artifacts[0].ref, _save(run_paths, valid))
    )
    assert list(out.index) == ["z", "a"]  # order and labels preserved, no reset_index


def test_wrong_type_fitted_ref_is_tool_param_for_each_apply(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["a", "b"], "x": [1.0, 2.0], "y": [0, 1]})
    oh = fit_onehot_encoder(run_paths, _save(run_paths, train), target="y", columns=("c",))
    sc = fit_scaler(run_paths, _save(run_paths, train), target="y", method="standard")
    frame = _save(run_paths, train)
    # a scaler ref passed to apply_onehot_encoder, and an encoder ref to apply_scaler.
    with pytest.raises(ToolParamError) as e1:
        apply_onehot_encoder(run_paths, sc.artifacts[0].ref, frame)
    assert "encoder" in e1.value.field_errors
    with pytest.raises(ToolParamError) as e2:
        apply_scaler(run_paths, oh.artifacts[0].ref, frame)
    assert "scaler" in e2.value.field_errors


def test_apply_missing_fitted_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"c": ["a", "b"], "d": ["x", "y"], "t": [0, 1]})
    oh = fit_onehot_encoder(run_paths, _save(run_paths, train), target="t", columns=("c", "d"))
    frame = pd.DataFrame({"c": ["a"], "t": [0]})  # missing d
    with pytest.raises(ToolParamError) as exc:
        apply_onehot_encoder(run_paths, oh.artifacts[0].ref, _save(run_paths, frame))
    assert "frame" in exc.value.field_errors


# --- integration: chunk 11 -> chunk 12 on churn_frame -------------------------------------------


def test_integration_cast_split_impute_onehot_scale(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """cast -> split -> impute -> one-hot -> scale on churn_frame: the final matrix has only
    numeric/bool columns besides the target, no nulls, and identical columns on train and valid."""
    raw = _save(run_paths, churn_frame_fixture)
    typed = cast_dtypes(
        run_paths,
        raw,
        target="churned",
        roles={"age": "numeric", "plan": "categorical", "satisfaction": "categorical"},
    )
    typed_ref = typed.artifacts[0].ref
    split = split_stratified(run_paths, typed_ref, target="churned", seed=42)
    train_ref = next(a.ref for a in split.artifacts if a.split == "train")
    valid_ref = next(a.ref for a in split.artifacts if a.split == "valid")

    # drop the free-text / id / date columns the predictive matrix should not carry.
    # fit on train
    imp = fit_imputer(run_paths, train_ref, target="churned")
    train_imp = apply_imputer(run_paths, imp.artifacts[0].ref, train_ref)
    valid_imp = apply_imputer(run_paths, imp.artifacts[0].ref, valid_ref)

    oh = fit_onehot_encoder(
        run_paths,
        train_imp.artifacts[0].ref,
        target="churned",
        columns=("plan", "satisfaction"),
    )
    train_oh = apply_onehot_encoder(run_paths, oh.artifacts[0].ref, train_imp.artifacts[0].ref)
    valid_oh = apply_onehot_encoder(run_paths, oh.artifacts[0].ref, valid_imp.artifacts[0].ref)

    sc = fit_scaler(
        run_paths, train_oh.artifacts[0].ref, target="churned", method="standard", columns=("age",)
    )
    train_final = _loaded(
        run_paths, apply_scaler(run_paths, sc.artifacts[0].ref, train_oh.artifacts[0].ref)
    )
    valid_final = _loaded(
        run_paths, apply_scaler(run_paths, sc.artifacts[0].ref, valid_oh.artifacts[0].ref)
    )

    assert list(train_final.columns) == list(valid_final.columns)
    features = [c for c in train_final.columns if c != "churned"]
    for col in features:
        dt = str(train_final[col].dtype)
        assert dt in ("float64", "bool", "int64", "str") or dt.startswith("datetime")
    # the engineered feature columns (age scaled, plan/satisfaction dummies) have no nulls.
    engineered = ["age"] + [c for c in features if c.startswith(("plan__", "satisfaction__"))]
    assert not train_final[engineered].isna().any().any()
    assert not valid_final[engineered].isna().any().any()


# --- strict JSON + MCP round-trip ----------------------------------------------------------------


def _dumpable(res: ToolSuccess) -> None:
    json.dumps(res.model_dump(mode="json"), allow_nan=False)


def test_every_encoding_tool_envelope_is_strict_json_on_churn(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    typed = cast_dtypes(
        run_paths,
        _save(run_paths, churn_frame_fixture),
        target="churned",
        roles={"plan": "categorical", "satisfaction": "categorical", "age": "numeric"},
    )
    tref = typed.artifacts[0].ref
    split = split_stratified(run_paths, tref, target="churned", seed=1)
    train_ref = next(a.ref for a in split.artifacts if a.split == "train")
    valid_ref = next(a.ref for a in split.artifacts if a.split == "valid")

    oh = fit_onehot_encoder(run_paths, train_ref, target="churned", columns=("plan",))
    _dumpable(oh)
    _dumpable(apply_onehot_encoder(run_paths, oh.artifacts[0].ref, valid_ref))
    od = fit_ordinal_encoder(run_paths, train_ref, target="churned", columns=("satisfaction",))
    _dumpable(od)
    _dumpable(apply_ordinal_encoder(run_paths, od.artifacts[0].ref, valid_ref))
    te = fit_target_encoder(run_paths, train_ref, target="churned", columns=("plan",), seed=1)
    _dumpable(te)
    _dumpable(apply_target_encoder(run_paths, te.artifacts[0].ref, valid_ref))
    sc = fit_scaler(run_paths, train_ref, target="churned", method="robust", columns=("age",))
    _dumpable(sc)
    _dumpable(apply_scaler(run_paths, sc.artifacts[0].ref, valid_ref))


@pytest.mark.asyncio
async def test_mcp_fit_then_apply_onehot_round_trips(
    mcp_session: SessionFactory, run_paths_over_mcp: str
) -> None:
    """fit_onehot_encoder then apply_onehot_encoder over a real session; the served meta carries
    the right phase and allow_in, and the target-encoder's seed is ORCHESTRATOR_SUPPLIED."""
    from mcp.types import CallToolResult
    from statlab_tools.settings import get_tool_settings
    from statlab_tools.tool import ORCHESTRATOR_SUPPLIED

    settings = get_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    train = pd.DataFrame({"c": ["a", "b", "a"], "y": [0, 1, 0]})
    ref = _save(paths, train)

    async with mcp_session() as session:
        listed = await session.list_tools()
        meta = {t.name: t.meta for t in listed.tools if t.meta is not None}
        assert meta["fit_onehot_encoder"]["phase"] == "encoding"
        assert meta["fit_onehot_encoder"]["allow_in"] == ["feature_engineering"]
        assert meta["apply_onehot_encoder"]["allow_in"] == [
            "feature_engineering",
            "champion_selection",
        ]
        te_schema = next(t for t in listed.tools if t.name == "fit_target_encoder").input_schema
        assert te_schema["properties"]["seed"]["description"] == ORCHESTRATOR_SUPPLIED
        assert "seed" in te_schema["required"]

        fit_result = await session.call_tool(
            "fit_onehot_encoder",
            {"project": "proj", "run": run_paths_over_mcp, "train": ref, "target": "y"},
        )
        assert isinstance(fit_result, CallToolResult)
        assert fit_result.structured_content is not None
        fit_validated = TOOL_RESULT_ADAPTER.validate_python(fit_result.structured_content["result"])
        assert fit_validated.ok is True
        encoder_ref = fit_validated.artifacts[0].ref

        apply_result = await session.call_tool(
            "apply_onehot_encoder",
            {"project": "proj", "run": run_paths_over_mcp, "encoder": encoder_ref, "frame": ref},
        )
        assert isinstance(apply_result, CallToolResult)
        assert apply_result.structured_content is not None
        apply_validated = TOOL_RESULT_ADAPTER.validate_python(
            apply_result.structured_content["result"]
        )
        assert apply_validated.ok is True
        out = load_dataframe(paths.artifact(apply_validated.artifacts[0].ref, "dataframe"))
        assert list(out.columns) == ["c__a", "c__b", "y"]
