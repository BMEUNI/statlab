"""`plot_distribution`, `plot_target_relation`. Chunk 09 section 3.3, section 4.4.

PNG-signature checks, the four `plot_target_relation` branch kinds, the two `plot_distribution`
branches, the D15 category cap, the mathtext hazards (P5's `parse_math=False` requirement), and
the concurrent-plot test that proves bodies overlap in time (T17b's barrier is the model).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd
import pytest
import statlab_tools.artifacts as mcp_artifacts
import statlab_tools.tools.profiling as profiling
from pydantic import JsonValue
from statlab_contracts.artifacts import ArtifactRef, new_artifact_ref
from statlab_contracts.envelope import ToolSuccess
from statlab_contracts.layout import RunPaths
from statlab_tools.artifacts import save_dataframe
from statlab_tools.tool import ToolDataError, ToolParamError
from statlab_tools.tools.profiling import plot_distribution, plot_target_relation

if TYPE_CHECKING:
    from mcp import ClientSession
    from mcp.types import CallToolResult

SessionFactory = Callable[[], AbstractAsyncContextManager["ClientSession"]]

pytestmark = pytest.mark.usefixtures("tool_settings")

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _save(run_paths: RunPaths, frame: pd.DataFrame) -> ArtifactRef:
    ref = new_artifact_ref()
    save_dataframe(run_paths.artifact(ref, "dataframe"), frame, ref=ref)
    return ref


def _png_bytes(run_paths: RunPaths, ref: ArtifactRef) -> bytes:
    return run_paths.artifact(ref, "plot").read_bytes()


# --- plot_distribution -------------------------------------------------------------------------


def test_plot_distribution_numeric_writes_valid_png(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = plot_distribution(run_paths, ref, column="age")
    assert result.summary["kind"] == "histogram"
    assert len(result.artifacts) == 1
    meta = result.artifacts[0]
    assert meta.kind == "plot"
    assert _png_bytes(run_paths, meta.ref)[:8] == _PNG_SIGNATURE


def test_plot_distribution_categorical_writes_valid_png(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = plot_distribution(run_paths, ref, column="plan")
    assert result.summary["kind"] == "bar"
    meta = result.artifacts[0]
    assert _png_bytes(run_paths, meta.ref)[:8] == _PNG_SIGNATURE


def test_plot_distribution_numeric_shown_levels_equals_total_for_small_cardinality(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"n": [1, 2, 3, 4, 5] * 10})
    ref = _save(run_paths, frame)
    result = plot_distribution(run_paths, ref, column="n")
    assert result.summary["shown_levels"] == result.summary["total_levels"] == 5


def test_plot_distribution_60_level_categorical_caps_at_50_with_warning(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = plot_distribution(run_paths, ref, column="many_levels")
    assert result.summary["shown_levels"] == 50
    assert result.summary["total_levels"] == 60
    assert any("50 of 60" in w for w in result.warnings)


def test_plot_distribution_no_warning_when_under_cap(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = plot_distribution(run_paths, ref, column="plan")
    assert result.warnings == ()


def test_plot_distribution_all_null_column_is_data_error(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"c": [None, None, None]})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolDataError):
        plot_distribution(run_paths, ref, column="c")


def test_plot_distribution_unknown_column_is_tool_param(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    with pytest.raises(ToolParamError):
        plot_distribution(run_paths, ref, column="nope")


def test_plot_distribution_renders_mathtext_shaped_column_name(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    """Without `parse_math=False`, matplotlib reads the paired `$` in this column's own name
    as mathtext and mangles the label silently. This test confirms the call succeeds and
    writes a real PNG rather than crashing or raising."""
    ref = _save(run_paths, messy_frame)
    result = plot_distribution(run_paths, ref, column="price ($)")
    meta = result.artifacts[0]
    assert _png_bytes(run_paths, meta.ref)[:8] == _PNG_SIGNATURE


def test_plot_distribution_renders_second_mathtext_shaped_column_name(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, messy_frame)
    result = plot_distribution(run_paths, ref, column="cost $ in $ usd")
    meta = result.artifacts[0]
    assert _png_bytes(run_paths, meta.ref)[:8] == _PNG_SIGNATURE


def test_plot_distribution_warnings_empty_tuple_not_none_when_no_cap_hit(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"n": [1.0, 2.0, 3.0]})
    ref = _save(run_paths, frame)
    result = plot_distribution(run_paths, ref, column="n")
    assert result.warnings == ()


def test_plot_distribution_artifact_kind_is_plot(
    run_paths: RunPaths, churn_frame_fixture: pd.DataFrame
) -> None:
    ref = _save(run_paths, churn_frame_fixture)
    result = plot_distribution(run_paths, ref, column="age")
    assert result.artifacts[0].kind == "plot"


def test_plot_distribution_renders_category_value_that_would_crash_mathtext(
    run_paths: RunPaths, messy_frame: pd.DataFrame
) -> None:
    """`messy_frame`'s `many_levels` column has the literal string `"$\\frac$"` as one of its
    60 values -- without `parse_math=False` on the tick labels, matplotlib raises mid-render
    trying to parse it as an incomplete `\\frac{}{}` command. This must not raise."""
    ref = _save(run_paths, messy_frame)
    result = plot_distribution(run_paths, ref, column="many_levels")
    meta = result.artifacts[0]
    assert _png_bytes(run_paths, meta.ref)[:8] == _PNG_SIGNATURE


# --- plot_target_relation -----------------------------------------------------------------------


@pytest.fixture
def relation_frame() -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(2026)
    n = 80
    return pd.DataFrame(
        {
            "class_target": rng.choice([0, 1], size=n, p=[0.6, 0.4]),
            "cont_target": rng.normal(size=n),
            "num_feature": rng.normal(size=n),
            "cat_feature": rng.choice(["a", "b", "c"], size=n),
        }
    )


def test_plot_target_relation_class_target_numeric_feature_is_box(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    result = plot_target_relation(run_paths, ref, column="num_feature", target="class_target")
    assert result.summary["kind"] == "box"
    assert _png_bytes(run_paths, result.artifacts[0].ref)[:8] == _PNG_SIGNATURE


def test_plot_target_relation_class_target_categorical_feature_is_bars(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    result = plot_target_relation(run_paths, ref, column="cat_feature", target="class_target")
    assert result.summary["kind"] == "bars"
    assert _png_bytes(run_paths, result.artifacts[0].ref)[:8] == _PNG_SIGNATURE


def test_plot_target_relation_continuous_target_numeric_feature_is_scatter(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    result = plot_target_relation(run_paths, ref, column="num_feature", target="cont_target")
    assert result.summary["kind"] == "scatter"
    assert _png_bytes(run_paths, result.artifacts[0].ref)[:8] == _PNG_SIGNATURE


def test_plot_target_relation_continuous_target_categorical_feature_is_box(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    result = plot_target_relation(run_paths, ref, column="cat_feature", target="cont_target")
    assert result.summary["kind"] == "box"
    assert _png_bytes(run_paths, result.artifacts[0].ref)[:8] == _PNG_SIGNATURE


def test_plot_target_relation_artifact_kind_is_plot(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    result = plot_target_relation(run_paths, ref, column="num_feature", target="class_target")
    assert result.artifacts[0].kind == "plot"


def test_plot_target_relation_column_equals_target_is_tool_param(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    with pytest.raises(ToolParamError):
        plot_target_relation(run_paths, ref, column="class_target", target="class_target")


def test_plot_target_relation_unknown_column_is_tool_param(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    with pytest.raises(ToolParamError):
        plot_target_relation(run_paths, ref, column="nope", target="class_target")


def test_plot_target_relation_unknown_target_is_tool_param(
    run_paths: RunPaths, relation_frame: pd.DataFrame
) -> None:
    ref = _save(run_paths, relation_frame)
    with pytest.raises(ToolParamError):
        plot_target_relation(run_paths, ref, column="num_feature", target="nope")


def test_plot_target_relation_class_categorical_caps_levels_with_warning(
    run_paths: RunPaths,
) -> None:
    import numpy as np

    rng = np.random.default_rng(3)
    n = 400
    frame = pd.DataFrame(
        {
            "t": rng.choice([0, 1], size=n),
            "many": [f"L{i % 60}" for i in range(n)],
        }
    )
    ref = _save(run_paths, frame)
    result = plot_target_relation(run_paths, ref, column="many", target="t")
    assert any("50 of 60" in w for w in result.warnings)


def test_plot_target_relation_continuous_target_categorical_feature_caps_levels_with_warning(
    run_paths: RunPaths,
) -> None:
    """The fourth branch (continuous target, non-numeric feature) has its own top-50 cap,
    separate from the class-target+categorical branch above -- both must warn independently."""
    import numpy as np

    rng = np.random.default_rng(4)
    n = 400
    frame = pd.DataFrame(
        {
            "t": rng.normal(size=n),
            "many": [f"L{i % 60}" for i in range(n)],
        }
    )
    ref = _save(run_paths, frame)
    result = plot_target_relation(run_paths, ref, column="many", target="t")
    assert result.summary["kind"] == "box"
    assert any("50 of 60" in w for w in result.warnings)


def test_plot_target_relation_no_overlapping_rows_is_data_error(run_paths: RunPaths) -> None:
    frame = pd.DataFrame({"t": [0, None, None], "f": [None, 1.0, 2.0]})
    ref = _save(run_paths, frame)
    with pytest.raises(ToolDataError):
        plot_target_relation(run_paths, ref, column="f", target="t")


# --- source scan: no pyplot in production plotting code -----------------------------------------


def test_no_pyplot_import_in_profiling_module() -> None:
    """A real import statement, not the word "pyplot" anywhere -- both this module's own
    docstring and `_new_figure`'s docstring explain the rule in prose, which must not trip the
    check meant to catch an actual `import matplotlib.pyplot`."""
    import re

    source_path = Path(profiling.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+matplotlib\.pyplot\b", source, re.MULTILINE)
    assert not re.search(r"^\s*import\s+matplotlib\s*$", source, re.MULTILINE)


def test_no_pyplot_import_in_artifacts_module() -> None:
    import re

    source_path = Path(mcp_artifacts.__file__)
    source = source_path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+matplotlib\.pyplot\b", source, re.MULTILINE)


def test_every_text_call_in_profiling_passes_parse_math_false() -> None:
    """Source-level check: every `set_xlabel`/`set_ylabel`/`set_title`/`set_xticks` call in
    `profiling.py` must pass `parse_math=False` -- column names and category values are user
    data, and this is the one convention a missed call site would silently violate without any
    test ever noticing (a plain English label renders identically with or without it).

    Matched by balancing parens by hand rather than a single non-greedy regex: `set_xticks`
    calls nest a list comprehension in their second argument, which a `[^)]*` pattern would
    truncate at the comprehension's own close-paren rather than the call's.
    """
    source = Path(profiling.__file__).read_text(encoding="utf-8")
    calls: list[str] = []
    for method in ("set_xlabel", "set_ylabel", "set_title", "set_xticks"):
        for match in re.finditer(rf"\.{method}\(", source):
            start = match.end() - 1
            depth = 0
            end = start
            for i in range(start, len(source)):
                if source[i] == "(":
                    depth += 1
                elif source[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            calls.append(source[match.start() : end + 1])
    assert calls, "no text calls found -- the matching logic itself may be wrong"
    for call in calls:
        assert "parse_math=False" in call, f"missing parse_math=False in: {call}"


# --- concurrency: bodies genuinely overlap, not merely both finish (T17b's model) ---------------


@pytest.mark.asyncio
async def test_eight_concurrent_plots_overlap_in_time(
    run_paths_over_mcp: str,
    mcp_session: SessionFactory,
    tool_settings: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P5, now that tool bodies run in worker threads (`c5f23d3`): eight real
    `plot_distribution` calls over a real MCP session must be able to render at the same time
    without touching pyplot's thread-unsafe global registry. Completion alone (T17's own
    shape) is exactly what let the pre-`c5f23d3` serialisation bug through for a whole chunk --
    this uses an 8-party `threading.Barrier`, the same mechanism as
    `test_tool_decorator.py`'s T17b: each call only proceeds past `_new_figure` once the
    barrier releases, which happens only when all 8 are simultaneously inside their bodies. A
    serialised implementation would deadlock on the barrier's timeout, turning the bug into a
    hard failure rather than a silently-passing test. Patches `profiling._new_figure` itself
    (a function this module owns, with a known type) rather than a matplotlib method, so no
    `# type: ignore` is needed to make the patch mypy-clean.
    """
    import anyio
    from statlab_tools.settings import ToolSettings

    assert isinstance(tool_settings, ToolSettings)
    paths = RunPaths(data_dir=tool_settings.data_dir, slug="proj", run_id=run_paths_over_mcp)
    frame = pd.DataFrame({"n": list(range(50))})
    ref = _save(paths, frame)

    n_parties = 8
    barrier = threading.Barrier(n_parties, timeout=5)
    original_new_figure = profiling._new_figure

    def barrier_new_figure() -> tuple[object, object]:
        barrier.wait()
        return original_new_figure()

    monkeypatch.setattr(profiling, "_new_figure", barrier_new_figure)

    results: list[CallToolResult] = []
    async with mcp_session() as session:

        async def run_and_collect() -> None:
            results.append(
                await session.call_tool(
                    "plot_distribution",
                    {"project": "proj", "run": run_paths_over_mcp, "frame": ref, "column": "n"},
                )
            )

        async with anyio.create_task_group() as tg:
            for _ in range(n_parties):
                tg.start_soon(run_and_collect)

    assert not barrier.broken
    assert len(results) == n_parties
    for result in results:
        assert not result.is_error


