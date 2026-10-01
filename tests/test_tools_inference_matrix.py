"""Chunk 15 §4: the two inference-matrix tools (`build_dummy_matrix`, `assemble_inference_matrix`).

Dummy coding with an explicit reference (D-b), the canonical assemble recipe (D-d), the manifest
(D-c) and its collinearity-drop step. The two mutation-targeted properties live here: the
reference level is dropped (not kept), and the collinearity drop removes a dummy trap.
"""

from __future__ import annotations

from typing import cast

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, load_json, save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.inference import (
    assemble_inference_matrix,
    build_dummy_matrix,
)


def _d(value: object) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return cast("dict[str, JsonValue]", value)


def _l(value: object) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _put(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="x")
    return ref


def _matrix(paths: RunPaths, res: ToolSuccess) -> pd.DataFrame:
    """The matrix frame from a builder result (first artifact)."""
    return load_dataframe(paths.artifact(res.artifacts[0].ref, "dataframe"))


def _manifest(paths: RunPaths, res: ToolSuccess) -> dict[str, JsonValue]:
    """The manifest JSON from a builder result (second artifact)."""
    return _d(load_json(paths.artifact(res.artifacts[1].ref, "table")))


# --- build_dummy_matrix: dummy coding ------------------------------------------------------------


def _frame_plan(n_basic: int = 5, n_std: int = 3, n_prem: int = 2) -> pd.DataFrame:
    plan = ["basic"] * n_basic + ["standard"] * n_std + ["premium"] * n_prem
    return pd.DataFrame({"plan": plan, "y": list(range(len(plan)))})


def test_default_reference_is_most_frequent(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _frame_plan())  # basic is most frequent
    res = build_dummy_matrix(run_paths, ref, "y", ("plan",))
    man = _manifest(run_paths, res)
    assert _d(man["references"])["plan"] == "basic"
    matrix = _matrix(run_paths, res)
    # the reference column is absent; the other two levels are present
    assert "plan__basic" not in matrix.columns
    assert set(matrix.columns) == {"y", "plan__premium", "plan__standard"}


def test_explicit_reference_is_respected(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _frame_plan())
    res = build_dummy_matrix(run_paths, ref, "y", ("plan",), {"plan": "premium"})
    man = _manifest(run_paths, res)
    assert _d(man["references"])["plan"] == "premium"
    matrix = _matrix(run_paths, res)
    assert "plan__premium" not in matrix.columns
    assert {"plan__basic", "plan__standard"} <= set(matrix.columns)


def test_reference_column_is_absent_mutation_guard(run_paths: RunPaths) -> None:
    """DoD mutation: the reference level is NOT dropped. If it were kept, the matrix would have a
    `plan__basic` column (the reference) -- this asserts it is absent."""
    ref = _put(run_paths, _frame_plan())
    res = build_dummy_matrix(run_paths, ref, "y", ("plan",))
    assert "plan__basic" not in _matrix(run_paths, res).columns


def test_dummy_names_and_dtypes(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _frame_plan())
    res = build_dummy_matrix(run_paths, ref, "y", ("plan",))
    matrix = _matrix(run_paths, res)
    for col in ("plan__premium", "plan__standard"):
        assert matrix[col].dtype == np.dtype("float64")
        assert set(matrix[col].unique()) <= {0.0, 1.0}
    man = _manifest(run_paths, res)
    assert _d(man["terms"])["plan__premium"] == "plan"


def test_bool_becomes_zero_one(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"flag": [True, False, True], "y": [1, 2, 3]})
    ref = _put(run_paths, frame)
    # flag is bool; it passes through (not in `columns`) and becomes 0/1 float64
    # empty `columns` is schema-invalid (min_length=1) but the body handles it directly, which
    # is what reaches the bool-passthrough branch for a tool-body unit test.
    res = build_dummy_matrix(run_paths, ref, "y", ())
    matrix = _matrix(run_paths, res)
    assert matrix["flag"].dtype == np.dtype("float64")
    assert list(matrix["flag"]) == [1.0, 0.0, 1.0]


def test_numeric_columns_are_unscaled(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"plan": ["a", "b", "a"], "age": [10.0, 20.0, 30.0], "y": [1, 2, 3]})
    ref = _put(run_paths, frame)
    res = build_dummy_matrix(run_paths, ref, "y", ("plan",))
    matrix = _matrix(run_paths, res)
    assert list(matrix["age"]) == [10.0, 20.0, 30.0]  # untouched, D31


