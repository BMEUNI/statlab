"""Chunk 10 §4.2: `verify_split`. The overlapping/relabelled bad splits are built BY HAND, never
with the split tool -- a bad split the split tool refuses to produce could not be built by it,
and one it would produce is not the leak this check exists to catch.
"""

from __future__ import annotations

import pandas as pd
import pytest
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tool import ToolParamError
from statlab_tools.tools.splitting import (
    OVERLAP_EXAMPLES,
    split_random,
    split_stratified,
    verify_split,
)


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref)
    return ref


def _get(value: object, *keys: str) -> object:
    """Walk nested dicts in a `JsonValue` summary. Asserts each level is a dict -- the same
    posture as `test_tools_profiling_summaries.py`'s `_dict`, chained."""
    for key in keys:
        assert isinstance(value, dict)
        value = value[key]
    return value


def _balanced_frame(n: int = 240) -> pd.DataFrame:
    labels = [0] * (n // 2) + [1] * (n - n // 2)
    return pd.DataFrame({"y": labels, "x": range(n)}, index=range(n))


def _clean_splits(paths: RunPaths) -> tuple[ArtifactRef, ArtifactRef, ArtifactRef]:
    ref = _save(paths, _balanced_frame())
    res = split_stratified(paths, ref, target="y", seed=1)
    metas = {str(m.split): m.ref for m in res.artifacts}
    return metas["train"], metas["valid"], metas["test"]


# --- clean split ---------------------------------------------------------------------------------


def test_clean_split_passes_all_four(run_paths: RunPaths) -> None:
    train, valid, test = _clean_splits(run_paths)
    res = verify_split(run_paths, train=train, valid=valid, test=test, target="y")
    assert res.summary["passed"] is True
    for name in ("index_overlap", "duplicate_rows", "target_balance", "size"):
        assert _get(res.summary, "checks", name, "status") in ("pass", "warn", "skipped")
        assert _get(res.summary, "checks", name, "status") != "fail"


def test_clean_split_all_pairs_zero(run_paths: RunPaths) -> None:
    train, valid, test = _clean_splits(run_paths)
    res = verify_split(run_paths, train=train, valid=valid, test=test, target="y")
    for pair in ("train/valid", "train/test", "valid/test"):
        assert _get(res.summary, "checks", "index_overlap", "pairs", pair) == 0
        assert _get(res.summary, "checks", "duplicate_rows", "pairs", pair) == 0


def test_clean_split_has_no_warnings(run_paths: RunPaths) -> None:
    train, valid, test = _clean_splits(run_paths)
    res = verify_split(run_paths, train=train, valid=valid, test=test, target="y")
    assert res.warnings == ()


# --- M3 acceptance: overlapping split ------------------------------------------------------------


def test_overlapping_split_fails(run_paths: RunPaths) -> None:
    """Built by hand: test shares exactly 10 labels with train."""
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180]
    # test takes 60 rows but 10 of them are the first 10 train rows (shared labels + values)
    test = pd.concat([full.iloc[180:230], full.iloc[0:10]])
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert res.summary["passed"] is False
    assert _get(res.summary, "checks", "index_overlap", "status") == "fail"
    assert _get(res.summary, "checks", "index_overlap", "pairs", "train/test") == 10
    examples = _get(res.summary, "checks", "index_overlap", "examples")
    assert isinstance(examples, list) and 0 < len(examples) <= OVERLAP_EXAMPLES
    assert _get(res.summary, "checks", "duplicate_rows", "status") == "fail"


# --- P3: relabelled copy -------------------------------------------------------------------------


def test_relabelled_duplicate_fails_duplicates_not_overlap(run_paths: RunPaths) -> None:
    """Train's first 10 rows copied into test under NEW labels: no index overlap, but the rows
    are identical. This is the test that fails if the duplicate hash ever includes the index."""
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180]
    relabelled = full.iloc[0:10].copy()
    relabelled.index = pd.RangeIndex(100000, 100010)
    test = pd.concat([full.iloc[180:230], relabelled])
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "index_overlap", "pairs", "train/test") == 0
    assert _get(res.summary, "checks", "index_overlap", "status") == "pass"
    assert _get(res.summary, "checks", "duplicate_rows", "status") == "fail"
    assert _get(res.summary, "checks", "duplicate_rows", "pairs", "train/test") == 10
    assert res.summary["passed"] is False


