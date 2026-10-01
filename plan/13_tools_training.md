---
status: completed
dependencies: [07, 12]
completion_date: 2026-10-01
implementation_notes: |
  `registry.ESTIMATORS` (the six D25 estimators as frozen `EstimatorEntry` rows with a
  `(seed, class_weight)` factory) and six tools in `tools/training.py`. sklearn is imported
  lazily inside the bodies, as in chunk 12, so `tools/__init__` stays scipy-free. The contract
  snapshot supplies `cv_folds` and `max_fit_seconds`; `tune_estimator` is a manual
  `ParameterGrid` loop over `cross_val_score` that stops at the budget and returns params, not a
  model. 58 tests in `test_tools_training.py` and `test_registry.py`.

  The implementation stage stalled for an hour and was cancelled by the idle deadline. Root
  cause: `cross_val_score(..., n_jobs=-1)` in the tuning loop started loky worker processes from
  a tool-body thread, and the test run deadlocked, leaving orphaned resource-tracker processes.
  This also breaks D17 (`n_jobs=1` everywhere). The reviewer fixed it to `n_jobs=1` and added
  `test_tuning_cv_is_single_threaded`, which spies on every `cross_val_score` call; `n_jobs=2`
  fails it. Two more review tests:
  - `test_predict_is_independent_of_frame_column_order`: the DoD mutation "predict uses frame
    column order" survived the original suite, because every test frame had its columns in fitted
    order;
  - `test_tune_best_params_is_the_argmax_of_results`, for a greater- and a lower-is-better
    metric.
  All seven DoD mutations now fail at least one test. Housekeeping was done by the reviewer.
  Gates: 1729 tests (1728 passed, 1 skipped), 100 % line and branch (3405 stmts, 886 branches),
  mypy clean on 83 files, ruff clean, suite 27 s.
---

# Chunk 13 — Training tools

**Goal.** The `ESTIMATORS` registry (six estimators, D25) and the six training tools from
`PLAN.md` §6.3:
- `list_estimators`, `train_estimator`, `predict` and `predict_proba` are walking-skeleton `[v1]`;
- `tune_estimator` and `get_model_params` are not.

This is where D34a (tuning by CV within train), D34b (calibration on train), D34e (seeds), D17
(`n_jobs=1`, the fit semaphore) and D36c (tuning stops at its budget) become code. It runs after
chunk 12 in batch B1 and consumes chunk 12's matrices.

---

## 1. Prerequisites & Context

### State

Chunk 12 is implemented in the working tree by the previous stage, uncommitted. Read its
`encoding.py` and the helpers it moved into `tools/_frame.py` (`require_columns`, `save_frame`,
`load_fitted`). This chunk uses all three.

| Symbol | Location | Use here |
|---|---|---|
| `tool(heavy=True)`, `ORCHESTRATOR_SUPPLIED`, `ToolParamError`, `ToolDataError` | `tool.py` | `train_estimator` and `tune_estimator` are heavy: they hold a fit-semaphore permit |
| `save_model` / `load_model`, `save_dataframe` | `statlab_tools/artifacts.py` | kinds `model` and `predictions` |
| `is_class_target`, `is_numeric_column`, `json_scalar` | `tools/_frame.py` | |
| contract snapshot read | `cleaning.cast_dtypes` | `DataContract.model_validate_json(paths.contract_snapshot_json.read_text())` |
| `ClassificationMetric`, `RegressionMetric`, `ClassWeight`, `ParamValue` | `statlab_contracts.vocabulary` | parameter types |

### Findings from probing (sklearn 1.9.0, `-W error`)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | `CalibratedClassifierCV(cv=StratifiedKFold(5, ...))` on a class with 3 rows **raises `ValueError`** | Pre-check: every class has at least `cv_folds` train rows, else `ToolDataError`. Otherwise it surfaces as `bug` |
| P2 | RandomForest and HistGB fit with NaN; LogisticRegression raises `ValueError` | Registry flag `handles_missing`; NaN plus an estimator without it is `tool_param` ("impute first") |
| P3 | `DataFrame.to_numpy(dtype="float64", na_value=np.nan)` converts `bool`, nullable `boolean` and `Int64` with NA correctly | One conversion for every allowed dtype |
| P4 | Multiclass `precision_macro` under `-W error`: the zero-division `UndefinedMetricWarning` makes **scoring fail** | Precision, recall and F1 scorers use `make_scorer(..., zero_division=0)` |
| P5 | `LinearRegression` has no `random_state`; HistGB accepts `class_weight` | Per-entry factory, not one shared constructor call |
| P6 | `sklearn` is untyped here (`ignore_missing_imports`), so its values are implicit `Any` | Keep sklearn objects behind `object`-typed dataclass fields and narrow at the boundary. Never write `Any` |

