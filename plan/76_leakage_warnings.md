---
status: completed
dependencies: [75]
completion_date: 2026-10-10
implementation_notes: "two new data-quality warnings: columns empty where the target is missing; target follows the row order"
---

# Chunk 76 — leakage warnings in the data-quality checks

User report 2026-10-10, run `r_f5e9364ca69378fa` (project "Teszt", `public_data.csv`, 89,760 ×
19, target `target_flag`): 99.5 % validation balanced accuracy on a task where the user's team
reached under 75 %. Read-only analysis:
1. Seven columns (`ke`, `hupx`, `afrr`, `afrr_fel`, `afrr_le`, `mfrr_fel`, `mfrr_le`) are 100 %
   filled on labelled rows and 0 % on the 17,952 unlabelled rows (day 4 of every period, the
   days to predict): measured values known only afterwards. `ke` alone reaches 0.948 balanced
   accuracy at one threshold; permutation importance of `ke` is ~0.5 for every candidate.
2. Rows are a 15-minute time series in file order; 83.5 % of labelled rows share the previous
   row's class (0.55 expected if shuffled); the contract used a stratified random split.
One HistGradientBoosting probe (balanced accuracy): all 17 features 0.995 random / 0.983
time-ordered; only prediction-day columns 0.658 random / 0.555 time-ordered.

Decision: `check_data_quality` gains two warn-only checks, shown wherever the existing nine are
(run Progress data-quality card, report table, agent evidence) and named in the skill:
- `missing_without_target` ("Columns empty where the target is missing"): a feature column that
  is filled on at least half of the labelled rows and on at most 1 % of the rows without a
  target. Skipped when fewer than 20 rows lack a target.
- `target_follows_row_order` ("Target follows the row order"): on labelled rows in file order,
  a class target whose share of rows equal to the previous row exceeds the shuffled
  expectation (sum of squared class shares) by at least 0.15, or a continuous target whose
  lag-1 autocorrelation is at least 0.5. A target sorted in file order (class: at most
  `levels − 1` changes; continuous: monotonic) passes, since sorting alone does not leak under
  a random split. Skipped when fewer than 100 rows have a target.
Non-goals: a temporal/grouped split (the split tool has none; `_split_rules` refuses
`temporal`/`grouped`), stopping the run, a setup-time warning, changing the split node.

## Stage 76a (detailed spec)

Owned: `mcp_server/statlab_tools/tools/quality.py`, `tests/test_tools_quality.py`,
`backend/statlab/domains/champion.py`, `skills/modelling.md`, `frontend/src/run/model.ts`,
`frontend/tests/run-model.test.ts`. Not alone: preserve others' edits.

