---
status: in_progress
dependencies: [54]
completion_date: null
implementation_notes: null
---

# Chunk 55 — interactive report charts, two per row

User request 2026-10-09: plots should be interactive (hover shows values), not images; show
two per row, three is too many.

Today every plot is a matplotlib PNG (`save_plot`, `mcp_server/statlab_tools/artifacts.py`),
made by four tools: `plot_curve` (evaluation.py; roc/pr/calibration/learning/
partial_dependence), `plot_distribution` and `plot_target_relation` (profiling.py),
`plot_residuals` (inference.py). `assemble_report` copies chosen plots to
`report/plots/<ref>.png`; `publish_retained_report` (backend rewind.py) copies retained ones;
the API serves `report/plots/<name>` for `a_<16 hex>.png` only. The report page shows them as
`<img>` in `.plot-grid` (`repeat(auto-fill, minmax(18rem, 1fr))`, three columns on wide panes).

Decision:
- Each plot tool also writes the data it drew as a chart spec JSON sidecar at
  `artifacts/charts/<ref>.json` (a subfolder, so `list_artifacts`, which reads files in
  `artifacts/`, is unaffected). The PNG stays (downloads, fallback, `report.md`).
- Report copies and the retained-report copy also copy the sidecar to
  `report/plots/<ref>.chart.json`; the API serves it as `application/json`.
- The report page draws charts as SVG with hover tooltips (no new dependency, like the run
  summary bars); a plot without a usable sidecar falls back to the PNG.
- Big series are thinned deterministically: lines to at most 500 points, scatters to at most
  2,000 (evenly spaced indices), with a note "showing 2000 of N points".
- `.plot-grid` is two columns, one on narrow screens.

Chart spec v1 (JSON):
`{"version": 1, "title": str, "note": str | null, "panels": [panel, ...]}`; panel:
`{"kind": "line" | "scatter" | "bar" | "box", "title": str | null, "x_label": str,
"y_label": str, "categories": [str], "series": [...], "groups": [...], "hline": float | null}`.
`line`/`scatter` series: `{"name": str, "x": [float|null], "y": [float|null]}` (equal length).
`bar`: `categories` plus series `{"name": str, "values": [float|null]}` (length = categories).
`box`: groups `{"name": str, "n": int, "low": float, "q1": float, "median": float,
"q3": float, "high": float}` (matplotlib whiskers: furthest data within 1.5 IQR).
Unused lists are `[]`.

## Stage 55a — backend chart data

Owned: new `mcp_server/statlab_tools/charts.py`, `mcp_server/statlab_tools/artifacts.py`,
`mcp_server/statlab_tools/tools/evaluation.py`, `mcp_server/statlab_tools/tools/profiling.py`,
`mcp_server/statlab_tools/tools/inference.py`, `mcp_server/statlab_tools/tools/reporting.py`,
`backend/statlab/domains/rewind.py`, `backend/statlab/api/inspect.py`, new
`tests/test_charts.py`, `tests/test_tool_artifacts.py`, `tests/test_tools_profiling_plots.py`,
`tests/test_tools_evaluation.py`, `tests/test_tools_inference.py`,
`tests/test_tools_interpretation.py`, `tests/test_tools_reporting.py`,
`tests/test_nodes_persistence.py`, `tests/test_api_inspect.py`. Preserve other edits.

