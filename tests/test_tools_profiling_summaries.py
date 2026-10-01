"""`profile_schema`, `summarize_numeric`, `summarize_categorical`, `report_missingness`,
`summarize_target`, `compute_correlations`, `detect_outliers`. Chunk 09 §3.3, §4.3.

Calls tools through their decorated callables directly, same convention as chunk 08's intake
tests. `churn_frame`/`messy_frame` are the two fixtures every tool is exercised against.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_json, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.profiling import (
    CO_MISSING_JACCARD,
    CORRELATION_TOP_PAIRS,
    IMBALANCE_WARNING,
    OUTLIER_ROWS_CAP,
    compute_correlations,
    detect_outliers,
    plot_distribution,
    plot_target_relation,
    profile_schema,
    report_missingness,
    summarize_categorical,
    summarize_numeric,
    summarize_target,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]

pytestmark = pytest.mark.usefixtures("tool_settings")


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _num(value: JsonValue) -> float:
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    return value


def _str(value: JsonValue) -> str:
    assert isinstance(value, str)
    return value


def _save(run_paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(run_paths.artifact(ref, "dataframe"), frame, ref=ref)
    return ref


# --- profile_schema --------------------------------------------------------------------------


def test_profile_schema_happy_path_on_churn_frame(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = profile_schema(run_paths, ref)
    assert result.summary["rows"] == 240
    assert result.summary["cols"] == 7
    columns = _dict(result.summary["columns"])
    assert set(columns) == set(churn_frame_fixture.columns)
    customer_id = _dict(columns["customer_id"])
    assert customer_id["n_unique"] == 240
    assert customer_id["n_null"] == 0
    assert type(customer_id["n_unique"]) is int
    assert type(customer_id["n_null"]) is int


def test_profile_schema_memory_bytes_is_plain_int(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = profile_schema(run_paths, ref)
    assert type(result.summary["memory_bytes"]) is int
    assert result.summary["memory_bytes"] > 0


def test_profile_schema_reports_dtype_as_string(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = profile_schema(run_paths, ref)
    columns = _dict(result.summary["columns"])
    assert _dict(columns["event_time"])["dtype"] == "datetime64[us]"


# --- summarize_numeric -------------------------------------------------------------------------


def test_summarize_numeric_happy_path_on_churn_frame(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = summarize_numeric(run_paths, ref)
    columns = _dict(result.summary["columns"])
    assert "age" in columns
    age = _dict(columns["age"])
    assert age["count"] == 240
    assert _num(age["min"]) >= 18
    assert _num(age["max"]) < 80


def test_summarize_numeric_unknown_column_is_tool_param_naming_all_unknowns(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as excinfo:
        summarize_numeric(run_paths, ref, columns=("age", "nope1", "nope2"))
    assert "nope1" in str(excinfo.value.field_errors)
    assert "nope2" in str(excinfo.value.field_errors)


def test_summarize_numeric_wrong_dtype_column_named_explicitly_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError):
        summarize_numeric(run_paths, ref, columns=("plan",))


def test_summarize_numeric_columns_none_selects_every_numeric_column(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = summarize_numeric(run_paths, ref, columns=None)
    columns = _dict(result.summary["columns"])
    assert set(columns) == {"customer_id", "age", "churned"}


def test_summarize_numeric_no_numeric_columns_returns_empty_not_error(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": ["x", "y"], "b": ["p", "q"]})
    ref = _save(run_paths, frame)
    result = summarize_numeric(run_paths, ref, columns=None)
    assert _dict(result.summary["columns"]) == {}


def test_s1_summarize_numeric_counts_are_plain_int_not_np_int64(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = summarize_numeric(run_paths, ref, columns=("age",))
    age = _dict(_dict(result.summary["columns"])["age"])
    assert type(age["count"]) is int


def test_s2_summarize_numeric_skips_constant_column(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"const": [5.0] * 20, "varies": list(range(20))})
    ref = _save(run_paths, frame)
    result = summarize_numeric(run_paths, ref)
    assert "const" in _dict(result.summary["skipped"])
    assert "const" not in _dict(result.summary["columns"])


def test_s3_summarize_numeric_excludes_bool_with_columns_none(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"flag": [True, False, True], "n": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = summarize_numeric(run_paths, ref, columns=None)
    assert "flag" not in _dict(result.summary["columns"])


def test_summarize_numeric_skew_is_null_below_three_values(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"n": [1.0] * 2})
    ref = _save(run_paths, frame)
    result = summarize_numeric(run_paths, ref, columns=None)
    assert _dict(result.summary["skipped"]).get("n") is not None  # constant -- skipped entirely


def test_summarize_numeric_skew_present_for_non_constant_column(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"n": [1.0, 2.0, 3.0, 4.0, 5.0] * 4})
    ref = _save(run_paths, frame)
    result = summarize_numeric(run_paths, ref, columns=("n",))
    assert "skew" in _dict(_dict(result.summary["columns"])["n"])


# --- summarize_categorical ----------------------------------------------------------------------


def test_summarize_categorical_happy_path_on_churn_frame(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = summarize_categorical(run_paths, ref)
    columns = _dict(result.summary["columns"])
    assert "plan" in columns
    plan = _dict(columns["plan"])
    assert plan["n_unique"] == 3
    top = _list(plan["top"])
    assert len(top) == 3


def test_summarize_categorical_unknown_column_names_all_unknowns(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as excinfo:
        summarize_categorical(run_paths, ref, columns=("plan", "bogus"))
    assert "bogus" in str(excinfo.value.field_errors)


def test_summarize_categorical_wrong_dtype_named_explicitly_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError):
        summarize_categorical(run_paths, ref, columns=("age",))


def test_summarize_categorical_columns_none_selects_every_non_numeric_column(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = summarize_categorical(run_paths, ref, columns=None)
    columns = _dict(result.summary["columns"])
    assert set(columns) == {"plan", "signup_date", "satisfaction", "notes_free_text"}


def test_s3_summarize_categorical_includes_bool(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"flag": [True, False, True, True], "n": [1, 2, 3, 4]})
    ref = _save(run_paths, frame)
    result = summarize_categorical(run_paths, ref, columns=None)
    assert "flag" in _dict(result.summary["columns"])


def test_summarize_categorical_top_k_caps_and_sets_truncated(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"c": [f"v{i}" for i in range(20)]})
    ref = _save(run_paths, frame)
    result = summarize_categorical(run_paths, ref, columns=("c",), top_k=5)
    c = _dict(_dict(result.summary["columns"])["c"])
    assert len(_list(c["top"])) == 5
    assert c["truncated"] is True


def test_summarize_categorical_no_truncation_when_top_k_covers_all(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"c": ["a", "b", "a", "c"]})
    ref = _save(run_paths, frame)
    result = summarize_categorical(run_paths, ref, columns=("c",), top_k=10)
    c = _dict(_dict(result.summary["columns"])["c"])
    assert c["truncated"] is False


@pytest.mark.asyncio
async def test_summarize_categorical_top_k_out_of_bounds_rejected_by_schema(
    run_paths_over_mcp: str,
    mcp_session: SessionFactory,
    churn_frame_fixture: pd.DataFrame,
    tool_settings: object,
) -> None:
    """`top_k`'s bounds are a `Field(ge=, le=)` constraint enforced at the MCP schema boundary,
    not inside the plain Python function -- calling the undecorated body directly bypasses
    pydantic validation entirely, so this needs a real session, same as chunk 08's L3."""
    from statlab_tools.settings import ToolSettings

    assert isinstance(tool_settings, ToolSettings)
    paths = RunPaths(data_dir=tool_settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    ref = _save(paths, churn_frame_fixture)

    async with mcp_session() as session:
        too_low = await session.call_tool(
            "summarize_categorical",
            {"project": "proj", "run": run_paths_over_mcp, "frame": ref, "top_k": 0},
        )
        assert too_low.is_error
        too_high = await session.call_tool(
            "summarize_categorical",
            {"project": "proj", "run": run_paths_over_mcp, "frame": ref, "top_k": 51},
        )
        assert too_high.is_error


def test_summarize_categorical_shares_are_of_non_null_rows(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"c": ["a", "a", None, "b"]})
    ref = _save(run_paths, frame)
    result = summarize_categorical(run_paths, ref, columns=("c",))
    top = _list(_dict(_dict(result.summary["columns"])["c"])["top"])
    shares = {_dict(e)["value"]: _num(_dict(e)["share"]) for e in top}
    assert shares["a"] == pytest.approx(2 / 3)


