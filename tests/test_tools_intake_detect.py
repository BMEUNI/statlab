"""`detect_id_columns` and `detect_leaky_columns`: `plan/08_tools_intake.md` section 3.5-3.6.

Calls both tools through their decorated callables directly.
"""

from __future__ import annotations

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.intake import (
    ID_UNIQUE_SHARE,
    LEAK_CORRELATION,
    LEAK_MAJORITY_SHARE_GUARD,
    LEAK_PURITY,
    MIN_ROWS_FOR_ID,
    detect_id_columns,
    detect_leaky_columns,
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


def _columns(entries: JsonValue) -> list[str]:
    return [str(_dict(e)["column"]) for e in _list(entries)]


# --- detect_id_columns -----------------------------------------------------------------------


def test_id_unique_int_column_is_flagged(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"id": range(50), "x": [1] * 50})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert "id" in _columns(result.summary["candidates"])


def test_id_float_near_unique_is_not_flagged(run_paths: RunPaths) -> None:
    """Floats are excluded by dtype -- a continuous measurement is near-unique by nature."""
    frame = pd.DataFrame({"measurement": [float(i) + 0.001 for i in range(50)]})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert "measurement" not in _columns(result.summary["candidates"])


def test_id_fewer_than_min_rows_is_skipped(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"id": range(MIN_ROWS_FOR_ID - 1)})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert result.summary["candidates"] == []


def test_id_at_min_rows_threshold_is_considered(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"id": range(MIN_ROWS_FOR_ID)})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert "id" in _columns(result.summary["candidates"])


def test_id_name_alone_does_not_flag_a_low_unique_share_column(run_paths: RunPaths) -> None:
    """A column named `customer_id` with lots of repeats (low unique share) must not be
    flagged -- the name is evidence, never a decision."""
    frame = pd.DataFrame({"customer_id": ([1, 2, 3] * 20)[:50]})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert "customer_id" not in _columns(result.summary["candidates"])


def test_id_evidence_lists_name_pattern_and_monotonic_hints(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"user_id": range(1, 51)})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    candidates = _list(result.summary["candidates"])
    entry = _dict(candidates[0])
    evidence = _list(entry["evidence"])
    assert "name matches id pattern" in evidence
    assert "monotonic integer sequence" in evidence


def test_id_evidence_empty_for_a_unique_but_unnamed_non_monotonic_column(
    run_paths: RunPaths,
) -> None:
    import numpy as np

    rng = np.random.default_rng(5)
    values = rng.permutation(50)
    frame = pd.DataFrame({"token": values})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    candidates = _list(result.summary["candidates"])
    entry = _dict(candidates[0])
    assert entry["evidence"] == []


def test_id_sort_order_is_unique_share_desc_then_name(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "b_id": list(range(50)),  # 100% unique
            "a_id": ([1, 2, *list(range(2, 50))])[:50],  # also near-100% unique, slightly less
        }
    )
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    columns = _columns(result.summary["candidates"])
    assert columns[0] == "b_id"


def test_id_unique_string_column_is_flagged(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"token": [f"tok-{i}" for i in range(50)]})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert "token" in _columns(result.summary["candidates"])


def test_id_unique_share_below_threshold_is_not_flagged(run_paths: RunPaths) -> None:
    """89 distinct values out of 100 rows -> 0.89 unique share, below ID_UNIQUE_SHARE."""
    values = list(range(89)) + [0] * 11
    frame = pd.DataFrame({"x": values})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert "x" not in _columns(result.summary["candidates"])


def test_id_threshold_constants_are_named() -> None:
    assert ID_UNIQUE_SHARE == 0.95
    assert MIN_ROWS_FOR_ID == 20


def test_id_uuid_suffix_name_pattern_is_detected(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"session_uuid": [f"s{i}" for i in range(50)]})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    candidates = _list(result.summary["candidates"])
    entry = _dict(candidates[0])
    assert "name matches id pattern" in _list(entry["evidence"])


