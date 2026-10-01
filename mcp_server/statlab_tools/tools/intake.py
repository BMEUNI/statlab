"""Node 1 intake tools: PLAN.md §4 node 1, §6.3's `intake` phase.

`load_csv` holds the one trust boundary in the ~86-tool catalogue: `dataset` is a sha256
identifier, not a path (D-a of `plan/08_tools_intake.md`), so a traversal shape is
unrepresentable by the schema rather than merely rejected by a check. The remaining four tools
report on a dataframe artifact already produced by `load_csv`; none of them modify a frame --
intake reports, it never changes data (D-e, D-d).

pandas 3.0 facts this module leans on (`00_index.md`, `plan/08_tools_intake.md` §1): string
columns are dtype `str`, detected with `pd.api.types.is_string_dtype`, never `dtype == object`;
`UnicodeDecodeError`/`EmptyDataError`/`ParserError` are all `ValueError` subclasses and must be
reclassified before they reach the decorator's default `bug`; `keep_default_na=False` makes the
contract's `na_values` list authoritative instead of pandas' own silent "None"/"nan" rule;
`a.rank().corr(b.rank())` avoids importing scipy that `.corr(method="spearman")` would pull in
silently, and returns `0.9999999999999999` rather than `1.0` -- compared with `>=`, never `==`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Final

import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.contract import DataContract, DatasetOptions
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import SHA256_PATTERN, ProjectPaths, RunPaths
from statlab_contracts.vocabulary import ColumnRole

from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import (
    CLASS_TARGET_MAX_LEVELS,
    is_numeric_column,
    iso8601_parse_share,
    json_scalar,
)

_OPTS: Final = DatasetOptions()
"""`load_csv`'s defaults and bounds come from here, not restated -- the contract and the tool
must agree on what "the default CSV dialect" means (§3.2 of the spec)."""

ROW_WARNING_THRESHOLD: Final = 200_000
"""D15: node 1 warns above this many rows that fitting will be slow. Nothing is ever sampled;
this is a warning, not a cap."""

_DELIMITER_CANDIDATES: Final = (";", "\t", "|", ",")
"""Characters `load_csv` checks for inside a single parsed column's name (P3). Order is the
order suggested in a repair message when more than one appears; `;` first because it is the
most common wrong-delimiter case after `,`."""

DATETIME_PARSE_SHARE: Final = 0.95
"""R7 / C10: a string column is `datetime` when at least this share of its non-null values
parse as ISO 8601. Shared between `infer_column_roles` and `validate_contract` so the two never
disagree about what "parses as a date" means."""

TEXT_MEAN_LENGTH: Final = 40
"""R8: mean string length at or above this, combined with `TEXT_UNIQUE_SHARE`, reads as free
text rather than a category."""

TEXT_UNIQUE_SHARE: Final = 0.5
"""R8's second half: unique share at or above this. Both thresholds must hold -- a short code
repeated with high uniqueness (e.g. a serial number) is not text; a long but low-cardinality
string is closer to categorical."""

_BOOLEAN_LIKE_STRINGS: Final = frozenset({"true", "false", "yes", "no", "y", "n"})
"""R6: lowercased string values that read as boolean when exactly two distinct values from this
set are present."""

ID_UNIQUE_SHARE: Final = 0.95
"""`detect_id_columns`: unique share (n_unique / n_non_null) at or above this makes a column an
id candidate."""

MIN_ROWS_FOR_ID: Final = 20
"""`detect_id_columns`: fewer non-null rows than this and uniqueness is skipped -- it means
nothing on a handful of rows."""

LEAK_CORRELATION: Final = 0.98
"""L2: `abs(rank_corr) >=` this flags a numeric feature against a numeric target."""

LEAK_PURITY: Final = 0.99
"""L3: level purity at or above this, combined with the majority-share guard, flags a feature."""

LEAK_MAJORITY_SHARE_GUARD: Final = 0.95
"""L3's guard: the overall majority class share must be below this for L3 to fire at all. On a
99%-negative target every feature looks "99% pure"; this guard is what tells the difference
between a real leak and an imbalanced target."""

LEAK_UNIQUE_SHARE_SKIP: Final = 0.5
"""L3 skips any *non-numeric* feature whose own unique share exceeds this -- a string id is
trivially 100% "pure" against any target and is `detect_id_columns`' concern, not a leak.
Numeric features no longer take the purity path at all (they go through rank AUC), so this
skip only ever gates string-shaped features now."""

LEAK_AUC: Final = 0.99
"""A numeric feature against a class target is flagged when its rank-based one-vs-rest AUC
reaches this. AUC is insensitive to class balance, so -- unlike the binned-purity path -- a
perfect continuous leak scores 1.0 at every split, not only when the class boundary happens to
fall on a decile edge (R2's amendment). Compared with `>=`."""

LEAK_MIN_CLASS_ROWS: Final = 10
"""The rank-AUC path scores a class only when it has at least this many rows among the paired
non-null rows. A class with a handful of members produces a noisy extreme AUC that means
nothing; AUC's balance-insensitivity makes a tiny class the one way it can still mislead."""

LEAK_MIN_ROWS: Final = 20
"""L3: `_level_purity` returns `None` when fewer than this many rows have both a non-null level
and a non-null target. A separate constant from `MIN_ROWS_FOR_ID` -- same value today,
different meaning (purity needs enough paired rows to mean anything, not enough rows to judge
uniqueness), and the two must be free to diverge."""


# --- load_csv ------------------------------------------------------------------------------------


def _dataset_csv_path(paths: RunPaths, dataset: str) -> Path:
    """Resolve `dataset` to a real path under `datasets/`, enforcing containment.

    `dataset`'s *shape* is already constrained by the schema (`SHA256_PATTERN`) before this
    function ever runs -- `../x`, `/etc/passwd` and every other path-shaped string cannot reach
    here at all (D-a). This function is the defence-in-depth second line: a symlink planted
    inside `datasets/` whose target resolves outside it. Two lines, not one, because the
    schema's unrepresentability guarantee and a filesystem-level escape are different failure
    modes with different fixes.
    """
    project = ProjectPaths(data_dir=paths.data_dir, slug=paths.slug)
    resolved = project.dataset_csv(dataset).resolve()
    if not resolved.is_relative_to(project.datasets.resolve()):
        raise ToolParamError(
            f"dataset {dataset!r} resolves outside datasets/",
            field_errors={"dataset": "resolves outside the project's datasets directory"},
        )
    return resolved


def _reclassify_read_error(exc: ValueError | LookupError, *, encoding: str) -> Exception:
    """Maps pandas' CSV-reading exceptions to the taxonomy §3.2 requires. P1 covers
    `UnicodeDecodeError`/`EmptyDataError`/`ParserError`, all `ValueError` subclasses; an unknown
    codec name raises a plain `LookupError`, which is NOT a `ValueError` (confirmed against the
    installed `codecs` module) and needs its own `except` clause at the call site, not just its
    own branch here. Anything not named here is re-raised unchanged and reaches the decorator
    as `bug` -- which is correct for a genuinely unexpected error, not merely untested (L20
    asserts this over every P1 case)."""
    if isinstance(exc, UnicodeDecodeError):
        return ToolParamError(
            f"cannot decode as {encoding!r}: {exc} (byte offset {exc.start})",
            field_errors={"encoding": f"byte {exc.start} is not valid {encoding!r}"},
        )
    if isinstance(exc, LookupError):
        return ToolParamError(
            f"unknown encoding {encoding!r}: {exc}", field_errors={"encoding": "unknown codec"}
        )
    if isinstance(exc, pd.errors.EmptyDataError):
        return ToolDataError("file is empty")
    if isinstance(exc, pd.errors.ParserError):
        return ToolDataError(f"malformed CSV: {exc}")
    return exc


def _raw_header_row(path: Path, *, delimiter: str, encoding: str, header_row: int) -> list[str]:
    """Re-reads only the header row, unparsed by column-renaming, to detect duplicates (P2).
    Verified this raw read sees `a,b,a` correctly, including with `header_row > 0`."""
    raw = pd.read_csv(
        path, header=None, nrows=1, skiprows=header_row, sep=delimiter, encoding=encoding
    )
    return [str(value) for value in raw.iloc[0].tolist()]


def _check_duplicate_headers(header: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in header:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    if duplicates:
        raise ToolDataError(f"duplicate column name(s) in header: {sorted(duplicates)}")


def _check_single_column(frame: pd.DataFrame, *, delimiter: str) -> None:
    """P3: a single parsed column whose name contains another delimiter character means the
    wrong `delimiter` was supplied -- repairable. A single column with no such character is a
    data problem: a target and at least one feature are needed, and no argument fixes that."""
    if frame.shape[1] != 1:
        return
    (name,) = (str(c) for c in frame.columns)
    found = [c for c in _DELIMITER_CANDIDATES if c != delimiter and c in name]
    if found:
        suggestion = found[0]
        raise ToolParamError(
            f"parsed one column; header contains {suggestion!r} -- try delimiter={suggestion!r}",
            field_errors={"delimiter": f"try delimiter={suggestion!r}"},
        )
    raise ToolDataError("only one column was read; a target and at least one feature are needed")


@tool(phase="intake", allow_in=["intake"])
def load_csv(
    paths: RunPaths,
    dataset: Annotated[str, Field(pattern=SHA256_PATTERN, description=ORCHESTRATOR_SUPPLIED)],
    delimiter: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4,
            description="Field separator. A single parsed column containing a different "
            "delimiter character in its name usually means this is wrong.",
        ),
    ] = _OPTS.delimiter,
    encoding: Annotated[
        str,
        Field(
            min_length=1,
            description="Text encoding of the uploaded file. A decode failure names the byte "
            "offset; retrying with the right encoding (e.g. 'latin-1') is the fix.",
        ),
    ] = _OPTS.encoding,
    decimal: Annotated[
        str,
        Field(
            min_length=1,
            max_length=1,
            description="Character used as the decimal point in numeric columns.",
        ),
    ] = _OPTS.decimal,
    na_values: Annotated[
        tuple[str, ...],
        Field(
            description="Strings treated as missing. This list is authoritative -- unlisted "
            "strings such as 'None' or 'nan' are kept as literal text, never silently dropped."
        ),
    ] = _OPTS.na_values,
    header_row: Annotated[
        int, Field(ge=0, description="Zero-based row index containing the column names.")
    ] = _OPTS.header_row,
) -> ToolSuccess:
    """Read the run's uploaded CSV into the `raw` dataframe artifact.

    `dataset` identifies the upload by its sha256, not by a path -- `../`, `/etc/passwd` and
    every other path shape are unrepresentable by the schema (D-a). No type conversion happens
    beyond what pandas does by default: dates stay `str`; detecting them is
    `infer_column_roles`' job, not this tool's.
    """
    path = _dataset_csv_path(paths, dataset)
    if not path.is_file():
        raise ToolDataError(f"dataset {dataset} not found")

    try:
        frame = pd.read_csv(
            path,
            sep=delimiter,
            encoding=encoding,
            decimal=decimal,
            na_values=list(na_values),
            keep_default_na=False,
            header=header_row,
        )
    except (ValueError, LookupError) as exc:
        raise _reclassify_read_error(exc, encoding=encoding) from exc

    header = _raw_header_row(path, delimiter=delimiter, encoding=encoding, header_row=header_row)
    _check_duplicate_headers(header)
    _check_single_column(frame, delimiter=delimiter)
    if frame.shape[0] == 0:
        raise ToolDataError("file has zero data rows")

    ref = new_artifact_ref()
    meta = save_dataframe(paths.artifact(ref, "dataframe"), frame, ref=ref, label="raw")

    warnings: list[str] = []
    if frame.shape[0] > ROW_WARNING_THRESHOLD:
        warnings.append(
            f"{frame.shape[0]} rows exceeds {ROW_WARNING_THRESHOLD}; fitting will be slow (D15)"
        )

    dtypes: dict[str, JsonValue] = {str(c): str(d) for c, d in frame.dtypes.items()}
    null_counts: dict[str, JsonValue] = {str(c): int(v) for c, v in frame.isna().sum().items()}
    return ToolSuccess(
        summary={
            "rows": frame.shape[0],
            "cols": frame.shape[1],
            "columns": [str(c) for c in frame.columns],
            "dtypes": dtypes,
            "null_counts": null_counts,
        },
        artifacts=(meta,),
        warnings=tuple(warnings),
    )