1. `quality.py`:
   - Constants after `EXAMPLES` (each with a one-line docstring like the others):
     `UNLABELLED_MIN_ROWS: Final = 20` ("Fewer rows without a target than this skips the
     missing_without_target check."), `UNLABELLED_PRESENT_MAX: Final = 0.01` ("A column filled
     on at most this share of rows without a target is flagged."), `LABELLED_PRESENT_MIN:
     Final = 0.5` ("...and on at least this share of rows with a target."),
     `ORDER_MIN_ROWS: Final = 100` ("Fewer rows with a target than this skips the row-order
     check."), `ORDER_LIFT: Final = 0.15` ("How far the same-class-as-previous share must
     exceed the shuffled expectation."), `ORDER_CORRELATION: Final = 0.5` ("Lag-1
     autocorrelation from which a continuous target follows the row order.").
   - Docstring of `check_data_quality`: "nine" becomes "eleven"; append to the Checks list
     ", columns empty where the target is missing, target following the row order".
   - After check 9 (`duplicate_columns`), before `fixable`, add:
     **10. missing_without_target.** `unlabelled = target_series.isna().to_numpy()`;
     `missing_rows = int(unlabelled.sum())`. If `missing_rows < UNLABELLED_MIN_ROWS`:
     `results["missing_without_target"] = _check("skipped", 0, [], {"reason": "fewer than 20
     rows without a target"})`. Else: `details = []`, `columns = []`; for `c` in `features`:
     `labelled_present = float(data[c][~unlabelled].notna().mean())` and
     `unlabelled_present = float(data[c][unlabelled].notna().mean())`; when
     `n - missing_rows > 0` is false treat `labelled_present` as 0.0 (avoid the empty mean);
     if `unlabelled_present <= UNLABELLED_PRESENT_MAX and labelled_present >=
     LABELLED_PRESENT_MIN`: append `c` to `columns` and `{"column": c, "labelled_present":
     round(labelled_present, 3), "unlabelled_present": round(unlabelled_present, 3)}` to
     `details`. Result `_check("warn" if details else "pass", missing_rows if details else 0,
     json_list(columns), details)`.
     **11. target_follows_row_order.** `labelled = target_series.dropna()`. If
     `len(labelled) < ORDER_MIN_ROWS`: `_check("skipped", 0, [], {"reason": "fewer than 100
     rows with a target"})`. Elif `is_class_target(labelled)`: `values =
     labelled.to_numpy()`; `same = float((values[1:] == values[:-1]).mean())`; `changes =
     int((values[1:] != values[:-1]).sum())`; `shares = observed_counts(labelled) /
     len(labelled)`; `expected = float((shares**2).sum())`; `follows = same - expected >=
     ORDER_LIFT and changes > len(shares) - 1`; details `{"same_as_previous": round(same, 3),
     "expected": round(expected, 3)}`. Else (continuous): `numeric =
     labelled.astype("float64")`; `sorted_target = numeric.is_monotonic_increasing or
     numeric.is_monotonic_decreasing`; `correlation = numeric.autocorr(lag=1)`; `follows = not
     sorted_target and not pd.isna(correlation) and float(correlation) >=
     ORDER_CORRELATION`; details `{"lag1_correlation": None if pd.isna(correlation) else
     round(float(correlation), 3)}`. For both branches: `_check("warn" if follows else
     "pass", 0, json_list([target]) if follows else [], details)`.
   Nothing else changes (the `warnings` tuple and `passed` already cover new entries).
2. `champion.py` `_QUALITY_LABELS`: add `"missing_without_target": "Columns empty where the
   target is missing"` and `"target_follows_row_order": "Target follows the row order"` after
   `duplicate_columns`.
3. `frontend/src/run/model.ts`:
   - `QUALITY_LABELS`: the same two entries, same texts.
   - `qualityNote` cases before `default`:
     `'missing_without_target'`: if `one.reason !== undefined` return `text(one.reason)`;
     else return `` `${items.map((i) => `${text(i.column)} (filled in ${percent(i.labelled_present)} of rows with a target, ${percent(i.unlabelled_present)} without)`).join('; ')}. These are probably not known when you predict; exclude them unless they are.` ``.
     `'target_follows_row_order'`: if `one.reason !== undefined` return `text(one.reason)`;
     const `tail = 'A random split puts near-copies of a row in training and test, so the
     scores may be too good.'`; if `typeof one.lag1_correlation === 'number'` return
     `` `Neighbouring rows have similar targets (lag-1 correlation ${text(one.lag1_correlation)}). ${tail}` ``;
     else return `` `${percent(one.same_as_previous)} of rows have the same class as the previous row (${percent(one.expected)} if shuffled). ${tail}` ``.
4. `skills/modelling.md` `## data_quality` `### Heuristics`: append two bullets:
   `- Columns empty where the target is missing are probably measured only afterwards: name them in the findings as likely leakage.`
   `- A target that follows the row order means a random split overstates the scores: say so in the findings.`
5. `tests/test_tools_quality.py`:
   - `test_clean_frame_passes_every_check`: the loop asserts `"skipped"` for names
     `missing_without_target` and `target_follows_row_order` (6 rows) and `"pass"` for the
     rest; keep the `passed` and `warnings == ()` asserts.
   - New `test_missing_without_target_flags_columns_empty_on_unlabelled_rows(run_paths)`:
     frame of 40 rows: `"leak": [float(i) for i in range(20)] + [None] * 20`, `"plan":
     [float(i) for i in range(40)]`, `"partial": [float(i) for i in range(30)] + [None] *
     10`, `"y": [0.0, 1.0] * 10 + [None] * 20`. `check_data_quality(..., target="y")`;
     `_check(res, "missing_without_target") == {"status": "warn", "rows": 20, "columns":
     ["leak"], "details": [{"column": "leak", "labelled_present": 1.0,
     "unlabelled_present": 0.0}]}` and `"missing_without_target check status is warn" in
     res.warnings`. Same frame with `id_columns=("leak",)`: status `"pass"`, rows 0, columns
     `[]`, details `[]`. Frame `frame.iloc[:39]` (19 unlabelled): `{"status": "skipped",
     "rows": 0, "columns": [], "details": {"reason": "fewer than 20 rows without a
     target"}}`.
   - New `test_target_follows_row_order_class_and_continuous(run_paths)`, each frame with
     `"x": [float(i) for i in range(200)]` plus `y`, check `"target_follows_row_order"`:
     a. `y = ([0] * 20 + [1] * 20) * 5` → `{"status": "warn", "rows": 0, "columns": ["y"],
        "details": {"same_as_previous": 0.955, "expected": 0.5}}`.
     b. `y = [0] * 100 + [1] * 100` (sorted) → status `"pass"`, columns `[]`, details
        `{"same_as_previous": 0.995, "expected": 0.5}`.
     c. `y = [0, 1] * 100` → status `"pass"`.
     d. `y = [float(i % 50) for i in range(200)]` → status `"warn"`, columns `["y"]`, details
        `{"lag1_correlation": 0.91}`.
     e. `y = [float(i) for i in range(200)]` (sorted continuous) → status `"pass"`.
     f. 99 rows, `"x": [float(i) for i in range(99)]`, `y = [0] * 50 + [1] * 49` → `{"status":
        "skipped", "rows": 0, "columns": [], "details": {"reason": "fewer than 100 rows with a
        target"}}`.
6. `frontend/tests/run-model.test.ts` `qualityNote explains each finding`: add
   - `qualityNote('missing_without_target', [{ column: 'ke', labelled_present: 1, unlabelled_present: 0 }])`
     equals `'ke (filled in 100.0% of rows with a target, 0.0% without). These are probably not known when you predict; exclude them unless they are.'`
   - `qualityNote('missing_without_target', { reason: 'fewer than 20 rows without a target' })`
     equals `'fewer than 20 rows without a target'`.
   - `qualityNote('target_follows_row_order', { same_as_previous: 0.835, expected: 0.55 })`
     equals `'83.5% of rows have the same class as the previous row (55.0% if shuffled). A random split puts near-copies of a row in training and test, so the scores may be too good.'`
   - `qualityNote('target_follows_row_order', { lag1_correlation: 0.91 })` equals
     `'Neighbouring rows have similar targets (lag-1 correlation 0.91). A random split puts near-copies of a row in training and test, so the scores may be too good.'`

Checks: `make test-fast TESTS="tests/test_tools_quality.py tests/test_nodes_foundation.py
tests/test_nodes_champion.py tests/test_skills.py"`; Ruff check, Ruff format --check and mypy
on `mcp_server/statlab_tools/tools/quality.py tests/test_tools_quality.py
backend/statlab/domains/champion.py`; from `frontend/`: `npm test`, `npm run typecheck`.

Required probe (once, after the checks pass; no repository file changes): in a temporary
directory, build a `RunPaths` the way `tests/conftest.py`'s `run_paths` fixture does, save
`projects/teszt/datasets/46b569a74d7972428e4f00c3b595cbb4da6e7ce6a7ff7f226145a1699beffab5.csv`
(read with pandas) as a dataframe artifact the way `_save` in `tests/test_tools_quality.py`
does, call `check_data_quality(paths, ref, target="target_flag", id_columns=("target_reg",))`
and report the full `missing_without_target` and `target_follows_row_order` entries. Expected:
both `warn`; the first lists exactly `afrr, afrr_fel, afrr_le, hupx, ke, mfrr_fel, mfrr_le` (in
file column order). Delete the temporary directory. Do not write the probe into the repository.

Stop and report on any failure or any spec gap.

Main then: review, `make check`, commit.

### Addendum 76a-A (local name clash)

mypy rejects reusing `values` and `numeric` (bound earlier in `check_data_quality` with other
types). In check 11 rename `values` to `order_values` (class branch: every use) and `numeric`
to `target_numeric` (continuous branch: every use). Nothing else changes. Rerun mypy on the
three Python files, Ruff check/format --check on them, then `npm test`, `npm run typecheck`
and the required probe.

## Acceptance receipt (2026-10-10)

Implementer (after addendum A): focused selectors (tools quality, nodes foundation, nodes
champion, skills) exit 0 before the rename; mypy, Ruff check and Ruff format --check on the three
Python files exit 0 after it; `npm test` 113/113 and `npm run typecheck` exit 0. Required probe
on the user's `public_data.csv` (`target="target_flag"`, `id_columns=("target_reg",)`):
`missing_without_target` warn, rows 17952, columns `ke, hupx, afrr_fel, afrr_le, mfrr_fel,
mfrr_le, afrr` (file order), each labelled_present 1.0 / unlabelled_present 0.0;
`target_follows_row_order` warn, `same_as_previous` 0.835, `expected` 0.55. Main reviewed the
diff (matches the recipe and addendum). `make check` (`check76.log`, temporary) exit 0, so the
focused tests ran again after the rename: coverage 100 % (TOTAL 13412 stmts, 0 missed), mypy 212
files, Ruff clean.