def _chart(run_paths: RunPaths, res: ToolSuccess) -> dict[str, JsonValue]:
    text = (run_paths.artifacts / "charts" / f"{res.artifacts[0].ref}.json").read_text(
        encoding="utf-8"
    )
    return cast("dict[str, JsonValue]", json.loads(text))


def test_plot_distribution_numeric_chart_is_histogram_bars(run_paths: RunPaths) -> None:
    ref = _save(run_paths, pd.DataFrame({"x": [1.0, 2.0, 2.0, 3.0, None]}))
    res = plot_distribution(run_paths, ref, column="x")
    panel = cast("list[dict[str, JsonValue]]", _chart(run_paths, res)["panels"])[0]
    assert panel["kind"] == "bar"
    series = cast("list[dict[str, JsonValue]]", panel["series"])[0]
    values = cast("list[float]", series["values"])
    assert sum(values) == 4
    assert len(cast("list[str]", panel["categories"])) == len(values)


def test_plot_distribution_categorical_chart_orders_levels_by_count(
    run_paths: RunPaths,
) -> None:
    ref = _save(run_paths, pd.DataFrame({"c": ["a", "b", "b", None]}))
    res = plot_distribution(run_paths, ref, column="c")
    panel = cast("list[dict[str, JsonValue]]", _chart(run_paths, res)["panels"])[0]
    assert panel["categories"] == ["b", "a"]
    series = cast("list[dict[str, JsonValue]]", panel["series"])[0]
    assert series["values"] == [2.0, 1.0]


