"""The eight encoding tools: `PLAN.md` §6.3's `encoding` phase, this chunk's spec
(`12_tools_encoding.md`). Four `fit_*`/`apply_*` pairs -- one-hot, ordinal, target, scaler --
that complete M4. Node 8 (`feature_engineering`) uses them to build a candidate's predictive
matrix.

Same shape as `cleaning.py` (chunk 11, D-d): fitted state is a frozen dataclass pickled as kind
`transformer`; `apply_*` type-checks the loaded object through `_frame.load_fitted`, so a wrong
ref is `tool_param`, not an `AttributeError`; the target is never transformed (present or absent);
labels and row order are preserved (never `reset_index`); every summary number goes through
`_frame.json_scalar`. The three shared helpers (`require_columns`, `save_frame`, `load_fitted`)
and `as_str` were moved into `_frame.py` for this chunk (§3.1).

Only target encoding uses sklearn (D-e): scaling and ordinal encoding are pure pandas, so their
fitted state is a readable dict. Target encoding is cross-fitted out-of-fold on train (D34c) --
`fit_target_encoder` returns both the encoder and the OOF-encoded train; `apply_target_encoder`
is for valid/test only and refuses any frame whose index intersects train's (D-d). sklearn is
imported lazily, inside the two target-encoder bodies rather than at module load, because it
pulls scipy (an unpinned transitive dependency) and `tools/__init__.py` imports this module at
package init -- chunk 09's spearman tool proves its own path stays scipy-free by importing a
sibling tool module, which runs that `__init__`.

sklearn 1.9 / pandas 3.0 facts this module leans on (verified under `-W error`, §1):
`TargetEncoder(shuffle=, random_state=)` raises a `FutureWarning` (P1), so a seeded
`StratifiedKFold`/`KFold` is passed as `cv` instead; a class with fewer than 5 rows in train
makes `StratifiedKFold` emit a `UserWarning` (-> error) before any `ValueError`, so the count is
checked first and raised as `ToolDataError`. `TargetEncoder.fit_transform` is out-of-fold and
differs from `fit(...).transform(...)` on the same rows (P2). Multiclass gives one output column
per class; an unseen level transforms to the target mean (P3). A `bool` dummy is excluded by
`is_numeric_column` (P4), so the scaler's default selection skips one-hot dummies and chunk 11's
`__outlier` flags with no special case. `TargetEncoder` outputs `float64`; the one-hot dummies are
plain `bool`; the scaler and ordinal encoder write `float64` (an `Int64` input is cast first, P1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal, cast

import numpy as np
import pandas as pd
from pydantic import Field, JsonValue
from statlab_contracts.artifacts import ArtifactMeta, ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths

from statlab_tools.artifacts import load_dataframe, save_model
from statlab_tools.tool import ORCHESTRATOR_SUPPLIED, ToolDataError, ToolParamError, tool
from statlab_tools.tools._frame import (
    as_str,
    is_class_target,
    is_numeric_column,
    json_list,
    json_scalar,
    load_fitted,
    observed_counts,
    require_columns,
    save_frame,
)

_FIT_PHASES = ("feature_engineering",)
_APPLY_PHASES = ("feature_engineering", "champion_selection")

_Train = Annotated[ArtifactRef, Field(description="The train split frame; never test-derived.")]
_Frame = Annotated[ArtifactRef, Field(description="The dataframe to transform.")]
_Target = Annotated[str, Field(min_length=1, description="The target column; never transformed.")]

_FOLDS = 5
"""The cross-fitting fold count for target encoding (D34c). A constant, not a parameter: five is
sklearn's own `TargetEncoder` default, and the min-class guard below is written against it."""

_NO_LEVEL_ORDER: dict[str, tuple[str, ...]] = {}
"""Shared empty default for `fit_ordinal_encoder`'s `level_order`. Never mutated -- read-only, so
one shared instance is safe (chunk 11's `_NO_PER_COLUMN` convention). A named constant, not a `{}`
literal default (ruff B006)."""