def test_shuffled_column_order_still_detects_duplicate(run_paths: RunPaths) -> None:
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180]
    relabelled = full.iloc[0:10].copy()
    relabelled.index = pd.RangeIndex(100000, 100010)
    test = pd.concat([full.iloc[180:230], relabelled])[["x", "y"]]  # columns reversed
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "duplicate_rows", "pairs", "train/test") == 10


def test_different_column_set_is_tool_param(run_paths: RunPaths) -> None:
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180]
    test = full.iloc[180:240].drop(columns=["x"])
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    with pytest.raises(ToolParamError) as exc:
        verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert "test" in exc.value.field_errors


def test_valid_column_set_mismatch_is_tool_param(run_paths: RunPaths) -> None:
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180].drop(columns=["x"])
    test = full.iloc[180:240]
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    with pytest.raises(ToolParamError) as exc:
        verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert "valid" in exc.value.field_errors


def test_overlap_examples_are_real_shared_labels(run_paths: RunPaths) -> None:
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180]
    test = pd.concat([full.iloc[180:230], full.iloc[0:10]])
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    examples = _get(res.summary, "checks", "index_overlap", "examples")
    assert isinstance(examples, list)
    shared = set(train.index) & set(test.index)
    for label in examples:
        assert label in shared


# --- balance -------------------------------------------------------------------------------------


def test_drifted_split_warns_but_passes(run_paths: RunPaths) -> None:
    """train heavily class-0, test heavily class-1, but every class still above the floor.
    Under the z-score rule the worst per-class z is ~3.81 (> BALANCE_DRIFT_Z_WARN=3), so this
    still warns -- a genuinely skewed split, not the 0.05-noise the old rule tripped on."""
    train = pd.DataFrame({"y": [0] * 80 + [1] * 40, "x": range(120)}, index=range(120))
    valid = pd.DataFrame({"y": [0] * 20 + [1] * 20, "x": range(120, 160)}, index=range(120, 160))
    test = pd.DataFrame({"y": [0] * 10 + [1] * 30, "x": range(160, 200)}, index=range(160, 200))
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "target_balance", "status") == "warn"
    assert res.summary["passed"] is True


def test_sorted_cut_from_churn_warns_but_passes(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """A split hand-cut from churn_frame sorted by `churned`, with cut points chosen to keep
    >= 5 of each class (and >= MIN_SPLIT_ROWS rows) in every split while still skewing the class
    shares hard: test/valid are each 15 class-0 + 30 class-1, train is the remaining 136 class-0
    + 14 class-1. The worst per-class z is ~5.70 (>> 3), so target_balance is 'warn' and `passed`
    stays True (no fail condition is tripped). FAILS the class-count floor if cut naively (first
    48 / next 48 / rest leaves valid and test with 0 class-1 rows), which is why the cut is by
    class count, not by row position."""
    sorted_frame = churn_frame_fixture.sort_values("churned", kind="stable")
    zeros = sorted_frame[sorted_frame["churned"] == 0]
    ones = sorted_frame[sorted_frame["churned"] == 1]
    assert len(zeros) == 166 and len(ones) == 74
    test = pd.concat([zeros.iloc[0:15], ones.iloc[0:30]])
    valid = pd.concat([zeros.iloc[15:30], ones.iloc[30:60]])
    train = pd.concat([zeros.iloc[30:166], ones.iloc[60:74]])
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="churned")
    balance = _get(res.summary, "checks", "target_balance")
    assert isinstance(balance, dict)
    assert balance["status"] == "warn"
    assert isinstance(balance["max_drift_z"], float)
    assert balance["max_drift_z"] > 3.0
    assert res.summary["passed"] is True


