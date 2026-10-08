---
status: implemented
dependencies: [38]
completion_date: 2026-10-08
implementation_notes: "Frontend + Addendum A accepted on b23592c dirty tree: make check exit 0 (3076 passed, 100% line/branch, mypy, ruff); npm test 56/56, typecheck 0, build 0; browser acceptance: candidate sort/champion row, file filters, preview, download 200, inspector files, fork child lists inherited split files with the test frame locked before test scoring."
---

# Chunk 39 — candidate comparison and file browser

User requirement (2026-10-08): every backend feature must be usable in the UI. PLAN §12 item 3
(right pane): candidate comparison table sortable by metric with `varies_from_baseline`,
artifacts. Live cost shipped in 38. Single frontend stage; no backend change (36b already serves
`GET /api/runs/{id}/artifacts`, `/api/artifacts/{id}`, `/api/artifacts/{id}/preview`).

Non-goals: recharts charts (the table with intervals carries the comparison; add charts only if
the user asks), report viewer, downloads of run files, lineage, champion card (chunk 40).

Verified facts (offline stack, 2026-10-08):
- Branch `spec`: `label`, `rationale`, `is_baseline` (bool), `varies_from_baseline` (string[] of
  spec field names, e.g. `["estimator"]`), `estimator {name, ...}`.
- Candidate evaluation outcome `summary` (node `model_evaluation`): `metrics` =
  `{name: {value, ci_low, ci_high}}` for every computed metric, `primary_metric` string,
  `greater_is_better` bool.
- Shared champion outcome `summary` (node `champion_selection`): `candidate_id` ("c_01"),
  `ranking.ranking[]` = `{rank, candidate, estimator, value, ci_low, ci_high, eligible,
  ineligible_reason, meets_success_threshold}`.
- `/api/runs/{id}/artifacts` -> `[{artifact: {id, run_id, kind, path, meta_json, produced_by,
  ts, pruned_at}, locked}]`; `meta_json` text `{ref, kind, label, rows, cols, split, ...}`;
  kinds seen: dataframe, table, transformer, predictions, model, inferential_fit, report, plot.
  A forked run lists inherited artifacts. Preview (dataframe/predictions only) returns
  `{rows, cols, dtypes{col: dtype}, head[{col: value}]}`; 403 locked, 410 pruned, 422 other kinds.

Owned files: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, new
`frontend/src/run/compare.ts`, new `frontend/src/run/CandidateTable.tsx`, new
`frontend/src/run/ArtifactBrowser.tsx`, `frontend/src/run/NodeInspector.tsx`,
`frontend/src/pages/RunPage.tsx`, `frontend/src/style.css` (append), `frontend/tests/client.test.ts`,
new `frontend/tests/compare.test.ts`, `frontend/package.json` (test list only).

## Types (`src/api/types.ts`)

- `ArtifactEntry = { id: string; kind: string; label: string | null; split: string | null; rows: number | null; cols: number | null; producedBy: string; ts: string; pruned: boolean; locked: boolean }`.
- `ArtifactPreview = { rows: number; cols: number; columns: string[]; dtypes: Record<string, string>; head: JsonObject[] }`.

## Client (`src/api/client.ts`)

- `parseArtifactEntries(value: unknown): ArtifactEntry[]` (exported): array; each item object
  with object `artifact` and boolean `locked`; artifact `id, kind, produced_by, ts` required
  strings, `pruned_at` nullable string (`pruned = pruned_at !== null`), `meta_json` string parsed
  in try: when it parses to an object take `label` (string else null), `split` (string else
  null), `rows`/`cols` (finite number else null); unparseable meta -> all four null. Wrong
  types elsewhere -> invalid response.
- `parseArtifactPreview(value: unknown): ArtifactPreview` (exported): object with numbers
  `rows`, `cols`, string-record `dtypes` (reuse `stringRecord`), array `head` of objects
  (`objectArray`); `columns = Object.keys(dtypes)`.
