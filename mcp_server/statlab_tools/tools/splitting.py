"""Node 4 splitting tools: `PLAN.md` §4 node 4, §6.3's `splitting` phase, this chunk's spec.

Three tools -- `split_random`, `split_stratified`, `verify_split` -- are the only place rows are
assigned to train/valid/test (D26: every candidate shares one split). The split tools set
`ArtifactMeta.split` so the D32a lineage lock has a typed fact, return a content fingerprint of
`test` so cross-run exposure can be counted (D32c, via D-c's formula), and refuse an undersized
split rather than produce one (D33c, D-e). `verify_split` re-runs the same size/balance checks
plus structural index-overlap and duplicate-row checks, and is the one tool allowed to read a
test-derived artifact before node 12 (D-g).

No sklearn (D-f): splitting is per-group permutation and cut with `np.random.default_rng(seed)`.
A per-call generator, never the global RNG, because tool bodies run concurrently in worker
threads. Output frames keep the input's row order and labels -- `frame.iloc[np.sort(positions)]`,
never `reset_index` (P1: parquet preserves a non-range index, which `verify_split`'s index
checks rely on).

pandas 3.0 facts this module leans on (verified under `-W error`, this chunk's §1):
`pd.util.hash_pandas_object` is deterministic across processes (P2) and, with `index=False`,
ignores the index so a relabelled duplicate still hashes equal (P3). Every number entering a
summary goes through `_frame.json_scalar`; class keys are `str(value)` (P2: `value_counts()`
values are `np.int64`, which `ToolSuccess` validation rejects).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Final

import numpy as np
import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_contracts.vocabulary import SplitRole

from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import is_class_target, json_scalar, observed_counts

MIN_SPLIT_ROWS: Final = 30
"""D33c: every split must hold at least this many rows. Named valid/test by D33c; train takes
the same floor (D-e: in every legal ratio but the extreme ones train is the largest split, and
where it is not the floor is just as necessary)."""

MIN_CLASS_ROWS_PER_SPLIT: Final = 5
"""D33c: for a class target, every class must hold at least this many rows in every split."""

BALANCE_DRIFT_Z_WARN: Final = 3.0
"""D-h: warns only when a split's class share is further from the pooled share than a random
split would plausibly produce -- the z-score `|share_in_split - share_in_pooled| /
sqrt(p*(1-p)/n_split)` (p the pooled share) exceeds this. A constant, not a parameter: the agent
has no basis for choosing one and the node logic never varies it. It replaces an absolute 0.05
tolerance that, measured on churn_frame's 48-row valid/test at ~31% positives, fired on 60 of 100
random seeds (one standard deviation of a 31% share at n=48 is ~0.066); the z rule's max over 200
random seeds was 2.56 (0 warnings) and a split cut from the target-sorted frame scored 5.34. It
only warns; failures still come from D33c's counts."""

OVERLAP_EXAMPLES: Final = 5
"""`_check_overlap` lists at most this many example labels per report."""

_SPLIT_ORDER: Final[tuple[SplitRole, SplitRole, SplitRole]] = ("train", "valid", "test")
_PAIRS: Final[tuple[tuple[SplitRole, SplitRole], ...]] = (
    ("train", "valid"),
    ("train", "test"),
    ("valid", "test"),
)

_Splits = dict[SplitRole, pd.DataFrame]


# --- checks --------------------------------------------------------------------------------------


def _check_size(splits: _Splits) -> dict[str, JsonValue]:
    """Every split has at least `MIN_SPLIT_ROWS` rows (D33c, D-e)."""
    rows: dict[str, JsonValue] = {name: json_scalar(len(splits[name])) for name in _SPLIT_ORDER}
    problems = [
        f"{name} has {len(splits[name])} rows, below MIN_SPLIT_ROWS={MIN_SPLIT_ROWS}"
        for name in _SPLIT_ORDER
        if len(splits[name]) < MIN_SPLIT_ROWS
    ]
    return {"status": "fail" if problems else "pass", "rows": rows, "problems": list(problems)}


