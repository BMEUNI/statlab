---
status: untouched
dependencies: [07, 13]
completion_date:
implementation_notes: |
---

# Chunk 14 — Evaluation tools

**Goal.** The eleven evaluation tools from `PLAN.md` §6.3 plus `rank_models` (champion phase,
pulled forward so the chunk 16 gate is self-contained). Walking-skeleton `[v1]`:
`score_classification`, `score_regression`, `compute_confusion_matrix`, `compare_models`,
`rank_models`.

This is where D33 becomes code:
- every score carries a bootstrap 95 % CI (D33b);
- a comparison says when the top two are not distinguishable;
- the top validation score is labelled a selection score (D33a).

Batch B2 runs this first and chunk 15 after it.

---

## 1. Prerequisites & Context

### State

Committed; see `git log -1`. 1729 tests (1728 passed, 1 skipped by design), 100 % line and
branch coverage, mypy clean on 83 files, suite ≈ 27 s.

Read `tools/training.py` and `registry.py` first. This chunk reuses chunk 13's matrix building,
CV splitter and scorer mapping. **Make the needed helpers public in `training.py`; do not
duplicate them.**

| Symbol | Location | Use here |
|---|---|---|
| `FittedModel`, splitter, scorer mapping, feature-matrix builder | `tools/training.py` | `cross_validate_estimator`, `compute_learning_curve` |
| `ESTIMATORS`, `EstimatorEntry` | `registry.py` | gains `interpretable: bool` (§3.5) |
| `predictions` artifacts | chunk 13 | indexed by the frame's labels; columns `prediction`, or `proba__{class}` in `FittedModel.classes` order |
| `as_str`, `json_scalar`, `require_columns`, `save_frame`, `load_fitted` | `tools/_frame.py` | |
| `save_json` (strict), `load_json`, `save_plot` | `statlab_tools/artifacts.py` | score tables and curves are JSON; plots are PNG via a pyplot-free `Figure` with `parse_math=False` (chunk 09) |

### Findings from probing (sklearn 1.9.0, `-W error`)

| # | Behaviour | Consequence |
|---|---|---|
| P1 | Nine sklearn classification metrics × 1000 resamples on 48 rows take **2.8 s**; five regression metrics take 0.4 s | Acceptable per call in production. Tests set the module constant `BOOTSTRAP_RESAMPLES` lower with `monkeypatch`, except one test that runs the real 1000 |
| P2 | A resample missing a class makes AUC raise and `balanced_accuracy` warn ("y_pred contains classes not in y_true"), which is an error under `-W error` | **Stratified bootstrap** for classification: resample within each true class, so class counts are fixed and every metric is defined in every resample. Plain bootstrap for regression |
| P3 | Multiclass works for `roc_auc_score(multi_class="ovr")`, macro `average_precision_score` on a one-hot truth, and `brier_score_loss` | Every classification metric is defined for multiclass too |
| P4 | `roc_curve`'s first threshold is `inf`, and strict JSON rejects `inf` | Drop that point, or store it as `null`. `save_json(allow_nan=False)` will fail otherwise |
| P5 | `learning_curve(..., n_jobs=1, cv=<seeded splitter>, shuffle=True, random_state=seed)` works under `-W error` | `compute_learning_curve` |

### Decisions this chunk makes

**D-a. Classification tools take probabilities.** `score_classification`,
`compute_confusion_matrix` and the three curves take `probabilities` (`predict_proba` output).
Hard labels come from argmax, or for binary from `p(positive) >= threshold` (default 0.5). A
`predictions` artifact without `proba__` columns is `tool_param`. The catalogue's `predictions`
parameter name was notation. Regression takes `predictions`.

**D-b. Truth is a frame plus `target`, aligned by label.** The predictions' index must equal the
truth frame's index as a set (same labels, same count), else `tool_param` naming the counts.
Truth is reordered to the predictions' order. Classes are compared by string form: `as_str(truth)`
against the `proba__` column suffixes. A truth class with no probability column is `tool_param`.