# --- fitted state --------------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedOneHot:
    """Observed train levels per source column (`str`-sorted). `apply` builds one `bool` dummy per
    level named `f"{col}__{level}"`; an unseen or null value is an all-`False` row (D-a, D-b)."""

    target: str
    levels: dict[str, tuple[object, ...]]


@dataclass(frozen=True)
class FittedOrdinal:
    """A level->rank map per column. `apply` writes `float64` ranks `0..k-1`; an unseen level or a
    null becomes NaN, counted (D-a). Keys are the string form of each level (`as_str`), so an
    integral float `1.0` matches a declared `"1"` (chunk 11's P3)."""

    target: str
    ranks: dict[str, dict[str, int]]


@dataclass(frozen=True)
class FittedTargetEncoder:
    """The fitted sklearn `TargetEncoder` plus everything `apply` needs without reaching into it.

    `encoder` is typed `object`, not `TargetEncoder`: sklearn is untyped here (an implicit
    dynamic type via `ignore_missing_imports`), and storing it as `object` keeps that dynamic
    type from leaking into the module's typed surface -- it is narrowed with an `isinstance` at
    use. `train_labels` is train's index, so `apply` can refuse an in-sample frame (D-d). Frozen
    and plain so joblib round-trips it and chunk 20 reads it without un-pickling bespoke classes.
    """

    target: str
    columns: tuple[str, ...]
    encoder: object
    output_columns: tuple[str, ...]
    train_labels: pd.Index


@dataclass(frozen=True)
class FittedScaler:
    """A `(center, scale)` pair per numeric feature column, and the method they were computed for.
    A column whose `scale` is zero is never fitted (skipped with a warning), so every entry has a
    non-zero `scale`. `apply` writes `(x - center) / scale` as `float64`."""

    target: str
    method: str
    params: dict[str, tuple[float, float]]


# --- shared ---------------------------------------------------------------------------------------


def _feature_columns(train: pd.DataFrame, target: str) -> list[str]:
    """Every column except `target` (D-e). The target need not be present."""
    return [col for col in train.columns if col != target]


def _reject_target_in_columns(columns: tuple[str, ...], target: str) -> None:
    """Naming the target in `columns` is `tool_param` (D-f): the target is never encoded."""
    if target in columns:
        raise ToolParamError(
            f"columns name the target {target!r}, which is never encoded",
            field_errors={"columns": f"must not include the target {target!r}"},
        )


def _save_transformer(paths: RunPaths, obj: object, label: str) -> ArtifactMeta:
    ref = new_artifact_ref()
    return save_model(
        paths.artifact(ref, "transformer"), obj, ref=ref, kind="transformer", label=label
    )


def _check_fitted_columns(
    frame: pd.DataFrame, fitted_columns: tuple[str, ...], target: str
) -> None:
    """Every fitted source column must be present (§3). Other columns pass through untouched, and
    the target may be present or absent -- so this only checks for *missing* fitted columns, unlike
    `apply_imputer`'s stricter exact-match (an encoder replaces only its own columns)."""
    missing = [col for col in fitted_columns if col not in frame.columns and col != target]
    if missing:
        raise ToolParamError(
            f"frame is missing fitted columns {missing!r}",
            field_errors={"frame": f"missing fitted columns: {missing!r}"},
        )


def _replace_columns(
    frame: pd.DataFrame, sources: tuple[str, ...], new: pd.DataFrame
) -> pd.DataFrame:
    """Drop `sources` and insert `new`'s columns at the leftmost source position, preserving the
    frame's index and the position of untouched columns. `new` is already indexed like `frame`;
    its values go in as a numpy array so the insert does not realign (D-b, target-encoder output).
    """
    position = min(list(frame.columns).index(col) for col in sources)
    out = frame.drop(columns=list(sources)).copy()
    for offset, name in enumerate(new.columns):
        out.insert(min(position + offset, len(out.columns)), name, new[name].to_numpy())
    return out