def _check_balance(splits: _Splits, target: str) -> dict[str, JsonValue]:
    """Per-class counts and share drift for a class target; `skipped` for a continuous one.

    Classes are those of the pooled, non-null target, counted with `observed_counts` so a
    categorical's unused category (count 0) is not mistaken for a class every split then "lacks"
    -- that false class would make the D33c count floor refuse an otherwise-fine split. Per-split
    counts come from `observed_counts(...).reindex(classes, fill_value=0)`: `observed_counts`
    drops the categorical's phantom levels, and the reindex to the pooled classes with
    `fill_value=0` stays, because a class present in the pool but absent from one split must
    still show as `0`. `summarize_class_target` omits absent classes, which is exactly the case
    this check exists for, so it is not reused here.

    Fails on D33c's `MIN_CLASS_ROWS_PER_SPLIT` floor; warns when the largest per-split, per-class
    z-score `|share_in_split - share_in_pooled| / sqrt(p*(1-p)/n_split)` exceeds
    `BALANCE_DRIFT_Z_WARN`. `max_drift` (the absolute share difference) is still reported;
    `max_drift_z` is the z the status is decided on. The standard error is `0` only for a
    single-class target, where the share is trivially the pooled share -- its z is `0.0`.
    """
    pooled = pd.concat([splits[name][target] for name in _SPLIT_ORDER])
    if not is_class_target(pooled):
        return {"status": "skipped", "reason": "continuous target"}

    pooled_non_null = pooled.dropna()
    classes = list(observed_counts(pooled_non_null).index)
    pooled_total = len(pooled_non_null)
    pooled_counts = observed_counts(pooled_non_null).reindex(classes, fill_value=0)
    pooled_shares = {str(cls): json_scalar(pooled_counts[cls] / pooled_total) for cls in classes}

    per_split_counts: dict[str, JsonValue] = {}
    problems: list[str] = []
    max_drift = 0.0
    max_drift_z = 0.0
    for name in _SPLIT_ORDER:
        split_non_null = splits[name][target].dropna()
        counts = observed_counts(split_non_null).reindex(classes, fill_value=0)
        per_split_counts[name] = {str(cls): json_scalar(counts[cls]) for cls in classes}
        split_total = len(split_non_null)
        for cls in classes:
            if counts[cls] < MIN_CLASS_ROWS_PER_SPLIT:
                problems.append(
                    f"{name} has {int(counts[cls])} rows of class {cls!s}, below "
                    f"MIN_CLASS_ROWS_PER_SPLIT={MIN_CLASS_ROWS_PER_SPLIT}"
                )
            if split_total:
                share = counts[cls] / split_total
                pooled_share = pooled_counts[cls] / pooled_total
                max_drift = max(max_drift, abs(float(share - pooled_share)))
                std_error = math.sqrt(pooled_share * (1 - pooled_share) / split_total)
                z = abs(float(share - pooled_share)) / std_error if std_error > 0 else 0.0
                max_drift_z = max(max_drift_z, z)

    if problems:
        status = "fail"
    elif max_drift_z > BALANCE_DRIFT_Z_WARN:
        status = "warn"
    else:
        status = "pass"
    return {
        "status": status,
        "classes": per_split_counts,
        "pooled_shares": pooled_shares,
        "max_drift": json_scalar(max_drift),
        "max_drift_z": json_scalar(max_drift_z),
        "problems": list(problems),
    }