- `artifactUrl(id: string): string` -> `` `/api/artifacts/${encodeURIComponent(id)}` ``.
- `listRunArtifacts(runId, signal?)`: GET `/api/runs/{enc}/artifacts` -> `parseArtifactEntries`.
- `previewArtifact(id, signal?)`: GET `${artifactUrl(id)}/preview` -> `parseArtifactPreview`.
- Tests (`client.test.ts`): `parseArtifactEntries` for one entry with full meta, one with
  `meta_json: '{bad'` (nulls) and `pruned_at` set; invalid response for `locked: 'no'`;
  `parseArtifactPreview` maps columns from dtypes; `artifactUrl('a/b')` -> `'/api/artifacts/a%2Fb'`;
  `previewArtifact('a1')` GETs `/api/artifacts/a1/preview` (withFetch).

## Comparison model (`src/run/compare.ts`, pure)

Imports `RunView`, `Branch`, `NodeOutcome` from `./model.ts`, `isJsonObject` from the client.
- `type MetricValue = { value: number; ciLow: number | null; ciHigh: number | null }`.
- `type CandidateRow = { id: string; label: string; estimator: string | null; isBaseline: boolean; varies: string[]; rationale: string | null; status: string; primaryMetric: string | null; greaterIsBetter: boolean | null; metrics: Record<string, MetricValue>; rank: number | null; eligible: boolean | null; ineligibleReason: string | null; champion: boolean }`.
- `scored(outcomes: NodeOutcome[]): NodeOutcome | null`: last outcome whose `summary.metrics`
  is an object with at least one entry that is an object with a finite number `value`.
- `candidateRows(view: RunView): CandidateRow[]`, one per branch in `view.branches` order:
  `isBaseline = spec.is_baseline === true`; `varies` = string items of
  `spec.varies_from_baseline` (else []); `rationale` = string `spec.rationale` or null;
  metrics from `scored(branch.outcomes)?.summary.metrics`: keep entries whose value is an object
  with finite number `value`; `ciLow`/`ciHigh` finite number else null; `primaryMetric` =
  string `summary.primary_metric` else null; `greaterIsBetter` = boolean
  `summary.greater_is_better` else null. Champion data: the last outcome in `view.shared` whose
  `summary.ranking` is an object with array `ranking`; for this branch the ranking entry with
  `candidate === branch.id` gives `rank` (number), `eligible` (boolean), `ineligibleReason`
  (string or null); none -> nulls. `champion` = that outcome's `summary.candidate_id === branch.id`.
- `metricColumns(rows): string[]`: union of metric names; the first non-null `primaryMetric`
  among rows first (when present in the union), the rest sorted alphabetically.
- `type SortKey = { metric: string | null; direction: 'asc' | 'desc' }`.
- `defaultSort(rows): SortKey`: metric = first non-null `primaryMetric` or null; direction
  'asc' when that row's `greaterIsBetter === false`, else 'desc'.
- `sortRows(rows, key): CandidateRow[]` (new array): metric null -> rank ascending, null ranks
  last, then id; metric set -> by `metrics[metric].value` in `direction`, rows missing the
  metric always last, ties by id ascending.
- `formatMetric(m: MetricValue): string`: `value.toFixed(4)`, plus
  `` ` (${ciLow.toFixed(4)} to ${ciHigh.toFixed(4)})` `` when both bounds are numbers.
- `artifactKinds(entries: ArtifactEntry[]): string[]` and `artifactSplits(entries): string[]`:
  distinct non-null values sorted.
- `filterArtifacts(entries, kind: string, split: string): ArtifactEntry[]`: '' means any.
- Tests (`tests/compare.test.ts`, add to `package.json` test list): a `RunView` with three
  branches (baseline c_01 with metrics roc_auc 0.81 CI + accuracy, c_02 varies ['estimator']
  roc_auc 0.75, c_03 with no scored outcome) and a shared outcome with ranking (c_01 rank 1
  eligible, c_02 rank 2 ineligible reason 'r') and `candidate_id: 'c_01'` -> exact rows;
  `metricColumns` -> ['roc_auc', 'accuracy']; `defaultSort` -> roc_auc desc; `sortRows` desc,
  asc (c_03 last both times), rank sort; `greaterIsBetter: false` -> asc default;
  `formatMetric` with and without CI; artifact kind/split helpers and `filterArtifacts`.

