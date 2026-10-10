---
status: in_progress
dependencies: [76]
completion_date: null
implementation_notes: "77a temporal split + time-ordered CV; 77b lag features; 77c predictions on unlabelled rows; 77d predictions on an uploaded file"
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
- **77c** predictions for the dataset's unlabelled rows: keep the rows with no target through
  the shared pre-split steps, run the champion's replay chain and model on them after the test
  score, store a predictions CSV, show and download it on the run page.
- **77d** predictions for an uploaded file (same columns as the dataset; lags read history from
  the dataset).

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