### Decisions this chunk makes

**D-a. The contract governs CV; the node chooses the metric.** `cv_folds` and `max_fit_seconds`
come from the snapshot's `modelling.tuning`. They are the user's budget, and the agent must not be
able to weaken them. `metric` is a parameter: node 2 picks the default (D33d), and the contract's
`primary_metric` may be unset.

**D-b. `tune_estimator` returns parameters, not a model.** One fit path: the node passes
`best_params` to `train_estimator`, which owns calibration and class weights. The tuning loop is a
manual `ParameterGrid` iteration with `cross_val_score`, because a `GridSearchCV` call cannot stop
partway. After each grid point the elapsed time is checked against `max_fit_seconds`. When the
budget is exceeded the loop stops, and returns the best point so far with
`points_evaluated < points_total` and a warning (D36c). At least one point is always evaluated.
The time source is the module-level `time.monotonic`, so a test can advance it.

**D-c. CV always shuffles, with the injected seed.** The splitter is
`StratifiedKFold(cv_folds, shuffle=True, random_state=seed)` for a classifier and `KFold(...)`
for a regressor, in both tuning and calibration. Chunk 10 keeps the input's row order, so an
unshuffled split of target-sorted or time-sorted data would make biased folds.

**D-d. Every non-target column of the frame is a feature.** The node has already dropped IDs and
anything else unwanted. Features must be numeric or bool, nullable included. A string, datetime or
categorical column is `tool_param` listing them ("encode first"). The fitted feature order is
stored, and `predict` selects exactly those columns, ignoring extras such as the target. A missing
fitted column is `tool_param`.

**D-e. Warnings are not captured.** `warnings.catch_warnings` mutates process-global state, and
tool bodies run in threads. So the registry avoids the common warning sources instead:
`LogisticRegression(max_iter=1000)`, and zero-division-safe scorers (P4). Tests use data that
converges.

**D-f. Task compatibility is checked by shape, not by contract type:**
- A classifier needs `is_class_target(train[target])`.
- A regressor needs `is_numeric_column(train[target])`. That admits a count target with few
  levels, which is right for regression.
- Metric and estimator task must agree.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/registry.py`: `ESTIMATORS` (§3.1). The plan names it `registry.ESTIMATORS` |
| create | `mcp_server/statlab_tools/tools/training.py` |
| modify | `tools/__init__.py`: one import line |
| create | `tests/test_tools_training.py`, `tests/test_registry.py` |
| modify | this file's frontmatter, `00_index.md` row 13, `WHATS_NEXT.md` (state table measured; "Next" set to batch B2, chunks 14 + 15; a combined `## Batch B1 — completed (summary)` covering chunks 12 and 13) |

---

## 3. Implementation Spec

### 3.1 `registry.ESTIMATORS`

```python
@dataclass(frozen=True)
class EstimatorEntry:
    task: Task
    build: Callable[[int, ClassWeight], object]     # (seed, class_weight) -> unfitted estimator
    default_grid: dict[str, tuple[ParamValue, ...]]
    handles_missing: bool
    supports_class_weight: bool
```

| Name | Build | `default_grid` | missing | class_weight |
|---|---|---|---|---|
| `logistic_regression` | `LogisticRegression(max_iter=1000, random_state=seed)` | `C: (0.1, 1.0, 10.0)` | no | yes |
| `random_forest_classifier` | `RandomForestClassifier(n_estimators=200, n_jobs=1, random_state=seed)` | `max_depth: (None, 5, 10)`, `min_samples_leaf: (1, 5)` | yes | yes |
| `hist_gb_classifier` | `HistGradientBoostingClassifier(random_state=seed)` | `learning_rate: (0.05, 0.1)`, `max_leaf_nodes: (15, 31)` | yes | yes |
| `linear_regression` | `LinearRegression()` | `{}` | no | no |
| `random_forest_regressor` | as the classifier | as the classifier | yes | no |
| `hist_gb_regressor` | `HistGradientBoostingRegressor(random_state=seed)` | as the classifier | yes | no |

A class weight other than `"none"` on an entry without support is `tool_param`. `"none"` maps to
`class_weight=None`. Adding an estimator is one entry (§6.2).

`SCORERS: dict[tuple[Task, Metric], str | object]` maps our metric names to sklearn scorers:
- classification:
  - `roc_auc` → `roc_auc` (binary) or `roc_auc_ovr` (multiclass);
  - `pr_auc` → `average_precision`;
  - `log_loss` → `neg_log_loss`;
  - `brier` → `neg_brier_score`, binary only, so multiclass is `tool_param`;
  - `accuracy` and `balanced_accuracy` as-is;
  - `precision`, `recall` and `f1` via `make_scorer(..., zero_division=0)`: binary, or
    `average="macro"` for multiclass.
