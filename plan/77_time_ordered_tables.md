---
status: completed
dependencies: [76]
completion_date: 2026-10-10
implementation_notes: "77a temporal split + time-ordered CV; 77b lag features; 77c backend predictions on unlabelled rows; 77d run-page Predictions tab; 77e backend predictions on an uploaded file; 77f upload in the Predictions tab"
---

# Chunk 77 — time-ordered tables and predictions

User request 2026-10-10 (after chunk 76's leakage analysis of run `r_f5e9364ca69378fa`): a real
time-ordered split, time series support, and predictions. User decisions: "Time-ordered tables"
(keep the tabular models; add a temporal split, time-ordered cross-validation and lag features;
a separate forecasting domain is out of scope) and "Yes, add predictions" (predict rows without a
target with the finished run's champion: the dataset's unlabelled rows or a newly uploaded file;
download as CSV).

Relevant PLAN decisions: D23 (per-strategy split tools: `split_temporal` takes `time_column`),
D25 (`split_temporal`/`split_grouped` deferred out of v1; node 4 escalates instead of falling
back to random — this chunk ships `split_temporal`; `split_grouped` stays deferred), D26 (one
shared split), D32 (no fitting on test; test scored once), D33c (split size floors), D12
(per-node tool allow-lists come from the skill's `### Tools`).

Stages (execution order; each later spec is written after the previous handoff):
- **77a** temporal split and time-ordered cross-validation (this file, below).
- **77b** lag features: contract `features.lags` (column → row lags), computed on the whole
  ordered frame before the split, so excluded columns (e.g. `target_reg`) and the target can be
  lag sources; requires a temporal split.
- **77c** backend predictions for the dataset's unlabelled rows, on demand after the champion:
  take the rows with no target from the frame the split was made from (so they carry the same
  pre-split steps, lags included), replay the champion's chain and model on them and write
  `predictions.csv` in the run folder (works for any finished run, any task and split).
- **77d** run-page Predictions tab: a button, a preview of the first rows, Download CSV.
- **77e** backend predictions for an uploaded file: the file's rows join the dataset's raw rows,
  the run's own preparation steps (casts, lags, dropped columns; row filters left out) are
  replayed on the combined table so lags read the dataset's history, then the champion predicts
  the uploaded rows into `uploaded_predictions.csv`.
- **77f** Predictions tab: upload a file and download its predictions.

## Stage 77a (detailed spec)

Goal: a contract with `split.strategy: "temporal"` and `split.time_column` runs end to end:
earlier rows train, later rows validate, the latest rows test; tuning, calibration and
cross-validation use forward-chaining folds. Non-goals: grouped splits, lag features (77b),
predictions (77c/d), target encoding's internal folds (`encoding.py`, train-only), UI changes
(the contract editor already offers `split.strategy` and a column picker for
`split.time_column`, `frontend/src/setup/SchemaForm.tsx` ~239).

Owned: `mcp_server/statlab_tools/tools/splitting.py`, `mcp_server/statlab_tools/tools/training.py`,
`mcp_server/statlab_tools/tools/evaluation.py`, `mcp_server/statlab_tools/tools/intake.py`,
`backend/statlab/domains/foundation.py`, `skills/modelling.md`, `tests/test_tools_splitting.py`,
`tests/test_tools_training.py`, `tests/test_tools_intake_validate.py`,
`tests/test_nodes_foundation.py`. Not alone: preserve others' edits.

Existing facts the recipe relies on: the split node calls `split_{rules.strategy}`
(`foundation.py` ~751); `SplitRules` V11 (`contracts/statlab_contracts/contract.py` ~226) makes
`time_column` mandatory for `temporal`; the time column is structurally protected from the
pre-split `drop_columns` (`foundation.py` ~705-711) and dropped later by candidate preprocessing
when it is in `features.exclude` (`candidate_pipeline.py` ~262-284), so the split tool never
drops it; per-candidate transforms keep their input's row order, so a time-ordered train frame
stays time-ordered; `training.cv_splitter` (~242) is the one CV splitter for tuning
(`tune_estimator` ~557), calibration (`_wrap_calibrated` ~466) and `evaluation._cv_prepare`
(~1126); the contract snapshot is read with `DataContract.model_validate_json(
paths.contract_snapshot_json.read_text())` when the file exists (`training.tuning_from_snapshot`
~144).

### 1. `splitting.py`

- Module docstring first paragraph: "Three tools -- `split_random`, `split_stratified`,
  `verify_split` --" becomes "Four tools -- `split_random`, `split_stratified`,
  `split_temporal` (chunk 77), `verify_split` --". Add `is_numeric_column` to the existing
  `statlab_tools.tools._frame` import.
- `_finish(paths, splits, target, dropped, extra: dict[str, JsonValue] | None = None)`: after
  `summary` is built, `if extra: summary.update(extra)`. Docstring: append "`extra` adds
  tool-specific summary keys."
- New private helper after `_cut`:
  `def _tie_end(values: np.ndarray, cut: int) -> int:` docstring "Move a cut forward to the end
  of its tie group so rows with the same time stay in one split." Body: if `0 < cut <
  len(values)`: `return int(np.searchsorted(values, values[cut - 1], side="right"))`; else
  `return cut`.
- New private helper `def _time_key(times: pd.Series, time_column: str) -> np.ndarray:`
  docstring "Sortable values of the time column: numbers as float64, dates (datetime columns or
  ISO 8601 text) as naive UTC datetime64." Body, in order:
  1. `missing = int(times.isna().sum())`; if `missing`: raise `ToolDataError(f"time_column
     {time_column!r} has {missing} missing value(s); a temporal split needs a time for every
     row")`.
  2. If `is_numeric_column(times)`: `return times.to_numpy(dtype="float64")`.
  3. If `pd.api.types.is_datetime64_any_dtype(times)`: `parsed = pd.to_datetime(times,
     utc=True)`; else `parsed = pd.to_datetime(times, format="ISO8601", errors="coerce",
     utc=True)`, then `bad = int(parsed.isna().sum())`; if `bad`: raise
     `ToolDataError(f"time_column {time_column!r}: {bad} value(s) are neither numbers nor ISO
     8601 dates")`.
  4. `return parsed.dt.tz_localize(None).to_numpy()`.
- New tool after `split_stratified`, before `verify_split`:
  `@tool(phase="splitting", allow_in=["data_splitting"])`
  `def split_temporal(paths: RunPaths, frame: Annotated[ArtifactRef, Field(description="The
  dataframe to split.")], target: _Target, time_column: Annotated[str, Field(min_length=1,
  description="Column that orders rows in time (numbers or dates). Earlier rows train, later
  rows validate, the latest rows test.")], test_size: _TestSize = 0.2, valid_size: _ValidSize
  = 0.2) -> ToolSuccess:`
  Docstring: "Partition rows by time: the earliest rows train, the next validate, the latest
  test. Use when rows are ordered in time and later rows must be predicted from earlier ones;
  a random split would let the model see the future. Each split's rows are in time order, so
  cross-validation inside train can use forward-chaining folds. Rows with the same time stay
  in one split. Null targets are dropped first (D34d); the same D33c guard refuses an
  undersized split. No seed: the cut is deterministic."
  Body, in order:
  1. `data, dropped = _prepare(paths, frame, target, test_size, valid_size)`.
  2. If `time_column not in data.columns`: raise `ToolParamError(f"time_column {time_column!r}
     is not a column in the data", field_errors={"time_column": "unknown"})`.
  3. `key = _time_key(data[time_column], time_column)`; `order = np.argsort(key,
     kind="stable")`; `ordered = key[order]`; `n = len(data)`.
  4. `train_end = _tie_end(ordered, n - round(n * test_size) - round(n * valid_size))`;
     `valid_end = _tie_end(ordered, max(n - round(n * test_size), train_end))`.
  5. `splits: _Splits = {"train": data.iloc[order[:train_end]], "valid":
     data.iloc[order[train_end:valid_end]], "test": data.iloc[order[valid_end:]]}`.
  6. `ranges: dict[str, JsonValue] = {name: ([json_scalar(part[time_column].iloc[0]),
     json_scalar(part[time_column].iloc[-1])] if len(part) else []) for name, part in
     splits.items()}`.
  7. `return _finish(paths, splits, target, dropped, extra={"time_column": time_column,
     "time_ranges": ranges})`.

### 2. `training.py`

- New public helper after `tuning_from_snapshot`: `def split_is_temporal(paths: RunPaths) ->
  bool:` docstring "True when the contract snapshot asks for a temporal split (chunk 77); then
  cross-validation folds must run forward in time." Body: if
  `paths.contract_snapshot_json.exists()`: return
  `DataContract.model_validate_json(paths.contract_snapshot_json.read_text()).split.strategy
  == "temporal"`; return `False`.
- `cv_splitter(cv_folds: int, task: Task, seed: int, temporal: bool = False) -> object`: import
  `TimeSeriesSplit` alongside the others; first statement after the import: `if temporal:
  return TimeSeriesSplit(n_splits=cv_folds)`. Docstring: append "With `temporal` (a
  temporal-split run), `TimeSeriesSplit`: each fold validates on rows after its training rows;
  train frames from `split_temporal` are in time order."
- `_wrap_calibrated(estimator: object, cv_folds: int, seed: int, temporal: bool) -> object`:
  pass `temporal` to `cv_splitter`. Its caller in `train_estimator` passes
  `split_is_temporal(paths)`.
- `tune_estimator`: `splitter = cv_splitter(cv_folds, entry.task, seed,
  split_is_temporal(paths))`.

### 3. `evaluation.py`

- Import `split_is_temporal` from `statlab_tools.tools.training` with the existing names.
- `_cv_prepare`: `splitter = cv_splitter(cv_folds, entry.task, seed, split_is_temporal(paths))`.

### 4. `intake.py` `_check_split_time_column`

Docstring "C12: `split.time_column`, when set, holds numbers or dates (chunk 77: numbers are
accepted)." After the `None`/missing guard: if `is_numeric_column(data[column]) or
pd.api.types.is_datetime64_any_dtype(data[column])`: `return []`. Mismatch text becomes
`f"only {share:.0%} of values parse as ISO 8601 dates and the column is not numeric"`.

### 5. `foundation.py`

- `_split_rules`: replace the single refusal (`if strategy in ("temporal", "grouped") or
  decision.dependence != "independent": raise ...`) with, in order:
  1. if `strategy == "grouped" or decision.dependence == "grouped"`: raise
     `_RefusalError("grouped rows have no supported split yet; review data")`.
  2. if `strategy == "temporal" and contract.split.time_column is None`: raise
     `_RefusalError("a temporal split needs split.time_column in the contract")`.
  3. if `decision.dependence == "temporal" and strategy != "temporal"`: raise
     `_RefusalError("rows depend on time: set split.strategy to temporal and split.time_column
     in the contract")`.
  `rules = SplitRules(strategy=strategy, test_size=..., valid_size=..., seed=...,
  time_column=contract.split.time_column if strategy == "temporal" else None)`. The stratified
  regression refusal stays after it.
- `_split` decision question becomes: "Choose only an absent strategy. Flag temporal or
  grouped dependence: a temporal split needs split.time_column in the contract, and grouped
  rows cannot be split yet. Contract ratios, seed and structural policies remain fixed."
- `_split` split call: build `arguments: dict[str, JsonValue] = {"frame": str(frame), "target":
  problem.target, "test_size": rules.test_size, "valid_size": rules.valid_size}`; if
  `rules.strategy == "temporal"`: `arguments["time_column"] = rules.time_column`; else
  `arguments["seed"] = rules.seed`; call `f"split_{rules.strategy}"` with `arguments`.
  Nothing else changes.

### 6. `skills/modelling.md` `## data_splitting`

- Heuristics: replace "- Use random splitting only for independent rows; escalate temporal or
  grouped data because v1 has no such split." with "- Use random splitting only for independent
  rows. Time-ordered rows need split.strategy temporal with split.time_column in the contract;
  flag temporal dependence when the contract has no such split. Grouped rows have no split yet:
  escalate."
- Tools: add `split_temporal` on its own line after `split_stratified`.

### 7. Tests

`tests/test_tools_splitting.py` (import `split_temporal`; helpers `_save`, `_loaded_splits`,
`_artifact_files` exist):
- `test_temporal_split_orders_by_time_and_reports_ranges(run_paths)`: frame `t = [(i * 37) %
  200 for i in range(200)]`, `y = [i % 2 for i in range(200)]`, `x = list(range(200))`.
  `res = split_temporal(run_paths, ref, target="y", time_column="t")`. Assert
  `res.summary["rows"] == {"train": 120, "valid": 40, "test": 40}`; loaded splits' `t` lists
  equal `list(range(120))`, `list(range(120, 160))`, `list(range(160, 200))`;
  `res.summary["time_column"] == "t"`; `res.summary["time_ranges"] == {"train": [0, 119],
  "valid": [120, 159], "test": [160, 199]}`; rows keep their values:
  `splits["train"]["x"].tolist() == [frame["x"][frame["t"] == v].item() for v in range(120)]`.
- `test_temporal_split_keeps_ties_together(run_paths)`: `t = [i // 10 for i in range(200)]`, `y
  = [i % 2 for i in range(200)]`; `split_temporal(..., test_size=0.18, valid_size=0.18)` →
  `rows == {"train": 130, "valid": 40, "test": 30}`; the sets of `t` per split are pairwise
  disjoint.
- `test_temporal_split_parses_dates(run_paths)`: `stamps = pd.date_range("2024-01-01",
  periods=200, freq="h")`; `positions = [(i * 37) % 200 for i in range(200)]`; frame A with `t =
  [stamps[p].strftime("%Y-%m-%dT%H:%M") for p in positions]` (text), `y = [i % 2 for i in
  range(200)]`: train `t` equals `[stamps[p].strftime("%Y-%m-%dT%H:%M") for p in range(120)]`;
  `time_ranges["test"] == [stamps[160].strftime("%Y-%m-%dT%H:%M"),
  stamps[199].strftime("%Y-%m-%dT%H:%M")]`. Frame B with `t = [stamps[p] for p in positions]`
  (datetime dtype): `rows == {"train": 120, "valid": 40, "test": 40}` and train `t` equals
  `list(stamps[:120])`.
- `test_temporal_split_rejects_bad_time_columns(run_paths)`: on a 200-row numeric frame:
  `time_column="nope"` raises `ToolParamError`; a `t` with one `None` (row 5) raises
  `ToolDataError` matching "missing value"; text `t` with one `"soon"` among ISO strings raises
  `ToolDataError` matching "neither numbers nor ISO 8601"; in each case `_artifact_files` is
  unchanged.

`tests/test_tools_training.py` (import `split_is_temporal` and `cv_splitter` from training,
`cross_validate_estimator` from `statlab_tools.tools.evaluation`, `SplitRules` and
`TargetSpec` from `statlab_contracts.contract`):
- `test_temporal_snapshot_uses_forward_folds(run_paths)`: assert `split_is_temporal(run_paths)
  is False`; write `DataContract(target=TargetSpec(column="y"),
  split=SplitRules(strategy="temporal", time_column="f1")).model_dump_json()` to
  `run_paths.contract_snapshot_json`; assert `split_is_temporal(run_paths) is True`; assert
  `type(cv_splitter(5, "classification", 0, temporal=True)).__name__ == "TimeSeriesSplit"`.
  `ref = _save(run_paths, _separable_binary(n=240))`; `tune_estimator` with
  `name="logistic_regression", metric="roc_auc", param_grid={"C": (0.1, 1.0, 10.0)}` for seeds 1
  and 99: `a.summary["results"] == b.summary["results"]` (forward folds ignore the seed).
  `cross_validate_estimator(run_paths, ref, target="y", name="logistic_regression",
  metric="roc_auc", seed=s)` for seeds 1 and 99: equal `fold_scores`, each of length 5.
  `train_estimator(run_paths, ref, target="y", name="logistic_regression", seed=s,
  calibrate=True)` for seeds 1 and 99: `predict_proba` outputs (via `_loaded`) are equal
  (`np.allclose` on `proba__1`).

`tests/test_tools_intake_validate.py`: `test_c12_time_column_numbers_and_datetimes_pass(seeded_run,
write_snapshot)`: same contract as the ISO test; frame `{"y": [0, 1], "d": [3, 1]}` → `valid is
True`; frame `{"y": [0, 1], "d": pd.to_datetime(["2024-01-01", "2024-02-01"])}` → `valid is
True`.

`tests/test_nodes_foundation.py`:
- `OfflineTools.call`: the split branch handles `("split_random", "split_stratified",
  "split_temporal")`; the harness allow-list set adds `"split_temporal"`.
- `test_unsupported_contract_strategy_has_no_actions`: parametrize `["grouped"]` only.
- New `test_temporal_contract_splits_by_time_column(migrated_db, project, tmp_path)`:
  `harness(..., contract=DataContract(target=TargetSpec(column="y", type="binary"),
  split=SplitRules(strategy="temporal", time_column="x")))`; `env.llm.responses[SplitDecision]
  = SplitDecision(rationale="time order", dependence="temporal")`; `await env.advance(4)`;
  `split = next(params for _, tool, params in env.tools.calls if tool == "split_temporal")`;
  `{k: v for k, v in split.items() if k != "frame"} == {"target": "y", "time_column": "x",
  "test_size": 0.2, "valid_size": 0.2}`; no call named `split_random` or `split_stratified`;
  `shared_result(env.state, "data_splitting").summary["train"] == TRAIN`.
- New `test_temporal_proposal_without_time_column_refuses(migrated_db, project, tmp_path)`:
  default contract `DataContract(target=TargetSpec(column="y", type="binary"))`;
  `SplitDecision(rationale="time", strategy="temporal")`; `await h.advance(3)`; `out = await
  h.step()`; failed with message `"a temporal split needs split.time_column in the contract"`;
  no split tool called.
- `test_decision_dependence_has_no_actions` stays (both cases still fail); add for the
  `temporal` case `out.error.message == "rows depend on time: set split.strategy to temporal and
  split.time_column in the contract"` and for `grouped` `"grouped rows have no supported split
  yet; review data"` (assert after obtaining the result from `foundation.step()`).

### 8. Checks

`make test-fast TESTS="tests/test_tools_splitting.py tests/test_tools_verify_split.py
tests/test_tools_training.py tests/test_tools_evaluation.py tests/test_tools_intake_validate.py
tests/test_nodes_foundation.py tests/test_skills.py tests/test_skeleton.py
tests/test_graph_engine_tools.py tests/test_candidate_fanout_tools.py"`; Ruff check, Ruff
format --check and mypy on every owned Python file; `.venv/bin/python -m integration.run_scenarios
--llm offline --only S01 S07` (offline, no cost).

Required probe (outside the repository, temporary directory deleted afterwards): as in chunk
76's probe, save
`projects/teszt/datasets/46b569a74d7972428e4f00c3b595cbb4da6e7ce6a7ff7f226145a1699beffab5.csv`
as a dataframe artifact and call `split_temporal(paths, ref, target="target_flag",
time_column="rowID")`; report `rows`, `rows_dropped_null_target`, `time_ranges` and the
`target_balance` check status.

Stop and report on any failure, unexpected value or spec gap. Main then: review, `make check`,
commit, then write 77b.

### Addendum 77a-A (typed summary access in tests)

`ToolSuccess.summary` values are `JsonValue`. In `tests/test_tools_splitting.py` read nested
summary values with the existing `_get` helper, e.g. `_get(res.summary, "time_ranges", "test")
== [...]` (and likewise for any other nested `time_ranges` access). In
`tests/test_tools_training.py` use the existing `_list` helper:
`len(_list(cv.summary["fold_scores"])) == 5` (and compare `fold_scores` values directly with
`==`, which needs no narrowing). Then rerun every check of subsection 8 and the probe.

### Addendum 77a-B (skill guard test; artifact snapshot placement)

1. `tests/test_skills.py` joins the owned files. In `test_modelling_policy` replace
   `assert "split_temporal" not in policy.original_bytes.decode()` with
   `assert "split_temporal" in policy.node("data_splitting").allowed` (the tool now ships and
   the skill allows it). The `split_grouped` assertion stays.
2. `tests/test_tools_splitting.py` `test_temporal_split_rejects_bad_time_columns`: take
   `before = _artifact_files(run_paths)` again directly before each `split_temporal` call (after
   that case's `_save`), so each comparison covers only the tool call.
Then rerun every check of subsection 8 and the probe.

### Addendum 77a-C (cover the cut at the end of the data)

`make check` coverage: `splitting.py` line 310 (`_tie_end`'s `return cut`) is never run. It is
reached when `round(n * test_size) == 0`, so the valid cut lands on `n` and test is empty; the
size floor then refuses. Add `test_temporal_split_with_empty_test_is_refused(run_paths)` to
`tests/test_tools_splitting.py`: frame `{"t": list(range(40)), "y": [i % 2 for i in
range(40)]}`; `before = _artifact_files(run_paths)` after `_save`; `with
pytest.raises(ToolDataError, match="MIN_SPLIT_ROWS"): split_temporal(run_paths, ref, target="y",
time_column="t", test_size=0.01)`; `_artifact_files(run_paths) == before`. Checks: `make
test-fast TESTS="tests/test_tools_splitting.py"`, Ruff check/format --check and mypy on
`tests/test_tools_splitting.py`.

## Stage 77a acceptance receipt (2026-10-10)

Implementer (after addenda A-C): Ruff check/format and mypy on the ten owned Python files exit
0; focused selectors (splitting, verify split, training, evaluation, intake validate, nodes
foundation, skills, skeleton, graph engine tools, candidate fanout tools) exit 0; offline S01
and S07 PASS (`integration/out/20261010T113754Z-offline`); addendum C's selector exit 0.
Probe on the user's `public_data.csv` (`split_temporal(target="target_flag",
time_column="rowID")`): rows train 43,084 / valid 14,362 / test 14,362; 17,952 null-target rows
dropped; time ranges train rowID 0-55,953, valid 55,954-74,869, test 74,870-93,168;
target_balance `warn` (class shares drift over time; a warning, not a refusal).
Main reviewed the diff against the recipe. First `make check` failed only on coverage
(`splitting.py` 310, `_tie_end`'s end-of-data branch) → addendum C. Final `make check`
(`check77a2.log`, temporary) exit 0: coverage 100 % (TOTAL 13462 stmts, 0 missed), mypy 212
files, Ruff clean.

## Stage 77b (detailed spec): lag features

Goal: the contract can ask for earlier values of a column as model inputs, e.g.
`features.lags: {"target_reg": [96], "ke": [96]}` (96 rows = one day of 15-minute rows). Lags
are computed once, in the split node, on the whole frame in `split.time_column` order, before
any row or excluded column is dropped. So an excluded column or the target can be a lag source,
and rows without a target still provide history. A lag column is named `<column>_lag<k>`. Its
value is the source column `k` rows earlier in time order (NaN for the first `k` rows), and it
is filled later by the candidate imputer. Non-goals: rolling windows, lags by time offset, lags
inside a candidate's feature step, UI changes (the contract form already renders a column →
list map as `list-map`, `frontend/src/contract/schema.ts` ~64; typed `"96"` coerces to `96`
because the contract is not strict).

Leakage note (for the skill text and the user, not a guard): a lag `k` uses the value known `k`
rows earlier. Lags of the target or of measured-afterwards columns are only honest when `k`
covers the gap between measuring and predicting (one day ahead → `k` ≥ 96 for 15-minute rows).

Owned: `contracts/statlab_contracts/contract.py`, `mcp_server/statlab_tools/tools/splitting.py`,
`mcp_server/statlab_tools/tools/intake.py`, `backend/statlab/domains/foundation.py`,
`backend/statlab/domains/candidate_pipeline.py`, `skills/modelling.md`,
`tests/test_contract_models.py`, `tests/test_tools_splitting.py`,
`tests/test_tools_intake_validate.py`, `tests/test_nodes_foundation.py`,
`tests/test_nodes_candidate_pipeline.py`. Not alone: preserve others' edits.

### 1. `contract.py`

- Module-level function before `class FeatureRules`: `def lag_column(column: str, lag: int) ->
  str:` docstring "Name of the lag feature for `column` `lag` rows earlier (chunk 77)." returns
  `f"{column}_lag{lag}"`.
- `FeatureRules`: new field after `ordinal_levels`: `lags: dict[str, tuple[int, ...]] =
  Field(default_factory=dict, description="Earlier values of a column as extra inputs: column ->
  row lags in time order (e.g. 96 = one day earlier for 15-minute rows). Needs a temporal
  split. The column may be excluded or the target; choose lags at least as large as the gap
  between measuring a value and predicting.")`.
- New validator in `FeatureRules` after the existing ones: `@model_validator(mode="after") def
  _lags_are_positive_and_distinct(self) -> FeatureRules:` docstring "V13 (chunk 77): every
  `lags` entry lists distinct row lags of at least 1." For each `column, lags` in
  `self.lags.items()`: if `not lags or any(lag < 1 for lag in lags) or len(set(lags)) !=
  len(lags)`: raise `ValueError(f"features.lags.{column} must list distinct row lags of at
  least 1, got {list(lags)!r}")`. Return `self`.
- New method in `FeatureRules`: `def lag_columns(self) -> tuple[str, ...]:` docstring "Names of
  the lag features the contract adds, in `lags` order." returns `tuple(lag_column(column, lag)
  for column, lags in self.lags.items() for lag in lags)`.
- `DataContract`: new validator after `_stratified_requires_classification_target`:
  `@model_validator(mode="after") def _lags_require_temporal_split(self) -> DataContract:`
  docstring "X5 (chunk 77): `features.lags` needs `split.strategy == "temporal"`; lags follow
  its `time_column` order." If `self.features.lags and self.split.strategy != "temporal"`: raise
  `ValueError("features.lags needs split.strategy 'temporal' with split.time_column: lags follow
  the time order")`. Return `self`.

### 2. `splitting.py`

- Imports: add `Field` usage as needed (already imported), `from statlab_contracts.contract
  import lag_column`, and `json_list`, `save_frame` to the `_frame` import.
- Module docstring: after the sentence naming the four tools add "`add_lag_features` (chunk 77)
  adds contract lags to the whole frame before the split; it shares the time ordering."
- New tool after `split_temporal`:
  `@tool(phase="splitting", allow_in=["data_splitting"])`
  `def add_lag_features(paths: RunPaths, frame: Annotated[ArtifactRef, Field(description="The
  whole frame before the split, including rows without a target.")], time_column:
  Annotated[str, Field(min_length=1, description="Column that orders rows in time (numbers or
  dates).")], lags: Annotated[dict[str, tuple[Annotated[int, Field(ge=1)], ...]],
  Field(min_length=1, description="Numeric column -> row lags. Adds <column>_lag<k>: the value
  k rows earlier in time order.")]) -> ToolSuccess:`
  Docstring: "Add earlier values of numeric columns as new columns, in time order. Runs on the
  whole frame before the split so rows without a target still give history and an excluded
  column or the target can be a source. The first k rows in time order get a missing value.
  Rows keep their order and labels."
  Body, in order:
  1. `data = load_dataframe(paths.artifact(frame, "dataframe"))`.
  2. `time_column` not in columns → `ToolParamError(f"time_column {time_column!r} is not a
     column in the data", field_errors={"time_column": "unknown"})`.
  3. `unknown = [c for c in lags if c not in data.columns]`; if any →
     `ToolParamError(f"lag columns not in the data: {unknown!r}", field_errors={"lags":
     "unknown columns"})`.
  4. `text = [c for c in lags if not is_numeric_column(data[c])]`; if any →
     `ToolParamError(f"lag columns must be numeric: {text!r}", field_errors={"lags":
     "non-numeric columns"})`.
  5. `names = [lag_column(c, k) for c, ks in lags.items() for k in ks]`; `taken = [name for
     name in names if name in data.columns]`; if any → `ToolParamError(f"lag columns already
     exist: {taken!r}", field_errors={"lags": "name collision"})`.
  6. `key = _time_key(data[time_column], time_column)`; `order = np.argsort(key,
     kind="stable")`; `inverse = np.argsort(order)`; `out = data.copy()`.
  7. For `c, ks` in `lags.items()`: `ordered = data[c].iloc[order].reset_index(drop=True)`; for
     `k` in `ks`: `out[lag_column(c, k)] = ordered.shift(k).to_numpy(dtype="float64",
     na_value=np.nan)[inverse]`.
  8. Return `ToolSuccess(summary={"time_column": time_column, "added": json_list(names), "rows":
     json_scalar(len(out))}, artifacts=(save_frame(paths, out, "lagged"),))`.

### 3. `intake.py` C1 (`named` list, ~512-524)

After the `ordinal_levels` line add `named.extend((f"features.lags.{c}", c) for c in
contract.features.lags)`.

### 4. `foundation.py` `_split`

Directly after the `if profiled is None and contract.features.roles:` cast block and before
`protected = {...}`: if `contract.features.lags`: `frame = _frame(await self._call(invocation,
evidence, "add_lag_features", {"frame": str(frame), "time_column": rules.time_column, "lags":
{column: list(lags) for column, lags in contract.features.lags.items()}}))`. (X5 guarantees a
temporal contract, so `rules.time_column` is set.) Nothing else changes; the existing
dedupe/drop/split steps then run on the lagged frame.

### 5. `candidate_pipeline.py` (~262-292)

After `features = self.runtime.contract.features` add `lag_names = features.lag_columns()`. The
`present` line iterates `(*columns, *lag_names)` instead of `columns`. In the `drop` condition the
include clause becomes `(features.include is not None and column not in features.include and
column not in lag_names)`. Nothing else changes (lag columns then appear in
`feature_manifest["input_features"]` and are never dropped by `include`).

### 6. `skills/modelling.md` `## data_splitting`

Heuristics: append "- Contract lags (features.lags) are added from the whole time-ordered frame
before any row is dropped; a lag of the target or of a column measured afterwards is honest only
when it covers the gap to the prediction." Tools: add `add_lag_features` on its own line after
`drop_columns`.

### 7. Tests

`tests/test_contract_models.py` (uses `DataContract.model_validate` and `pytest.raises(
ValidationError, match=...)`):
- `test_lags_need_temporal_split_and_positive_distinct_lags`: base dict `{"target": {"column":
  "y"}, "split": {"strategy": "temporal", "time_column": "t"}}`. With `"features": {"lags": {"x":
  [1, 96]}}` → `features.lag_columns() == ("x_lag1", "x_lag96")`. With `{"x": ["96"]}` →
  `features.lags == {"x": (96,)}`. Without the `split` key and with lags `{"x": [1]}` → raises
  matching "features.lags needs split.strategy 'temporal'". With the split and each of `{"x":
  []}`, `{"x": [0]}`, `{"x": [2, 2]}` → raises matching "distinct row lags of at least 1".
`tests/test_tools_splitting.py` (import `add_lag_features`):
- `test_add_lag_features_follow_time_order(run_paths)`: frame `{"t": [3, 1, 2, 0, 4], "x":
  [30.0, 10.0, 20.0, 0.0, 40.0], "y": [1.0, None, 0.0, 1.0, 0.0]}`; `res =
  add_lag_features(run_paths, ref, time_column="t", lags={"x": (1, 2), "y": (1,)})`; `out =
  load_dataframe(run_paths.artifact(res.artifacts[0].ref, "dataframe"))`; `out.columns.tolist()
  == ["t", "x", "y", "x_lag1", "x_lag2", "y_lag1"]`; `out.index.tolist() == [0, 1, 2, 3, 4]`;
  `out["x_lag1"].fillna(-1.0).tolist() == [20.0, 0.0, 10.0, -1.0, 30.0]`;
  `out["x_lag2"].fillna(-1.0).tolist() == [10.0, -1.0, 0.0, -1.0, 20.0]`;
  `out["y_lag1"].fillna(-1.0).tolist() == [0.0, 1.0, -1.0, -1.0, 1.0]`; `res.summary ==
  {"time_column": "t", "added": ["x_lag1", "x_lag2", "y_lag1"], "rows": 5}`.
- `test_add_lag_features_rejects_bad_input(run_paths)`: on the frame above plus a text column
  `"s": ["a", "b", "c", "d", "e"]` and a column `"x_lag1": [0.0] * 5` saved as a second frame
  where needed: `time_column="nope"` → `ToolParamError`; `lags={"zz": (1,)}` → `ToolParamError`
  matching "not in the data"; `lags={"s": (1,)}` → `ToolParamError` matching "must be numeric";
  frame with existing `x_lag1` and `lags={"x": (1,)}` → `ToolParamError` matching "already
  exist".
`tests/test_tools_intake_validate.py`: `test_c1_lag_columns_must_exist(seeded_run,
write_snapshot)`: contract `{"target": {"column": "y"}, "features": {"lags": {"zz": [1]}},
"split": {"strategy": "temporal", "time_column": "d"}}`; frame `{"y": [0, 1], "d": [1, 2]}`;
`"features.lags.zz" in _fields(result.summary["mismatches"])`.
`tests/test_nodes_foundation.py`:
- `OfflineTools.call`: add `"add_lag_features"` to the frame-returning tuple `("cast_dtypes",
  "drop_duplicate_rows", "drop_high_missing_rows", "drop_columns")`; add `"add_lag_features"`
  to the harness allow-list set.
- New `test_contract_lags_run_before_rows_and_columns_drop(migrated_db, project, tmp_path)`:
  contract `DataContract(target=TargetSpec(column="y", type="binary"),
  features=FeatureRules(exclude=("excluded",), lags={"excluded": (1, 2), "y": (3,)}),
  split=SplitRules(strategy="temporal", time_column="x"))`; `env.llm.responses[SplitDecision] =
  SplitDecision(rationale="time order", dependence="temporal")`; `await env.advance(4)`;
  `names = [tool for _, tool, _ in env.tools.calls]`; `names.index("add_lag_features") <
  names.index("drop_duplicate_rows") < names.index("drop_columns") <
  names.index("split_temporal")`; the `add_lag_features` params minus `frame` equal
  `{"time_column": "x", "lags": {"excluded": [1, 2], "y": [3]}}`; the `drop_columns` params'
  `columns == ["excluded"]`.
`tests/test_nodes_candidate_pipeline.py`: new
`test_lag_columns_are_inputs_and_survive_include(migrated_db, project, tmp_path)`: `h, _ = await
candidate_harness(migrated_db, project, tmp_path)`; `contract = h.runtime.contract`;
`h.runtime = replace(h.runtime, contract=DataContract(target=contract.target,
objective=contract.objective, features=FeatureRules(include=("x",), id_columns=("id",),
lags={"x": (1,)}), split=SplitRules(strategy="temporal", time_column="id")))`; `await
prepare_candidate(h)`; `pipeline = pipeline_from_state(h.state, h.state.branches[0])`;
`pipeline.feature_manifest["input_features"] == ["x", "x_lag1"]`; `[step.parameters for step in
pipeline.replay if step.tool == "drop_columns"][0] == {"columns": ["id", "excluded"]}`.

### 8. Checks

`make test-fast TESTS="tests/test_contract_models.py tests/test_contract_consistency.py
tests/test_tools_splitting.py tests/test_tools_intake_validate.py tests/test_nodes_foundation.py
tests/test_nodes_candidate_pipeline.py tests/test_skills.py tests/test_tools_reproducibility.py"`;
Ruff check, Ruff format --check and mypy on every owned Python file;
`.venv/bin/python -m integration.run_scenarios --llm offline --only S01 S07`.

Required probe (outside the repository, temporary directory deleted afterwards): save the
user's `public_data.csv` (path as in 77a's probe) as a dataframe artifact; `add_lag_features(
paths, ref, time_column="rowID", lags={"ke": (96,), "target_reg": (96,)})`; on the result report
`summary`, and for rows whose `target_flag` is null: the share of non-null `ke_lag96` and of
`target_reg_lag96`. Then `split_temporal(paths, <lagged ref>, target="target_flag",
time_column="rowID")` and report `rows`.

Stop and report on any failure, unexpected value or spec gap. Main then: review, `make check`,
commit, then write 77c.

### Addendum 77b-A (validator labels; typed test loop)

1. Labels V13 and X5 are already taken (`ModellingRules` V13, `DataContract` X5). In
   `contract.py` the lag docstrings become "V14 (chunk 77): ..." for
   `_lags_are_positive_and_distinct` and "X15 (chunk 77): ..." for
   `_lags_require_temporal_split`. Text otherwise unchanged; no code change.
2. `tests/test_contract_models.py` invalid-lags loop: bind the cases first,
   `bad_lags: tuple[dict[str, list[int]], ...] = ({"x": []}, {"x": [0]}, {"x": [2, 2]})`, then
   `for lags in bad_lags:`.
Then rerun every check of subsection 8 (all, since lint edits followed the pytest run), the
offline S01 S07 run, and the probe.

## Stage 77b acceptance receipt (2026-10-10)

Implementer (after addendum A): focused selectors (contract models, contract consistency, tools
splitting, intake validate, nodes foundation, nodes candidate pipeline, skills, reproducibility)
exit 0; Ruff check/format and mypy on the owned Python files exit 0; offline S01 and S07 PASS
(`integration/out/20261010T115040Z-offline`). Probe (the implementer read
`~/Documents/Egyetem/BSc/5.semester/Python/Verseny/data/public_data.csv`; main confirmed its
sha256 `46b569a7…` equals the project dataset, so the evidence holds):
`add_lag_features(time_column="rowID", lags={"ke": (96,), "target_reg": (96,)})` added
`ke_lag96`, `target_reg_lag96` on 89,760 rows; on the 17,952 rows without a target both lag
columns are 100 % filled; `split_temporal` on the lagged frame: train 43,084 / valid 14,362 /
test 14,362 (balance-drift warning, as in 77a). Main reviewed the diff (matches the recipe and
addendum). `make check` (`check77b.log`, temporary) exit 0: coverage 100 % (TOTAL 13507 stmts,
0 missed), mypy 212 files, Ruff clean.

## Stage 77c (detailed spec): backend predictions for rows without a target

Goal: `POST /api/runs/{run_id}/predictions` predicts the dataset's rows whose target is missing
with the run's champion and writes `<run>/predictions.csv`; `GET
/api/runs/{run_id}/files/predictions.csv` downloads it. Works for any run whose
`champion_selection` completed (classification or regression, any split strategy, forks).

Non-goals: the UI (77d), uploaded files (77e), registering the CSV as an artifact, LLM calls,
run-scoped tool audit, changing how runs execute.

Design facts (main, read-only):
- Source rows: the `frame` argument of the logged `split_*` tool call whose result artifacts
  include the shared `data_splitting` summary's `test` ref. That frame is the whole table after
  the shared pre-split steps (cast, lags, dedupe, high-missing rows, excluded columns) and before
  `split_*` drops rows without a target (`splitting.py` ~253). Such calls live in `tool_calls`
  of the run or a fork ancestor (forks hard-link parent artifacts under the same refs,
  `storage.py` ~215). Checked on the user DB (read-only): every successful split's `frame`
  artifact exists and none is pruned.
- Champion: `shared_result(state, "champion_selection").summary` has `model` (str ref),
  `threshold` (float or null) and `replay` (list of `ReplayStep` dumps: `tool`, `parameters`
  without `frame`/`project`/`run`), exactly what champion scoring replays on test
  (`champion.py` ~356-372).
- Replay tools (`apply_*`, `drop_columns`, row-wise constructors) and `predict`/`predict_proba`
  are `transform_only`; each takes `frame` and returns one new frame/predictions artifact.
  Dispatching them through `McpClient._dispatch_direct` keeps the served-schema validation and
  error envelope without run accounting. The frames they write are not registered; the client
  deletes them afterwards.
- `D32`: no test rows are involved; the champion is final before this runs.

Owned files: `mcp_server/statlab_tools/tools/evaluation.py`, `backend/statlab/mcp_client.py`,
`backend/statlab/api/inspect.py`, `tests/test_tools_evaluation.py`, `tests/test_mcp_client.py`,
`tests/test_api_inspect.py`. Not alone: preserve others' edits.

### 1. `evaluation.py` (directly after `validation_rows`)

Imports: add `import os`; add `save_frame` to the existing `statlab_tools.tools._frame` import.

Module constant: `PREDICTIONS_CSV: Final = "predictions.csv"`.

a. `unlabelled_rows`, decorated `@tool(phase="artifacts", allow_in=["*"])`:
   `def unlabelled_rows(paths: RunPaths, frame: Annotated[ArtifactRef, Field(description="The
   whole frame the split was made from, rows without a target included.")], target:
   Annotated[str, Field(min_length=1, description="The target column.")]) -> ToolSuccess`.
   Docstring: `"""The frame's rows whose target is missing, without the target column, saved as
   a new frame to predict on (chunk 77). The caller deletes that frame after use."""`
   Body, in order:
   1. `data = load_dataframe(paths.artifact(frame, "dataframe"))`.
   2. If `target not in data.columns`: raise `ToolParamError("target is not a column of the
      frame", field_errors={"target": "not in the frame"})`.
   3. `rows = data.loc[data[target].isna()].drop(columns=[target])`.
   4. If `rows.empty`: raise `ToolDataError("the dataset has no rows without a target to
      predict")`.
   5. Return `ToolSuccess(summary={"rows": len(rows)}, artifacts=(save_frame(paths, rows,
      "unlabelled"),))`.

b. `write_predictions`, decorated `@tool(phase="artifacts", allow_in=["*"])`:
   parameters after `paths: RunPaths`:
   - `frame: Annotated[ArtifactRef, Field(description="The predicted rows with their original
     columns.")]`
   - `predictions: Annotated[ArtifactRef, Field(description="Class probabilities
     (classification) or predictions (regression) on those rows.")]`
   - `task: Annotated[Literal["classification", "regression"], Field(description="The run's
     task.")]`
   - `threshold: Annotated[float | None, Field(gt=0, lt=1, description="Binary
     p(positive)>=threshold, as scored.")] = None`
   Docstring: `"""Join predictions to their rows by label and write predictions.csv in the run
   folder: a row column (the dataset row label), the original columns, prediction and, for
   classification, one probability_{class} column per class. Hard classes follow
   score_classification. Returns the first 20 rows (chunk 77)."""`
   Body, in order:
   1. `data = load_dataframe(paths.artifact(frame, "dataframe"))`;
      `preds = load_dataframe(paths.artifact(predictions, "predictions"))`.
   2. If `not preds.index.sort_values().equals(data.index.sort_values())`: raise
      `ToolDataError("predictions do not match the predicted rows")`. Then
      `preds = preds.loc[data.index]`.
   3. `added: dict[str, object] = {}`. If `task == "classification"`:
      - `cols = proba_columns(preds)`; `classes = class_labels(cols)`.
      - If `threshold is not None and len(classes) != 2`: raise the same `ToolParamError` as
        `validation_rows` (message `"threshold is binary only; this target has more than two
        classes"`, `field_errors={"threshold": "not applicable to a multiclass target"}`).
      - `positive, _ = _positive_class(snapshot(paths), classes)`;
        `proba = preds[cols].to_numpy(dtype="float64")`;
        `codes = _hard_codes(proba, classes.index(positive), threshold)`.
      - `added["prediction"] = [classes[int(code)] for code in codes]`; then for
        `index, label in enumerate(classes)`: `added[f"probability_{label}"] = proba[:, index]`.
      Else (regression):
      - If `"prediction" not in preds.columns`: raise `ToolParamError("predictions need a
        prediction column", field_errors={"predictions": "no prediction column"})`.
      - `added["prediction"] = preds["prediction"].to_numpy(dtype="float64")`.
   4. `clashes = [name for name in added if name in data.columns]`; if any: raise
      `ToolDataError(f"the dataset already has columns named {clashes!r}")`.
   5. `out = data.assign(**added)`.
   6. `temporary = paths.root / f".{PREDICTIONS_CSV}.{new_artifact_ref()}.tmp"`;
      `out.to_csv(temporary, index_label="row")`;
      `os.replace(temporary, paths.root / PREDICTIONS_CSV)`.
   7. `head: list[JsonValue] = [{"row": json_scalar(label), **{str(column): json_scalar(value)
      for column, value in record.items()}} for label, record in zip(out.index[:20],
      out.head(20).to_dict(orient="records"), strict=True)]`.
   8. Return `ToolSuccess(summary={"task": task, "rows": len(out), "columns": ["row",
      *(str(column) for column in out.columns)], "head": head})` (no artifacts).

### 2. `mcp_client.py` (`McpClient`, directly after `validation_rows`)

a. `def _prediction_spec(self, tool: str, *, replayed: bool) -> ToolSpec`:
   `spec = self.tools.get(tool)`. If `replayed`: if `spec is None or not spec.transform_only`:
   raise `EngineError(f"{tool} cannot be replayed for predictions")`. Else: if `spec is None or
   spec.phase != "artifacts" or spec.allow_in != ("*",) or spec.heavy or spec.transform_only`:
   raise `EngineError(f"{tool} registry metadata is invalid")`. Return `spec`.

b. `async def _prediction_step(self, tool: str, arguments: dict[str, JsonValue], made:
   list[ArtifactMeta], *, replayed: bool) -> str`:
   `spec = self._prediction_spec(tool, replayed=replayed)`;
   `result = await self._dispatch_direct(spec, arguments, read_timeout_seconds=900)`;
   `made.extend(result.artifacts)`; if `len(result.artifacts) != 1`: raise
   `ToolCallError(tool, Failure(error_class="bug", message=f"{tool} must return exactly one
   artifact"))`; return `str(result.artifacts[0].ref)`.

c. Public method:
   `async def predict_unlabelled(self, *, project: str, run: str, frame: str, target: str,
   task: Literal["classification", "regression"], model: str, threshold: float | None, replay:
   tuple[tuple[str, dict[str, JsonValue]], ...]) -> ToolSuccess`, docstring `"""Predict the
   dataset's rows without a target with the champion and write predictions.csv, without
   run-scoped accounting (chunk 77). Frames made on the way are deleted afterwards."""`
   Body, in order:
   1. If `self._session is None`: raise `EngineError("MCP client is not entered")`.
   2. If `get_project_by_slug(self.conn, project) is None`: raise
      `EngineError("predict_unlabelled project does not exist")`.
   3. `paths = RunPaths(self.settings.data_dir, project, run)`; `made: list[ArtifactMeta] = []`.
   4. `try:`
      - `rows = await self._prediction_step("unlabelled_rows", {"project": project, "run": run,
        "frame": frame, "target": target}, made, replayed=False)`; `current = rows`.
      - For `tool, parameters in replay`: `current = await self._prediction_step(tool,
        {**parameters, "project": project, "run": run, "frame": current}, made,
        replayed=True)`.
      - `predicted = await self._prediction_step("predict_proba" if task == "classification"
        else "predict", {"project": project, "run": run, "model": model, "frame": current},
        made, replayed=True)`.
      - `arguments: dict[str, JsonValue] = {"project": project, "run": run, "frame": rows,
        "predictions": predicted, "task": task}`; if `threshold is not None`:
        `arguments["threshold"] = threshold`.
      - `written = await self._dispatch_direct(self._prediction_spec("write_predictions",
        replayed=False), arguments, read_timeout_seconds=900)`.
      - If `written.artifacts`: raise `ToolCallError("write_predictions",
        Failure(error_class="bug", message="write_predictions returned unexpected
        artifacts"))`.
      - Return `written`.
   5. `finally:` for `meta in made`: if `ARTIFACT_ID_PATTERN.match(str(meta.ref))` and
      `get_artifact(self.conn, str(meta.ref)) is None`: `paths.artifact(meta.ref,
      meta.kind).unlink(missing_ok=True)`. (Never delete a registered artifact or a path built
      from a malformed ref.)

### 3. `api/inspect.py`

Imports: `json`; `from pydantic import ValidationError` (add to the existing pydantic import);
`from statlab_contracts.envelope import TOOL_RESULT_ADAPTER, ToolSuccess`;
`from statlab.domains.decisions import ReplayStep`; `ToolCallError` added to the
`statlab.errors` import; `run_ancestry` added to the repository import.

a. `RUN_FILES` gains `"predictions.csv": (("predictions.csv",), "text/csv; charset=utf-8")`
   (last entry).
b. Constants after `NO_VALIDATION`: `NO_CHAMPION = "this run has no champion model yet"` and
   `NO_SOURCE = "the rows before the split are not recorded for this run"`.
c. Module function after `_artifact`:
   `def _split_source(conn: sqlite3.Connection, run_id: str, test: str) -> str | None:` with
   docstring `"""The frame the shared split was made from: the frame argument of the logged
   split call that produced the test frame, in the run or a fork ancestor (chunk 77)."""`
   (`import sqlite3` at the top). For `ancestor in run_ancestry(conn, run_id)`: for `call in
   list_tool_calls(conn, ancestor.id)`: `if not call.tool_name.startswith("split_"):
   continue`; `result = TOOL_RESULT_ADAPTER.validate_json(call.result_json)`;
   `source = json.loads(call.args_json).get("frame")`; if `isinstance(result, ToolSuccess) and
   any(str(meta.ref) == test for meta in result.artifacts) and isinstance(source, str)`:
   return `source`. After the loops return `None`.
d. Route inside `inspect_router`, after `validation`:
   `@router.post("/api/runs/{run_id}/predictions")`
   `async def predictions(run_id: str) -> dict[str, JsonValue]:`
   1. `resource = resources()`; `run, paths = _run(resource, run_id)`;
      `state = _checkpoint(paths)`; if `state is None`: raise `HTTPException(409,
      NO_CHAMPION)`.
   2. `try:` `champion = shared_result(state, "champion_selection").summary`;
      `split = shared_result(state, "data_splitting").summary`;
      `problem = problem_from_state(state)`; `except EngineError: raise HTTPException(409,
      NO_CHAMPION) from None`.
   3. `model = champion.get("model")`; `replay = champion.get("replay")`;
      `test = split.get("test")`; if `not isinstance(model, str) or not isinstance(replay,
      list) or not isinstance(test, str)`: raise `HTTPException(409, NO_CHAMPION)`.
   4. `try: steps = tuple(ReplayStep.model_validate(item) for item in replay)` `except
      ValidationError: raise EngineError("champion replay is malformed") from None`.
   5. `source = _split_source(resource.conn, run.id, test)`; if `None`: raise
      `HTTPException(409, NO_SOURCE)`.
   6. `threshold = champion.get("threshold")`.
   7. `try: result = await resource.mcp.predict_unlabelled(project=paths.slug, run=run.id,
      frame=source, target=problem.target, task=problem.task, model=model,
      threshold=threshold if isinstance(threshold, float) else None, replay=tuple((step.tool,
      dict(step.parameters)) for step in steps))` `except ToolCallError as exc:` if
      `exc.failure.error_class in ("tool_param", "data")`: raise `HTTPException(422,
      exc.failure.message) from None`; otherwise `raise`.
   8. Return `result.summary`.

### 4. Tests

a. `tests/test_tools_evaluation.py`: import `unlabelled_rows`, `write_predictions` in the
   existing evaluation import block and `load_dataframe` in the `statlab_tools.artifacts`
   import. After `test_validation_rows_refusals` add:
   - `test_unlabelled_rows(run_paths)`: `frame = _save(run_paths, pd.DataFrame({"x": [1, 2,
     3, 4], "y": [0.0, None, 1.0, None]}, index=[10, 11, 12, 13]))`; `res =
     unlabelled_rows(run_paths, frame=frame, target="y")`; assert `res.summary == {"rows": 2}`,
     `len(res.artifacts) == 1`, `res.artifacts[0].kind == "dataframe"`; `saved =
     load_dataframe(run_paths.artifact(res.artifacts[0].ref, "dataframe"))`; assert
     `list(saved.index) == [11, 13]` and `list(saved.columns) == ["x"]`. Then `pytest.raises(
     ToolParamError)` for `target="missing"`; `full = _save(run_paths, pd.DataFrame({"x": [1],
     "y": [1.0]}))` and `pytest.raises(ToolDataError)` for `unlabelled_rows(run_paths,
     frame=full, target="y")`.
   - `test_write_predictions_classification(run_paths)`: `_write_snapshot(run_paths,
     _binary_contract(1))`; `frame = _save(run_paths, pd.DataFrame({"x": [1, 2, 3], "c": ["a",
     "b", "c"]}, index=[11, 13, 15]))`; `probas = _save(run_paths, pd.DataFrame({"proba__0":
     [0.45, 0.8, 0.3], "proba__1": [0.55, 0.2, 0.7]}, index=[15, 11, 13]),
     kind="predictions")`; `res = write_predictions(run_paths, frame=frame, predictions=probas,
     task="classification")`. Assert `res.artifacts == ()`, `res.summary["rows"] == 3`,
     `res.summary["columns"] == ["row", "x", "c", "prediction", "probability_0",
     "probability_1"]`, `_l(res.summary["head"])[0] == {"row": 11, "x": 1, "c": "a",
     "prediction": "0", "probability_0": 0.8, "probability_1": 0.2}`; `csv =
     pd.read_csv(run_paths.root / "predictions.csv")`; `list(csv["row"]) == [11, 13, 15]`,
     `list(csv["prediction"]) == [0, 1, 1]`. Then `tuned = write_predictions(..., threshold=0.6)`:
     `[_d(row)["prediction"] for row in _l(tuned.summary["head"])] == ["0", "1", "0"]` and
     `list(pd.read_csv(run_paths.root / "predictions.csv")["prediction"]) == [0, 1, 0]`; and
     `[path.name for path in run_paths.root.iterdir() if path.name.endswith(".tmp")] == []`.
   - `test_write_predictions_regression(run_paths)`: `frame = _save(run_paths,
     pd.DataFrame({"x": [1, 2]}, index=[0, 1]))`; `preds = _save(run_paths,
     pd.DataFrame({"prediction": [1.5, 2.5]}, index=[0, 1]), kind="predictions")`; `res =
     write_predictions(run_paths, frame=frame, predictions=preds, task="regression")`; assert
     `res.summary == {"task": "regression", "rows": 2, "columns": ["row", "x", "prediction"],
     "head": [{"row": 0, "x": 1, "prediction": 1.5}, {"row": 1, "x": 2, "prediction": 2.5}]}`.
   - `test_write_predictions_refusals(run_paths)`: `_write_snapshot(run_paths,
     _binary_contract(1))`; `frame = _save(run_paths, pd.DataFrame({"x": [1, 2]}, index=[0,
     1]))`; `probas = _save(run_paths, pd.DataFrame({"proba__0": [0.6, 0.4], "proba__1": [0.4,
     0.6]}, index=[0, 1]), kind="predictions")`. Each `pytest.raises`:
     - `ToolDataError`: predictions `_save(run_paths, pd.DataFrame({"prediction": [1.0, 2.0]},
       index=[0, 5]), kind="predictions")`, task regression.
     - `ToolParamError`: `probas` with task regression (no prediction column).
     - `ToolParamError`: predictions `_save(run_paths, pd.DataFrame({"prediction": [1.0, 2.0]},
       index=[0, 1]), kind="predictions")` with task classification (no proba columns).
     - `ToolParamError`: three-class predictions `_save(run_paths, pd.DataFrame({"proba__0":
       [0.2, 0.3], "proba__1": [0.3, 0.3], "proba__2": [0.5, 0.4]}, index=[0, 1]),
       kind="predictions")`, task classification, `threshold=0.5`.
     - `ToolDataError`: frame `_save(run_paths, pd.DataFrame({"x": [1, 2], "prediction": [0,
       0]}, index=[0, 1]))` with `probas`, task classification.
     Finally assert `not (run_paths.root / "predictions.csv").exists()`.

b. `tests/test_mcp_client.py`, after `test_validation_rows_dispatches_without_audit`. Shared
   module helper `_OPEN: dict[str, object] = {"type": "object", "properties": {},
   "additionalProperties": True}` and `def _made(paths: RunPaths, kind: ArtifactKind) ->
   ArtifactMeta:` that mints `ref = new_artifact_ref()`, writes `b"x"` to `paths.artifact(ref,
   kind)` and returns `ArtifactMeta(ref=ref, kind=kind)` (import `ArtifactKind`). And
   `def _prediction_tools() -> tuple[object, ...]` returning `served_tool("unlabelled_rows",
   _OPEN, allow_in=["*"], phase="artifacts")`, `served_tool("apply_scaler", _OPEN,
   allow_in=["feature_engineering"], phase="encoding", transform_only=True)`,
   `served_tool("predict", _OPEN, allow_in=["champion_selection"], phase="training",
   transform_only=True)`, `served_tool("predict_proba", _OPEN,
   allow_in=["champion_selection"], phase="training", transform_only=True)`,
   `served_tool("drop_columns", _OPEN, allow_in=["*"], phase="cleaning")` (not transform-only
   here on purpose), `served_tool("write_predictions", _OPEN, allow_in=["*"],
   phase="artifacts")`. Envelopes are `ToolSuccess(...).model_dump(mode="json")`.
   - `test_predict_unlabelled_replays_and_deletes_unregistered_frames(world, tmp_path)`:
     `rows, scaled, predicted = _made(paths, "dataframe"), _made(paths, "dataframe"),
     _made(paths, "predictions")`; register `scaled` with `create_artifact(conn, ref=scaled.ref,
     run_id=paths.run_id, kind="dataframe", path=paths.artifact(scaled.ref, "dataframe"),
     meta=scaled, produced_by="seed", split=None)`. `session = FakeSession(_prediction_tools())`;
     `session.envelopes = [envelopes with artifacts (rows,), (scaled,), (predicted,), and
     ToolSuccess(summary={"rows": 2})]`. Call `predict_unlabelled(project=paths.slug,
     run=paths.run_id, frame="a_" + "1" * 16, target="y", task="regression", model="a_" + "2" *
     16, threshold=None, replay=(("apply_scaler", {"scaler": "a_" + "3" * 16}),))`. Assert
     `result.summary == {"rows": 2}`; `session.calls == [("unlabelled_rows", {"project":
     paths.slug, "run": paths.run_id, "frame": "a_" + "1" * 16, "target": "y"}, 900),
     ("apply_scaler", {"scaler": "a_" + "3" * 16, "project": paths.slug, "run": paths.run_id,
     "frame": str(rows.ref)}, 900), ("predict", {"project": paths.slug, "run": paths.run_id,
     "model": "a_" + "2" * 16, "frame": str(scaled.ref)}, 900), ("write_predictions",
     {"project": paths.slug, "run": paths.run_id, "frame": str(rows.ref), "predictions":
     str(predicted.ref), "task": "regression"}, 900)]`; `not paths.artifact(rows.ref,
     "dataframe").exists()`, `paths.artifact(scaled.ref, "dataframe").exists()`, `not
     paths.artifact(predicted.ref, "predictions").exists()`; tool_calls count 0.
   - `test_predict_unlabelled_classification_and_guards(world, tmp_path)`, in order (each
     `async with McpClient(settings(paths, tmp_path), conn, session=...)`; the kwargs below are
     the success call's unless noted):
     1. Unentered `McpClient(..., session=FakeSession(()))`: `EngineError` match `"MCP client
        is not entered"`.
     2. Entered with `FakeSession(_prediction_tools())`, `project="missing-project"`:
        `EngineError`; `session.calls == []`.
     3. Classification: envelopes `(rows,)`, `(probas,)` (`_made(paths, "predictions")`),
        `ToolSuccess(summary={"rows": 1})`; call with `task="classification"`,
        `threshold=0.4`, `replay=()`; assert names `[call[0] for call in session.calls] ==
        ["unlabelled_rows", "predict_proba", "write_predictions"]`, the predict_proba
        arguments' `"frame" == str(rows.ref)`, and the write arguments' `"threshold" == 0.4`.
     4. Not replayable: fresh session, envelopes `(rows,)`; `replay=(("drop_columns",
        {"columns": ["x"]}),)`: `EngineError` match `"cannot be replayed"`; the `rows` file no
        longer exists.
     5. Two artifacts from one step: fresh session, envelope with artifacts `(first, second)`
        (both `_made(paths, "dataframe")`): `ToolCallError`; neither file exists.
     6. Malformed ref: fresh session, envelope with artifacts `(ArtifactMeta(ref=
        ArtifactRef("a_bad"), kind="dataframe"),)` and `replay=(("drop_columns", {}),)`:
        `EngineError`; `session.calls` has one call.
     7. Write returns an artifact: fresh session, envelopes `(rows,)`, `(predicted,)`,
        envelope with artifacts `(_made(paths, "table"),)`: `ToolCallError`; the table file
        still exists (only artifacts of steps before the write are tracked).
     8. Wrong write metadata: `FakeSession` whose tools are `_prediction_tools()` with
        `write_predictions` replaced by `served_tool("write_predictions", _OPEN,
        allow_in=["*"], phase="intake")`, envelopes `(rows,)`, `(predicted,)`: `EngineError`
        match `"registry metadata is invalid"`.

c. `tests/test_api_inspect.py` after `test_validation_route`: helper
   `_prediction_state(seed: _Seed, *, champion: dict[str, JsonValue] | None) -> RunState`
   built like `_validation_state`: problem as there (classification); nodes
   `node("problem_definition"), node("data_splitting"), node("champion_selection")`; shared
   results problem (e1), `data_splitting` summary `{"test": seed.test_frame}` (e2) and, when
   `champion` is not None, `champion_selection` with that summary (e3); `cursor` 3 or 2;
   `RunState.initial(...)` without `candidates`, and `model_copy(update={"shared_results":
   ..., "cursor": ...})`. Test module imports to add: `json`; `ToolFailure` from
   `statlab_contracts.envelope`. `test_predictions_route(tmp_path, monkeypatch)`:
   1. POST with no checkpoint: 409 `{"detail": NO_CHAMPION}` (local constant `NO_CHAMPION =
      "this run has no champion model yet"`).
   2. State with `champion=None`: 409 same detail.
   3. State with `champion={"model": 1}`: 409 same detail.
   4. State with `champion={"model": model, "threshold": 0.4, "replay": [{"tool":
      "apply_imputer", "parameters": {"imputer": imputer}}]}` (`model`, `imputer` =
      `str(new_artifact_ref())`): POST gives 409 `{"detail": "the rows before the split are not
      recorded for this run"}` (only the seed's `load_csv` call exists).
   5. Record with `record_tool_call(conn, run_id=seed.run_id, node_execution_id=
      seed.execution_id, ...)` (open `connect(settings.db_path)` and close it): a failed
      `split_random` (`args_json=json.dumps({"frame": seed.frame})`, `result_json=
      ToolFailure(error_class="data", message="m").model_dump_json()`), and a successful
      `split_random` with `args_json=json.dumps({"frame": seed.frame})` and `result_json=
      ToolSuccess(artifacts=(ArtifactMeta(ref=parse_artifact_ref(seed.test_frame),
      kind="dataframe", split="test"),)).model_dump_json()`. Mint `rows`, `imputed`,
      `probas` refs; write `b"x"` to `paths.artifact(rows, "dataframe")`. Queue
      `session.responses` with `ToolSuccess(summary={"rows": 2}, artifacts=(ArtifactMeta(ref=
      rows, kind="dataframe"),))`, the same shape for `imputed` (dataframe) and `probas`
      (predictions), then `ToolSuccess(summary={"rows": 2, "task": "classification"})`. POST:
      200, JSON `{"rows": 2, "task": "classification"}`; `[name for name, _, _ in
      session.calls[-4:]] == ["unlabelled_rows", "apply_imputer", "predict_proba",
      "write_predictions"]`; the first of those has arguments `{"project": "demo", "run":
      seed.run_id, "frame": seed.frame, "target": "a"}`; the last has `"threshold": 0.4` and
      `"frame": str(rows)`; the `rows` file no longer exists.
   6. Queue `ToolFailure(error_class="data", message="the dataset has no rows without a target
      to predict")`: POST gives 422 `{"detail": "the dataset has no rows without a target to
      predict"}`.
   7. Queue `ToolFailure(error_class="transient", message="m")`: POST gives 503.
   8. State with `champion={"model": model, "replay": [{"tool": ""}]}`: POST gives 500.
   9. Write `b"a,b\n"` to `paths.root / "predictions.csv"`; `GET
      /api/runs/{seed.run_id}/files/predictions.csv` gives 200, `content-type` starting
      `text/csv`, body `"a,b\n"`.

### 5. Checks

`make test-fast TESTS="tests/test_tools_evaluation.py tests/test_mcp_client.py
tests/test_api_inspect.py tests/test_skills.py tests/test_tool_decorator.py"`;
`.venv/bin/python -m ruff check` and `.venv/bin/python -m ruff format --check` on the six owned
files; `.venv/bin/python -m mypy` on the six owned files. Stop and report on any failure,
unexpected value or spec gap.

Main then: review; end-to-end probe on scratch copies (MCP 8767, API 8766, a copy of
`projects/teszt` and a `.backup` snapshot of `statlab.db`; never the user's files or ports):
POST the friend's run `r_f5e9364ca69378fa` predictions, check rows/columns and the CSV;
`make check`; commit; then write 77d.

### Addendum 77c-A (lint/type corrections in `write_predictions`)

1. Step 3: declare `added: dict[str, list[str] | np.ndarray] = {}` (values are a list of class
   strings, or float64 arrays).
2. Step 5: replace `out = data.assign(**added)` with `out = data.copy()` followed by
   `for name, values in added.items(): out[name] = values`.
3. Step 6: replace `os.replace(temporary, paths.root / PREDICTIONS_CSV)` with
   `temporary.replace(paths.root / PREDICTIONS_CSV)`, and remove `import os`.
4. Tests: 4b step 7 uses `task="regression"` (accepted). 4b step 6 stays as written: the
   malformed-ref meta is appended by the first step, so the `finally` cleanup skips it before
   the replay guard's `EngineError` propagates; that is the intended coverage.
Rerun all subsection 5 checks.

## Stage 77c acceptance receipt (2026-10-10)

Implementer (after addendum A): focused selectors (tools evaluation, mcp client, api inspect,
skills, tool decorator) exit 0, 247 passed; Ruff check/format and mypy on the six owned files
exit 0. Main reviewed the full diff (matches the recipe and addendum).

End-to-end probe (main): scratch copy of `projects/teszt` plus a `.backup` snapshot of
`statlab.db` (other projects deleted from the snapshot, paths rewritten, the one
`awaiting_user` run cancelled so boot resumed nothing); MCP 8767, API 8766. POST
`/api/runs/r_f5e9364ca69378fa/predictions` gave 200 in about 1 s: classification, 17,952 rows
(the day-4 rows), columns `row`, the 17 kept dataset columns, `prediction`, `probability_0`,
`probability_1`; the CSV download gave 200 `text/csv`, 17,953 lines. No intermediate frames
left (artifact count 288 before and after). Every row was predicted `1` (p ≥ 0.9994): that
run's champion leans on the 7 columns empty on those rows (chunk 76), so its predictions are
meaningless, as expected. Scratch servers stopped, scratch directory deleted.

`make check` (`check77c.log`, temporary) exit 0: coverage 100 % (TOTAL 13637 stmts, 0
missed), mypy 212 files, Ruff clean.

## Stage 77d (detailed spec): run-page Predictions tab

Goal: on a run page, a "Predictions" tab calls 77c's `POST /api/runs/{run_id}/predictions`,
previews the first rows and links `GET /api/runs/{run_id}/files/predictions.csv` for download.
Non-goals: uploaded files (77e), paging beyond the 20-row head, a chat shortcut. Contract
editing of `features.lags` already works: the schema form renders it as a column→list map
(`SchemaForm.tsx` `MapControl`, `list-map`), and the backend coerces `"96"` to `96` (checked).

Owned files: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`,
`frontend/src/run/PredictionsView.tsx` (new), `frontend/src/pages/RunPage.tsx`,
`frontend/tests/client.test.ts`. Not alone: preserve others' edits.

1. `types.ts`, directly after `ValidationPage`:
   `export type PredictionPreview = { task: 'classification' | 'regression'; rows: number;
   columns: string[]; head: JsonObject[] };`
2. `client.ts`: add `PredictionPreview` to the `import type` list directly before
   `ValidationPage`. Directly after `getValidation`:
   - `export function parsePredictionPreview(value: unknown): PredictionPreview`: throw
     `invalidResponse()` unless `isJsonObject(value)`; throw unless `value.task` is
     `'classification'` or `'regression'`; return `{ task: value.task, rows:
     requiredNumber(value, 'rows'), columns: stringArray(value.columns), head:
     objectArray(value.head) }`.
   - `export async function predictUnlabelled(runId: string, signal?: AbortSignal):
     Promise<PredictionPreview>` returning `parsePredictionPreview(await
     request(\`${runPath(runId)}/predictions\`, { method: 'POST', signal }))`.
3. New `src/run/PredictionsView.tsx`, default export `PredictionsView({ runId, ready }: {
   runId: string; ready: boolean })`:
   - Imports: `useMutation` from `@tanstack/react-query`; `predictUnlabelled`, `runFileUrl`
     from `../api/client.ts`; `errorText` from `../ui/format.ts`; `cellText` from
     `./compare.ts`.
   - `const predict = useMutation({ mutationFn: () => predictUnlabelled(runId) });` (before
     any return).
   - If `!ready`: return `<p className="secondary-line">Predictions are available once the run
     has chosen its champion model.</p>`.
   - Otherwise `const d = predict.data;` and return a `<section className="validation"
     aria-label="Predictions">` containing, in order:
     a. `<p className="help">The champion model predicts every row of your dataset whose
        target is empty. Rows removed before the split (duplicates, rows with too many missing
        values) are not included.</p>`
     b. `<div className="validation-bar">` with a `<button type="button" className="primary"
        disabled={predict.isPending} onClick={() => predict.mutate()}>` whose text is
        `predict.isPending ? 'Predicting…' : 'Predict rows without a target'`, and, when `d`
        is defined, `<a className="button" href={runFileUrl(runId, 'predictions.csv')}
        download>Download CSV</a>`.
     c. When `predict.isError`: `<p role="alert">{errorText(predict.error)}</p>`.
     d. When `d` is defined: `<p className="secondary-line">` with text
        `` `${d.rows} rows predicted.` `` followed by, when `d.rows > d.head.length`, the text
        `` ` The first ${d.head.length} are shown; download the CSV for all of them.` ``; then
        `<div className="table-wrap"><table className="validation-table">` with
        `<caption>Predicted rows</caption>`, a `<thead><tr>` of `<th scope="col"
        key={c}>{c}</th>` for each `c` in `d.columns`, and a `<tbody>` with one `<tr
        key={cellText(r.row ?? null)}>` per `r` in `d.head`, holding `<td key={c}>{cellText(r[c]
        ?? null)}</td>` for each `c` in `d.columns`.
4. `RunPage.tsx`:
   - Import `Sparkles` from `lucide-react` (add to the existing import, alphabetical) and
     `PredictionsView from '../run/PredictionsView.tsx'` directly after the `ValidationView`
     import.
   - `tab` state union gains `'predictions'`.
   - `tabs` gains, last: `{ id: 'predictions' as const, label: 'Predictions', Icon: Sparkles as
     LucideIcon }`.
   - Panel, after the validation line: `{tab === 'predictions' && <PredictionsView
     runId={runId} ready={top !== null} />}`.
5. `tests/client.test.ts`: import `predictUnlabelled` and `parsePredictionPreview`. After the
   `parseValidationPage` test add:
   - `test('predictUnlabelled POSTs and parses the preview', ...)`: body `{ task:
     'classification', rows: 3, columns: ['row', 'x', 'prediction'], head: [{ row: 4, x: 1,
     prediction: 'a' }] }`; inside `withFetch`: `const preview = await
     predictUnlabelled('r1');` assert `requests[0]?.input === '/api/runs/r1/predictions'`,
     `requests[0]?.init?.method === 'POST'`, `new Headers(requests[0]?.init?.headers).get(
     'X-Statlab-Csrf') === '1'`, and `assert.deepEqual(preview, body)`.
   - `test('parsePredictionPreview rejects a bad task, columns and head', ...)`: `const
     preview = { task: 'regression', rows: 0, columns: [], head: [] };` assert
     `parsePredictionPreview(preview).task === 'regression'`; `assert.throws(() =>
     parsePredictionPreview({ ...preview, task: 'other' }), ApiError)`; same for `columns:
     [1]`, `head: ['x']` and `rows: 'many'`.

Checks (from `frontend/`): `npm run typecheck`, `npm test`, `npm run build`. Stop and report on
any failure or gap. Main then: review, browser check on scratch ports (preview a predictions
tab against a scratch copy), `make check` not needed for frontend-only edits beyond these
(frontend gates are `typecheck`/`test`/`build`), commit, then write 77e.

## Stage 77d acceptance receipt (2026-10-10)

Implementer: `npm run typecheck`, `npm test` (115 pass, 0 fail), `npm run build` exit 0. Main
reviewed the diff (matches the recipe; the result block sits in a fragment, mechanical) and
reran the three frontend gates: exit 0, 115/115. Browser (scratch copy as in 77c, vite 5174, API
8766, MCP 8767): signed in, opened run `r_f5e9364ca69378fa`, Predictions tab → "Predict rows
without a target" showed "17952 rows predicted. The first 20 are shown; download the CSV for all
of them.", the 20-row table and Download CSV (`.playwright-mcp/77d-predictions-tab.png`). Only
console errors were pre-login (favicon 404, `/api/auth/me` 401). Scratch servers stopped and
directory deleted. Python is unchanged, so 77c's `make check` evidence holds.

## Stage 77e (detailed spec): backend predictions for an uploaded file

Goal: `POST /api/runs/{run_id}/predictions/upload` (multipart, one `file` part, `.csv`) predicts
every row of the uploaded file with the champion and writes `<run>/uploaded_predictions.csv`
(downloadable via the run-file route). Works for any run 77c works for, provided its
preparation chain is replayable (below).

Design (main):
- Preparation chain: walk producers back from the split source (77c `_split_source`): each
  frame was produced by a logged call whose `frame` argument is the previous frame, ending at the
  `load_csv` output (the raw table). Observed chains (user DB): `load_csv` → [`cast_dtypes`] →
  [`add_lag_features`] → [`drop_duplicate_rows`] → [`drop_high_missing_rows`] →
  [`drop_columns`] → `split_*` (`foundation.py` ~680-790). Row filters (`drop_duplicate_rows`,
  `drop_high_missing_rows`) are skipped so no uploaded row disappears; `cast_dtypes`,
  `add_lag_features`, `drop_columns` are replayed with their logged arguments. Any other tool in
  the chain → 409.
- Combined table: raw rows + uploaded rows (labels continue after the raw labels, target
  empty), read with the run's logged `load_csv` options. Uploaded columns missing from the
  dataset become empty (reported); extra columns and an uploaded target column are ignored
  (reported). String columns keep the dataset's string dtype. Lags over the combined table read
  the dataset's history for the uploaded rows.
- The uploaded rows (labels ≥ first uploaded label) are selected by `unlabelled_rows` with a
  new `from_row`, relabelled from 0 (the file's own row order), then 77c's replay, prediction and
  write run unchanged except the output file name.
- The upload is stored as `<run>/upload_<32 hex>.csv` for the request only and deleted
  afterwards (concurrent uploads never share a file).

Owned files: `mcp_server/statlab_tools/tools/intake.py`,
`mcp_server/statlab_tools/tools/evaluation.py`, `backend/statlab/mcp_client.py`,
`backend/statlab/api/inspect.py`, `tests/test_tools_intake_load.py`,
`tests/test_tools_evaluation.py`, `tests/test_mcp_client.py`, `tests/test_api_inspect.py`. Not
alone: preserve others' edits.

### 1. `intake.py`: new tool after `load_csv`

`UPLOAD_PATTERN: Final = r"^[0-9a-f]{32}\z"` (module constant next to the tool).

`@tool(phase="artifacts", allow_in=["*"])`
`def append_upload_rows(paths: RunPaths, raw: Annotated[ArtifactRef, Field(description="The
run's raw table from load_csv.")], upload: Annotated[str, Field(pattern=UPLOAD_PATTERN,
description="The uploaded file's token; the file is upload_<token>.csv in the run folder.")],
target: Annotated[str, Field(min_length=1, description="The target column.")], options:
Annotated[DatasetOptions, Field(description="The run's CSV parsing options.")] = _OPTS) ->
ToolSuccess`
Docstring: `"""Append an uploaded file's rows after the raw table's rows, with an empty target,
so the run's preparation can be replayed on them (chunk 77). Columns the file lacks become
empty; extra columns and a target column in the file are ignored."""`
Body, in order:
1. `data = load_dataframe(paths.artifact(raw, "dataframe"))`.
2. `new = _read_csv(paths.root / f"upload_{upload}.csv", options)`.
3. If `new.empty`: raise `ToolDataError("the uploaded file has no rows")`.
4. `columns = [column for column in data.columns if column != target]`;
   `missing = [str(column) for column in columns if column not in new.columns]`;
   `ignored = [str(column) for column in new.columns if column not in columns]`.
5. If `len(missing) == len(columns)`: raise `ToolDataError("the uploaded file shares no
   columns with the dataset; check its delimiter")`.
6. `start = int(data.index.max()) + 1`; `rows = new.reindex(columns=columns)`;
   `rows.index = pd.RangeIndex(start, start + len(rows))`.
7. `combined = pd.concat([data, rows])`. Then for `column in data.columns`: if
   `isinstance(data[column].dtype, pd.StringDtype) and combined[column].dtype !=
   data[column].dtype`: `combined[column] = combined[column].astype(data[column].dtype)`.
8. Return `ToolSuccess(summary={"rows": len(rows), "first_row": start, "missing": missing,
   "ignored": ignored}, artifacts=(save_frame(paths, combined, "with_upload"),))` (import
   `save_frame` from `statlab_tools.tools._frame` if not imported yet; `missing`/`ignored` are
   `list[str]`, annotate `JsonValue` lists as needed for mypy).

### 2. `evaluation.py`

a. `unlabelled_rows` gains, after `target`: `from_row: Annotated[int | None, Field(ge=0,
   description="Predict the rows labelled from_row onwards (an uploaded file) instead of the
   rows without a target.")] = None`. Body step 3 becomes: if `from_row is None`: as now;
   else `rows = data.loc[data.index >= from_row].drop(columns=[target])` and
   `rows.index = rows.index - from_row`. Step 4's message becomes `"the dataset has no rows
   without a target to predict" if from_row is None else "the uploaded file has no rows to
   predict"`. Docstring gains the sentence `With from_row, the rows labelled from_row onwards
   instead, relabelled from 0.`
b. `write_predictions` gains, last: `file: Annotated[Literal["predictions.csv",
   "uploaded_predictions.csv"], Field(description="The output file in the run folder.")] =
   "predictions.csv"`. Use `file` wherever `PREDICTIONS_CSV` is used (temporary name and
   target); delete the `PREDICTIONS_CSV` constant. The summary gains `"file": file` (last key).
   Docstring: replace `write predictions.csv` with `write file`.

### 3. `mcp_client.py`

a. Module level, after `CallContext`:
   - `PREPARATION_TOOLS: Final = frozenset({"cast_dtypes", "add_lag_features",
     "drop_columns"})` (add `Final` to the `typing` import).
   - `@dataclass(frozen=True, slots=True) class PredictionUpload:` docstring `"""An uploaded
     file and the run's preparation to replay on it before predicting (chunk 77)."""`, fields
     `token: str`, `raw: str`, `options: dict[str, JsonValue]`, `steps: tuple[tuple[str,
     dict[str, JsonValue]], ...]`.
b. `_prediction_spec(self, tool: str, *, kind: Literal["fixed", "replayed", "prepared"])`:
   `"replayed"` keeps today's `replayed=True` rule; `"fixed"` keeps today's `replayed=False`
   rule; `"prepared"`: if `spec is None or tool not in PREPARATION_TOOLS or spec.heavy`: raise
   `EngineError(f"{tool} cannot prepare uploaded rows")`.
c. `_prediction_step(..., *, kind: Literal["fixed", "replayed", "prepared"]) -> tuple[str,
   dict[str, JsonValue]]`: same body, passes `kind`, returns
   `(str(result.artifacts[0].ref), result.summary)`. Update the existing call sites
   (`replayed=False` → `kind="fixed"`, `replayed=True` → `kind="replayed"`, unpack `ref, _`).
d. `predict_unlabelled` gains the last keyword parameter `upload: PredictionUpload | None =
   None`; docstring gains `With upload, the uploaded rows instead (written to
   uploaded_predictions.csv).` Inside `try`, before the `unlabelled_rows` step:
   - `source = frame`; `rows_arguments: dict[str, JsonValue] = {"project": project, "run": run,
     "frame": frame, "target": target}`; `notes: dict[str, JsonValue] = {}`.
   - If `upload is not None`:
     1. `source, appended = await self._prediction_step("append_upload_rows", {"project":
        project, "run": run, "raw": upload.raw, "upload": upload.token, "target": target,
        "options": upload.options}, made, kind="fixed")`.
     2. `first_row = appended.get("first_row")`; if `isinstance(first_row, bool) or not
        isinstance(first_row, int)`: raise `ToolCallError("append_upload_rows",
        Failure(error_class="bug", message="append_upload_rows returned no first_row"))`.
     3. For `tool, parameters in upload.steps`: `source, _ = await
        self._prediction_step(tool, {**parameters, "project": project, "run": run, "frame":
        source}, made, kind="prepared")`.
     4. `rows_arguments.update(frame=source, from_row=first_row)`;
        `notes = {"missing": appended.get("missing", []), "ignored": appended.get("ignored",
        [])}`.
   - The `unlabelled_rows` step uses `rows_arguments`.
   - After `if threshold is not None: ...`: if `upload is not None`:
     `arguments["file"] = "uploaded_predictions.csv"`.
   - The final `return written` becomes `return written.model_copy(update={"summary":
     {**written.summary, **notes}})`.

### 4. `inspect.py`

a. Imports: `secrets`, `tempfile`, `from pathlib import Path`, `from fastapi import Request`
   (extend the fastapi import), `from statlab.api.projects import bounded_file` (extend),
   `from statlab.mcp_client import PREPARATION_TOOLS, PredictionUpload`, `from typing import
   Final` (extend), `dataclass` from `dataclasses`.
b. `RUN_FILES` gains `"uploaded_predictions.csv": (("uploaded_predictions.csv",), "text/csv;
   charset=utf-8")` (last). Constants: `NO_PREPARATION = "this run's data preparation cannot
   be repeated on new rows"`, `ROW_FILTERS: Final = frozenset({"drop_duplicate_rows",
   "drop_high_missing_rows"})`, `LOAD_OPTIONS: Final = ("delimiter", "encoding", "decimal",
   "na_values", "header_row")`, `_CHUNK: Final = 64 * 1024`.
c. Replace `_split_source` with two functions:
   - `def _producer(conn: sqlite3.Connection, run_id: str, ref: str) -> tuple[str, dict[str,
     JsonValue]] | None:` docstring `"""The tool and arguments of the logged call that produced
     ref, in the run or a fork ancestor (chunk 77)."""`. For `ancestor in run_ancestry(conn,
     run_id)`: for `call in list_tool_calls(conn, ancestor.id)`: `if ref not in
     call.result_json: continue`; `result = TOOL_RESULT_ADAPTER.validate_json(
     call.result_json)`; if `isinstance(result, ToolSuccess) and any(str(meta.ref) == ref for
     meta in result.artifacts)`: `arguments = json.loads(call.args_json)`; return
     `(call.tool_name, arguments if isinstance(arguments, dict) else {})`. After the loops
     return `None`.
   - `def _split_source(conn, run_id, test) -> str | None:` `found = _producer(conn, run_id,
     test)`; if `found is None or not found[0].startswith("split_")`: return `None`;
     `source = found[1].get("frame")`; return `source if isinstance(source, str) else None`.
d. New function `def _preparation(conn: sqlite3.Connection, run_id: str, source: str) ->
   tuple[str, dict[str, JsonValue], tuple[tuple[str, dict[str, JsonValue]], ...]] | None:`
   docstring `"""The raw table, its load_csv options and the replayable steps from it to the
   split source, row filters left out; None when the chain cannot be replayed (chunk 77)."""`.
   `steps: list[tuple[str, dict[str, JsonValue]]] = []`; `current = source`; loop `for _ in
   range(20):` `found = _producer(conn, run_id, current)`; if `None`: return `None`;
   `tool, arguments = found`; if `tool == "load_csv"`: return `(current, {key:
   arguments[key] for key in LOAD_OPTIONS if key in arguments}, tuple(reversed(steps)))`;
   `previous = arguments.get("frame")`; if `not isinstance(previous, str) or tool not in
   PREPARATION_TOOLS | ROW_FILTERS`: return `None`; if `tool in PREPARATION_TOOLS`:
   `steps.append((tool, {key: value for key, value in arguments.items() if key not in
   ("project", "run", "frame")}))`; `current = previous`. After the loop return `None`.
e. Refactor the 77c route body (no behavior change) into:
   - `@dataclass(frozen=True, slots=True) class _Champion:` fields `target: str`, `task:
     Literal["classification", "regression"]` (import `Literal`), `model: str`, `threshold:
     float | None`, `replay: tuple[tuple[str, dict[str, JsonValue]], ...]`, `source: str`.
   - `def _champion(resource: APIResources, run: RunRow, paths: RunPaths) -> _Champion`: steps
     1-6 of the 77c route (same HTTP errors, same order), returning the dataclass
     (`threshold` already narrowed to `float | None`, `replay` as the tuple passed today).
   - `async def _predict(resource: APIResources, run: RunRow, paths: RunPaths, champion:
     _Champion, upload: PredictionUpload | None) -> dict[str, JsonValue]`: step 7 (the
     `predict_unlabelled` call with `upload=upload` and the same `ToolCallError` mapping),
     returning `result.summary`.
   - The 77c route becomes: `run, paths = _run(resource, run_id)`; `return await
     _predict(resource, run, paths, _champion(resource, run, paths), None)`.
f. New route after it: `@router.post("/api/runs/{run_id}/predictions/upload")` `async def
   predict_upload(run_id: str, request: Request) -> dict[str, JsonValue]:`
   1. `resource = resources()`; `run, paths = _run(resource, run_id)`; `champion =
      _champion(resource, run, paths)`.
   2. `preparation = _preparation(resource.conn, run.id, champion.source)`; if `None`: raise
      `HTTPException(409, NO_PREPARATION)`. `raw, options, steps = preparation`.
   3. `token = secrets.token_hex(16)`; `stored = paths.root / f"upload_{token}.csv"`;
      `limit = min(resource.settings.max_upload_mb, 200) * 1024 * 1024`; `size = 0`.
   4. `try:` `async with bounded_file(request, limit=limit) as uploaded:` `name =
      uploaded.filename or ""`; if `Path(name).suffix.lower() != ".csv"`: raise
      `HTTPException(422, "CSV filename must end in .csv")`; `with stored.open("wb") as
      handle:` `while chunk := await uploaded.read(_CHUNK):` `size += len(chunk)`; if
      `size > limit`: raise `HTTPException(413, "upload exceeds limit")`;
      `handle.write(chunk)`. After the `async with`: if `size == 0`: raise
      `HTTPException(422, "uploaded file is empty")`. Then `return await _predict(resource,
      run, paths, champion, PredictionUpload(token=token, raw=raw, options=options,
      steps=steps))`. `except OSError: raise HTTPException(500, "could not store the uploaded
      file") from None` `finally: stored.unlink(missing_ok=True)`.
   (`tempfile` is not needed after all: do not import it.)

### 5. Tests

a. `tests/test_tools_intake_load.py`: import `append_upload_rows` and `save_dataframe`,
   `new_artifact_ref`. `test_append_upload_rows(run_paths)`:
   - `ref = new_artifact_ref()`; `save_dataframe(run_paths.artifact(ref, "dataframe"),
     pd.DataFrame({"t": [1, 2], "name": pd.array(["a", "b"], dtype="str"), "y": [0.0, 1.0]}),
     ref=ref, label="raw")`; `token = "0" * 32`; write `"t,extra,y\n3,z,5\n4,w,\n"` to
     `run_paths.root / f"upload_{token}.csv"`.
   - `res = append_upload_rows(run_paths, raw=ref, upload=token, target="y")`: assert
     `res.summary == {"rows": 2, "first_row": 2, "missing": ["name"], "ignored": ["extra",
     "y"]}`; `combined = load_dataframe(run_paths.artifact(res.artifacts[0].ref,
     "dataframe"))`: `list(combined.index) == [0, 1, 2, 3]`, `list(combined["t"]) == [1, 2, 3,
     4]`, `combined["y"].isna().tolist() == [False, False, True, True]`,
     `isinstance(combined["name"].dtype, pd.StringDtype)`.
   - Refusals (`pytest.raises(ToolDataError)`): a header-only upload `"t,name\n"` (no rows);
     an upload `"a;b\n1;2\n"` (shares no columns); a token whose file does not exist
     (`"1" * 32`).
b. `tests/test_tools_evaluation.py`:
   - `test_unlabelled_rows`: add `uploaded = unlabelled_rows(run_paths, frame=frame,
     target="y", from_row=12)`; its saved frame has index `[0, 1]` and columns `["x"]`; and
     `pytest.raises(ToolDataError)` for `from_row=99`.
   - `test_write_predictions_regression`: the expected summary gains `"file":
     "predictions.csv"`; then `again = write_predictions(run_paths, frame=frame,
     predictions=preds, task="regression", file="uploaded_predictions.csv")`:
     `again.summary["file"] == "uploaded_predictions.csv"` and `(run_paths.root /
     "uploaded_predictions.csv").exists()`.
c. `tests/test_mcp_client.py`: `_prediction_tools()` gains
   `served_tool("append_upload_rows", _OPEN, allow_in=["*"], phase="artifacts")`,
   `served_tool("cast_dtypes", _OPEN, allow_in=["data_profiling"], phase="cleaning")`,
   `served_tool("split_random", _OPEN, allow_in=["data_splitting"], phase="splitting")`.
   New `test_predict_unlabelled_upload_prepares_and_reports(world, tmp_path)`:
   - Envelopes in order: `ToolSuccess(summary={"first_row": 5, "missing": ["m"], "ignored":
     ["i"]}, artifacts=(combined,))`, `(typed,)`, `(rows,)`, `(predicted,)` (each `_made`;
     `predicted` kind predictions, others dataframe), then `ToolSuccess(summary={"rows":
     1})`. Call with `task="regression"`, `threshold=None`, `replay=()`, `frame="a_" + "1" *
     16`, `model="a_" + "2" * 16`, `upload=PredictionUpload(token="0" * 32, raw="a_" + "4" *
     16, options={"delimiter": ";"}, steps=(("cast_dtypes", {"target": "y"}),))`.
   - Assert `result.summary == {"rows": 1, "missing": ["m"], "ignored": ["i"]}`; call names
     `["append_upload_rows", "cast_dtypes", "unlabelled_rows", "predict",
     "write_predictions"]`; append arguments `{"project": paths.slug, "run": paths.run_id,
     "raw": "a_" + "4" * 16, "upload": "0" * 32, "target": "y", "options": {"delimiter":
     ";"}}`; cast arguments `{"target": "y", "project": paths.slug, "run": paths.run_id,
     "frame": str(combined.ref)}`; unlabelled arguments `{"project": paths.slug, "run":
     paths.run_id, "frame": str(typed.ref), "target": "y", "from_row": 5}`; write arguments
     include `"file": "uploaded_predictions.csv"` and `"frame": str(rows.ref)`; none of the
     four made files exists afterwards.
   - Guards, each with a fresh session and the same upload unless noted: (1) append envelope
     `ToolSuccess(summary={}, artifacts=(_made(paths, "dataframe"),))` → `ToolCallError`
     (no first_row); (2) `first_row` `True` → `ToolCallError`; (3) upload steps
     `(("split_random", {}),)` with a valid append envelope → `EngineError` match `"cannot
     prepare uploaded rows"`.
d. `tests/test_api_inspect.py`:
   - In `test_predictions_route`, the failed `split_random` row's message becomes
     `f"split failed near {seed.test_frame}"` (so the producer search parses and skips a
     failure that mentions the ref). Nothing else in that test changes.
   - New `test_predictions_upload_route(tmp_path, monkeypatch)` with the same champion state as
     step 4 of `test_predictions_route` (classification, threshold 0.4, one `apply_imputer`
     step). `url = f"/api/runs/{seed.run_id}/predictions/upload"`; `upload = {"file":
     ("new.csv", b"b\n1\n", "text/csv")}`. Helper `record(tool, arguments, outputs)` inside
     the test opens `connect(settings.db_path)`, records one successful call (`ToolSuccess(
     artifacts=tuple(ArtifactMeta(ref=parse_artifact_ref(ref), kind="dataframe") for ref in
     outputs))`), closes; `outputs` is a list of `str` refs. Refs `raw`, `typed`, `combined`, `typed2`,
     `rows`, `imputed`, `probas` are `new_artifact_ref()` values (`str(...)` in JSON arguments,
     `record` outputs and assertions; the `ArtifactRef` itself in `ArtifactMeta`). `seed.frame`
     is the split source, `seed.test_frame` the test frame.
     1. No calls recorded yet: POST gives 409 `{"detail": "the rows before the split are not
        recorded for this run"}`.
     2. `record("split_random", {"frame": seed.frame}, [seed.test_frame])` and
        `record("drop_duplicate_rows", {"frame": typed, "subset": ["b"]}, [seed.frame])`:
        POST gives 409 `{"detail": "this run's data preparation cannot be repeated on new
        rows"}` (no producer for `typed`).
     3. `record("cast_dtypes", {"frame": raw, "target": "a", "project": "demo", "run":
        seed.run_id}, [typed])`: POST gives the same 409 (no producer for `raw`).
     4. `record("load_csv", {"delimiter": ";", "dataset": "x", "project": "demo", "run":
        seed.run_id}, [raw])`.
     5. Queue responses: append `ToolSuccess(summary={"first_row": 7, "missing": [],
        "ignored": []}, artifacts=(ArtifactMeta(ref=combined, kind="dataframe"),))`, cast →
        `typed2`, unlabelled → `rows`, imputer → `imputed`, predict_proba → `probas` (kinds
        dataframe except `probas` predictions), write → `ToolSuccess(summary={"rows": 1,
        "file": "uploaded_predictions.csv"})`. POST with `files=upload`: 200, JSON `{"rows":
        1, "file": "uploaded_predictions.csv", "missing": [], "ignored": []}`. The last six
        call names are `["append_upload_rows", "cast_dtypes", "unlabelled_rows",
        "apply_imputer", "predict_proba", "write_predictions"]`; the append arguments'
        `"options" == {"delimiter": ";"}` and `"raw" == raw`; its `"upload"` token is 32 hex
        characters and `paths.root / f"upload_{token}.csv"` no longer exists; the cast
        arguments equal `{"target": "a", "project": "demo", "run": seed.run_id, "frame":
        str(combined)}` (the `drop_duplicate_rows` row filter is not replayed).
     6. Upload errors: `files={"file": ("new.txt", b"b\n1\n", "text/plain")}` → 422; an empty
        `("new.csv", b"", "text/csv")` → 422 `"uploaded file is empty"`; and no
        `upload_*.csv` file is left in `paths.root` (`list(paths.root.glob("upload_*.csv")) ==
        []`).
     7. `GET /api/runs/{seed.run_id}/files/uploaded_predictions.csv` after writing `b"r\n"` to
        that file: 200 `text/csv`.
   - New `test_preparation_refusals(tmp_path, monkeypatch)` (import `_preparation` from
     `statlab.api.inspect`; same `_open_project_api` + `_seed` setup, then `conn =
     connect(settings.db_path)`, closed in `finally`; record calls with `record_tool_call` as in
     `test_predictions_route`): (1) `other = str(new_artifact_ref())`; record
     `detect_outliers` with args `{"frame": other}` producing `seed.frame`; assert
     `_preparation(conn, seed.run_id, seed.frame) is None`. (2) `loop = str(new_artifact_ref())`;
     record `drop_columns` with args `{"frame": loop, "columns": ["b"]}` producing `loop`; assert
     `_preparation(conn, seed.run_id, loop) is None` (the 20-step limit ends the cycle).

### 6. Checks

`make test-fast TESTS="tests/test_tools_intake_load.py tests/test_tools_evaluation.py
tests/test_mcp_client.py tests/test_api_inspect.py tests/test_skills.py
tests/test_tool_decorator.py"` (counts via `.venv/bin/python -m pytest -p no:cacheprovider -o
addopts="" -q <same selectors>`); Ruff check, Ruff format --check and mypy on the eight owned
files. Stop and report on any failure or gap.

Main then: review; scratch end-to-end probe (upload a few rows of the friend's dataset to its
run copy, and a regression run); `make check`; commit; then write 77f.

### Addendum 77e-A (no-shared-columns test input)

`_read_csv` already refuses a one-column parse whose header contains another delimiter
(`ToolParamError`), so 5a's `"a;b\n1;2\n"` never reaches the no-shared-columns check. In
`test_append_upload_rows` replace that upload with `"p,q\n1,2\n"` (still
`pytest.raises(ToolDataError)`). The served-tool placement before `write_predictions` is
accepted. Rerun all subsection 6 checks.

### Addendum 77e-B (coverage: two dead branches, one missing test)

`make check` (main): 99 %, uncovered `intake.py` 339 and `inspect.py` 372, 384.
1. `intake.py` `append_upload_rows`: delete step 3 (`if new.empty: raise ...`). `_read_csv`
   already refuses a file without data rows ("file has zero data rows", `ToolDataError`), so it
   is unreachable; the header-only test case still expects `ToolDataError`.
2. `inspect.py` `predict_upload`: delete the `except OSError:` clause (keep `try`/`finally`).
   The app's `OSError` handler already answers 500 `internal error`; the `finally` still deletes
   the stored upload.
3. `tests/test_api_inspect.py`: new `test_predictions_upload_size_limit(tmp_path,
   monkeypatch)`: `_open_project_api(tmp_path, monkeypatch, max_upload_mb=1)`; `_seed`; write
   the same champion state as `test_predictions_upload_route`; record (as there)
   `split_random` with args `{"frame": seed.frame}` producing `seed.test_frame`, and `load_csv`
   with args `{}` producing `seed.frame`. POST `files={"file": ("big.csv", b"a\n" + b"x" *
   (1024 * 1024 - 1), "text/csv")}` (1 MiB + 1 byte; file parts pass the parser's part limit
   and reach the route's own check): status 413, `{"detail": "upload exceeds limit"}`, and
   `list(paths.root.glob("upload_*.csv")) == []`.
Rerun all subsection 6 checks.

## Stage 77e acceptance receipt (2026-10-10)

Implementer (after addenda A, B): focused selectors (intake load, evaluation, mcp client, api
inspect, skills, tool decorator) exit 0, 296 passed; Ruff check/format and mypy on the eight
owned files exit 0. Accepted deviations: served test tools placed before `write_predictions`;
one description string wrapped for E501. Main reviewed the full diff (matches the recipe and
addenda).

Probe (main; scratch copies of `teszt` and `regression-test` plus a `.backup` DB snapshot,
other projects deleted, paths rewritten, `awaiting_user` runs cancelled; MCP 8767, API 8766):
- `r_f5e9364ca69378fa` (classification, chain `load_csv` → `drop_duplicate_rows` →
  `drop_columns`): upload of 5 day-4 rows without `target_flag`, `target_reg`, `ke` plus an
  extra `note` column → 200, 5 rows, `missing` `["ke", "target_reg"]`, `ignored` `["note"]`,
  file `uploaded_predictions.csv`, rows relabelled 0-4.
- `r_922ffb37436a8f84` (regression, chain with `cast_dtypes`): first 5 dataset rows without
  `exam_score` → 200, predictions 89.9 / 87.0 / 77.3 / 78.2 / 53.4 against actual 90.4 / 86.2 /
  76.1 / 79.7 / 50.7; CSV download 200.
- No `upload_*.csv` left; artifact count unchanged (288). Servers stopped, scratch deleted.

`make check` (`check77e2.log`, temporary) exit 0: coverage 100 % (TOTAL 13754 stmts, 0
missed), mypy 212 files, Ruff clean.

## Stage 77f (detailed spec): upload a file in the Predictions tab

Goal: the run page's Predictions tab also predicts an uploaded CSV through 77e's
`POST /api/runs/{run_id}/predictions/upload` and links the summary's `file` for download, with
the `missing`/`ignored` column notes. Non-goals: drag and drop, upload progress.

Owned files: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`,
`frontend/src/run/PredictionsView.tsx`, `frontend/tests/client.test.ts`. Not alone: preserve
others' edits.

1. `types.ts` `PredictionPreview` gains `file: string; missing: string[]; ignored: string[];`
   (after `head`).
2. `client.ts`:
   - `parsePredictionPreview` returns additionally `file: requiredString(value, 'file')`,
     `missing: value.missing === undefined ? [] : stringArray(value.missing)`, `ignored:
     value.ignored === undefined ? [] : stringArray(value.ignored)`.
   - After `predictUnlabelled`: `export async function predictUpload(runId: string, file:
     Blob, filename: string, signal?: AbortSignal): Promise<PredictionPreview>`: `const form =
     new FormData(); form.append('file', file, filename);` return
     `parsePredictionPreview(await request(\`${runPath(runId)}/predictions/upload\`, { method:
     'POST', form, signal }))`.
3. `PredictionsView.tsx` (rewrite the file; keep the default export name and props):
   - Imports: `useMutation` from `@tanstack/react-query`; `useState` from `react`;
     `predictUnlabelled`, `predictUpload`, `runFileUrl` from `../api/client.ts`; `type
     PredictionPreview` from `../api/types.ts`; `errorText` from `../ui/format.ts`; `cellText`
     from `./compare.ts`.
   - Local component `function Preview({ runId, preview }: { runId: string; preview:
     PredictionPreview })` returning a fragment with, in order: the `<p
     className="secondary-line">` row-count sentence exactly as 77d (using `preview`); when
     `preview.missing.length > 0`: `<p className="help">{\`Not in your file, treated as empty:
     ${preview.missing.join(', ')}.\`}</p>`; when `preview.ignored.length > 0`: `<p
     className="help">{\`Not used by this run, ignored: ${preview.ignored.join(', ')}.\`}</p>`;
     `<p><a className="button" href={runFileUrl(runId, preview.file)} download>Download
     CSV</a></p>`; the 77d `table-wrap` table (caption `Predicted rows`, same header and body).
   - `PredictionsView({ runId, ready })`: hooks first: `const predict = useMutation({
     mutationFn: () => predictUnlabelled(runId) });` `const [file, setFile] = useState<File |
     null>(null);` `const upload = useMutation({ mutationFn: (chosen: File) =>
     predictUpload(runId, chosen, chosen.name) });`. Then the 77d `!ready` paragraph. Otherwise
     a `<section className="validation" aria-label="Predictions">` containing, in order:
     a. The 77d help paragraph.
     b. `<div className="validation-bar">` with only the 77d predict button (the Download link
        moves into `Preview`).
     c. `predict.isError` → `<p role="alert">{errorText(predict.error)}</p>`.
     d. `predict.data !== undefined` → `<Preview runId={runId} preview={predict.data} />`.
     e. `<h3>Predict a new file</h3>` and `<p className="help">Upload a CSV with the
        dataset's columns; the target can be left out. Its rows get the same preparation as the
        dataset (placed after the dataset's rows, so lag features see its history), then the
        champion predicts them.</p>`.
     f. `<div className="validation-bar">` with `<input type="file" accept=".csv,text/csv"
        aria-label="CSV file to predict" onChange={(event) => setFile(event.target.files?.[0]
        ?? null)} />` and `<button type="button" className="primary" disabled={file === null ||
        upload.isPending} onClick={() => { if (file !== null) upload.mutate(file); }}>` with
        text `upload.isPending ? 'Predicting…' : 'Predict this file'`.
     g. `upload.isError` → `<p role="alert">{errorText(upload.error)}</p>`.
     h. `upload.data !== undefined` → `<Preview runId={runId} preview={upload.data} />`.
4. `tests/client.test.ts`: import `predictUpload`.
   - `predictUnlabelled POSTs and parses the preview`: body gains `file: 'predictions.csv'`;
     the final assertion becomes `assert.deepEqual(preview, { ...body, missing: [], ignored: []
     })`.
   - `parsePredictionPreview rejects ...`: `preview` gains `file: 'predictions.csv'`; add
     `assert.deepEqual(parsePredictionPreview({ ...preview, missing: ['m'], ignored: ['i']
     }).missing, ['m'])` and throws (`ApiError`) for `{ ...preview, file: 1 }`, `{ ...preview,
     missing: [1] }` and `{ ...preview, ignored: 'x' }`.
   - New `test('predictUpload POSTs the file as multipart', ...)`: body `{ task: 'regression',
     rows: 1, columns: ['row', 'prediction'], head: [{ row: 0, prediction: 1.5 }], file:
     'uploaded_predictions.csv', missing: ['m'], ignored: [] }`; inside `withFetch`: `const
     preview = await predictUpload('r1', new Blob(['a\n1\n']), 'new.csv');` assert
     `requests[0]?.input === '/api/runs/r1/predictions/upload'`, method `'POST'`, `const sent
     = requests[0]?.init?.body; assert.ok(sent instanceof FormData); const part =
     sent.get('file'); assert.ok(part instanceof File); assert.equal(part.name, 'new.csv');`
     and `assert.deepEqual(preview.missing, ['m'])`, `assert.equal(preview.file,
     'uploaded_predictions.csv')`.

Checks (from `frontend/`): `npm run typecheck`, `npm test`, `npm run build`. Stop and report on
any failure or gap. Main then: review, browser check on scratch servers (upload a small CSV),
commit, close chunk 77.

## Stage 77f acceptance receipt (2026-10-10)

Implementer: `npm run typecheck`, `npm test` (116 pass, 0 fail), `npm run build` exit 0.
Accepted deviations: `&apos;` in the help text; `predict.data` used directly. Main reviewed the
diff (matches the recipe) and reran the three gates: exit 0, 116/116. Browser (scratch copy of
`regression-test`, vite 5174, API 8766, MCP 8767): run `r_922ffb37436a8f84` → Predictions →
chose a 3-row CSV without `exam_score` and `age` plus an `extra` column → "Predict this file"
showed "3 rows predicted.", "Not in your file, treated as empty: age.", "Not used by this run,
ignored: extra.", Download CSV and the table (`.playwright-mcp/77f-upload.png`). Scratch
servers stopped, scratch data deleted. Python unchanged since 77e's `make check`.

## Chunk 77 completion (2026-10-10)

All six stages accepted: 77a `8b333d5`, 77b `f6b627e`, 77c `b4859ea`, 77d `9ac3197`, 77e
`eab7d58`, 77f (this commit). How to use it on run `r_f5e9364ca69378fa`'s data: contract
`split.strategy: "temporal"`, `split.time_column: "rowID"`, `features.exclude` the seven
measured-afterwards columns (`ke`, `hupx`, `afrr`, `afrr_fel`, `afrr_le`, `mfrr_fel`,
`mfrr_le`) and `target_reg`, and `features.lags` such as `{"ke": [96], "target_reg": [96]}`;
after the run, Predictions → "Predict rows without a target".