def test_summarize_categorical_all_null_column_is_skipped(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"c": [None, None, None], "n": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = summarize_categorical(run_paths, ref, columns=None)
    assert "c" in _dict(result.summary["skipped"])


# --- report_missingness -------------------------------------------------------------------------


def test_report_missingness_happy_path_on_churn_frame(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = report_missingness(run_paths, ref)
    assert result.summary["complete_rows"] == 240
    assert result.summary["rows_with_any_missing"] == 0
    assert _dict(result.summary["columns"]) == {}


def test_report_missingness_on_messy_frame_counts_match(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = report_missingness(run_paths, ref)
    columns = _dict(result.summary["columns"])
    assert _dict(columns["int_with_nulls"])["n_null"] == 12


def test_s6_co_missing_pair_at_jaccard_one(run_paths: RunPaths, messy_frame: pd.DataFrame) -> None:
    ref = _save(run_paths, messy_frame)
    result = report_missingness(run_paths, ref)
    co_missing = _list(result.summary["co_missing"])
    entries = [_dict(e) for e in co_missing]
    pairs = {frozenset((e["a"], e["b"])) for e in entries}
    assert frozenset({"co_a", "co_b"}) in pairs
    entry = next(e for e in entries if frozenset((e["a"], e["b"])) == frozenset({"co_a", "co_b"}))
    assert entry["jaccard"] == pytest.approx(1.0)


def test_s6_independent_missing_pair_not_in_co_missing(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "a": [None, 1, 2, 3, None, 5, 6, 7, 8, 9],
            "b": [1, None, 2, 3, 4, None, 6, 7, 8, 9],
        }
    )
    ref = _save(run_paths, frame)
    result = report_missingness(run_paths, ref)
    co_missing = _list(result.summary["co_missing"])
    pairs = {frozenset((_dict(e)["a"], _dict(e)["b"])) for e in co_missing}
    assert frozenset({"a", "b"}) not in pairs


def test_report_missingness_below_jaccard_threshold_not_reported(run_paths: RunPaths) -> None:
    a = [None] * 10 + list(range(10))
    b = [None] * 2 + list(range(18))
    frame = pd.DataFrame({"a": a, "b": b})
    ref = _save(run_paths, frame)
    result = report_missingness(run_paths, ref)
    co_missing = _list(result.summary["co_missing"])
    pairs = {frozenset((_dict(e)["a"], _dict(e)["b"])) for e in co_missing}
    assert frozenset({"a", "b"}) not in pairs


def test_report_missingness_co_missing_sorted_descending(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "a": [None, None, 1, 2, 3, 4, 5, 6, 7, 8],
            "b": [None, None, 1, 2, 3, 4, 5, 6, 7, None],
            "c": [None, 1, None, 2, 3, 4, 5, 6, 7, 8],
        }
    )
    ref = _save(run_paths, frame)
    result = report_missingness(run_paths, ref)
    co_missing = _list(result.summary["co_missing"])
    jaccards = [_num(_dict(e)["jaccard"]) for e in co_missing]
    assert jaccards == sorted(jaccards, reverse=True)


def test_report_missingness_jaccard_constant_is_point_eight() -> None:
    assert CO_MISSING_JACCARD == 0.8


# --- summarize_target ----------------------------------------------------------------------------


def test_s7_summarize_target_class_shares_sum_to_one(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = summarize_target(run_paths, ref, target="churned")
    assert result.summary["kind"] == "class"
    classes = _dict(result.summary["classes"])
    shares = [_num(_dict(c)["share"]) for c in classes.values()]
    assert sum(shares) == pytest.approx(1.0)
    minority_share = result.summary["minority_share"]
    assert isinstance(minority_share, float)
    assert 0.25 < minority_share < 0.35
    assert result.summary["imbalance_ratio"] == pytest.approx(max(shares) / min(shares))


def test_s8_imbalance_warning_fires_at_five_percent(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"t": [0] * 95 + [1] * 5})
    ref = _save(run_paths, frame)
    result = summarize_target(run_paths, ref, target="t")
    assert any("below" in w for w in result.warnings)


def test_s8_imbalance_warning_absent_at_thirty_percent(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"t": [0] * 70 + [1] * 30})
    ref = _save(run_paths, frame)
    result = summarize_target(run_paths, ref, target="t")
    assert result.warnings == ()