def test_churn_frame_flags_exactly_customer_id(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """Exactly, not "includes": notes_free_text is 100% unique but is text, not an identifier."""
    ref = _save(run_paths, churn_frame_fixture)
    result = detect_id_columns(run_paths, ref)
    assert _columns(result.summary["candidates"]) == ["customer_id"]


def test_id_unique_free_text_is_not_flagged(run_paths: RunPaths) -> None:
    """Long unique strings are text (infer_column_roles R8), not IDs -- same threshold."""
    frame = pd.DataFrame(
        {"notes": [f"customer said something different #{i} " * 3 for i in range(50)]}
    )
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert _columns(result.summary["candidates"]) == []


def test_id_uuid_strings_stay_under_the_text_cutoff(run_paths: RunPaths) -> None:
    """A UUID is 36 chars, below TEXT_MEAN_LENGTH, so the text exclusion must not swallow it."""
    import uuid

    frame = pd.DataFrame({"key": [str(uuid.UUID(int=i)) for i in range(50)]})
    ref = _save(run_paths, frame)
    result = detect_id_columns(run_paths, ref)
    assert _columns(result.summary["candidates"]) == ["key"]


# --- detect_leaky_columns ----------------------------------------------------------------------


def test_l1_column_identical_to_target_is_flagged(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"y": [0, 1, 0, 1, 1], "copy": [0, 1, 0, 1, 1]})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    suspects = _list(result.summary["suspects"])
    columns = [str(_dict(s)["column"]) for s in suspects]
    assert "copy" in columns
    entry = _dict(suspects[columns.index("copy")])
    assert entry["rule"] == "identical_to_target"


def test_l2_numeric_target_above_threshold_is_flagged(run_paths: RunPaths) -> None:
    """A continuous target (> CLASS_TARGET_MAX_LEVELS distinct values) with a near-perfectly
    rank-correlated feature is flagged by L2."""
    n = 50
    target = list(range(n))
    feature = [v + 0.001 * (i % 2) for i, v in enumerate(target)]
    frame = pd.DataFrame({"y": target, "x": feature})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    suspects = _list(result.summary["suspects"])
    columns = [str(_dict(s)["column"]) for s in suspects]
    assert "x" in columns


def test_l2_below_threshold_is_not_flagged(run_paths: RunPaths) -> None:
    import numpy as np

    rng = np.random.default_rng(7)
    n = 50
    target = list(range(n))
    noise = rng.normal(scale=15, size=n)
    feature = [t + nz for t, nz in zip(target, noise, strict=True)]
    frame = pd.DataFrame({"y": target, "x": feature})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "x" not in columns


def test_l2_threshold_is_the_named_constant() -> None:
    assert LEAK_CORRELATION == 0.98


def test_l3_post_outcome_categorical_is_flagged(run_paths: RunPaths) -> None:
    """A categorical feature that is (almost) a direct re-encoding of the target -- the
    canonical "recorded after the outcome" leak."""
    n = 200
    target = [i % 2 for i in range(n)]
    feature = ["churned_flag" if t == 1 else "active_flag" for t in target]
    frame = pd.DataFrame({"y": target, "x": feature})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "x" in columns


def test_l3_guard_suppresses_everything_on_an_imbalanced_target(run_paths: RunPaths) -> None:
    """The majority-share guard: without it, a feature independent of the target would still
    read as ~99% pure on a 99%-negative target. Directly compares the raw purity (which DOES
    cross the threshold) against the tool's actual output (which must flag nothing)."""
    import numpy as np
    from statlab_tools.tools.intake import _level_purity

    rng = np.random.default_rng(2)
    n = 500
    target = rng.choice([0, 1], size=n, p=[0.99, 0.01])
    feature = rng.integers(0, 10, size=n)
    frame = pd.DataFrame({"y": target, "x": feature})

    raw_purity = _level_purity(frame["x"], frame["y"])
    assert raw_purity is not None
    assert raw_purity >= LEAK_PURITY  # confirms the guard is actually load-bearing here

    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert result.summary["suspects"] == []


def test_l3_id_column_is_not_an_l3_suspect(run_paths: RunPaths) -> None:
    """A unique key is trivially 100% pure against any target; L3 skips features whose own
    unique share exceeds LEAK_UNIQUE_SHARE_SKIP."""
    n = 100
    target = [i % 2 for i in range(n)]
    frame = pd.DataFrame({"y": target, "id": range(n)})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "id" not in columns


def test_l3_purity_threshold_is_the_named_constant() -> None:
    assert LEAK_PURITY == 0.99
    assert LEAK_MAJORITY_SHARE_GUARD == 0.95


def test_l4_name_containing_target_name_alone_does_not_flag(run_paths: RunPaths) -> None:
    """L4 is evidence only; a column named after the target with no real relationship must
    not appear in suspects on its own."""
    n = 100
    target = [i % 2 for i in range(n)]
    unrelated = [i % 3 for i in range(n)]
    frame = pd.DataFrame({"churned": target, "churned_notes": unrelated})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="churned")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "churned_notes" not in columns


