---
status: completed
dependencies: [07, 08, 09, 10]
completion_date: 2026-10-01
implementation_notes: |
  Ten tools in cleaning.py; fitted state is a frozen dataclass (FittedImputer/FittedClipper)
  pickled as kind transformer. P1/P2/P4 reproduced exactly. One pandas 3.0 fact NOT in P1-P7:
  pd.Categorical built with a dtype whose categories do not cover a non-null value raises a
  Pandas4Warning (-> error under -W error); cast_dtypes' ordinal branch maps out-of-level values
  to None before construction. is_string_dtype(Categorical) is True, so _is_str_column excludes
  categorical explicitly (constant on a categorical must be tool_param, P2). Review corrections
  (2026-10-01, see §6): cast_dtypes gained a required `target` (never cast; an agent role for it
  is warned and ignored); a shared `_as_str` helper routes the string/ordinal/boolean casts
  through Int64 so integral floats do not stringify as "1.0"; apply_outlier_clipper casts to
  float64 before clip_iqr/winsorize and emits a plain-bool flag column. After corrections: 1627
  passed, 1 skipped; cleaning.py 100% line+branch; mypy --strict clean on 77 files; ruff clean.
  All 8 DoD mutations caught. No sklearn, no reset_index, no backend/contracts change beyond the
  one-line transformer comment.
---

# Chunk 11 — Cleaning tools

**Goal.** The ten cleaning tools from `PLAN.md` §6.3, three of them walking-skeleton `[v1]`
(`drop_columns`, `fit_imputer`, `apply_imputer`). They fall into three groups, by when they may
run:

