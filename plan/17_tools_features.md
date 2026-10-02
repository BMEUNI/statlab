---
status: completed
dependencies: [07, 11, 12, 16]
completion_date: 2026-10-02
implementation_notes: |
  Implemented the 7 feature tools in mcp_server/statlab_tools/tools/features.py (phase="features")
  plus the cross-cutting transform_only flag (D-a). Measured outcomes:
  - Full suite 1964 passed, 1 skipped in ~46s (< 70s DoD). Coverage 100% line+branch on both
    features.py (196 stmts, 50 branches) and tool.py (149 stmts); TOTAL 100%.
  - mypy clean on 93 files; ruff check + format clean. No Any, no pragma, no new type: ignore.
  - All 7 DoD mutations killed (each failed >=1 test; the "outer edges finite" mutant failed 4).
  - transform_only is served on exactly 13 tools: the 7 apply_* (imputer, outlier clipper, onehot,
    ordinal, target, scaler, binner), drop_columns, predict, predict_proba, and the 3 row-wise
    constructors (add_datetime_parts, add_interaction_terms, add_ratio_feature). A consistency test
    in test_tool_decorator.py pins the exact set and the three properties (not fit_*, no `train`
    param, lists champion_selection) over the real tools/list.
  - check_lineage now reads meta.transform_only from tools/list (Driver carries a transform_only
    dict); its champion_selection clause is `transform_only or score_*`, replacing the former
    apply_*/predict name-prefix match. All five lineage clauses re-verified by deletion: each has a
    guard case only it rejects. Skeleton G1-G7 unchanged, gate green.
  Hazards confirmed and handled: P1 (NaT mask on is_weekend + Int64 parts), P3 (edge dedup + warn),
  P4 (±inf outer edges), P5 (divide by b.where(b!=0)), P6 (to_numpy float64), P7 (NaN refused, bool
  discrete, sklearn lazy, n_jobs=1), P8 (ddof=0). Row-wise constructors add/replace per D-b; the
  binner replaces source with f"{col}__bin" string labels picked up by fit_onehot_encoder; both
  selectors decide on train and replay via drop_columns.
  For chunk 18: the transform_only set as served by tools/list is the 13 above; node 12 replays
  these (plus score_* once) on test. Chunk 21 enforces the flag; chunk 29 replays the champion's
  node 7-8 transform_only calls in order with the same non-frame args.
---

# Chunk 17 — Feature construction and selection tools

**Goal.** The node-8 feature tools: three row-wise constructors, a fitted binner, and two
selectors that decide on train. Also one cross-cutting fix that this chunk forces: tools now
**declare** whether they are transform-only, so node 12 can replay a champion's feature steps on
test.

Batch B4 runs this first and chunk 18 after it.

---

## 1. Prerequisites & Context

### State

Committed; see `git log -1`. 1919 tests (1918 passed, 1 skipped by design), 100 % line and
branch coverage, mypy clean on 91 files, suite ≈ 44 s.

Read first:
- `tools/cleaning.py`: `drop_constant_columns`, the decide-on-train pattern (a reduced train
  frame plus `dropped_columns`, which `drop_columns` replays on the other splits);
- `tools/encoding.py`: the fit/apply pairs and `FittedScaler`;
- `tools/_frame.py`;
- `tools/training.py`: `check_features_usable`, `build_matrix`, `is_class_target`;
- `tool.py`: the decorator;
- `tests/test_skeleton.py`: `check_lineage` and the `Driver`.

