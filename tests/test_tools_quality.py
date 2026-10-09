"""Chunk 63a: `check_data_quality` and `fix_data_quality` on hand-built frames. Every expectation
is computed by hand from the frame shown in the test."""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.quality import check_data_quality, fix_data_quality


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded(paths: RunPaths, res: ToolSuccess) -> pd.DataFrame:
    return load_dataframe(paths.artifact(res.artifacts[0].ref, "dataframe"))


def _check(res: ToolSuccess, name: str) -> dict[str, JsonValue]:
    return _dict(_dict(res.summary["checks"])[name])


def test_disguised_missing_text_and_sentinel(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "city": ["a", "?", " unknown ", "b", None],
            "n": [1.0, -999.0, 3.0, 4.0, 5.0],
            "y": [0, 1, 0, 1, 0],
        }
    )
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y")
    check = _check(res, "disguised_missing")
    assert check["status"] == "warn"
    assert check["rows"] == 2
    assert check["columns"] == ["city", "n"]
    assert check["details"] == [
        {"column": "city", "count": 2, "values": [" unknown ", "?"]},
        {"column": "n", "count": 1, "values": ["-999"]},
    ]
    assert _dict(res.summary["fixable"])["mark_missing"] == {
        "city": [" unknown ", "?"],
        "n": ["-999"],
    }


def test_mixed_types_numeric_text_minority(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"code": ["1", "2", "3", "4", "x"], "y": [0, 1, 0, 1, 0]})
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y")
    check = _check(res, "mixed_types")
    assert check["status"] == "warn"
    assert check["rows"] == 1
    assert check["details"] == [{"column": "code", "numeric_share": 0.8, "examples": ["x"]}]


def test_category_variants_groups_and_merge_plan(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"sex": ["Male", "Male", "male ", "MALE", "Female"], "y": [0, 1, 0, 1, 0]})
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y")
    check = _check(res, "category_variants")
    assert check["status"] == "warn"
    assert check["rows"] == 2
    assert check["details"] == [{"column": "sex", "groups": [["Male", "MALE", "male "]]}]
    assert _dict(res.summary["fixable"])["merge_variants"] == ["sex"]


def test_duplicate_rows_and_conflicting_labels(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"id": [1, 2, 3, 4], "x": [1, 1, 1, 2], "y": ["a", "a", "b", "a"]})
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y", id_columns=("id",))
    dups = _check(res, "duplicate_rows")
    assert dups["status"] == "warn"
    assert dups["rows"] == 1
    assert _dict(dups["details"])["example_rows"] == [1]
    conflicts = _check(res, "conflicting_labels")
    assert conflicts["status"] == "warn"
    assert conflicts["rows"] == 3
    assert _dict(conflicts["details"])["groups"] == 1


def test_hundred_rows_near_constant_rare_pairs_and_target(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "c": ["k"] * 99 + ["z"],
            "e": [float(i) for i in range(100)],
            "f": [float(i) for i in range(100)],
            "y": ["a"] * 50 + ["b"] * 47 + ["c"] * 3,
        }
    )
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y")
    assert _check(res, "near_constant")["details"] == [{"column": "c", "value": "k", "share": 0.99}]
    assert _check(res, "rare_categories")["details"] == [
        {"column": "c", "levels": 1, "rows": 1, "examples": ["z"]}
    ]
    assert _check(res, "duplicate_columns")["details"] == {"pairs": [["e", "f"]]}
    target = _check(res, "target_health")
    assert target["status"] == "fail"
    assert target["details"] == {"nulls": 0, "small_classes": {"c": 3}}
    assert res.summary["passed"] is False


def test_continuous_target_skips_conflicting_labels(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1.0] * 25, "y": [float(i) for i in range(25)]})
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y")
    check = _check(res, "conflicting_labels")
    assert check["status"] == "skipped"
    assert check["details"] == {"reason": "continuous target"}