def test_imbalance_warning_constant_is_point_one() -> None:
    assert IMBALANCE_WARNING == 0.1


def test_s9_summarize_target_continuous_branch_for_many_valued_numeric(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"t": list(range(100)) + list(range(100))})
    ref = _save(run_paths, frame)
    result = summarize_target(run_paths, ref, target="t")
    assert result.summary["kind"] == "continuous"
    assert "mean" in result.summary
    assert "classes" not in result.summary


def test_summarize_target_unknown_target_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError):
        summarize_target(run_paths, ref, target="nonexistent")


def test_summarize_target_string_target_is_always_class(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"t": ["a", "b", "a", "a"]})
    ref = _save(run_paths, frame)
    result = summarize_target(run_paths, ref, target="t")
    assert result.summary["kind"] == "class"


def test_summarize_target_counts_are_plain_ints(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"t": [0, 1, 0, 1, 1]})
    ref = _save(run_paths, frame)
    result = summarize_target(run_paths, ref, target="t")
    classes = _dict(result.summary["classes"])
    for cls in classes.values():
        assert type(_dict(cls)["count"]) is int


# --- compute_correlations -----------------------------------------------------------------------


def test_s10_correlation_artifact_strict_json_square_symmetric_diagonal_one(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = compute_correlations(run_paths, ref)
    assert len(result.artifacts) == 1
    meta = result.artifacts[0]
    assert meta.kind == "table"
    payload = load_json(run_paths.artifact(meta.ref, "table"))
    assert isinstance(payload, dict)
    matrix = payload["matrix"]
    assert isinstance(matrix, list)
    n = len(matrix)
    for row in matrix:
        assert isinstance(row, list)
        assert len(row) == n
    for i in range(n):
        row_i = matrix[i]
        assert isinstance(row_i, list)
        assert row_i[i] == pytest.approx(1.0)
        for j in range(n):
            row_j = matrix[j]
            assert isinstance(row_j, list)
            assert row_i[j] == pytest.approx(row_j[i])
    text = run_paths.artifact(meta.ref, "table").read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda tok: (_ for _ in ()).throw(ValueError(tok)))