### Findings from probing (pandas 3.0, sklearn 1.9.0, `-W error`, 2026-10-02)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | `.dt.year`/`.dt.dayofweek` on `datetime64[us]` with NaT return `float64` with NaN; `(dt.dayofweek >= 5)` is **False** on NaT, not NA | Cast parts to `Int64`; mask `is_weekend` with `isna()`, or a missing date becomes "weekday" |
| P2 | tz-aware columns (`datetime64[us, UTC]` after `cast_dtypes`' mixed-offset retry) give parts in their own zone (UTC) | Document it; no conversion |
| P3 | `np.quantile` edges collapse on ties: 4 requested quantile bins on `[1,1,1,1,2,3,4,5,6,100]` give 3 | Deduplicate edges, warn with the achieved count. Fewer than 2 bins is a data error |
| P4 | `pd.cut(x, [-inf, *inner, inf], right=False, labels=False)` puts out-of-range values in the outer bins and keeps NaN as NaN | Outer edges are always ±inf, so apply never produces an unbinned value |
| P5 | `a / b` with `b == 0` gives `±inf` silently, with no warning even under `-W error` | Divide by `b.where(b != 0)`, so a zero denominator gives NaN, and count them |
| P6 | `Int64 * float64` gives nullable `Float64` | Convert inputs via `to_numpy(dtype="float64", na_value=np.nan)`; outputs are plain `float64` |
| P7 | `mutual_info_classif/regression(X, y, discrete_features=mask, random_state=seed, n_jobs=1)` work; NaN in `X` raises `ValueError` | Refuse NaN up front as `tool_param` ("impute first"). Bool columns are discrete |
| P8 | Population variance of a 1-in-4 bool is 0.1875 (`ddof=0`) | `select_by_variance` uses `ddof=0`, like sklearn's `VarianceThreshold` |

### Decisions this chunk makes

**D-a. Transform-only is declared, not inferred from names.** D32b lets node 12 pass test-derived
frames only to "transform-only tools (`apply_*`, `predict`, `predict_proba`)". That list already
misses `drop_columns`, which replays a train-side column drop. This chunk adds row-wise feature
tools that are neither `apply_*` nor fitted, and a champion that used them could never be scored.
- `@tool` gains `transform_only: bool = False`, exposed in `meta` next to `phase`, `allow_in`
  and `heavy`.
- `True` on: every `apply_*` (imputer, outlier clipper, one-hot, ordinal, target, scaler,
  binner), `drop_columns`, `predict`, `predict_proba`, `add_datetime_parts`,
  `add_interaction_terms` and `add_ratio_feature`: 13 tools.
- A transform-only tool takes no `train` parameter, is not `fit_*`, and lists
  `champion_selection` in `allow_in`. A consistency test pins all three properties and the exact
  set of 13.
- `PLAN.md` D32b and §-lineage text now say "tools registered `transform_only`" (amended with
  this spec). Chunk 21 enforces the flag. Chunk 29 replays the champion's node 7–8 calls whose
  tool is `transform_only` on test, in order, with the same non-frame arguments (noted in
  `00_index.md` rows 21 and 29).
- `tests/test_skeleton.py`: `check_lineage` reads the flag from `tools/list` instead of matching
  name prefixes; its guard tests pass the flag.

**D-b. Row-wise constructors are stateless and transform-only.** Calling one with the same
arguments on train, valid and test is the replay; there is nothing to fit.
- `add_datetime_parts` **replaces** its source column, as one-hot does, because training refuses
  a datetime feature.
- `add_interaction_terms` and `add_ratio_feature` **add** columns; their sources stay useful.

**D-c. Binning is fitted.** Quantile and uniform edges are learned from data, so the catalogue's
`bin_numeric` becomes `fit_binner(train)` and `apply_binner(binner, frame)` (D22's fit/apply
rule). The binned column replaces its source as `f"{col}__bin"` with string labels `b0 … b{k-1}`.
It is a string, so `fit_onehot_encoder` picks it up by default. The edges are in the fitted state
and in the summary, so a report can render them.

**D-d. Selectors decide on train, as chunk 11's droppers do.** They write a reduced train frame
plus `dropped_columns`; `drop_columns` (transform-only) replays the drop on valid and test. The
target is never dropped.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| modify | `tool.py`: `transform_only` parameter and meta key |
| modify | `tools/cleaning.py`, `tools/encoding.py`, `tools/training.py`: `transform_only=True` on the 9 existing tools in D-a. No other change |
| create | `mcp_server/statlab_tools/tools/features.py`: the 7 tools |
| modify | `tools/__init__.py`: one import line |
| modify | `tests/test_tool_decorator.py` (`test_t2` gains the key, plus the consistency test), `tests/test_server_mcp_session.py` (`test_m5` gains the key), `tests/test_skeleton.py` (D-a) |
| create | `tests/test_tools_features.py` |
| modify | this file's frontmatter, `00_index.md` row 17, and a short "Chunk 17 — completed" section in `WHATS_NEXT.md`. Chunk 18's stage writes the state table and "Next" |

---

## 3. Implementation Spec

**Conventions:**
- `phase="features"`.
- `allow_in`: transform-only tools get `["feature_engineering", "champion_selection"]`; the rest
  get `["feature_engineering"]`.
- `select_by_mutual_info` is `heavy=True`, imports sklearn lazily, and uses `n_jobs=1`.
- Every input column check goes through `require_columns`. The target is refused wherever a
  feature column is named (`tool_param`). A generated name colliding with an existing column is
  `tool_param` (chunk 12's rule).

### 3.1 Row-wise constructors (transform-only)

**`add_datetime_parts(frame, target, column, parts: tuple[DatetimePart, ...])`**
- `DatetimePart = Literal["year", "month", "day", "dayofweek", "dayofyear", "hour", "is_weekend"]`
  (`min_length=1`, no duplicates).
- The column must be a datetime dtype, tz-aware included; a `str` column is `tool_param` ("cast
  it with `cast_dtypes` in node 4").
- Each part becomes `f"{column}__{part}"` as `Int64`, with NA wherever the date is NaT (P1).
- The source column is dropped (D-b), and the new columns take its position.

**`add_interaction_terms(frame, target, pairs: tuple[tuple[str, str], ...])`**
- Each `(a, b)` adds `f"{a}__x__{b}"` = a·b as `float64` (P6). Both must be numeric or bool;
  bool is cast to 0/1.
- `(a, a)` is allowed and adds a square. NaN propagates.

**`add_ratio_feature(frame, target, numerator, denominator)`**
- Adds `f"{numerator}__per__{denominator}"` as `float64`. A zero denominator gives NaN (P5).
- The summary carries `zero_denominator_rows`, with a warning when it is above 0.

### 3.2 Binning

**`fit_binner(train, target, columns: tuple[str, ...], n_bins: int = 5 (2–20), strategy: Literal["quantile", "uniform"] = "quantile")`**
- Numeric columns only (`is_numeric_column`).
- Inner edges come from train's non-null values: quantiles at `linspace(0, 1, n_bins + 1)`, or a
  uniform `linspace(min, max, n_bins + 1)`, deduplicated (P3); outer edges are ±inf.
- An achieved count below `n_bins` is a warning naming the column. Fewer than 2 bins is
  `tool_data`.
- Saves `FittedBinner(target, strategy, edges: dict[str, tuple[float, ...]])` as kind
  `transformer`. The summary has the edges per column, with infinities as `null` (strict JSON).

**`apply_binner(binner, frame)`** (transform-only)
- Each fitted column → `f"{col}__bin"` with labels `b{i}` (`pd.cut(..., right=False)`, P4), at
  the source's position. NaN stays NaN.
- The edges are **never** recomputed from `frame`.

### 3.3 Selection (decide on train)

**`select_by_variance(train, target, threshold: float = 0.0 (ge 0))`**
- Numeric and bool features are considered; others are left alone and counted in
  `not_considered`.
- Population variance (P8) of the non-null values. An all-null column has undefined variance and
  counts as 0, like chunk 11's "an all-null column is constant".
- Drops every considered column with variance ≤ `threshold`. The summary has `dropped_columns`
  and their variances.

**`select_by_mutual_info(train, target, k: int (ge 1), seed: <required>)`** (heavy)
- Every feature must pass `check_features_usable` ("encode first"), and the matrix must have no
  NaN ("impute first"), else `tool_param`.
- `mutual_info_classif` when `is_class_target(target)`, else `mutual_info_regression`, with
  `discrete_features` true for bool columns, `random_state=seed` and `n_jobs=1` (P7).
- Keeps the top `k` (ties broken by column name); drops the rest. `k` ≥ the number of features
  drops nothing, with a warning.
- The summary has every feature's score, sorted, plus `dropped_columns`.

---

## 4. Test Suite Spec (~60)

**Datetime.**
- Known answers: 2024-01-06 → year 2024, month 1, day 6, dayofweek 5, dayofyear 6,
  is_weekend 1; 2024-03-15 13:00 → hour 13, is_weekend 0.
- NaT → NA in **every** part, including `is_weekend`.
- The source is replaced in place; labels are preserved.
- A `str` column and a duplicated part are both `tool_param`; tz-aware works.

**Interaction and ratio.**
- The product equals a·b, including bool × numeric; NaN propagates.
- `Int64` inputs give `float64` outputs; a collision is `tool_param`.
- A zero denominator, including -0.0, gives NaN, never inf, and is counted.
- A non-numeric input is `tool_param`.

**Binner.**
- Edges are fitted on train only: an apply frame with a different distribution keeps train's
  edges.
- Out-of-range values land in `b0` and `b{k-1}`; NaN stays NaN.
- The P3 tie case warns with 3 bins; a constant column is `tool_data`; the uniform strategy has
  known edges.
- The binned column is then picked up by `fit_onehot_encoder` by default.
- The target is refused.

**Variance.**
- The 1-in-4 bool has variance 0.1875 and is dropped at threshold 0.2, kept at 0.1.
- An all-null numeric column is dropped. A string column is not considered. The target is never
  dropped.
- `drop_columns` replay gives identical columns on valid.

**Mutual information.**
- Scores equal sklearn called directly with the same mask and seed (the oracle).
- A planted informative feature is kept and pure noise is dropped at k=1.
- NaN and string features are `tool_param`; a large `k` warns; a repeated call is deterministic;
  a regression target uses `mutual_info_regression`.

**Transform-only consistency** (in `test_tool_decorator.py`, over `server.list_tools()`):
- The exact set of 13 is transform-only.
- Every `apply_*` is transform-only.
- None of the 13 is `fit_*` or has a `train` parameter.
- Each lists `champion_selection`.

**End to end.** A seeded frame with a datetime column (the skeleton's `subscribers` plus a signup date is fine) goes
through the spine to node 8, then: `add_ratio_feature` and `add_interaction_terms` on train and
valid, `fit_binner` on train, `apply_binner` on both, and `select_by_variance` with its
`drop_columns` replay. Train and valid end with identical column lists, and `train_estimator`
accepts the result.

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 70 s. Coverage **100 %** line and branch. Existing tests
      pass, with edits only where §2 names them.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - `apply_binner` recomputes the quantile edges from `frame`;
  - the outer edges are left finite;
  - `is_weekend` is computed without the NaT mask;
  - the ratio divides by the raw denominator;
  - `select_by_variance` uses `ddof=1`;
  - `drop_columns` loses `transform_only=True`;
  - mutual information treats bool columns as continuous.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

P1–P8 do not reproduce; the skeleton changes beyond D-a; anything needs `backend/` or
`contracts/`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The end-to-end column list after node 8.
4. For chunk 18: the transform-only set as served by `tools/list`.

---

## 6. Addendum — review corrections (2026-10-02)

The review re-ran the gates and all seven §5 mutations. One mutation survived: **`apply_binner`
recomputing the quantile edges from `frame`** passed every test. The train-only test checked only
the two extreme values, which land in the outer bins whichever data the edges come from. The test
now uses apply values in the middle, where train's edge (20) and the frame's own median (5)
disagree, and asserts the whole label list. It fails under the mutation and passes on the code.
No production change was needed.

An independent probe on the skeleton's `subscribers` covered the full node-4–12 path:
- add datetime parts, ratio and interaction; fit and apply the binner; one-hot; select by
  variance and by mutual information; scale;
- logistic regression;
- the champion's 8 transform-only calls replayed on test in order (`apply_imputer`,
  `add_datetime_parts`, `add_ratio_feature`, `add_interaction_terms`, `apply_binner`,
  `apply_onehot_encoder`, `apply_scaler`, `drop_columns`).

The replay gave test the same columns as train, and test ROC AUC 0.75. D-a holds in practice.