def test_plot_target_relation_class_target_numeric_feature_chart_is_box(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": ["p", "p", "p", "q", "q", "q"]})
    ref = _save(run_paths, frame)
    res = plot_target_relation(run_paths, ref, column="x", target="y")
    panel = cast("list[dict[str, JsonValue]]", _chart(run_paths, res)["panels"])[0]
    assert panel["kind"] == "box"
    groups = cast("list[dict[str, JsonValue]]", panel["groups"])
    assert [g["name"] for g in groups] == ["p", "q"]
    assert [g["n"] for g in groups] == [3, 3]


def test_plot_target_relation_class_target_categorical_feature_chart_is_bars(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"c": ["a", "a", "b", "b"], "y": ["p", "q", "p", "p"]})
    ref = _save(run_paths, frame)
    res = plot_target_relation(run_paths, ref, column="c", target="y")
    panel = cast("list[dict[str, JsonValue]]", _chart(run_paths, res)["panels"])[0]
    assert panel["kind"] == "bar"
    assert set(cast("list[str]", panel["categories"])) == {"a", "b"}
    series = cast("list[dict[str, JsonValue]]", panel["series"])
    assert [s["name"] for s in series] == ["p", "q"]


def test_plot_target_relation_continuous_target_chart_is_scatter(run_paths: RunPaths) -> None:
    x = [float(i) for i in range(30)]
    y = [2.0 * v for v in x]
    ref = _save(run_paths, pd.DataFrame({"x": x, "y": y}))
    res = plot_target_relation(run_paths, ref, column="x", target="y")
    spec = _chart(run_paths, res)
    panel = cast("list[dict[str, JsonValue]]", spec["panels"])[0]
    assert panel["kind"] == "scatter"
    series = cast("list[dict[str, JsonValue]]", panel["series"])[0]
    assert series["x"] == x
    assert spec["note"] is None


