"""Chunk 07 §4.5: the two v1 artifact tools, called directly against their bodies (not through
a session) using the `run_paths` fixture -- `RunPaths` is exactly what the decorator strips
`project`/`run` down to before calling the body.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tool import ToolDataError
from statlab_tools.tools.artifacts import describe_artifact, list_artifacts


def _entry_dict(value: JsonValue) -> dict[str, JsonValue]:
    """Narrows one `artifacts` list entry (a `JsonValue`) to a plain dict for key access.
    Every entry is genuinely a dict at runtime -- `list_artifacts` builds them that way -- this
    just gives mypy the same fact."""
    assert isinstance(value, dict)
    return value


def _write_frame(paths: RunPaths, ref: str, frame: pd.DataFrame) -> Path:
    from statlab_contracts.artifacts import ArtifactRef

    path = paths.artifact(ArtifactRef(ref), "dataframe")
    save_dataframe(path, frame, ref=ArtifactRef(ref))
    return path


# --- list_artifacts --------------------------------------------------------------------------


def test_list_artifacts_empty_directory(run_paths: RunPaths) -> None:
    result = list_artifacts(run_paths)
    assert result.summary["count"] == 0
    assert result.summary["artifacts"] == []


def test_list_artifacts_one_artifact(run_paths: RunPaths) -> None:
    _write_frame(run_paths, "a_0000000000000001", pd.DataFrame({"x": [1, 2]}))
    result = list_artifacts(run_paths)
    assert result.summary["count"] == 1
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    first = _entry_dict(entries[0])
    assert first["ref"] == "a_0000000000000001"
    assert first["kind"] == "dataframe"


def test_list_artifacts_many(run_paths: RunPaths) -> None:
    for i in range(5):
        _write_frame(run_paths, f"a_000000000000000{i}", pd.DataFrame({"x": [i]}))
    result = list_artifacts(run_paths)
    assert result.summary["count"] == 5


def test_list_artifacts_excludes_non_artifact_files(run_paths: RunPaths) -> None:
    _write_frame(run_paths, "a_0000000000000002", pd.DataFrame({"x": [1]}))
    stray_dir = run_paths.artifacts / "not_a_file_a_dir"
    stray_dir.mkdir()
    result = list_artifacts(run_paths)
    assert result.summary["count"] == 1


def test_list_artifacts_reports_kind_from_extension(run_paths: RunPaths) -> None:
    """`.parquet` maps to `dataframe` by the module's own documented best-effort choice."""
    _write_frame(run_paths, "a_0000000000000003", pd.DataFrame({"x": [1]}))
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    assert _entry_dict(entries[0])["kind"] == "dataframe"


def test_list_artifacts_reports_first_declared_kind_for_joblib(run_paths: RunPaths) -> None:
    """`.joblib` is shared by three kinds (`transformer`/`model`/`inferential_fit`); the
    best-effort guess is whichever is declared first in `EXTENSION_FOR_KIND` --
    `transformer`, not `model`. This test pins the actual guess, not an assumed one."""
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_contracts.layout import EXTENSION_FOR_KIND
    from statlab_tools.artifacts import save_model

    ref = ArtifactRef("a_0000000000000004")
    path = run_paths.artifact(ref, "model")
    save_model(path, {"fitted": True}, ref=ref, kind="model")
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    expected_guess = next(k for k, ext in EXTENSION_FOR_KIND.items() if ext == ".joblib")
    assert _entry_dict(entries[0])["kind"] == expected_guess


def test_list_artifacts_unknown_extension_reports_unknown(run_paths: RunPaths) -> None:
    stray = run_paths.artifacts / "a_0000000000000005.txt"
    stray.write_text("not an artifact extension")
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    assert _entry_dict(entries[0])["kind"] == "unknown"


def test_list_artifacts_sorted_order(run_paths: RunPaths) -> None:
    _write_frame(run_paths, "a_0000000000000009", pd.DataFrame({"x": [1]}))
    _write_frame(run_paths, "a_0000000000000001", pd.DataFrame({"x": [1]}))
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    refs = [_entry_dict(e)["ref"] for e in entries]
    assert refs == sorted(refs, key=str)


