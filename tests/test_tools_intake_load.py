"""`load_csv`: PLAN.md §4 node 1, `plan/08_tools_intake.md` §3.2, §4.2 (L1-L20).

Calls the tool through its decorated callable directly for most cases -- `load_csv(paths, ...)`
is the undecorated body (the decorator returns `func`, not `wrapper`, chunk 07 §8). L3 is the
one exception: it must prove the schema rejects a malformed `dataset` *before* the body runs,
which only a real MCP round-trip can show.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
from pydantic import JsonValue
from statlab_contracts.artifacts import new_artifact_ref
from statlab_contracts.contract import DatasetOptions
from statlab_contracts.layout import ProjectPaths, RunPaths
from statlab_tools.artifacts import load_dataframe, save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.intake import (
    ROW_WARNING_THRESHOLD,
    append_upload_rows,
    load_csv,
)

if TYPE_CHECKING:
    from mcp import ClientSession

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]

pytestmark = pytest.mark.usefixtures("tool_settings")

_OPTS = DatasetOptions()


def _dict(value: JsonValue) -> dict[str, JsonValue]:
    """Narrows a `JsonValue`-typed `summary[...]` entry to a plain dict for key access -- the
    tool genuinely builds it that way; this just gives mypy the same fact."""
    assert isinstance(value, dict)
    return value


def _write_dataset(paths: RunPaths, content: bytes) -> str:
    """Writes `content` under `datasets/<sha256>.csv`, returning the sha256 hex digest."""
    project = ProjectPaths(data_dir=paths.data_dir, slug=paths.slug)
    project.datasets.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    project.dataset_csv(digest).write_bytes(content)
    return digest


# --- L1: happy path --------------------------------------------------------------------------


def test_l1_happy_path_round_trips_and_summarises(
    seeded_run: RunPaths, dataset_csv_bytes: bytes
) -> None:
    digest = hashlib.sha256(dataset_csv_bytes).hexdigest()
    result = load_csv(seeded_run, dataset=digest)
    assert result.summary["rows"] == 3
    assert result.summary["cols"] == 3
    assert result.summary["columns"] == ["a", "b", "c"]
    assert result.summary["dtypes"] == {"a": "int64", "b": "float64", "c": "str"}
    assert len(result.artifacts) == 1
    meta = result.artifacts[0]
    assert meta.kind == "dataframe"
    assert meta.label == "raw"
    frame = load_dataframe(seeded_run.artifact(meta.ref, "dataframe"))
    assert frame.shape == (3, 3)


def test_l1_null_counts_reported(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"a,b\n1,\n2,3\n")
    result = load_csv(seeded_run, dataset=digest)
    assert result.summary["null_counts"] == {"a": 0, "b": 1}


# --- L2: defaults derived from DatasetOptions -------------------------------------------------


@pytest.mark.parametrize("field_name", sorted(DatasetOptions.model_fields))
def test_l2_default_equals_dataset_options(field_name: str) -> None:
    """Derived from `model_fields`, not hand-listed -- a change to `DatasetOptions`'s defaults
    must not silently diverge from `load_csv`'s own defaults."""
    import inspect

    signature = inspect.signature(load_csv)
    assert signature.parameters[field_name].default == getattr(_OPTS, field_name)


def test_l2_every_dataset_options_field_is_a_load_csv_parameter() -> None:
    """The reverse direction: every option `load_csv` could silently fail to accept."""
    import inspect

    signature = inspect.signature(load_csv)
    for field_name in DatasetOptions.model_fields:
        assert field_name in signature.parameters


# --- L3: schema rejects a malformed dataset before the body runs (real MCP) ------------------