# --- infer_column_roles --------------------------------------------------------------------------


def _role_for_column(series: pd.Series) -> tuple[ColumnRole, str] | None:
    """R1-R9, first match wins. Returns `None` for R1 (all-null) -- callers put the column in
    `all_null` instead of `roles`."""
    non_null = series.dropna()

    if non_null.empty:  # R1
        return None

    if pd.api.types.is_bool_dtype(series):  # R2
        return "boolean", "bool dtype"

    if pd.api.types.is_numeric_dtype(series):  # R3 / R4
        distinct = set(non_null.unique().tolist())
        if distinct <= {0, 1} and len(distinct) == 2:
            return "boolean", "numeric with values {0, 1}"
        return "numeric", str(series.dtype)

    if pd.api.types.is_datetime64_any_dtype(series):  # R5
        return "datetime", "datetime dtype"

    if pd.api.types.is_string_dtype(series):
        lowered = non_null.astype(str).str.lower()
        distinct_strings = set(lowered.unique().tolist())
        if distinct_strings <= _BOOLEAN_LIKE_STRINGS and len(distinct_strings) == 2:  # R6
            return "boolean", "two boolean-like strings"

        share = iso8601_parse_share(series)
        if share >= DATETIME_PARSE_SHARE:  # R7
            return "datetime", f"{share:.0%} parse as ISO 8601"

        lengths = non_null.astype(str).str.len()
        mean_length = float(lengths.mean())
        unique_share = non_null.nunique() / len(non_null)
        if mean_length >= TEXT_MEAN_LENGTH and unique_share >= TEXT_UNIQUE_SHARE:  # R8
            return "text", f"mean length {mean_length:.0f}, {unique_share:.0%} unique"

    n_distinct = non_null.nunique()  # R9
    return "categorical", f"{n_distinct} distinct values"


