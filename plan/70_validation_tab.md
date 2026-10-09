---
status: completed
dependencies: [69]
completion_date: 2026-10-09
implementation_notes: "Validation tab: per-model validation rows with actual, predicted, confidence/error and the original columns; make check 100%, npm 105/105, browser-checked"
---

# Chunk 70 — Validation tab: actual vs predicted on the validation rows

User request 2026-10-09: "new tab beside report where I can see the validation data and
compare it with the original and predicted, make it nice".

Facts (read-only):
- `data_splitting` summary holds `valid` (the validation split frame with the original
  columns and the target; `backend/statlab/domains/foundation.py` `_split`).
- Each candidate's latest completed `model_evaluation` summary holds `predictions` (kind
  `predictions`, column `prediction`), for classification also `probabilities`
  (`proba__{class}` columns) and `threshold` (float or null). Both are indexed by the
  validation frame's labels (`mcp_server/statlab_tools/tools/training.py` `predict`
  ~654/`predict_proba` ~673). Scoring derives hard classes from the probabilities: argmax,
  or binary `p(positive) >= threshold` with the positive class from the contract snapshot
  (`evaluation.py` `score_classification` ~390, helpers `proba_columns` ~153,
  `class_labels` ~166, `snapshot` ~145, `_positive_class` ~183, `_hard_codes` ~301).
- The backend imports no compute library; frames are read by MCP tools. UI previews use the
  read-only direct dispatch `McpClient.describe_artifact` (`backend/statlab/mcp_client.py`
  ~327) behind `GET /api/artifacts/{id}/preview` (`backend/statlab/api/inspect.py` ~136).
- Validation only: no test-split data is read (D32).

Decisions:
1. New MCP tool `validation_rows` (stateless, phase `artifacts`, `allow_in=["*"]`, read-only)
   joins the validation frame and one prediction artifact by label and returns one page of
   rows. Classification labels are derived exactly as scoring does.
2. `McpClient` gets a shared read-only dispatcher; `describe_artifact` and the new
   `validation_rows` use it.
3. `GET /api/runs/{run_id}/validation?candidate=c_01&worst_first=false&offset=0&limit=50`
   reads the run checkpoint, finds the sources and calls the tool.
4. UI: a "Validation" tab after "Report": model picker (champion first by default), "All
   rows" / "Mistakes only" (classification) or "Largest errors first" (regression), a summary
   line, a table (row, actual, predicted as a green/red pill, confidence or error, then every
   original column), mistakes tinted, 50 rows per page.
Non-goals: charts, test-split rows, editing, CSV export.

## Stage 70a (detailed spec)

Owned: `mcp_server/statlab_tools/tools/evaluation.py`, `backend/statlab/mcp_client.py`,
`backend/statlab/api/inspect.py`, `tests/test_tools_evaluation.py`,
`tests/test_mcp_client.py`, `tests/test_api_inspect.py`, `frontend/src/api/types.ts`,
`frontend/src/api/client.ts`, `frontend/src/run/compare.ts`,
`frontend/src/run/ValidationView.tsx` (new), `frontend/src/pages/RunPage.tsx`,
`frontend/src/style.css`, `frontend/tests/compare.test.ts`, `frontend/tests/client.test.ts`.
Not alone in the tree: preserve others' edits.

### 1. MCP tool (`evaluation.py`, after `score_classification`)

`@tool(phase="artifacts", allow_in=["*"])` `def validation_rows(paths: RunPaths, frame:
Annotated[ArtifactRef, Field(description="The validation split frame with the original
columns.")], predictions: Annotated[ArtifactRef, Field(description="Class probabilities
(classification) or predictions (regression) on the validation split.")], target:
Annotated[str, Field(min_length=1, description="The target column.")], task:
Literal["classification", "regression"], threshold: Annotated[float | None, Field(gt=0,
lt=1, description="Binary p(positive)>=threshold, as scored.")] = None, worst_first: bool =
False, offset: Annotated[int, Field(ge=0)] = 0, limit: Annotated[int, Field(ge=1, le=200)] =
50) -> ToolSuccess:` (use the module's existing `Annotated`/`Field`/`Literal` imports; add
`ArtifactRef` only if missing). Docstring: "One page of validation rows joined to a model's
predictions by label: actual, predicted, confidence (classification) or error (regression)
and the original columns. Hard classes follow `score_classification` (argmax, or binary
p(positive) >= threshold). Read-only; validation split only (D32)."

