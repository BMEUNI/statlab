---
status: completed
dependencies: [41]
completion_date: 2026-10-08
implementation_notes: "42a-42d plus Addendum E; make check 3104 passed, 100%; npm test 77/77"
---

# Chunk 42 — faster runs, talkative chat, simpler decisions, blue redesign

User feedback 2026-10-08 after the first real (paid) run `r_261239930187a0a7` (Loan_default.csv,
255,347 rows, 5 candidates, 26 min):

1. "I dont like the UI, its hard to understand and look at, please redesign it (i like the color
   blue)"; text collides with the diagram while models are learning. Chosen look: **navy + light
   blue** (deep navy top bar and progress header, light blue-grey canvas, sky-blue highlights).
2. When a run finishes, the chat must say what happened: short summary of the steps, diagrams,
   the champion and why it was selected.
3. "the models are not fitted or evaluated on GPU". The machine is an Apple M4 Max (no CUDA;
   scikit-learn cannot use Metal). User chose **CPU speedups** instead: measured, 64 % of tool
   time was `score_classification` (6 calls, 1663 s: 1000 sklearn-metric bootstrap resamples on
   51k rows) and 26 % `tune_estimator` (5 calls, 681 s, one core).
4. Gate forms ask for too many parameters and are unclear: show only the main values by
   default, everything else in a collapsible "All settings" section; every field explained.
5. The agent barely uses the chat. The user wants to steer by chat ("go back to profiling",
   "set test size to 0.3") and wants the agent to talk more, with every detail clear. Fact: the
   run chat already routes messages (`backend/statlab/graph/router.py`): qa, status, decision
   (gate answers incl. edits, `gates.py:636`), change (rewind/fork), lifecycle (pause/resume/
   stop). It is undiscoverable and the agent never speaks unprompted.

Stage order (sequential, one implementer each): **42a** speed (MCP tools) -> **42b** blue
redesign + timeline/lanes collision fix -> **42c** talkative run chat (step narration, gate
prompts with quick replies, final summary with plots, capability hints) -> **42d** gate forms
(main fields + "All settings", field help). Specs for 42b–42d are written after the previous
stage's handoff.

## 42a — fast scoring and multi-core tuning

Goal: same numbers, much less time. (1) Classification bootstrap metrics computed with numpy on
integer class codes instead of nine sklearn calls on string labels per resample. (2) Tuning CV
folds and permutation-importance repeats run on threads (never loky processes).

Non-goals: no GPU, no new dependency, no regression-scoring change, no change to estimators'
own `n_jobs=1` (registry tests stay), no change to the fit semaphore size.

Relevant decisions: D17 (fit semaphore; `n_jobs=1` because loky workers started from a
tool-body thread deadlocked — ENVIRONMENT.md row "`n_jobs=-1` ... deadlocked"), D33b/D-e
(seeded stratified bootstrap, percentile CI, point estimate on full data), D34e (seeds).
PLAN addendum D17-42 (written by main with this spec): joblib **threading** backend is allowed
inside tool bodies; loky stays forbidden.

### Owned files

`mcp_server/statlab_tools/registry.py`, `mcp_server/statlab_tools/tools/evaluation.py`,
`mcp_server/statlab_tools/tools/training.py`, `mcp_server/statlab_tools/tools/interpretation.py`,
`tests/test_tools_evaluation.py`, `tests/test_tools_training.py`,
`tests/test_tools_interpretation.py`, `tests/test_registry.py`.

### registry.py

- `import os` at module top.
- `def fit_threads() -> int`: `max(1, (os.cpu_count() or 1) // 4)`. Docstring: threads one heavy
  tool may use for its parallel loop (CV folds, permutation repeats); a quarter of the cores so
  concurrent candidates do not oversubscribe badly; threads only, loky processes deadlock (D17).
- `def parallel_jobs(name: str) -> int`: `1` when `estimators()[name].family ==
  "hist_gradient_boosting"` (HGB already runs OpenMP threads on every core), else
  `fit_threads()`.
- Update the module docstring sentence about forests' `n_jobs=1` to add: tool loops use
  `parallel_jobs` threads under joblib's threading backend.

### evaluation.py

1. `_stratified_indices` becomes two functions:
   - `_strata(y: np.ndarray) -> list[np.ndarray]`: `[np.flatnonzero(y == cls) for cls in np.unique(y)]`.
   - `_stratified_indices(strata: list[np.ndarray], rng: np.random.Generator) -> np.ndarray`:
     concatenation of `rng.choice(members, size=members.size, replace=True)` per stratum, in
     strata order. Same draws as today for the same `y` and seed.
   Keep the P2 docstring on `_stratified_indices`.
2. `_bootstrap_cis`: before the loop `strata = _strata(y) if stratified else None`; in the loop
   `idx = _stratified_indices(strata, rng) if strata is not None else _plain_indices(n, rng)`.
   Nothing else changes.
3. New private helpers (module section "--- fast classification metrics ---", above
   `_classification_metrics`):
   - `_class_codes(values: np.ndarray, classes: list[str]) -> np.ndarray`: position of each value
     in `classes` (dict lookup over `values.tolist()`), dtype `np.intp`. Callers have already run
     `check_truth_classes_have_columns`, so every value is present.
   - `_hard_codes(proba: np.ndarray, pos_idx: int, threshold: float | None) -> np.ndarray`:
     `threshold is None` -> `np.argmax(proba, axis=1)`; else (binary only) `np.where(proba[:, pos_idx] >= threshold, pos_idx, 1 - pos_idx)`.
     Same labels as `hard_labels` (which stays, used at evaluation.py:538 and interpretation.py:589).
   - `_binary_auc(scores: np.ndarray, is_pos: np.ndarray) -> float`: `n_pos = int(is_pos.sum())`,
     `n_neg = is_pos.size - n_pos`; either zero -> raise `ToolDataError("roc_auc is undefined: the truth has only one class")`;
     ranks via `scipy.stats.rankdata(scores)` (average ties; lazy import inside the function);
     return `(ranks[is_pos].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)` as float.
   - `_binary_ap(scores: np.ndarray, is_pos: np.ndarray) -> float` (sklearn's step-wise average
     precision): no positive -> raise `ToolDataError("pr_auc is undefined: the truth has no positive rows")`;
     `order = np.argsort(scores, kind="mergesort")[::-1]`; sorted scores `s`, sorted labels `y`
     as float64; `threshold_idx = np.r_[np.flatnonzero(np.diff(s)), y.size - 1]`;
     `tps = np.cumsum(y)[threshold_idx]`; `fps = threshold_idx + 1 - tps`;
     `precision = tps / (tps + fps)`; `recall = tps / tps[-1]`;
     return `float(np.sum(np.diff(np.r_[0.0, recall]) * precision))`.