# --- 3.2 one-hot ([v1]) --------------------------------------------------------------------------


@tool(phase="encoding", allow_in=_FIT_PHASES)
def fit_onehot_encoder(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Columns to encode; None selects every low-cardinality categorical."),
    ] = None,
    max_cardinality: Annotated[
        int,
        Field(
            ge=2,
            description="A column with more observed train levels than this is refused/skipped.",
        ),
    ] = 30,
) -> ToolSuccess:
    """Learn each named column's observed train levels (D-b). `apply_onehot_encoder` replaces every
    source column with one `bool` dummy per level, named `f"{col}__{level}"`; nothing is dropped,
    so chunk 15 builds the inferential matrix with its own reference level. An unseen level or a
    null at apply is an all-`False` row (D-a).

    With `columns=None`, every non-numeric, non-bool, non-datetime feature with at most
    `max_cardinality` observed levels is selected; higher-cardinality ones are listed in
    `skipped_high_cardinality` with a warning (D-c). An explicit column above `max_cardinality` is
    an error that says to target-encode it instead. A generated dummy name colliding with an
    existing column is an error (D-b).
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    warnings_out: list[str] = []

    if columns is not None:
        require_columns(data, columns, "columns")
        _reject_target_in_columns(columns, target)
        over = [col for col in columns if len(observed_counts(data[col])) > max_cardinality]
        if over:
            raise ToolParamError(
                f"columns {over!r} have more than max_cardinality={max_cardinality} levels; "
                f"target-encode them instead",
                field_errors={"columns": f"above max_cardinality; target-encode: {over!r}"},
            )
        selected = list(columns)
        skipped: dict[str, int] = {}
    else:
        selected = []
        skipped = {}
        for col in _feature_columns(data, target):
            if not _is_onehot_candidate(data[col]):
                continue
            n_levels = len(observed_counts(data[col]))
            if n_levels > max_cardinality:
                skipped[col] = n_levels
                warnings_out.append(
                    f"{col!r} has {n_levels} levels (> max_cardinality={max_cardinality}); "
                    f"skipped -- target-encode it instead"
                )
            else:
                selected.append(col)

    levels: dict[str, tuple[object, ...]] = {}
    output_names: list[str] = []
    for col in selected:
        col_levels = tuple(sorted(observed_counts(data[col]).index, key=str))
        levels[col] = col_levels
        output_names.extend(f"{col}__{level}" for level in col_levels)

    existing = set(data.columns)
    collisions = [name for name in output_names if name in existing and name not in selected]
    if collisions:
        raise ToolParamError(
            f"generated dummy names collide with existing columns: {collisions!r}",
            field_errors={"columns": f"dummy-name collision: {collisions!r}"},
        )

    encoder = FittedOneHot(target=target, levels=levels)
    meta = _save_transformer(paths, encoder, "onehot_encoder")
    summary: dict[str, JsonValue] = {
        "columns": {col: json_scalar(len(levels[col])) for col in selected},
        "skipped_high_cardinality": {col: json_scalar(n) for col, n in skipped.items()},
        "output_columns": json_scalar(len(output_names)),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,), warnings=tuple(warnings_out))


def _is_onehot_candidate(series: pd.Series) -> bool:
    """A default-selected one-hot column: not numeric, not bool, not datetime (D-c, P4)."""
    if is_numeric_column(series) or pd.api.types.is_bool_dtype(series):
        return False
    return not bool(pd.api.types.is_datetime64_any_dtype(series))


@tool(phase="encoding", allow_in=_APPLY_PHASES)
def apply_onehot_encoder(
    paths: RunPaths,
    encoder: Annotated[ArtifactRef, Field(description="A fitted encoder from fit_onehot_encoder.")],
    frame: _Frame,
) -> ToolSuccess:
    """Replace each fitted source column with its `bool` dummies, inserted at the source column's
    position. The levels come only from the encoder (learned on train) -- nothing is relearned
    here, which is what keeps the fit leak-free. An unseen level or a null is an all-`False` row,
    counted in `unseen`/`null` (D-a). Works whether the target is present or absent (D-e).
    """
    fitted = load_fitted(paths, encoder, FittedOneHot, "encoder")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    _check_fitted_columns(data, tuple(fitted.levels), fitted.target)

    unseen: dict[str, int] = {}
    null: dict[str, int] = {}
    pieces: list[pd.DataFrame | pd.Series] = []
    for col in data.columns:
        if col not in fitted.levels:
            pieces.append(data[col])
            continue
        source = data[col]
        stringified = as_str(source)
        level_strs = {str(level): str(level) for level in fitted.levels[col]}
        n_null = int(source.isna().sum())
        present = stringified[source.notna()]
        n_unseen = int((~present.isin(set(level_strs))).sum())
        if n_null:
            null[col] = n_null
        if n_unseen:
            unseen[col] = n_unseen
        for level in fitted.levels[col]:
            name = f"{col}__{level}"
            match = (stringified == str(level)) & source.notna()
            pieces.append(pd.Series(match.to_numpy(), index=data.index, name=name, dtype=bool))

    out = pd.concat(pieces, axis=1)
    summary: dict[str, JsonValue] = {
        "unseen": {col: json_scalar(n) for col, n in unseen.items()},
        "null": {col: json_scalar(n) for col, n in null.items()},
    }
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, out, "onehot_encoded"),))


# --- 3.3 ordinal ---------------------------------------------------------------------------------


@tool(phase="encoding", allow_in=_FIT_PHASES)
def fit_ordinal_encoder(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    columns: Annotated[
        tuple[str, ...], Field(min_length=1, description="Columns to ordinal-encode.")
    ],
    level_order: Annotated[
        dict[str, tuple[str, ...]],
        Field(description="Explicit order per column; else a Categorical's or str-sorted order."),
    ] = _NO_LEVEL_ORDER,
) -> ToolSuccess:
    """Learn a level->rank map per column (D-e). The order is `level_order[col]` if given; else an
    ordered `Categorical`'s categories (chunk 11's `cast_dtypes` produces these); else the observed
    train levels `sorted(key=str)` -- an arbitrary order meaningful only for tree models, as the
    summary notes. Level comparison uses the string form (`as_str`), so an integral float `1.0`
    matches a declared `"1"`, not `"1.0"` (chunk 11's P3 addendum).

    A given `level_order` missing an observed train level is an error on `level_order`: an
    incomplete order would silently NaN real data. `apply_ordinal_encoder` writes `float64` ranks;
    an unseen level or a null becomes NaN, counted (D-a).
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    require_columns(data, columns, "columns")
    _reject_target_in_columns(columns, target)

    ranks: dict[str, dict[str, int]] = {}
    for col in columns:
        observed = {str(level) for level in as_str(data[col]).dropna().unique()}
        dtype = data[col].dtype
        if col in level_order:
            order = level_order[col]
            missing = sorted(observed - set(order))
            if missing:
                raise ToolParamError(
                    f"level_order[{col!r}] omits observed train levels {missing!r}; "
                    f"an incomplete order would silently null real data",
                    field_errors={"level_order": f"{col!r} omits observed levels: {missing!r}"},
                )
        elif isinstance(dtype, pd.CategoricalDtype) and dtype.ordered:
            order = tuple(str(c) for c in dtype.categories)
        else:
            order = tuple(sorted(observed, key=str))
        ranks[col] = {level: rank for rank, level in enumerate(order)}

    encoder = FittedOrdinal(target=target, ranks=ranks)
    meta = _save_transformer(paths, encoder, "ordinal_encoder")
    summary: dict[str, JsonValue] = {
        "ranks": {
            col: {level: json_scalar(r) for level, r in ranks[col].items()} for col in columns
        }
    }
    return ToolSuccess(summary=summary, artifacts=(meta,))


@tool(phase="encoding", allow_in=_APPLY_PHASES)
def apply_ordinal_encoder(
    paths: RunPaths,
    encoder: Annotated[
        ArtifactRef, Field(description="A fitted encoder from fit_ordinal_encoder.")
    ],
    frame: _Frame,
) -> ToolSuccess:
    """Write each fitted column's `float64` ranks `0..k-1` in place. The ranks come only from the
    encoder (learned on train). A null stays NaN; an unseen level becomes NaN and is counted (D-a).
    Works whether the target is present or absent (D-e).
    """
    fitted = load_fitted(paths, encoder, FittedOrdinal, "encoder")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    _check_fitted_columns(data, tuple(fitted.ranks), fitted.target)

    unseen: dict[str, int] = {}
    for col in fitted.ranks:
        source = data[col]
        stringified = as_str(source)
        mapped = stringified.map(fitted.ranks[col]).astype("float64")
        n_unseen = int((source.notna() & mapped.isna()).sum())
        if n_unseen:
            unseen[col] = n_unseen
        data[col] = mapped

    summary: dict[str, JsonValue] = {"unseen": {col: json_scalar(n) for col, n in unseen.items()}}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, data, "ordinal_encoded"),))