Algorithm:
1. `data = load_dataframe(paths.artifact(frame, "dataframe"))`; if `target not in
   data.columns`: raise `ToolParamError("target is not a column of the frame",
   field_errors={"target": "not in the validation frame"})`.
2. `preds = load_dataframe(paths.artifact(predictions, "predictions"))`;
   `keep = preds.index[preds.index.isin(data.index)]`; `preds = preds.loc[keep]`;
   `data = data.loc[keep]`; `n = len(keep)`; `truth = data[target]`;
   `features = [str(c) for c in data.columns if c != target]`.
3. Classification: `cols = proba_columns(preds)`; `classes = class_labels(cols)`; if
   `threshold is not None and len(classes) != 2`: raise the same `ToolParamError` as
   `score_classification` ("threshold is binary only; this target has more than two
   classes", `field_errors={"threshold": "not applicable to a multiclass target"}`);
   `positive, _ = _positive_class(snapshot(paths), classes)`; `proba =
   preds[cols].to_numpy(dtype="float64")`; `codes = _hard_codes(proba, classes.index(positive),
   threshold)`; `predicted = [classes[int(c)] for c in codes]`; `confidence =
   proba[np.arange(n), codes]`; `actual = as_str(truth).tolist()`; `correct =
   np.array([a == p for a, p in zip(actual, predicted, strict=True)], dtype=bool)`;
   `error = None`.
   Regression: if `"prediction" not in preds.columns`: raise `ToolParamError("predictions need
   a prediction column", field_errors={"predictions": "no prediction column"})`;
   `predicted_num = preds["prediction"].to_numpy(dtype="float64")`; `actual_num =
   pd.to_numeric(truth, errors="coerce").to_numpy(dtype="float64")`; `error = predicted_num -
   actual_num`; `confidence = None`; `correct = None`.
4. Order: `order = np.arange(n)`. If `worst_first`: classification `mistakes =
   np.flatnonzero(~correct)`; `order = mistakes[np.argsort(-confidence[mistakes],
   kind="stable")]`; regression `order = np.argsort(-np.abs(error), kind="stable")`.
   `page = order[offset : offset + limit]`.
5. Each row `i` in `page`: `{"row": json_scalar(keep[i]), "actual": actual[i]` (classification)
   or `json_scalar(truth.iloc[i])` (regression)`, "predicted": predicted[i]` or
   `json_scalar(float(predicted_num[i]))`, `"confidence": round(float(confidence[i]), 4)` or
   `None`, `"error": json_scalar(float(error[i]))` or `None`, `"correct": bool(correct[i])` or
   `None`, `"values": {c: json_scalar(data[c].iloc[i]) for c in features}}`. (`data[c]` with
   `c` the stringified name: if a frame has non-string column labels, stop and report.)
6. `mean_abs_error`: regression only — `finite = np.abs(error[np.isfinite(error)])`;
   `float(finite.mean())` if `finite.size` else `None`; classification `None`.
7. Return `ToolSuccess(summary={"task": task, "total": n, "shown_total": len(order),
   "offset": offset, "limit": limit, "correct": int(correct.sum())` (classification) or
   `None, "mean_abs_error": ..., "threshold": threshold, "columns": features, "rows": rows})`
   (lists through `json_list` if mypy requires `JsonValue` lists).

### 2. `McpClient` (`mcp_client.py`)

- Add private `async def _read_only(self, tool: str, project: str, arguments: dict[str,
  JsonValue]) -> ToolSuccess:` holding the current `describe_artifact` body: not entered ->
  `EngineError("MCP client is not entered")`; unknown project -> `EngineError(f"{tool}
  project does not exist")`; spec missing or not (`phase == "artifacts"`, `allow_in ==
  ("*",)`, not heavy, not transform_only) -> `EngineError(f"{tool} registry metadata is
  invalid")`; `validated = await self._dispatch_direct(spec, arguments,
  read_timeout_seconds=60)`; artifacts present -> `ToolCallError(tool,
  Failure(error_class="bug", message="read-only tool returned unexpected artifacts"))`.
- `describe_artifact` keeps its signature and docstring and returns `await
  self._read_only("describe_artifact", project, {...same arguments...})`.
- New `async def validation_rows(self, *, project: str, run: str, frame: str, predictions:
  str, target: str, task: Literal["classification", "regression"], threshold: float | None,
  worst_first: bool, offset: int, limit: int) -> ToolSuccess:` docstring "Dispatch the
  read-only validation rows tool for the UI without run-scoped accounting." Returns
  `await self._read_only("validation_rows", project, {"project": project, "run": run,
  "frame": frame, "predictions": predictions, "target": target, "task": task, "threshold":
  threshold, "worst_first": worst_first, "offset": offset, "limit": limit})`.

### 3. API (`inspect.py`)

Imports: `Annotated` (typing), `Query` (fastapi), `candidate_result` from
`statlab.domains.candidate_pipeline`, `problem_from_state` and `shared_result` from
`statlab.domains.foundation`. Constant `NO_VALIDATION = "this model has no validation
predictions yet"`. Route after `preview`:
`@router.get("/api/runs/{run_id}/validation")` `async def validation(run_id: str,
candidate: str, worst_first: bool = False, offset: Annotated[int, Query(ge=0)] = 0, limit:
Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, JsonValue]:`
1. `resource = resources()`; `run, paths = _run(resource, run_id)`; `state =
   _checkpoint(paths)`; `None` -> 404 `NO_VALIDATION`.
2. `branch = next((item for item in state.branches if item.public_id == candidate), None)`;
   `None` -> 404 `"model not found"`.
3. In `try`: `split = shared_result(state, "data_splitting").summary`; `evaluation =
   candidate_result(branch, "model_evaluation").summary`; `problem =
   problem_from_state(state)`; `except EngineError` -> 404 `NO_VALIDATION`.
4. `source = evaluation.get("probabilities" if problem.task == "classification" else
   "predictions")`; `frame = split.get("valid")`; either not a `str` -> 404 `NO_VALIDATION`.
   `threshold = evaluation.get("threshold")`.
5. `result = await resource.mcp.validation_rows(project=paths.slug, run=run.id, frame=frame,
   predictions=source, target=problem.target, task=problem.task, threshold=threshold if
   isinstance(threshold, float) else None, worst_first=worst_first, offset=offset,
   limit=limit)`; return `result.summary`.

### 4. Frontend

1. `types.ts`: `export type ValidationRow = { row: JsonValue; actual: JsonValue; predicted:
   JsonValue; confidence: number | null; error: number | null; correct: boolean | null;
   values: JsonObject };` and `export type ValidationPage = { task: 'classification' |
   'regression'; total: number; shownTotal: number; offset: number; limit: number; correct:
   number | null; meanAbsError: number | null; threshold: number | null; columns: string[];
   rows: ValidationRow[] };`
2. `client.ts`:
   - `export function parseValidationPage(value: unknown): ValidationPage` — throw
     `invalidResponse()` unless: `isJsonObject(value)`; `task` is `'classification'` or
     `'regression'`; `total`, `shown_total`, `offset`, `limit` are numbers; `correct`,
     `mean_abs_error`, `threshold` are numbers or null; `columns` is an array of strings;
     `rows` is an array whose items are JSON objects with `values` a JSON object,
     `confidence` and `error` numbers or null, `correct` a boolean or null. Map each row to
     `{ row: item.row ?? null, actual: item.actual ?? null, predicted: item.predicted ?? null,
     confidence, error, correct, values }` and the page to camelCase fields.
   - `export async function getValidation(runId: string, query: { candidate: string;
     worstFirst: boolean; offset: number; limit: number }, signal?: AbortSignal):
     Promise<ValidationPage>` — `const params = new URLSearchParams({ candidate:
     query.candidate, worst_first: String(query.worstFirst), offset: String(query.offset),
     limit: String(query.limit) });` then `parseValidationPage(await
     request(`/api/runs/${encodeURIComponent(runId)}/validation?${params.toString()}`, {
     method: 'GET', signal }))`.
3. `compare.ts` (add value imports `humanize`, `latestOutcome` from `./model.ts`; types
   `JsonValue`, `ValidationPage` from `../api/types.ts`):
   - `export type ValidationModel = { id: string; label: string; champion: boolean };`
   - `export function validationModels(view: RunView): ValidationModel[]` —
     `candidateRows(view).filter((row) => latestOutcome(view, 'model_evaluation',
     row.id)?.status === 'completed').map((row) => ({ id: row.id, label: humanize(row.label),
     champion: row.champion }))`.
   - `export function cellText(value: JsonValue): string` — `null` -> `'—'`; number ->
     `Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))`; boolean ->
     `value ? 'yes' : 'no'`; string -> itself; otherwise `JSON.stringify(value)`.
   - `export function validationSummary(page: ValidationPage): string` — `page.total === 0` ->
     `'No validation rows.'`; classification: `` `${page.correct ?? 0} of ${page.total}
     validation rows predicted correctly (${(((page.correct ?? 0) / page.total) *
     100).toFixed(1)}%).` `` plus, when `page.threshold !== null`, `` ` Classes use the tuned
     threshold ${page.threshold}.` ``; regression: `` `Mean absolute error
     ${cellText(page.meanAbsError)} over ${page.total} validation rows.` ``.
4. `ValidationView.tsx` (new, default export `ValidationView({ runId, view }: { runId:
   string; view: RunView })`), `const PAGE = 50`:
   - `models = validationModels(view)`; state `chosen: string | null` (null), `worstFirst`
     (false), `offset` (0); `candidate = chosen ?? (models.find((m) => m.champion) ??
     models[0])?.id ?? null`.
   - `page = useQuery({ queryKey: ['validation', runId, candidate, worstFirst, offset],
     queryFn: ({ signal }) => getValidation(runId, { candidate: candidate ?? '', worstFirst,
     offset, limit: PAGE }, signal), enabled: candidate !== null })`.
   - `candidate === null` -> `<p className="secondary-line">No model has validation
     predictions yet.</p>`.
   - Otherwise `<section className="validation" aria-label="Validation predictions">`:
     a `div.validation-bar` with `<label>Model <select value={candidate} onChange={(e) => {
     setChosen(e.target.value); setOffset(0); }}>` options `{m.label}{m.champion ? '
     (champion)' : ''}` keyed and valued by `m.id`, and `<div className="chips" role="group"
     aria-label="Rows to show">` with two `button.chip` (`type="button"`, `aria-pressed`):
     "All rows" (pressed when `!worstFirst`, sets false and offset 0) and
     `page.data?.task === 'regression' ? 'Largest errors first' : 'Mistakes only'` (pressed
     when `worstFirst`, sets true and offset 0).
   - `page.isPending` -> `<p>Loading validation rows…</p>`; `page.isError` -> `<p
     role="alert">{errorText(page.error)}</p>`.
   - With data `d`: `<p className="secondary-line">{validationSummary(d)}</p>`; a
     `div.table-wrap` with `<table className="validation-table">`; caption `d.shownTotal ===
     0 ? 'No rows to show' : `Rows ${d.offset + 1}–${d.offset + d.rows.length} of
     ${d.shownTotal}``; header cells (`scope="col"`): Row, Actual, Predicted, `d.task ===
     'classification' ? 'Confidence' : 'Error'`, then each `d.columns` name; body rows keyed
     `cellText(r.row)`, `className={r.correct === false ? 'miss' : undefined}`: `cellText(r.row)`,
     `cellText(r.actual)`, predicted as `r.correct === null ? cellText(r.predicted) : <span
     className={r.correct ? 'pill signal' : 'pill fault'}>{cellText(r.predicted)}</span>`,
     `<td className="num">` with `d.task === 'classification' ? (r.confidence === null ? '—' :
     `${(r.confidence * 100).toFixed(1)}%`) : cellText(r.error)`, then `cellText(r.values[c] ??
     null)` per column. Then `<div className="pager">` with "Previous" (`disabled={offset ===
     0}`, `setOffset(Math.max(0, offset - PAGE))`) and "Next" (`disabled={offset + PAGE >=
     d.shownTotal}`, `setOffset(offset + PAGE)`).
5. `RunPage.tsx`: import `ScanSearch` from lucide and `ValidationView`; tab union adds
   `'validation'`; tabs array gets `{ id: 'validation' as const, label: 'Validation', Icon:
   ScanSearch as LucideIcon }` after `report`; panel `{tab === 'validation' && <ValidationView
   runId={runId} view={view} />}`.
6. `style.css` (append near the table rules): `.validation-bar { display: flex; flex-wrap:
   wrap; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px;
   }`, `.chip[aria-pressed="true"] { border-color: var(--signal); color: var(--signal); }`,
   `table.validation-table tr.miss td { background: var(--fault-soft); }`,
   `table.validation-table td.num { text-align: right; font-variant-numeric: tabular-nums; }`,
   `.pager { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }`.

### 5. Tests

1. `tests/test_tools_evaluation.py` (use `_save`, `_write_snapshot`, `_binary_contract`, the
   `run_paths` fixture):
   - `test_validation_rows_classification`: frame `pd.DataFrame({"x": [1, 2, 3, 4], "c": ["a",
     "b", "c", "d"], "y": [0, 1, 1, 0]}, index=[10, 11, 12, 13])`; probabilities
     `pd.DataFrame({"proba__0": [0.8, 0.3, 0.6, 0.45, 0.5], "proba__1": [0.2, 0.7, 0.4, 0.55,
     0.5]}, index=[10, 11, 12, 13, 99])` saved as kind `"predictions"`; snapshot
     `_binary_contract(1)`. Default call: `total == 4`, `shown_total == 4`, `correct == 2`,
     `columns == ["x", "c"]`, `rows[0] == {"row": 10, "actual": "0", "predicted": "0",
     "confidence": 0.8, "error": None, "correct": True, "values": {"x": 1, "c": "a"}}`.
     `worst_first=True`: rows `[12, 13]`, `shown_total == 2`. `threshold=0.65`: `correct ==
     3`, row 13 `predicted == "0"` and `confidence == 0.45`. `offset=1, limit=2`: rows `[11,
     12]`.
   - `test_validation_rows_regression`: frame `{"x": [1, 2, 3], "y": [1.0, 2.0, 3.0]}` index
     `[0, 1, 2]`; predictions `{"prediction": [1.5, 2.0, 0.0]}` same index (kind
     `"predictions"`); errors `[0.5, 0.0, -3.0]`, `abs(mean_abs_error - 3.5 / 3) < 1e-9`,
     `correct is None`, row confidences `None`; `worst_first=True` rows `[2, 0, 1]`.
   - `test_validation_rows_refusals`: `pytest.raises(ToolParamError)` for target `"missing"`;
     for regression predictions without a `prediction` column (save the probabilities frame);
     for `threshold=0.5` with a three-class probabilities frame.
2. `tests/test_mcp_client.py`: `test_validation_rows_dispatches_without_audit` modelled on
   `test_describe_artifact_dispatches_without_audit` (~2055) with `served_tool("validation_rows",
   {"type": "object"}, allow_in=["*"], phase="artifacts")`; assert the summary, the exact
   arguments dict of section 2 and timeout 60, and zero `tool_calls` rows. The existing
   describe tests stay unchanged and must pass.
3. `tests/test_api_inspect.py` `test_validation_route` with `_open_project_api` and `_seed`:
   - no checkpoint -> GET `/api/runs/{run}/validation?candidate=c_01` 404 `NO_VALIDATION`.
   - Write a checkpoint: `RunState.initial(run_id=seed.run_id, spine_id="tabular",
     nodes=TABULAR_SPINE, mode="autonomous", candidates=(candidate_spec(),))` (imports from
     `statlab.domains.tabular` and `tests.test_graph_state`), then `model_copy` with
     `shared_results` = `BoundaryResult(node_name="problem_definition", execution_id="e1",
     result=NodeResult(status="completed", summary={"problem": ProblemSpec(rationale="r",
     target="a", target_type="binary", task="classification", primary_metric="roc_auc",
     metric_rule="explicit").model_dump(mode="json")}))` and `BoundaryResult(node_name=
     "data_splitting", execution_id="e2", result=NodeResult(status="completed",
     summary={"valid": seed.frame}))`, and `branches` = the first branch `model_copy` with
     `results=(BoundaryResult(node_name="model_evaluation", execution_id="e3",
     result=NodeResult(status="completed", summary={"probabilities": seed.frame,
     "predictions": seed.frame, "threshold": 0.4})),)`. Write it to `paths.state_json`. Use
     the branch's actual `public_id` in the URL.
   - Unknown candidate `c_99` -> 404 `"model not found"`.
   - `session.responses.append(ToolSuccess(summary={"task": "classification"}))`; GET with
     `worst_first=true&offset=5&limit=10` -> 200 and the summary echoed; last session call is
     `validation_rows` with `{"project": "demo", "run": seed.run_id, "frame": seed.frame,
     "predictions": seed.frame, "target": "a", "task": "classification", "threshold": 0.4,
     "worst_first": True, "offset": 5, "limit": 10}`.
   - `limit=500` -> 422.
   - A checkpoint whose branch has no `model_evaluation` result -> 404 `NO_VALIDATION`; one
     whose evaluation summary lacks `probabilities` -> 404 `NO_VALIDATION`.
   If `ProblemSpec(...)` or `RunState.initial(...)` rejects these arguments, stop and report
   the error.
4. `frontend/tests/compare.test.ts`: `validationModels` keeps only candidates whose latest
   `model_evaluation` completed and marks the champion (build the view the way the existing
   compare tests do); `cellText` for `null`, `3`, `0.123456`, `true`, `'x'`, `[1]` ->
   `'—'`, `'3'`, `'0.1235'`, `'yes'`, `'x'`, `'[1]'`; `validationSummary` for a
   classification page (`correct` 3, `total` 4, `threshold` 0.4) -> `'3 of 4 validation rows
   predicted correctly (75.0%). Classes use the tuned threshold 0.4.'`, a regression page
   (`meanAbsError` 1.5, `total` 2) -> `'Mean absolute error 1.5 over 2 validation rows.'`,
   and `total` 0 -> `'No validation rows.'`.
5. `frontend/tests/client.test.ts`: `getValidation` builds `/api/runs/r1/validation?
   candidate=c_01&worst_first=true&offset=50&limit=50` and returns the parsed page (camelCase
   fields); `parseValidationPage` throws on a bad `task` and on a row whose `values` is not an
   object.

### 6. Checks

`make test-fast TESTS="tests/test_tools_evaluation.py tests/test_mcp_client.py
tests/test_api_inspect.py"`; Ruff check, Ruff format --check and mypy on the six owned
Python files; from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`. Stop and
report on any failure or gap.

Main then: review, `make check`, a browser check on an offline-stack run, commit.

### Addendum 70a-A (parameter descriptions)

Every tool parameter needs `Field(description=...)` (`tool.py` `_check_parameter_descriptions`).
Use: `task: Annotated[Literal["classification", "regression"], Field(description="The run's
task.")]`; `worst_first: Annotated[bool, Field(description="Classification: only mistakes,
most confident first; regression: largest absolute error first.")] = False`; `offset:
Annotated[int, Field(ge=0, description="Rows to skip.")] = 0`; `limit: Annotated[int,
Field(ge=1, le=200, description="Rows to return.")] = 50`. Then continue the 70a recipe and
checks. Extra mypy-narrowing branches are acceptable only if the focused tests cover them;
report any branch the new tests cannot reach instead of adding tests for it.

### Addendum 70a-B (test fixtures; no unreachable branches)

1. `tests/test_mcp_client.py` `test_validation_rows_dispatches_without_audit`: serve the tool
   with schema `{"type": "object", "properties": {}, "additionalProperties": True}`
   (`_discover` requires `properties`).
2. `tests/test_api_inspect.py` `_validation_state`: a full `TABULAR_SPINE` checkpoint fails
   `RunState` validation ("shared boundaries disagree with cursor"). Use `nodes =
   (node("problem_definition"), node("data_splitting"), node("model_evaluation",
   candidate=True))` (`node` from `tests.test_graph_state`), `spine_id="s"`, and in the final
   `model_copy` also set `cursor` to `3` when `evaluation is not None`, else `2` (main verified
   both shapes round-trip through `RunState.model_validate_json`). Drop the `TABULAR_SPINE`
   import if unused.
3. `validation_rows` (`evaluation.py`): replace the optional locals and the guarded branches
   (`confidence is not None and correct is not None`, `elif error is not None`, `elif
   predicted_num is not None and error is not None`, `if error is not None`) with per-task
   lists so every branch is reachable:
   - Classification branch computes as now, then sets `actual_out: list[JsonValue] =
     list(actual)`, `predicted_out: list[JsonValue] = list(predicted)`, `confidence_out:
     list[JsonValue] = [round(float(v), 4) for v in confidence]`, `error_out: list[JsonValue] =
     [None] * n`, `correct_out: list[JsonValue] = [bool(v) for v in correct]`, `eligible =
     ~correct`, `badness = confidence`, `correct_count: int | None = int(correct.sum())`,
     `mean_abs_error: float | None = None`.
   - Regression branch computes `predicted_num`, `actual_num`, `error` as now, then
     `actual_out = [json_scalar(v) for v in truth]`, `predicted_out =
     [json_scalar(float(v)) for v in predicted_num]`, `confidence_out = [None] * n`,
     `error_out = [json_scalar(float(v)) for v in error]`, `correct_out = [None] * n`,
     `eligible = np.ones(n, dtype=bool)`, `badness = np.abs(error)`, `correct_count = None`,
     `finite = np.abs(error[np.isfinite(error)])`, `mean_abs_error = float(finite.mean()) if
     finite.size else None`.
   - `order = np.arange(n)`; if `worst_first`: `chosen = np.flatnonzero(eligible)`; `order =
     chosen[np.argsort(-badness[chosen], kind="stable")]`. `page = order[offset : offset +
     limit]`.
   - Each row: `{"row": json_scalar(keep[i]), "actual": actual_out[i], "predicted":
     predicted_out[i], "confidence": confidence_out[i], "error": error_out[i], "correct":
     correct_out[i], "values": {c: json_scalar(data[c].iloc[i]) for c in features}}`.
   - Summary `"correct": correct_count`, `"mean_abs_error": mean_abs_error`; rest unchanged.
   Declare each name once before the `if` with its type if mypy needs it.
4. `test_validation_rows_regression`: also call with a predictions frame indexed `[7]` (no
   shared label): `total == 0`, `rows == []`, `mean_abs_error is None`.
Then rerun the Python checks and continue with the frontend sections and all 70a checks.

## Acceptance receipt (2026-10-09)

Implementer (after addenda A, B): focused selectors exit 0 (162 passed); Ruff check/format
and mypy on the six Python files exit 0; `npm test` 105/105, typecheck 0, build 0. Main
reviewed the diff (tool joins by label and derives classes like `score_classification`;
shared read-only dispatcher; route 404s; tab, picker, filter, pager). `make check`
(`check70.log`, temporary) exit 0: coverage 100 % (TOTAL 12695 stmts, 0 missed), mypy 200
files, Ruff clean. Browser check on the offline suite output
(`integration/out/20261009T135816Z-offline`, MCP 8767, API 8766, Vite 5174, dummy provider
key): S01 classification tab shows "75 of 100 validation rows predicted correctly (75.0%)",
matching the run's validation accuracy 0.75, mistakes tinted; "Mistakes only" returns 25
rows; S02 regression "Largest errors first" orders by absolute error. Screenshots
`.playwright-mcp/validation-tab.png`, `.playwright-mcp/validation-tab-regression.png`.
Servers stopped.
