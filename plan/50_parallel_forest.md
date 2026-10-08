---
status: completed
dependencies: [49]
completion_date: 2026-10-08
implementation_notes: "fit_threads a third of cores; uncalibrated forest final fit on threads, reset to n_jobs=1; make check 100%"
---

# Chunk 50 — faster random forests (D17 addendum 50)

User request 2026-10-08: "start working on paralelling the random forest, this is really slow".
Evidence, run `r_ca4d7d9637afd45e` (100,000 x 44, ~60,000 train rows, 16-core M4 Max):
`random_forest_regressor` `tune_estimator` with 16 grid points took 328 s; the final
`train_estimator` fit took ~100 s at 98 % CPU (one core).

Today (D17, D17-42): estimators are built with `n_jobs=1`; tuning runs CV folds on
`min(cv_folds, parallel_jobs(name))` threads where `parallel_jobs` = `fit_threads()` =
`cpu_count // 4` (4 on 16 cores), so the default 5 folds take two rounds; the final fit is
single-threaded.

Decision:
1. `fit_threads()` becomes a third of the cores (`cpu_count // 3`, min 1): on 16 cores, 5
   threads, so the default 5 CV folds (and 5 permutation repeats) finish in one round.
2. The uncalibrated random-forest final fit in `train_estimator` builds trees on
   `fit_threads()` threads (joblib threading backend), then resets the estimator to
   `n_jobs=1` before it is saved or used. Reason for the reset: a forest predicting with
   `n_jobs > 1` sums per-tree outputs in thread-completion order, so predictions could differ
   in the last bits between runs. A fitted forest is identical for any `n_jobs` (per-tree seeds
   are drawn before fitting), so fit-parallel / predict-single keeps every score reproducible.
3. Tuning stays fold-parallel with single-threaded forests (no nested pools, deterministic
   scores). Calibrated fits are unchanged.

Expected on the evidence run: tuning ~2x faster (one fold round), final fit ~5x faster.
Non-goals: no loky, no change to the semaphore, HGB, other estimators, or `params` rules
(`n_jobs` stays a reserved key).

## Stage 50a

Owned: `mcp_server/statlab_tools/registry.py`, `mcp_server/statlab_tools/tools/training.py`,
`tests/test_registry.py`, `tests/test_tools_training.py`. Preserve other edits.

1. `registry.py` `fit_threads()`: return `max(1, (os.cpu_count() or 1) // 3)`. Docstring:
   "Threads one heavy tool may use for its parallel loop (CV folds, permutation repeats, a
   forest's final fit): a third of the cores, so on 16 cores the default 5 folds run in one
   round; threads only, loky processes deadlock (D17, addendum 50)." In the module docstring,
   replace "the forests take `n_jobs=1` (D17: nothing oversubscribes the fit semaphore; tool
   loops use `parallel_jobs` threads under joblib's threading backend)" with "the forests are
   built with `n_jobs=1` (D17); tool loops use `parallel_jobs` threads under joblib's threading
   backend, and `train_estimator` fits an uncalibrated forest on `fit_threads()` threads
   (addendum 50)". Builders unchanged.
2. `training.py`: import `fit_threads` in the existing `from statlab_tools.registry import (...)`
   list (alphabetical). In `train_estimator`, replace `fitted = _fit(estimator, x, y, entry.task)`
   with:
   - `threaded = entry.family == "random_forest" and not calibrated`
   - if `threaded`: `from joblib import parallel_config` (local import, like `tune_estimator`),
     `_set_params(estimator, {"n_jobs": fit_threads()})`, then
     `with parallel_config(backend="threading"): fitted = _fit(estimator, x, y, entry.task)`,
     then `_set_params(fitted, {"n_jobs": 1})`. Comment above the block:
     `# D17 addendum 50: forest trees fit on threads; predictions stay single-threaded because a`
     `# threaded forest sums tree outputs in completion order (last-bit nondeterminism).`
   - else: `fitted = _fit(estimator, x, y, entry.task)` as now.
   Check `_set_params`' signature/return first: if it returns the estimator rather than
   mutating in place, assign its result (`estimator = _set_params(...)`,
   `fitted = _set_params(fitted, ...)`). Report which.
   Module docstring line 4: "D17 (`n_jobs=1` for fits, the fit semaphore via `heavy=True`)"
   becomes "D17 (`n_jobs=1` estimators, the fit semaphore via `heavy=True`; addendum 50 threads
   an uncalibrated forest's final fit)".