def test_clean_frame_passes_every_check(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": ["p", "q"] * 3})
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y", min_class_rows=2)
    for name, check in _dict(res.summary["checks"]).items():
        assert _dict(check)["status"] == "pass", name
    assert res.summary["passed"] is True
    assert res.warnings == ()


def test_fix_data_quality_marks_missing_and_merges_categorical(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "city": pd.Categorical(["Budapest", "budapest ", "?", "Szeged"]),
            "n": [1.0, -999.0, 3.0, 4.0],
            "y": [0, 1, 0, 1],
        }
    )
    res = fix_data_quality(
        run_paths,
        _save(run_paths, frame),
        target="y",
        mark_missing={"city": ("?",), "n": ("-999",)},
        merge_variants=("city",),
    )
    out = _loaded(run_paths, res)
    assert isinstance(out["city"].dtype, pd.CategoricalDtype)
    assert out["city"].isna().tolist() == [False, False, True, False]
    assert out["city"].dropna().tolist() == ["Budapest", "Budapest", "Szeged"]
    assert out["n"].isna().tolist() == [False, True, False, False]
    assert out["n"].dropna().tolist() == [1.0, 3.0, 4.0]
    assert out.index.tolist() == [0, 1, 2, 3]
    assert res.summary["marked_missing"] == {"city": 1, "n": 1}
    assert res.summary["merged"] == {"city": {"budapest ": "Budapest"}}
    assert res.artifacts[0].label == "quality_fixed"


def test_fix_data_quality_errors_name_the_field(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"city": ["a", "b"], "n": [1.0, 2.0], "y": [0, 1]})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolParamError) as no_fix:
        fix_data_quality(run_paths, ref, target="y")
    assert "mark_missing" in no_fix.value.field_errors
    with pytest.raises(ToolParamError) as unknown:
        fix_data_quality(run_paths, ref, target="y", mark_missing={"nope": ("x",)})
    assert "mark_missing" in unknown.value.field_errors
    with pytest.raises(ToolParamError) as target_merge:
        fix_data_quality(run_paths, ref, target="y", merge_variants=("y",))
    assert "merge_variants" in target_merge.value.field_errors
    with pytest.raises(ToolParamError) as numeric_merge:
        fix_data_quality(run_paths, ref, target="y", merge_variants=("n",))
    assert "merge_variants" in numeric_merge.value.field_errors
    with pytest.raises(ToolParamError) as target_mark:
        fix_data_quality(run_paths, ref, target="y", mark_missing={"y": ("1",)})
    assert "mark_missing" in target_mark.value.field_errors


def test_conflicting_labels_without_features_passes_with_zero_groups(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"id": [1, 2], "y": ["a", "b"]})
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y", id_columns=("id",))
    assert _check(res, "conflicting_labels") == {
        "status": "pass",
        "rows": 0,
        "columns": [],
        "details": {"groups": 0, "example_rows": []},
    }


def test_other_dtypes_are_skipped_by_text_checks(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "flag": [True, False] * 5,
            "when": pd.date_range("2024-01-01", periods=10),
            "empty": pd.Series([None] * 10, dtype="str"),
            "grp": ["a"] * 5 + ["b"] * 5,
            "y": [0, 1] * 5,
        }
    )
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y")
    for name in ("disguised_missing", "mixed_types", "near_constant", "rare_categories"):
        assert _check(res, name)["status"] == "pass", name


def test_target_nulls_warn_with_null_rows(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "y": ["p", "q", "p", "q", "p", None],
        }
    )
    res = check_data_quality(run_paths, _save(run_paths, frame), target="y", min_class_rows=2)
    assert _check(res, "target_health") == {
        "status": "warn",
        "rows": 1,
        "columns": ["y"],
        "details": {"nulls": 1, "small_classes": {}},
    }


def test_plain_text_merge_keeps_text_dtype(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"sex": ["Male", "male "], "y": [0, 1]})
    res = fix_data_quality(run_paths, _save(run_paths, frame), target="y", merge_variants=("sex",))
    out = _loaded(run_paths, res)
    assert out["sex"].tolist() == ["Male", "Male"]
    assert not isinstance(out["sex"].dtype, pd.CategoricalDtype)
    assert res.summary["merged"] == {"sex": {"male ": "Male"}}