4. Replace `_classification_metrics` (the sklearn version) with
   `_classification_metrics(codes: np.ndarray, proba: np.ndarray, pred: np.ndarray, pos_idx: int, average: str) -> dict[str, object]`,
   `k = proba.shape[1]`, `n = codes.size`, all float64:
   - `support = np.bincount(codes, minlength=k)`, `predicted = np.bincount(pred, minlength=k)`,
     `tp = np.bincount(codes[codes == pred], minlength=k)`.
   - per class with zero where the denominator is 0 (`np.divide(..., out=np.zeros(k), where=den > 0)`):
     `recall_c = tp / support`, `precision_c = tp / predicted`, `f1_c = 2 * tp / (support + predicted)`.
   - `accuracy = mean(codes == pred)`; `balanced_accuracy = mean(recall_c[support > 0])`;
     `log_loss = -mean(log(clip(proba[arange(n), codes], eps, 1 - eps)))`, `eps = np.finfo(np.float64).eps`.
   - binary (`k == 2`): precision/recall/f1 = the `pos_idx` entries; `is_pos = codes == pos_idx`,
     `s = proba[:, pos_idx]`; `roc_auc = _binary_auc(s, is_pos)`; `pr_auc = _binary_ap(s, is_pos)`;
     `brier = mean((is_pos - s) ** 2)`.
   - multiclass: `average == "macro"` -> plain means of the per-class arrays over all k classes;
     `"weighted"` -> `sum(support * x) / support.sum()`; `roc_auc` = mean over c of
     `_binary_auc(proba[:, c], codes == c)`; `pr_auc` likewise with `_binary_ap`;
     `brier = mean(sum((proba - one_hot) ** 2, axis=1))` with `one_hot = (codes[:, None] == arange(k)[None, :])`.
   - Every value returned as a Python float. Class columns are positional (the `proba__` order);
     this also fixes sklearn's lexicographic relabelling for classes like "2"/"10".
   Docstring: numpy implementation, sklearn is the test oracle; same metric definitions as
   before (zero_division=0, macro one-vs-rest, clipped log loss).
5. `score_classification` body after `positive, source = ...`:
   `pos_idx = classes.index(positive)`; `y_true = as_str(aligned.truth).to_numpy()` (unchanged);
   `codes = _class_codes(y_true, classes)`; `proba` unchanged; `pred = _hard_codes(proba, pos_idx, threshold)`;
   `full = _classification_metrics(codes, proba, pred, pos_idx, average)`;
   `compute(idx)` returns `_classification_metrics(codes[idx], proba[idx], pred[idx], pos_idx, average)`;
   `_bootstrap_cis(full, compute, y=y_true, ...)` (strings, so strata order and draws are unchanged).
   Update its docstring sentence "computed with sklearn as the oracle" to "computed with numpy
   (sklearn is the test oracle)". Remove imports that become unused.

### training.py

- Import `parallel_jobs` from `statlab_tools.registry` (by name).
- `tune_estimator`: `jobs = min(cv_folds, parallel_jobs(name))`; the `cross_val_score` call
  passes `n_jobs=jobs` and runs inside `with parallel_config(backend="threading"):`
  (`from joblib import parallel_config`, lazy inside the body next to the sklearn import).
  Update the docstring: folds run on `jobs` threads (D17 addendum 42); results are identical to
  the sequential loop.
- Comments/docstrings elsewhere saying "`n_jobs=1` (D17)" about tuning: update to the new rule.

### interpretation.py

- Import `parallel_jobs` from `statlab_tools.registry` (extend the existing import).
- `permutation_importance`: `jobs = parallel_jobs(fitted.name)`; the sklearn call passes
  `n_jobs=jobs` inside `with parallel_config(backend="threading"):` (lazy joblib import).
  Module docstring "`n_jobs=1` everywhere (D17)" -> "`n_jobs=1` except permutation repeats,
  which run on threads (D17 addendum 42)". P1 line likewise.

### Tests

- `tests/test_registry.py`: `fit_threads` with `os.cpu_count` monkeypatched to 16 -> 4, 2 -> 1,
  None -> 1. `parallel_jobs("hist_gb_classifier") == 1`, `parallel_jobs("hist_gb_regressor") == 1`,
  `parallel_jobs("random_forest_classifier") == fit_threads()`.
- `tests/test_tools_training.py`: replace `test_tuning_cv_is_single_threaded` with
  `test_tuning_cv_runs_folds_on_threads`: monkeypatch
  `statlab_tools.tools.training.parallel_jobs` to `lambda name: 1 if name.startswith("hist_gb") else 3`;
  spy on `sklearn.model_selection.cross_val_score` (as today) recording `kwargs["n_jobs"]` and
  `type(joblib.parallel.get_active_backend()[0]).__name__`; tune `logistic_regression` on
  `_separable_binary()` -> every record `(3, "ThreadingBackend")`. Then a second spy run tuning
  `hist_gb_classifier` (`param_grid={"learning_rate": (0.1,)}`) -> every `n_jobs` is 1.
  Keep the docstring's deadlock history and add that threads are the allowed path.
- `tests/test_tools_interpretation.py`: one test patching
  `statlab_tools.tools.interpretation.parallel_jobs` to `lambda name: 3` and spying on
  `sklearn.inspection.permutation_importance` (the tool imports it at call time) recording
  `n_jobs` and the active backend name -> `(3, "ThreadingBackend")`; the tool result's
  `importance_mean` values equal those of the existing oracle call with `n_jobs=1` (line ~151)
  — reuse that test's fixture/model.
- `tests/test_tools_evaluation.py`:
  - `test_mutation_plain_bootstrap_fails`: patch becomes
    `lambda strata, rng: ev._plain_indices(sum(int(m.size) for m in strata), rng)`, and it expects
    `pytest.raises(ToolDataError)` (import it from `statlab_tools.tool`); drop the `noqa`.
  - Add `_sklearn_reference(y_true, proba, y_pred, classes, positive, average)`: the previous
    sklearn `_classification_metrics` body copied verbatim into the test module (labels are
    strings, classes lexicographically sorted in these tests).
  - `test_fast_metrics_match_sklearn_binary`: rng seed 0, n=400, labels "neg"/"pos" with ~15 %
    "pos", p(pos) rounded to 2 decimals (ties), proba columns `[1-p, p]`, classes
    `["neg", "pos"]`, positive "pos". For thresholds `None` and `0.3`, for the full index and five
    stratified resamples (`_stratified_indices(_strata(y), np.random.default_rng(1))` drawn
    successively): every one of the nine metrics from `_classification_metrics(codes[idx], ...)`
    equals the reference on `y[idx]` with `pytest.approx(rel=1e-9, abs=1e-12)`.
  - `test_fast_metrics_match_sklearn_multiclass`: classes `["a", "b", "c"]`, n=300, Dirichlet
    probabilities rounded to 3 decimals and renormalised, `average` in `("macro", "weighted")`,
    full index plus three stratified resamples; same tolerance.
  - `test_fast_metrics_use_positional_class_order`: classes `["2", "10", "30"]` (not
    lexicographic), labels drawn from them; compare against the reference run on the same data
    relabelled `"2"->"a", "10"->"b", "30"->"c"` with classes `["a", "b", "c"]`.
  - `test_auc_and_ap_undefined_raise_tool_data_error`: `_binary_auc` with all-true and all-false
    `is_pos` raises `ToolDataError`; `_binary_ap` with all-false raises `ToolDataError`.
  - Existing full-data oracle tests stay unchanged and must pass.

### Checks