## Components

### `src/run/ArtifactBrowser.tsx`

- `export function ArtifactRow({ entry }: { entry: ArtifactEntry })` renders a fragment of one
  `<tr>` plus, when expanded, a second `<tr><td colSpan={7}>`. Cells: File (`label ?? id` and a
  `<small className="help"><code>{id}</code></small>`), Kind, Split (`split ?? '—'`), Shape
  (`rows`/`cols` both numbers -> `` `${rows} × ${cols}` `` else '—'), Made by (`producedBy`),
  Created (`formatTime(ts)`), Actions:
  - `locked` -> `<span className="pill gate">Locked until the test score is recorded</span>`;
  - `pruned` -> `<span className="pill">Removed to save space</span>`;
  - else `<a className="button" href={artifactUrl(id)} download>Download</a>`, plus kind
    `plot` -> button "Show"/"Hide" toggling an `<img className="plot" alt={label ?? id} src={artifactUrl(id)} />`
    in the expanded row; kind `dataframe` or `predictions` -> button "Preview"/"Hide preview"
    toggling `<ArtifactPreviewTable id={id} />` in the expanded row.
- `function ArtifactPreviewTable({ id })`: `useQuery({ queryKey: ['preview', id], queryFn: previewArtifact })`;
  pending "Loading preview…"; error alert `errorText`; data -> `.table-wrap` table caption
  `` `First ${head.length} of ${rows} rows, ${cols} columns` ``; header = columns with dtype in
  `<small className="help">`; cells `String(row[col] ?? '')` (objects via `JSON.stringify`).
- `export const ARTIFACT_HEADERS = ['File', 'Kind', 'Split', 'Shape', 'Made by', 'Created', 'Actions']`.
- `export default function ArtifactBrowser({ entries }: { entries: ArtifactEntry[] })`: state
  `kind` '' and `split` ''; labelled selects "Kind" (All + `artifactKinds`) and "Split"
  (All + `artifactSplits`) in a `.filters` div; `filtered = filterArtifacts(...)`; paragraph
  `` `${filtered.length} of ${entries.length} files` ``; `.table-wrap` table caption "Run files",
  header from `ARTIFACT_HEADERS`, body rows `ArtifactRow` keyed by id. No entries ->
  `<p className="secondary-line">No files yet. Steps add files as they run.</p>`.

### `src/run/CandidateTable.tsx`

`export default function CandidateTable({ rows }: { rows: CandidateRow[] })`. Empty ->
"Candidate models appear after the experiment design step.". State `sort` initialised with
`defaultSort(rows)` (re-initialise when `metricColumns` change is not needed).
`<p className="help">Validation scores with bootstrap intervals. Select a column to sort.</p>`;
`.table-wrap` table caption "Candidate comparison". Header: "Rank" (sort button, metric null),
"Candidate", "Estimator", "Differs from baseline", "Status", one sortable header per
`metricColumns(rows)` (primary gets `<small className="help">primary</small>`), "Eligible".
Sortable headers are `<th aria-sort={...}>` (`ascending`/`descending` on the active column,
else `none`) containing `<button type="button" className="cell-link">`; clicking the active
column flips direction; a new metric column starts 'desc' unless the first row with
`greaterIsBetter === false` exists (then 'asc'); Rank starts 'asc'. Rows `sortRows(rows, sort)`;
champion row `className="is-target"` and after the label `<span className="pill signal">Champion</span>`.
Cells: rank number or '—'; label + `<small className="help">{id}</small>`; estimator or '—';
`isBaseline` -> "Baseline", else `varies.join(', ')` or '—'; status; metrics via `formatMetric`
or '—'; eligible true "Yes", false `` `No: ${ineligibleReason ?? 'not eligible'}` ``, null '—'.
Rationale: each row's label cell wraps in `<details><summary>{label}</summary><p className="reasoning">{rationale}</p></details>`
when `rationale` is non-null.

### `src/run/NodeInspector.tsx`

