---
status: untouched
dependencies: [06, 07, 08]
completion_date: null
implementation_notes: ""
---

# Chunk 09 — Profiling tools

**Goal.** The nine profiling tools from `PLAN.md` §6.3 that node 3 (`data_profiling`) uses for EDA:
`profile_schema`, `summarize_numeric`, `summarize_categorical`, `report_missingness`,
`summarize_target`, `compute_correlations`, `detect_outliers`, `plot_distribution`,
`plot_target_relation`. Five are walking-skeleton `[v1]` tools.

This chunk is where ~80 tools' worth of a cross-cutting problem gets solved once: **turning pandas
results into valid JSON.** Two probes run while drafting showed that every numeric summary written
the obvious way either fails validation or writes invalid JSON.

---

## 1. Prerequisites & Context

### State (verified, committed at `c5f23d3`)

1293 tests (1292 passed, 1 skipped by design), 100 % line and branch coverage, mypy clean on 66 files.

**`c5f23d3` changed the decorator. Read it.** `@tool`'s async wrapper was running synchronous bodies
on the event loop, so every tool call in every run was serialised — silently defeating D17. Bodies now
run in a worker thread via `anyio.to_thread.run_sync`, pinned by test T17b. Two consequences for this
chunk: tool bodies really do run **concurrently in threads**, and anything with process-global state is
now a race (see P5).

| Symbol | Location | Use here |
|---|---|---|
| `tool`, `ToolParamError`, `ToolDataError` | `statlab_tools/tool.py` | as in chunk 08 |
| `load_dataframe`, `save_json`, `save_plot` | `statlab_tools/artifacts.py` | read frames, write the correlation table and PNGs. **Two of these change (§3.2)** |
| `_iso8601_parse_share`, `CLASS_TARGET_MAX_LEVELS` | `statlab_tools/tools/intake.py` | move to the shared helper module (§3.1) |
| `describe_artifact`'s `isoformat` duck-typing | `statlab_tools/tools/artifacts.py` | replaced by the shared helper |
| `churn_frame` fixture | `tests/conftest.py` | the profiling fixture too |

### Six findings from probing before writing this spec

All verified against pandas 3.0.5, pydantic 2.13.5 and matplotlib 3.11.1.

| # | Behaviour | Consequence |
|---|---|---|
| P1 | **`ToolSuccess(summary={"v": np.int64(3)})` raises `ValidationError`.** `np.float64` passes (it subclasses `float`); `np.int64` does not subclass `int`. `Series.count()`, `.sum()`, `value_counts()` values and `.iloc[i]` on integer columns all return `np.int64` | Every count in every summary fails unless converted. `nunique()` happens to return `int`, so tests that only check `nunique` miss it |
| P2 | `pd.NA` and `pd.NaT` in a summary raise `ValidationError`; `float("nan")` and `inf` are accepted and **silently serialised as `null`** | `inf → null` loses meaning without anyone deciding it should. Convert explicitly |
| P3 | **`save_json` writes `NaN` as a bare token** — `{"r": [[1.0, NaN]]}`. That is not JSON: strict parsers, including the browser's `JSON.parse`, reject the file | Latent chunk 06 bug, first triggered here: a constant column makes `corr()` produce `NaN`, and the correlation artifact would be unreadable by the frontend (chunk 39) |
| P4 | `pd.api.types.is_numeric_dtype(bool) is True`, but `select_dtypes("number")` **excludes** bool | Two "numeric" tests that disagree. Pick one predicate and use it everywhere |
| P5 | pyplot keeps a global figure registry and is not thread-safe; `matplotlib.figure.Figure()` built directly needs no pyplot and rendered cleanly in 8 concurrent threads under `-W error`. `plt.close()` on such a figure is a harmless no-op | Since `c5f23d3`, tool bodies run in threads. Plots must not touch pyplot |
| P6 | On a constant column: `rank().corr()` gives an all-`NaN` row and column; IQR has zero width; z-score divides by `std == 0` and gives `NaN`, so every comparison is `False` and **zero outliers are reported** | A silent wrong answer. Constant columns are skipped with a warning, never computed |

