---
status: untouched
dependencies: [07, 13, 14, 17]
---

# Chunk 18 — Interpretation tools

**Goal.** Node 11's predictive interpretation: permutation importance, standardised
coefficients, partial dependence, and error analysis by segment. Inferential diagnostics stay in
node 6 (chunk 15). PDP rendering is a new curve kind in `plot_curve` rather than a separate tool
(D-d).

Batch B4 runs this after chunk 17.

---

## 1. Prerequisites & Context

### State

Chunk 17 is in the working tree, uncommitted, with green gates; its report is passed to this
stage. The last commit is the B4 specs.

Read first:
- `tools/training.py`: `FittedModel`, `_predict_matrix`, `_check_metric_task`, `scorer_for`,
  `is_binary_only`, and how `train_estimator` takes `y`;
- `tools/evaluation.py`: `_align`, `_positive_class`, `_hard_labels`, `_proba_columns`,
  `_class_labels`, `_check_truth_classes_have_columns`, `_load_truth_target`, `_snapshot`,
  `plot_curve`, `_new_figure`, `_save_curve`;
- `registry.py` (`interpretable`) and `tools/encoding.py` (`FittedScaler`).

**Reuse, do not duplicate.** Rename the helpers this chunk calls to public names (drop the
leading underscore) with no behaviour change, as chunk 14 did for `training.py`. No test
references them today.

### Findings from probing (sklearn 1.9.0, `-W error`, 2026-10-02)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | `sklearn.inspection.permutation_importance(est, X, y, scoring=scorer_for(metric), n_repeats, random_state=seed, n_jobs=1)` works for every metric; for `log_loss` and `brier` the scorer is negated, so importance is still "drop in score", positive = important | One sign convention for all metrics. 4 features × 5 repeats on RF(50) and 300 rows ≈ 0.04 s |
| P2 | It works on a `CalibratedClassifierCV` and on HGB with NaN in `X` | No special cases |
| P3 | `CalibratedClassifierCV` has no `coef_`: it holds one calibrated copy per fold | `coefficient_importance` refuses a calibrated model |
| P4 | Binary `LogisticRegression.coef_` has shape (1, p) and refers to `classes_[1]`; multiclass is (K, p) | When the contract's positive class is `classes_[0]`, negate, so a positive coefficient always means "towards the positive class" |
| P5 | Brute-force partial dependence is a loop: set column j to v for every row, then average the prediction | Written directly, about 15 lines. This avoids `sklearn.inspection.partial_dependence`'s method selection, and handles calibrated models and NaN the same way `predict` does |

### Decisions this chunk makes

**D-a. The model knows its target.** The interpretation tools read `FittedModel.target` and
`FittedModel.features`; the catalogue's `target` parameter is dropped. A frame without those
columns is `tool_param`, through `predict_matrix`.