1. `charts.py` (module docstring: "Chart specs (v1) for interactive report plots, written
   beside each PNG (chunk 55)."). Imports: `numpy as np`, `JsonValue` from pydantic,
   `json_scalar` from `statlab_tools.tools._frame` (if that import cycles, stop and report),
   `Sequence` from `collections.abc`, `Literal` from typing.
   - `LINE_CAP = 500`, `POINT_CAP = 2000`.
   - `def thin_indices(n: int, cap: int) -> np.ndarray`: `np.arange(n)` when `n <= cap`, else
     `np.unique(np.linspace(0, n - 1, cap).round().astype(int))`.
   - `def _floats(values: np.ndarray) -> list[JsonValue]`: `[json_scalar(float(v)) for v in values]`.
   - `def xy_series(name: str, x: np.ndarray, y: np.ndarray, cap: int) -> dict[str, JsonValue]`:
     `keep = thin_indices(len(x), cap)`; return `{"name": name, "x": _floats(x[keep]),
     "y": _floats(y[keep])}`.
   - `def bar_series(name: str, values: Sequence[float]) -> dict[str, JsonValue]`:
     `{"name": name, "values": _floats(np.asarray(values, dtype="float64"))}`.
   - `def box_group(name: str, values: np.ndarray) -> dict[str, JsonValue]`: `v` float64;
     `q1, median, q3 = np.percentile(v, [25, 50, 75])`; `iqr = q3 - q1`;
     `low = v[v >= q1 - 1.5 * iqr].min()`; `high = v[v <= q3 + 1.5 * iqr].max()`; return
     `{"name", "n": int(len(v)), "low", "q1", "median", "q3", "high"}` (floats via
     `json_scalar(float(...))`).
   - `def panel(kind: Literal["line", "scatter", "bar", "box"], *, x_label: str, y_label: str,
     title: str | None = None, categories: Sequence[str] = (), series:
     Sequence[dict[str, JsonValue]] = (), groups: Sequence[dict[str, JsonValue]] = (),
     hline: float | None = None) -> dict[str, JsonValue]`: the panel dict with lists.
   - `def chart(title: str, panels: Sequence[dict[str, JsonValue]], note: str | None = None)
     -> dict[str, JsonValue]`: `{"version": 1, "title": title, "note": note, "panels": list}`.
   - `def sample_note(shown: int, total: int) -> str | None`: `None` when equal, else
     `f"showing {shown} of {total} points"`.
2. `artifacts.py` `save_plot` gains keyword `chart: JsonValue | None = None`. After the PNG
   write succeeds and when `chart is not None`: `sidecar = path.parent / "charts" /
   f"{ref}.json"`; `sidecar.parent.mkdir(exist_ok=True)`; write
   `json.dumps(chart, allow_nan=False, separators=(",", ":"))` (utf-8); an `OSError` becomes
   `ToolArtifactError(str(sidecar), f"cannot write chart: {exc}", cause=exc)` like the PNG.
   Docstring adds one sentence about the sidecar. Return value unchanged.
3. `evaluation.py` `plot_curve`: build `panels` alongside the drawing and pass
   `chart=chart(title or f"{kind_str} curve", [panel("line", x_label=xlabel, y_label=ylabel,
   series=...)])` to `save_plot`. Series: partial dependence one `xy_series(str(name),
   x_axis, y, LINE_CAP)` per series (make `_plot_partial_dependence` also return the series
   list, i.e. return `(xlabel, ylabel, series)` and adjust its one caller); learning
   `xy_series("train", sizes, train_mean, LINE_CAP)` and `("valid", sizes, valid_mean, ...)`;
   roc/pr/calibration one `xy_series(kind_str, x, y, LINE_CAP)`.
4. `profiling.py`:
   - `_save_plot_artifact(paths, fig, *, label, chart)` passes `chart` through (both callers
     supply it).
   - `plot_distribution`: numeric: `counts, edges = np.histogram(non_null.to_numpy(),
     bins=max(bins, 1))` (same binning as `ax.hist`); categories
     `[f"{edges[i]:.4g} to {edges[i + 1]:.4g}" for i in range(len(counts))]`, series
     `[bar_series("count", counts)]`. Categorical: categories `[str(v) for v in top.index]`,
     series `[bar_series("count", top.to_numpy())]`. Panel `bar`, `x_label=str(column)`,
     `y_label="count"`; chart title `f"distribution of {column}"`; note = the cap warning
     text when levels were capped, else `None`.
   - `plot_target_relation` per branch: class x numeric `box` with
     `[box_group(str(cls), group) for cls, group in zip(classes, groups)]`, x_label target,
     y_label column; class x non-numeric `bar` with categories `[str(v) for v in levels]` and
     one `bar_series(str(cls), shares)` per class (collect each `shares` list), x_label column,
     y_label `f"share of {target}"`; continuous x numeric `scatter` with
     `xy_series(str(column), feature_series.to_numpy(dtype="float64"),
     target_series.to_numpy(dtype="float64"), POINT_CAP)`, note
     `sample_note(min(len(pair), POINT_CAP), len(pair))`; continuous x non-numeric `box` with
     `box_group(str(level), group)` per level. Chart title `f"{column} vs {target}"`; for the
     capped-levels branches the note is the cap warning text.
5. `inference.py` `plot_residuals`: chart title `f"{fitted.model} residuals"`, two panels:
   `panel("scatter", x_label="fitted values", y_label="residuals", title="residuals vs
   fitted", series=[xy_series("residuals", fitted_vals, resid, POINT_CAP)], hline=0.0)` and
   `panel("scatter", x_label="theoretical quantiles", y_label="ordered residuals",
   title="normal QQ plot", series=[xy_series("residuals", theoretical, sample, POINT_CAP)])`;
   note `sample_note(min(n, POINT_CAP), n)`.
6. `reporting.py` `_copy_plots`: after copying the PNG, `sidecar = paths.artifacts /
   "charts" / f"{plot}.json"`; when `sidecar.is_file()`, copy it to
   `plots_dir / f"{plot}.chart.json"`.
7. `rewind.py` `publish_retained_report`: inside the loop, after writing the PNG copy,
   `chart = parent.artifacts / "charts" / f"{ref}.json"`; when `chart.is_file()`, write its
   bytes to `plots / f"{ref}.chart.json"`.
8. `inspect.py`: `PLOT_NAME = re.compile(r"^a_[0-9a-f]{16}\.(?:png|chart\.json)\Z")`;
   `run_plot` returns `FileResponse(path, media_type="application/json" if
   plot.endswith(".chart.json") else "image/png")`.
9. Tests:
   - `tests/test_charts.py`: `thin_indices(10, 20)` equals `np.arange(10)`;
     `thin_indices(1000, 5).tolist() == [0, 250, 500, 749, 999]`;
     `box_group("g", np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 100.0]))` equals
     `{"name": "g", "n": 10, "low": 1.0, "q1": 3.25, "median": 5.5, "q3": 7.75, "high": 9.0}`;
     `chart("t", [panel("line", x_label="a", y_label="b", series=[xy_series("s",
     np.array([0.0, 1.0]), np.array([1.0, float("nan")]), 500)])])` equals the full dict with
     `"y": [1.0, None]`, `"note": None`, empty `categories`/`groups`, `"hline": None`,
     `"title": None` on the panel; `sample_note(5, 5) is None`;
     `sample_note(2000, 2500) == "showing 2000 of 2500 points"`.
   - `tests/test_tool_artifacts.py`: `save_plot` with `chart={"version": 1}` writes
     `path.parent / "charts" / f"{ref}.json"` whose text is `{"version":1}`; without `chart`
     no `charts` directory exists. Use the file's existing figure/ref helpers as in
     `test_a8_plot_writes_valid_png`.
   - `tests/test_tools_profiling_plots.py`: helper `_chart(run_paths, res)` loads
     `json.loads((run_paths.artifacts / "charts" / f"{res.artifacts[0].ref}.json").read_text())`.
     Frames and asserts: numeric `{"x": [1.0, 2.0, 2.0, 3.0, None]}` -> panel kind `bar`,
     `sum(values) == 4`, `len(categories) == len(values)`; categorical
     `{"c": ["a", "b", "b", None]}` -> categories `["b", "a"]`, values `[2.0, 1.0]`;
     target relation `{"x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "y": ["p", "p", "p", "q", "q", "q"]}`
     -> `box`, group names `["p", "q"]`, n `[3, 3]`; `{"c": ["a", "a", "b", "b"], "y": ["p",
     "q", "p", "p"]}` -> `bar`, `set(categories) == {"a", "b"}`, series names `["p", "q"]`;
     `{"x": [1.0, 2.0, 3.0], "y": [2.0, 4.0, 6.0]}` -> `scatter`, `series[0]["x"] == [1.0,
     2.0, 3.0]`, note `None`; 2,500 rows `x = np.arange(2500.0)`, `y = 2 * x` -> scatter x
     length 2000 and note `"showing 2000 of 2500 points"`; `{"c": ["a", "a", "b"], "y":
     [1.0, 2.0, 3.0]}` -> `box`, group names `["a", "b"]`. Use the file's `_save` and call
     `plot_distribution(run_paths, ref, column=...)` / `plot_target_relation(run_paths, ref,
     column=..., target="y")` as existing tests do. If `is_class_target` treats `y` in the
     2-row/3-row frames differently from this plan (e.g. a float target as class), stop and
     report.
   - `tests/test_tools_evaluation.py` `test_plot_curve_renders_each_kind`: for each `out`,
     the sidecar exists and `panels[0]["kind"] == "line"`; for the learning curve the series
     names are `["train", "valid"]`.
   - `tests/test_tools_interpretation.py` `test_plot_curve_renders_multiclass_partial_dependence`:
     sidecar panel kind `line` and `len(series) >= 2`.
   - `tests/test_tools_inference.py` `test_plot_residuals_renders_for_every_model`: sidecar
     panel kinds `["scatter", "scatter"]`, `panels[0]["hline"] == 0.0`.
   - `tests/test_tools_reporting.py`: in the test that asserts a copied
     `report/plots/<ref>.png`, write `run_paths.artifacts / "charts" / f"{ref}.json"`
     (`b"{}"`) for one plot before assembling and assert
     `report/plots/<ref>.chart.json` exists with `b"{}"`; a plot without a sidecar gets no
     `.chart.json`. If no such test exists, stop and report.
   - `tests/test_nodes_persistence.py`
     `test_publish_retained_report_copies_plots_and_rejects_changed_report`: also write
     `parent.artifacts / "charts" / f"{ref}.json"` (`b"{}"`, mkdir first) and assert the
     child's `report/plots/{ref}.chart.json` bytes equal `b"{}"`.
   - `tests/test_api_inspect.py` `test_run_files_and_report_plots`: write
     `RunPaths(settings.data_dir, "demo", seed.run_id).report / "plots" /
     seed.report_plot.replace(".png", ".chart.json")` with `b'{"version":1}'`; GET it:
     200, `content-type` starts with `application/json`, body equal; a `.chart.json` for an
     unknown ref is 404. (If the seed project slug is not `"demo"`, use the slug the other
     test in that file uses and report.)

Checks: `make test-fast TESTS="tests/test_charts.py tests/test_tool_artifacts.py tests/test_tools_profiling_plots.py tests/test_tools_evaluation.py tests/test_tools_inference.py tests/test_tools_interpretation.py tests/test_tools_reporting.py tests/test_nodes_persistence.py tests/test_api_inspect.py tests/test_tools_artifacts_tools.py"`;
Ruff check, Ruff format --check and mypy on every owned Python file.

### Addendum 55a-A (after implementer stop)

`is_class_target` is `not numeric or nunique <= 20`, so two planned profiling frames hit class
branches. Replace them (assertions otherwise unchanged):
- continuous x numeric scatter: `x = [float(i) for i in range(30)]`, `y = [2.0 * v for v in x]`;
  assert kind `scatter`, `series[0]["x"] == x`, note `None`.
- continuous x non-numeric box: `{"c": ["a"] * 20 + ["b"] * 10, "y": [float(i) for i in
  range(30)]}`; assert kind `box`, group names `["a", "b"]`, n `[20, 10]`; name the test for a
  continuous target with a categorical column.
Accepted deviations: `.tolist()` into `bar_series`, `hist_counts` name, note
`warnings[0] if warnings else None`, rewind copy inside `if artifact.is_file():`, second plot
without a sidecar in the reporting test (n_plots 2).

### Addendum 55a-B (import cycle)

`charts.py` must not import `statlab_tools.tools._frame` (the `tools` package `__init__`
imports the plot tools, which import `charts`). In `charts.py`: remove that import, add
`import math`, add `def _num(value: float) -> float | None:` returning `None` when
`not math.isfinite(value)`, else `value`. `_floats` becomes `[_num(float(v)) for v in values]`;
every other `json_scalar(float(...))` in `charts.py` becomes `_num(float(...))`. No other file
changes.

### Addendum 55a-C (Ruff)

Both `zip` calls in `plot_target_relation` box branches get `strict=True` (groups are built
from the same classes/levels). `charts.py` `box_group`: `"n": len(v)`. Wrap the two E501 lines
(`evaluation.py` `_plot_partial_dependence` docstring, `tests/test_api_inspect.py:256`) without
changing meaning. Then `ruff format` on the owned Python files.

### Addendum 55a-D (coverage of the sidecar write failure)

`make check` shows `artifacts.py` lines 143-144 (the sidecar `OSError` branch) uncovered. Add
to `tests/test_tool_artifacts.py`, after `test_save_plot_without_chart_creates_no_charts_directory`:
`test_save_plot_chart_write_failure_raises_tool_artifact_error(tmp_path)`. Same figure/
`_ref_path` setup; write a regular file `path.parent / "charts"` (`write_text("x")`) so the
`mkdir(exist_ok=True)` raises `FileExistsError`; assert `save_plot(path, fig, ref=ref,
chart={"version": 1})` raises `ToolArtifactError` matching `"cannot write chart"`. Checks:
`make test-fast TESTS="tests/test_tool_artifacts.py"`, Ruff check/format --check on that file.

## Stage 55b — interactive charts in the report (after 55a)

Owned: new `frontend/src/report/chart.ts`, new `frontend/src/report/Chart.tsx`, new
`frontend/src/report/PlotFigure.tsx`, `frontend/src/report/Markdown.tsx`,
`frontend/src/report/ReportView.tsx`, `frontend/src/api/client.ts`, `frontend/src/style.css`,
new `frontend/tests/chart.test.ts`, `frontend/package.json` (test script only).

1. `chart.ts` (pure, no React):
   - Types: `XYSeries = { name: string; x: (number | null)[]; y: (number | null)[] }`;
     `BarSeries = { name: string; values: (number | null)[] }`;
     `BoxGroup = { name: string; n: number; low: number; q1: number; median: number;
     q3: number; high: number }`;
     `ChartPanel = { kind: 'line' | 'scatter'; title: string | null; xLabel: string;
     yLabel: string; series: XYSeries[]; hline: number | null } | { kind: 'bar'; title: string
     | null; xLabel: string; yLabel: string; categories: string[]; series: BarSeries[] } |
     { kind: 'box'; title: string | null; xLabel: string; yLabel: string; groups: BoxGroup[] }`;
     `ChartSpec = { title: string; note: string | null; panels: ChartPanel[] }`.
   - `parseChart(value: unknown): ChartSpec | null`: null unless `isJsonObject(value)`,
     `version === 1`, string `title`, `note` string or null, `panels` a non-empty array whose
     every entry parses. Panel: `kind` one of the four, `title` string or null, string
     `x_label`/`y_label`. line/scatter: `series` array of objects with string `name` and
     arrays `x`, `y` of equal length whose items are finite numbers or null; `hline` finite
     number or null. bar: `categories` string array, `series` objects with string `name` and
     `values` (finite number or null) of length `categories.length`. box: `groups` objects
     with string `name` and finite numbers `n`, `low`, `q1`, `median`, `q3`, `high`. Anything
     else: `null`. Use `isJsonObject` from `../api/client.ts`.
   - `niceStep(raw: number): number`: `exp = 10 ** Math.floor(Math.log10(raw))`;
     `f = raw / exp`; nice = `f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10`; return `nice * exp`.
   - `niceScale(values: number[], includeZero: boolean): { min: number; max: number;
     ticks: number[] }`: keep finite values; `lo`/`hi` = their min/max, or `0`/`1` when none;
     with `includeZero` extend to include 0; when `lo === hi`, `lo -= 1; hi += 1`;
     `step = niceStep((hi - lo) / 5)`; `min = Math.floor(lo / step) * step`;
     `max = Math.ceil(hi / step) * step`; `ticks = Array.from({ length: Math.round((max - min)
     / step) + 1 }, (_, i) => Number((min + i * step).toPrecision(12)))`; return min and max
     rounded the same way.
   - `formatNumber(v: number): string`: `'0'` for 0; `v.toExponential(2)` when
     `Math.abs(v) >= 1e5 || Math.abs(v) < 1e-3`; else `String(Number(v.toPrecision(4)))`.
   - `chartRef(src: string): string | null`: the `a_<16 hex>` from `/^plots\/(a_[0-9a-f]{16})\.png$/`.
2. `client.ts`: `export async function getRunChart(runId: string, ref: string, signal?:
   AbortSignal): Promise<unknown>` returning `request(runFileUrl(runId,
   `plots/${ref}.chart.json`), { method: 'GET', signal })`.
3. `Chart.tsx`: `export default function Chart({ spec }: { spec: ChartSpec })` renders
   `<figure className="chart">` with `<figcaption className="chart-title">{spec.title}
   </figcaption>`, one `<PanelView panel={...} key={index} />` per panel, and
   `{spec.note && <p className="help">{spec.note}</p>}`.
   `PanelView` (same file):
   - Geometry: `W = 640`, `H = 380`; margins top 16, right 16, left 64, bottom 56, or 96 for a
     bar panel with more than 8 categories. Plot box `x0 = left`, `x1 = W - right`,
     `y0 = H - bottom` (bottom edge), `y1 = top`.
   - `PALETTE = ['#1F6FD1', '#E8833A', '#2A9D8F', '#C2352B', '#7B61FF', '#A85F00']`, series
     `i` uses `PALETTE[i % 6]`.
   - Scales: y from `niceScale` over all y values (line/scatter, plus `hline` when set), all
     bar values with `includeZero = true`, or all box `low`/`high`; x for line/scatter from
     `niceScale` over all x. `sx(v) = x0 + (v - xs.min) / (xs.max - xs.min) * (x1 - x0)`,
     `sy(v) = y0 - (v - ys.min) / (ys.max - ys.min) * (y0 - y1)`. Bar/box x is a band: `n`
     slots of width `(x1 - x0) / n`.
   - State `const [tip, setTip] = useState<{ x: number; y: number; lines: string[] } | null>(null)`;
     each mark gets `onMouseEnter={() => setTip({ x, y, lines })}` and
     `onMouseLeave={() => setTip(null)}` where `x`,`y` are the mark's SVG coordinates.
   - Render `<div className="chart-panel">`: optional `<p className="chart-panel-title">`;
     `<svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={panel.title ?? panel.yLabel}>`
     containing: horizontal grid lines at y ticks (`className="chart-grid"`), y tick labels
     (`x = x0 - 8`, `textAnchor="end"`, `formatNumber`), for line/scatter x tick labels below
     `y0` (`formatNumber`), for bar/box one label per band centred (when more than 8 labels:
     `transform={`rotate(-40 ${cx} ${y0 + 14})`}`, `textAnchor="end"`, text cut to 16
     characters plus `…` when longer), axis titles (`xLabel` centred at `y = H - 8`; `yLabel`
     rotated `-90` at `x = 16`), an `hline` as a dashed line (`className="chart-ref"`), then
     the marks:
     - line: per series a `<polyline fill="none" stroke={color} strokeWidth={2}>` over points
       with both x and y non-null, plus a `<circle r={3} fill={color}>` per point;
     - scatter: `<circle r={2.5} fill={color} fillOpacity={0.6}>` per point;
     - bar: per category `c` and series `j` with a non-null value `v`, a
       `<rect className="mark">` at `x = bandStart + band * 0.1 + j * w` with
       `w = band * 0.8 / series.length`, `y = sy(Math.max(v, 0))`,
       `height = Math.abs(sy(v) - sy(0))`, fill = series colour;
     - box: per group centre `cx`, a whisker `<line>` from `sy(low)` to `sy(high)`, a
       `<rect className="mark">` from `sy(q3)` to `sy(q1)` with width `band * 0.5`, fill
       `PALETTE[0]` at `fillOpacity={0.25}` and stroke `PALETTE[0]`, and a median `<line>`
       (`strokeWidth={2.5}`).
     Tooltip lines: line/scatter `[...(series.length > 1 ? [name] : []), `${xLabel}:
     ${formatNumber(x)}`, `${yLabel}: ${formatNumber(y)}`]`; bar `[category, series.length > 1
     ? `${name}: ${formatNumber(v)}` : `${yLabel}: ${formatNumber(v)}`]`; box `[name,
     `n = ${n}`, `median ${formatNumber(median)}`, `quartiles ${formatNumber(q1)} to
     ${formatNumber(q3)}`, `whiskers ${formatNumber(low)} to ${formatNumber(high)}`]`
     (hover target: the box rect and the whisker line).
   - After the svg: when `tip`, `<div className="chart-tip" style={{ left: `${(tip.x / W) *
     100}%`, top: `${(tip.y / H) * 100}%` }}>{tip.lines.map((line, i) => <div key={i}>{line}
     </div>)}</div>`; for line/scatter/bar with more than one series, a
     `<ul className="chart-legend">` with `<li><span className="swatch" style={{ background:
     color }} />{name}</li>` per series.
4. `PlotFigure.tsx`: `export default function PlotFigure({ runId, alt, src, url }: { runId:
   string; alt: string; src: string; url: string })`. `const ref = chartRef(src)`;
   `useQuery({ queryKey: ['chart', runId, ref], queryFn: ({ signal }) => getRunChart(runId,
   ref ?? '', signal), enabled: ref !== null, retry: false, staleTime: Infinity })`;
   `const spec = query.isSuccess ? parseChart(query.data) : null`. With a spec: `<div
   className="plot-figure"><Chart spec={spec} /><a className="chart-open" href={url}
   target="_blank" rel="noreferrer noopener">Open image</a></div>`; otherwise the old
   `<a href={url} target="_blank" rel="noreferrer noopener" title="Open full size"><img
   className="plot" alt={alt} src={url} /></a>`.
5. `Markdown.tsx`: prop `figure?: (fig: { alt: string; src: string; url: string }) =>
   ReactNode`. In the plot-grid branch, when `url !== null` and `figure` is given, render
   `<Fragment key={at}>{figure({ alt: fig.alt, src: fig.src, url })}</Fragment>`; all other
   cases unchanged.
6. `ReportView.tsx`: pass `figure={(fig) => <PlotFigure runId={run.id} {...fig} />}` to
   `Markdown`.
7. `style.css`: `.plot-grid` becomes `grid-template-columns: repeat(2, minmax(0, 1fr));`
   (gap/margin unchanged) and add `@media (max-width: 48rem) { .plot-grid {
   grid-template-columns: 1fr; } }`. Add:
   `.chart { margin: 0; }`
   `.chart-title, .chart-panel-title { margin: 0 0 6px; font-weight: 600; font-size: 0.875rem; }`
   `.chart-panel { position: relative; }`
   `.chart-panel + .chart-panel { margin-top: 10px; }`
   `.chart-panel svg { display: block; width: 100%; height: auto; background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius); }`
   `.chart-panel text { font-size: 12px; fill: var(--graphite); }`
   `.chart-grid { stroke: var(--rule); }`
   `.chart-ref { stroke: var(--graphite); stroke-dasharray: 4 4; }`
   `.chart-panel circle:hover, .chart-panel .mark:hover { stroke: var(--ink); stroke-width: 1.5; }`
   `.chart-tip { position: absolute; transform: translate(-50%, calc(-100% - 10px)); pointer-events: none; padding: 6px 8px; border-radius: 6px; background: var(--ink); color: var(--surface); font-size: 0.75rem; white-space: nowrap; z-index: 5; }`
   `.chart-legend { list-style: none; display: flex; flex-wrap: wrap; gap: 4px 12px; margin: 6px 0 0; padding: 0; font-size: 0.75rem; color: var(--graphite); }`
   `.chart-legend .swatch { display: inline-block; width: 10px; height: 10px; margin-right: 4px; border-radius: 2px; vertical-align: middle; }`
   `.chart-open { display: inline-block; margin-top: 4px; font-size: 0.75rem; }`
8. `frontend/tests/chart.test.ts` (append `tests/chart.test.ts` to the `test` script list in
   `package.json`): `niceScale([0.03, 0.97], false)` gives min 0, max 1, ticks
   `[0, 0.2, 0.4, 0.6, 0.8, 1]`; `niceScale([5, 5], true)` gives min 0, max 5, ticks
   `[0, 1, 2, 3, 4, 5]`; `niceScale([], false)` gives min 0, max 1; `formatNumber`: `0` ->
   `'0'`, `1234.5678` -> `'1235'`, `0.000123` -> `'1.23e-4'`, `123456` -> `'1.23e+5'`, `0.5`
   -> `'0.5'`; `chartRef('plots/a_0123456789abcdef.png')` -> `'a_0123456789abcdef'`,
   `chartRef('x.png')` -> null; `parseChart` returns the parsed spec for a valid line chart
   (`{version: 1, title: 't', note: null, panels: [{kind: 'line', title: null, x_label: 'a',
   y_label: 'b', categories: [], series: [{name: 's', x: [0, 1], y: [1, null]}], groups: [],
   hline: null}]}` -> `{title: 't', note: null, panels: [{kind: 'line', title: null, xLabel:
   'a', yLabel: 'b', series: [{name: 's', x: [0, 1], y: [1, null]}], hline: null}]}`) and
   null for version 2, for unequal x/y lengths, for an unknown kind and for a bar series whose
   length differs from `categories`.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: offline browser check of the report charts (hover, two columns), `make check`,
commit.

## Stage 55a receipt (2026-10-09)

Implementer: focused selectors exit 0, Ruff check/format and mypy on 17 files exit 0
(addenda A-D). Main reviewed the diff (box groups come from the non-null `pair`, so never
empty) and ran `make check` exit 0: coverage 100 % (TOTAL 12151 stmts, 0 missed), mypy 197
files, Ruff clean, 206 files formatted (log `check55a2.log`, temporary). The first run
(`check55a.log`) showed `artifacts.py` 143-144 uncovered, fixed by addendum D.