Also confirmed: frame-level `df.rank().corr()` does not import scipy (chunk 08's P6 at the frame level).

### Four decisions this chunk makes

**D-a. One shared helper module, not a tool.** The chunk 08 report asked whether ISO-date parsing
should be centralised. Yes, along with JSON conversion and the numeric predicate, as plain functions in
`statlab_tools/tools/_frame.py`. They are pure, used by ≥ 3 tools, and have nothing to say to the
agent, so a tool would be the wrong shape. Producing a frame with *typed* date columns is a different
job, and it's chunk 11's `cast_dtypes`.

**D-b. Tools dispatch on dtype, not on inferred role.** `summarize_numeric` accepts numeric columns
per P4's single predicate and rejects anything else as `tool_param`. Date strings are strings, so
before `cast_dtypes` exists they profile as categorical. That's honest, if unhelpful for dates. Tools
never call `infer_column_roles` internally: the agent already has its output, and a hidden second
inference could disagree with the one it saw.

**D-c. `columns` is optional.** §6.3 writes `summarize_numeric(df, columns)`. Here it's
`columns: tuple[str, ...] | None = None`, where `None` means every applicable column. That's the common
call, and making the agent list 30 names to get it is noise.

**D-d. Summaries carry findings; artifacts carry data.** A correlation matrix is data, so it goes into
a `table` artifact and the summary holds only the strongest pairs. Outlier row indices are capped in
the summary. §6.1 rule 5 says the summary is "small, LLM-readable, never raw data". Every cap is a
named constant with a `truncated` flag beside it, so the agent knows it's seeing part of the picture.

### What this chunk does NOT do

| Not here | Where |
|---|---|
| Converting date strings to `datetime64` | 11 (`cast_dtypes`) |
| Any modification of the frame | never; profiling reads |
| Deciding which profiles matter, or the EDA narrative | 27 (node 3's agent logic) |
| `plot_curve` and the evaluation plots | 14 |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/_frame.py` — shared helpers (§3.1) |
| create | `mcp_server/statlab_tools/tools/profiling.py` — the nine tools |
| **modify** | `mcp_server/statlab_tools/tools/__init__.py` — import `profiling` |
| **modify** | `mcp_server/statlab_tools/tools/intake.py` — import moved helpers; **no behaviour change** |
| **modify** | `mcp_server/statlab_tools/tools/artifacts.py` — `describe_artifact` uses `json_scalar` |
| **modify** | `mcp_server/statlab_tools/artifacts.py` — `save_json` strict, `save_plot` pyplot-free (§3.2) |
| create | `tests/test_tools_frame_helpers.py` |
| create | `tests/test_tools_profiling_summaries.py` |
| create | `tests/test_tools_profiling_plots.py` |
| **modify** | `tests/test_tool_artifacts.py` — add the P3 regression tests |

`_frame.py` is underscore-private: it registers no tools, and `tools/__init__.py` does not import it.

---

## 3. Implementation Spec

### 3.1 `_frame.py`

```python
def json_scalar(value: object) -> JsonValue:
    """Convert one pandas/numpy cell or reduction result into a strict-JSON value.

    np.integer -> int; np.floating -> float; np.bool_ -> bool; NaN / +inf / -inf -> None;
    pd.NA, pd.NaT, None -> None; pd.Timestamp / datetime / date -> isoformat(); str, int,
    float, bool pass through. Anything else raises TypeError: a bug, surfaced loudly rather
    than str()-ed into something misleading.
    """

def json_list(values: Iterable[object]) -> list[JsonValue]: ...

def is_numeric_column(series: pd.Series) -> bool:
    """The one numeric predicate (P4): numeric dtype and not bool."""

def iso8601_parse_share(values: pd.Series) -> float:
    """Moved from intake.py unchanged."""

CLASS_TARGET_MAX_LEVELS: Final = 20   # moved from intake.py
```

Order matters inside `json_scalar`: `bool` is a subclass of `int`, and `np.bool_` must hit its own
branch before any integer check. Tests pin each conversion, `inf` and `-inf` separately.

Rule for every tool in this chunk and after: **every value placed in a summary or a JSON artifact goes
through `json_scalar` / `json_list`.** That replaces chunk 07's `isoformat` duck-typing in
`describe_artifact`.

`intake.py` imports the moved names. Its tests must pass **unchanged**. If one needs editing, the move
changed behaviour, so stop and tell me.

### 3.2 Two fixes to `statlab_tools/artifacts.py`

**`save_json`: `json.dumps(payload, allow_nan=False)`.** A payload containing `NaN` or `inf` then
raises `ValueError`, which the decorator reports as `bug`. That's right: the caller should have used
`json_scalar`. Strict JSON is what the frontend parses. Regression tests: `NaN` raises; `None` writes
`null`; the written file loads under a parser that rejects non-standard tokens.

**`save_plot`: remove the pyplot import and `plt.close` call.** Tools build `matplotlib.figure.Figure()`
directly (P5), so nothing is ever in pyplot's registry and closing it is a no-op that drags in
thread-unsafe global state. The existing test A9 ("no open figures afterwards") still holds. Add one
assertion that `matplotlib.pyplot` is not imported by `statlab_tools.artifacts` or
`tools/profiling.py`, using a source scan.

### 3.3 The nine tools

All: `phase="profiling"`, `allow_in=["data_profiling"]`, `needs_run=True`. Each takes
`frame: Annotated[ArtifactRef, Field(description=...)]` for the input dataframe, read with
`load_dataframe(paths.artifact(frame, "dataframe"))`.

Shared validation: a `columns` entry not in the frame → `ToolParamError(field_errors={"columns": ...})`
listing every unknown name in one message, not just the first.

**Constant columns** (one distinct non-null value, or all null) are skipped by every statistic that
divides by spread, and listed in the summary's `skipped` with a reason. They're never silently computed
(P6).

| Tool | Parameters beyond `frame` | Summary | Artifact |
|---|---|---|---|
| `profile_schema` | — | `rows`, `cols`, `memory_bytes` (deep); per column `dtype`, `n_unique`, `n_null` | — |
| `summarize_numeric` | `columns` | per column `count`, `mean`, `std`, `min`, `q25`, `median`, `q75`, `max`, `skew`; `skipped` | — |
| `summarize_categorical` | `columns`, `top_k: int = 10` (1..50) | per column `n_unique`, `top` (`[{"value", "count", "share"}]`, ≤ `top_k`), `truncated`; `skipped` | — |
| `report_missingness` | — | per column `n_null`, `share` (columns with nulls only); `complete_rows`; `rows_with_any_missing`; `co_missing` pairs | — |
| `summarize_target` | `target: str` | see below | — |
| `compute_correlations` | `columns`, `method: Literal["pearson", "spearman"] = "pearson"` | `strongest` (top `CORRELATION_TOP_PAIRS = 10` by \|r\|, each `{"a", "b", "r"}`); `skipped` | `table` |
| `detect_outliers` | `columns`, `method: Literal["iqr", "zscore"] = "iqr"` | per column `count`, `share`, `rows` (≤ `OUTLIER_ROWS_CAP = 100` row labels), `truncated`; `skipped` | — |
| `plot_distribution` | `column: str` | `kind` (`histogram` / `bar`), `shown_levels`, `total_levels` | `plot` |
| `plot_target_relation` | `column: str`, `target: str` | `kind` (`box` / `bars` / `scatter`) | `plot` |

Details that matter:

- **`summarize_numeric`** rejects non-numeric columns named explicitly (`tool_param`). With
  `columns=None` it takes every `is_numeric_column` column, so a frame with none returns an empty
  result, not an error. `skew` comes from pandas' `.skew()`. It's `NaN` below 3 values, and
  `json_scalar` turns that into `null`.
- **`summarize_categorical`** applies to string, category and bool columns (`not is_numeric_column`).
  Shares are of non-null rows.
- **`report_missingness` `co_missing`**: for each pair of columns that both have nulls, the Jaccard
  overlap of their null masks. Pairs at or above `CO_MISSING_JACCARD = 0.8` are listed, sorted
  descending. This is what makes "these three columns are missing together" visible, the
  usual sign of a structural cause such as an optional form section.
- **`summarize_target`**: target not in frame → `tool_param`. If `is_numeric_column` and more than
  `CLASS_TARGET_MAX_LEVELS` distinct values, it's **continuous**: `summarize_numeric`'s statistics plus
  `n_null`. Otherwise it's a **class** target: per class `count` and `share`, `minority_share`,
  `imbalance_ratio` (majority / minority), `n_null`. Warn when `minority_share < IMBALANCE_WARNING
  = 0.1`. This is the number that later justifies class weighting (§4.2's worked example).
- **`compute_correlations`**: numeric columns only (`tool_param` for others named explicitly; fewer than
  two applicable → `ToolDataError`). `spearman` is `df.rank().corr()`, never `method="spearman"`
  (scipy). The artifact is `{"method", "columns": [...], "matrix": [[...]]}` with every cell through
  `json_scalar`, so a constant column's `NaN`s become `null`. Constant columns are excluded from the
  matrix and listed in `skipped`. `strongest` excludes the diagonal and counts each pair once.
- **`detect_outliers`**: `iqr` flags values outside `[q1 − k·IQR, q3 + k·IQR]`, with `IQR_K = 1.5`.
  `zscore` flags `|z| > ZSCORE_THRESHOLD = 3.0`. Row labels are the frame's index labels through
  `json_scalar`. The result reports. It never removes rows; `fit_outlier_clipper` (chunk 11) acts.
- **`plot_distribution`**: numeric → histogram with `bins = min(HIST_MAX_BINS = 30, n_unique)`.
  Otherwise → bar chart of value counts, capped at `PLOT_MAX_CATEGORIES = 50` (D15) by frequency,
  with a warning `"showing 50 of 312 levels"` when truncated. An all-null column → `ToolDataError`.
- **`plot_target_relation`**: class target (≤ `CLASS_TARGET_MAX_LEVELS` levels) with a numeric
  feature → box plot per class. Class target with a non-numeric feature → grouped bars of the class
  share per feature level, top 50 levels. Continuous target with a numeric feature → scatter.
  Continuous target with a non-numeric feature → box plot per level, top 50. `column == target` →
  `tool_param`.

**Plots**: `Figure(figsize=(8, 5), dpi=100)`, `fig.add_subplot()`, axis labels set to the column names,
title stating what is shown, `fig.tight_layout()`, then `save_plot`. No `pyplot` anywhere (P5).
**Pass `parse_math=False` to every text call**: `set_xlabel`, `set_ylabel`, `set_title`, and tick labels
via `ax.set_xticks(positions, labels, parse_math=False)`. Column names and category values are user
data, and matplotlib otherwise reads paired `$` as mathtext. Probed: `cost $ in $ usd` renders silently
mangled (the middle in italics, spaces dropped), and `$\frac$` raises `ValueError` mid-render. With
`parse_math=False` both render verbatim. Don't strip or escape characters: the label should be exactly
the column name.

`json_scalar` also applies to `plot_*` summaries.

### 3.4 Strict-JSON property test

One test, over every tool in this chunk and the five intake tools, on two frames (`churn_frame` and
`messy_frame`, defined below): each `ToolSuccess` must survive
`json.dumps(result.model_dump(mode="json"), allow_nan=False)`, and every JSON artifact written must
load under a strict parser. This is the test that proves P1–P3 are solved across the catalogue
instead of in the five places someone remembered.

---

## 4. Test Suite Spec

Target **~140 new tests**, total ≥ **1433**.

### 4.1 Fixtures

```python
@pytest.fixture
def messy_frame() -> pd.DataFrame:
    """Built to trip every probe: an int column with nulls (np.int64 counts, P1); a constant
    numeric column (P6); a bool column (P4); a column named 'price ($)' (mathtext); two columns
    missing on exactly the same rows (co_missing); a numeric column with three planted
    outliers at known row labels; a categorical with 60 levels (plot cap), one of whose values
    is the string '$\\frac$' (mathtext crash); a column named 'cost $ in $ usd' (silent
    mathtext mangling); a datetime64[us] column (Timestamp conversion)."""
```

### 4.2 `test_tools_frame_helpers.py` (~25)

`json_scalar`, parametrised over `np.int64`, `np.int32`, `np.float64`, `np.bool_`, `True`, `nan`,
`inf`, `-inf`, `pd.NA`, `pd.NaT`, `None`, `pd.Timestamp`, `datetime.date`, `str`. Check both the output
value and its exact `type`, since `True` must not become `1`. An unsupported object (`object()`) raises
`TypeError`. `is_numeric_column` is true for int/float, and false for bool, str, category and datetime.
The moved helpers are importable from `_frame` and no longer defined in `intake.py`.

### 4.3 `test_tools_profiling_summaries.py` (~75)

Per tool: happy path on `churn_frame`; the `messy_frame` hazard it is exposed to; unknown column →
`tool_param` naming **all** unknowns; wrong-dtype column named explicitly → `tool_param`;
`columns=None` selection. Plus:

| # | Test | Expect |
|---|---|---|
| S1 | **P1** | every count in every summary is `int`, not `np.int64`. Assert on `type()` |
| S2 | **P6** constant column | in `skipped` with a reason, for `summarize_numeric`, `compute_correlations`, both `detect_outliers` methods. Never "0 outliers" |
| S3 | **P4** bool column | excluded from `summarize_numeric` with `columns=None`; included in `summarize_categorical` |
| S4 | outliers | the three planted labels are exactly what `iqr` reports |
| S5 | outlier cap | 150 planted outliers → `rows` has 100 entries, `truncated` true, `count` 150 |
| S6 | `co_missing` | the two co-missing columns appear with Jaccard 1.0; an independent pair does not |
| S7 | `summarize_target` class | `churn_frame`'s `churned` gives shares summing to 1, the correct minority share and ratio |
| S8 | imbalance warning | 5 % minority warns; 30 % does not |
| S9 | `summarize_target` continuous | a 100-distinct numeric target takes the continuous branch |
| S10 | correlation artifact | written as a `table`; strict-JSON parseable; `matrix` square and symmetric; diagonal 1.0 |
| S11 | `spearman` | rank correlation of a monotone non-linear pair is 1.0 where pearson is not; `"scipy" not in sys.modules` after the call **in a subprocess** (the test session may have imported it elsewhere) |
| S12 | `strongest` | no self-pairs, no duplicate pairs, sorted by \|r\| |
| S13 | fewer than two numeric columns | `data` |
| S14 | §3.4 strict-JSON property | all 14 tools × 2 frames |

### 4.4 `test_tools_profiling_plots.py` (~30)

PNG signature on every plot; `kind` correct for each of the four `plot_target_relation` branches and
both `plot_distribution` branches; 60-level categorical → `shown_levels == 50`, `total_levels == 60`,
warning present; `cost $ in $ usd` and a `$\frac$` category value render (they crash or mangle without `parse_math=False`); all-null column → `data`; `column == target` → `tool_param`;
**8 plots rendered concurrently through a real `mcp_session`** all succeed (P5, now that bodies are
threaded); `matplotlib.pyplot` absent from `profiling.py` and `statlab_tools/artifacts.py` by source
scan.

### 4.5 `test_tool_artifacts.py` additions (~5)

`save_json` with `NaN` raises; with `inf` raises; `None` writes `null`; output loads under a strict
parser; `save_plot` still leaves `plt.get_fignums()` empty.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **1433**.
- [ ] Coverage **100 %** line and branch; project stays 100 %.
- [ ] §3.4's strict-JSON property test covers all 14 tools on both frames.
- [ ] Intake tests pass **without edits** after the helper move.
- [ ] The concurrent-plot test runs through a real `mcp_session`.

**Static analysis**
- [ ] `make typecheck`, `make lint` exit 0.
- [ ] No `Any`, no `pragma: no cover`, `# type: ignore` only in the four permitted shapes.
- [ ] No `dtype == object`, no `method="spearman"`, no `matplotlib.pyplot` in `tools/` or
      `statlab_tools/artifacts.py`. Every text call in `profiling.py` passes `parse_math=False`.

**Behaviour**
- [ ] `save_json` refuses `NaN`/`inf`.
- [ ] No tool reports a statistic computed on a constant column.
- [ ] Every cap has a named constant and a `truncated` flag or warning.

**Housekeeping**
- [ ] `00_index.md` pandas block gains P1, P3, P4 as one-liners. Every later tool chunk needs them.
- [ ] `WHATS_NEXT.md` conventions: "every value entering a summary or JSON artifact goes through
      `json_scalar`".
- [ ] Frontmatter, `00_index.md` chunk 09 row, `WHATS_NEXT.md` state table. Do not commit.

### Stop and ask if

- Any of P1–P6 doesn't reproduce.
- An intake test needs editing after the helper move.
- `save_json` going strict breaks an existing caller. That means some tool already writes `NaN`,
  and I want to know which.
- A plot can't be rendered without pyplot.

### Report back with

1. Test count, coverage, mypy file count, ruff.
2. Which existing call sites `json_scalar` replaced, and whether any was already producing `np.int64`
   in a summary without a test noticing.
3. How the concurrent-plot test proves concurrency rather than just completion. T17b's barrier is the
   model.
4. `compute_correlations`' artifact for `messy_frame`, verbatim, so I can see how the constant column
   is handled.
5. Any threshold you'd change after seeing the tests, and why. "None" is fine if true.
6. Whether D-b (dispatch on dtype, not role) made any tool's output misleading for date-string columns.
7. For chunk 10 (splitting): `verify_split` compares class proportions across three frames. Is
   `summarize_target`'s class-share logic reusable there as a helper, or is it shaped wrong for that?