@tool(phase="intake", allow_in=["intake"])
def infer_column_roles(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect, usually `raw`.")],
) -> ToolSuccess:
    """Infer a role for every column from its dtype and values. Never infers `ordinal`.

    This is a heuristic, applied in a fixed order (R1-R9, first match wins). `ordinal` needs a
    level order only the contract can supply, so it is never a candidate here. Datetimes are
    only recognised when they parse as ISO 8601 -- `03/01/2026` is ambiguous between January
    and March, and guessing would silently corrupt every date-derived feature downstream.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    roles: dict[str, JsonValue] = {}
    evidence: dict[str, JsonValue] = {}
    all_null: list[JsonValue] = []
    for column in data.columns:
        result = _role_for_column(data[column])
        if result is None:
            all_null.append(str(column))
            continue
        role, why = result
        roles[str(column)] = role
        evidence[str(column)] = why
    return ToolSuccess(summary={"roles": roles, "evidence": evidence, "all_null": all_null})


# --- validate_contract -----------------------------------------------------------------------


_Mismatch = dict[str, JsonValue]


def _mismatch(field: str, message: str) -> _Mismatch:
    return {"field": field, "message": message}


def _declared_matches_observed(
    declared: Iterable[str | int | bool], observed: pd.Series
) -> tuple[set[str], set[str]]:
    """Render a declared set of target values and the observed non-null values onto a common
    representation so C7/C8 compare like with like.

    When the target column is numeric (`is_numeric_column`, which excludes bool), both sides are
    compared as floats: a 0/1 column with one missing value loads as `float64`, so its observed
    set is `{0.0, 1.0}` while a declared `positive_class=1` would stringify to `"1"` and never
    match (R1). A declared value that does not parse as a number against a numeric target is a
    *mismatch*, not an exception -- it is rendered as a sentinel that cannot equal any observed
    float, so it falls out of the comparison without a crash. Otherwise both sides are compared
    as strings.

    `declared` is typed to the exact scalar union the contract allows (`positive_class` is
    `str | int | bool`, `class_order` entries are `str`), so `float(value)` type-checks with no
    narrowing and there is no unreachable "not a scalar" branch to cover.
    """
    non_null = observed.dropna()
    declared_list = list(declared)
    if is_numeric_column(observed):
        observed_repr = {repr(float(v)) for v in non_null}
        return {_as_float_repr(v) for v in declared_list}, observed_repr
    return {str(v) for v in declared_list}, {str(v) for v in non_null}


def _as_float_repr(value: str | int | bool) -> str:
    """`repr(float(value))`, or a sentinel when a string does not parse as a number (R1: that
    is a mismatch, not an exception). `int`/`bool`/numeric `str` all parse; only a non-numeric
    `str` reaches the `except`."""
    try:
        return repr(float(value))
    except ValueError:
        return f"__unparseable__{value!r}"


def _check_columns_exist(
    contract: DataContract, columns: set[str]
) -> tuple[list[_Mismatch], set[str]]:
    """C1: every column the contract names must exist. Returns the mismatches plus the set of
    missing column names, so every later check can skip a column C1 already reported."""
    named: list[tuple[str, str]] = [("target.column", contract.target.column)]
    if contract.features.include is not None:
        named.extend(("features.include", c) for c in contract.features.include)
    named.extend(("features.exclude", c) for c in contract.features.exclude)
    named.extend(("features.id_columns", c) for c in contract.features.id_columns)
    named.extend((f"features.roles.{c}", c) for c in contract.features.roles)
    named.extend((f"features.ordinal_levels.{c}", c) for c in contract.features.ordinal_levels)
    for i, goal in enumerate(contract.objective.inference_goals):
        named.extend((f"objective.inference_goals.{i}.predictors", c) for c in goal.predictors)
    if contract.split.time_column is not None:
        named.append(("split.time_column", contract.split.time_column))
    if contract.split.group_column is not None:
        named.append(("split.group_column", contract.split.group_column))
    named.extend(
        (f"preprocessing.missing.per_column.{c}", c)
        for c in contract.preprocessing.missing.per_column
    )

    mismatches: list[_Mismatch] = []
    missing: set[str] = set()
    reported: set[str] = set()
    for field, column in named:
        if column in columns:
            continue
        missing.add(column)
        if column in reported:
            continue
        reported.add(column)
        mismatches.append(_mismatch(field, f"column {column!r} does not exist in the data"))
    return mismatches, missing


def _check_target(
    contract: DataContract, data: pd.DataFrame, *, missing_columns: set[str]
) -> list[_Mismatch]:
    """C2-C8: everything about `target.column`, skipped entirely if C1 already reported it."""
    target = contract.target
    if target.column in missing_columns:
        return []
    series = data[target.column]
    non_null = series.dropna()
    mismatches: list[_Mismatch] = []

    if non_null.empty:  # C2
        mismatches.append(_mismatch("target.column", "target column is entirely null"))
        return mismatches  # nothing else about the target is checkable

    distinct = non_null.unique().tolist()

    if target.type == "binary" and len(set(distinct)) != 2:  # C3
        mismatches.append(
            _mismatch(
                "target.type",
                f"target.type is 'binary' but {len(set(distinct))} distinct non-null "
                f"values were observed",
            )
        )
    if target.type == "multiclass" and len(set(distinct)) < 3:  # C4
        mismatches.append(
            _mismatch(
                "target.type",
                f"target.type is 'multiclass' but only {len(set(distinct))} distinct "
                f"non-null values were observed",
            )
        )
    if target.type in ("continuous", "count") and not pd.api.types.is_numeric_dtype(series):  # C5
        mismatches.append(
            _mismatch(
                "target.type", f"target.type is {target.type!r} but the column is not numeric"
            )
        )
    if target.type == "count" and pd.api.types.is_numeric_dtype(series):  # C6
        negative = bool((non_null < 0).any())
        non_integral = bool(((non_null % 1) != 0).any())
        if negative or non_integral:
            mismatches.append(
                _mismatch(
                    "target.type",
                    "target.type is 'count' but values are not all non-negative integers",
                )
            )
    if target.positive_class is not None:  # C7
        declared, observed = _declared_matches_observed([target.positive_class], series)
        if not declared <= observed:
            mismatches.append(
                _mismatch(
                    "target.positive_class",
                    f"positive_class {target.positive_class!r} does not occur in the target",
                )
            )
    if target.class_order is not None:  # C8
        declared, observed = _declared_matches_observed(target.class_order, series)
        if observed != declared:
            mismatches.append(
                _mismatch(
                    "target.class_order",
                    f"class_order {list(target.class_order)!r} does not match the observed "
                    f"values {sorted({str(v) for v in distinct})!r}",
                )
            )
    return mismatches


def _check_roles(
    contract: DataContract, data: pd.DataFrame, *, missing_columns: set[str]
) -> list[_Mismatch]:
    """C9-C11: declared `features.roles` and `ordinal_levels` against the real columns."""
    mismatches: list[_Mismatch] = []
    for column, role in sorted(contract.features.roles.items()):
        if column in missing_columns:
            continue
        series = data[column]
        if role == "numeric" and not pd.api.types.is_numeric_dtype(series):  # C9
            mismatches.append(
                _mismatch(f"features.roles.{column}", "role is 'numeric' but column is not")
            )
        if role == "datetime":  # C10
            share = iso8601_parse_share(series)
            if share < DATETIME_PARSE_SHARE:
                mismatches.append(
                    _mismatch(
                        f"features.roles.{column}",
                        f"role is 'datetime' but only {share:.0%} of values parse as ISO 8601",
                    )
                )
    for column, levels in sorted(contract.features.ordinal_levels.items()):  # C11
        if column in missing_columns:
            continue
        observed = {str(v) for v in data[column].dropna().unique().tolist()}
        declared = {str(v) for v in levels}
        uncovered = observed - declared
        if uncovered:
            mismatches.append(
                _mismatch(
                    f"features.ordinal_levels.{column}",
                    f"observed values {sorted(uncovered)!r} are not covered by the declared "
                    f"levels {list(levels)!r}",
                )
            )
    return mismatches


def _check_split_time_column(
    contract: DataContract, data: pd.DataFrame, *, missing_columns: set[str]
) -> list[_Mismatch]:
    """C12: `split.time_column`, when set, parses as datetime."""
    column = contract.split.time_column
    if column is None or column in missing_columns:
        return []
    share = iso8601_parse_share(data[column])
    if share < DATETIME_PARSE_SHARE:
        return [_mismatch("split.time_column", f"only {share:.0%} of values parse as ISO 8601")]
    return []


@tool(phase="intake", allow_in=["intake"])
def validate_contract(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to validate, usually `raw`.")],
) -> ToolSuccess:
    """Check the run's data contract against the actual data and report every mismatch.

    Reads the contract from the run's snapshot (`contract.snapshot.json`), not from a
    parameter -- the contract is already on disk by the time this tool runs (D-c), and asking
    the agent to reproduce it in a tool call would be both expensive and a chance to change it.
    A mismatch is a finding, not a failure: this tool always succeeds and lists every mismatch
    it finds, so node 1 sees all of them in one call (D-d). If a column the contract names does
    not exist, that is reported once under C1 and every other check on that column is skipped --
    a missing column must produce one mismatch, not five.
    """
    contract = DataContract.model_validate_json(paths.contract_snapshot_json.read_text())
    data = load_dataframe(paths.artifact(frame, "dataframe"))

    column_mismatches, missing_columns = _check_columns_exist(contract, set(data.columns))
    mismatches: list[_Mismatch] = [
        *column_mismatches,
        *_check_target(contract, data, missing_columns=missing_columns),
        *_check_roles(contract, data, missing_columns=missing_columns),
        *_check_split_time_column(contract, data, missing_columns=missing_columns),
    ]
    summary: dict[str, JsonValue] = {
        "valid": len(mismatches) == 0,
        "mismatches": list(mismatches),
        "checked": len(mismatches),
    }
    return ToolSuccess(summary=summary)


# --- detect_id_columns -----------------------------------------------------------------------


def _id_evidence(column: str, series: pd.Series) -> list[str]:
    evidence: list[str] = []
    lowered = column.lower()
    if lowered == "id" or lowered.endswith(("_id", "id", "uuid")):
        evidence.append("name matches id pattern")
    if pd.api.types.is_integer_dtype(series):
        non_null = series.dropna()
        if len(non_null) > 1 and bool((non_null.diff().dropna() > 0).all()):
            evidence.append("monotonic integer sequence")
    return evidence


@tool(phase="intake", allow_in=["intake"])
def detect_id_columns(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect.")],
) -> ToolSuccess:
    """Flag near-unique columns that identify rows rather than describe them.

    A column is a candidate purely on unique share and dtype; name and monotonicity only add
    evidence, they never decide on their own.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    scored: list[tuple[float, str, list[str]]] = []
    for column in data.columns:
        series = data[column]
        non_null = series.dropna()
        if len(non_null) < MIN_ROWS_FOR_ID:
            continue
        is_candidate_dtype = pd.api.types.is_integer_dtype(series) or pd.api.types.is_string_dtype(
            series
        )
        if not is_candidate_dtype:
            continue
        # Free text is near-unique too, but it describes rows rather than identifying them --
        # infer_column_roles' R8 already calls it `text`. Same length threshold, so the two
        # tools agree. UUIDs (36 chars) stay under it.
        if pd.api.types.is_string_dtype(series) and non_null.str.len().mean() >= TEXT_MEAN_LENGTH:
            continue
        unique_share = non_null.nunique() / len(non_null)
        if unique_share < ID_UNIQUE_SHARE:
            continue
        scored.append((unique_share, str(column), _id_evidence(str(column), series)))
    scored.sort(key=lambda s: (-s[0], s[1]))
    candidates: list[JsonValue] = [
        {"column": column, "unique_share": unique_share, "evidence": list(evidence)}
        for unique_share, column, evidence in scored
    ]
    return ToolSuccess(summary={"candidates": candidates})