**D-c. Positive class.** It is the snapshot's `target.positive_class`, matched by string form.
Otherwise it is the last probability column, which is sklearn's convention, and the summary
reports `"positive_class_source": "default"`.

**D-d. Every score is a table artifact, and comparisons read those tables.** `score_*` write a
`table` artifact holding the metrics with CIs, `n_rows`, `task`, `positive_class`, and a
`truth_fingerprint`. The fingerprint is a sha256 of the sorted truth labels with their target
values. `compare_models` refuses tables whose fingerprints differ: candidates scored on different
rows are not comparable (D26). That replaces the catalogue's `split` parameter, which the tool
cannot verify.

**D-e. Bootstrap.**
- `BOOTSTRAP_RESAMPLES: Final = 1000`, with a percentile 95 % interval.
- The seed is orchestrator-supplied and required, as in chunks 10–13.
- The point estimate is computed on the full data, never as the bootstrap mean.
- All metrics share the same resample indices, so their CIs are coherent.

**D-f. "Not distinguishable" is CI overlap of the top two** (D33b). `compare_models` reports
`top_two_distinguishable: bool`. It is false when the leader's CI and the runner-up's CI overlap.
This is a deliberately conservative reading that the report states as "not distinguishable on
this validation set", not as a significance test.

**D-g. `rank_models` applies the contract; it does not re-score.** It reads `compare_models`'
table and the snapshot's `constraints.interpretability_required` and
`objective.success_threshold`. It flags each candidate:
- `eligible`, false for a non-interpretable estimator when interpretability is required;
- `meets_success_threshold`, direction-aware, only when the ranking metric is the primary metric
  or the primary metric is unset.

It carries D-f's flag for the top two **eligible**, and a `selection_note`: "the top validation
score is a selection score over N candidates; only the test score estimates performance" (D33a).
Node 12 adds the wave count W, which the tool cannot know.

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| create | `mcp_server/statlab_tools/tools/evaluation.py`: the eleven evaluation tools |
| create | `mcp_server/statlab_tools/tools/champion.py`: `rank_models` (its catalogue phase). Chunk 19 adds the rest |
| modify | `registry.py`: `EstimatorEntry.interpretable`, true for `logistic_regression` and `linear_regression` only. `list_estimators` reports it |
| modify | `tools/training.py`: expose the helpers named above (rename to public); no behaviour change. Chunk 13 tests pass **without edits** |
| modify | `tools/__init__.py`: two import lines |
| create | `tests/test_tools_evaluation.py`, `tests/test_tools_champion.py` |
| modify | this file's frontmatter, `00_index.md` row 14, and a short "Chunk 14 — completed" section in `WHATS_NEXT.md`. Chunk 15's stage writes the state table and "Next" |

---

## 3. Implementation Spec