1. `make test-fast TESTS="tests/test_tools_evaluation.py tests/test_tools_training.py tests/test_tools_interpretation.py tests/test_registry.py"`.
2. `.venv/bin/python -m ruff check` and `ruff format --check` on the eight owned files;
   `.venv/bin/python -m mypy` on the four source files.
3. Timing probe (implementer, scratch file outside the repo, not committed): build 51,070 rows
   of binary truth (11.6 % positive) and proba, call `score_classification` with the real
   `BOOTSTRAP_RESAMPLES` (1000) through a temporary `RunPaths` and report wall time; report the
   machine's `fit_threads()`. Expected well under 30 s (was ~300 s).
Stop and report on any failure or gap. Receipt: files, commands, exits, counts, probe time.

### 42a handoff (2026-10-08)

Implemented with Addendum A. Main re-ran: `make test-fast TESTS="tests/test_tools_evaluation.py
tests/test_tools_training.py tests/test_tools_interpretation.py tests/test_registry.py"` exit 0
(172 tests); ruff check/format and mypy on the owned files exit 0. Implementer probe:
`score_classification` on 51,070 rows with 1000 resamples 8.55 s (real run: ~300 s per call);
`fit_threads()` = 4 on the M4 Max. Diff reviewed against the spec (strata draws unchanged,
positional class codes, numpy average ranks). Full `make check` deferred to chunk acceptance.

## 42b — navy + light blue redesign, readable progress

Goal: the app looks calm and is easy to read. Navy top bar and run status band, light
blue-grey canvas, one blue accent; chat as clear bubbles; the run header becomes a compact
status band; candidate lanes become readable cards whose step chips wrap (no text/diagram
collisions); raw ids are humanised everywhere in the run pane.

Non-goals: no behaviour change in data flow, no new API calls; gate forms (42d) and chat
narration (42c) untouched except styling.

Screens checked by main before writing this: `.playwright-mcp/42-real-lanes.png` (real run
`r_261239930187a0a7`: candidate labels like `hist_gradient_boosting_default_capacity` overrun
the dot track; the header is an 8-row fact list).

### Design plan (frontend-design skill; user picked "Navy + light blue")

- Tokens (`:root`, keep existing names so every page restyles; new names added):
  `--paper #EEF3F8` (canvas), `--surface #FFFFFF`, `--ink #13213A`, `--graphite #56677F`,
  `--rule #D6E0EB`, `--signal #1F6FD1` (accent: links, primary buttons, done markers),
  `--signal-ink #FFFFFF`, `--target #E3EEFB` (selection wash), `--gate #A85F00`,
  `--gate-soft #FFF3DF`, `--fault #C2352B`, `--fault-soft #FDECEA`, new `--navy #14213D`,
  `--navy-2 #1E2F54`, `--navy-ink #FFFFFF`, `--navy-muted #A9B8D3`, `--sky #4EA8DE`,
  `--radius 10px`, `--radius-lg 16px`, `--shadow 0 1px 2px rgba(19, 33, 58, 0.06), 0 6px 20px rgba(19, 33, 58, 0.05)`.
  Avatar and brand mark gradient: `radial-gradient(circle at 35% 35%, var(--sky), var(--signal))`.
- Type unchanged (Atkinson Hyperlegible Next / Mono). Numbers in stats use
  `font-variant-numeric: tabular-nums`.
- Memorable element: the navy run band at the top of the progress pane (status in large type,
  three stats, actions), echoing the navy top bar.
- Layout unchanged otherwise (two panes, mobile switch).

### Owned files

`frontend/src/style.css`, `frontend/src/run/RunHeader.tsx`, `frontend/src/run/RunTimeline.tsx`,
`frontend/src/run/model.ts`, `frontend/tests/run-model.test.ts`, `frontend/src/run/CandidateTable.tsx`,
`frontend/src/pages/RunPage.tsx` (one prop), `frontend/src/chat/MessageList.tsx` (no change
expected; styling only — listed in case a wrapper class is needed: do not edit unless the
recipe says so).

### model.ts — labels

- `humanize(text: string): string`: replace `_` with space, collapse repeated spaces, trim,
  capitalise the first character. `nodeLabel(name)` returns `humanize(name)` (same output).
- `ESTIMATOR_LABELS: Record<string, string>` = logistic_regression "Logistic regression",
  random_forest_classifier "Random forest", hist_gb_classifier "Gradient boosting",
  linear_regression "Linear regression", ridge "Ridge regression", random_forest_regressor
  "Random forest", hist_gb_regressor "Gradient boosting". `estimatorLabel(name: string | null): string`:
  null -> "Model"; known -> label; else `humanize(name)`.
- `METRIC_LABELS: Record<string, string>` = accuracy "Accuracy", balanced_accuracy "Balanced
  accuracy", precision "Precision", recall "Recall", f1 "F1", roc_auc "ROC AUC", pr_auc "PR AUC",
  brier "Brier score", log_loss "Log loss", rmse "RMSE", mae "MAE", r2 "R²", medae "Median
  absolute error", mape "MAPE". `metricLabel(name: string): string`: known -> label; else `humanize(name)`.
- Tests: `humanize('hist_gradient_boosting_default_capacity')` -> "Hist gradient boosting default
  capacity"; `humanize('a__b ')` -> "A b"; `estimatorLabel('hist_gb_classifier')` -> "Gradient
  boosting"; `estimatorLabel(null)` -> "Model"; `estimatorLabel('xgb')` -> "Xgb";
  `metricLabel('roc_auc')` -> "ROC AUC"; `metricLabel('custom_thing')` -> "Custom thing".

### RunHeader.tsx — status band

Props add `championLabel: string | null` (RunPage passes `champion(view)` label humanised:
`view === null ? null : (champion(view) === null ? null : humanize(champion.label))`; import
`champion` from `../report/champion.ts`). Keep the mutation, controls, confirm dialogs and rewind
form logic exactly. New markup:
```
<section className="run-band" aria-label="Run status">
  <div className="run-band-top">
    <div>
      <p className="run-band-kicker">{mode === 'guided' ? 'Guided run' : 'Autonomous run'}</p>
      <p className={`run-band-status tone-${tone}`}>{STATUS_LABELS[run.status]}</p>
      <p className="run-band-sub">{sub}</p>
    </div>
    <div className="run-band-actions">…Pause / Resume / Stop the run buttons as today…</div>
  </div>
  <dl className="run-stats">
    <div><dt>Agent cost</dt><dd>{costUsd === null ? '…' : formatUsd(costUsd)}</dd></div>
    {budget && <div><dt>Fit time</dt><dd>{`${used} s of ${limit} s`}</dd></div>}
    <div><dt>Started</dt><dd>{formatTime(run.started_at)}</dd></div>
  </dl>
</section>
<details className="run-more">
  <summary>Run details and rewind</summary>
  <dl className="facts">Run ID (<code>), Ended, Live updates, Rewound from (link, when parent)</dl>
  …the existing rewind field + button (when controls.rewind && view !== null)…
</details>
{success / error lines as today}
```
`sub`: status `completed` and `championLabel` -> `Champion: ${championLabel}`; else
`run.current_node` -> `Now: ${nodeLabel(run.current_node)}`; else `''` (render the `<p>` only
when non-empty). Fit-time numbers formatted as today. Remove the `<h1>Run</h1>` heading block
and the old `page-head` wrapper (the band is the heading region; give the status `<p>` no
heading role change).