def test_s11_spearman_matches_rank_correlation_not_pearson(run_paths: RunPaths) -> None:
    x = list(range(1, 21))
    y = [v**3 for v in x]  # monotone, non-linear
    frame = pd.DataFrame({"x": x, "y": y})
    ref = _save(run_paths, frame)
    pearson = compute_correlations(run_paths, ref, method="pearson")
    spearman = compute_correlations(run_paths, ref, method="spearman")
    pearson_entry = _dict(_list(pearson.summary["strongest"])[0])
    spearman_entry = _dict(_list(spearman.summary["strongest"])[0])
    pearson_r = pearson_entry["r"]
    spearman_r = spearman_entry["r"]
    assert isinstance(spearman_r, float) and isinstance(pearson_r, float)
    assert spearman_r >= 0.999
    assert pearson_r < spearman_r


def test_s11_spearman_does_not_import_scipy() -> None:
    """Run in a subprocess: the test session may have already imported scipy elsewhere (e.g.
    via statsmodels in another test module), so checking `sys.modules` in-process would not
    prove this specific call avoids importing it."""
    repo_root = Path(__file__).parent.parent
    script = """
import sys
import pandas as pd
from statlab_tools.tools.profiling import compute_correlations
from statlab_contracts.layout import RunPaths
from statlab_contracts.artifacts import new_artifact_ref
from statlab_tools.artifacts import save_dataframe
from statlab.storage import ensure_run_layout
import tempfile, pathlib

with tempfile.TemporaryDirectory() as d:
    paths = RunPaths(data_dir=pathlib.Path(d), slug="proj", run_id="r_test0000000000")
    ensure_run_layout(paths)
    frame = pd.DataFrame({"a": list(range(10)), "b": [v**2 for v in range(10)]})
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref)
    compute_correlations(paths, ref, method="spearman")
    assert "scipy" not in sys.modules, "scipy was imported"
    print("OK")
"""
    result = subprocess.run(  # noqa: S603  # `script` is a hardcoded literal defined above, not untrusted input; sys.executable is the running interpreter
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=str(repo_root)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_s12_strongest_no_self_pairs_no_duplicates_sorted_by_abs_r(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = compute_correlations(run_paths, ref)
    strongest = [_dict(e) for e in _list(result.summary["strongest"])]
    pairs_seen: set[object] = set()
    for e in strongest:
        a, b = e["a"], e["b"]
        assert a != b
        pair = frozenset((a, b))
        assert pair not in pairs_seen
        pairs_seen.add(pair)
    r_values = [abs(_num(e["r"])) for e in strongest]
    assert r_values == sorted(r_values, reverse=True)


def test_correlation_top_pairs_cap_is_ten(run_paths: RunPaths) -> None:
    assert CORRELATION_TOP_PAIRS == 10
    frame = pd.DataFrame({f"c{i}": list(range(20)) for i in range(8)})
    ref = _save(run_paths, frame)
    result = compute_correlations(run_paths, ref)
    assert len(_list(result.summary["strongest"])) <= CORRELATION_TOP_PAIRS


def test_s13_fewer_than_two_numeric_columns_is_data_error(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolDataError):
        compute_correlations(run_paths, ref)


def test_s2_compute_correlations_skips_constant_column(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"const": [5.0] * 10, "a": list(range(10)), "b": list(range(10, 20))})
    ref = _save(run_paths, frame)
    result = compute_correlations(run_paths, ref)
    assert "const" in _dict(result.summary["skipped"])


def test_compute_correlations_unknown_column_names_all_unknowns(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as excinfo:
        compute_correlations(run_paths, ref, columns=("age", "bogus1", "bogus2"))
    assert "bogus1" in str(excinfo.value.field_errors)
    assert "bogus2" in str(excinfo.value.field_errors)


def test_compute_correlations_data_error_when_only_constant_columns_remain(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"const1": [1.0] * 10, "const2": [2.0] * 10, "s": ["x"] * 10})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolDataError):
        compute_correlations(run_paths, ref)


