"""Data quality checks before the split (chunk 63): Deepchecks-style integrity checks
written in pandas. No fitting, so they may read the whole input like profiling does. Two
tools: `check_data_quality` reports, `fix_data_quality` applies the two safe fixes. Output
frames keep the input's index labels and row order.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Annotated, Final, Literal

import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactRef
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import load_dataframe
from statlab_tools.tool import ToolParamError, tool
from statlab_tools.tools._frame import (
    as_str,
    as_str_scalar,
    is_class_target,
    is_numeric_column,
    json_list,
    json_scalar,
    observed_counts,
    require_columns,
    save_frame,
)

MISSING_TOKENS: Final = frozenset(
    {
        "",
        "?",
        "-",
        "--",
        "na",
        "n/a",
        "nan",
        "null",
        "none",
        "missing",
        "unknown",
        "undefined",
        "not available",
    }
)
"""Text cell values that stand for a missing value; compared after `strip()` and `casefold()`."""

SENTINELS: Final = (-999.0, -9999.0, 9999.0, 99999.0, 999999.0)
"""Numeric cell values that stand for a missing value."""

MIXED_LOW: Final = 0.05
"""Lower bound of the numeric share for a text column to count as mixed."""

MIXED_HIGH: Final = 0.95
"""Upper bound of the numeric share for a text column to count as mixed."""

NEAR_CONSTANT_SHARE: Final = 0.99
"""Share of rows one value must hold to make a column near-constant."""

RARE_LEVEL_ROWS: Final = 5
"""A text level with fewer rows than this is rare."""

ID_LIKE_SHARE: Final = 0.5
"""A text column with more distinct values than this share of rows is ID-like, not categorical."""

EXAMPLES: Final = 5
"""How many example values or rows a detail lists at most."""

UNLABELLED_MIN_ROWS: Final = 20
"""Fewer rows without a target than this skips the missing_without_target check."""

UNLABELLED_PRESENT_MAX: Final = 0.01
"""A column filled on at most this share of rows without a target is flagged."""

LABELLED_PRESENT_MIN: Final = 0.5
"""...and on at least this share of rows with a target."""

ORDER_MIN_ROWS: Final = 100
"""Fewer rows with a target than this skips the row-order check."""

ORDER_LIFT: Final = 0.15
"""How far the same-class-as-previous share must exceed the shuffled expectation."""

ORDER_CORRELATION: Final = 0.5
"""Lag-1 autocorrelation from which a continuous target follows the row order."""

_Status = Literal["pass", "warn", "fail", "skipped"]
_Target = Annotated[str, Field(min_length=1, description="The target column.")]


def _text(series: pd.Series) -> bool:
    """A text column: not numeric, not bool, not datetime."""
    return (
        not is_numeric_column(series)
        and not pd.api.types.is_bool_dtype(series)
        and not pd.api.types.is_datetime64_any_dtype(series)
    )


def _check(
    status: _Status, rows: int, columns: list[JsonValue], details: JsonValue
) -> dict[str, JsonValue]:
    """One check's result: status, affected row count, affected column names, details."""
    return {"status": status, "rows": rows, "columns": columns, "details": details}


def _variant_groups(series: pd.Series) -> list[list[str]]:
    """Groups of spellings that differ only by case, spacing or punctuation. Inside a group the
    most frequent spelling comes first (then text ascending); groups are ordered by that first
    spelling. Only groups with at least two spellings are returned."""
    strings = as_str(series.dropna())
    keys = strings.str.casefold().str.replace(r"[\W_]+", "", regex=True)
    counts = strings.value_counts()
    key_of = dict(zip(strings, keys, strict=True))
    by_key: dict[str, list[str]] = {}
    for spelling in counts.index:
        key = key_of[spelling]
        if key:
            by_key.setdefault(key, []).append(str(spelling))
    groups: list[list[str]] = []
    for members in by_key.values():
        if len(members) >= 2:
            members.sort(key=lambda s: (-int(counts[s]), s))
            groups.append(members)
    groups.sort(key=lambda members: members[0])
    return groups


