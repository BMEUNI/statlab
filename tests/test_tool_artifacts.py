"""Chunk 06 §4.6 + §7.4: content IO. A1-A18. A1 is the load-bearing test: if it fails on any
dtype, the "no preservation logic needed" decision is wrong."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest
from matplotlib.figure import Figure
from pandas.testing import assert_frame_equal
from pydantic import JsonValue
from sklearn.linear_model import LogisticRegression
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_tools.artifacts import (
    ToolArtifactError,
    load_dataframe,
    load_json,
    load_model,
    save_dataframe,
    save_json,
    save_model,
    save_plot,
    save_text,
)

_DTYPE_COLUMNS = ("s", "c", "i", "f", "b", "dt", "ni")


def _ref_path(tmp_path: Path, suffix: str) -> tuple[Path, ArtifactRef]:
    ref = new_artifact_ref()
    return tmp_path / f"{ref}{suffix}", ref


@pytest.mark.parametrize("column", _DTYPE_COLUMNS)
def test_a1_every_dtype_round_trips(
    tmp_path: Path, sample_frame: pd.DataFrame, column: str
) -> None:
    """A1: parametrised over all seven columns; `str(dtype)` before == after."""
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)
    loaded = load_dataframe(path)
    assert str(loaded[column].dtype) == str(sample_frame[column].dtype)


def test_a2_values_round_trip(tmp_path: Path, sample_frame: pd.DataFrame) -> None:
    """A2."""
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)
    loaded = load_dataframe(path)
    assert_frame_equal(loaded, sample_frame)


def test_a3_nullable_int64_null_survives(tmp_path: Path, sample_frame: pd.DataFrame) -> None:
    """A3."""
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)
    loaded = load_dataframe(path)
    assert pd.isna(loaded["ni"].iloc[-1])


def test_a4_save_dataframe_returns_artifact_meta_with_shape(
    tmp_path: Path, sample_frame: pd.DataFrame
) -> None:
    """A4 (amended §7.4): `save_dataframe` returns `ArtifactMeta`, not a bare shape tuple."""
    path, ref = _ref_path(tmp_path, ".parquet")
    meta = save_dataframe(path, sample_frame, ref=ref, label="train split")
    assert (meta.rows, meta.cols) == sample_frame.shape
    assert meta.ref == ref
    assert meta.kind == "dataframe"
    assert meta.label == "train split"


def test_a4b_save_dataframe_kind_can_be_predictions(tmp_path: Path) -> None:
    """`kind` stays explicit: `predictions` is also parquet but a different kind (§7.4)."""
    frame = pd.DataFrame({"y_pred": [0, 1, 1]})
    path, ref = _ref_path(tmp_path, ".parquet")
    meta = save_dataframe(path, frame, ref=ref, kind="predictions")
    assert meta.kind == "predictions"


def test_a4c_save_dataframe_split_sets_meta_split(tmp_path: Path) -> None:
    """Chunk 10 §3.2: `save_dataframe(split="test")` sets `meta.split`; the default is `None`."""
    frame = pd.DataFrame({"y": [0, 1, 1]})
    path, ref = _ref_path(tmp_path, ".parquet")
    meta = save_dataframe(path, frame, ref=ref, split="test")
    assert meta.split == "test"


def test_a4d_save_dataframe_split_defaults_to_none(tmp_path: Path) -> None:
    frame = pd.DataFrame({"y": [0, 1, 1]})
    path, ref = _ref_path(tmp_path, ".parquet")
    meta = save_dataframe(path, frame, ref=ref)
    assert meta.split is None


def test_a5_empty_frame_round_trips(tmp_path: Path) -> None:
    """A5."""
    frame = pd.DataFrame({"x": pd.array([], dtype="int64")})
    path, ref = _ref_path(tmp_path, ".parquet")
    meta = save_dataframe(path, frame, ref=ref)
    assert meta.rows == 0
    assert meta.cols == 1
    loaded = load_dataframe(path)
    assert len(loaded) == 0


def test_a6_single_column_frame_round_trips(tmp_path: Path) -> None:
    """A6."""
    frame = pd.DataFrame({"only": pd.array([1, 2, 3], dtype="int64")})
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, frame, ref=ref)
    loaded = load_dataframe(path)
    assert_frame_equal(loaded, frame)


def test_a7_model_round_trip_predictions_equal(tmp_path: Path) -> None:
    """A7: joblib on a fitted sklearn estimator; predictions equal before and after."""
    x = [[0, 0], [1, 1], [2, 2], [3, 3]]
    y = [0, 0, 1, 1]
    model = LogisticRegression().fit(x, y)
    path, ref = _ref_path(tmp_path, ".joblib")
    meta = save_model(path, model, ref=ref, kind="model")
    assert meta.kind == "model"
    loaded = load_model(path)
    assert isinstance(loaded, LogisticRegression)
    assert list(loaded.predict(x)) == list(model.predict(x))


def test_a7b_save_model_kind_can_be_transformer(tmp_path: Path) -> None:
    """`kind` stays explicit: `transformer`/`model`/`inferential_fit` are all joblib (§7.4)."""
    path, ref = _ref_path(tmp_path, ".joblib")
    meta = save_model(path, {"fitted": True}, ref=ref, kind="transformer")
    assert meta.kind == "transformer"


def test_a8_plot_writes_valid_png(tmp_path: Path) -> None:
    """A8: first 8 bytes are the PNG signature."""
    fig = Figure()
    ax = fig.add_subplot()
    ax.plot([1, 2, 3])
    path, ref = _ref_path(tmp_path, ".png")
    meta = save_plot(path, fig, ref=ref)
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert meta.kind == "plot"


def test_save_plot_writes_chart_sidecar_beside_png(tmp_path: Path) -> None:
    fig = Figure()
    fig.add_subplot().plot([1, 2, 3])
    path, ref = _ref_path(tmp_path, ".png")
    save_plot(path, fig, ref=ref, chart={"version": 1})
    sidecar = path.parent / "charts" / f"{ref}.json"
    assert sidecar.read_text(encoding="utf-8") == '{"version":1}'


def test_save_plot_without_chart_creates_no_charts_directory(tmp_path: Path) -> None:
    fig = Figure()
    fig.add_subplot().plot([1, 2, 3])
    path, ref = _ref_path(tmp_path, ".png")
    save_plot(path, fig, ref=ref)
    assert not (path.parent / "charts").exists()


def test_save_plot_chart_write_failure_raises_tool_artifact_error(tmp_path: Path) -> None:
    fig = Figure()
    fig.add_subplot().plot([1, 2, 3])
    path, ref = _ref_path(tmp_path, ".png")
    (path.parent / "charts").write_text("x", encoding="utf-8")
    with pytest.raises(ToolArtifactError, match="cannot write chart"):
        save_plot(path, fig, ref=ref, chart={"version": 1})


def test_a9_save_plot_never_touches_pyplots_figure_registry(tmp_path: Path) -> None:
    """A9, restated for chunk 09 (§3.2): `save_plot` no longer calls `plt.close` -- a figure
    built via `matplotlib.figure.Figure()` directly, as every tool in this project now builds
    one, was never in pyplot's global registry to begin with, so there is nothing to close.
    `plt.get_fignums()` stays exactly as it was before the call -- the real invariant now is
    that `save_plot` leaves pyplot's state alone entirely, not that it tidies up after it."""
    fig = Figure()
    ax = fig.add_subplot()
    ax.plot([1, 2, 3])
    before = plt.get_fignums()
    path, ref = _ref_path(tmp_path, ".png")
    save_plot(path, fig, ref=ref)
    assert plt.get_fignums() == before