| Group | Tools | Node |
|---|---|---|
| Pre-split, structural (D34d) | `drop_duplicate_rows`, `drop_high_missing_rows`, `cast_dtypes` | 4 `data_splitting` (`cast_dtypes` also 3) |
| Decide on train | `drop_constant_columns`, `drop_high_missing_columns`, `fit_imputer`, `fit_outlier_clipper` | 7 `preprocessing` |
| Apply to any frame | `drop_columns`, `apply_imputer`, `apply_outlier_clipper` | 4, 7, and 12 `champion_selection` (the champion's pipeline replayed on test, D32b) |

M4 is completed by chunk 12 (encoding).

---

## 1. Prerequisites & Context

### State (verified, committed — see `git log -1`)

1563 tests (1562 passed, 1 skipped by design), 100 % line and branch coverage (2478 statements,
636 branches), mypy clean on 74 files.

| Symbol | Location | Use here |
|---|---|---|
| `tool`, `ToolParamError`, `ToolDataError` | `statlab_tools/tool.py` | as in chunks 08–10 |
| `load_dataframe`, `save_dataframe`, `save_model`, `load_model` | `statlab_tools/artifacts.py` | fitted transformers are kind `transformer` (`.joblib`) |
| `is_numeric_column`, `json_scalar`, `json_list`, `observed_counts` | `statlab_tools/tools/_frame.py` | as in chunks 09–10 |
| `validate_contract`'s snapshot read | `statlab_tools/tools/intake.py` | `cast_dtypes` reads the contract the same way |
| `ColumnRole`, `ImputationStrategy` | `statlab_contracts.vocabulary` | parameter types |
| `churn_frame`, `messy_frame` | `tests/conftest.py` | fixtures |

### Findings from probing before writing this spec

Verified against pandas 3.0.5 under `-W error`.

| # | Behaviour | Consequence |
|---|---|---|
| P1 | **`Int64.fillna(2.5)` raises `TypeError`** | A median or mean fill on a nullable-int column crashes. Mean/median imputation casts the column to `float64` first, on every frame, so dtypes agree across splits |
| P2 | `Categorical.fillna(<new value>)` raises `TypeError` | `constant` on a categorical-dtype column is `tool_param` |
| P3 | `float` codes with NaN under `astype("str")` give `"1.0"`, `"2.0"`. Via `Int64` first they give `"1"`, `"2"`, and NaN stays NaN | `cast_dtypes` to categorical routes integral floats through `Int64` |
| P4 | `to_datetime(format="ISO8601", errors="coerce")` gives `datetime64[us]`, but **raises `ValueError` on mixed UTC offsets despite `errors="coerce"`** | Catch it, re-parse with `utc=True`, warn |
| P5 | `mode()` returns ties sorted ascending; `drop_duplicates` treats NaN as equal to NaN | `most_frequent` is deterministic; a row that is all-NaN in the same places is a duplicate |
| P6 | Ordered string `Categorical` survives parquet with its order; nullable `boolean` survives; `is_numeric_column(boolean)` is False | `cast_dtypes` outputs are stable through artifacts |
| P7 | `Series.clip` keeps NaN; `joblib` round-trips a frozen dataclass holding numpy scalars | Clip before or after imputing is the node's choice; fitted state can be a plain dataclass |

### Decisions this chunk makes

**D-a. After the split, no tool drops a row.** Every candidate must be scored on the same valid
and test rows (D26). A per-candidate row drop would also bias test toward the easy rows. Hence:
- Duplicates and rows above `drop_rows_above` are dropped pre-split, in node 4. That needs a new
  tool, `drop_high_missing_rows`, beside `drop_duplicate_rows`. The catalogue is now 86 tools.
- **`ImputationStrategy` `"drop"` drops the column, never rows.** The vocabulary never defined it.
  Defining it as row deletion would break D26.
- A candidate's `preprocessing.missing.drop_rows_above` is meaningless. Chunk 27b rejects one that
  differs from the contract's (`00_index.md` row 27b).

**D-b. `cast_dtypes` is pre-split and stateless.** A cast learns nothing from the data, so doing it
once, before the split, is leak-free. It means test never needs a cast replayed in node 12. Roles:
the agent passes `roles` (normally `infer_column_roles`' output), and every role declared in the
contract snapshot **overrides** it. The summary says which role came from where. §6.3's "not
chosen" was written assuming a contract always exists, but the contract is optional.

**D-c. Column-drop decisions are fitted like everything else.** Deciding "constant" or "mostly
missing" separately on each split can give the splits different columns. So
`drop_constant_columns` and `drop_high_missing_columns` take `train`, return the reduced train
frame plus `dropped_columns`, and the node applies the same list to valid and test with
`drop_columns`. The parameter name is the enforcement hook. Chunk 21 now has a rule that a
parameter named `train` never receives a test-derived artifact (`00_index.md` row 21). It covers
every `fit_*` and these two deciders.

**D-d. Fitted state is a frozen dataclass, pickled as kind `transformer`.** No sklearn. A fill
value per column and a pair of bounds per column are fully described by a dict, which keeps them
readable in the summary (the project's point is auditability) and trivial for chunk 20's
`export_pipeline_script`. `apply_*` checks the loaded object's type, so passing the wrong ref
fails as `tool_param`, not as an `AttributeError`.

**D-e. The target is never transformed.** Every decide-on-train tool takes `target` and leaves it
out. Imputing or clipping a regression target changes the labels. For `apply_*`, the target may
be present or absent (a prediction-time frame has none), and it is never modified either way.

### What this chunk does NOT do

| Not here | Where |
|---|---|
| Encoding, scaling | 12 |
| Datetime part extraction, binning | 17 |
| Deciding which cleaning steps a candidate gets | 28 (node 7) |
| Enforcing D-c's `train` rule | 21 |

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/cleaning.py` |
| modify | `mcp_server/statlab_tools/tools/__init__.py`: one import line |
| modify | `contracts/statlab_contracts/artifacts.py`: the `"transformer"` comment becomes "a fitted transformer (imputer, clipper, encoder, scaler)". **Comment only** |
| create | `tests/test_tools_cleaning_rows.py` (pre-split tools), `tests/test_tools_cleaning_fit.py` (imputer, clipper, deciders, `drop_columns`) |
| modify | this file's frontmatter, `00_index.md` row 11, `WHATS_NEXT.md` |

If `validate_contract`'s snapshot-reading code is not already a reusable function, extract it
into a private helper in `intake.py` and import it. Intake tests must pass **without edits**.

---

## 3. Implementation Spec

**Conventions for all ten tools:**
- `phase="cleaning"`.
- Frame parameters are named `train` (decide-on-train tools) or `frame` (everything else).
- Output frames keep the input's index labels and row order. Never use `reset_index`:
  `verify_split` and chunk 10's fingerprint rely on labels.
- Every number entering a summary goes through `json_scalar`.
- A column name that is not in the frame is `tool_param`, with the parameter as the field.
- An output frame is saved as kind `dataframe` with the label given below.

### 3.1 Pre-split (`allow_in=["data_splitting"]`, except `cast_dtypes`)

**`drop_duplicate_rows(frame, subset: tuple[str, ...] | None = None)`**
- `keep="first"`, over `subset` or all columns.
- Summary: `{"rows_before", "rows_dropped", "subset"}`. Label `deduplicated`.
- The docstring says to pass a `subset` that excludes ID columns. An ID column makes every row
  unique.

**`drop_high_missing_rows(frame, threshold: float)`**
- `threshold` takes `Field(gt=0, le=1)`. It drops rows whose share of null cells is **strictly
  greater** than `threshold`, matching the contract's `drop_rows_above`.
- Summary: `{"rows_before", "rows_dropped", "threshold"}`. Label `sparse_rows_dropped`.

**`cast_dtypes(frame, target, roles: dict[str, ColumnRole] = {})`**
- `allow_in=["data_profiling", "data_splitting"]`.
- The effective roles are `roles` overlaid by the snapshot's `features.roles`. A role for a
  column not in the frame is `tool_param` on `roles` when it came from the agent; it is
  **ignored with a warning** when it came from the contract (`validate_contract` already reports
  it). Columns with no role are unchanged.
- The `target` is **never cast** (D-e): an agent role for it (e.g. `infer_column_roles`'
  `boolean` for a 0/1 target) is ignored with a warning, since the target's dtype is governed by
  contract `target.type`, not roles (the contract validator already forbids naming the target in
  `features.roles`, so only the agent side needs the guard).

| Role | Cast | Already that type |
|---|---|---|
| `numeric` | `pd.to_numeric(errors="coerce")` | unchanged if `is_numeric_column` |
| `categorical`, `text` | `str` dtype; numeric with all non-null values integral goes via `Int64` first (P3) | unchanged if `str` |
| `ordinal` | ordered `Categorical` over the snapshot's `ordinal_levels[col]`; values outside the levels become NaN | no levels declared: unchanged, with a warning |
| `datetime` | `to_datetime(format="ISO8601", errors="coerce")`; on `ValueError` (mixed offsets, P4) retry with `utc=True` and warn | unchanged if datetime dtype |
| `boolean` | case-insensitive map from {`true`,`t`,`yes`,`y`,`1`} / {`false`,`f`,`no`,`n`,`0`} to nullable `boolean`; anything else becomes NaN | unchanged if bool dtype |

- Summary: `{"columns": {col: {"role", "source": "agent"|"contract", "from", "to",
  "newly_null"}}}`, listing only cast columns. `newly_null` counts values that were non-null
  before and null after. A warning is added for each column with `newly_null > 0`.
- Label `typed`.

### 3.2 Decide on train (`allow_in=["preprocessing"]`)

**`drop_constant_columns(train, target)`**
- Drops columns other than `target` with at most one distinct non-null value (an all-null
  column is constant).
- Summary: `{"dropped_columns"}`. Label `columns_dropped`.
- The docstring says: "apply `dropped_columns` to valid and test with `drop_columns`".

**`drop_high_missing_columns(train, threshold, target)`**
- Drops columns other than `target` whose null share is strictly greater than `threshold`.
- Same summary shape and docstring note as `drop_constant_columns`, plus `"null_share"` per
  dropped column.

**`fit_imputer(train, target, default: ImputationStrategy = "median", per_column: dict[str, ImputationStrategy] = {})`**
- Learns a fill for **every** feature column (every column except `target`), not only those with
  nulls in train: valid or test can have nulls where train has none.
- Effective strategy per column:
  - `per_column[col]` if given. `mean`/`median` on a non-numeric, non-datetime column there is
    `tool_param` on `per_column`.
  - Otherwise `default`. A `mean`/`median` default on a non-numeric, non-datetime column falls
    back to `most_frequent`, and the effective strategy is recorded.
  - A column that is entirely null in train cannot be imputed: it becomes `drop`, with a warning.
- Fills:
  - `mean`/`median`: `series.mean()`/`median()`, numeric or datetime.
  - `most_frequent`: `mode().iloc[0]` (P5).
  - `constant`: `0.0` for numeric, `"__missing__"` for `str`/object. Any other dtype
    (categorical, bool, datetime) is `tool_param` (P2).
  - `drop`: the column is dropped at apply.
- Saves a frozen dataclass `FittedImputer(target: str, columns: tuple[str, ...],
  strategies: dict[str, str], fills: dict[str, object], dropped: tuple[str, ...])` via
  `save_model(kind="transformer", label="imputer")`.
- Summary: `{"strategies", "fills" (json_scalar), "dropped", "fitted_rows"}`.
- No frame output: train goes through `apply_imputer` like every other frame.

**`fit_outlier_clipper(train, target, method: Literal["clip_iqr", "winsorize", "flag_only"], columns: tuple[str, ...] | None = None)`**
- Columns: `columns` or every `is_numeric_column` feature. Naming a non-numeric column or the
  target is `tool_param`.
- Bounds, computed on non-null train values:
  - `clip_iqr` and `flag_only`: `Q1 − 1.5·IQR`, `Q3 + 1.5·IQR`.
  - `winsorize`: the 0.01 and 0.99 quantiles.
- A column whose bounds are equal (constant, or IQR 0) is **skipped with a warning**, never fitted.
  This is chunk 09's P6: zero-width fences flag everything or nothing.
- Saves `FittedClipper(target, method, bounds: dict[str, tuple[float, float]])`, kind
  `transformer`, label `outlier_clipper`.
- Summary: `{"method", "bounds", "skipped"}`.

### 3.3 Apply (`allow_in=["data_splitting", "preprocessing", "champion_selection"]` for `drop_columns`; `["preprocessing", "champion_selection"]` for the two `apply_*`)

**`drop_columns(frame, columns: tuple[str, ...])`**
- `min_length=1`. An unknown column is `tool_param`. Label `columns_dropped`.

**`apply_imputer(imputer, frame)`**
- A loaded object that is not a `FittedImputer` is `tool_param` on `imputer`.
- The frame's columns minus `target` must equal the fitted `columns`. A missing or unseen column
  is `tool_param` on `frame`, naming the columns.
- `mean`/`median` columns are cast to `float64` before filling (P1). Dropped columns are removed.
- Summary: `{"filled": {col: n}}`, counting only columns where n > 0. Label `imputed`.

**`apply_outlier_clipper(clipper, frame)`**
- The same type check, on `clipper`. Every fitted column must be present.
- `clip_iqr` and `winsorize` clip to the bounds.
- `flag_only` adds a bool column `f"{col}__outlier"` and leaves values unchanged. An existing
  column with that name is `tool_param` on `frame`.
- Summary: `{"affected": {col: n}}`, counting values clipped or flagged. Label `clipped` or
  `outliers_flagged`.

---

## 4. Test Suite Spec (~110 tests)

Every guard is tested by triggering the failure it prevents. Build inputs by hand, with known
answers, so that no expected value is computed by the code under test.

**Rows (`test_tools_cleaning_rows.py`):**
- `drop_duplicate_rows`:
  - Duplicates are dropped and the labels of the kept rows are preserved, on a non-range index.
  - NaN-equal rows are duplicates.
  - `subset` excluding an ID column finds duplicates that the full-column check misses.
  - Unknown `subset` is `tool_param`.
- `drop_high_missing_rows`:
  - A row at exactly the threshold share is **kept** and one above it is dropped.
  - Labels are preserved.
- `cast_dtypes`, one test per role row of the table:
  - Each cast and each already-typed no-op.
  - `newly_null` is exact.
  - Integral floats with NaN give `"1"`, not `"1.0"`.
  - Mixed offsets retry with `utc=True` and warn.
  - Ordinal with no levels warns.
  - A contract role overrides the agent's role, with `source` reported.
  - An unknown agent column is `tool_param`; an unknown contract column only warns.
  - The outputs survive a parquet round-trip with dtypes intact (P6).

**Fit and apply (`test_tools_cleaning_fit.py`):**
- **The core leak test:** fit the imputer on a train whose median is 10, then apply it to a valid
  whose own median is 99. The fill is 10. Do the same for the clipper's bounds.
- The imputer learns a fill for a column with no nulls in train and applies it on valid.
- The `median` default on a string column falls back to `most_frequent` and is recorded. An
  explicit `per_column` `median` on a string column is `tool_param`.
- An all-null train column becomes `drop` with a warning.
- `drop` removes the column and keeps the **row count**.
- `constant` on a categorical column is `tool_param`.
- An `Int64` column with a fractional median imputes without error, to `float64` (P1), on every
  frame.
- The target is not imputed, not clipped and not dropped by either decider, even when it is
  constant or mostly null.
- `apply_imputer` on a frame with an unseen column, a missing column, or with the target absent:
  the first two are `tool_param`; the last succeeds.
- Passing a clipper to `apply_imputer`, and the reverse, is `tool_param`.
- The clipper:
  - `clip_iqr` bounds match a hand computation.
  - `winsorize` uses the 0.01/0.99 quantiles.
  - `flag_only` adds the bool column and leaves values unchanged; a name collision is
    `tool_param`.
  - A constant column is skipped with a warning.
  - NaN survives clipping.
- The deciders return `dropped_columns`; `drop_columns` with that list on valid gives the same
  columns as the train output.
- Every frame output keeps labels.

**Cross-cutting:**
- Every tool's result survives `json.dumps(model_dump(mode="json"), allow_nan=False)` on
  `churn_frame` and `messy_frame`.
- One MCP test: `fit_imputer` then `apply_imputer` over a real `mcp_session`. Check the served
  `meta.allow_in` for one tool from each group.

---

## 5. Definition of Done

**Tests**
- [ ] `make test` exits 0. Total ≥ **1670**. Coverage **100 %** line and branch.
- [ ] Intake and profiling tests pass **without edits**.
- [ ] **Mutation evidence:** each change below makes at least one test fail. Restore after each.
  - `apply_imputer` recomputes the fill from the frame it is given;
  - the clipper's bounds are recomputed at apply;
  - imputation `drop` drops rows with `dropna` instead of the column;
  - remove the target exclusion in `fit_imputer`;
  - change `>` to `>=` in `drop_high_missing_rows`;
  - add `reset_index(drop=True)` to any frame output;
  - remove the `float64` cast before mean/median fills;
  - remove the constant-column skip in the clipper.

**Static analysis**
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] No sklearn import in `cleaning.py`. No `reset_index`, no bare `value_counts()`.

**Housekeeping**
- [ ] This file's frontmatter.
- [ ] `00_index.md` row 11 completed. Add new environment facts (P1, P2, P4 at least) as
      one-liners in the pandas 3.0 block.
- [ ] `WHATS_NEXT.md`: state table measured; "Next" set to chunk 12; a
      `## Chunk 11 — completed (summary)` section.
- [ ] **Do not commit.**

### Stop and ask if

- Any of P1–P7 does not reproduce.
- An intake or profiling test needs editing.
- A tool seems to need sklearn, the database, or a change under `backend/`.

### Report back with

1. Test count, coverage statements/branches, mypy file count, ruff.
2. The mutation table: change → which tests failed → restored.
3. `fit_imputer` on `churn_frame`'s 144-row train from `split_stratified(seed=42)`, summary
   verbatim.
4. `cast_dtypes` on `messy_frame` with `infer_column_roles`' roles, summary verbatim.
5. Any case where the `median` → `most_frequent` fallback or the all-null → `drop` rule felt like
   the tool deciding something the agent should decide.
6. Anything surprising about pandas 3.0 not in P1–P7.
7. For chunk 12: the encoders face the same "unseen column / unseen level at apply" question as
   `apply_imputer`. Should an unseen *level* be an error, ignored, or mapped to an "other" column?
   Answer from what you saw of the fixtures.

---

## 6. Addendum — review corrections (2026-10-01)

Four defects reproduced in review of the implemented `cleaning.py`, fixed in the working tree
(no behaviour outside these four changed). Each has a test that fails on the pre-fix code.

1. **`cast_dtypes` cast the TARGET.** `infer_column_roles` returns `churned: 'boolean'`; passed in
   `roles`, that turned the 0/1 target into nullable `boolean`, so `validate_contract` with
   `positive_class '1'` reported "`positive_class '1'` does not occur in the target" (breaking
   D-e). Fix: `cast_dtypes` gains a required `target` parameter (the `_Target` alias); the target
   is never cast, and an agent role for it is ignored with a warning. Signature is now
   `cast_dtypes(df, target, roles)`.

2 & 3. **Integral floats stringified as `"1.0"`, destroying data in two casts.** A float64 column
   `[1.0, 0.0, NaN]*10` with role `boolean` nulled every value (newly_null 30: `"1.0"` is not a
   boolean token); a float64 column `[1.0, 2.0, 3.0, NaN]*10` with role `ordinal` and levels
   `['1','2','3']` nulled every value too. Integer-coded columns with a null load as float64, so
   this was the common case. Fix (once): the Int64-route from `_cast_string` is extracted into
   `_as_str(series)` and used by the string, ordinal, and boolean casts; ordinal compares
   `_as_str`'s output against the levels.

4. **Clipping an Int64 column crashed.** `fit_outlier_clipper(method='clip_iqr')` then
   `apply_outlier_clipper` on an Int64 column raised `TypeError: Invalid value '59.5' for dtype
   'Int64'` (P1 again); `flag_only` on Int64 produced a nullable `BooleanDtype` flag with NA. Fix:
   `apply_outlier_clipper` casts the column to `float64` before `clip_iqr`/`winsorize` (on every
   frame, as `apply_imputer` does for mean/median), and `flag_only` produces a plain `bool` column
   with a null value flagged `False`.