**Conventions:**
- sklearn is imported **lazily inside tool bodies** (chunk 12's scipy fact).
- `n_jobs=1` everywhere (chunk 13's deadlock).
- `phase="evaluation"` for the eleven; `phase="champion"` for `rank_models`.
- `allow_in`:
  - scoring, confusion and curve tools: `["model_evaluation", "champion_selection"]`;
  - `plot_curve` and `select_threshold`: `["model_evaluation", "model_interpretation",
    "champion_selection"]`;
  - `cross_validate_estimator` and `compute_learning_curve`: `["model_evaluation"]`, `heavy=True`;
  - `compare_models`: `["model_evaluation", "champion_selection"]`;
  - `rank_models`: `["champion_selection"]`.

### 3.1 Scoring

**`score_classification(probabilities, truth, target, threshold: float | None = None, average: Literal["macro", "weighted"] = "macro", seed: <required>)`**
- `threshold` takes `gt=0, lt=1`, and is binary only: a value on a multiclass target is
  `tool_param`.
- Metrics: all nine `ClassificationMetric` values.
  - precision, recall and F1 use `zero_division=0`; they are binary for two classes, else
    `average`;
  - `roc_auc` and `pr_auc` use the positive column for binary, and macro one-vs-rest otherwise;
  - `log_loss` uses `labels=` all classes;
  - `brier`.
- Summary: `{"task": "classification", "metrics": {m: {"value", "ci_low", "ci_high"}},
  "n_rows", "classes", "positive_class", "positive_class_source", "threshold", "resamples"}`.
- Writes the same content as a `table` artifact (D-d), label `scores`.

**`score_regression(predictions, truth, target, seed: <required>)`**
- The five `RegressionMetric` values, with the same shape. `mape` is reported as `null` with a
  warning when any truth value is 0.

### 3.2 Confusion and curves

**`compute_confusion_matrix(probabilities, truth, target, threshold=None)`**
- Summary: `{"labels", "matrix"}` (rows are truth, columns are predicted, in class order), plus
  per-class `support`. Also written as a `table` artifact. No bootstrap.

**`compute_roc_curve` / `compute_pr_curve(probabilities, truth, target)`**
- Binary only; multiclass is `tool_param`.
- Writes a `curve` artifact:
  `{"kind": "roc"|"pr", "x": [...], "y": [...], "thresholds": [...], "auc", "n_pos", "n_neg",
  "positive_class"}`, with P4 handled.
- Summary: `{"kind", "auc", "points"}`.

**`compute_calibration_curve(probabilities, truth, target, n_bins: int = Field(10, ge=2, le=50))`**
- Binary only. `{"kind": "calibration", "x": mean_predicted, "y": fraction_positive, ...}`.

**`select_threshold(curve, objective: Literal["f1", "target_recall", "min_cost"], target_recall: float = 0.8, cost_fn_to_fp: float = 1.0)`**
- Requires a `roc` curve artifact; any other kind is `tool_param`. Counts come from `fpr`,
  `tpr`, `n_pos` and `n_neg`.
- The objectives:
  - `f1`: maximise F1;
  - `target_recall`: the highest threshold with recall ≥ `target_recall`;
  - `min_cost`: minimise `FP + cost_fn_to_fp · FN`.
- The docstring states which parameter each objective uses, and that a threshold chosen on
  valid is itself a selection decision (D33a).
- Summary: `{"threshold", "precision", "recall", "objective_value"}`.

**`plot_curve(curve, title: str = Field(max_length=120))`**
- Renders any curve artifact kind (`roc`, `pr`, `calibration`, `learning`). An unknown kind is
  `tool_param`.
- Output is a PNG artifact; `parse_math=False` on every text call.

### 3.3 CV on train

**`cross_validate_estimator(train, target, name, metric, params={}, class_weight="none", seed: <required>)`**
- Chunk 13's matrix, splitter (contract `cv_folds`) and scorer.
- Summary: `{"metric", "fold_scores", "mean", "std", "greater_is_better"}`, sign-corrected as in
  chunk 13. No artifact.

**`compute_learning_curve(train, target, name, metric, params={}, class_weight="none", seed: <required>)`**
- `train_sizes=np.linspace(0.2, 1.0, 5)`, with P5's arguments.
- Writes a `curve` artifact `{"kind": "learning", "sizes", "train_mean", "train_std",
  "valid_mean", "valid_std"}`, sign-corrected.

### 3.4 Comparing

**`compare_models(candidates: dict[str, ArtifactRef], metric: Metric)`**
- `candidates` maps a candidate label to a score table, with `min_length=2`.
- Errors, each `tool_param`:
  - tables with mixed `task`;
  - a metric that does not belong to the task;
  - differing `truth_fingerprint`, naming which candidates differ (D-d).
- Ranks by value in the metric's natural direction. Ties keep label order.
- Writes a `table` artifact of rows `{"rank", "candidate", "value", "ci_low", "ci_high"}`.
- Summary: `{"metric", "ranking", "top_two_distinguishable"}` (D-f).

### 3.5 Ranking under the contract — `champion.py`

**`rank_models(comparison, estimators: dict[str, str])`**
- `estimators` maps each candidate label to its registry name. Every label in `comparison` must
  be present, and every name must be in `ESTIMATORS`; otherwise `tool_param`.
- Applies D-g. Writes a `table` artifact.
- Summary: `{"ranking": [{"rank", "candidate", "estimator", "value", "eligible",
  "ineligible_reason", "meets_success_threshold"}], "top_eligible",
  "top_two_eligible_distinguishable", "selection_note", "n_candidates"}`.
- When no candidate is eligible, `top_eligible` is `null` with a warning. That is not an error;
  node 12 decides what to do about it.

---

## 4. Test Suite Spec (~75)

An autouse fixture in each test module sets `BOOTSTRAP_RESAMPLES` to 200. **One** test runs the
real 1000 and asserts it.

- **Metric correctness:** every point estimate equals sklearn's own function on the same inputs,
  binary and multiclass. The test calls sklearn directly as the oracle.
- **CIs:**
  - `ci_low ≤ value ≤ ci_high` on realistic data;
  - the same seed gives identical CIs, and a different seed different ones;
  - the CI narrows as n grows (100 vs 1000 rows);
  - a stratified resample never drops a class: a 5-positive / 43-negative truth scores every
    metric without warning under `-W error`. **This test must fail with plain bootstrap**
    (mutation below).
- **Alignment (D-b):**
  - predictions in shuffled order score identically to aligned ones;
  - a missing label, an extra label, and a truth class without a probability column are each
    `tool_param`;
  - a non-range index works.
- **Positive class (D-c):**
  - contract `positive_class=0` flips binary precision/recall relative to the default;
  - a string-class target works.
- **Threshold:** threshold 0.9 vs 0.1 changes recall in the right direction; a threshold on
  multiclass is `tool_param`.
- **Regression:** metrics equal sklearn's; a truth containing 0 gives `mape` `null` with a warning.
- **Curves:**
  - ROC/PR AUC equal `roc_auc_score`/`average_precision_score`;
  - each artifact survives `json.loads` strictly (P4);
  - multiclass is `tool_param`;
  - the calibration curve has `n_bins` or fewer points.
- **`select_threshold`:**
  - on a hand-built ROC, each objective's answer matches a hand computation;
  - a `pr` curve input is `tool_param`.
- **`plot_curve`:** each of the four kinds renders a PNG, and an unknown kind is `tool_param`.
- **CV and learning curves:**
  - `cross_validate_estimator` fold count equals the snapshot's `cv_folds`;
  - the learning curve has 5 sizes;
  - the CV calls are single-threaded (a spy, as in chunk 13).
- **`compare_models`:**
  - ranks in the right direction for `roc_auc` and for `rmse`;
  - overlapping CIs give `top_two_distinguishable` false, and well-separated ones true;
  - mismatched fingerprints are `tool_param`, naming the candidates;
  - mixed tasks are `tool_param`.
- **`rank_models`:**
  - with `interpretability_required` the forest is ineligible and `top_eligible` is the
    logistic model;
  - `success_threshold` is direction-aware for `rmse`;
  - no eligible candidate gives `null` plus a warning;
  - an unknown label or estimator is `tool_param`;
  - the selection note names N.
- **End to end:** on `churn_frame`, run B1's pipeline, then `predict_proba` → `score_classification`
  for the three classifiers → `compare_models` → `rank_models`.
- Strict JSON on every result. One MCP round-trip of `score_classification`.

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 60 s. Coverage **100 %** line and branch. Chunk 13 tests
      pass **without edits**.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - plain bootstrap instead of stratified;
  - the point estimate replaced by the bootstrap mean;
  - truth not reordered to the predictions' order;
  - remove the fingerprint check;
  - CI overlap computed against the third-ranked candidate instead of the second;
  - `rank_models` ignores `interpretability_required`;
  - the positive class is always the last column.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

P1–P5 do not reproduce; chunk 13 tests need edits; anything needs `backend/` or `contracts/`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The end-to-end `rank_models` summary, verbatim.
4. The real-1000 scoring time on 48 rows.
5. For chunk 16: is anything in the skeleton path still missing a tool?