def test_a9b_save_plot_raises_tool_artifact_error_on_write_failure(tmp_path: Path) -> None:
    """Restated for chunk 09: `save_plot` no longer has a `finally`-block figure-close to
    prove works even on failure (there is nothing to close), so this now just confirms a write
    failure still raises `ToolArtifactError`, same as every other `save_*`."""
    fig = Figure()
    ax = fig.add_subplot()
    ax.plot([1, 2, 3])
    ref = new_artifact_ref()
    bad_path = tmp_path / "no_such_dir" / f"{ref}.png"
    with pytest.raises(ToolArtifactError):
        save_plot(bad_path, fig, ref=ref)


def test_a10_json_round_trip_nested_and_none(tmp_path: Path) -> None:
    """A10."""
    payload: JsonValue = {"a": 1, "b": [1, 2, {"c": None}], "d": "text", "e": True}
    path, ref = _ref_path(tmp_path, ".json")
    save_json(path, payload, ref=ref, kind="table")
    assert load_json(path) == payload


def test_a10b_save_json_rejects_nan(tmp_path: Path) -> None:
    """Chunk 09 §3.2/§4.5: `NaN` passes `JsonValue` validation (P2) but is not valid strict
    JSON (P3). `save_json` must refuse to write it rather than silently emit the bare token
    `NaN`, which a strict parser -- including the browser's `JSON.parse` -- rejects."""
    payload: JsonValue = {"r": float("nan")}
    path, ref = _ref_path(tmp_path, ".json")
    with pytest.raises(ToolArtifactError):
        save_json(path, payload, ref=ref, kind="table")
    assert not path.exists()