@tool(phase="quality", allow_in=["data_quality"])
def check_data_quality(
    paths: RunPaths,
    frame: Annotated[
        ArtifactRef, Field(description="The frame to check, usually the profiled frame.")
    ],
    target: _Target,
    id_columns: Annotated[
        tuple[str, ...],
        Field(description="Identifier columns; excluded from every check."),
    ] = (),
    min_class_rows: Annotated[
        int,
        Field(
            ge=2,
            description="Smallest class size cross-validation needs; pass the contract's CV fold "
            "count.",
        ),
    ] = 5,
) -> ToolSuccess:
    """Run eleven integrity checks on a frame before the split and report each one's status.

    Checks: disguised missing values, mixed types, category spelling variants, duplicate rows,
    conflicting labels, near-constant columns, rare categories, target health, duplicate
    columns, columns empty where the target is missing, target following the row order. Each
    check reports `pass`, `warn`, `fail` or `skipped` with the rows and columns it touches.
    Nothing is changed; `fix_data_quality` applies the two safe fixes it proposes.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    require_columns(data, (target, *id_columns), "columns")
    n = len(data)
    features = [str(c) for c in data.columns if c != target and c not in id_columns]
    results: dict[str, dict[str, JsonValue]] = {}
    mark_plan: dict[str, JsonValue] = {}
    merge_plan: list[str] = []

    # 1. disguised_missing
    details: list[JsonValue] = []
    columns: list[str] = []
    affected: set[Hashable] = set()
    for c in features:
        values = data[c].dropna()
        if _text(data[c]):
            hit = values[as_str(values).str.strip().str.casefold().isin(MISSING_TOKENS)]
        elif is_numeric_column(data[c]):
            hit = values[values.isin(SENTINELS)]
        else:
            continue
        if hit.empty:
            continue
        affected.update(hit.index)
        columns.append(c)
        hit_values = json_list(sorted(set(as_str(hit))))
        mark_plan[c] = hit_values
        detail: dict[str, JsonValue] = {"column": c, "count": len(hit), "values": hit_values}
        details.append(detail)
    results["disguised_missing"] = _check(
        "warn" if details else "pass", len(affected), json_list(columns), details
    )

    # 2. mixed_types
    details = []
    columns = []
    affected = set()
    for c in features:
        if not _text(data[c]):
            continue
        values = data[c].dropna()
        if len(values) == 0:
            continue
        numeric = pd.to_numeric(values.astype("str"), errors="coerce").notna()
        share = float(numeric.mean())
        if not MIXED_LOW <= share <= MIXED_HIGH:
            continue
        minority = ~numeric if share >= 0.5 else numeric
        minority_values = values[minority]
        examples = sorted(set(as_str(minority_values)))[:EXAMPLES]
        detail = {
            "column": c,
            "numeric_share": round(share, 3),
            "examples": json_list(examples),
        }
        details.append(detail)
        affected.update(minority_values.index)
        columns.append(c)
    results["mixed_types"] = _check(
        "warn" if details else "pass", len(affected), json_list(columns), details
    )

    # 3. category_variants
    details = []
    columns = []
    affected = set()
    for c in features:
        if not _text(data[c]):
            continue
        groups = _variant_groups(data[c])
        if not groups:
            continue
        variants = {s for group in groups for s in group[1:]}
        strings = as_str(data[c].dropna())
        affected.update(strings[strings.isin(variants)].index)
        detail = {"column": c, "groups": [json_list(g) for g in groups[:EXAMPLES]]}
        details.append(detail)
        columns.append(c)
    merge_plan = list(columns)
    results["category_variants"] = _check(
        "warn" if details else "pass", len(affected), json_list(columns), details
    )

    # 4. duplicate_rows
    subset = [str(c) for c in data.columns if c not in id_columns]
    mask = data.duplicated(subset=subset, keep="first").to_numpy()
    dup_rows = int(mask.sum())
    dup_detail: dict[str, JsonValue] = {
        "share": round(dup_rows / n, 4) if n else 0.0,
        "example_rows": json_list(data.index[mask][:EXAMPLES]),
    }
    results["duplicate_rows"] = _check("warn" if dup_rows else "pass", dup_rows, [], dup_detail)

    # 5. conflicting_labels
    target_series = data[target]
    if not is_class_target(target_series):
        results["conflicting_labels"] = _check("skipped", 0, [], {"reason": "continuous target"})
    elif not features:
        results["conflicting_labels"] = _check("pass", 0, [], {"groups": 0, "example_rows": []})
    else:
        keys = pd.util.hash_pandas_object(data[features], index=False)
        per_key = target_series.groupby(keys).nunique(dropna=False)
        conflict_mask = keys.isin(per_key.index[per_key > 1]).to_numpy()
        conflict_rows = int(conflict_mask.sum())
        conflict_detail: dict[str, JsonValue] = {
            "groups": int((per_key > 1).sum()),
            "example_rows": json_list(data.index[conflict_mask][:EXAMPLES]),
        }
        results["conflicting_labels"] = _check(
            "warn" if conflict_rows else "pass", conflict_rows, [], conflict_detail
        )

    # 6. near_constant
    details = []
    columns = []
    affected = set()
    for c in features if n > 0 else []:
        counts = observed_counts(data[c])
        if len(counts) == 0:
            continue
        top_share = float(counts.iloc[0]) / n
        if top_share < NEAR_CONSTANT_SHARE:
            continue
        value = counts.index[0]
        detail = {"column": c, "value": json_scalar(value), "share": round(top_share, 4)}
        details.append(detail)
        affected.update(data.index[(data[c] == value).to_numpy()])
        columns.append(c)
    results["near_constant"] = _check(
        "warn" if details else "pass", len(affected), json_list(columns), details
    )

    # 7. rare_categories
    details = []
    columns = []
    affected = set()
    for c in features:
        if not _text(data[c]):
            continue
        counts = observed_counts(data[c])
        if len(counts) <= 1 or data[c].nunique() > ID_LIKE_SHARE * n:
            continue
        rare = counts[counts < RARE_LEVEL_ROWS]
        if rare.empty:
            continue
        detail = {
            "column": c,
            "levels": len(rare),
            "rows": int(rare.sum()),
            "examples": json_list(rare.index[:EXAMPLES]),
        }
        details.append(detail)
        affected.update(data.index[data[c].isin(rare.index).to_numpy()])
        columns.append(c)
    results["rare_categories"] = _check(
        "warn" if details else "pass", len(affected), json_list(columns), details
    )

    # 8. target_health
    nulls = int(target_series.isna().sum())
    if is_class_target(target_series):
        target_counts = observed_counts(target_series.dropna())
        small = target_counts[target_counts < min_class_rows]
    else:
        small = pd.Series(dtype="int64")
    small_classes: dict[str, JsonValue] = {as_str_scalar(k): int(v) for k, v in small.items()}
    target_detail: dict[str, JsonValue] = {"nulls": nulls, "small_classes": small_classes}
    if not small.empty:
        target_status: _Status = "fail"
    elif nulls:
        target_status = "warn"
    else:
        target_status = "pass"
    target_mask = (target_series.isna() | target_series.isin(small.index)).to_numpy()
    results["target_health"] = _check(
        target_status,
        int(target_mask.sum()),
        [] if target_status == "pass" else json_list([target]),
        target_detail,
    )

    # 9. duplicate_columns
    pairs: list[list[str]] = []
    for i, a in enumerate(features):
        for b in features[i + 1 :]:
            # ponytail: O(k^2) column compare; hash columns if very wide frames matter
            if data[a].equals(data[b]):
                pairs.append([a, b])
    pair_names = list(dict.fromkeys(name for pair in pairs for name in pair))
    dup_col_details: dict[str, JsonValue] = {}
    if pairs:
        dup_col_details = {"pairs": [json_list(p) for p in pairs[:EXAMPLES]]}
    results["duplicate_columns"] = _check(
        "warn" if pairs else "pass", 0, json_list(pair_names), dup_col_details
    )

    # 10. missing_without_target
    unlabelled = target_series.isna().to_numpy()
    missing_rows = int(unlabelled.sum())
    if missing_rows < UNLABELLED_MIN_ROWS:
        results["missing_without_target"] = _check(
            "skipped", 0, [], {"reason": "fewer than 20 rows without a target"}
        )
    else:
        details = []
        columns = []
        for c in features:
            labelled_present = (
                float(data[c][~unlabelled].notna().mean()) if n - missing_rows > 0 else 0.0
            )
            unlabelled_present = float(data[c][unlabelled].notna().mean())
            if (
                unlabelled_present <= UNLABELLED_PRESENT_MAX
                and labelled_present >= LABELLED_PRESENT_MIN
            ):
                columns.append(c)
                details.append(
                    {
                        "column": c,
                        "labelled_present": round(labelled_present, 3),
                        "unlabelled_present": round(unlabelled_present, 3),
                    }
                )
        results["missing_without_target"] = _check(
            "warn" if details else "pass",
            missing_rows if details else 0,
            json_list(columns),
            details,
        )

    # 11. target_follows_row_order
    labelled = target_series.dropna()
    if len(labelled) < ORDER_MIN_ROWS:
        results["target_follows_row_order"] = _check(
            "skipped", 0, [], {"reason": "fewer than 100 rows with a target"}
        )
    elif is_class_target(labelled):
        order_values = labelled.to_numpy()
        same = float((order_values[1:] == order_values[:-1]).mean())
        changes = int((order_values[1:] != order_values[:-1]).sum())
        shares = observed_counts(labelled) / len(labelled)
        expected = float((shares**2).sum())
        follows = same - expected >= ORDER_LIFT and changes > len(shares) - 1
        results["target_follows_row_order"] = _check(
            "warn" if follows else "pass",
            0,
            json_list([target]) if follows else [],
            {"same_as_previous": round(same, 3), "expected": round(expected, 3)},
        )
    else:
        target_numeric = labelled.astype("float64")
        sorted_target = (
            target_numeric.is_monotonic_increasing or target_numeric.is_monotonic_decreasing
        )
        correlation = target_numeric.autocorr(lag=1)
        follows = (
            not sorted_target
            and not pd.isna(correlation)
            and float(correlation) >= ORDER_CORRELATION
        )
        results["target_follows_row_order"] = _check(
            "warn" if follows else "pass",
            0,
            json_list([target]) if follows else [],
            {"lag1_correlation": None if pd.isna(correlation) else round(float(correlation), 3)},
        )

    fixable: dict[str, JsonValue] = {
        "mark_missing": mark_plan,
        "merge_variants": json_list(merge_plan),
    }
    checks: dict[str, JsonValue] = dict(results)
    summary: dict[str, JsonValue] = {
        "rows": n,
        "passed": not any(check["status"] == "fail" for check in results.values()),
        "checks": checks,
        "fixable": fixable,
    }
    warnings = tuple(
        f"{name} check status is {check['status']}"
        for name, check in results.items()
        if check["status"] in ("warn", "fail")
    )
    return ToolSuccess(summary=summary, warnings=warnings)


@tool(phase="quality", allow_in=["data_quality"])
def fix_data_quality(
    paths: RunPaths,
    frame: Annotated[
        ArtifactRef, Field(description="The frame to fix, the one `check_data_quality` read.")
    ],
    target: _Target,
    mark_missing: Annotated[
        dict[str, tuple[str, ...]] | None,
        Field(
            description="Per column, the exact cell texts (as reported under fixable.mark_missing) "
            "to turn into missing values."
        ),
    ] = None,
    merge_variants: Annotated[
        tuple[str, ...],
        Field(
            description="Text columns whose spelling variants merge into the most frequent "
            "spelling."
        ),
    ] = (),
) -> ToolSuccess:
    """Apply the two safe fixes `check_data_quality` proposes: turn listed cell texts into missing
    values (`mark_missing`) and merge spelling variants of text columns into their most frequent
    spelling (`merge_variants`). The target is never changed. Writes a new frame; the input is
    left as it was.
    """
    if not mark_missing and not merge_variants:
        raise ToolParamError(
            "no fixes requested", field_errors={"mark_missing": "no fixes requested"}
        )
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    mark_cols = tuple(mark_missing or {})
    require_columns(data, mark_cols, "mark_missing")
    require_columns(data, merge_variants, "merge_variants")
    if target in mark_cols:
        raise ToolParamError(
            "the target is never changed here",
            field_errors={"mark_missing": "the target is never changed here"},
        )
    if target in merge_variants:
        raise ToolParamError(
            "the target is never changed here",
            field_errors={"merge_variants": "the target is never changed here"},
        )
    for column in merge_variants:
        if not _text(data[column]):
            raise ToolParamError(
                "only text columns have spelling variants",
                field_errors={"merge_variants": "only text columns have spelling variants"},
            )

    fixed = data.copy()
    marked: dict[str, JsonValue] = {}
    for column, texts in (mark_missing or {}).items():
        wanted = {text.strip().casefold() for text in texts}
        mask = fixed[column].notna() & as_str(fixed[column]).str.strip().str.casefold().isin(wanted)
        fixed[column] = fixed[column].mask(mask)
        marked[column] = int(mask.sum())

    merged: dict[str, JsonValue] = {}
    for column in merge_variants:
        mapping = {
            spelling: group[0] for group in _variant_groups(fixed[column]) for spelling in group[1:]
        }
        categorical = isinstance(fixed[column].dtype, pd.CategoricalDtype)
        merged_column = as_str(fixed[column]).replace(mapping).where(fixed[column].notna())
        if categorical:
            merged_column = merged_column.astype("category")
        fixed[column] = merged_column
        merged[column] = dict(mapping)

    summary: dict[str, JsonValue] = {
        "rows": len(fixed),
        "marked_missing": marked,
        "merged": merged,
    }
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, fixed, "quality_fixed"),))