def test_l4_evidence_appears_alongside_a_real_rule_when_both_apply(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"y": [0, 1, 0, 1, 1], "y_copy": [0, 1, 0, 1, 1]})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    suspects = _list(result.summary["suspects"])
    columns = [str(_dict(s)["column"]) for s in suspects]
    entry = _dict(suspects[columns.index("y_copy")])
    evidence = _list(entry["evidence"])
    assert any("name" in str(e) for e in evidence)


def test_null_target_rows_are_dropped_before_scoring(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"y": [0, 1, 0, 1, None, None], "x": ["a", "b", "a", "b", "c", "d"]})
    ref = _save(run_paths, frame)
    # Should not raise despite the nulls, and should compute over the 4 non-null rows only.
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert isinstance(result.summary["suspects"], list)


def test_entirely_null_target_produces_no_suspects_not_a_vacuous_match(
    run_paths: RunPaths,
) -> None:
    """An entirely-null target leaves nothing to compare after dropping null rows -- two empty
    series compare equal under `.equals()`, which would otherwise flag every other column as
    "identical to target" vacuously. Confirms the early-return guard."""
    frame = pd.DataFrame({"y": pd.array([None, None, None], dtype="object"), "x": [1, 2, 3]})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert result.summary["suspects"] == []


def test_unknown_target_is_tool_param(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"y": [0, 1], "x": [1, 2]})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolParamError) as excinfo:
        detect_leaky_columns(run_paths, ref, target="ghost")
    assert "target" in excinfo.value.field_errors


def test_churn_frame_produces_no_suspects(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = detect_leaky_columns(run_paths, ref, target="churned")
    assert result.summary["suspects"] == []


def test_docstring_names_this_a_heuristic() -> None:
    doc = detect_leaky_columns.__doc__
    assert doc is not None
    assert "heuristic" in doc.lower()


def test_suspects_sorted_by_score_descending(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {
            "y": [0, 1, 0, 1, 1, 0, 1, 0],
            "exact_copy": [0, 1, 0, 1, 1, 0, 1, 0],
            "unrelated": ["a", "b", "c", "d", "e", "f", "g", "h"],
        }
    )
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    suspects = _list(result.summary["suspects"])
    scores = [float(str(_dict(s)["score"])) for s in suspects]
    assert scores == sorted(scores, reverse=True)


def test_target_itself_is_never_a_suspect(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"y": [0, 1, 0, 1], "x": [1, 2, 3, 4]})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "y" not in columns


def test_two_independent_detect_calls_do_not_interfere(run_paths: RunPaths) -> None:
    frame_a = pd.DataFrame({"y": [0, 1, 0, 1], "copy_a": [0, 1, 0, 1]})
    ref_a = _save(run_paths, frame_a)
    frame_b = pd.DataFrame({"y": [0, 1, 0, 1], "x": [5, 6, 7, 8]})
    ref_b = _save(run_paths, frame_b)

    result_a = detect_leaky_columns(run_paths, ref_a, target="y")
    result_b = detect_leaky_columns(run_paths, ref_b, target="y")

    columns_a = [str(_dict(s)["column"]) for s in _list(result_a.summary["suspects"])]
    columns_b = [str(_dict(s)["column"]) for s in _list(result_b.summary["suspects"])]
    assert "copy_a" in columns_a
    assert "x" not in columns_b


def test_entirely_null_feature_against_a_continuous_target_is_skipped_not_crashed(
    run_paths: RunPaths,
) -> None:
    """A feature with zero non-null values has nothing to score; L2 must skip it cleanly."""
    frame = pd.DataFrame({"y": list(range(30)), "allnull": pd.array([None] * 30, dtype="object")})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "allnull" not in columns


def test_l2_too_few_overlapping_rows_is_skipped_not_scored(run_paths: RunPaths) -> None:
    """A numeric feature overlapping the target in fewer than 2 non-null rows cannot support a
    rank correlation; L2 must skip it rather than compute one."""
    frame = pd.DataFrame({"y": list(range(30)), "sparse": [None] * 29 + [5.0]})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "sparse" not in columns


def test_l3_constant_numeric_feature_against_a_class_target_is_not_flagged(
    run_paths: RunPaths,
) -> None:
    """A constant numeric feature collapses `qcut` to zero bins -- `_level_purity` returns
    `None` and the feature must not be flagged, even though its own unique share (one distinct
    value) is far below `LEAK_UNIQUE_SHARE_SKIP` and would otherwise reach the purity check."""
    n = 100
    target = [i % 2 for i in range(n)]
    frame = pd.DataFrame({"y": target, "const": [5.0] * n})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    columns = [str(_dict(s)["column"]) for s in _list(result.summary["suspects"])]
    assert "const" not in columns