def test_plot_target_relation_scatter_chart_thins_to_point_cap(run_paths: RunPaths) -> None:
    x = np.arange(2500.0)
    ref = _save(run_paths, pd.DataFrame({"x": x, "y": 2 * x}))
    res = plot_target_relation(run_paths, ref, column="x", target="y")
    spec = _chart(run_paths, res)
    panel = cast("list[dict[str, JsonValue]]", spec["panels"])[0]
    series = cast("list[dict[str, JsonValue]]", panel["series"])[0]
    assert len(cast("list[float]", series["x"])) == 2000
    assert spec["note"] == "showing 2000 of 2500 points"


def test_plot_target_relation_continuous_target_categorical_column_chart_is_box(
    run_paths: RunPaths,
) -> None:
    frame = pd.DataFrame({"c": ["a"] * 20 + ["b"] * 10, "y": [float(i) for i in range(30)]})
    ref = _save(run_paths, frame)
    res = plot_target_relation(run_paths, ref, column="c", target="y")
    panel = cast("list[dict[str, JsonValue]]", _chart(run_paths, res)["panels"])[0]
    assert panel["kind"] == "box"
    groups = cast("list[dict[str, JsonValue]]", panel["groups"])
    assert [g["name"] for g in groups] == ["a", "b"]
    assert [g["n"] for g in groups] == [20, 10]