# --- 3.4 target (D34c) ---------------------------------------------------------------------------


@tool(phase="encoding", allow_in=_FIT_PHASES)
def fit_target_encoder(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    columns: Annotated[
        tuple[str, ...], Field(min_length=1, description="Columns to target-encode.")
    ],
    seed: Annotated[int, Field(ge=0, description=ORCHESTRATOR_SUPPLIED)],
    smoothing: Annotated[
        float | Literal["auto"],
        Field(description="sklearn's `smooth`; a float takes ge=0, else 'auto'."),
    ] = "auto",
) -> ToolSuccess:
    """Cross-fit a `TargetEncoder` out-of-fold on train (D34c), returning both the encoder and the
    OOF-encoded train. The out-of-fold encoding is what prevents the in-sample leak that applying
    a full-data encoder to its own train rows would be (D-d).

    The splitter is a seeded `StratifiedKFold` for a class target and `KFold` otherwise, passed as
    `cv` -- `TargetEncoder`'s own `shuffle`/`random_state` are deprecated in sklearn 1.9 (P1). A
    class with fewer than `_FOLDS` rows is a `ToolDataError`, checked before the fit so sklearn's
    own `UserWarning`/`ValueError` never surfaces as a `bug`. Output columns are `f"{col}__te"`, or
    `f"{col}__te_{class}"` for a multiclass target (P3), replacing the source columns.
    """
    # sklearn is imported lazily inside the two target-encoder bodies, never at module load: it
    # pulls scipy (a transitive, unpinned dependency), and chunk 09's spearman path proves it
    # stays scipy-free by importing a sibling tool module -- which runs this package's __init__.
    from sklearn.model_selection import KFold, StratifiedKFold
    from sklearn.preprocessing import TargetEncoder

    data = load_dataframe(paths.artifact(train, "dataframe"))
    require_columns(data, columns, "columns")
    _reject_target_in_columns(columns, target)
    if target not in data.columns:
        raise ToolParamError(
            f"target {target!r} is not in train; target encoding needs it",
            field_errors={"target": f"not a column in train: {target!r}"},
        )
    if isinstance(smoothing, float) and smoothing < 0:
        raise ToolParamError(
            "smoothing must be >= 0 or 'auto'",
            field_errors={"smoothing": "a float smoothing must be >= 0"},
        )

    y = data[target]
    class_target = is_class_target(y)
    n_classes = int(y.nunique())
    if class_target:
        target_type = "binary" if n_classes == 2 else "multiclass"
        smallest = int(observed_counts(y).min())
        if smallest < _FOLDS:
            raise ToolDataError(
                f"the smallest target class has {smallest} rows in train, fewer than the "
                f"{_FOLDS} cross-fitting folds; target encoding cannot cross-fit it"
            )
        splitter: object = StratifiedKFold(_FOLDS, shuffle=True, random_state=seed)
    else:
        target_type = "continuous"
        splitter = KFold(_FOLDS, shuffle=True, random_state=seed)

    x = data[list(columns)].apply(lambda s: as_str(s) if not is_numeric_column(s) else s)
    encoder = TargetEncoder(target_type=target_type, smooth=smoothing, cv=splitter)
    oof = np.asarray(encoder.fit_transform(x, y), dtype="float64")

    if target_type == "multiclass":
        classes = [str(c) for c in encoder.classes_]
        output_columns = tuple(f"{col}__te_{cls}" for col in columns for cls in classes)
    else:
        output_columns = tuple(f"{col}__te" for col in columns)

    oof_frame = pd.DataFrame(oof, columns=list(output_columns), index=data.index)
    encoded = _replace_columns(data, tuple(columns), oof_frame)

    fitted = FittedTargetEncoder(
        target=target,
        columns=tuple(columns),
        encoder=encoder,
        output_columns=output_columns,
        train_labels=data.index,
    )
    enc_meta = _save_transformer(paths, fitted, "target_encoder")
    summary: dict[str, JsonValue] = {
        "target_type": target_type,
        "columns": json_list(columns),
        "output_columns": json_list(output_columns),
        "smoothing": smoothing,
        "folds": json_scalar(_FOLDS),
    }
    return ToolSuccess(
        summary=summary,
        artifacts=(enc_meta, save_frame(paths, encoded, "target_encoded_train")),
    )


