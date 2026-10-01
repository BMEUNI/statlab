"""`detect_id_columns` and `detect_leaky_columns`: `plan/08_tools_intake.md` section 3.5-3.6.

Calls both tools through their decorated callables directly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.intake import (
    ID_UNIQUE_SHARE,
    LEAK_AUC,
    LEAK_CORRELATION,
    LEAK_MAJORITY_SHARE_GUARD,
    LEAK_MIN_CLASS_ROWS,
    LEAK_MIN_ROWS,
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


# --- R2: continuous numeric leaks are scored by rank AUC (not binned purity) -----------------


def _rule_for(result: ToolSuccess, column: str) -> str | None:
    for s in _list(result.summary["suspects"]):
        entry = _dict(s)
        if str(entry["column"]) == column:
            return str(entry["rule"])
    return None


def _entry_for(result: ToolSuccess, column: str) -> dict[str, JsonValue]:
    return next(
        _dict(s) for s in _list(result.summary["suspects"]) if str(_dict(s)["column"]) == column
    )


def _sorted_binary_leak(positives: int, *, n: int = 400, seed: int) -> pd.DataFrame:
    """A perfect continuous leak against a binary target with exactly `positives` ones.

    Targets are deterministic and sorted (`positives` ones after `n - positives` zeros), so the
    class boundary falls at an arbitrary row rather than a decile edge -- the exact case binned
    purity missed. `leak = target*10 + N(0, 0.01)` keeps the two classes perfectly separated
    with fixed-seed noise, so the rank AUC is 1.0 regardless of balance.
    """
    rng = np.random.default_rng(seed)
    target = np.array([0] * (n - positives) + [1] * positives)
    leak = target * 10 + rng.normal(scale=0.01, size=n)
    return pd.DataFrame({"y": target, "leak": leak})


@pytest.mark.parametrize("positives", [200, 210, 221, 60, 20])
def test_r2_perfect_continuous_leak_is_flagged_as_rank_auc_at_every_balance(
    run_paths: RunPaths, positives: int
) -> None:
    """The amended rule: a perfect continuous leak against a binary target is flagged with rule
    `rank_auc` at every class balance. Binned purity only reached 1.0 at the exactly-balanced
    200/400 split (0.975 at 210, 0.9525 at 221, 0.950 at 60 and 20) and so missed the leak at
    every other split -- the defect R2's amendment fixes. The rank AUC of this leak is 1.0
    throughout."""
    import numpy as np  # noqa: F401  -- used indirectly via the helper's module-level import

    frame = _sorted_binary_leak(positives, seed=100 + positives)
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    entry = _entry_for(result, "leak")
    assert entry["rule"] == "rank_auc"
    assert float(str(entry["score"])) >= 0.99
    assert "rank AUC" in str(_list(entry["evidence"])[0])


def test_r2_perfect_leak_with_20pct_missing_still_flagged(run_paths: RunPaths) -> None:
    """20% of the feature missing. Rank AUC is computed over paired non-null rows, so missing
    feature values neither crash it nor pull the score down (the old purity path divided by the
    full frame length and scored ~0.76 here). Still flagged `rank_auc` with score >= 0.99."""
    frame = _sorted_binary_leak(200, seed=12)
    # copy=True: under pandas 3.0 copy-on-write, to_numpy() returns a read-only view.
    leak = frame["leak"].to_numpy(copy=True)
    rng = np.random.default_rng(99)
    missing_idx = rng.choice(len(leak), size=len(leak) // 5, replace=False)
    leak[missing_idx] = np.nan
    frame["leak"] = leak
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    entry = _entry_for(result, "leak")
    assert entry["rule"] == "rank_auc"
    assert float(str(entry["score"])) >= 0.99


def test_r2_perfect_leak_against_balanced_three_class_target_is_flagged(
    run_paths: RunPaths,
) -> None:
    """A perfect continuous leak against a balanced 3-class target: each class occupies a
    contiguous band of the feature, so every one-vs-rest AUC is 1.0 and the feature is flagged
    `rank_auc`."""
    rng = np.random.default_rng(21)
    y = np.repeat(np.arange(3), 130)
    leak = y * 10 + rng.normal(scale=0.01, size=y.size)
    frame = pd.DataFrame({"y": y, "leak": leak})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "leak") == "rank_auc"


def test_r2_class_below_min_class_rows_is_not_scored(run_paths: RunPaths) -> None:
    """A 3-class target whose only separable class has fewer than LEAK_MIN_CLASS_ROWS rows: the
    tiny class is skipped, the two large classes overlap on the feature (AUC ~0.5), so the
    feature is not flagged. If LEAK_MIN_CLASS_ROWS did not gate the tiny class, its perfect
    separation would score 1.0 and flag the feature."""
    rng = np.random.default_rng(30)
    big = 195
    tiny = LEAK_MIN_CLASS_ROWS - 5
    target = pd.Series([0] * big + [1] * big + [2] * tiny)
    feature = pd.Series(list(rng.normal(size=big * 2)) + [1000.0] * tiny)
    frame = pd.DataFrame({"y": target, "x": feature})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "x") is None


def test_r2_random_continuous_feature_is_not_flagged(run_paths: RunPaths) -> None:
    """A random continuous feature on a balanced binary target scores a rank AUC near 0.5 --
    well below LEAK_AUC -- so the rule discriminates rather than firing on everything numeric."""
    rng = np.random.default_rng(13)
    n = 400
    target = np.tile([0, 1], n // 2)
    frame = pd.DataFrame({"y": target, "x": rng.normal(size=n)})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "x") is None
    from statlab_tools.tools.intake import _rank_auc

    scored = _rank_auc(frame["x"], frame["y"])
    assert scored is not None
    assert scored[0] < 0.6


def test_r2_tied_three_value_feature_mixed_within_values_is_not_flagged(
    run_paths: RunPaths,
) -> None:
    """A numeric feature with only three distinct values, each holding a balanced mix of both
    classes: average-rank ties give every class an AUC of 0.5, so the feature is not flagged.
    This is the case a boundary-counting rule could mistake for a clean split."""
    n = 399
    feature = pd.Series([v for v in (0.0, 1.0, 2.0) for _ in range(n // 3)])
    target = pd.Series([i % 2 for i in range(n)])
    frame = pd.DataFrame({"y": target, "x": feature})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "x") is None


def test_r2_leak_constants_are_named() -> None:
    assert LEAK_AUC == 0.99
    assert LEAK_MIN_CLASS_ROWS == 10
    assert LEAK_MIN_ROWS == 20


def test_r2_level_purity_returns_none_below_min_rows(run_paths: RunPaths) -> None:
    """`_level_purity` (now the non-numeric path only) returns None when fewer than
    LEAK_MIN_ROWS paired non-null rows remain -- a purity over a handful of rows is noise.
    Triggers the guard directly with a categorical feature."""
    from statlab_tools.tools.intake import _level_purity

    n = LEAK_MIN_ROWS - 1
    feature = pd.Series([f"v{i % 3}" for i in range(n)])
    target = pd.Series([i % 2 for i in range(n)])
    assert _level_purity(feature, target) is None


# --- R3: L2 runs whenever feature and target are both numeric --------------------------------


def test_r3_fifteen_level_numeric_target_near_duplicate_is_flagged_by_l2(
    run_paths: RunPaths,
) -> None:
    """A 15-level numeric target (<= CLASS_TARGET_MAX_LEVELS, so is_class_target) with
    dup = 2*y + N(0, 0.001). Before R3, L2 was gated on `not is_class_target`, so it never ran
    and L3's ten decile bins could not reach 0.99 purity against 15 levels -> no suspects. It
    must now be flagged by L2 (rule rank_correlation)."""
    import numpy as np

    rng = np.random.default_rng(14)
    y = np.repeat(np.arange(15), 20)
    dup = 2.0 * y + rng.normal(scale=0.001, size=y.size)
    frame = pd.DataFrame({"y": y, "dup": dup})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "dup") == "rank_correlation"


def test_r3_perfect_separating_continuous_feature_on_binary_is_l3_not_l2(
    run_paths: RunPaths,
) -> None:
    """A continuous feature that perfectly separates a binary target is caught by the
    class-target rule (rank AUC for numeric features, R2), not L2 -- at k=2 the maximum rank
    correlation is 0.866, below LEAK_CORRELATION, so L2 cannot fire. Confirms the measured bound
    R3 records."""
    import numpy as np

    rng = np.random.default_rng(15)
    n = 400
    target = np.tile([0, 1], n // 2)
    sep = target * 100 + rng.normal(scale=0.01, size=n)
    frame = pd.DataFrame({"y": target, "sep": sep})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "sep") == "rank_auc"


def test_r3_bool_feature_is_not_passed_to_l2(run_paths: RunPaths) -> None:
    """is_numeric_column excludes bool, so a bool feature never reaches L2's rank correlation
    -- it is a categorical-shaped column and L3's purity path is where it belongs. A bool
    feature identical in shape to a binary target must not be flagged with rule
    rank_correlation."""
    n = 400
    target = [0, 1] * (n // 2)
    boolfeat = [bool(v) for v in target]
    frame = pd.DataFrame({"y": target, "flag": boolfeat})
    ref = _save(run_paths, frame)
    result = detect_leaky_columns(run_paths, ref, target="y")
    assert _rule_for(result, "flag") != "rank_correlation"