def test_list_artifacts_docstring_states_filesystem_not_registry() -> None:
    """The docstring must be honest about what this tool actually reads."""
    doc = list_artifacts.__doc__
    assert doc is not None
    assert "filesystem" in doc.lower() or "database" in doc.lower()


# --- describe_artifact ------------------------------------------------------------------------


def test_describe_artifact_reports_shape(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    _write_frame(run_paths, "a_0000000000000010", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000010"), "dataframe")
    assert result.summary["rows"] == 3
    assert result.summary["cols"] == 2


def test_describe_artifact_reports_dtypes_as_str_not_object(run_paths: RunPaths) -> None:
    """The pandas 3.0 trap: string columns report dtype `str`, not `object`."""
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"s": pd.array(["a", "b"], dtype="str")})
    _write_frame(run_paths, "a_0000000000000011", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000011"), "dataframe")
    dtypes = result.summary["dtypes"]
    assert isinstance(dtypes, dict)
    assert dtypes["s"] == "str"
    assert dtypes["s"] != "object"


def test_describe_artifact_returns_head_rows(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"x": list(range(20))})
    _write_frame(run_paths, "a_0000000000000012", frame)
    result = describe_artifact(
        run_paths, ArtifactRef("a_0000000000000012"), "dataframe", head_rows=3
    )
    head = result.summary["head"]
    assert isinstance(head, list)
    assert len(head) == 3
    assert _entry_dict(head[0])["x"] == 0