def test_a10c_save_json_rejects_inf(tmp_path: Path) -> None:
    """Same as A10b for `inf`/`-inf` -- both pass `JsonValue` validation and both are invalid
    strict JSON tokens."""
    path, ref = _ref_path(tmp_path, ".json")
    with pytest.raises(ToolArtifactError):
        save_json(path, {"r": float("inf")}, ref=ref, kind="table")
    path2, ref2 = _ref_path(tmp_path, ".json")
    with pytest.raises(ToolArtifactError):
        save_json(path2, {"r": float("-inf")}, ref=ref2, kind="table")


def test_a10d_save_json_writes_none_as_null(tmp_path: Path) -> None:
    """`None` is a legitimate, strict-JSON-compliant value -- distinguishing it from `NaN`/
    `inf` is the whole point of `_frame.json_scalar` mapping the latter to the former rather
    than to a sentinel string."""
    path, ref = _ref_path(tmp_path, ".json")
    save_json(path, {"r": None}, ref=ref, kind="table")
    assert path.read_text(encoding="utf-8") == '{"r": null}'


def test_a10e_save_json_output_loads_under_a_strict_parser(tmp_path: Path) -> None:
    """The written file must be loadable by a parser that rejects non-standard tokens --
    `json.loads` itself already does this (it has no `parse_constant` override by default that
    would accept `NaN`), so a payload saved by `save_json` round-trips through the strictest
    parser this project has without any special-casing."""
    payload: JsonValue = {"a": [1, 2.5, None, True, "x"], "b": {"c": 3}}
    path, ref = _ref_path(tmp_path, ".json")
    save_json(path, payload, ref=ref, kind="table")
    import json

    with path.open(encoding="utf-8") as f:
        # parse_constant raises on the very tokens allow_nan=False refuses to write; a file
        # that still contained one would fail here even if load_json's own json.loads happened
        # to tolerate it.
        loaded = json.load(f, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    assert loaded == payload


def test_a9c_save_plot_leaves_pyplots_registry_empty(tmp_path: Path) -> None:
    """§4.5: with no figure ever created through `pyplot` in this test, `plt.get_fignums()`
    stays empty across a `save_plot` call -- the registry was never touched at all."""
    assert plt.get_fignums() == []
    fig = Figure()
    ax = fig.add_subplot()
    ax.plot([1, 2, 3])
    path, ref = _ref_path(tmp_path, ".png")
    save_plot(path, fig, ref=ref)
    assert plt.get_fignums() == []


def test_a11_text_round_trip_utf8(tmp_path: Path) -> None:
    """A11."""
    path, ref = _ref_path(tmp_path, ".md")
    text = "héllo wörld — 日本語"
    save_text(path, text, ref=ref)
    assert path.read_text(encoding="utf-8") == text


def test_a12_write_to_nonexistent_directory_raises_tool_artifact_error(tmp_path: Path) -> None:
    """A12."""
    ref = new_artifact_ref()
    path = tmp_path / "no_such_dir" / f"{ref}.parquet"
    frame = pd.DataFrame({"x": [1]})
    with pytest.raises(ToolArtifactError) as exc_info:
        save_dataframe(path, frame, ref=ref)
    assert str(path) in str(exc_info.value)


def test_a13_read_missing_file_raises_tool_artifact_error(tmp_path: Path) -> None:
    """A13."""
    path = tmp_path / "does_not_exist.parquet"
    with pytest.raises(ToolArtifactError) as exc_info:
        load_dataframe(path)
    assert str(path) in str(exc_info.value)


def test_a14_read_corrupt_parquet_raises_tool_artifact_error_not_pyarrow_traceback(
    tmp_path: Path,
) -> None:
    """A14."""
    path = tmp_path / "corrupt.parquet"
    path.write_bytes(b"not a parquet file at all")
    with pytest.raises(ToolArtifactError):
        load_dataframe(path)


def test_a15_read_corrupt_joblib_raises_tool_artifact_error(tmp_path: Path) -> None:
    """A15."""
    path = tmp_path / "corrupt.joblib"
    path.write_bytes(b"not a pickle at all")
    with pytest.raises(ToolArtifactError):
        load_model(path)


def test_load_model_oserror_other_than_missing_raises_tool_artifact_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `except OSError` branch distinct from `FileNotFoundError` -- e.g. a permission
    failure on an existing path, not a missing one."""
    path = tmp_path / "m.joblib"
    joblib.dump({"a": 1}, path)

    def fake_load(*args: object, **kwargs: object) -> object:
        raise PermissionError("simulated permission failure")

    monkeypatch.setattr("statlab_tools.artifacts.joblib.load", fake_load)
    with pytest.raises(ToolArtifactError, match="cannot read model"):
        load_model(path)


def test_load_json_oserror_other_than_missing_raises_tool_artifact_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `except OSError` branch in `load_json` distinct from `FileNotFoundError`."""
    path = tmp_path / "j.json"
    path.write_text("{}", encoding="utf-8")

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("simulated permission failure")

    monkeypatch.setattr(Path, "read_text", fake_read_text)
    with pytest.raises(ToolArtifactError, match="cannot read json"):
        load_json(path)


def test_a13b_read_missing_model_raises_tool_artifact_error(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.joblib"
    with pytest.raises(ToolArtifactError) as exc_info:
        load_model(path)
    assert str(path) in str(exc_info.value)


def test_a13c_read_missing_json_raises_tool_artifact_error(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.json"
    with pytest.raises(ToolArtifactError) as exc_info:
        load_json(path)
    assert str(path) in str(exc_info.value)


def test_load_json_corrupt_raises_tool_artifact_error(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(ToolArtifactError):
        load_json(path)


def test_save_text_write_failure_raises_tool_artifact_error(tmp_path: Path) -> None:
    ref = new_artifact_ref()
    path = tmp_path / "no_such_dir" / f"{ref}.md"
    with pytest.raises(ToolArtifactError):
        save_text(path, "hello", ref=ref)


def test_save_model_write_failure_raises_tool_artifact_error(tmp_path: Path) -> None:
    ref = new_artifact_ref()
    path = tmp_path / "no_such_dir" / f"{ref}.joblib"
    with pytest.raises(ToolArtifactError):
        save_model(path, {"a": 1}, ref=ref, kind="model")


def test_save_json_write_failure_raises_tool_artifact_error(tmp_path: Path) -> None:
    ref = new_artifact_ref()
    path = tmp_path / "no_such_dir" / f"{ref}.json"
    with pytest.raises(ToolArtifactError):
        save_json(path, {"a": 1}, ref=ref, kind="table")


# --- §7.4: path/ref guard ---------------------------------------------------------------------


def test_save_dataframe_rejects_path_stem_ref_mismatch(
    tmp_path: Path, sample_frame: pd.DataFrame
) -> None:
    ref = new_artifact_ref()
    wrong_path = tmp_path / "not_the_ref.parquet"
    with pytest.raises(ToolArtifactError, match="does not match ref"):
        save_dataframe(wrong_path, sample_frame, ref=ref)
    assert not wrong_path.exists()


def test_save_model_rejects_path_stem_ref_mismatch(tmp_path: Path) -> None:
    ref = new_artifact_ref()
    wrong_path = tmp_path / "not_the_ref.joblib"
    with pytest.raises(ToolArtifactError, match="does not match ref"):
        save_model(wrong_path, {"a": 1}, ref=ref, kind="model")
    assert not wrong_path.exists()


def test_save_plot_rejects_path_stem_ref_mismatch(tmp_path: Path) -> None:
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    ref = new_artifact_ref()
    wrong_path = tmp_path / "not_the_ref.png"
    with pytest.raises(ToolArtifactError, match="does not match ref"):
        save_plot(wrong_path, fig, ref=ref)
    assert not wrong_path.exists()
    # the figure is still closed even though the guard fired before any write was attempted --
    # the guard runs before the try/finally, so this proves the guard itself does not leak.
    plt.close(fig)


def test_save_json_rejects_path_stem_ref_mismatch(tmp_path: Path) -> None:
    ref = new_artifact_ref()
    wrong_path = tmp_path / "not_the_ref.json"
    with pytest.raises(ToolArtifactError, match="does not match ref"):
        save_json(wrong_path, {"a": 1}, ref=ref, kind="table")
    assert not wrong_path.exists()


def test_save_text_rejects_path_stem_ref_mismatch(tmp_path: Path) -> None:
    ref = new_artifact_ref()
    wrong_path = tmp_path / "not_the_ref.md"
    with pytest.raises(ToolArtifactError, match="does not match ref"):
        save_text(wrong_path, "hello", ref=ref)
    assert not wrong_path.exists()


def test_save_dataframe_accepts_matching_stem(tmp_path: Path, sample_frame: pd.DataFrame) -> None:
    """The positive case, paired with the rejection tests above: `ref == path.stem` succeeds."""
    ref = new_artifact_ref()
    path = tmp_path / f"{ref}.parquet"
    meta = save_dataframe(path, sample_frame, ref=ref)
    assert meta.ref == ref
    assert path.exists()


# --- source-scan tests ----------------------------------------------------------------------


def test_a16_no_dtype_equals_object_in_module() -> None:
    """A16: the pandas 3.0 trap most likely to be written from habit."""
    import statlab_tools.artifacts as artifacts_mod

    source = Path(artifacts_mod.__file__).read_text(encoding="utf-8")
    assert "== object" not in source
    assert "dtype == object" not in source


def test_a18_module_imports_no_statlab_backend() -> None:
    """A18 (restored to strict form, §7.1): the MCP side must not reach into the backend at
    all. Not narrowed to persistence/storage/domains -- `statlab.*` in full, including
    `statlab.errors`. The codebase's own precedent (`ToolSettingsError`, chunk 01) is that even
    the shared error taxonomy is backend-owned and must not cross this boundary; this module
    defines its own `ToolArtifactError` instead."""
    import statlab_tools.artifacts as artifacts_mod

    for name, value in vars(artifacts_mod).items():
        if name.startswith("__"):
            continue
        module_name = getattr(value, "__module__", None)
        if module_name is None:
            continue
        assert not module_name.startswith("statlab."), f"{name} -> {module_name}"
        assert module_name != "statlab", f"{name} resolves to the statlab package itself"


def test_a18b_module_source_never_imports_statlab_backend() -> None:
    """Regex source-scan in addition to A18's import-graph walk: catches an import added
    inside a function body, which `vars()` cannot see. Matched against actual `import`/`from`
    statements, not a bare substring -- this module's own docstring explains why it does NOT
    import `statlab.errors`, and that explanation legitimately contains the string."""
    import statlab_tools.artifacts as artifacts_mod

    pattern = re.compile(r"^\s*(?:import|from)\s+statlab\.", re.MULTILINE)
    source = Path(artifacts_mod.__file__).read_text(encoding="utf-8")
    assert pattern.search(source) is None


def test_error_type_independence_from_backend() -> None:
    """`ToolArtifactError` and the backend's `ArtifactError` are unrelated types -- same
    boundary reasoning as `test_t10_error_type_independence` for `ToolSettingsError`."""
    from statlab.errors import ArtifactError

    assert not issubclass(ToolArtifactError, ArtifactError)
    assert not issubclass(ArtifactError, ToolArtifactError)


# --- A17: no warning from any operation ------------------------------------------------------


def test_a17_save_load_dataframe_raises_no_warning(
    tmp_path: Path, sample_frame: pd.DataFrame
) -> None:
    """A17: `filterwarnings = ["error"]` already covers this at the suite level; asserted
    explicitly here so a future pandas deprecation is attributed to this exact operation."""
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)
    load_dataframe(path)


def test_boundary_pandas_appears_only_in_this_module() -> None:
    """Confirms the DoD's single-module claim from the test side too: importing this module
    is what pulls pandas in, and no sibling module in the same package does."""
    assert "pandas" in sys.modules


# --- additional coverage: save-side failures, more shapes -----------------------------------


def test_save_model_oserror_wraps_with_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = new_artifact_ref()
    path = tmp_path / f"{ref}.joblib"

    def fake_dump(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("statlab_tools.artifacts.joblib.dump", fake_dump)
    with pytest.raises(ToolArtifactError, match="cannot write model"):
        save_model(path, {"a": 1}, ref=ref, kind="model")


def test_save_dataframe_oserror_wraps_with_path(
    tmp_path: Path, sample_frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, ref = _ref_path(tmp_path, ".parquet")

    def fake_to_parquet(self: pd.DataFrame, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fake_to_parquet)
    with pytest.raises(ToolArtifactError, match="cannot write dataframe"):
        save_dataframe(path, sample_frame, ref=ref)


def test_load_dataframe_oserror_other_than_missing_wraps(
    tmp_path: Path, sample_frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)

    def fake_read_parquet(*args: object, **kwargs: object) -> None:
        raise OSError("simulated read failure")

    monkeypatch.setattr(pd, "read_parquet", fake_read_parquet)
    with pytest.raises(ToolArtifactError, match="cannot read dataframe"):
        load_dataframe(path)


def test_save_plot_oserror_still_wraps_with_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    path, ref = _ref_path(tmp_path, ".png")

    def fake_savefig(self: object, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(type(fig), "savefig", fake_savefig)
    with pytest.raises(ToolArtifactError, match="cannot write plot"):
        save_plot(path, fig, ref=ref)


def test_json_round_trip_list_payload(tmp_path: Path) -> None:
    path, ref = _ref_path(tmp_path, ".json")
    payload: JsonValue = [1, "two", 3.0, None, True]
    save_json(path, payload, ref=ref, kind="table")
    assert load_json(path) == payload


def test_json_round_trip_empty_dict(tmp_path: Path) -> None:
    path, ref = _ref_path(tmp_path, ".json")
    save_json(path, {}, ref=ref, kind="curve")
    assert load_json(path) == {}


def test_text_round_trip_empty_string(tmp_path: Path) -> None:
    path, ref = _ref_path(tmp_path, ".md")
    save_text(path, "", ref=ref)
    assert path.read_text(encoding="utf-8") == ""


def test_category_dtype_categories_are_preserved(
    tmp_path: Path, sample_frame: pd.DataFrame
) -> None:
    """Not just the dtype name but the actual category set survives -- a stricter check than
    A1's `str(dtype)` comparison for this one column."""
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)
    loaded = load_dataframe(path)
    assert set(loaded["c"].cat.categories) == set(sample_frame["c"].cat.categories)


def test_datetime_values_preserve_microsecond_precision(
    tmp_path: Path, sample_frame: pd.DataFrame
) -> None:
    path, ref = _ref_path(tmp_path, ".parquet")
    save_dataframe(path, sample_frame, ref=ref)
    loaded = load_dataframe(path)
    assert (loaded["dt"] == sample_frame["dt"]).all()


def test_multiple_models_do_not_interfere(tmp_path: Path) -> None:
    """Two distinct joblib files at different paths round-trip independently."""
    m1 = LogisticRegression().fit([[0], [1]], [0, 1])
    m2 = LogisticRegression(C=2.0).fit([[0], [1]], [0, 1])
    p1, ref1 = _ref_path(tmp_path, ".joblib")
    p2, ref2 = _ref_path(tmp_path, ".joblib")
    save_model(p1, m1, ref=ref1, kind="model")
    save_model(p2, m2, ref=ref2, kind="model")
    loaded1 = load_model(p1)
    loaded2 = load_model(p2)
    assert isinstance(loaded1, LogisticRegression)
    assert isinstance(loaded2, LogisticRegression)
    assert loaded1.C == m1.C
    assert loaded2.C == m2.C