# --- detect_leaky_columns --------------------------------------------------------------------


def _rank_correlation(a: pd.Series, b: pd.Series) -> float:
    """P6: the rank form of Spearman correlation, avoiding an implicit scipy import. Returns
    `0.9999999999999999` for identical series, never exactly `1.0` -- callers compare with
    `>=`, never `==`."""
    return float(a.rank().corr(b.rank()))


def _majority_share(series: pd.Series) -> float:
    """Share held by the single most frequent value. Only ever called with a non-empty
    `target_series` (`detect_leaky_columns` returns early for an entirely-null target), so
    `value_counts()` is always non-empty here -- no `empty` guard is needed or reachable."""
    counts = series.value_counts()
    return float(counts.iloc[0] / counts.sum())


def _level_purity(feature: pd.Series, target: pd.Series) -> float | None:
    """L3, non-numeric features only: majority-class share summed over the feature's own
    levels. Numeric features no longer reach this -- they take the rank-AUC path, which does
    not depend on where a class boundary falls between decile edges (R2's amendment), so the
    quantile-binning branch that used to live here is gone.

    Purity is computed over rows whose level AND target are both non-null, and returns `None`
    when fewer than `LEAK_MIN_ROWS` such paired rows remain -- a purity over a handful of rows
    is noise, not evidence.
    """
    frame = pd.DataFrame({"level": feature, "target": target}).dropna()
    if len(frame) < LEAK_MIN_ROWS:
        return None
    majority_per_level = frame.groupby("level", observed=True)["target"].agg(
        lambda s: s.value_counts().iloc[0]
    )
    return float(majority_per_level.sum() / len(frame))