- regression:
  - `rmse`, `mae`, `mape`, `medae` → `neg_root_mean_squared_error`, `neg_mean_absolute_error`,
    `neg_mean_absolute_percentage_error`, `neg_median_absolute_error`;
  - `r2` → `r2`.

Reported CV scores are **sign-corrected to the metric's natural direction** (an RMSE is
positive), with `"greater_is_better"` stated in the summary.

### 3.2 Tools

**`list_estimators(task: Task | None = None)`**
- `needs_run=False`, `allow_in=["experiment_design", "model_training"]`.
- Returns, per entry: `task`, `default_grid`, `handles_missing`, `supports_class_weight`,
  `supports_proba` (true for classifiers).

**`train_estimator(train, target, name, params: dict[str, ParamValue] = {}, class_weight: ClassWeight = "none", calibrate: bool = False, seed: int = <ORCHESTRATOR_SUPPLIED, required>)`**
- `heavy=True`, `allow_in=["model_training"]`.
- Errors:
  - an unknown `name` is `tool_param`, listing the valid names;
  - a `params` key the estimator does not accept (checked against `get_params()`) is
    `tool_param`. `n_jobs` and `random_state` are **not overridable**: naming either is
    `tool_param`, because D17 and D34e own them.
- Then:
  - build the matrix (D-d);
  - check task compatibility (D-f);
  - if `calibrate`, require a classifier, run P1's pre-check, and wrap in
    `CalibratedClassifierCV(est, cv=<D-c splitter>, method="sigmoid")`.
- Fit on the `float64` matrix, never on a DataFrame. Feature-name warnings must not be possible.
- Saves `FittedModel(name, task, target, features: tuple[str, ...], classes: tuple[object, ...],
  params, class_weight, calibrated, seed, estimator: object)` as kind `model`, label `name`.
- Summary: `{"name", "task", "features": n, "train_rows", "classes", "params" (the effective
  `get_params()` subset of the estimator's own keys, json_scalar or `repr` for non-JSON values),
  "calibrated", "cv_folds" (when calibrated)}`.

**`tune_estimator(train, target, name, metric: Metric, param_grid: dict[str, tuple[ParamValue, ...]] | None = None, class_weight: ClassWeight = "none", seed: <required>)`**
- `heavy=True`, `allow_in=["model_training"]`.
- `param_grid=None` uses the entry's `default_grid`. An empty grid is `tool_param`: there is
  nothing to tune, so call `train_estimator`.
- Each key is validated as in `train_estimator`. Each value is non-empty. The grid has at most
  `MAX_GRID_POINTS = 50` points, else `tool_param`.
- D-b's loop, with D-c's splitter. Same class-count pre-check as P1, against `cv_folds`.
- Summary: `{"best_params", "best_score", "metric", "greater_is_better", "points_evaluated",
  "points_total", "budget_seconds", "stopped_early", "results": [{"params", "mean", "std"}]}`.
  `results` is capped at `MAX_GRID_POINTS`, so it needs no truncation flag.
- No artifact.

**`predict(model, frame)`** and **`predict_proba(model, frame)`**
- `allow_in=["model_evaluation", "model_interpretation", "champion_selection"]`.
- Feature selection as in D-d. NaN plus `handles_missing=False` is `tool_param`.
- Output is kind `predictions`, **indexed by the frame's labels** (chunk 14 aligns predictions to
  truth by label):
  - `predict`: one column, `prediction`;
  - `predict_proba`: `proba__{class}` per class in `classes` order. A regressor is `tool_param`.
- Summary: `{"rows", "columns"}`.

**`get_model_params(model)`**
- `allow_in=["model_training", "model_evaluation", "champion_selection"]`.
- Summary of `FittedModel`'s metadata and params. No artifact.

`model` and `frame` loading uses `load_fitted(paths, ref, FittedModel, "model")`, so a wrong-type
ref is `tool_param`.

---

## 4. Test Suite Spec (~70)

Keep fits small: 150–300-row hand-built frames, and `n_estimators` overridden through `params`
where speed matters. The **whole suite must stay under 45 s**; it is about 17 s now.

- **Registry:**
  - exactly the six D25 names, with tasks as in the table;
  - every `default_grid` key is a real `get_params()` key of its estimator;
  - `build(seed, ...)` passes the seed to every estimator that has `random_state`;
  - every forest has `n_jobs == 1`.
- **Train:**
  - a separable binary frame trains and predicts above 0.9 accuracy;
  - a regression frame gives R² above 0.9;
  - multiclass and string-class targets work.