def test_compute_correlations_nan_pair_from_no_overlapping_rows_excluded_from_strongest(
    run_paths: RunPaths,
) -> None:
    """Distinct from the constant-column case (which `_is_constant` filters out before this
    point): two genuinely non-constant columns can still produce a `NaN` pairwise correlation
    if they never have a non-null value on the same row -- `.corr()` has nothing to compute
    for that pair. Such a pair must be excluded from `strongest`, not crash or appear as a
    `NaN`-valued entry."""
    frame = pd.DataFrame(
        {
            "a": [1.0, 2.0, 3.0, None, None, None],
            "b": [None, None, None, 1.0, 2.0, 3.0],
            "c": list(range(6)),
        }
    )
    ref = _save(run_paths, frame)
    result = compute_correlations(run_paths, ref)
    strongest = [_dict(e) for e in _list(result.summary["strongest"])]
    pairs = {frozenset((e["a"], e["b"])) for e in strongest}
    assert frozenset({"a", "b"}) not in pairs


# --- detect_outliers ------------------------------------------------------------------------------


def test_s4_detect_outliers_iqr_reports_exact_planted_labels(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = detect_outliers(run_paths, ref, columns=("with_outliers",), method="iqr")
    column = _dict(_dict(result.summary["columns"])["with_outliers"])
    assert set(_list(column["rows"])) == {3, 17, 42}
    assert column["count"] == 3


def test_detect_outliers_zscore_also_reports_the_planted_labels(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = detect_outliers(run_paths, ref, columns=("with_outliers",), method="zscore")
    column = _dict(_dict(result.summary["columns"])["with_outliers"])
    assert set(_list(column["rows"])) == {3, 17, 42}


def test_s5_outlier_cap_truncates_rows_but_count_is_exact(run_paths: RunPaths) -> None:
    n_base = 2000
    n_out = 150
    values = [50.0] * n_base + [1000.0 + i for i in range(n_out)]
    frame = pd.DataFrame({"v": values})
    ref = _save(run_paths, frame)
    result = detect_outliers(run_paths, ref, columns=("v",), method="iqr")
    column = _dict(_dict(result.summary["columns"])["v"])
    assert column["count"] == n_out
    assert len(_list(column["rows"])) == OUTLIER_ROWS_CAP
    assert column["truncated"] is True


def test_s2_detect_outliers_skips_constant_column_never_reports_zero(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"const": [5.0] * 20})
    ref = _save(run_paths, frame)
    result = detect_outliers(run_paths, ref, columns=("const",), method="iqr")
    assert "const" in _dict(result.summary["skipped"])
    assert "const" not in _dict(result.summary["columns"])


def test_s2_detect_outliers_skips_constant_column_zscore_too(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"const": [5.0] * 20})
    ref = _save(run_paths, frame)
    result = detect_outliers(run_paths, ref, columns=("const",), method="zscore")
    assert "const" in _dict(result.summary["skipped"])