def _rank_auc(feature: pd.Series, target: pd.Series) -> tuple[float, JsonValue] | None:
    """Rank-based one-vs-rest AUC of a numeric feature against a class target, folded to
    `max(auc, 1 - auc)` and maximised over classes (R2's amendment). For a class with `n1`
    paired rows and `n0 = N - n1` rows in the rest,
    `auc = (sum of the class's average ranks - n1*(n1+1)/2) / (n1*n0)`, where ranks come from
    `Series.rank()` (average ties, no scipy). Only classes with at least `LEAK_MIN_CLASS_ROWS`
    rows are scored; returns `None` when none qualifies, otherwise the best folded AUC and the
    class that achieved it (for the evidence string). AUC is insensitive to class balance, so a
    perfect continuous leak scores 1.0 at every split while a random feature sits near 0.5 --
    the majority-share guard the purity path needs is unnecessary here.
    """
    aligned = pd.DataFrame({"f": feature, "t": target}).dropna()
    ranks = aligned["f"].rank()
    total = len(aligned)
    best: tuple[float, JsonValue] | None = None
    for value, group_ranks in ranks.groupby(aligned["t"], observed=True):
        n1 = len(group_ranks)
        n0 = total - n1
        if n1 < LEAK_MIN_CLASS_ROWS or n0 == 0:
            continue
        auc = (float(group_ranks.sum()) - n1 * (n1 + 1) / 2) / (n1 * n0)
        folded = max(auc, 1.0 - auc)
        if best is None or folded > best[0]:
            best = (folded, json_scalar(value))
    return best