@tool(phase="encoding", allow_in=_APPLY_PHASES)
def apply_target_encoder(
    paths: RunPaths,
    encoder: Annotated[ArtifactRef, Field(description="A fitted encoder from fit_target_encoder.")],
    frame: _Frame,
) -> ToolSuccess:
    """Encode a valid/test frame with the full-data `TargetEncoder` (D-d: never train). A frame
    whose index intersects train's labels is refused as `tool_param` -- applying the full encoder
    to train rows is exactly the in-sample leak the out-of-fold fit prevents, and the splits are
    disjoint by label (chunk 10 keeps labels, `verify_split` proves disjointness). An unseen level
    transforms to the target mean (sklearn's own behaviour, P3), counted per column.
    """
    fitted = load_fitted(paths, encoder, FittedTargetEncoder, "encoder")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    _check_fitted_columns(data, fitted.columns, fitted.target)

    from sklearn.preprocessing import TargetEncoder  # lazy: see fit_target_encoder (scipy)

    overlap = data.index.intersection(fitted.train_labels)
    if len(overlap) > 0:
        raise ToolParamError(
            f"{len(overlap)} frame rows share a label with the encoder's train; apply is for "
            f"valid/test only -- use fit_target_encoder's target_encoded_train for train (D-d)",
            field_errors={"frame": f"{len(overlap)} rows overlap train labels"},
        )

    # `fitted` is a FittedTargetEncoder (load_fitted checked), and that dataclass's `encoder` is
    # only ever a fitted TargetEncoder (fit_target_encoder is its sole constructor), so narrowing
    # the `object` field to the sklearn type is a cast, not a runtime check -- an isinstance guard
    # here would be an unreachable branch (the project's no-dead-code rule).
    model = cast(TargetEncoder, fitted.encoder)
    known = {
        col: {str(level) for level in cats}
        for col, cats in zip(fitted.columns, model.categories_, strict=True)
    }
    x = data[list(fitted.columns)].apply(lambda s: as_str(s) if not is_numeric_column(s) else s)
    transformed = np.asarray(model.transform(x), dtype="float64")

    unseen: dict[str, int] = {}
    for col in fitted.columns:
        stringified = as_str(data[col])
        n_unseen = int((data[col].notna() & ~stringified.isin(known[col])).sum())
        if n_unseen:
            unseen[col] = n_unseen

    enc_frame = pd.DataFrame(transformed, columns=list(fitted.output_columns), index=data.index)
    out = _replace_columns(data, fitted.columns, enc_frame)

    summary: dict[str, JsonValue] = {"unseen": {col: json_scalar(n) for col, n in unseen.items()}}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, out, "target_encoded"),))