### RunTimeline.tsx — lanes as cards

Keep props, selection, toggling and shared items. Add prop `championId: string | null`.
Replace the candidate-models body (when branches exist) with:
```
<ul className="lanes">
  per branch:
  <li className={`lane${isChampion ? ' is-champion' : ''}`}>
    <div className="lane-head">
      <div className="lane-title">
        <h4>{humanize(branch.label)}</h4>
        <p>{estimatorLabel(branch.estimator)}{retune text}</p>   (retune text: `, retuned ${n} times` when > 0)
      </div>
      {isChampion && <span className="pill signal">Champion</span>}
      <p className="lane-metric">{primary metric text}</p>
    </div>
    <ol className="lane-steps" aria-label={`Steps for ${humanize(branch.label)}`}>
      per per-candidate node: <li><button type="button" className={`step-chip chip-${status}`} aria-pressed={isSel} onClick={toggle}>
        <span className="chip-icon" aria-hidden="true" />{nodeLabel(node.name)}<span className="sr-only">: {CELL_LABELS[status]}</span>
      </button></li>
    </ol>
    {selected per-candidate on this branch && <div className="tl-detail">{detail}</div>}
  </li>
</ul>
```
Primary metric text: entries of `mergedMetrics(branch)`; if empty "Not scored yet"; else the
first entry: `${metricLabel(key)} ${value.toFixed(4)}` (one metric only; full table lives in
Candidates). Remove the "Steps for each model, in order" note and the dot/track markup. The
detail now renders inside the selected branch's card, not after all lanes.
`RunPage.tsx`: pass `championId={view === null ? null : champion(view)?.candidateId ?? null}` and
`championLabel` to RunHeader as above.

### CandidateTable.tsx

Metric column header text: `metricLabel(name)` (sorting keys unchanged). Estimator cell:
`estimatorLabel(row.estimator)` if that column shows the raw estimator name.

### style.css

1. Replace the `:root` token values per the design plan; add the new tokens.
2. Top bar: `.topbar { background: var(--navy); border-bottom: 0; color: var(--navy-ink); }`;
   `.brand { color: var(--navy-ink); }`; `.topnav a, .topnav button { background: transparent; color: var(--navy-ink); border-color: rgba(255,255,255,0.28); }`;
   `.topnav a:hover, .topnav button:hover { background: var(--navy-2); }`;
   `.topnav a[aria-current="page"] { border-color: var(--sky); color: var(--navy-ink); background: var(--navy-2); }`.
3. Buttons: `button.primary` keeps `--signal`; add `button:hover:not(:disabled) { border-color: var(--signal); }`.
4. Chat bubbles: `.messages .msg.agent .msg-body { background: var(--surface); border: 1px solid var(--rule); border-radius: 4px var(--radius-lg) var(--radius-lg) var(--radius-lg); padding: 10px 14px; box-shadow: var(--shadow); }`;
   `.messages .msg.user { background: var(--signal); color: var(--signal-ink); }` (keep radius/padding);
   `.messages .msg.user a { color: var(--signal-ink); }`; typing indicator row unchanged.
   `.chat-scroll { background: var(--paper); }`; `.side-pane { background: var(--surface); }`.