def _check_overlap(splits: _Splits) -> dict[str, JsonValue]:
    """Pairwise row-label overlap from `a.index.intersection(b.index)`. Fails on any non-zero
    pair. Lists at most `OVERLAP_EXAMPLES` overlapping labels (P1: labels are preserved)."""
    pairs: dict[str, JsonValue] = {}
    examples: list[JsonValue] = []
    any_overlap = False
    for a, b in _PAIRS:
        shared = splits[a].index.intersection(splits[b].index)
        any_overlap = any_overlap or len(shared) > 0
        pairs[f"{a}/{b}"] = json_scalar(len(shared))
        for label in shared[: OVERLAP_EXAMPLES - len(examples)]:
            examples.append(json_scalar(label))
    return {
        "status": "fail" if any_overlap else "pass",
        "pairs": pairs,
        "examples": list(examples),
    }


def _row_hashes(frame: pd.DataFrame) -> pd.Series:
    """`hash_pandas_object(frame, index=False)` (P3: ignores the index, so a relabelled
    duplicate still hashes equal). 64-bit collisions are negligible at D15 sizes -- a false
    "duplicate" would need two different rows to collide in 2**64, which does not happen at a
    few thousand rows."""
    return pd.util.hash_pandas_object(frame, index=False)


def _check_duplicates(splits: _Splits) -> dict[str, JsonValue]:
    """Pairwise duplicate-row count: rows of `b` whose row hash occurs in `a` (P2/P3). Fails on
    any non-zero pair. Same shape as `_check_overlap`."""
    pairs: dict[str, JsonValue] = {}
    any_duplicate = False
    for a, b in _PAIRS:
        a_hashes = set(_row_hashes(splits[a]).tolist())
        b_hashes = _row_hashes(splits[b])
        count = int(b_hashes.isin(a_hashes).sum())
        any_duplicate = any_duplicate or count > 0
        pairs[f"{a}/{b}"] = json_scalar(count)
    return {"status": "fail" if any_duplicate else "pass", "pairs": pairs}


# --- partitioning --------------------------------------------------------------------------------