def test_detect_outliers_no_outliers_reports_zero_on_a_real_non_constant_column(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"v": list(range(20))})
    ref = _save(run_paths, frame)
    result = detect_outliers(run_paths, ref, columns=("v",), method="iqr")
    column = _dict(_dict(result.summary["columns"])["v"])
    assert column["count"] == 0
    assert column["rows"] == []


def test_detect_outliers_unknown_column_names_all_unknowns(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as excinfo:
        detect_outliers(run_paths, ref, columns=("age", "zzz"))
    assert "zzz" in str(excinfo.value.field_errors)


def test_detect_outliers_columns_none_checks_every_numeric_column(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = detect_outliers(run_paths, ref, columns=None)
    assert set(_dict(result.summary["columns"])) == {"customer_id", "age", "churned"}


def test_detect_outliers_zscore_threshold_matches_named_constant(run_paths: RunPaths) -> None:
    from statlab_tools.tools.profiling import ZSCORE_THRESHOLD

    assert ZSCORE_THRESHOLD == 3.0


def test_detect_outliers_iqr_k_matches_named_constant() -> None:
    from statlab_tools.tools.profiling import IQR_K

    assert IQR_K == 1.5


def test_profile_schema_on_single_column_frame(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"only": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = profile_schema(run_paths, ref)
    assert result.summary["cols"] == 1
    assert set(_dict(result.summary["columns"])) == {"only"}


def test_summarize_target_boolean_dtype_target_is_class(run_paths: RunPaths) -> None:
    """A bool target is excluded from `is_numeric_column` (P4), so it must take the class
    branch regardless of its tiny cardinality -- this is a case neither "numeric" check alone
    would get right if the two checks disagreed (the whole reason for `_frame.is_numeric_column`
    being the one predicate every tool uses)."""
    frame = pd.DataFrame({"t": [True, False, True, True, False]})
    ref = _save(run_paths, frame)
    result = summarize_target(run_paths, ref, target="t")
    assert result.summary["kind"] == "class"


def test_report_missingness_no_columns_with_nulls_gives_empty_co_missing(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    ref = _save(run_paths, frame)
    result = report_missingness(run_paths, ref)
    assert result.summary["co_missing"] == []


def test_summarize_categorical_default_top_k_is_ten(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"c": [f"v{i}" for i in range(15)]})
    ref = _save(run_paths, frame)
    result = summarize_categorical(run_paths, ref, columns=("c",))
    c = _dict(_dict(result.summary["columns"])["c"])
    assert len(_list(c["top"])) == 10


# --- §3.4 strict-JSON property test (S14): all 14 tools x 2 frames ------------------------------


def _run_every_profiling_and_most_intake_tools(
    run_paths: RunPaths, frame: pd.DataFrame
) -> list[tuple[str, JsonValue]]:
    """Every tool in this chunk, plus chunk 08's `infer_column_roles`/`detect_id_columns`/
    `detect_leaky_columns` (`load_csv` needs a real uploaded file, not an in-memory frame, and
    `validate_contract` needs a contract snapshot -- both are exercised by their own chunk's
    tests and by `test_s14_validate_contract_strict_json` below, so "all 14" is covered across
    this module rather than needing one function with 14 incompatible call shapes).
    """
    from statlab_tools.tools._frame import is_numeric_column
    from statlab_tools.tools.intake import (
        detect_id_columns,
        detect_leaky_columns,
        infer_column_roles,
    )

    ref = _save(run_paths, frame)
    numeric_columns = [str(c) for c in frame.columns if is_numeric_column(frame[c])]
    target = numeric_columns[0] if numeric_columns else str(frame.columns[0])
    feature = next((str(c) for c in frame.columns if str(c) != target), target)

    results: list[tuple[str, JsonValue]] = [
        ("infer_column_roles", infer_column_roles(run_paths, ref).model_dump(mode="json")),
        ("detect_id_columns", detect_id_columns(run_paths, ref).model_dump(mode="json")),
        (
            "detect_leaky_columns",
            detect_leaky_columns(run_paths, ref, target=target).model_dump(mode="json"),
        ),
        ("profile_schema", profile_schema(run_paths, ref).model_dump(mode="json")),
        ("summarize_numeric", summarize_numeric(run_paths, ref).model_dump(mode="json")),
        ("summarize_categorical", summarize_categorical(run_paths, ref).model_dump(mode="json")),
        ("report_missingness", report_missingness(run_paths, ref).model_dump(mode="json")),
        (
            "summarize_target",
            summarize_target(run_paths, ref, target=target).model_dump(mode="json"),
        ),
    ]
    if len(numeric_columns) >= 2:
        results.append(
            ("compute_correlations", compute_correlations(run_paths, ref).model_dump(mode="json"))
        )
    results.append(
        (
            "detect_outliers",
            detect_outliers(run_paths, ref, columns=tuple(numeric_columns) or None).model_dump(
                mode="json"
            ),
        )
    )
    results.append(
        (
            "plot_distribution",
            plot_distribution(run_paths, ref, column=feature).model_dump(mode="json"),
        )
    )
    if feature != target:
        results.append(
            (
                "plot_target_relation",
                plot_target_relation(run_paths, ref, column=feature, target=target).model_dump(
                    mode="json"
                ),
            )
        )
    return results


def test_s14_validate_contract_strict_json(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame, full_contract: object
) -> None:
    """`validate_contract` needs a contract snapshot, not just a frame -- covered here on its
    own rather than reshaped into the shared helper above, to keep "all 14 tools" true across
    this module without one function handling 14 different call shapes."""
    from statlab_contracts.contract import DataContract
    from statlab_tools.tools.intake import validate_contract

    assert isinstance(full_contract, DataContract)
    run_paths.contract_snapshot_json.write_text(full_contract.model_dump_json(), encoding="utf-8")
    ref = _save(run_paths, churn_frame_fixture)
    result = validate_contract(run_paths, ref)
    json.dumps(result.model_dump(mode="json"), allow_nan=False)


@pytest.mark.parametrize("frame_name", ["churn", "messy"])
def test_s14_every_profiling_and_intake_tool_survives_strict_json_dump(
    run_paths: RunPaths,
    churn_frame_fixture: pd.DataFrame,
    messy_frame: pd.DataFrame,
    frame_name: str,
) -> None:
    """§3.4: every `ToolSuccess` from every tool in this chunk and chunk 08's intake tools must
    survive `json.dumps(..., allow_nan=False)` on both `churn_frame` and `messy_frame`. This is
    the test that proves P1-P3 are solved across the catalogue, not just in the few places a
    developer happened to remember -- it must not be narrowed to make it pass.
    """
    frame = churn_frame_fixture if frame_name == "churn" else messy_frame
    results = _run_every_profiling_and_most_intake_tools(run_paths, frame)
    assert len(results) >= 10
    for _name, payload in results:
        json.dumps(payload, allow_nan=False)