def test_stratified_split_balance_passes(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """A stratified split keeps per-split shares at the pooled share, so target_balance is
    'pass', not 'warn', under the z-score rule."""
    ref = _save(run_paths, churn_frame_fixture)
    res = split_stratified(run_paths, ref, target="churned", seed=1)
    assert _get(res.summary, "checks", "target_balance", "status") == "pass"


def test_zero_of_a_class_fails(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"y": [0] * 60 + [1] * 60, "x": range(120)}, index=range(120))
    valid = pd.DataFrame({"y": [0] * 20 + [1] * 20, "x": range(120, 160)}, index=range(120, 160))
    test = pd.DataFrame({"y": [0] * 40, "x": range(160, 200)}, index=range(160, 200))
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "target_balance", "status") == "fail"
    assert res.summary["passed"] is False


def test_four_of_a_class_fails(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"y": [0] * 60 + [1] * 60, "x": range(120)}, index=range(120))
    valid = pd.DataFrame({"y": [0] * 20 + [1] * 20, "x": range(120, 160)}, index=range(120, 160))
    test = pd.DataFrame({"y": [0] * 36 + [1] * 4, "x": range(160, 200)}, index=range(160, 200))
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "target_balance", "status") == "fail"


def test_continuous_target_balance_skipped(run_paths: RunPaths) -> None:
    def cont(start: int, n: int) -> pd.DataFrame:
        return pd.DataFrame(
            {"y": [float(i) for i in range(start, start + n)], "x": range(start, start + n)},
            index=range(start, start + n),
        )

    tr = _save(run_paths, cont(0, 120))
    va = _save(run_paths, cont(120, 40))
    te = _save(run_paths, cont(160, 40))
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "target_balance", "status") == "skipped"


# --- size ----------------------------------------------------------------------------------------


def test_29_row_valid_fails_size(run_paths: RunPaths) -> None:
    train = pd.DataFrame({"y": [0] * 60 + [1] * 60, "x": range(120)}, index=range(120))
    valid = pd.DataFrame({"y": [0] * 15 + [1] * 14, "x": range(120, 149)}, index=range(120, 149))
    test = pd.DataFrame({"y": [0] * 20 + [1] * 20, "x": range(149, 189)}, index=range(149, 189))
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    assert _get(res.summary, "checks", "size", "status") == "fail"
    assert res.summary["passed"] is False


def test_missing_target_is_tool_param(run_paths: RunPaths) -> None:
    train, valid, test = _clean_splits(run_paths)
    with pytest.raises(ToolParamError) as exc:
        verify_split(run_paths, train=train, valid=valid, test=test, target="nope")
    assert "target" in exc.value.field_errors


# --- warnings ------------------------------------------------------------------------------------


def test_warnings_one_line_per_non_pass_non_skipped(run_paths: RunPaths) -> None:
    """Overlapping + relabelled: index_overlap fail, duplicate_rows fail. target_balance passes,
    size passes. Exactly two warning lines."""
    full = _balanced_frame()
    train = full.iloc[0:120]
    valid = full.iloc[120:180]
    test = pd.concat([full.iloc[180:230], full.iloc[0:10]])
    tr = _save(run_paths, train)
    va = _save(run_paths, valid)
    te = _save(run_paths, test)
    res = verify_split(run_paths, train=tr, valid=va, test=te, target="y")
    statuses = {
        name: _get(res.summary, "checks", name, "status")
        for name in ("index_overlap", "duplicate_rows", "target_balance", "size")
    }
    expected = sum(1 for s in statuses.values() if s in ("warn", "fail"))
    assert len(res.warnings) == expected


def test_messy_copies_fail_duplicates(run_paths: RunPaths, messy_frame: pd.DataFrame) -> None:
    """Five exact copies of messy_frame split by split_random: every test row is an exact
    duplicate of a train/valid row, so duplicate_rows must fail (§4.1's cross-reference)."""
    big = pd.concat([messy_frame] * 5, ignore_index=True)
    ref = _save(run_paths, big)
    res = split_random(run_paths, ref, target="target", seed=2)
    metas = {m.split: m.ref for m in res.artifacts}
    verified = verify_split(
        run_paths, train=metas["train"], valid=metas["valid"], test=metas["test"], target="target"
    )
    assert _get(verified.summary, "checks", "duplicate_rows", "status") == "fail"
