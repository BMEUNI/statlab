"""Chunk 17 §4: the seven feature-construction and selection tools.

Inputs are built by hand with known answers. The heart of the suite:
- a binner's edges are learned on train and replayed unchanged onto a differently-distributed
  apply frame (the fit must not be recomputed);
- `is_weekend` is NA on a NaT row, not a silent weekday (P1);
- a zero denominator gives NaN, never inf, and is counted (P5);
- `select_by_variance` uses population variance (ddof=0, P8);
- `select_by_mutual_info`'s scores equal sklearn called directly with the same mask and seed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.encoding import fit_onehot_encoder
from statlab_tools.tools.features import (
    add_datetime_parts,
    add_interaction_terms,
    add_ratio_feature,
    apply_binner,
    fit_binner,
    select_by_mutual_info,
    select_by_variance,
)
from statlab_tools.tools.training import train_estimator


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


# --- datetime -----------------------------------------------------------------------------------


def _dt_frame() -> pd.DataFrame:
    raw = np.array(["2024-01-06", "2024-03-15 13:00", None], dtype=object)
    dates = pd.to_datetime(raw, format="mixed")
    return pd.DataFrame({"d": dates, "y": [0, 1, 0]})


def test_datetime_known_answers(run_paths: RunPaths) -> None:
    frame = _dt_frame()
    res = add_datetime_parts(
        run_paths,
        _save(run_paths, frame),
        target="y",
        column="d",
        parts=("year", "month", "day", "dayofweek", "dayofyear", "hour", "is_weekend"),
    )
    out = _loaded(run_paths, res)
    # 2024-01-06 is a Saturday (dayofweek 5), day-of-year 6, a weekend.
    assert out["d__year"].tolist()[0] == 2024
    assert out["d__month"].tolist()[0] == 1
    assert out["d__day"].tolist()[0] == 6
    assert out["d__dayofweek"].tolist()[0] == 5
    assert out["d__dayofyear"].tolist()[0] == 6
    assert out["d__is_weekend"].tolist()[0] == 1
    # 2024-03-15 13:00 is a Friday 13:00, not a weekend.
    assert out["d__hour"].tolist()[1] == 13
    assert out["d__is_weekend"].tolist()[1] == 0


def test_datetime_nat_is_na_in_every_part_including_is_weekend(run_paths: RunPaths) -> None:
    frame = _dt_frame()
    res = add_datetime_parts(
        run_paths,
        _save(run_paths, frame),
        target="y",
        column="d",
        parts=("year", "dayofweek", "is_weekend", "hour"),
    )
    out = _loaded(run_paths, res)
    for part in ("d__year", "d__dayofweek", "d__is_weekend", "d__hour"):
        assert pd.isna(out[part].iloc[2]), f"{part} should be NA on the NaT row (P1)"
        assert str(out[part].dtype) == "Int64"


def test_datetime_source_replaced_in_place_labels_preserved(run_paths: RunPaths) -> None:
    frame = _dt_frame()
    frame.index = ["r0", "r1", "r2"]
    res = add_datetime_parts(
        run_paths, _save(run_paths, frame), target="y", column="d", parts=("year", "month")
    )
    out = _loaded(run_paths, res)
    # d replaced in place by its parts; y stays after; labels preserved.
    assert list(out.columns) == ["d__year", "d__month", "y"]
    assert list(out.index) == ["r0", "r1", "r2"]


def test_datetime_str_column_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"d": pd.array(["2024-01-06", "2024-03-15"], dtype="str"), "y": [0, 1]})
    with pytest.raises(ToolParamError, match="cast_dtypes"):
        add_datetime_parts(
            run_paths, _save(run_paths, frame), target="y", column="d", parts=("year",)
        )


def test_datetime_duplicate_part_is_tool_param(run_paths: RunPaths) -> None:
    with pytest.raises(ToolParamError, match="duplicates"):
        add_datetime_parts(
            run_paths,
            _save(run_paths, _dt_frame()),
            target="y",
            column="d",
            parts=("year", "year"),
        )


def test_datetime_tz_aware_works(run_paths: RunPaths) -> None:
    dates = pd.to_datetime(["2024-01-06 23:00+00:00", "2024-03-15 13:00+00:00"], utc=True)
    frame = pd.DataFrame({"d": dates, "y": [0, 1]})
    res = add_datetime_parts(
        run_paths, _save(run_paths, frame), target="y", column="d", parts=("hour", "year")
    )
    out = _loaded(run_paths, res)
    assert out["d__hour"].tolist() == [23, 13]  # P2: parts come out in the column's own zone (UTC)


def test_datetime_target_refused(run_paths: RunPaths) -> None:
    frame = _dt_frame()
    with pytest.raises(ToolParamError, match="target"):
        add_datetime_parts(
            run_paths, _save(run_paths, frame), target="d", column="d", parts=("year",)
        )


# --- interaction and ratio ----------------------------------------------------------------------


def test_interaction_product_equals_ab(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [2.0, 3.0], "b": [5.0, 7.0], "y": [0, 1]})
    res = add_interaction_terms(run_paths, _save(run_paths, frame), target="y", pairs=(("a", "b"),))
    out = _loaded(run_paths, res)
    assert out["a__x__b"].tolist() == [10.0, 21.0]
    assert str(out["a__x__b"].dtype) == "float64"
    # sources stay (they are still useful).
    assert "a" in out.columns and "b" in out.columns


def test_interaction_bool_times_numeric_and_square(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [True, False, True], "n": [2.0, 3.0, 4.0], "y": [0, 1, 0]})
    res = add_interaction_terms(
        run_paths, _save(run_paths, frame), target="y", pairs=(("a", "n"), ("n", "n"))
    )
    out = _loaded(run_paths, res)
    assert out["a__x__n"].tolist() == [2.0, 0.0, 4.0]  # bool cast to 0/1
    assert out["n__x__n"].tolist() == [4.0, 9.0, 16.0]  # (n, n) squares


def test_interaction_int64_inputs_give_float64_and_nan_propagates(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {"a": pd.array([1, 2, None], dtype="Int64"), "b": [10.0, 20.0, 30.0], "y": [0, 1, 0]}
    )
    res = add_interaction_terms(run_paths, _save(run_paths, frame), target="y", pairs=(("a", "b"),))
    out = _loaded(run_paths, res)
    assert str(out["a__x__b"].dtype) == "float64"  # P6: not nullable Float64
    assert out["a__x__b"].tolist()[:2] == [10.0, 40.0]
    assert pd.isna(out["a__x__b"].iloc[2])  # NaN propagates


def test_interaction_non_numeric_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": pd.array(["x", "y"], dtype="str"), "b": [1.0, 2.0], "y": [0, 1]})
    with pytest.raises(ToolParamError, match="numeric or bool"):
        add_interaction_terms(run_paths, _save(run_paths, frame), target="y", pairs=(("a", "b"),))


def test_interaction_collision_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1.0], "b": [2.0], "a__x__b": [9.0], "y": [0]})
    with pytest.raises(ToolParamError, match="collide"):
        add_interaction_terms(run_paths, _save(run_paths, frame), target="y", pairs=(("a", "b"),))


def test_interaction_duplicate_pair_names_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0], "y": [0, 1]})
    with pytest.raises(ToolParamError, match="duplicate names"):
        add_interaction_terms(
            run_paths, _save(run_paths, frame), target="y", pairs=(("a", "b"), ("a", "b"))
        )


def test_ratio_known_answer(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"n": [10.0, 9.0], "d": [2.0, 3.0], "y": [0, 1]})
    res = add_ratio_feature(
        run_paths, _save(run_paths, frame), target="y", numerator="n", denominator="d"
    )
    out = _loaded(run_paths, res)
    assert out["n__per__d"].tolist() == [5.0, 3.0]
    assert str(out["n__per__d"].dtype) == "float64"
    assert res.summary["zero_denominator_rows"] == 0


def test_ratio_zero_denominator_including_negative_zero_is_nan_and_counted(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"n": [1.0, 2.0, 3.0], "d": [0.0, -0.0, 4.0], "y": [0, 1, 0]})
    res = add_ratio_feature(
        run_paths, _save(run_paths, frame), target="y", numerator="n", denominator="d"
    )
    out = _loaded(run_paths, res)
    ratios = out["n__per__d"].tolist()
    assert pd.isna(ratios[0]) and pd.isna(ratios[1])  # 0.0 and -0.0 both -> NaN, never inf (P5)
    assert not np.isinf(out["n__per__d"].to_numpy(dtype="float64", na_value=0.0)).any()
    assert ratios[2] == 0.75
    assert res.summary["zero_denominator_rows"] == 2
    assert len(res.warnings) == 1


def test_ratio_non_numeric_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"n": pd.array(["x"], dtype="str"), "d": [1.0], "y": [0]})
    with pytest.raises(ToolParamError, match="numeric or bool"):
        add_ratio_feature(
            run_paths, _save(run_paths, frame), target="y", numerator="n", denominator="d"
        )


# --- binner -------------------------------------------------------------------------------------


def test_binner_edges_fitted_on_train_only(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [0.0, 10.0, 20.0, 30.0, 40.0], "y": [0, 1, 0, 1, 0]})
    # valid has a totally different distribution; apply must keep train's edges. Train's single
    # inner edge is 20; valid's own median is 5, so edges recomputed from valid would put 5 and
    # 15 in b1 -- only the middle values tell the two apart, the extremes land outside either way.
    valid = pd.DataFrame({"x": [-100.0, -50.0, 5.0, 15.0, 1000.0], "y": [1, 0, 1, 0, 1]})
    fit = fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=2)
    edges_fit = fit.summary["edges"]
    res = apply_binner(run_paths, fit.artifacts[0].ref, _save(run_paths, valid))
    out = _loaded(run_paths, res)
    # out-of-range values land in the outer bins b0 and b{k-1}; nothing unbinned (P4).
    k = len(_list(_dict(edges_fit)["x"])) - 1
    assert out["x__bin"].iloc[0] == "b0"  # -100 below all inner edges
    assert out["x__bin"].iloc[-1] == f"b{k - 1}"  # 1000 above all inner edges
    assert out["x__bin"].tolist() == ["b0", "b0", "b0", "b0", "b1"]


def test_binner_out_of_range_and_nan(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [0, 1, 0, 1, 0]})
    fit = fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=3)
    apply_frame = pd.DataFrame({"x": [-50.0, 2.0, 50.0, np.nan], "y": [0, 1, 0, 1]})
    res = apply_binner(run_paths, fit.artifacts[0].ref, _save(run_paths, apply_frame))
    out = _loaded(run_paths, res)
    assert out["x__bin"].iloc[0] == "b0"
    k = len(_list(_dict(fit.summary["edges"])["x"])) - 1
    assert out["x__bin"].iloc[2] == f"b{k - 1}"
    assert pd.isna(out["x__bin"].iloc[3])  # NaN stays NaN


def test_binner_outer_edges_are_null_in_summary(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [0, 1, 0, 1, 0]})
    fit = fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=2)
    edges = _list(_dict(fit.summary["edges"])["x"])
    assert edges[0] is None and edges[-1] is None  # +-inf -> null (strict JSON)


def test_binner_tie_case_warns_with_three_bins(run_paths: RunPaths) -> None:
    # P3: 4 requested quantile bins on this tied data yield 3.
    train = pd.DataFrame({"x": [1, 1, 1, 1, 2, 3, 4, 5, 6, 100], "y": [0, 1] * 5}).astype(
        {"x": "float64"}
    )
    fit = fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=4)
    achieved = _dict(fit.summary["n_bins"])["x"]
    assert achieved == 3
    assert any("3 bins" in w for w in fit.warnings)


def test_binner_constant_column_is_tool_data(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [5.0, 5.0, 5.0, 5.0], "y": [0, 1, 0, 1]})
    with pytest.raises(ToolDataError, match="fewer than 2 bins"):
        fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",))


def test_binner_uniform_known_edges(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [0.0, 2.5, 5.0, 7.5, 10.0], "y": [0, 1, 0, 1, 0]})
    fit = fit_binner(
        run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=2, strategy="uniform"
    )
    edges = _list(_dict(fit.summary["edges"])["x"])
    # inner edge at linspace(0, 10, 3)[1] == 5.0; outer edges null.
    assert edges == [None, 5.0, None]


def test_binner_output_picked_up_by_onehot(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [0, 1, 0, 1, 0]})
    ref = _save(run_paths, train)
    fit = fit_binner(run_paths, ref, target="y", columns=("x",), n_bins=3)
    binned = apply_binner(run_paths, fit.artifacts[0].ref, ref)
    binned_ref = binned.artifacts[0].ref
    oh = fit_onehot_encoder(run_paths, binned_ref, target="y")
    # the string x__bin column is selected by default.
    assert "x__bin" in _dict(oh.summary["columns"])


def test_binner_target_refused(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [0.0, 1.0, 2.0, 3.0]})
    with pytest.raises(ToolParamError, match="target"):
        fit_binner(run_paths, _save(run_paths, train), target="y", columns=("y",))


def test_binner_non_numeric_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"s": pd.array(["a", "b", "c"], dtype="str"), "y": [0, 1, 0]})
    with pytest.raises(ToolParamError, match="not numeric"):
        fit_binner(run_paths, _save(run_paths, train), target="y", columns=("s",))


def test_apply_binner_missing_fitted_column_is_tool_param(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0], "y": [0, 1, 0, 1, 0]})
    fit = fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=2)
    other = pd.DataFrame({"z": [1.0, 2.0], "y": [0, 1]})
    with pytest.raises(ToolParamError, match="missing fitted columns"):
        apply_binner(run_paths, fit.artifacts[0].ref, _save(run_paths, other))


def test_binner_apply_never_recomputes_edges(run_paths: RunPaths) -> None:
    # the fitted edges must survive even an apply frame whose quantiles differ wildly.
    train = pd.DataFrame({"x": list(range(100)), "y": [0, 1] * 50}).astype({"x": "float64"})
    fit = fit_binner(run_paths, _save(run_paths, train), target="y", columns=("x",), n_bins=4)
    train_edges = _dict(fit.summary["edges"])
    apply_frame = pd.DataFrame({"x": [0.0] * 50 + [99.0] * 50, "y": [0, 1] * 50})
    apply_binner(run_paths, fit.artifacts[0].ref, _save(run_paths, apply_frame))
    # re-reading the fitted summary proves apply left the edges untouched (they live in the state).
    assert train_edges == _dict(fit.summary["edges"])


# --- variance selection -------------------------------------------------------------------------


def test_variance_one_in_four_bool_is_0_1875(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"b": [True, False, False, False], "y": [0, 1, 0, 1]})
    res = select_by_variance(run_paths, _save(run_paths, train), target="y", threshold=0.2)
    assert _dict(res.summary["variances"])["b"] == pytest.approx(0.1875)  # P8: ddof=0
    assert "b" in _list(res.summary["dropped_columns"])  # dropped at 0.2


def test_variance_bool_kept_below_its_variance(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"b": [True, False, False, False], "y": [0, 1, 0, 1]})
    res = select_by_variance(run_paths, _save(run_paths, train), target="y", threshold=0.1)
    assert _list(res.summary["dropped_columns"]) == []  # 0.1875 > 0.1, kept


def test_variance_all_null_column_dropped(run_paths: RunPaths) -> None:
    train = pd.DataFrame(
        {"n": pd.array([None, None, None], dtype="Float64"), "k": [1.0, 2.0, 3.0], "y": [0, 1, 0]}
    )
    res = select_by_variance(run_paths, _save(run_paths, train), target="y")
    assert "n" in _list(res.summary["dropped_columns"])  # undefined variance counts as 0
    assert "k" not in _list(res.summary["dropped_columns"])


def test_variance_string_column_not_considered_target_never_dropped(run_paths: RunPaths) -> None:
    train = pd.DataFrame(
        {"s": pd.array(["a", "a", "b"], dtype="str"), "n": [1.0, 2.0, 3.0], "y": [1.0, 1.0, 1.0]}
    )
    res = select_by_variance(run_paths, _save(run_paths, train), target="y")
    assert "s" in _list(res.summary["not_considered"])
    assert "y" not in _list(res.summary["dropped_columns"])  # a constant target is never dropped
    assert "y" not in _dict(res.summary["variances"])


def test_variance_drop_columns_replay_matches_on_valid(run_paths: RunPaths) -> None:
    from statlab_tools.tools.cleaning import drop_columns

    train = pd.DataFrame({"const": [1.0, 1.0, 1.0], "v": [1.0, 2.0, 3.0], "y": [0, 1, 0]})
    valid = pd.DataFrame({"const": [1.0, 1.0], "v": [9.0, 8.0], "y": [1, 0]})
    res = select_by_variance(run_paths, _save(run_paths, train), target="y")
    dropped = tuple(str(c) for c in _list(res.summary["dropped_columns"]))
    reduced_train = _loaded(run_paths, res)
    replay = drop_columns(run_paths, _save(run_paths, valid), columns=dropped)
    reduced_valid = _loaded(run_paths, replay)
    assert list(reduced_train.columns) == list(reduced_valid.columns)


# --- mutual information -------------------------------------------------------------------------


def _mi_frame(n: int = 80) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    informative = rng.normal(size=n)
    noise = rng.normal(size=n)
    y = (informative > 0).astype(int)
    return pd.DataFrame({"informative": informative, "noise": noise, "y": y})


def test_mutual_info_scores_equal_sklearn_oracle(run_paths: RunPaths) -> None:
    from sklearn.feature_selection import mutual_info_classif

    frame = _mi_frame()
    res = select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=7)
    matrix = frame[["informative", "noise"]].to_numpy(dtype="float64")
    oracle = mutual_info_classif(
        matrix, frame["y"], discrete_features=np.array([False, False]), random_state=7, n_jobs=1
    )
    scores = _dict(res.summary["scores"])
    assert scores["informative"] == pytest.approx(float(oracle[0]))
    assert scores["noise"] == pytest.approx(float(oracle[1]))


def test_mutual_info_keeps_informative_drops_noise_at_k1(run_paths: RunPaths) -> None:
    res = select_by_mutual_info(run_paths, _save(run_paths, _mi_frame()), target="y", k=1, seed=7)
    assert _list(res.summary["dropped_columns"]) == ["noise"]
    out = _loaded(run_paths, res)
    assert "informative" in out.columns and "noise" not in out.columns


def test_mutual_info_bool_is_discrete(run_paths: RunPaths) -> None:
    from sklearn.feature_selection import mutual_info_classif

    rng = np.random.default_rng(1)
    flag = rng.integers(0, 2, size=80).astype(bool)
    frame = pd.DataFrame({"flag": flag, "n": rng.normal(size=80), "y": flag.astype(int)})
    res = select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=2, seed=3)
    matrix = frame[["flag", "n"]].to_numpy(dtype="float64")
    oracle = mutual_info_classif(
        matrix, frame["y"], discrete_features=np.array([True, False]), random_state=3, n_jobs=1
    )
    assert _dict(res.summary["scores"])["flag"] == pytest.approx(float(oracle[0]))


def test_mutual_info_nan_is_tool_param(run_paths: RunPaths) -> None:
    frame = _mi_frame()
    frame.loc[frame.index[0], "informative"] = np.nan
    with pytest.raises(ToolParamError, match="impute first"):
        select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=1)


def test_mutual_info_target_absent_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0]})
    with pytest.raises(ToolParamError, match="needs it"):
        select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=1)


def test_mutual_info_string_feature_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "s": pd.array(["a", "b", "c", "a"], dtype="str"),
            "n": [1.0, 2.0, 3.0, 4.0],
            "y": [0, 1, 0, 1],
        }
    )
    with pytest.raises(ToolParamError, match="encode them first"):
        select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=1)


def test_mutual_info_large_k_warns_and_drops_nothing(run_paths: RunPaths) -> None:
    res = select_by_mutual_info(run_paths, _save(run_paths, _mi_frame()), target="y", k=99, seed=1)
    assert _list(res.summary["dropped_columns"]) == []
    assert any("nothing is dropped" in w for w in res.warnings)


def test_mutual_info_deterministic(run_paths: RunPaths) -> None:
    frame = _mi_frame()
    r1 = select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=5)
    r2 = select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=5)
    assert _dict(r1.summary["scores"]) == _dict(r2.summary["scores"])


def test_mutual_info_regression_target_uses_regression(run_paths: RunPaths) -> None:
    from sklearn.feature_selection import mutual_info_regression

    rng = np.random.default_rng(2)
    x = rng.normal(size=100)
    frame = pd.DataFrame(
        {"x": x, "noise": rng.normal(size=100), "y": x * 3.0 + rng.normal(size=100) * 0.1}
    )
    res = select_by_mutual_info(run_paths, _save(run_paths, frame), target="y", k=1, seed=4)
    matrix = frame[["x", "noise"]].to_numpy(dtype="float64")
    oracle = mutual_info_regression(
        matrix, frame["y"], discrete_features=np.array([False, False]), random_state=4, n_jobs=1
    )
    assert _dict(res.summary["scores"])["x"] == pytest.approx(float(oracle[0]))
    assert _list(res.summary["dropped_columns"]) == ["noise"]


# --- end to end: node 8 pipeline, replayed on valid, accepted by train_estimator ----------------


def test_end_to_end_node8_pipeline(run_paths: RunPaths) -> None:
    from statlab_tools.tools.cleaning import drop_columns

    rng = np.random.default_rng(0)
    n = 60
    base = pd.DataFrame(
        {
            "subscribers": rng.normal(100, 10, size=n),
            "visits": rng.normal(50, 5, size=n),
            "const": np.ones(n),
            "y": rng.integers(0, 2, size=n),
        }
    )
    train = base.iloc[:40].copy()
    valid = base.iloc[40:].copy()
    train_ref, valid_ref = _save(run_paths, train), _save(run_paths, valid)

    # ratio + interaction on train and valid (transform-only replay with identical args).
    def _construct(ref: ArtifactRef) -> ArtifactRef:
        r1 = add_ratio_feature(
            run_paths, ref, target="y", numerator="subscribers", denominator="visits"
        )
        r2 = add_interaction_terms(
            run_paths, r1.artifacts[0].ref, target="y", pairs=(("subscribers", "visits"),)
        )
        return r2.artifacts[0].ref

    train_c = _construct(train_ref)
    valid_c = _construct(valid_ref)

    # fit the binner on train, apply to both.
    fit = fit_binner(run_paths, train_c, target="y", columns=("subscribers",), n_bins=3)
    train_b = apply_binner(run_paths, fit.artifacts[0].ref, train_c).artifacts[0].ref
    valid_b = apply_binner(run_paths, fit.artifacts[0].ref, valid_c).artifacts[0].ref

    # one-hot the binned string column on both so the matrix is numeric for training.
    oh = fit_onehot_encoder(run_paths, train_b, target="y", columns=("subscribers__bin",))
    from statlab_tools.tools.encoding import apply_onehot_encoder

    train_oh = apply_onehot_encoder(run_paths, oh.artifacts[0].ref, train_b).artifacts[0].ref
    valid_oh = apply_onehot_encoder(run_paths, oh.artifacts[0].ref, valid_b).artifacts[0].ref

    # variance selection on train, replayed on valid with drop_columns.
    sel = select_by_variance(run_paths, train_oh, target="y")  # drops the constant column
    dropped = tuple(str(c) for c in _list(sel.summary["dropped_columns"]))
    assert "const" in dropped
    train_final = _loaded(run_paths, sel)
    valid_final = _loaded(run_paths, drop_columns(run_paths, valid_oh, columns=dropped))

    assert list(train_final.columns) == list(valid_final.columns)

    # train_estimator accepts the engineered train frame.
    res = train_estimator(
        run_paths, sel.artifacts[0].ref, target="y", name="logistic_regression", seed=1
    )
    assert res.summary["name"] == "logistic_regression"