- **Errors:**
  - unknown name;
  - an unknown param, `n_jobs` and `random_state` are each `tool_param`;
  - `class_weight="balanced"` on a regressor is `tool_param`;
  - a string feature column is `tool_param` naming it;
  - NaN with logistic regression is `tool_param`, while NaN with HistGB trains.
- **Calibration:**
  - `calibrate=True` produces a `CalibratedClassifierCV` and different probabilities from the
    uncalibrated fit;
  - on a regressor it is `tool_param`;
  - a class below `cv_folds` rows is `data` (P1).
- **Seeds:**
  - the same seed gives identical forest predictions, and a different seed different ones;
  - the CV splitter shuffles: on a target-sorted train, tuning results differ between two seeds.
- **Tune:**
  - finds the obviously best `C` on a constructed frame;
  - scores are sign-corrected (RMSE positive);
  - a grid over 50 points and an empty grid are `tool_param`;
  - `brier` on multiclass is `tool_param`;
  - multiclass `f1` does not fail under `-W error` (P4).
- **Budget (D36c):** monkeypatch `training.time.monotonic` to advance 10 000 s per call. Then
  `points_evaluated == 1`, `stopped_early` is true, a warning is present and `best_params` is
  that point. With the clock not advancing, every point is evaluated.
- **The contract governs:** `cv_folds` from the snapshot (set it to 3) is the fold count actually
  used; assert via the splitter or the result count.
- **Predict:**
  - labels preserved on a non-range index;
  - extra columns, including the target, are ignored;
  - a missing feature is `tool_param`;
  - probabilities sum to 1 and their columns follow `classes`;
  - `predict_proba` on a regressor is `tool_param`.
- **End to end (B1 integration):** on `churn_frame`:
  - run chunks 10 → 11 → 12 → 13: `split_stratified` → drop ID and free text → impute → one-hot →
    scale → train all three classifiers → `predict_proba` on valid. Every step succeeds, and
    valid probabilities have the right shape and labels;
  - one regression path on a numeric target likewise, with the three regressors.
- Strict JSON on every result. One MCP round-trip of `train_estimator` (it is heavy, so it goes
  through the semaphore path) and `predict`.

---

## 5. Definition of Done

- [ ] `make test` exits 0 in under 45 s. Coverage **100 %** line and branch.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - `shuffle=False` in the splitter;
  - `random_state` hard-coded to 0;
  - calibration skipped when `calibrate=True`;
  - remove the budget check;
  - `predict` uses `frame.columns` order instead of the fitted features;
  - remove the class-count pre-check;
  - `n_jobs=-1` on the forests.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore` (P6: narrow, do not suppress; if a stub gap truly forces one, **stop and
      ask**).
- [ ] Housekeeping per §2, including measured numbers. **Do not commit.**

### Stop and ask if

P1–P5 do not reproduce; typing sklearn seems to need `Any` or `# type: ignore`; the suite exceeds
45 s; anything needs a change under `backend/` or `contracts/`.

### Report back with

1. Gate numbers and suite runtime.
2. The mutation table.
3. The B1 end-to-end test's three classifiers: valid ROC-AUC per model on `churn_frame` (it is
   near-random data; the point is that the pipeline runs, not the score).
4. How sklearn's untyped values were kept out of `Any`.
5. For chunk 14: `predict_proba`'s column naming and label alignment. Is anything awkward for
   scoring against a truth artifact?


## 6. Addendum — review corrections (2026-10-01)

**Item (a): a float-coded binary target could not be scored.** `predict_proba` named its columns
`f"proba__{cls}"` with the raw class labels, so a `0.0/1.0` `float64` target (an integer target
with a null loads as `float64`) produced `proba__0.0`/`proba__1.0`. Evaluation then compares
`as_str(truth)` (`"0"`, `"1"`) against the suffixes and `score_classification` raised
"truth classes ['0', '1'] have no proba__ column" — a float-coded binary target could not be
scored at all, all gates green.

Fix: `predict_proba` now names columns `f"proba__{as_str_scalar(cls)}"` (the scalar twin of
`as_str` added to `_frame.py`), so the suffix is `"0"`/`"1"` and matches the truth's string form.
Regression test (in `tests/test_tools_evaluation.py`):
`test_float_coded_binary_target_scores_and_honours_contract` trains on a `0.0/1.0` target, asserts
the proba columns are `proba__0`/`proba__1`, scores successfully, and checks the honoured positive
class and recall differ between contract `positive_class` 1 and 0; it fails on the old code with
`proba__0.0`-style names. See `00_index.md`'s integral-float row and WHATS_NEXT.md correction 22.