New prop `artifacts: ArtifactEntry[] | undefined`. Replace the "Artifacts" fact with a section
after the outcome facts when `outcome.artifacts.length > 0`: `<h3>Files</h3>` and a
`.table-wrap` table (header `ARTIFACT_HEADERS`) rendering `ArtifactRow` for each ref found in
`artifacts`; refs not found render one row `<tr><td colSpan={7}><code>{ref}</code> is not listed for this run.</td></tr>`;
`artifacts === undefined` -> "Loading files…".

### `src/pages/RunPage.tsx`

- Query `['artifacts', runId]` -> `listRunArtifacts`; include it in the debounced event refresh.
- Tabs inside the workspace main column (reuse `.tabs` markup from `EditStep`: `role="tablist"`,
  buttons `role="tab"` `aria-selected` `aria-controls="run-panel"`, panel `role="tabpanel"`):
  "Steps" (RunGraph + NodeInspector, default), `` `Candidates (${rows.length})` `` (CandidateTable
  with `candidateRows(view)`), `` `Files (${entries.length})` `` (ArtifactBrowser; while loading
  "Loading files…", error alert). `view === null` keeps today's alert in place of all tabs.
- Pass `artifacts={artifacts.data}` to NodeInspector.

### `src/style.css` (append)

`.filters { display: flex; flex-wrap: wrap; gap: 16px; margin: 12px 0; }`,
`.filters select { width: auto; }`, `img.plot { max-width: 100%; height: auto; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--surface); }`,
`th[aria-sort="ascending"] button::after { content: " ▲"; }`, `th[aria-sort="descending"] button::after { content: " ▼"; }`.

## Checks

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build` (genuine exits, counts).
Stop and report on failure or missing decision. Main then does browser acceptance (candidate
sort, champion row, file filters, preview, download link, inspector files, locked pill on a
guided run before test scoring) and the final `make check`.

## Addendum A — inherited files missing from fork listings (2026-10-08)

Browser acceptance: a rewind child forked after `data_splitting` (`r_d91078ee5aeabca7`) lists
only its own 48 artifacts; the inherited train/valid/test frames are absent, so the D32 lock and
D11 "listing follows the fork" are invisible in the UI. Root cause:
`backend/statlab/api/inspect.py` `artifacts` calls `list_artifacts(conn, run.id,
include_pruned=True)` without `checkpoint`; `storage.list_artifacts` adds ancestor rows only
when given the run's checkpoint (`storage.py:126-133`, validated by
`validate_checkpoint_ownership`). Downloads/previews of inherited rows already work (rows carry
their owner `run_id`); the lock uses the child's `test_scored_at` (correct per child).

Owned: `backend/statlab/api/inspect.py`, `tests/test_api_inspect.py`.
- `inspect.py`: import `RunState` from `statlab.graph.state` and `EngineError` from
  `statlab.errors`. New module function `_checkpoint(paths: RunPaths) -> RunState | None`
  (docstring "The run's retained checkpoint, or None before the first save."):
  `try: text = paths.state_json.read_text(encoding="utf-8")` `except FileNotFoundError: return None`;
  `try: return RunState.model_validate_json(text)` `except ValueError: raise EngineError("run checkpoint is malformed") from None`.
  `artifacts` route: `run, paths = _run(resource, run_id)` and
  `list_artifacts(resource.conn, run.id, include_pruned=True, checkpoint=_checkpoint(paths))`.
- `tests/test_api_inspect.py` new `test_artifact_list_reads_checkpoint(tmp_path, monkeypatch)`:
  `_open_project_api`, `seed = _seed(settings, str(project["id"]))`,
  `paths = RunPaths(settings.data_dir, "demo", seed.run_id)`;
  (1) write `paths.state_json` with `RunState(run_id=seed.run_id, spine_id="s", spine=(), mode="autonomous").model_dump_json()`
  -> GET `/api/runs/{run}/artifacts` 200 with the same five ids as the existing list test;
  (2) write `"{"` -> 500 `{"detail": "internal error"}`;
  (3) write the state with `run_id="r_other"` -> 500 (ownership mismatch, `PersistenceError`).
  If `RunState` rejects an empty `spine`, stop and report.
- Checks: `make test-fast TESTS="tests/test_api_inspect.py"`, ruff check, ruff format --check
  and mypy on the two files.