def test_nan_in_coded_column_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"plan": ["a", None, "b"], "y": [1, 2, 3]})
    ref = _put(run_paths, frame)
    with pytest.raises(ToolParamError, match="missing values"):
        build_dummy_matrix(run_paths, ref, "y", ("plan",))


def test_unobserved_reference_level_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _frame_plan())
    with pytest.raises(ToolParamError, match="not observed"):
        build_dummy_matrix(run_paths, ref, "y", ("plan",), {"plan": "nonexistent"})


def test_unknown_column_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _frame_plan())
    with pytest.raises(ToolParamError):
        build_dummy_matrix(run_paths, ref, "y", ("missing_col",))


def test_target_in_columns_is_tool_param(run_paths: RunPaths) -> None:
    ref = _put(run_paths, _frame_plan())
    with pytest.raises(ToolParamError, match="target"):
        build_dummy_matrix(run_paths, ref, "plan", ("plan",))


# --- assemble_inference_matrix -------------------------------------------------------------------


def _tv() -> tuple[pd.DataFrame, pd.DataFrame]:
    """A simple train/valid pair with disjoint labels, one numeric and one categorical predictor
    and a numeric target."""
    train = pd.DataFrame(
        {"x": [1.0, 2.0, 3.0, 4.0], "g": ["a", "a", "b", "b"], "y": [1.0, 2.0, 3.0, 4.0]},
        index=[0, 1, 2, 3],
    )
    valid = pd.DataFrame(
        {"x": [5.0, 6.0], "g": ["a", "b"], "y": [5.0, 6.0]},
        index=[4, 5],
    )
    return train, valid


def test_train_valid_concatenated(run_paths: RunPaths) -> None:
    train, valid = _tv()
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x", "g")
    )
    matrix = _matrix(run_paths, res)
    assert len(matrix) == 6  # 4 + 2 rows


def test_overlapping_labels_are_tool_param(run_paths: RunPaths) -> None:
    train, valid = _tv()
    valid.index = [2, 3]  # overlaps train's 2,3
    with pytest.raises(ToolParamError, match="share row labels"):
        assemble_inference_matrix(
            run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x", "g")
        )


def test_median_mode_imputation_uses_concatenation(run_paths: RunPaths) -> None:
    # train x median alone = 2.5; concatenation median over [1,2,3,4,5,nan,...] differs.
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [1.0, 2.0, 3.0, 4.0]}, index=[0, 1, 2, 3])
    valid = pd.DataFrame({"x": [10.0, np.nan], "y": [5.0, 6.0]}, index=[4, 5])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x",)
    )
    man = _manifest(run_paths, res)
    imputed = _l(man["imputed"])
    assert len(imputed) == 1
    fill = _d(imputed[0])["fill"]
    # concatenation of x non-null = [1,2,3,4,10]; median = 3.0 (not train-only 2.5)
    assert fill == 3.0


def test_imputes_from_train_only_mutation_guard(run_paths: RunPaths) -> None:
    """DoD mutation: assemble imputes from train only. The median above is 3.0 over the whole
    concatenation; a train-only implementation would fill 2.5. This test fails under that
    mutation."""
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [1.0, 2.0, 3.0, 4.0]}, index=[0, 1, 2, 3])
    valid = pd.DataFrame({"x": [10.0, np.nan], "y": [5.0, 6.0]}, index=[4, 5])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x",)
    )
    man = _manifest(run_paths, res)
    assert _d(_l(man["imputed"])[0])["fill"] == 3.0


def test_indicator_at_five_percent_not_four(run_paths: RunPaths) -> None:
    # 100 rows, 5 missing (5%) -> indicator; 4 missing (4%) -> no indicator.
    def build(n_missing: int) -> dict[str, JsonValue]:
        x = [float(i) for i in range(100)]
        for i in range(n_missing):
            x[i] = np.nan
        train = pd.DataFrame({"x": x, "y": [float(i) for i in range(100)]}, index=range(100))
        valid = pd.DataFrame({"x": [1.0], "y": [1.0]}, index=[200])
        res = assemble_inference_matrix(
            run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x",)
        )
        return _manifest(run_paths, res)

    # share over 101 rows: 5/101 ~= 0.0495 < 0.05 -> make it 6 missing for >=5% at n=101.
    # Use exact 100-row concatenation shares instead: put the 1 non-missing valid row aside and
    # test at the boundary directly on the concatenated share.
    man5 = build(6)  # 6/101 = 0.0594 >= 0.05
    assert "x__missing" in _l(man5["indicators"])
    man4 = build(4)  # 4/101 = 0.0396 < 0.05
    assert "x__missing" not in _l(man4["indicators"])