5. Tabs as a segmented control: `.tabs { display: flex; flex-wrap: wrap; gap: 4px; padding: 4px; border: 0; border-radius: var(--radius); background: #E3EAF3; width: fit-content; max-width: 100%; }`,
   `.tabs button { border: 0; border-radius: 8px; background: transparent; padding: 6px 14px; color: var(--graphite); }`,
   `.tabs button[aria-selected="true"] { background: var(--surface); color: var(--ink); box-shadow: var(--shadow); }`.
   Same look for `.pane-switch` buttons inside the 64rem media query (selected = white on #E3EAF3).
6. Run band: `.side-pane .run-band { margin: -24px -28px 16px; padding: 20px 28px; background: var(--navy); color: var(--navy-ink); }`,
   `.run-band-top { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px 24px; align-items: flex-start; }`,
   `.run-band-kicker { margin: 0; color: var(--navy-muted); font-size: 0.875rem; }`,
   `.run-band-status { margin: 2px 0; font-size: 1.75rem; font-weight: 700; line-height: 1.15; }`,
   `.run-band-status.tone-signal { color: #8CC8F0; }`, `.tone-gate { color: #FFC873; }`, `.tone-fault { color: #FF9C94; }` (scoped `.run-band-status.tone-…`),
   `.run-band-sub { margin: 0; color: var(--navy-muted); }`,
   `.run-band-actions { display: flex; flex-wrap: wrap; gap: 8px; }`,
   `.run-band button { background: transparent; color: var(--navy-ink); border-color: rgba(255,255,255,0.35); }`,
   `.run-band button.primary { background: var(--sky); border-color: var(--sky); color: var(--navy); }`,
   `.run-band button.danger { color: #FFB4AD; border-color: #FFB4AD; }`,
   `.run-stats { display: flex; flex-wrap: wrap; gap: 8px 28px; margin: 16px 0 0; }`,
   `.run-stats dt { color: var(--navy-muted); font-size: 0.8125rem; }`,
   `.run-stats dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }`,
   `.run-more { margin: 0 0 16px; }`, `.run-more summary { color: var(--graphite); }`.
   At `max-width: 40rem`: `.side-pane .run-band { margin: -16px -16px 16px; padding: 16px; }` and `.side-pane { padding: 16px; }`.
7. Timeline status text: `.tl-head .mark { padding: 0 8px; border-radius: 999px; font-size: 0.8125rem; background: var(--paper); }`,
   `.tl-head .mark-completed { background: var(--target); }`, `.tl-head .mark-awaiting { background: var(--gate-soft); }`,
   `.tl-head .mark-failed { background: var(--fault-soft); }`.
8. Lanes: delete the `.lane-dot*`, `.dot-*`, `.lane-steps` (old), `.lane-name`, `.lane-metrics`
   rules and the `.lane` grid + its 40rem media rule; add:
   `.lane { padding: 12px 14px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); box-shadow: var(--shadow); min-width: 0; }`,
   `.lane.is-champion { border-color: var(--signal); box-shadow: 0 0 0 1px var(--signal), var(--shadow); }`,
   `.lane-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px; }`,
   `.lane-title { flex: 1 1 14rem; min-width: 0; }`,
   `.lane-title h4 { margin: 0; font-size: 1rem; overflow-wrap: anywhere; }`,
   `.lane-title p { margin: 0; color: var(--graphite); font-size: 0.875rem; }`,
   `.lane-metric { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }`,
   `.lane-steps { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0 0; padding: 0; }`,
   `.step-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; font-size: 0.8125rem; background: var(--paper); border: 1px solid var(--rule); color: var(--ink); }`,
   `.step-chip[aria-pressed="true"] { border-color: var(--ink); }`,
   `.chip-icon { width: 10px; height: 10px; border-radius: 50%; border: 2px solid var(--graphite); flex: none; }`,
   `.chip-completed .chip-icon { background: var(--signal); border-color: var(--signal); }`,
   `.chip-running .chip-icon { border-color: var(--signal); box-shadow: 0 0 0 3px var(--target); }`,
   `.chip-awaiting { background: var(--gate-soft); border-color: var(--gate); }`, `.chip-awaiting .chip-icon { border-color: var(--gate); background: var(--gate); }`,
   `.chip-failed { background: var(--fault-soft); border-color: var(--fault); }`, `.chip-failed .chip-icon { border-color: var(--fault); background: var(--fault); }`,
   `.chip-skipped .chip-icon { border-style: dashed; }`,
   `@media (prefers-reduced-motion: no-preference) { .chip-running .chip-icon { animation: halo 2.4s ease-in-out infinite; } }`.
9. `.pill.signal` and links pick up the new `--signal` automatically; `.ready`, `.tl-*` stay.
10. `dialog.drawer::backdrop { background: rgba(19, 33, 58, 0.4); }`.

### Checks

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build` (capture each `$?`). Main
then does browser review on the H9 offline stack and on the user's real run (read-only viewing).

### 42a Addendum A — corrections after the first implementation pass

Diagnosis (main): `test_budget_evaluates_every_point_when_clock_frozen` hangs alone (60 s
timeout); the other 44 training tests pass. The budget tests patch
`statlab_tools.tools.training.time.monotonic`, which is the global `time.monotonic`; joblib's
threaded result waits read it, so a frozen clock never lets them finish. mypy rejects the
untyped `scipy.stats` import.

1. `training.py`: add a module-level function `_monotonic() -> float` returning
   `time.monotonic()`, docstring: the tuning budget clock; tests patch this function, never the
   global `time.monotonic`, which joblib's threaded waits also read. `tune_estimator` uses
   `_monotonic()` for `started` and for the budget check. Its docstring "via this module's
   `time.monotonic`" becomes "via this module's `_monotonic`".
2. `tests/test_tools_training.py`: both budget tests patch
   `"statlab_tools.tools.training._monotonic"` (same replacement callables as today).
3. `evaluation.py` `_binary_auc`: drop the scipy import; average ranks with numpy:
   `order = np.argsort(scores, kind="mergesort")`, `s = scores[order]`,
   `starts = np.r_[0, np.flatnonzero(np.diff(s)) + 1]`, `ends = np.r_[starts[1:], s.size]`,
   `ranks = np.empty(s.size, dtype=np.float64)`; `ranks[order] = np.repeat((starts + ends + 1) / 2.0, ends - starts)`
   (1-based average rank of each tie group). The rest of `_binary_auc` is unchanged.
4. Rerun the full "### Checks" list (all four test files, ruff, mypy); no probe rerun needed
   unless `_binary_auc` changes the probe's runtime class (report the probe once more anyway).

### 42b handoff (2026-10-08)

Implemented as specified. Evidence: `npm test` 72/72, typecheck, build exit 0 (implementer).
Main browser review (user's `make dev`, read-only viewing of real run `r_261239930187a0a7` and
home): navy top bar and run band, humanised lane cards with wrapping step chips, champion
outline, segmented tabs; no text/diagram collision. Accepted deviations: sortable header key
`header.metric ?? header.text`; pane-switch background. Open defect carried into 42c: at
390 px the run band is 414 px wide starting at x = -12 (the 40rem override sits before the
base `.side-pane .run-band` rule). Screenshots `.playwright-mcp/42b-*.png`.

## 42c — the agent talks in the run chat

Goal: the run chat explains everything as it happens and at the end. (1) An intro message tells
the user what they can say. (2) After each shared step (and each candidate's evaluation, and
any failure) the agent posts a short plain-language update with a "Show the details" link.
(3) A pending decision appears in the chat as a question with quick-reply buttons; free-text
replies already work through the control router (`gates.py:636`). (4) When the run completes,
a summary card: champion and why, test score with interval, what was done, validation plots,
confusion matrix, top features, buttons to the report and candidates.

Text source: decisions get an optional `user_summary` (1–3 plain sentences the LLM writes as
part of the decision it already makes — no extra LLM call). Fallback: first sentences of
`rationale`. Node events already carry the full result (`engine.py:634`).

Non-goals: no new endpoint, no extra LLM call, no change to the control router or gate
semantics, no offline-responder change (offline runs fall back to the rationale text).

### Owned files

Backend: `backend/statlab/domains/decisions.py`, `backend/statlab/graph/gates.py` (one line),
new `tests/test_domain_decisions.py`, `tests/test_domain_binding.py` (two assertions).
Frontend: `src/run/model.ts`, `tests/run-model.test.ts`, `src/chat/session.ts` (type),
`src/chat/MessageList.tsx`, `src/chat/RunChat.tsx`, new `src/chat/summary.ts`, new
`tests/summary.test.ts`, new `src/chat/RunSummary.tsx`, `src/pages/RunPage.tsx`,
`src/style.css`, `package.json` (test list).

### Backend

1. `decisions.py` `Decision`: add
   `user_summary: str | None = Field(default=None, min_length=1, max_length=600, description=...)`
   with description exactly: "Always fill this: one to three short plain-language sentences for
   the user's chat saying what you did at this step and what you found. Name the columns and
   numbers that matter. No internal ids, no jargon, no markdown."
   `ChampionChoice` inherits it unchanged.
2. `gates.py:210`: `editable=() if invocation.after_action else tuple(name for name in type(decision).model_fields if name != "user_summary")`.
3. `tests/test_domain_decisions.py` (new): `Decision(rationale="r").user_summary is None`;
   `IntakeDecision(rationale="r", user_summary="Loaded 10 rows.").user_summary == "Loaded 10 rows."`;
   `Decision.model_validate({"rationale": "r", "user_summary": ""})` raises `ValidationError`;
   same with `"x" * 601`; `IntakeDecision.model_json_schema()["properties"]["user_summary"]["description"]`
   starts with "Always fill this".
4. `tests/test_domain_binding.py`: right after line 338 (`question = GateQuestion.model_validate(gate.question())`)
   add `assert "user_summary" not in question.editable` and `assert "rationale" in question.editable`.

### model.ts

- `firstSentences(text: string, n: number): string`: trim; split on `/(?<=[.!?])\s+/`; first n
  parts joined with a space.
- `narrate(summary: JsonObject, status: string, skipReason: string | null, errorMessage: string | null): string`:
  `failed` -> errorMessage ? `It failed: ${errorMessage}` : 'It failed.'; `skipped` ->
  skipReason ? `Skipped: ${skipReason}` : 'Skipped.'; otherwise: `decision = summary.decision`;
  object with non-blank string `user_summary` -> it trimmed; else non-blank string `rationale`
  -> `firstSentences(rationale, 2)`; else 'Done.'.
- `narrateOutcome(outcome: NodeOutcome): string` = `narrate(outcome.summary, outcome.status, outcome.skipReason, outcome.error?.message ?? null)`.
- `FeedItem` adds `{ kind: 'step'; seq: number; ts: string; node: string; candidateId: string | null; status: 'completed' | 'failed' | 'skipped'; text: string }`.
- `foldEvents`, `node.completed|skipped|failed` (not `node.started`): keep the activity item;
  then, with `candidate = typeof p.candidate_id === 'string' ? publicCandidateId(p.candidate_id) : null`
  and `result = isJsonObject(p.result) ? p.result : {}`, push a step item when `candidate === null`,
  or `event.type === 'node.failed'`, or (`event.type === 'node.completed'` and `p.node === 'model_evaluation'`).
  Text: `narrate(isJsonObject(result.summary) ? result.summary : {}, status, string result.skip_reason or null, string result.error.message or null)`
  where status is `event.type.slice(5)`.
- Tests (`run-model.test.ts`): `firstSentences('A b. C d! E f? G', 2) === 'A b. C d!'`; narrate
  with user_summary, rationale-only (three sentences -> first two), neither -> 'Done.', failed with
  and without message, skipped with reason; foldEvents: a shared `node.completed` with
  `result.summary.decision.user_summary` -> one activity + one step item (candidateId null);
  a per-candidate `preprocessing` completion -> no step item; a per-candidate
  `model_evaluation` completion with `candidate_id: 'r_x:c_02'` -> step item with candidateId
  `c_02`; a per-candidate `node.failed` -> step item 'It failed: boom'; `node.started` -> no step.

### session.ts / MessageList.tsx

- `ChatMessage` adds optional `title?: string` and `step?: { node: string; candidateId: string | null }`.
- MessageList: new optional prop `onStep?: (step: { node: string; candidateId: string | null }) => void`.
  Agent message body: when `title` -> `<p className="msg-title">{title}</p>` first; when `step` and
  `onStep` -> after the paragraphs `<button type="button" className="link-button" onClick={() => onStep(step)}>Show the details</button>`.

### src/chat/summary.ts (pure, typed accessors over RunView)

- `export type Plot = { ref: string; caption: string }`.
- `championPlots(view: RunView, candidateId: string): Plot[]`: `curves = latestOutcome(view, 'model_evaluation', candidateId)?.summary.curves`;
  for keys in order `roc`, `pr`, `calibration` with object entries whose `plot` is a string:
  captions "ROC curve (validation)", "Precision-recall curve (validation)", "Calibration
  (validation)"; for roc/pr append `, AUC ${auc.toFixed(3)}` when `entry.summary.auc` is a finite number.
- `confusionMatrix(view, candidateId): { labels: string[]; matrix: number[][] } | null` from
  `curves.confusion.summary` when `labels` is a string array and `matrix` is an array of
  number arrays each of length `labels.length` with `labels.length` rows; else null.
- `topFeatures(view, candidateId, n: number): { metric: string; items: { feature: string; value: number }[] } | null`
  from `latestOutcome(view, 'model_interpretation', candidateId)?.summary.diagnostics.permutation_importance`
  (`metric` string, `top` array of `{feature: string, importance_mean: finite number}`), first n
  valid items; null when absent or empty.
- `whyChampion(view): string | null`: last shared outcome with node `champion_selection`;
  `narrateOutcome` of it unless the result would be 'Done.' (return null then).
- `stepLines(view): { node: string; text: string }[]`: shared outcomes in order, last outcome per
  node wins (keep the position of the first occurrence), excluding `champion_selection` and
  `artifact_persistence`, mapped to `{ node, text: narrateOutcome(outcome) }`.
- Tests (`tests/summary.test.ts`, add to package.json test list): build a `RunView` literal with
  shared outcomes (intake with user_summary, experiment_design twice — second wins, inferential
  skipped, champion_selection with rationale "Picked c_03. It ranked first. Ties broken. Extra.")
  and a branch `c_03` with model_evaluation `curves` (roc with plot+auc 0.75132, pr with plot,
  calibration with plot, confusion summary 2×2) and model_interpretation diagnostics (3 features).
  Assert plots order/captions ("ROC curve (validation), AUC 0.751"), confusion labels/matrix,
  topFeatures(…, 2) has 2 items and metric, whyChampion = 'Picked c_03. It ranked first.',
  stepLines order and texts; a branch without curves -> `[]` and `null`s.

### src/chat/RunSummary.tsx

Props `{ runId: string; view: RunView; onTab(tab: 'candidates' | 'report'): void; onStep(step: { node: string; candidateId: string | null }): void }`.
`c = champion(view)`; null -> render nothing. `fmt = (x: number) => x.toFixed(3)`.
```
<section className="run-summary" aria-label="Run summary">
  <h3>The run is finished</h3>
  <p>The best model is <strong>{humanize(c.label)}</strong> ({estimatorLabel(c.estimator)}), chosen from {view.branches.length} candidate models.</p>
  {test line when c.primaryMetric and c.testMetrics[primary]: "On the held-out test set it scored <strong>{metricLabel(p)} {fmt(v)}</strong>" + (both CI ends non-null ? ` (95% interval ${fmt(lo)} to ${fmt(hi)})` : '') + ". This is the only unbiased estimate of how it will do on new data."}
  {why && <><h4>Why this model</h4><p>{why}</p></>}
  <h4>What I did</h4>
  <ol className="summary-steps">{stepLines: <li><button className="link-button" onClick={() => onStep({node, candidateId: null})}>{nodeLabel(node)}</button>: {text}</li>}
    <li><button className="link-button" onClick={() => onTab('candidates')}>Candidate models</button>: trained and compared {view.branches.length} models on the same validation rows.</li></ol>
  {plots.length > 0 && <><h4>How the best model performs on validation data</h4><div className="summary-plots">{figure per plot: <a href={artifactUrl(ref)} target="_blank" rel="noreferrer"><img src={artifactUrl(ref)} alt={caption} loading="lazy" /></a><figcaption>{caption}</figcaption>}</div></>}
  {confusion && <table className="confusion"><caption>Confusion matrix (validation): rows are the true class, columns the predicted class</caption><thead><tr><th scope="col">True \ predicted</th>{labels th}</tr></thead><tbody>{rows: <tr><th scope="row">{label}</th>{counts td with toLocaleString()}</tr>}</tbody></table>}
  {features && <><h4>What drives the predictions</h4><ul className="bars">{items: <li><span className="bar-label">{feature}</span><span className="bar" style={{ width: `${pct}%` }} aria-hidden="true" /><span className="sr-only">{value.toFixed(4)}</span></li>}</ul><p className="help">How much {metricLabel(metric)} drops when the feature is shuffled (permutation importance on validation data).</p></>}
  <div className="actions"><button type="button" className="primary" onClick={() => onTab('report')}>Open the full report</button><button type="button" onClick={() => onTab('candidates')}>Compare all candidates</button></div>
</section>
```
`pct = max > 0 ? Math.max(0, value) / max * 100 : 0` with `max` = largest value among items.
Features: `topFeatures(view, c.candidateId, 5)`.

### src/chat/RunChat.tsx

Props become `{ runId: string; feed: RunFeed; view: RunView | null; status: RunStatus; mode: RunMode; pending: { gate: GateSummary; question: GateQuestion | null }[]; navigate(route): void; onStep(step): void; onShowGate(): void; onTab(tab: 'candidates' | 'report'): void }`.
Messages in order:
1. setup transcript (as now), then the `run-started` note (as now, only with a transcript);
2. intro agent message `{id: 'intro', role: 'assistant', text: intro}`; `intro` =
   "I'll post a short update here after each step, and a summary of the results at the end.\n\nYou can write to me at any time:\n- ask why I did something (\"why did you drop LoanID?\")\n- change a setting (\"use 10 folds\", \"set the test size to 0.3\")\n- redo part of the run (\"go back to data profiling\")\n- control the run (\"pause\", \"resume\", \"stop\")"
   plus, when `mode === 'guided'`, "\n\nIn guided mode I stop and ask you before each key decision. Answer with the buttons or in your own words.";
3. feed items: `step` -> `{id: `e${seq}`, role: 'assistant', title, text, step: {node, candidateId}}`
   with `title = candidateId === null ? nodeLabel(node) : `${nodeLabel(node)}: ${humanize(view branch label for candidateId ?? candidateId)}``
   (always shown); `activity` only when `showActivity` (as now); user/assistant/error/fork as now;
4. one agent message per pending gate with a parsed question:
   `{id: `gate-${gate.id}-${question.revision}`, role: 'assistant', title: `Your decision: ${nodeLabel(question.node)}` + (candidate label as in 3 when `question.candidateId`), text: gatePrompt(question)}`;
   `gatePrompt` (local function): lead by phase — before: "I'm about to run this step. Here is my plan."; after:
   "This step has finished. Please check the result."; failure: `This step failed: ${question.failure?.message ?? 'unknown error'}`;
   then `firstSentences(question.reasoning, 3)` when non-blank; then, when `question.editable`
   ∩ keys of `question.proposal` is non-empty, "Main settings:" and up to 6 lines
   `- ${humanize(key)}: ${valueText(proposal[key])}` (valueText: null -> "not set"; boolean ->
   "yes"/"no"; string as is; number `String`; array -> items' valueText joined ", " or "none" when
   empty; object -> `JSON.stringify`); then "Reply with the buttons below, or tell me in your own
   words what to change." Paragraphs separated by blank lines.
5. `extra` fault notes (as now).
Widget slot children (in order): the existing waiting hint is replaced by quick replies for the
LAST pending gate with a parsed question: `<div className="quick-replies">` with buttons
"Approve" (when `options` includes `approve`), "Approve and run the rest automatically" (when
`options` includes `approve_all_remaining`), "Review the details" (always; calls `onShowGate`).
The two approve buttons call a mutation `answerGate(runId, gate.id, { revision: question.revision, action })`;
success -> invalidate `['run', runId]` and `['executions', runId]`; error -> fault note
`errorText(error)`; buttons disabled while pending. Then, when `status === 'completed'` and
`view !== null`: `<RunSummary runId view onTab onStep />`.
Pass `onStep` to MessageList.

### src/pages/RunPage.tsx

Pass the new RunChat props: `view`, `status={data.run.status}`, `mode={data.run.mode}`, `pending`
(the existing computed list), `onStep={(step) => { setSelected(step); setTab('progress'); setPane('side'); }}`,
`onShowGate={() => setPane('side')}`, `onTab={(tab) => { setTab(tab); setPane('side'); }}`. Remove
the old `waiting` prop. The run-chat must render before `view` checks only as today (RunChat
handles `view === null`).

### style.css

- Move the `@media (max-width: 40rem)` block that sets `.side-pane .run-band` / `.side-pane`
  padding to the very end of the file (fixes the 42b overflow).
- Add: `.msg-title { margin: 0 0 4px; font-weight: 700; }`,
  `.link-button { padding: 0; border: 0; background: none; color: var(--signal); text-decoration: underline; cursor: pointer; font: inherit; box-shadow: none; }`,
  `.msg-body .link-button { margin-top: 6px; font-size: 0.875rem; }`,
  `.quick-replies { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }`,
  `.run-summary { margin-top: 8px; padding: 16px 18px; background: var(--surface); border: 1px solid var(--rule); border-radius: var(--radius-lg); box-shadow: var(--shadow); }`,
  `.run-summary h3 { margin: 0 0 8px; font-size: 1.125rem; }`, `.run-summary h4 { margin: 16px 0 6px; font-size: 1rem; }`,
  `.summary-steps { margin: 0; padding-left: 1.25rem; } .summary-steps li { margin: 4px 0; }`,
  `.summary-plots { display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); gap: 12px; }`,
  `.summary-plots figure { margin: 0; } .summary-plots img { display: block; width: 100%; height: auto; border: 1px solid var(--rule); border-radius: var(--radius); background: #fff; }`,
  `.summary-plots figcaption { margin-top: 4px; color: var(--graphite); font-size: 0.8125rem; }`,
  `table.confusion { width: auto; margin-top: 12px; } table.confusion th, table.confusion td { padding: 4px 12px; text-align: right; }`,
  `table.confusion caption { padding: 0 0 6px; font-weight: 400; color: var(--graphite); font-size: 0.8125rem; }`,
  `.bars { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }`,
  `.bars li { display: grid; grid-template-columns: minmax(6rem, 12rem) 1fr; gap: 10px; align-items: center; }`,
  `.bar-label { font-size: 0.875rem; overflow-wrap: anywhere; }`,
  `.bar { display: block; height: 10px; min-width: 2px; border-radius: 999px; background: var(--signal); }`.

### Checks

Backend: `make test-fast TESTS="tests/test_domain_decisions.py tests/test_domain_binding.py tests/test_control_gates.py"`;
ruff check/format --check and mypy on `decisions.py`, `gates.py` and the two test files.
Frontend (from `frontend/`): `npm test`, `npm run typecheck`, `npm run build`. Capture every
exit code. Stop and report on any gap.

### 42c handoff (2026-10-08)

Implemented as specified. Evidence (implementer): `make test-fast TESTS="tests/test_domain_decisions.py
tests/test_domain_binding.py tests/test_control_gates.py"` exit 0; ruff/format/mypy on the 4
backend files exit 0; `npm test` 76/76, typecheck, build exit 0. Main browser review on the real
run: intro message, one titled narration per shared step with "Show the details", summary card
(champion, test ROC AUC 0.750 [0.744, 0.757], why, what I did, ROC/PR/calibration plots,
confusion matrix, top-5 permutation bars, report/candidates buttons). That run predates
`user_summary`, so texts fall back to rationale sentences (technical); new runs carry the
plain summary. Accepted deviations: `as Step` cast in MessageList, unused `runId` prop.

## 42d — clear decision forms

Goal: a gate shows only the main settings by default; everything else is in a collapsed "All
settings" section; every field has a sentence-case label and its schema description as help;
secondary actions are grouped. The chat's gate prompt lists the same main settings.

Non-goals: no backend change; no change to gate submission semantics (`gateEdits`, check,
409 handling stay exactly).

### Owned files

`frontend/src/contract/schema.ts`, `frontend/tests/schema.test.ts`, `frontend/src/setup/SchemaForm.tsx`,
`frontend/src/run/model.ts`, `frontend/tests/run-model.test.ts`, `frontend/src/run/GatePanel.tsx`,
`frontend/src/chat/RunChat.tsx` (gatePrompt keys only), `frontend/src/style.css`.

### schema.ts

`FieldSpec` gains `description: string | null` = `property.description` when it is a non-blank
string, else null (in `fieldOf`, from the outer property). Update `tests/schema.test.ts`
expectations with `description: null` everywhere and give `notes` in the fixture
`description: 'Free text.'` -> expected `description: 'Free text.'`.

### model.ts

`gateFieldSplit(question: GateQuestion): { main: string[]; rest: string[] }`:
- `fields = fieldsOf(question.decisionSchema, null)`; `editable = new Set(question.editable)`;
  `keys` = field keys in schema order that are editable and not `user_summary`.
- `required` = `question.decisionSchema.required` as a string array (else empty).
- `isSet(key)`: value `question.proposal[key]` is not undefined/null, not an empty array, not an
  empty object, not a blank string, and `JSON.stringify(value) !== JSON.stringify(field.defaultValue)`.
- `main` = keys, excluding `rationale`, where `required.has(key) || isSet(key)`, first 6.
- `rest` = remaining keys (incl. `rationale`) in schema order.
Tests: schema with properties rationale (required), task (enum, default null), metric (default
"roc_auc"), folds (default 5), notes (array default []), user_summary; required
`["rationale", "task"]`; editable all six; proposal `{rationale: 'r', task: 'classification', metric: 'roc_auc', folds: 10, notes: [], user_summary: 's'}`
-> main `['task', 'folds']`, rest `['rationale', 'metric', 'notes']`. A second case with
`editable: []` -> both empty.

### SchemaForm.tsx

- `FormProps` gains optional `only?: string[]`. In `SchemaForm`, when `only` is given, `fields`
  = `only` mapped to the matching field (skip keys without a field), in `only` order; otherwise
  today's ordering.
- `Field`: label text = `humanize(field.key)` (import from `../run/model.ts`) instead of
  `field.title`; help = parts joined with " ": `field.description` (when set) and, when
  `hasDefault`, `Default: ${defaultText}` where `defaultText` = the value as text (strings as-is,
  arrays joined ", " or "none" when empty, others `JSON.stringify`). When both are absent render
  no help `<p>` (and no `aria-describedby` to it if the control references `helpId` — keep the
  attribute only when the help exists). "Not set by default" is no longer shown.
  Update existing frontend tests only if one asserts the old help text (none known).

### GatePanel.tsx

Keep state, mutation, actions logic. New layout:
```
<article className="panel gate">
  <h3>Your decision: {nodeLabel(node)}{candidate}<span className="pill gate">{PHASE_LABELS[phase]}</span></h3>
  <p className="gate-lead">{lead}</p>   (same three phase texts as RunChat's gatePrompt lead)
  {failure alert as today}
  {reasoning: <p>{firstSentences(question.reasoning, 3)}</p> and, when the full text is longer,
   <details><summary>Read the full reasoning</summary><p className="reasoning">{question.reasoning}</p></details>}
  editable ?
    <fieldset className="gate-main"><legend>Main settings</legend>
       main.length > 0 ? <SchemaForm … only={main} /> : <p className="help">The agent kept every setting at its default.</p>
    </fieldset>
    {rest.length > 0 && <details className="all-settings"><summary>{`All settings (${rest.length} more)`}</summary><SchemaForm … only={rest} /></details>}
  : <DecisionView value={question.proposal} />   (non-editable, as today, inside a fieldset legend "Proposal")
  {issues alert as today}
  <div className="actions">…approve / approve with edits / discard / approve all — unchanged…</div>
  {help line as today}
  <details className="gate-more"><summary>Other options</summary>
     context list, evidence, earlier proposals (as today's three details, now nested here),
     ask for a revision, skip this step, rewind, stop the run (today's blocks, unchanged inside)
  </details>
  {error + reply lines as today}
</article>
```
`{ main, rest } = gateFieldSplit(question)`; both SchemaForms share `draft`/`setDraft`.
Candidate suffix: ` for ${question.candidateId}` as today.

### RunChat.tsx

`gatePrompt`: replace `keys` with `gateFieldSplit(question).main` (still at most 6) and keep the
rest of the function.

### style.css

`.gate-lead { font-weight: 600; margin: 4px 0 8px; }`,
`.gate-main { border: 1px solid var(--rule); border-radius: var(--radius); padding: 8px 16px 12px; }`,
`.all-settings, .gate-more { margin-top: 12px; border-top: 1px solid var(--rule); padding-top: 8px; }`,
`.all-settings > summary, .gate-more > summary { font-weight: 600; color: var(--signal); }`.

### Checks

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build` (capture each `$?`).
Main then reviews a guided gate on the offline stack.

### 42d handoff (2026-10-08)

Implemented as specified (`npm test` 77/77, typecheck, build exit 0, implementer). Browser
review of a guided gate deferred: the user interrupted the click-through to add chunk 43 asks;
it runs at combined acceptance. Known cosmetic: on a failure-phase gate the failure message
shows in the lead and in the alert.

### 42 Addendum E — first `make check` corrections

`make check` (all 42 stages) exit 2: 3,173 passed; failures
`tests/test_nodes_persistence.py::test_guided_preservation_awaits_approval_without_writes` and
`::test_guided_preservation_approval_edits_only_rationale` (decision dump now carries
`"user_summary": None`), and coverage 99.97 % (`evaluation.py:289-292`, the threshold branch of
`hard_labels`, no longer reached by `score_classification`).
1. `tests/test_nodes_persistence.py` lines ~114 and ~153: add `"user_summary": None` to both
   expected decision dicts.
2. `evaluation.py` `hard_labels`: body becomes
   `return np.asarray(classes)[_hard_codes(proba, classes.index(positive), threshold)]`
   (same labels and `<U` dtype as before; `_hard_codes` must be defined above it — move
   `hard_labels` below `_hard_codes` if needed). Docstring unchanged.
Checks: `make test-fast TESTS="tests/test_nodes_persistence.py tests/test_tools_evaluation.py tests/test_tools_interpretation.py"`,
ruff check/format --check and mypy on `evaluation.py` and the test file.
3. (Addendum E correction) mypy `no-any-return` on the indexed result: the body becomes
   `return np.asarray(np.asarray(classes)[_hard_codes(proba, classes.index(positive), threshold)])`.

## Acceptance receipt (2026-10-08)

Candidate: `7908334` plus the chunk 42 working-tree diff (files staged in the chunk 42 commit).
- `make check` (second run, after Addendum E): exit 0; 3,104 passed, 1 designed skip; coverage
  100 % (11,944 statements, 3,432 branches); mypy "Success: no issues found in 194 source files";
  Ruff "All checks passed!". Log: session scratchpad `check42b.log` (temporary).
- First `make check`: exit 2 (two persistence dict assertions, coverage 99.97 %); fixed by
  Addendum E.
- Frontend from `frontend/`: `npm test` exit 0 (77/77), `npm run typecheck` exit 0,
  `npm run build` exit 0.
- 42a probe: bootstrap scoring on 51,070 validation rows with 1,000 resamples took 8.55 s
  (about 300 s before).
- Deferred: browser review of a guided gate (form split, chat quick replies, summary card).
  The user interrupted it to add chunk 43 asks; it runs at chunk 43 acceptance.