@tool(phase="intake", allow_in=["intake"])
def detect_leaky_columns(
    paths: RunPaths,
    frame: Annotated[ArtifactRef, Field(description="The dataframe to inspect.")],
    target: Annotated[str, Field(min_length=1, description="The target column.")],
) -> ToolSuccess:
    """Flag columns that predict the target suspiciously well on their own.

    This is a heuristic, not a verdict: a flagged column is a question for node 1's gate. A
    column that genuinely predicts the outcome is the whole point of modelling, and a leak here
    means one column, alone, nearly determines the target -- usually because it was recorded
    after the outcome. Split leakage (train/test overlap) is `verify_split`'s concern, a
    different check entirely.
    """
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not a column in the data", field_errors={"target": "unknown"}
        )

    target_series = data[target]
    mask = target_series.notna()
    data = data[mask]
    target_series = target_series[mask]

    if target_series.empty:
        # An entirely-null target leaves nothing to compare after dropping null rows -- two
        # empty series compare equal under `.equals()`, which would otherwise flag every other
        # column as "identical to target" vacuously. No data means no finding, not a false one.
        return ToolSuccess(summary={"suspects": []})

    target_as_str = target_series.astype(str)

    n_distinct_target = target_series.nunique()
    is_class_target = n_distinct_target <= CLASS_TARGET_MAX_LEVELS
    majority_share = _majority_share(target_series) if is_class_target else 0.0

    suspects: list[tuple[float, JsonValue]] = []
    for column in data.columns:
        if column == target:
            continue
        series = data[column]
        name_mentions_target = target.lower() in str(column).lower()
        evidence: list[str] = []
        if name_mentions_target:
            evidence.append("column name contains the target's name")  # L4, never alone

        if series.astype(str).equals(target_as_str):  # L1
            suspects.append(
                (
                    1.0,
                    {
                        "column": str(column),
                        "rule": "identical_to_target",
                        "score": 1.0,
                        "evidence": ["identical to target", *evidence],
                    },
                )
            )
            continue

        non_null_feature = series.dropna()
        if len(non_null_feature) == 0:
            continue
        feature_unique_share = non_null_feature.nunique() / len(non_null_feature)

        if is_numeric_column(series) and is_numeric_column(target_series):  # L2
            aligned = pd.DataFrame({"f": series, "t": target_series}).dropna()
            # A rank correlation needs both sides to vary: a constant feature or target has
            # zero rank variance, so `.corr()` divides by zero and returns NaN -- which under
            # `filterwarnings=error` raises a numpy RuntimeWarning rather than scoring. The old
            # code never hit this because L2 ran only against continuous (never constant)
            # targets; now that L2 also runs against class targets and binned-numeric features,
            # a constant column must be skipped here (it is L3's or no rule's concern).
            if len(aligned) >= 2 and aligned["f"].nunique() > 1 and aligned["t"].nunique() > 1:
                score = abs(_rank_correlation(aligned["f"], aligned["t"]))
                if score >= LEAK_CORRELATION:
                    suspects.append(
                        (
                            score,
                            {
                                "column": str(column),
                                "rule": "rank_correlation",
                                "score": score,
                                "evidence": [f"rank correlation {score:.4f}", *evidence],
                            },
                        )
                    )
                    continue

        if is_class_target and is_numeric_column(series):  # L3a: numeric feature, class target
            # Rank AUC, not binned purity: a perfect continuous leak reaches purity 1.0 only
            # when the class boundary lands on a decile edge, so the binned rule missed it at
            # most splits (R2's amendment). AUC is balance-insensitive, so the majority-share
            # guard is not applied here -- `LEAK_MIN_CLASS_ROWS` inside `_rank_auc` is what
            # keeps a tiny class from producing a noisy extreme.
            auc = _rank_auc(series, target_series)
            if auc is not None and auc[0] >= LEAK_AUC:
                score, best_class = auc
                suspects.append(
                    (
                        score,
                        {
                            "column": str(column),
                            "rule": "rank_auc",
                            "score": score,
                            "evidence": [
                                f"rank AUC {score:.4f} for class {best_class}",
                                *evidence,
                            ],
                        },
                    )
                )
            continue

        if is_class_target and majority_share < LEAK_MAJORITY_SHARE_GUARD:  # L3b: non-numeric
            if feature_unique_share > LEAK_UNIQUE_SHARE_SKIP:
                # The skip exists for string IDs, which are trivially 100% pure against any
                # target. Numeric features never reach here -- they take the rank-AUC path
                # above -- so this guards string-shaped features only.
                continue
            purity = _level_purity(series, target_series)
            if purity is not None and purity >= LEAK_PURITY:
                suspects.append(
                    (
                        purity,
                        {
                            "column": str(column),
                            "rule": "level_purity",
                            "score": purity,
                            "evidence": [f"level purity {purity:.4f}", *evidence],
                        },
                    )
                )

    suspects.sort(key=lambda s: -s[0])
    return ToolSuccess(summary={"suspects": [entry for _, entry in suspects]})