def _cut(
    positions: np.ndarray, rng: np.random.Generator, test_size: float, valid_size: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Permute `positions` and split into (train, valid, test) by `round(n * size)`.

    `round` (banker's rounding, numpy/Python 3) rather than `int(...)` so a fractional
    `n * size` lands on the nearer integer, not always down -- this is why stratification tests
    must use class counts where `n_c * size` is fractional, or `round` never matters.
    """
    permuted = rng.permutation(positions)
    n = len(permuted)
    n_test = round(n * test_size)
    n_valid = round(n * valid_size)
    test = permuted[:n_test]
    valid = permuted[n_test : n_test + n_valid]
    train = permuted[n_test + n_valid :]
    return train, valid, test


def _fingerprint(test: pd.DataFrame) -> str:
    """D-c's content digest: order-independent (`sort_index`), label- and value-sensitive
    (`index=True` in the hash, columns folded in first). Needs no dataset identity (P4), and
    matches across runs exactly when the same rows of the same frame land in test."""
    h = hashlib.sha256(json.dumps([str(c) for c in test.columns]).encode())
    h.update(pd.util.hash_pandas_object(test.sort_index(), index=True).to_numpy().tobytes())
    return h.hexdigest()


def _prepare(
    paths: RunPaths, frame: ArtifactRef, target: str, test_size: float, valid_size: float
) -> tuple[pd.DataFrame, int]:
    """Shared preparation for both split tools (spec order): load, require the target, mirror
    V10 (`test_size + valid_size < 0.9`), drop null-target rows and count them."""
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not a column in the data", field_errors={"target": "unknown"}
        )
    if test_size + valid_size >= 0.9:
        raise ToolParamError(
            f"test_size + valid_size = {test_size + valid_size} must be < 0.9 (V10)",
            field_errors={"valid_size": "test_size + valid_size must be < 0.9"},
        )
    non_null_mask = data[target].notna()
    dropped = int((~non_null_mask).sum())
    return data[non_null_mask], dropped


def _finish(paths: RunPaths, splits: _Splits, target: str, dropped: int) -> ToolSuccess:
    """Shared finish: run the two refusal checks, raise `ToolDataError` on failure (writing no
    file), otherwise save the three frames with their `split` and return the summary."""
    size = _check_size(splits)
    balance = _check_balance(splits, target)
    if size["status"] == "fail" or balance["status"] == "fail":
        problems: list[str] = []
        for check in (size, balance):
            check_problems = check.get("problems")
            if isinstance(check_problems, list):
                problems.extend(str(p) for p in check_problems)
        raise ToolDataError("split refused (D33c): " + "; ".join(problems))

    metas: list[ArtifactMeta] = []
    for name in _SPLIT_ORDER:
        ref = new_artifact_ref()
        metas.append(
            save_dataframe(
                paths.artifact(ref, "dataframe"),
                splits[name],
                ref=ref,
                label=name,
                split=name,
            )
        )

    test_fingerprint = _fingerprint(splits["test"])
    summary: dict[str, JsonValue] = {
        "rows": {name: json_scalar(len(splits[name])) for name in _SPLIT_ORDER},
        "rows_dropped_null_target": json_scalar(dropped),
        "test_fingerprint": test_fingerprint,
        "checks": {"size": size, "target_balance": balance},
    }
    warnings: list[str] = []
    if dropped > 0:
        warnings.append(f"dropped {dropped} row(s) with a null target before splitting (D34d)")
    if balance["status"] == "warn":
        warnings.append(
            f"class balance drifts across splits (max_drift_z > {BALANCE_DRIFT_Z_WARN}); see checks"
        )
    return ToolSuccess(summary=summary, artifacts=tuple(metas), warnings=tuple(warnings))


# --- the tools -----------------------------------------------------------------------------------

_TestSize = Annotated[
    float,
    Field(gt=0.0, lt=1.0, description="Share of rows assigned to the test split."),
]
_ValidSize = Annotated[
    float,
    Field(ge=0.0, lt=1.0, description="Share of rows assigned to the validation split."),
]
_Seed = Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)]
"""Required, with no default, like `load_csv`'s `dataset`: if the orchestrator's injection ever
missed it, a default would silently split with the wrong seed instead of failing schema
validation. Declared before the defaulted ratios for that reason."""
_Target = Annotated[str, Field(min_length=1, description="The target column.")]


@tool(phase="splitting", allow_in=["data_splitting"])
def split_random(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to split.")],
    target: _Target,
    seed: _Seed,
    test_size: _TestSize = 0.2,
    valid_size: _ValidSize = 0.2,
) -> ToolSuccess:
    """Partition rows into train/valid/test uniformly at random. Use for an i.i.d. dataset
    with no time or group structure -- the common case.

    `target` is required even though the partition ignores its values: rows with a null target
    are dropped (D34d, leak-free -- it reads no target value) and the class-count guard (D33c)
    needs it. A refused split (any split below MIN_SPLIT_ROWS, or any class below
    MIN_CLASS_ROWS_PER_SPLIT) writes no file.
    """
    data, dropped = _prepare(paths, frame, target, test_size, valid_size)
    rng = np.random.default_rng(seed)
    train, valid, test = _cut(np.arange(len(data)), rng, test_size, valid_size)
    splits: _Splits = {
        "train": data.iloc[np.sort(train)],
        "valid": data.iloc[np.sort(valid)],
        "test": data.iloc[np.sort(test)],
    }
    return _finish(paths, splits, target, dropped)


@tool(phase="splitting", allow_in=["data_splitting"])
def split_stratified(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to split.")],
    target: _Target,
    seed: _Seed,
    test_size: _TestSize = 0.2,
    valid_size: _ValidSize = 0.2,
) -> ToolSuccess:
    """Partition rows so each split holds the same class proportions as the whole. Use for a
    class target, especially an imbalanced one -- a random split can starve test of a rare
    class. For a continuous target this is meaningless; use `split_random`.

    Each class's positions are cut independently (one generator, classes in `str`-sorted order)
    and unioned, so the per-split class shares match the pooled shares up to rounding. Null
    targets are dropped first (D34d); the same D33c guard refuses an undersized split.
    """
    data, dropped = _prepare(paths, frame, target, test_size, valid_size)
    if not is_class_target(data[target]):
        n_distinct = int(data[target].nunique())
        raise ToolParamError(
            f"{n_distinct} distinct numeric values; stratifying a continuous target is "
            f"meaningless; use split_random",
            field_errors={"target": "continuous target cannot be stratified"},
        )

    rng = np.random.default_rng(seed)
    target_values = data[target].to_numpy()
    train_parts: list[np.ndarray] = []
    valid_parts: list[np.ndarray] = []
    test_parts: list[np.ndarray] = []
    for cls in sorted(data[target].dropna().unique(), key=str):
        class_positions = np.flatnonzero(target_values == cls)
        tr, va, te = _cut(class_positions, rng, test_size, valid_size)
        train_parts.append(tr)
        valid_parts.append(va)
        test_parts.append(te)

    splits: _Splits = {
        "train": data.iloc[np.sort(np.concatenate(train_parts))],
        "valid": data.iloc[np.sort(np.concatenate(valid_parts))],
        "test": data.iloc[np.sort(np.concatenate(test_parts))],
    }
    return _finish(paths, splits, target, dropped)


@tool(phase="splitting", allow_in=["data_splitting"])
def verify_split(
    paths: RunPaths,
    train: Annotated[ArtifactRef, Field(description="The train split frame.")],
    valid: Annotated[ArtifactRef, Field(description="The validation split frame.")],
    test: Annotated[ArtifactRef, Field(description="The test split frame.")],
    target: _Target,
) -> ToolSuccess:
    """Check three already-produced split frames for leakage and size. Runs four checks:
    index_overlap (shared row labels), duplicate_rows (identical rows across splits, index
    aside), target_balance (per-class counts and share drift), and size (every split above the
    floor).

    `passed` is false iff any check fails. Reads `test` -- the one tool allowed to before node
    12 (D-g) -- but produces no artifact, so nothing inherits test lineage from it. Valid and
    test are reordered to train's column order before hashing, because the row hash depends on
    column order; a frame whose column *set* differs from train's is a `tool_param` error, so a
    mismatched frame cannot silently pass the duplicate check.
    """
    frames: _Splits = {
        "train": load_dataframe(paths.artifact(train, "dataframe")),
        "valid": load_dataframe(paths.artifact(valid, "dataframe")),
        "test": load_dataframe(paths.artifact(test, "dataframe")),
    }

    missing = {name for name, frame in frames.items() if target not in frame.columns}
    if missing:
        raise ToolParamError(
            f"target {target!r} is missing from: {sorted(missing)}",
            field_errors={"target": f"not a column in: {sorted(missing)}"},
        )

    train_columns = set(frames["train"].columns)
    field_errors: dict[str, str] = {}
    for name in ("valid", "test"):
        if set(frames[name].columns) != train_columns:
            field_errors[name] = "column set differs from train"
    if field_errors:
        raise ToolParamError(
            "a split frame's column set differs from train's", field_errors=field_errors
        )

    column_order = list(frames["train"].columns)
    frames["valid"] = frames["valid"][column_order]
    frames["test"] = frames["test"][column_order]

    checks: dict[str, JsonValue] = {
        "index_overlap": _check_overlap(frames),
        "duplicate_rows": _check_duplicates(frames),
        "target_balance": _check_balance(frames, target),
        "size": _check_size(frames),
    }
    passed = not any(
        isinstance(check, dict) and check.get("status") == "fail" for check in checks.values()
    )
    warnings = tuple(
        f"{name} check status is {check['status']}"
        for name, check in checks.items()
        if isinstance(check, dict) and check.get("status") in ("warn", "fail")
    )
    return ToolSuccess(summary={"passed": passed, "checks": checks}, warnings=warnings)