**D-b. Importance is per model column.** Permuting each one-hot dummy separately produces rows
with zero or two levels active. It still measures how much the model relies on that column, and
it is what sklearn reports. Grouped permutation by source column is not built; mark it with a
`ponytail:` comment naming the ceiling and the upgrade (a `groups` parameter fed from the
encoder's levels).

**D-c. Standardised coefficients are coef × the population sd of the train column.** This makes
scaled and unscaled matrices comparable: a standard-scaled column has sd 1, and a 0/1 dummy has
sd √(p(1−p)). Hence `coefficient_importance(model, train)`; the parameter named `train` keeps it
under the lineage rule. The sign follows the contract's positive class (P4).

**D-d. PDP rendering is a `plot_curve` kind.** `partial_dependence` writes a `curve` artifact of
kind `partial_dependence`, and `plot_curve` gains that kind, drawing one line per series. The
catalogue's `plot_partial_dependence` is deleted. The 86-tool count holds: chunk 17 turned
`bin_numeric` into two tools, and this removes one.

**D-e. A PDP can be read in original units.** With the optional `scaler` (the candidate's
`FittedScaler`), a scaled column's grid is mapped back with `x · scale + center` into
`x_original`. A column the scaler did not touch (a dummy) has `x_original = x`. Without a
scaler, `x_original` is `null`.

**D-f. Error analysis segments the readable frame.** `analyze_errors` aligns predictions by label
(chunk 14 D-b) to a `truth` frame that carries both the target and the `by` columns. That frame
is normally the **untransformed** valid split, which has the same labels, because no transform
resets the index, and the original categories.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/interpretation.py`: the 4 tools |
| modify | `tools/evaluation.py`: public helper names; `plot_curve` gains `partial_dependence` |
| modify | `tools/training.py`: `predict_matrix`, `check_metric_task` made public |
| modify | `tools/__init__.py`: one import line |
| create | `tests/test_tools_interpretation.py` |
| modify | this file's frontmatter; `00_index.md` row 18 → completed and B4 ✅ (and M7a if `00_index.md` says B4 closes it); `WHATS_NEXT.md` state table (measured), "Next" → batch B5 (chunks 19 + 20), and a combined "Batch B4 — completed (summary)" that absorbs chunk 17's section |

---

## 3. Implementation Spec

**Conventions:**
- `phase="interpretation"`, `allow_in=["model_interpretation"]`.
- `permutation_importance` and `partial_dependence` are `heavy=True`.
- sklearn is imported lazily; `n_jobs=1`.
- Tables are `table` artifacts and curves are `curve` artifacts (strict JSON); every summary
  number goes through `json_scalar`.

### 3.1 `permutation_importance(model, frame, metric: Metric, n_repeats: int = 5 (1–50), seed: <required>)` (heavy)

- `metric` is checked against the model's task (`check_metric_task`); a multiclass `brier` is
  `tool_param` (`is_binary_only`).
- `X = predict_matrix(frame, fitted)`; `y` is `frame[fitted.target]`, taken exactly the way
  `train_estimator` takes it.
- Calls P1. The table has one row per feature, `{feature, importance_mean, importance_std}`,
  sorted by mean descending with ties broken by name.
- Summary: `metric`, `baseline` (the scorer on the unpermuted data, in natural direction),
  `n_repeats`, `n_features`, and the top 10 rows.

### 3.2 `coefficient_importance(model, train)`

- `ESTIMATORS[fitted.name].interpretable` must be true, else `tool_param` ("use
  permutation_importance"). A calibrated model is `tool_param` (P3).
- Each feature gets its raw coefficient and `std_coef = coef × sd_ddof0(train[feature])`
  (`float64`, NaN skipped). A constant column has sd 0, so `std_coef` is 0.
- Binary: one row per feature, sign-aligned to `positive_class(snapshot, classes)` (P4).
  Multiclass: one row per (class, feature). Regression: one row per feature.
- The intercept is reported separately.
- Summary: `positive_class` (binary), `intercept`, and the top 10 by `|std_coef|` (multiclass:
  the largest `|std_coef|` across classes).

### 3.3 `partial_dependence(model, frame, column, grid_resolution: int = 20 (2–100), scaler: ArtifactRef | None = None)` (heavy)

- `column` must be one of `fitted.features`.
- Grid, from the column's non-null values in `frame`:
  - if there are at most `grid_resolution` distinct values, use them sorted;
  - otherwise use the unique values of `np.quantile(values, linspace(0.05, 0.95, grid_resolution))`;
  - a bool column's grid is `[0.0, 1.0]`.
- P5 on a copy of the matrix.
- Series:
  - regression: `{"prediction": [...]}`;
  - binary: `{positive_class: [...]}` (the mean P(positive));
  - multiclass: one series per class.
- Curve payload: `{"kind": "partial_dependence", "column", "x", "x_original", "series", "task",
  "n_rows"}`. `x_original` follows D-e. A `scaler` that is not a `FittedScaler` is `tool_param`
  (`load_fitted`).
- `plot_curve` on this kind:
  - x axis: `x_original` when present, else `x`, labelled with the column name and, when scaled
    without a scaler, "(scaled)";
  - y axis: "average prediction" or "average P(class)";
  - one line per series, with a legend when there are several.

### 3.4 `analyze_errors(predictions, truth, target, by: tuple[str, ...] (min 1), threshold: float | None = None)`

- Aligns by label (`align`; an exact label-set match, else `tool_param`).
- **Task:**
  - `proba__` columns mean classification: hard labels via `hard_labels`, with `threshold`
    binary only as in chunk 14;
  - a `prediction` column means regression;
  - neither is `tool_param`.
- `by` columns must be in `truth` and must not be the target.
- **Segments:**
  - a numeric (`is_numeric_column`) column with more than `ERROR_SEGMENT_MAX_LEVELS = 10`
    distinct values is cut into quartiles (`pd.qcut(..., duplicates="drop")`, labels as
    strings);
  - otherwise segments are `as_str` levels;
  - nulls form a `"<missing>"` segment.
- Per segment: `n`, `error` (error rate, or MAE), `bias` (regression only: mean of prediction −
  truth), and `small` (`n < MIN_SEGMENT_ROWS = 10`).
- **Output:**
  - the table holds every `(column, segment)` row, sorted by error descending, then `n`
    descending;
  - summary: `task`, `metric` (`"error_rate"` or `"mae"`), `overall_error`, `n_segments`, and
    `worst`, the top 5 rows that are **not** `small`.

---

## 4. Test Suite Spec (~55)

**Permutation.**
- Equal to sklearn called directly with the same arguments (oracle).
- On a planted two-feature model, the informative feature ranks first and noise is near 0.
- Same seed gives the same result; a different seed gives a different result.
- A regression metric on a classifier is `tool_param`; multiclass `brier` is `tool_param`.
- A frame missing a fitted feature is `tool_param`. A calibrated model works.

**Coefficients.**
- `std_coef` equals `coef_ × sd` computed by hand.
- With an explicit contract `positive_class` equal to `classes_[0]`, every binary coefficient's
  sign flips relative to sklearn's.
- Multiclass gives K × p rows.
- Random forest and calibrated models are `tool_param`.
- On a standard-scaled matrix, `std_coef` equals `coef` for the scaled columns, to 1e-12.

**Partial dependence.**
- For `linear_regression`, consecutive PD differences divided by grid differences equal the
  fitted coefficient, to 1e-9 (the known answer).
- For binary logistic, PD is monotone in the coefficient's sign.
- With a scaler, `x_original` equals `x · scale + center`. A dummy column gets `x_original = x`
  and the grid `[0, 1]`.
- A low-cardinality column's grid is its unique values.
- A non-feature column is `tool_param`. Multiclass gives K series.
- `plot_curve` renders the kind as a PNG, with a `$`-containing title.

**Errors.**
- A planted segment where every prediction is wrong ranks first in `worst`.
- A segment of 5 rows is `small` and absent from `worst`.
- A numeric `by` with 50 distinct values gives at most 4 segments; nulls go to `<missing>`.
- Shuffled prediction order gives an identical table.
- Regression `bias` has the right sign for a model that over-predicts by a constant.
- `by` equal to the target, or an unknown column, is `tool_param`.

**End to end.** On the skeleton's `subscribers` pipeline after node 10:
- `permutation_importance` ranks `tenure_months` (planted) above every `region__*` dummy (noise);
- `coefficient_importance` on the logistic model gives `plan__premium` a negative `std_coef`
  (planted −1.0 vs basic +0.9) with contract `positive_class` 1;
- `partial_dependence` on `tenure_months` with the scaler has a decreasing positive-class series
  and `x_original` within the raw column's range;
- `analyze_errors` on the raw valid split by `plan` and `region` returns rows for every level.

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 75 s. Coverage **100 %** line and branch. Chunk 13, 14
      and 17 tests pass **without edits**.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - `random_state` not passed to permutation importance;
  - `std_coef` without the sd factor;
  - no sign flip for a `classes_[0]` positive class;
  - PD overwrites the column on the first row only;
  - `x_original` computed as `(x − center) / scale`;
  - `analyze_errors` aligns by position;
  - `worst` includes `small` segments.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

P1–P5 do not reproduce; chunk 13, 14 or 17 tests need edits; anything needs `backend/` or
`contracts/`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The end-to-end summaries for the four tools, verbatim (permutation and coefficients: top 5
   only).
4. For chunk 19: what `assemble_report` needs from these artifacts that their summaries do not
   carry.
