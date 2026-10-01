"""Content IO: parquet, joblib, png, json, text. The only module in the project that imports
pandas — tools produce and consume content; the backend never opens a frame.

pandas 3.0 facts (verified, `00_index.md`): string columns are dtype `str`, not `object`;
datetimes default to `datetime64[us]`. Neither matters for this module's own code, since it
does no dtype inspection or comparison — parquet round-trips all seven tested dtypes (`str`,
`category`, `int64`, `float64`, `bool`, `datetime64[us]`, `Int64`) unchanged, measured before
writing this module. No dtype preservation or restoration logic is added because none is
needed; see test A1.

No atomic temp-file-then-`os.replace` write. Every tool call mints a fresh artifact id, so a
truncated file from a crashed tool call is never registered and never read — its id never
enters the registry, so the worst case is an orphan file on disk. Atomicity would guard a path
that cannot be taken.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import pandas as pd
import pyarrow
from matplotlib.figure import Figure
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactKind, ArtifactMeta, ArtifactRef


class ToolArtifactError(Exception):
    """Content IO failure in the MCP server process.

    Deliberately NOT `statlab.errors.ArtifactError`: importing the backend package here would
    couple two processes that must stay independently deployable (D13), same trade as
    `ToolSettingsError` in `settings.py`.
    """

    def __init__(self, path: str, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        if self.cause is None:
            return f"{self.path}: {self.message}"
        return f"{self.path}: {self.message} (caused by {type(self.cause).__name__}: {self.cause})"


def _check_ref_matches_path(path: Path, ref: ArtifactRef) -> None:
    """Every `save_*` writes to a path whose stem must be `ref` -- one guard where the two
    facts meet, rather than trust spread across every call site (chunk 06 §7.4)."""
    if path.stem != ref:
        raise ToolArtifactError(str(path), f"path stem {path.stem!r} does not match ref {ref!r}")


def save_dataframe(
    path: Path,
    frame: pd.DataFrame,
    *,
    ref: ArtifactRef,
    kind: ArtifactKind = "dataframe",
    label: str | None = None,
) -> ArtifactMeta:
    """Write `frame` as parquet. Returns the `ArtifactMeta` describing it."""
    _check_ref_matches_path(path, ref)
    try:
        frame.to_parquet(path)
    except (OSError, pyarrow.ArrowException) as exc:
        raise ToolArtifactError(str(path), f"cannot write dataframe: {exc}", cause=exc) from exc
    rows, cols = frame.shape
    return ArtifactMeta(ref=ref, kind=kind, rows=rows, cols=cols, label=label)


def load_dataframe(path: Path) -> pd.DataFrame:
    """Read a parquet file back into a `DataFrame`."""
    try:
        return pd.read_parquet(path)
    except FileNotFoundError as exc:
        raise ToolArtifactError(str(path), "file not found", cause=exc) from exc
    except (OSError, pyarrow.ArrowException) as exc:
        raise ToolArtifactError(str(path), f"cannot read dataframe: {exc}", cause=exc) from exc


def save_model(
    path: Path, obj: object, *, ref: ArtifactRef, kind: ArtifactKind, label: str | None = None
) -> ArtifactMeta:
    """Write `obj` (a fitted transformer, estimator, or inferential-fit result) via joblib."""
    _check_ref_matches_path(path, ref)
    try:
        joblib.dump(obj, path)
    except OSError as exc:
        raise ToolArtifactError(str(path), f"cannot write model: {exc}", cause=exc) from exc
    return ArtifactMeta(ref=ref, kind=kind, label=label)


def load_model(path: Path) -> object:
    """Read a joblib-serialised object back."""
    try:
        return joblib.load(path)
    except FileNotFoundError as exc:
        raise ToolArtifactError(str(path), "file not found", cause=exc) from exc
    except OSError as exc:
        raise ToolArtifactError(str(path), f"cannot read model: {exc}", cause=exc) from exc
    # joblib/pickle raise varied undocumented types on corrupt input; must not leak any raw.
    except Exception as exc:
        raise ToolArtifactError(str(path), f"cannot read model: {exc}", cause=exc) from exc


def save_plot(
    path: Path, figure: Figure, *, ref: ArtifactRef, label: str | None = None
) -> ArtifactMeta:
    """Write `figure` as PNG.

    No `plt.close()`: this chunk's tools build `matplotlib.figure.Figure()` directly rather
    than through `pyplot`, so the figure was never registered in pyplot's global figure
    registry and there is nothing to close. `pyplot`'s registry is process-global and not
    thread-safe, and tool bodies run concurrently in worker threads since `c5f23d3` -- touching
    it here would reintroduce the exact hazard the direct-`Figure()` convention exists to avoid,
    even though this function itself only ever receives an already-built figure.
    """
    _check_ref_matches_path(path, ref)
    try:
        figure.savefig(path, format="png")
    except OSError as exc:
        raise ToolArtifactError(str(path), f"cannot write plot: {exc}", cause=exc) from exc
    return ArtifactMeta(ref=ref, kind="plot", label=label)


def save_json(
    path: Path,
    payload: JsonValue,
    *,
    ref: ArtifactRef,
    kind: ArtifactKind,
    label: str | None = None,
) -> ArtifactMeta:
    """Write `payload` as strict JSON.

    `allow_nan=False`: `JsonValue` validation accepts `NaN`/`inf` (they are valid Python
    floats), but the default `json.dumps` then writes them as the bare tokens `NaN`/`Infinity`,
    which are not valid JSON and a strict parser -- including a browser's `JSON.parse` -- would
    reject (P3). A `ValueError` here means some upstream value should have gone through
    `_frame.json_scalar` (which maps non-finite floats to `None`) and did not; that is a bug in
    the caller, not something this function should paper over by writing invalid JSON.
    """
    _check_ref_matches_path(path, ref)
    try:
        text = json.dumps(payload, allow_nan=False)
    except ValueError as exc:
        raise ToolArtifactError(str(path), f"payload is not strict JSON: {exc}", cause=exc) from exc
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ToolArtifactError(str(path), f"cannot write json: {exc}", cause=exc) from exc
    return ArtifactMeta(ref=ref, kind=kind, label=label)


def load_json(path: Path) -> JsonValue:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ToolArtifactError(str(path), "file not found", cause=exc) from exc
    except OSError as exc:
        raise ToolArtifactError(str(path), f"cannot read json: {exc}", cause=exc) from exc
    try:
        result: JsonValue = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolArtifactError(str(path), f"cannot parse json: {exc}", cause=exc) from exc
    return result


def save_text(path: Path, text: str, *, ref: ArtifactRef, label: str | None = None) -> ArtifactMeta:
    _check_ref_matches_path(path, ref)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ToolArtifactError(str(path), f"cannot write text: {exc}", cause=exc) from exc
    return ArtifactMeta(ref=ref, kind="report", label=label)