def test_describe_artifact_default_head_rows_is_ten(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"x": list(range(20))})
    _write_frame(run_paths, "a_0000000000000013", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000013"), "dataframe")
    head = result.summary["head"]
    assert isinstance(head, list)
    assert len(head) == 10


def test_describe_artifact_handles_empty_frame(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"x": pd.array([], dtype="int64")})
    _write_frame(run_paths, "a_0000000000000014", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000014"), "dataframe")
    assert result.summary["rows"] == 0
    assert result.summary["head"] == []


def test_describe_artifact_datetime_column_is_json_safe(run_paths: RunPaths) -> None:
    """`pandas.Timestamp` is not a valid `JsonValue` -- the tool must stringify it."""
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"dt": pd.to_datetime(["2024-01-01", "2024-01-02"])})
    _write_frame(run_paths, "a_0000000000000015", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000015"), "dataframe")
    head = result.summary["head"]
    assert isinstance(head, list)
    dt_value = _entry_dict(head[0])["dt"]
    assert isinstance(dt_value, str)
    assert dt_value.startswith("2024-01-01")


def test_describe_artifact_missing_file_is_transient(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import ToolArtifactError

    with pytest.raises(ToolArtifactError):
        describe_artifact(run_paths, ArtifactRef("a_ffffffffffffffff"), "dataframe")


def test_describe_artifact_corrupt_file_raises_tool_artifact_error(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import ToolArtifactError

    ref = ArtifactRef("a_0000000000000016")
    path = run_paths.artifact(ref, "dataframe")
    path.write_bytes(b"not a parquet file")
    with pytest.raises(ToolArtifactError):
        describe_artifact(run_paths, ref, "dataframe")


def test_describe_artifact_predictions_kind_also_supported(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"y_pred": [0, 1, 1]})
    ref = ArtifactRef("a_0000000000000017")
    path = run_paths.artifact(ref, "predictions")
    save_dataframe(path, frame, ref=ref, kind="predictions")
    result = describe_artifact(run_paths, ref, "predictions")
    assert result.summary["rows"] == 3


def test_describe_artifact_unsupported_kind_raises_tool_data_error(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import save_model

    ref = ArtifactRef("a_0000000000000018")
    path = run_paths.artifact(ref, "model")
    save_model(path, {"fitted": True}, ref=ref, kind="model")
    with pytest.raises(ToolDataError):
        describe_artifact(run_paths, ref, "model")


def test_describe_artifact_head_rows_bound_is_enforced_at_the_schema_layer(
    run_paths: RunPaths,
) -> None:
    """`head_rows` has `ge=1, le=100` in its `Field` -- enforced by pydantic when the MCP
    layer validates arguments against the served schema, before the body ever runs. Calling
    the body directly (as every other test in this file does) bypasses that validation
    entirely, since there is no pydantic model at this layer -- so this test instead confirms
    the bound is genuinely declared on the parameter, via the same `Annotated`/`Field`
    introspection the decorator itself uses to build the schema."""
    from typing import get_type_hints

    from pydantic.fields import FieldInfo

    hints = get_type_hints(describe_artifact, include_extras=True)
    head_rows_hint = hints["head_rows"]
    field_info = next(arg for arg in head_rows_hint.__metadata__ if isinstance(arg, FieldInfo))
    assert field_info.metadata
    bounds = {type(constraint).__name__: constraint for constraint in field_info.metadata}
    assert "Ge" in bounds
    assert bounds["Ge"].ge == 1
    assert "Le" in bounds
    assert bounds["Le"].le == 100


def test_describe_artifact_docstring_states_filesystem_not_registry() -> None:
    doc = describe_artifact.__doc__
    assert doc is not None
    assert "filesystem" in doc.lower() or "database" in doc.lower()


# --- boundary ----------------------------------------------------------------------------------


def test_tools_artifacts_module_does_not_import_pandas() -> None:
    """The tools module reads a `DataFrame`'s `.dtypes`/`.shape`/`.head()` but never imports
    pandas itself -- `statlab_tools.artifacts` (content IO) is the only pandas-importing
    module in the project."""
    import statlab_tools.tools.artifacts as artifacts_tools_mod

    source = Path(artifacts_tools_mod.__file__).read_text(encoding="utf-8")
    assert "import pandas" not in source


def test_tools_artifacts_module_imports_no_statlab_backend() -> None:
    import re

    import statlab_tools.tools.artifacts as artifacts_tools_mod

    pattern = re.compile(r"^\s*(?:import|from)\s+statlab\.", re.MULTILINE)
    source = Path(artifacts_tools_mod.__file__).read_text(encoding="utf-8")
    assert pattern.search(source) is None


# --- additional coverage: mixed kinds, plots, reports -------------------------------------------


def test_list_artifacts_mixed_kinds_in_one_run(run_paths: RunPaths) -> None:
    """A run with a dataframe, a model, and a plot together -- each reports its own
    best-effort kind independently."""
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_contracts.layout import EXTENSION_FOR_KIND
    from statlab_tools.artifacts import save_model

    _write_frame(run_paths, "a_0000000000000020", pd.DataFrame({"x": [1]}))
    model_ref = ArtifactRef("a_0000000000000021")
    save_model(
        run_paths.artifact(model_ref, "model"), {"fitted": True}, ref=model_ref, kind="model"
    )
    result = list_artifacts(run_paths)
    assert result.summary["count"] == 2
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    kinds = {_entry_dict(e)["kind"] for e in entries}
    expected_joblib_guess = next(k for k, ext in EXTENSION_FOR_KIND.items() if ext == ".joblib")
    assert kinds == {"dataframe", expected_joblib_guess}


def test_describe_artifact_table_kind_reports_top_level_structure(run_paths: RunPaths) -> None:
    """`table`/`curve` are JSON. The walking-skeleton gate (chunk 16) describes the comparison
    *table* at node 13, so `describe_artifact` summarises a JSON payload's top-level structure:
    its keys and the length of any list-valued entry. Values are not reconstructed -- only shape
    is reported (PLAN.md §3.1)."""
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import save_json

    ref = ArtifactRef("a_0000000000000022")
    path = run_paths.artifact(ref, "table")
    save_json(
        path, {"metric": "roc_auc", "ranking": [{"rank": 1}, {"rank": 2}]}, ref=ref, kind="table"
    )
    result = describe_artifact(run_paths, ref, "table")
    assert result.summary["shape"] == "object"
    assert result.summary["keys"] == ["metric", "ranking"]
    assert result.summary["list_lengths"] == {"ranking": 2}


def test_describe_artifact_curve_kind_bare_list_reports_length(run_paths: RunPaths) -> None:
    """A `curve` whose JSON payload is a bare array reports `shape="array"` and its length."""
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import save_json

    ref = ArtifactRef("a_000000000000002a")
    path = run_paths.artifact(ref, "curve")
    save_json(path, [0.1, 0.2, 0.3], ref=ref, kind="curve")
    result = describe_artifact(run_paths, ref, "curve")
    assert result.summary["shape"] == "array"
    assert result.summary["length"] == 3


def test_describe_artifact_table_scalar_payload_reports_value(run_paths: RunPaths) -> None:
    """A JSON payload that is a bare scalar reports `shape="scalar"` and the value -- the third
    branch of the JSON describer."""
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import save_json

    ref = ArtifactRef("a_000000000000002b")
    path = run_paths.artifact(ref, "table")
    save_json(path, 42, ref=ref, kind="table")
    result = describe_artifact(run_paths, ref, "table")
    assert result.summary["shape"] == "scalar"
    assert result.summary["value"] == 42


def test_describe_artifact_plot_kind_raises_not_yet_supported(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef
    from statlab_tools.artifacts import save_text

    ref = ArtifactRef("a_0000000000000023")
    # save_text always writes kind="report", but the point here is exercising describe_artifact
    # with a genuinely unsupported *requested* kind ("plot"), independent of what produced the
    # file on disk -- describe_artifact never opens the file at all before raising.
    path = run_paths.artifact(ref, "report")
    save_text(path, "not a plot", ref=ref)
    with pytest.raises(ToolDataError, match="plot"):
        describe_artifact(run_paths, ref, "plot")


def test_list_artifacts_and_describe_artifact_share_the_same_run(run_paths: RunPaths) -> None:
    """A basic end-to-end sanity check: an artifact `list_artifacts` reports can be described
    by `describe_artifact` using the ref and a correctly-guessed kind."""
    frame = pd.DataFrame({"a": [1, 2]})
    _write_frame(run_paths, "a_0000000000000024", frame)
    listing = list_artifacts(run_paths)
    entries = listing.summary["artifacts"]
    assert isinstance(entries, list)
    entry = _entry_dict(entries[0])
    ref_str = entry["ref"]
    kind_str = entry["kind"]
    assert isinstance(ref_str, str)
    assert isinstance(kind_str, str)
    from statlab_contracts.artifacts import ArtifactRef

    described = describe_artifact(run_paths, ArtifactRef(ref_str), kind_str)
    assert described.summary["rows"] == 2


def test_describe_artifact_multiple_calls_are_independent(run_paths: RunPaths) -> None:
    """Two `describe_artifact` calls on different artifacts in the same run do not interfere
    with each other -- statelessness at the tool-body level, not just the session level."""
    from statlab_contracts.artifacts import ArtifactRef

    frame_a = pd.DataFrame({"x": [1, 2]})
    frame_b = pd.DataFrame({"y": [1, 2, 3]})
    _write_frame(run_paths, "a_0000000000000025", frame_a)
    _write_frame(run_paths, "a_0000000000000026", frame_b)
    result_a = describe_artifact(run_paths, ArtifactRef("a_0000000000000025"), "dataframe")
    result_b = describe_artifact(run_paths, ArtifactRef("a_0000000000000026"), "dataframe")
    assert result_a.summary["cols"] == 1
    assert result_b.summary["cols"] == 1
    assert result_a.summary["rows"] == 2
    assert result_b.summary["rows"] == 3


def test_describe_artifact_bool_column_dtype_reported(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"flag": pd.array([True, False, True], dtype="bool")})
    _write_frame(run_paths, "a_0000000000000027", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000027"), "dataframe")
    dtypes = result.summary["dtypes"]
    assert isinstance(dtypes, dict)
    assert dtypes["flag"] == "bool"


def test_describe_artifact_category_column_dtype_reported(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"c": pd.Categorical(["a", "b", "a"])})
    _write_frame(run_paths, "a_0000000000000028", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000028"), "dataframe")
    dtypes = result.summary["dtypes"]
    assert isinstance(dtypes, dict)
    assert dtypes["c"] == "category"


def test_describe_artifact_int64_column_dtype_reported(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"i": pd.array([1, 2, 3], dtype="int64")})
    _write_frame(run_paths, "a_0000000000000029", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000029"), "dataframe")
    dtypes = result.summary["dtypes"]
    assert isinstance(dtypes, dict)
    assert dtypes["i"] == "int64"


def test_list_artifacts_summary_has_exactly_count_and_artifacts_keys(run_paths: RunPaths) -> None:
    result = list_artifacts(run_paths)
    assert set(result.summary) == {"count", "artifacts"}


def test_describe_artifact_summary_has_exactly_four_keys(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    _write_frame(run_paths, "a_0000000000000030", pd.DataFrame({"x": [1]}))
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000030"), "dataframe")
    assert set(result.summary) == {"rows", "cols", "dtypes", "head"}


def test_list_artifacts_head_rows_param_not_present(run_paths: RunPaths) -> None:
    """`list_artifacts` takes only `paths` -- confirms its signature has no stray parameters
    inherited by copy-paste from `describe_artifact`."""
    import inspect

    signature = inspect.signature(list_artifacts)
    assert list(signature.parameters) == ["paths"]


def test_describe_artifact_signature_parameter_order(run_paths: RunPaths) -> None:
    """`paths, artifact, kind, head_rows` in that order -- the decorator strips `paths` for
    the schema but the body itself keeps it first."""
    del run_paths
    import inspect

    signature = inspect.signature(describe_artifact)
    assert list(signature.parameters) == ["paths", "artifact", "kind", "head_rows"]


def test_write_frame_helper_produces_a_readable_parquet_file(run_paths: RunPaths) -> None:
    """A sanity check on this test file's own helper, not the tools -- if `_write_frame` were
    broken, every other test in this file would fail for the wrong reason."""
    frame = pd.DataFrame({"z": [9, 8, 7]})
    path = _write_frame(run_paths, "a_0000000000000031", frame)
    assert path.exists()
    assert path.suffix == ".parquet"


def test_two_describe_artifact_calls_same_ref_are_idempotent(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    _write_frame(run_paths, "a_0000000000000032", pd.DataFrame({"x": [1, 2, 3]}))
    ref = ArtifactRef("a_0000000000000032")
    first = describe_artifact(run_paths, ref, "dataframe")
    second = describe_artifact(run_paths, ref, "dataframe")
    assert first.summary == second.summary


def test_list_artifacts_zero_count_matches_empty_list_length(run_paths: RunPaths) -> None:
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    assert result.summary["count"] == len(entries)


def test_list_artifacts_count_matches_entries_length_when_nonempty(run_paths: RunPaths) -> None:
    for i in range(3):
        _write_frame(run_paths, f"a_000000000000004{i}", pd.DataFrame({"x": [i]}))
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    assert result.summary["count"] == len(entries) == 3


def test_describe_artifact_head_rows_larger_than_frame_returns_all_rows(
    run_paths: RunPaths,
) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"x": [1, 2, 3]})
    _write_frame(run_paths, "a_0000000000000050", frame)
    result = describe_artifact(
        run_paths, ArtifactRef("a_0000000000000050"), "dataframe", head_rows=100
    )
    head = result.summary["head"]
    assert isinstance(head, list)
    assert len(head) == 3


def test_describe_artifact_float_column_dtype_reported(run_paths: RunPaths) -> None:
    from statlab_contracts.artifacts import ArtifactRef

    frame = pd.DataFrame({"f": pd.array([1.1, 2.2], dtype="float64")})
    _write_frame(run_paths, "a_0000000000000051", frame)
    result = describe_artifact(run_paths, ArtifactRef("a_0000000000000051"), "dataframe")
    dtypes = result.summary["dtypes"]
    assert isinstance(dtypes, dict)
    assert dtypes["f"] == "float64"


def test_list_artifacts_ref_matches_filename_stem(run_paths: RunPaths) -> None:
    """The `ref` reported by `list_artifacts` is exactly the file's stem -- proven against a
    real written file, not assumed from the ref used to write it."""
    ref_used = "a_0000000000000060"
    path = _write_frame(run_paths, ref_used, pd.DataFrame({"x": [1]}))
    result = list_artifacts(run_paths)
    entries = result.summary["artifacts"]
    assert isinstance(entries, list)
    assert _entry_dict(entries[0])["ref"] == path.stem == ref_used
