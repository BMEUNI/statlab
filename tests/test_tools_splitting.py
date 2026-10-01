"""Chunk 10 §4.1: `split_random` / `split_stratified`. Every guard is tested by triggering the
failure it prevents; tools are called directly except the one MCP test.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.splitting import (
    MIN_SPLIT_ROWS,
    split_random,
    split_stratified,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]


def _save(paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")
    return ref


def _loaded_splits(paths: RunPaths, res: ToolSuccess) -> dict[str, pd.DataFrame]:
    """Load the three artifacts keyed by their `split` role. `m.split` is `SplitRole | None`;
    a split tool always sets it, so `str(m.split)` is the role string the dict is keyed on."""
    return {str(m.split): load_dataframe(paths.artifact(m.ref, "dataframe")) for m in res.artifacts}


def _test_frame(paths: RunPaths, res: ToolSuccess) -> pd.DataFrame:
    meta = next(m for m in res.artifacts if m.split == "test")
    return load_dataframe(paths.artifact(meta.ref, "dataframe"))


def _get(value: object, *keys: str) -> object:
    """Walk nested dicts in a `JsonValue` summary (mirrors the profiling suite's `_dict`)."""
    for key in keys:
        assert isinstance(value, dict)
        value = value[key]
    return value


def _artifact_files(paths: RunPaths) -> set[str]:
    return {p.name for p in paths.artifacts.iterdir()}


# --- sizes and partition -------------------------------------------------------------------------


def test_churn_sizes_are_144_48_48(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=1)
    assert res.summary["rows"] == {"train": 144, "valid": 48, "test": 48}


def test_partitions_are_disjoint_and_cover_the_input(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=2)
    frames = _loaded_splits(run_paths, res)
    idx = {role: set(frames[role].index) for role in ("train", "valid", "test")}
    assert idx["train"] & idx["valid"] == set()
    assert idx["train"] & idx["test"] == set()
    assert idx["valid"] & idx["test"] == set()
    assert idx["train"] | idx["valid"] | idx["test"] == set(churn_frame_fixture.index)


def test_outputs_keep_input_order(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    """Input index is a monotonic RangeIndex; each output must stay monotonic."""
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=3)
    for m in res.artifacts:
        frame = load_dataframe(run_paths.artifact(m.ref, "dataframe"))
        assert frame.index.is_monotonic_increasing


def test_labels_preserved_on_non_range_index(run_paths: RunPaths) -> None:
    frame = pd.DataFrame(
        {"y": [i % 2 for i in range(240)], "x": range(240)},
        index=[f"row_{i}" for i in range(240)],
    )
    ref = _save(run_paths, frame)
    res = split_random(run_paths, ref, target="y", seed=4)
    seen: set[str] = set()
    for m in res.artifacts:
        loaded = load_dataframe(run_paths.artifact(m.ref, "dataframe"))
        for label in loaded.index:
            assert isinstance(label, str) and label.startswith("row_")
            seen.add(label)
    assert seen == set(frame.index)


# --- determinism ---------------------------------------------------------------------------------


def test_same_seed_is_identical(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    a = split_random(run_paths, ref, target="churned", seed=7)
    b = split_random(run_paths, ref, target="churned", seed=7)
    for ma, mb in zip(a.artifacts, b.artifacts, strict=True):
        assert_frame_equal(
            load_dataframe(run_paths.artifact(ma.ref, "dataframe")),
            load_dataframe(run_paths.artifact(mb.ref, "dataframe")),
        )
    assert a.summary["test_fingerprint"] == b.summary["test_fingerprint"]


def test_different_seed_differs(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    a = split_random(run_paths, ref, target="churned", seed=7)
    b = split_random(run_paths, ref, target="churned", seed=8)
    test_a = _test_frame(run_paths, a)
    test_b = _test_frame(run_paths, b)
    assert set(test_a.index) != set(test_b.index)
    assert a.summary["test_fingerprint"] != b.summary["test_fingerprint"]


# --- stratification ------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [0.2, 0.25])
def test_stratified_counts_equal_round_n_c_times_size(run_paths: RunPaths, size: float) -> None:
    """Class counts chosen so `n_c * size` is fractional, so `round()` genuinely matters:
    class 0 has 103 rows, class 1 has 97. 103*0.2 = 20.6, 97*0.25 = 24.25."""
    labels = [0] * 103 + [1] * 97
    frame = pd.DataFrame({"y": labels, "x": range(len(labels))})
    ref = _save(run_paths, frame)
    res = split_stratified(run_paths, ref, target="y", test_size=size, valid_size=size, seed=5)
    frames = _loaded_splits(run_paths, res)
    for cls, n_c in ((0, 103), (1, 97)):
        n_test = round(n_c * size)
        n_valid = round(n_c * size)
        assert int((frames["test"]["y"] == cls).sum()) == n_test
        assert int((frames["valid"]["y"] == cls).sum()) == n_valid
        assert int((frames["train"]["y"] == cls).sum()) == n_c - n_test - n_valid


def test_stratified_multiclass(run_paths: RunPaths) -> None:
    labels = ["a"] * 70 + ["b"] * 65 + ["c"] * 65
    frame = pd.DataFrame({"y": labels, "x": range(len(labels))})
    ref = _save(run_paths, frame)
    res = split_stratified(run_paths, ref, target="y", seed=6)
    frames = _loaded_splits(run_paths, res)
    for cls, n_c in (("a", 70), ("b", 65), ("c", 65)):
        assert int((frames["test"]["y"] == cls).sum()) == round(n_c * 0.2)


def test_stratified_bool_target(run_paths: RunPaths) -> None:
    labels = [True] * 101 + [False] * 99
    frame = pd.DataFrame({"y": labels, "x": range(len(labels))})
    ref = _save(run_paths, frame)
    res = split_stratified(run_paths, ref, target="y", seed=9)
    frames = _loaded_splits(run_paths, res)
    assert int((frames["test"]["y"] == True).sum()) == round(101 * 0.2)  # noqa: E712
    assert int((frames["test"]["y"] == False).sum()) == round(99 * 0.2)  # noqa: E712


# --- null target ---------------------------------------------------------------------------------


def _frame_with_nulls() -> pd.DataFrame:
    labels: list[int | None] = [i % 2 for i in range(240)]
    labels[0] = None
    labels[1] = None
    labels[2] = None
    return pd.DataFrame({"y": pd.array(labels, dtype="Int64"), "x": range(240)})


def test_null_targets_dropped_and_warned(run_paths: RunPaths) -> None:
    ref = _save(run_paths, _frame_with_nulls())
    res = split_random(run_paths, ref, target="y", seed=10)
    assert res.summary["rows_dropped_null_target"] == 3
    assert any("null target" in w for w in res.warnings)
    for m in res.artifacts:
        frame = load_dataframe(run_paths.artifact(m.ref, "dataframe"))
        assert frame["y"].notna().all()


def test_null_drop_gives_same_sizes_as_prefiltered(run_paths: RunPaths) -> None:
    with_nulls = _frame_with_nulls()
    without = with_nulls[with_nulls["y"].notna()]
    ref_a = _save(run_paths, with_nulls)
    ref_b = _save(run_paths, without)
    a = split_random(run_paths, ref_a, target="y", seed=11)
    b = split_random(run_paths, ref_b, target="y", seed=11)
    assert a.summary["rows"] == b.summary["rows"]


# --- guard (D33c): refused splits write no file --------------------------------------------------


def test_guard_too_few_total_rows(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"y": [i % 2 for i in range(100)], "x": range(100)})
    ref = _save(run_paths, frame)
    before = _artifact_files(run_paths)
    with pytest.raises(ToolDataError):
        split_random(run_paths, ref, target="y", seed=12)
    assert _artifact_files(run_paths) == before


def test_guard_valid_size_zero(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    before = _artifact_files(run_paths)
    with pytest.raises(ToolDataError):
        split_random(run_paths, ref, target="churned", valid_size=0.0, seed=13)
    assert _artifact_files(run_paths) == before


def test_guard_minority_class_too_small_stratified(run_paths: RunPaths) -> None:
    """20-row minority class: test would hold round(20*0.2)=4, below MIN_CLASS_ROWS_PER_SPLIT."""
    labels = [0] * 220 + [1] * 20
    frame = pd.DataFrame({"y": labels, "x": range(len(labels))})
    ref = _save(run_paths, frame)
    before = _artifact_files(run_paths)
    with pytest.raises(ToolDataError):
        split_stratified(run_paths, ref, target="y", seed=14)
    assert _artifact_files(run_paths) == before


def test_guard_tiny_class_under_random(run_paths: RunPaths) -> None:
    labels = [0] * 220 + [1] * 20
    frame = pd.DataFrame({"y": labels, "x": range(len(labels))})
    ref = _save(run_paths, frame)
    before = _artifact_files(run_paths)
    with pytest.raises(ToolDataError):
        split_random(run_paths, ref, target="y", seed=15)
    assert _artifact_files(run_paths) == before


def test_guard_too_few_rows_continuous_target(run_paths: RunPaths) -> None:
    """Size fails while target_balance is skipped (continuous target): the refusal message is
    built from the size problems alone, with the skipped balance check contributing none."""
    frame = pd.DataFrame({"y": [float(i) for i in range(100)], "x": range(100)})
    ref = _save(run_paths, frame)
    before = _artifact_files(run_paths)
    with pytest.raises(ToolDataError) as exc:
        split_random(run_paths, ref, target="y", seed=27)
    assert "MIN_SPLIT_ROWS" in str(exc.value)
    assert _artifact_files(run_paths) == before


# --- params --------------------------------------------------------------------------------------


def test_missing_target_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as exc:
        split_random(run_paths, ref, target="nope", seed=16)
    assert "target" in exc.value.field_errors


def test_sizes_sum_too_large_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as exc:
        split_random(run_paths, ref, target="churned", test_size=0.5, valid_size=0.4, seed=17)
    assert "valid_size" in exc.value.field_errors


def test_stratified_on_continuous_target_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError) as exc:
        split_stratified(run_paths, ref, target="age", seed=18)
    assert "target" in exc.value.field_errors


# --- continuous target under split_random --------------------------------------------------------


def test_continuous_target_balance_skipped(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="age", seed=19)
    assert _get(res.summary, "checks", "target_balance", "status") == "skipped"


# --- fingerprint ---------------------------------------------------------------------------------


def _recompute_fingerprint(test: pd.DataFrame) -> str:
    """D-c's formula, recomputed INLINE from the loaded test artifact -- never by calling the
    tool's own helper, so a wrong formula in the tool cannot make this test pass by agreement."""
    h = hashlib.sha256(json.dumps([str(c) for c in test.columns]).encode())
    h.update(pd.util.hash_pandas_object(test.sort_index(), index=True).to_numpy().tobytes())
    return h.hexdigest()


def test_fingerprint_matches_dc_formula(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=20)
    test = _test_frame(run_paths, res)
    assert res.summary["test_fingerprint"] == _recompute_fingerprint(test)


def test_fingerprint_unchanged_by_row_reversal(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=21)
    test = _test_frame(run_paths, res)
    assert _recompute_fingerprint(test) == _recompute_fingerprint(test.iloc[::-1])


def test_tool_fingerprint_is_row_order_invariant(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """The tool's OWN `_fingerprint`, given a frame and a row-shuffled copy of it, must return
    the same digest -- this is the property `sort_index` provides. Fails if `sort_index` is
    dropped from the fingerprint, which a comparison against the (already-ascending) stored test
    artifact cannot catch, since the tool's output index is always ascending."""
    from statlab_tools.tools.splitting import _fingerprint

    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=28)
    test = _test_frame(run_paths, res)
    shuffled = test.sample(frac=1.0, random_state=99)
    assert not shuffled.index.is_monotonic_increasing  # the shuffle actually reordered rows
    assert _fingerprint(shuffled) == _fingerprint(test)


def test_fingerprint_changes_when_a_value_changes(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=22)
    test = _test_frame(run_paths, res)
    mutated = test.copy()
    first = mutated.index[0]
    mutated.loc[first, "age"] = int(mutated.loc[first, "age"]) + 1
    assert _recompute_fingerprint(mutated) != res.summary["test_fingerprint"]


def test_fingerprint_changes_when_labels_shift(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=23)
    test = _test_frame(run_paths, res)
    shifted = test.copy()
    shifted.index = shifted.index + 100000
    assert _recompute_fingerprint(shifted) != res.summary["test_fingerprint"]


# --- artifacts -----------------------------------------------------------------------------------


def test_artifact_meta_split_and_label_order(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=24)
    assert [m.split for m in res.artifacts] == ["train", "valid", "test"]
    assert [m.label for m in res.artifacts] == ["train", "valid", "test"]
    for m in res.artifacts:
        assert m.rows == _get(res.summary, "rows", str(m.split))
        assert m.cols == churn_frame_fixture.shape[1]


# --- strict JSON ---------------------------------------------------------------------------------


def test_strict_json_on_churn(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    for tool_fn in (split_random, split_stratified):
        res = tool_fn(run_paths, ref, target="churned", seed=25)
        json.dumps(res.model_dump(mode="json"), allow_nan=False)


def test_strict_json_on_messy_copies(run_paths: RunPaths, messy_frame: pd.DataFrame) -> None:
    """Five messy copies keep every dtype hazard flowing through partitioning and hashing. The
    copies are exact duplicates, so verify_split must fail duplicate_rows -- asserted in the
    verify_split suite; here only the split tools' strict-JSON survival is checked."""
    big = pd.concat([messy_frame] * 5, ignore_index=True)
    ref = _save(run_paths, big)
    res = split_random(run_paths, ref, target="target", seed=26)
    json.dumps(res.model_dump(mode="json"), allow_nan=False)


# --- constants -----------------------------------------------------------------------------------


def test_min_split_rows_is_30() -> None:
    assert MIN_SPLIT_ROWS == 30


# --- additional behaviour ------------------------------------------------------------------------


def test_stratified_same_seed_identical(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    a = split_stratified(run_paths, ref, target="churned", seed=30)
    b = split_stratified(run_paths, ref, target="churned", seed=30)
    for ma, mb in zip(a.artifacts, b.artifacts, strict=True):
        assert_frame_equal(
            load_dataframe(run_paths.artifact(ma.ref, "dataframe")),
            load_dataframe(run_paths.artifact(mb.ref, "dataframe")),
        )
    assert a.summary["test_fingerprint"] == b.summary["test_fingerprint"]


def test_stratified_outputs_disjoint_and_cover(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_stratified(run_paths, ref, target="churned", seed=31)
    frames = _loaded_splits(run_paths, res)
    idx = {role: set(frames[role].index) for role in ("train", "valid", "test")}
    assert idx["train"] & idx["valid"] == set()
    assert idx["train"] & idx["test"] == set()
    assert idx["valid"] & idx["test"] == set()
    assert idx["train"] | idx["valid"] | idx["test"] == set(churn_frame_fixture.index)


def test_stratified_fingerprint_matches_dc_formula(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_stratified(run_paths, ref, target="churned", seed=32)
    test = _test_frame(run_paths, res)
    assert res.summary["test_fingerprint"] == _recompute_fingerprint(test)


def test_stratified_outputs_keep_order(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_stratified(run_paths, ref, target="churned", seed=33)
    for role, frame in _loaded_splits(run_paths, res).items():  # noqa: B007 -- role documents intent
        assert frame.index.is_monotonic_increasing


def test_non_default_sizes_partition_correctly(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", test_size=0.15, valid_size=0.25, seed=34)
    n = len(churn_frame_fixture)
    assert res.summary["rows"] == {
        "test": round(n * 0.15),
        "valid": round(n * 0.25),
        "train": n - round(n * 0.15) - round(n * 0.25),
    }


def test_rows_dropped_zero_when_no_null_target(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=35)
    assert res.summary["rows_dropped_null_target"] == 0
    assert not any("null target" in w for w in res.warnings)


def test_balance_summary_shape(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_stratified(run_paths, ref, target="churned", seed=36)
    balance = _get(res.summary, "checks", "target_balance")
    assert isinstance(balance, dict)
    classes = balance["classes"]
    assert isinstance(classes, dict)
    assert set(classes) == {"train", "valid", "test"}
    assert "pooled_shares" in balance
    assert "max_drift" in balance
    assert balance["status"] in ("pass", "warn")


def test_size_check_reports_rows(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    res = split_random(run_paths, ref, target="churned", seed=37)
    rows = _get(res.summary, "checks", "size", "rows")
    assert rows == {"train": 144, "valid": 48, "test": 48}


def test_every_number_is_strict_json_scalar(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """Round-trip the whole envelope through the strict JSON adapter -- any np.int64 that
    escaped json_scalar would be rejected here."""
    ref = _save(run_paths, churn_frame_fixture)
    res = split_stratified(run_paths, ref, target="churned", seed=38)
    reparsed = TOOL_RESULT_ADAPTER.validate_python(res.model_dump(mode="json"))
    assert reparsed.ok is True


def test_output_dtypes_preserved(run_paths: RunPaths, messy_frame: pd.DataFrame) -> None:
    """Int64/datetime64 survive the split round-trip unchanged (P1): the split selects rows,
    it never casts."""
    big = pd.concat([messy_frame] * 5, ignore_index=True)
    ref = _save(run_paths, big)
    res = split_random(run_paths, ref, target="target", seed=40)
    test = _test_frame(run_paths, res)
    assert str(test["int_with_nulls"].dtype) == "Int64"
    assert str(test["event_time"].dtype) == "datetime64[us]"


def test_valid_size_default_is_020(run_paths: RunPaths, churn_frame_fixture: pd.DataFrame) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    explicit = split_random(
        run_paths, ref, target="churned", test_size=0.2, valid_size=0.2, seed=41
    )
    default = split_random(run_paths, ref, target="churned", seed=41)
    assert explicit.summary["rows"] == default.summary["rows"]


# --- review corrections 2026-10-01 ---------------------------------------------------------------


def _churn_categorical_target(churn_frame_fixture: pd.DataFrame) -> pd.DataFrame:
    """churn_frame with `churned` as a string categorical declaring a third, unused level.
    String labels (not the original 0/1 ints) deliberately: an *integer* categorical is
    normalised back to int64 by the parquet round-trip the split tools load through, dropping
    the unused level before any tool sees it, whereas a string categorical keeps its declared
    categories across the round-trip -- which is exactly the frame that reproduces the defect."""
    frame = churn_frame_fixture.copy()
    frame["churned"] = pd.Categorical(
        frame["churned"].map({0: "stay", 1: "churn"}),
        categories=["stay", "churn", "unused"],
    )
    return frame


def test_split_random_accepts_categorical_target_with_unused_level(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """`churned` as a categorical declaring an extra, unused level must not be refused as a
    class with 0 rows. FAILS under bare `value_counts()`, which lists the unused level with 0
    rows in every split and trips the MIN_CLASS_ROWS_PER_SPLIT floor ('train has 0 rows ...')."""
    ref = _save(run_paths, _churn_categorical_target(churn_frame_fixture))
    res = split_random(run_paths, ref, target="churned", seed=1)
    classes = _get(res.summary, "checks", "target_balance", "classes")
    assert isinstance(classes, dict)
    train_classes = classes["train"]
    assert isinstance(train_classes, dict)
    assert set(train_classes) == {"stay", "churn"}


def test_split_stratified_accepts_categorical_target_with_unused_level(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """Same as above for `split_stratified`: the unused categorical level is not a class, so
    `target_balance.classes`/`pooled_shares` list only the observed classes."""
    ref = _save(run_paths, _churn_categorical_target(churn_frame_fixture))
    res = split_stratified(run_paths, ref, target="churned", seed=1)
    pooled_shares = _get(res.summary, "checks", "target_balance", "pooled_shares")
    assert isinstance(pooled_shares, dict)
    assert set(pooled_shares) == {"stay", "churn"}


def test_random_splits_never_warn_over_fifty_seeds(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    """`split_random` on churn_frame over seeds 0-49 never reports target_balance 'warn' under
    the z-score rule. FAILS under the old absolute 0.05 rule, which fired on ~60% of seeds (one
    standard deviation of a 31% share at n=48 is ~0.066, well above 0.05)."""
    ref = _save(run_paths, churn_frame_fixture)
    for seed in range(50):
        res = split_random(run_paths, ref, target="churned", seed=seed)
        status = _get(res.summary, "checks", "target_balance", "status")
        assert status != "warn", f"seed {seed} warned: {status}"


def test_split_random_emits_balance_drift_warning(run_paths: RunPaths) -> None:
    """A split tool itself emits the class-balance-drift warning when a random split drifts past
    the z threshold while every split still clears the count floors. The frame is small and
    imbalanced (90 class-0 / 60 class-1 over 150 rows) so a random partition can drift hard;
    seed 47 is a split whose worst per-class z is ~3.35 (> 3) with >= 5 of each class and >= 30
    rows in every split, so target_balance is 'warn' and the tool surfaces the drift warning."""
    labels = [1] * 60 + [0] * 90
    frame = pd.DataFrame({"y": labels, "x": range(150)}, index=range(150))
    ref = _save(run_paths, frame)
    res = split_random(run_paths, ref, target="y", seed=47)
    assert _get(res.summary, "checks", "target_balance", "status") == "warn"
    assert any("class balance drifts" in w for w in res.warnings)


@pytest.mark.asyncio
async def test_mcp_split_stratified_round_trips(
    mcp_session: SessionFactory, run_paths_over_mcp: str, churn_frame_fixture: pd.DataFrame
) -> None:
    """split_stratified round-trips over a real session; the test artifact carries
    split="test", and seed's served description is ORCHESTRATOR_SUPPLIED."""
    from mcp.types import CallToolResult
    from statlab_tools.settings import get_tool_settings

    settings = get_tool_settings()
    paths = RunPaths(data_dir=settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    ref = _save(paths, churn_frame_fixture)

    async with mcp_session() as session:
        listed = await session.list_tools()
        split_tool = next(t for t in listed.tools if t.name == "split_stratified")
        assert split_tool.meta is not None
        assert split_tool.meta["phase"] == "splitting"
        assert split_tool.meta["allow_in"] == ["data_splitting"]
        seed_schema = split_tool.input_schema["properties"]["seed"]
        from statlab_tools.tool import ORCHESTRATOR_SUPPLIED

        assert seed_schema["description"] == ORCHESTRATOR_SUPPLIED
        # Required with no default in BOTH split tools: a default would let a missed
        # injection split silently with the wrong seed instead of failing validation.
        for name in ("split_random", "split_stratified"):
            schema = next(t for t in listed.tools if t.name == name).input_schema
            assert "seed" in schema["required"]
            assert "default" not in schema["properties"]["seed"]

        result = await session.call_tool(
            "split_stratified",
            {
                "project": "proj",
                "run": run_paths_over_mcp,
                "frame": ref,
                "target": "churned",
                "seed": 42,
            },
        )
        assert isinstance(result, CallToolResult)
        assert result.structured_content is not None
        validated = TOOL_RESULT_ADAPTER.validate_python(result.structured_content["result"])
        assert validated.ok is True
        test_meta = next(m for m in validated.artifacts if m.split == "test")
        assert test_meta.split == "test"