# --- 3.5 scaler ([v1]) ---------------------------------------------------------------------------


@tool(phase="encoding", allow_in=_FIT_PHASES)
def fit_scaler(
    paths: RunPaths,
    train: _Train,
    target: _Target,
    method: Annotated[
        Literal["standard", "minmax", "robust"], Field(description="How center/scale are computed.")
    ],
    columns: Annotated[
        tuple[str, ...] | None,
        Field(description="Numeric feature columns; None selects every numeric feature."),
    ] = None,
) -> ToolSuccess:
    """Learn a `(center, scale)` pair per numeric feature column from train (D-e). `standard` uses
    mean and population std (`ddof=0`); `minmax` uses min and `max-min`; `robust` uses median and
    IQR. A column whose `scale` would be zero is skipped with a warning (chunk 09's P6: dividing by
    zero spread is meaningless). With `columns=None` every `is_numeric_column` feature is selected,
    which skips bool dummies and chunk 11's `__outlier` flags (P4). An explicit non-numeric column
    is an error. `apply_scaler` writes `(x - center) / scale` as `float64`.
    """
    data = load_dataframe(paths.artifact(train, "dataframe"))
    if columns is not None:
        require_columns(data, columns, "columns")
        _reject_target_in_columns(columns, target)
        non_numeric = [col for col in columns if not is_numeric_column(data[col])]
        if non_numeric:
            raise ToolParamError(
                f"columns {non_numeric!r} are not numeric; the scaler needs numeric features",
                field_errors={"columns": f"must be numeric: {non_numeric!r}"},
            )
        candidates = list(columns)
    else:
        candidates = [col for col in _feature_columns(data, target) if is_numeric_column(data[col])]

    warnings_out: list[str] = []
    params: dict[str, tuple[float, float]] = {}
    skipped: list[str] = []
    for col in candidates:
        values = data[col].dropna().astype("float64")
        if method == "standard":
            center, scale = float(values.mean()), float(values.std(ddof=0))
        elif method == "minmax":
            center, scale = float(values.min()), float(values.max() - values.min())
        else:  # robust
            center = float(values.median())
            scale = float(values.quantile(0.75) - values.quantile(0.25))
        if scale == 0:
            skipped.append(col)
            warnings_out.append(f"{col!r} has zero spread under {method!r}; skipped")
            continue
        params[col] = (center, scale)

    scaler = FittedScaler(target=target, method=method, params=params)
    meta = _save_transformer(paths, scaler, "scaler")
    summary: dict[str, JsonValue] = {
        "method": method,
        "params": {col: [json_scalar(c), json_scalar(s)] for col, (c, s) in params.items()},
        "skipped": json_list(skipped),
    }
    return ToolSuccess(summary=summary, artifacts=(meta,), warnings=tuple(warnings_out))


@tool(phase="encoding", allow_in=_APPLY_PHASES)
def apply_scaler(
    paths: RunPaths,
    scaler: Annotated[ArtifactRef, Field(description="A fitted scaler from fit_scaler.")],
    frame: _Frame,
) -> ToolSuccess:
    """Write `(x - center) / scale` as `float64` for each fitted column, using only the scaler's
    learned parameters (nothing is recomputed here). An `Int64` input is cast to `float64` first so
    it does not crash (chunk 11's P1); a null stays NaN. Works whether the target is present or
    absent (D-e).
    """
    fitted = load_fitted(paths, scaler, FittedScaler, "scaler")
    data = load_dataframe(paths.artifact(frame, "dataframe"))
    _check_fitted_columns(data, tuple(fitted.params), fitted.target)

    for col, (center, scale) in fitted.params.items():
        data[col] = (data[col].astype("float64") - center) / scale

    summary: dict[str, JsonValue] = {"scaled": json_list(fitted.params)}
    return ToolSuccess(summary=summary, artifacts=(save_frame(paths, data, "scaled"),))