3. `tests/test_registry.py`: `test_fit_threads_is_a_quarter_of_the_cores` renamed
   `test_fit_threads_is_a_third_of_the_cores`, parametrize
   `[(16, 5), (2, 1), (None, 1)]`. Other tests unchanged (`test_every_forest_is_single_threaded`
   still holds: builders keep `n_jobs=1`).
4. `tests/test_tools_training.py`, new test after `test_tuning_cv_runs_folds_on_threads`:
   `test_forest_final_fit_runs_trees_on_threads_then_predicts_single_threaded(run_paths, monkeypatch)`:
   - `import joblib.parallel`; `import statlab_tools.tools.training as training` (as the
     neighbouring test does).
   - Spy: `real = training._fit`; `seen: list[tuple[object, str]] = []`; `def spy(estimator:
     object, x: np.ndarray, y: pd.Series, task: Task) -> object:` appends
     `(cast(BaseEstimator, estimator).get_params()["n_jobs"], type(joblib.parallel.get_active_backend()[0]).__name__)`
     and returns `real(estimator, x, y, task)`. Use the file's existing imports for
     `np`/`pd`/`cast`/`Task`; import `BaseEstimator` from `sklearn.base` inside the test if the
     file has no import of it. `monkeypatch.setattr(training, "_fit", spy)`.
   - `monkeypatch.setattr(training, "fit_threads", lambda: 3)`; `ref = _save(run_paths, _separable_binary())`;
     `threaded = train_estimator(run_paths, ref, target="y", name="random_forest_classifier", seed=1, params=_FAST_RF)`.
     Assert `seen == [(3, "ThreadingBackend")]` and
     `_load_model(run_paths, threaded.artifacts[0].ref).estimator.get_params()["n_jobs"] == 1`
     (cast the estimator to `BaseEstimator` if mypy needs it; report).
   - `seen.clear()`; `monkeypatch.setattr(training, "fit_threads", lambda: 1)`;
     `single = train_estimator(...same args...)`; assert `seen[0][0] == 1`.
   - Determinism: `pd.testing.assert_frame_equal(_loaded(run_paths, predict_proba(run_paths, threaded.artifacts[0].ref, ref)), _loaded(run_paths, predict_proba(run_paths, single.artifacts[0].ref, ref)))`.
   - `seen.clear()`; `train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=1)`;
     assert `seen` has one entry whose backend is not `"ThreadingBackend"` (no threading
     context for non-forests): `assert seen[0][1] != "ThreadingBackend"`.
   If `FittedModel` has no `estimator` attribute or `predict_proba` is not importable in the
   file, stop and report.

Checks: `make test-fast TESTS="tests/test_registry.py tests/test_tools_training.py tests/test_tools_interpretation.py"`;
Ruff check, Ruff format --check and mypy on the four owned files.

Main then: PLAN D17 addendum 50 row, review, `make check`, commit.

## Acceptance receipt (2026-10-08)

Implementer: focused pytest (registry, training, interpretation) exit 0; Ruff check/format and
mypy on the four owned files exit 0. `_set_params` mutates in place (called as a statement);
`Task` and `BaseEstimator` imported inside the new test. Main reviewed the diff and ran
`make check` exit 0: coverage 100 %, mypy 195 files, Ruff and format clean (log
`check50.log`, temporary). Speed on real data is the user's next online run.