@pytest.mark.parametrize(
    "bad_dataset",
    [
        "not-hex-characters-------------------------------------------x",
        "a" * 63,
        "a" * 65,
        ("A" * 64),
        ("a" * 64) + "\n",
        "../" + "a" * 61,
        "/etc/passwd" + "a" * 53,
    ],
)
@pytest.mark.asyncio
async def test_l3_malformed_dataset_rejected_by_schema_not_body(
    mcp_session: SessionFactory,
    run_paths_over_mcp: str,
    bad_dataset: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schema rejects every path-shaped or malformed `dataset` before `load_csv`'s body
    runs. Proven two ways at once: the body is monkeypatched to raise `AssertionError` if ever
    called (so a body-reaching call would fail LOUDLY, not silently), and the real MCP call's
    `is_error` is asserted `True` -- a genuine schema-validation rejection is a protocol-level
    error (confirmed by direct probe: the message names the exact pattern), unlike a tool body
    failure, which returns `is_error=False` with a structured `ok: False` result (see the
    companion test below)."""
    import statlab_tools.tools.intake as intake_module

    def _never(*args: object, **kwargs: object) -> None:
        raise AssertionError("load_csv body must never run for a malformed dataset")

    monkeypatch.setattr(intake_module, "_dataset_csv_path", _never)

    async with mcp_session() as session:
        result = await session.call_tool(
            "load_csv",
            {"project": "proj", "run": run_paths_over_mcp, "dataset": bad_dataset},
        )
        assert result.is_error


@pytest.mark.asyncio
async def test_l3_well_formed_dataset_reaches_the_body(
    mcp_session: SessionFactory, run_paths_over_mcp: str, tool_settings: object
) -> None:
    """Companion to L3: a syntactically valid `dataset` DOES reach the body (and fails for the
    ordinary "not found" reason), proving the schema check is discriminating, not blanket. A
    body failure is a valid structured result at the protocol level (`is_error` stays `False`;
    chunk 07's own convention, M6) -- unlike L3's schema rejection, which IS a protocol-level
    error (`is_error=True`, confirmed by direct probe), because that failure never reaches the
    tool body at all."""
    async with mcp_session() as session:
        result = await session.call_tool(
            "load_csv",
            {"project": "proj", "run": run_paths_over_mcp, "dataset": "a" * 64},
        )
        assert result.is_error is False
        assert result.structured_content is not None
        failure = result.structured_content["result"]
        assert failure["ok"] is False
        assert failure["error_class"] == "data"


# --- L4: symlink defence in depth --------------------------------------------------------------


def test_l4_symlink_inside_datasets_pointing_outside_is_tool_param(
    seeded_run: RunPaths, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("a,b\n1,2\n")
    project = ProjectPaths(data_dir=seeded_run.data_dir, slug=seeded_run.slug)
    digest = "b" * 64
    link = project.dataset_csv(digest)
    link.symlink_to(outside)
    with pytest.raises(ToolParamError) as excinfo:
        load_csv(seeded_run, dataset=digest)
    assert "dataset" in excinfo.value.field_errors


def test_l4_symlink_inside_datasets_pointing_inside_is_fine(seeded_run: RunPaths) -> None:
    """A symlink that resolves back inside `datasets/` is not a traversal and must not be
    rejected by the containment check -- only escapes are."""
    project = ProjectPaths(data_dir=seeded_run.data_dir, slug=seeded_run.slug)
    real_digest = "c" * 64
    real_path = project.dataset_csv(real_digest)
    real_path.write_text("a,b\n1,2\n")
    link_digest = "d" * 64
    link = project.dataset_csv(link_digest)
    link.symlink_to(real_path)
    result = load_csv(seeded_run, dataset=link_digest)
    assert result.summary["rows"] == 1


# --- L5: missing file -------------------------------------------------------------------------


def test_l5_dataset_not_found_is_data(seeded_run: RunPaths) -> None:
    with pytest.raises(ToolDataError, match="not found"):
        load_csv(seeded_run, dataset="e" * 64)


# --- L6: the repair loop end to end, latin-1 ---------------------------------------------------


def test_l6_latin1_file_fails_as_utf8_then_succeeds_with_latin1(seeded_run: RunPaths) -> None:
    content = "café,x\n1,2\n".encode("latin-1")
    digest = _write_dataset(seeded_run, content)

    with pytest.raises(ToolParamError) as excinfo:
        load_csv(seeded_run, dataset=digest, encoding="utf-8")
    assert "encoding" in excinfo.value.field_errors

    result = load_csv(seeded_run, dataset=digest, encoding="latin-1")
    assert result.summary["rows"] == 1
    columns = result.summary["columns"]
    assert isinstance(columns, list)
    assert "café" in columns


# --- L7: unknown codec -------------------------------------------------------------------------


def test_l7_unknown_codec_is_tool_param(seeded_run: RunPaths, dataset_csv_bytes: bytes) -> None:
    digest = _write_dataset(seeded_run, dataset_csv_bytes)
    with pytest.raises(ToolParamError) as excinfo:
        load_csv(seeded_run, dataset=digest, encoding="not-a-real-codec")
    assert "encoding" in excinfo.value.field_errors


# --- L8: empty file ----------------------------------------------------------------------------


def test_l8_empty_file_is_data(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"")
    with pytest.raises(ToolDataError, match="empty"):
        load_csv(seeded_run, dataset=digest)


# --- L9: ragged row (overflow, per the probed pandas behaviour) --------------------------------


def test_l9_ragged_row_with_extra_fields_is_data_and_names_the_line(seeded_run: RunPaths) -> None:
    """Only a row with MORE fields than the header raises `ParserError` -- a row with fewer
    fields pads silently with NaN and is not an error at all (confirmed against pandas 3.0.5
    before writing this test)."""
    digest = _write_dataset(seeded_run, b"a,b,c\n1,2,3\n4,5,6,7\n")
    with pytest.raises(ToolDataError) as excinfo:
        load_csv(seeded_run, dataset=digest)
    assert "3" in str(excinfo.value)


def test_l9_short_row_pads_with_nan_and_does_not_raise(seeded_run: RunPaths) -> None:
    """The companion fact: a row with FEWER fields than the header does not raise at all."""
    digest = _write_dataset(seeded_run, b"a,b,c\n1,2,3\n4,5\n")
    result = load_csv(seeded_run, dataset=digest)
    assert result.summary["rows"] == 2
    assert _dict(result.summary["null_counts"])["c"] == 1


# --- L10: duplicate headers ----------------------------------------------------------------------


def test_l10_duplicate_header_names_is_data(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"a,b,a\n1,2,3\n4,5,6\n")
    with pytest.raises(ToolDataError, match="a"):
        load_csv(seeded_run, dataset=digest)


def test_l10_duplicate_header_with_header_row_offset(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"junk line here\na,b,a\n1,2,3\n4,5,6\n")
    with pytest.raises(ToolDataError, match="a"):
        load_csv(seeded_run, dataset=digest, header_row=1)


def test_l10_no_duplicate_headers_is_fine(seeded_run: RunPaths, dataset_csv_bytes: bytes) -> None:
    digest = _write_dataset(seeded_run, dataset_csv_bytes)
    result = load_csv(seeded_run, dataset=digest)
    assert result.summary["rows"] == 3


# --- L11: wrong delimiter, the repair loop end to end -------------------------------------------


def test_l11_semicolon_file_read_with_comma_is_tool_param_then_succeeds(
    seeded_run: RunPaths,
) -> None:
    digest = _write_dataset(seeded_run, b"a;b;c\n1;2;3\n4;5;6\n")

    with pytest.raises(ToolParamError) as excinfo:
        load_csv(seeded_run, dataset=digest, delimiter=",")
    assert "delimiter" in excinfo.value.field_errors
    assert ";" in excinfo.value.message

    result = load_csv(seeded_run, dataset=digest, delimiter=";")
    assert result.summary["cols"] == 3


# --- L12: genuine single column ------------------------------------------------------------------


def test_l12_genuine_single_column_is_data(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"onlycolumn\n1\n2\n3\n")
    with pytest.raises(ToolDataError, match="one column"):
        load_csv(seeded_run, dataset=digest)


# --- L13: header only ------------------------------------------------------------------------


def test_l13_header_only_file_is_data(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"a,b,c\n")
    with pytest.raises(ToolDataError, match="zero data rows"):
        load_csv(seeded_run, dataset=digest)


# --- L14: P4, keep_default_na=False honours the contract's na_values list exactly ---------------


def test_l14_none_and_nan_strings_survive_when_not_listed(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"cat,other\nNone,1\nnan,2\nfoo,3\n")
    result = load_csv(seeded_run, dataset=digest, na_values=())
    frame = load_dataframe(seeded_run.artifact(result.artifacts[0].ref, "dataframe"))
    assert frame["cat"].tolist() == ["None", "nan", "foo"]
    assert _dict(result.summary["null_counts"])["cat"] == 0


def test_l14_none_becomes_missing_when_explicitly_listed(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"cat,other\nNone,1\nnan,2\nfoo,3\n")
    result = load_csv(seeded_run, dataset=digest, na_values=("None",))
    frame = load_dataframe(seeded_run.artifact(result.artifacts[0].ref, "dataframe"))
    assert frame["cat"].isna().tolist() == [True, False, False]


# --- L15: a contract-style na_values like -999 is honoured --------------------------------------


def test_l15_custom_na_value_is_honoured(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"x,y\n1,a\n-999,b\n3,c\n")
    result = load_csv(seeded_run, dataset=digest, na_values=("-999",))
    assert _dict(result.summary["null_counts"])["x"] == 1


# --- L16: decimal comma with semicolon delimiter ------------------------------------------------


def test_l16_decimal_comma_with_semicolon_delimiter_parses_float(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"x;y\n1,5;2\n3,5;4\n")
    result = load_csv(seeded_run, dataset=digest, delimiter=";", decimal=",")
    frame = load_dataframe(seeded_run.artifact(result.artifacts[0].ref, "dataframe"))
    assert frame["x"].tolist() == [1.5, 3.5]


# --- L17: BOM --------------------------------------------------------------------------------


def test_l17_bom_is_stripped_from_first_column_name(seeded_run: RunPaths) -> None:
    content = b"\xef\xbb\xbfa,b\n1,2\n"
    digest = _write_dataset(seeded_run, content)
    result = load_csv(seeded_run, dataset=digest)
    columns = result.summary["columns"]
    assert isinstance(columns, list)
    first_column = columns[0]
    assert isinstance(first_column, str)
    assert "\ufeff" not in first_column
    assert first_column == "a"


# --- L18: row-count warning, threshold monkeypatched ---------------------------------------------


def test_l18_row_count_above_threshold_warns(
    seeded_run: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    import statlab_tools.tools.intake as intake_module

    monkeypatch.setattr(intake_module, "ROW_WARNING_THRESHOLD", 2)
    digest = _write_dataset(seeded_run, b"x,y\n1,a\n2,b\n3,c\n4,d\n")
    result = load_csv(seeded_run, dataset=digest)
    assert result.warnings
    assert any("rows" in w for w in result.warnings)


def test_l18_row_count_at_or_below_threshold_does_not_warn(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"x,y\n1,a\n2,b\n3,c\n")
    result = load_csv(seeded_run, dataset=digest)
    assert result.warnings == ()


def test_l18_default_threshold_is_the_named_constant() -> None:
    assert ROW_WARNING_THRESHOLD == 200_000


# --- L19: dates stay str -----------------------------------------------------------------------


def test_l19_dates_are_not_converted(seeded_run: RunPaths) -> None:
    digest = _write_dataset(seeded_run, b"d,v\n2024-01-01,1\n2024-06-15,2\n")
    result = load_csv(seeded_run, dataset=digest)
    assert _dict(result.summary["dtypes"])["d"] == "str"
    frame = load_dataframe(seeded_run.artifact(result.artifacts[0].ref, "dataframe"))
    assert pd.api.types.is_string_dtype(frame["d"])


# --- L20: no P1 ValueError subclass is ever classified bug --------------------------------------


@pytest.mark.parametrize(
    "content,kwargs",
    [
        (b"caf\xe9,x\n1,2\n", {"encoding": "utf-8"}),  # UnicodeDecodeError
        (b"a,b\n1,2\n", {"encoding": "not-a-real-codec"}),  # LookupError
        (b"", {}),  # EmptyDataError
        (b"a,b,c\n1,2,3\n4,5,6,7\n", {}),  # ParserError
    ],
)
def test_l20_no_p1_case_is_ever_a_bug(
    seeded_run: RunPaths, content: bytes, kwargs: dict[str, str]
) -> None:
    digest = _write_dataset(seeded_run, content)
    with pytest.raises((ToolParamError, ToolDataError)) as excinfo:
        load_csv(seeded_run, dataset=digest, **kwargs)
    # Neither exception type is ever reclassified as `bug` by the decorator -- ToolParamError
    # and ToolDataError are the only two outcomes `_classify` can assign them (tool_param, data).
    assert type(excinfo.value) in (ToolParamError, ToolDataError)


def test_l20_a_genuinely_unexpected_value_error_is_not_caught_here(
    seeded_run: RunPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reclassification is narrow by design: a `ValueError` pandas never actually raises for
    CSV reading (simulated here) is NOT one of the four named shapes, and must propagate
    unchanged -- confirming `_reclassify_read_error` doesn't swallow everything
    indiscriminately."""
    digest = _write_dataset(seeded_run, b"a,b\n1,2\n")

    def _fake_read_csv(*args: object, **kwargs: object) -> None:
        raise ValueError("a value error pandas never actually raises for this")

    monkeypatch.setattr(pd, "read_csv", _fake_read_csv)
    with pytest.raises(ValueError, match="never actually raises"):
        load_csv(seeded_run, dataset=digest)


def test_append_upload_rows(run_paths: RunPaths) -> None:
    ref = new_artifact_ref()
    save_dataframe(
        run_paths.artifact(ref, "dataframe"),
        pd.DataFrame({"t": [1, 2], "name": pd.array(["a", "b"], dtype="str"), "y": [0.0, 1.0]}),
        ref=ref,
        label="raw",
    )
    token = "0" * 32
    (run_paths.root / f"upload_{token}.csv").write_text(
        "t,extra,y\n3,z,5\n4,w,\n", encoding="utf-8"
    )
    res = append_upload_rows(run_paths, raw=ref, upload=token, target="y")
    assert res.summary == {
        "rows": 2,
        "first_row": 2,
        "missing": ["name"],
        "ignored": ["extra", "y"],
    }
    combined = load_dataframe(run_paths.artifact(res.artifacts[0].ref, "dataframe"))
    assert list(combined.index) == [0, 1, 2, 3]
    assert list(combined["t"]) == [1, 2, 3, 4]
    assert combined["y"].isna().tolist() == [False, False, True, True]
    assert isinstance(combined["name"].dtype, pd.StringDtype)
    header_only = "2" * 32
    (run_paths.root / f"upload_{header_only}.csv").write_text("t,name\n", encoding="utf-8")
    with pytest.raises(ToolDataError):
        append_upload_rows(run_paths, raw=ref, upload=header_only, target="y")
    no_shared = "3" * 32
    (run_paths.root / f"upload_{no_shared}.csv").write_text("p,q\n1,2\n", encoding="utf-8")
    with pytest.raises(ToolDataError):
        append_upload_rows(run_paths, raw=ref, upload=no_shared, target="y")
    with pytest.raises(ToolDataError):
        append_upload_rows(run_paths, raw=ref, upload="1" * 32, target="y")