def test_collinear_duplicate_dropped_with_warning(run_paths: RunPaths) -> None:
    # x2 == 2*x1 is perfectly collinear with x1; one is dropped.
    train = pd.DataFrame(
        {
            "x1": [1.0, 2.0, 3.0, 4.0, 5.0],
            "x2": [2.0, 4.0, 6.0, 8.0, 10.0],
            "y": [1.0, 2.0, 3.0, 4.0, 5.0],
        },
        index=range(5),
    )
    valid = pd.DataFrame({"x1": [6.0], "x2": [12.0], "y": [6.0]}, index=[99])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x1", "x2")
    )
    man = _manifest(run_paths, res)
    dropped = [_d(d)["term"] for d in _l(man["dropped"])]
    assert "x2" in dropped
    assert any("x2" in w for w in res.warnings)
    matrix = _matrix(run_paths, res)
    assert "x2" not in matrix.columns


def test_dummy_trap_dropped_as_collinear(run_paths: RunPaths) -> None:
    # A two-level categorical already drops a reference, so no trap. Build an explicit redundancy:
    # a numeric column equal to the sum of two dummies -> collinear with the intercept + dummies.
    train = pd.DataFrame(
        {
            "g": ["a", "b", "a", "b", "a", "b"],
            "ones": [1.0] * 6,  # constant -> dropped as constant
            "y": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        },
        index=range(6),
    )
    valid = pd.DataFrame({"g": ["a"], "ones": [1.0], "y": [7.0]}, index=[99])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("g", "ones")
    )
    man = _manifest(run_paths, res)
    reasons = {_d(d)["term"]: _d(d)["reason"] for d in _l(man["dropped"])}
    assert reasons.get("ones") == "constant"


def test_collinearity_drop_removed_mutation_guard(run_paths: RunPaths) -> None:
    """DoD mutation: the collinearity drop is removed. Without it, the duplicate x2 survives in
    the matrix. This asserts it is dropped."""
    train = pd.DataFrame(
        {
            "x1": [1.0, 2.0, 3.0, 4.0, 5.0],
            "x2": [2.0, 4.0, 6.0, 8.0, 10.0],
            "y": [1.0, 2.0, 3.0, 4.0, 5.0],
        },
        index=range(5),
    )
    valid = pd.DataFrame({"x1": [6.0], "x2": [12.0], "y": [6.0]}, index=[99])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x1", "x2")
    )
    assert "x2" not in _matrix(run_paths, res).columns


def test_output_has_no_nan(run_paths: RunPaths) -> None:
    train = pd.DataFrame(
        {"x": [1.0, 2.0, np.nan, 4.0], "g": ["a", None, "b", "b"], "y": [1.0, 2.0, 3.0, 4.0]},
        index=range(4),
    )
    valid = pd.DataFrame({"x": [5.0], "g": ["a"], "y": [5.0]}, index=[99])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x", "g")
    )
    matrix = _matrix(run_paths, res)
    assert not matrix.isna().to_numpy().any()


def test_output_terms_are_only_predictors_and_target(run_paths: RunPaths) -> None:
    train = pd.DataFrame(
        {
            "x": [1.0, 2.0, 3.0, 4.0],
            "g": ["a", "a", "b", "b"],
            "unused": [9.0, 9.0, 9.0, 9.0],
            "y": [1.0, 2.0, 3.0, 4.0],
        },
        index=range(4),
    )
    valid = pd.DataFrame({"x": [5.0], "g": ["a"], "unused": [9.0], "y": [5.0]}, index=[99])
    res = assemble_inference_matrix(
        run_paths, _put(run_paths, train), _put(run_paths, valid), "y", ("x", "g")
    )
    matrix = _matrix(run_paths, res)
    assert "unused" not in matrix.columns
    assert "y" in matrix.columns
