---
status: in_progress
dependencies: [33, 36, 36b]
completion_date: null
implementation_notes: ""
---

# Chunk37 — setup flow (project picker, upload, contract editor, validation, start)

User requirement (2026-10-08): every backend feature must be usable in the UI. PLAN §12 items 1–2.
Two stages: 37a backend reads the editor needs; 37b frontend (spec written after 37a handoff).

## Stage 37a — dataset profile and vocabulary endpoints

### Goal and non-goals

The mapping table needs the dataset's columns/dtypes/inferred roles without running the LLM
(today the profile exists only inside `propose`). The contract forms need the allowed values
(contract JSON schema enums, registry estimators and inferential models) from the backend, not
hard-coded TypeScript copies. Non-goals: no change to existing routes' responses, no new MCP
tool, no frontend.

Owned files: `backend/statlab/api/projects.py`, `backend/statlab/mcp_client.py`,
`tests/test_api_projects.py`, `tests/test_mcp_client.py`.

### Recipe

1. `mcp_client.py`: new public `async def list_estimators(self) -> ToolSuccess` (docstring
   "Dispatch the read-only registry listing for the UI without run-scoped accounting."):
   `self._session is None` -> `EngineError("MCP client is not entered")`;
   `spec = self.tools.get("list_estimators")`; if `spec is None or spec.phase != "training" or spec.heavy or spec.transform_only`
   -> `EngineError("list_estimators registry metadata is invalid")`;
   `validated = await self._dispatch_direct(spec, {}, read_timeout_seconds=60)`; if
   `validated.artifacts` -> `ToolCallError("list_estimators", Failure(error_class="bug", message="registry tool returned unexpected artifacts"))`;
   return `validated`.
2. `projects.py`
   - Module constant `_PROFILE_KEYS: tuple[str, ...] = ("rows", "cols", "columns", "dtypes", "null_counts", "n_unique", "roles", "role_evidence", "all_null")`
     and function `def _profile(summary: Mapping[str, JsonValue]) -> dict[str, JsonValue]` returning
     `{key: summary.get(key) for key in _PROFILE_KEYS}`. `ProjectsService.propose` builds its
     `profile` with `_profile(summary)` instead of its inline comprehension (same keys, same order).
   - New `ProjectsService.profile(self, project_id: str, dataset_id: str, options: DatasetOptions) -> dict[str, JsonValue]`:
     `row = self.dataset(project_id, dataset_id)` (existing 404s); `project = self.project(project_id)`;
     `result = await self.resources.mcp.setup_dataset(project=project.slug, dataset=row.sha256, options=options)`;
     return `_profile(result.summary)`. (async method.)
   - Routes in `projects_router`:
     a. `GET /api/projects/{project_id}/datasets/{dataset_id}/profile` with
        `options: Annotated[DatasetOptions, Query()] = _DEFAULT_OPTIONS` (same pattern as
        `upload_dataset`) -> `dict[str, JsonValue]`: `await ProjectsService(resources()).profile(...)`.
     b. `GET /api/vocabulary` -> `dict[str, JsonValue]`: `resource = resources()`;
        `registry = await resource.mcp.list_estimators()`; return
        `{"contract_schema": DataContract.model_json_schema(), "registry": registry.summary}`.

### Tests

`tests/test_api_projects.py` (use `_open_project_api`):
1. `test_dataset_profile_uses_options_and_scopes_project`: upload `data.csv`
   (`b"a,b\n1,2\n3,4\n"`); GET `/api/projects/{id}/datasets/{dataset_id}/profile?delimiter=%3B`
   -> 200; JSON has exactly the nine `_PROFILE_KEYS`; `columns == ["a", "b", "c"]` (SetupSession
   default summary); `session.calls[-1][0] == "setup_dataset"` and
   `session.calls[-1][1]["options"]["delimiter"] == ";"`. GET with dataset id `missing` -> 404.
   Create a second project (`slug "other"`) and GET its profile route with the first dataset id
   -> 404.
2. `test_vocabulary_lists_schema_and_registry`: `session.responses.append(ToolSuccess(summary={"ridge": {"task": "regression"}, "inferential_models": {"ols": {}}}))`;
   GET `/api/vocabulary` -> 200; `json["contract_schema"]["title"] == "DataContract"`;
   `json["registry"] == {"ridge": {"task": "regression"}, "inferential_models": {"ols": {}}}`;
   `session.calls[-1] == ("list_estimators", {}, 60)` (assert as three unpacked fields, like
   36b addendum B, to avoid mypy tuple narrowing).
`tests/test_mcp_client.py` (reuse `world`, `FakeSession`, `served_tool`, `settings`):
3. `test_list_estimators_dispatches_without_audit`: schema
   `{"type": "object", "properties": {"task": {"type": ["string", "null"]}}, "additionalProperties": False}`;
   `served_tool("list_estimators", schema, allow_in=["experiment_design"], phase="training")`;
   envelope summary `{"ridge": {}}`; result summary equals it; `session.calls` is exactly one call
   `("list_estimators", {}, 60)` (unpacked asserts); `tool_calls` count 0.
4. `test_list_estimators_guards`: not entered -> `EngineError("MCP client is not entered")`;
   `FakeSession(())` -> `EngineError`; served with `phase="intake"` -> `EngineError` and no calls;
   envelope with an artifact (`ArtifactMeta(ref=new_artifact_ref(), kind="table")`) -> `ToolCallError`.

Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_api_projects.py tests/test_mcp_client.py tests/test_api_inspect.py'`;
Ruff check + format --check and mypy on the four owned files.

### 37a acceptance (2026-10-08)

Diff matches recipe. `make check` exit 0: 3073 passed, 1 designed skip; TOTAL 11625 stmts /
3334 branches 100%; mypy 190; Ruff clean; 199 formatted.

## Stage 37b — frontend data layer (types, client, routes, sparse contract draft, schema reader)

### Goal and non-goals

Pure TypeScript modules the 37c screens use; all unit-tested with `node --test`. No React
components, no styles, no new npm packages. Chunk 36 rules stay: runtime-validated parsers for
every response, fixed error messages, **never read error bodies**, abort signals passed through.

Decisions:
- Contracts are edited as a *sparse* JSON object (`JsonObject`): a field is present only when the
  user (or an uploaded/proposed contract) set it. Unsetting removes the key and prunes parent
  objects left empty. Backend explicitness (`model_fields_set`) therefore round-trips.
- Forms are driven by the backend contract JSON schema (`GET /api/vocabulary`), so every contract
  field is editable without hard-coded enum copies.
- Hash routes, no router package.

Owned files: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, new
`frontend/src/routes.ts`, new `frontend/src/contract/draft.ts`, new
`frontend/src/contract/schema.ts`, `frontend/tests/client.test.ts`, new
`frontend/tests/routes.test.ts`, new `frontend/tests/draft.test.ts`, new
`frontend/tests/schema.test.ts`, `frontend/package.json` (test script only).

### types.ts

Add (exported):
- `RunSummary` gains `contract_id: string`, `champion_model_id: string | null`,
  `config_json: string`, `test_scored_at: string | null`, `test_fingerprint: string | null`.
- `DatasetRecord = { id: string; project_id: string; filename: string; sha256: string; n_rows: number; n_cols: number; created_at: string }`.
- `ContractRecord = { id: string; project_id: string; version: number; created_at: string; contract: JsonObject }`.
- `MemoryEntry = { id: string; key: string; value: JsonValue; source_run_id: string | null; ts: string }`.
- `ProjectDetail = { project: ProjectSummary; runs: RunSummary[]; memory: MemoryEntry[] }`.
- `DatasetProfile = { rows: number; cols: number; columns: string[]; dtypes: Record<string, string>; null_counts: Record<string, number>; n_unique: Record<string, number>; roles: Record<string, string>; role_evidence: Record<string, string>; all_null: string[] }`.
- `EstimatorInfo = { name: string; task: string }`; `Vocabulary = { contractSchema: JsonObject; estimators: EstimatorInfo[]; inferentialModels: string[] }`.
- `SetupValidation = { valid: boolean; mismatches: JsonObject[]; warnings: string[] }`.
- `ContractProposal = { contract: JsonObject; reasoning: string }`.

### client.ts

1. Request core: replace `requestJson` with
   `async function request(path: string, options: { method: 'GET' | 'POST'; json?: JsonValue; form?: FormData; signal?: AbortSignal | undefined }): Promise<unknown>`.
   Headers always `Accept: application/json`; with `json`: body `JSON.stringify(json)` and
   `Content-Type: application/json`; with `form`: body = the FormData, no Content-Type header.
   Same network/abort/invalid-JSON handling as today. Status messages (body never read):
   403 `'Locked until the test score is recorded.'`, 404 `'Resource not found.'`,
   409 `'Run transition rejected.'`, 410 `'Artifact was pruned.'`, 413 `'Upload is too large.'`,
   422 `'Invalid request.'`, 502 `'The agent could not produce a valid answer.'`,
   503 `'Backend is unavailable.'`, else `'Backend request failed.'`. `listProjects` and
   `getRun` call `request(path, { method: 'GET', signal })`.
2. Small parse helpers (private): `requiredNumber(object, key)` (finite number else invalid),
   `nullableNumber`, `stringArray(value)`, `stringRecord(value)`, `numberRecord(value)`; all throw
   `invalidResponse()` on mismatch. `parseRunSnapshot` and a new `parseRun(value: JsonObject): RunSummary`
   share the run-field parsing (the snapshot additionally checks the run id).
3. Exported parsers: `parseProjectDetail`, `parseDatasets` (array of `DatasetRecord`; ignores
   `path`), `parseContracts` (array; each `contract_json` string is `JSON.parse`d and must be a
   JSON object, else invalid), `parseContractRow` (one), `parseProfile`, `parseVocabulary`
   (`contract_schema` must be an object; `registry` object: every key other than
   `inferential_models` whose value is an object with string `task` becomes an `EstimatorInfo`,
   sorted by name; other keys ignored; `inferentialModels` = sorted keys of
   `registry.inferential_models` when it is an object, else `[]`), `parseValidation`,
   `parseProposal`, `parseRunAccepted` (returns `run_id` string), `parseDatasetRecord` (one).
4. `export function datasetQuery(options: JsonObject): string`: `URLSearchParams`; for each key in
   insertion order: string array -> append each element; string/number/boolean -> append
   `String(value)`; anything else skipped. Return `''` when empty, else `'?' + params.toString()`.
5. Exported API functions (ids always `encodeURIComponent`ed):
   - `createProject(slug: string, name: string): Promise<ProjectSummary>` — POST `/api/projects` json `{slug, name}`; parse with the existing single-project field rules.
   - `getProject(projectId, signal?)` — GET `/api/projects/{id}` -> `parseProjectDetail`.
   - `listDatasets(projectId, signal?)`, `listContracts(projectId, signal?)`.
   - `uploadDataset(projectId, file: Blob, filename: string, options: JsonObject)` — POST
     `/api/projects/{id}/datasets` + `datasetQuery(options)`, form field `file` (`form.append('file', file, filename)`) -> `parseDatasetRecord`.
   - `getDatasetProfile(projectId, datasetId, options: JsonObject, signal?)` — GET
     `/api/projects/{id}/datasets/{datasetId}/profile` + `datasetQuery(options)`.
   - `uploadContract(projectId, contract: JsonObject)` — POST `/api/projects/{id}/contracts`,
     form field `file` = `new Blob([JSON.stringify(contract)], { type: 'application/json' })`
     with filename `contract.json` -> `parseContractRow`.
   - `validateContract(projectId, datasetId, contract)` — POST `.../contracts/validate` json
     `{dataset_id, contract}` -> `parseValidation`.
   - `proposeContract(projectId, datasetId, contract)` — POST `.../contracts/propose` same body
     -> `parseProposal`.
   - `getVocabulary(signal?)` — GET `/api/vocabulary`.
   - `createRun(projectId, mode: RunMode, datasetId: string, contractId: string): Promise<string>` —
     POST `/api/projects/{id}/runs` json `{mode, dataset_ids: [datasetId], contract_id: contractId}`.

### routes.ts

`export type Route = { name: 'projects' } | { name: 'project'; projectId: string } | { name: 'setup'; projectId: string } | { name: 'run'; runId: string } | { name: 'notFound' }`.
`parseRoute(hash: string): Route`: drop one leading `#`; split on `/` after dropping one leading
`/`; segments `[]` or `['']` -> projects; `['projects', id]` -> project; `['projects', id, 'new']`
-> setup; `['runs', id]` -> run; each id `decodeURIComponent` (a thrown `URIError` -> notFound)
and must be non-empty; anything else -> notFound. `formatRoute(route): string`: projects and
notFound -> `'#/'`; project -> `'#/projects/' + enc(id)`; setup -> that + `'/new'`; run ->
`'#/runs/' + enc(id)`.

### contract/draft.ts

`export type Path = readonly (string | number)[]`.
- `getAt(value: JsonValue | undefined, path: Path): JsonValue | undefined` — walks objects by
  string keys and arrays by number keys; any mismatch -> `undefined`.
- `setAt(root: JsonObject, path: Path, value: JsonValue): JsonObject` — immutable copy along the
  path; a missing/non-container intermediate becomes `{}` when the next key is a string and `[]`
  when it is a number. Empty path returns `root` unchanged.
- `unsetAt(root: JsonObject, path: Path): JsonObject` — removes the final key (object) or splices
  the index (array); then walks back up and removes any *object* left with no keys (never
  removes `root`; arrays are not pruned). Missing path -> structurally equal copy.
- `listWith(root, path, item: string)`: current value a string array (or absent = `[]`); append
  `item` if absent; `setAt`. `listWithout(root, path, item)`: remove all equal items; if the
  result is empty -> `unsetAt(root, path)`, else `setAt`.
- `export type ColumnRow = { name: string; dtype: string; inferredRole: string | null; role: string | null; isTarget: boolean; included: boolean; isId: boolean; nulls: number | null; unique: number | null }`.
  `columnRows(profile: DatasetProfile, draft: JsonObject): ColumnRow[]` in `profile.columns`
  order: `role` = string at `features.roles[name]` else null; `inferredRole` =
  `profile.roles[name] ?? null`; `isTarget` = `target.column === name`; `isId` =
  `features.id_columns` contains name; `included` = (`features.include` is an array ? it
  contains name : true) and not `features.exclude` contains name; `nulls`/`unique` from profile
  records or null; `dtype` = `profile.dtypes[name] ?? ''`.
- `setTarget(draft, column)`: `setAt(['target','column'], column)` then `listWithout` on
  `features.exclude`, `features.include`, `features.id_columns` for that column.
- `setRole(draft, column, role: string | null)`: null -> `unsetAt(['features','roles',column])`;
  else `setAt`.
- `setIncluded(draft, column, included: boolean)`: true -> `listWithout(features.exclude)` then,
  if `features.include` is an array, `listWith(features.include)`; false -> `listWith(features.exclude)`
  then `listWithout(features.include)`.
- `setIdColumn(draft, column, isId: boolean)`: `listWith`/`listWithout` on `features.id_columns`.
- `parseContractText(text: string): { ok: true; contract: JsonObject } | { ok: false; error: string }`:
  `JSON.parse` failure -> error `'Not valid JSON.'`; non-object or failing `isJsonObject` ->
  `'The contract must be a JSON object.'`; missing string `target.column` ->
  `'The contract needs target.column.'`.

### contract/schema.ts

`export type FieldKind = 'enum' | 'boolean' | 'integer' | 'number' | 'string' | 'string-list' | 'enum-list' | 'enum-map' | 'list-map' | 'string-map' | 'object' | 'object-list' | 'json'`.
`export type FieldSpec = { key: string; title: string; kind: FieldKind; nullable: boolean; options: string[]; defaultValue: JsonValue | undefined; ref: string | null }`.
- `resolveDef(schema: JsonObject, name: string): JsonObject | null` — `schema.$defs[name]` when an object.
- `refName(value: JsonObject): string | null` — `'#/$defs/X'` -> `'X'`.
- `classify(member: JsonObject): { kind: FieldKind; options: string[]; ref: string | null }`:
  `$ref` -> object/ref; `enum` string array -> enum/options; `type` boolean/integer/number/string
  -> that kind; `type` array: `items.$ref` -> object-list/ref; `items.enum` -> enum-list;
  `items.anyOf` whose members all have string `enum` -> enum-list with concatenated options;
  `items.type == 'string'` -> string-list; else json. `type` object with
  `additionalProperties`: `enum` -> enum-map/options; `type: 'array'` -> list-map;
  `type: 'string'` -> string-map; else json. Anything else -> json.
- `fieldsOf(schema: JsonObject, defName: string | null): FieldSpec[]` — properties of the root
  (null) or of `$defs[defName]`, in schema order; skip properties with `const`; `title` =
  property `title` string or key; `defaultValue` = property `default` (undefined if absent).
  With `anyOf`: `nullable` = some member `{type:'null'}`; non-null members: all have string
  `enum` -> kind enum with concatenated options; exactly one -> `classify` it; otherwise json.
  Without `anyOf`: `nullable` false, `classify(property)`.

### Tests

`package.json` `test` script: `node --test tests/client.test.ts tests/events.test.ts tests/routes.test.ts tests/draft.test.ts tests/schema.test.ts`.
- `client.test.ts`: update the snapshot fixture run with the five new run fields (values
  `'c1'`, `null`, `'{}'`, `null`, `null`); add one invalid-snapshot case with `config_json: 1`.
  Extend the fixed-message test's table with 403, 410, 413, 502 and their messages. New tests,
  each with a fetch stub recording `(input, init)`:
  (a) `createProject` POSTs JSON `{slug, name}` with `Content-Type: application/json`;
  (b) `uploadDataset` sends POST to `/api/projects/p1/datasets?delimiter=%3B&na_values=NA&na_values=-`
      for options `{delimiter: ';', na_values: ['NA', '-']}`, body is FormData whose `file` entry
      has name `data.csv`, no Content-Type header set by the client;
  (c) `uploadContract` form `file` is named `contract.json` and its text equals
      `JSON.stringify(contract)`;
  (d) `createRun` POSTs `{mode: 'guided', dataset_ids: ['d1'], contract_id: 'c1'}` and returns `'r1'`
      from `{run_id: 'r1'}`;
  (e) `datasetQuery({})` is `''`; `datasetQuery({header_row: 0, x: null})` is `'?header_row=0'`;
  (f) parsers: `parseContracts` parses `contract_json` and rejects a non-object JSON string and
      invalid JSON; `parseVocabulary` with registry `{ridge: {task: 'regression'}, lr: {task: 'classification'}, bad: 1, inferential_models: {ols: {}, logit: {}}}`
      yields estimators `[{name:'lr',…},{name:'ridge',…}]` and inferentialModels `['logit','ols']`;
      `parseProfile` rejects a non-string in `columns`; `parseProjectDetail` parses memory
      `value_json` `'{"a":1}'` to `{a: 1}` and rejects invalid JSON there.
- `routes.test.ts`: every route round-trips through `formatRoute`/`parseRoute`, including an id
  `'a/b c'`; `''`, `'#'`, `'#/'` -> projects; `'#/projects/'`, `'#/x'`, `'#/runs/a/b'`,
  `'#/projects/%E0%A4%A'` -> notFound.
- `draft.test.ts`: `setAt` creates nested objects/arrays and leaves the input unmodified;
  `unsetAt` prunes `{features: {roles: {a: 'numeric'}}}` to `{}` and keeps sibling keys; `listWith`
  is idempotent; `listWithout` removes the key when the list empties; with profile columns
  `['id','x','y']`: `setTarget(draft,'y')` on a draft excluding `y` removes it from exclude;
  `setIncluded(draft,'x',false)` then `true` returns to a draft equal to the start (`{target:{column:'y'}}`);
  `setIdColumn` toggles; `columnRows` reports `included` false for an excluded column and for a
  column missing from an explicit `include`; `parseContractText` returns each of its three errors
  and succeeds on `'{"target":{"column":"y"}}'`.
- `schema.test.ts`: a hand-written schema fixture containing: root `version` with `const`,
  `target` `$ref`, `notes` string; `$defs` with a nullable enum, a two-group metric `anyOf`, a
  nullable union (string/integer/boolean/null), an array of `$ref`, an array whose items are an
  `anyOf` of two enums, an `additionalProperties` enum map, an `additionalProperties` array map,
  a number with default, a boolean. Assert every `FieldSpec` (kind, nullable, options,
  defaultValue, ref) and that `version` is skipped.

Checks (from `frontend/`): `npm test`, `npm run typecheck`, `npm run build`; report genuine exits.

### 37b acceptance (2026-10-08)

Diff reviewed against recipe; added exported `parseProjectRow` (single project) is consistent.
Main reran from `frontend/`: `npm test` 26/26 pass, `npm run typecheck` exit 0, `npm run build`
exit 0 (retained dependency "use client" directive warning, as in B14).

## Stage 37c — setup screens (projects, project page, setup wizard)

### Goal and non-goals

React screens for PLAN §12 items 1–2 on top of 37b. Every setup-side backend feature is
reachable: create/list projects, project detail (runs, datasets, contracts, memory), CSV upload
with all `DatasetOptions`, column profile, four contract sources (write, agent proposal with
reasoning, saved contract, JSON file), the full contract editable (mapping table + schema form +
raw JSON), validation preview, mode choice, start. Non-goals: run workspace (38; the current
inspect panel moves to `RunPage` unchanged), comparison/report (39/40), new packages,
component tests (logic lives in tested pure modules), routing library.

### Design system (from the frontend-design pass; apply exactly)

Subject: a local statistical modelling lab bench; user = the analyst supervising an agent.
Tables over cards; colour only encodes state; one family for UI text, its mono sibling only
for code/JSON.
- Tokens on `:root` in `style.css`: `--paper: #F6F7F4` (page), `--surface: #FFFFFF` (panels),
  `--ink: #18232E` (text), `--graphite: #5A6672` (secondary text), `--rule: #D5DBD6` (borders),
  `--signal: #1F6F5C` (primary actions, focus ring), `--signal-ink: #FFFFFF`,
  `--gate: #B7791F` (awaiting the user), `--fault: #B3261E` (errors, locks),
  `--target: #E6F0EC` (target-row tint). Radius `--radius: 6px` on inputs/buttons only; panels
  use 1px `--rule` borders, radius 8px, no shadows. Spacing scale 4/8/12/16/24/32/48 px.
- Type: `index.html` loads Google Fonts `Atkinson Hyperlegible Next` (400, 600, 700) and
  `Atkinson Hyperlegible Mono` (400) via one `<link rel="stylesheet">` (plus the two
  `preconnect` links). `body` font: `"Atkinson Hyperlegible Next", system-ui, sans-serif`,
  16px, line-height 1.5; `code, pre, textarea.json`: `"Atkinson Hyperlegible Mono", ui-monospace, monospace`, 14px.
  Scale: h1 2rem/700, h2 1.25rem/700, h3 1rem/600. Numbers in tables: `font-variant-numeric: tabular-nums`.
  Sentence case everywhere; no all-caps labels; no eyebrow text.
- Layout: page column `max-width: 72rem`, left-aligned, 24px gutters (16px under 40rem).
  Top bar: product name "Statlab" (link to `#/`) and a breadcrumb (`Projects › <name> › New run`)
  separated by `›` characters; bottom border `--rule`.
- Controls: primary button = `--signal` background, `--signal-ink` text; secondary = surface
  with `--rule` border; disabled = 50% opacity, `cursor: not-allowed`. Focus: 3px `--signal`
  outline, 2px offset. Errors: `role="alert"`, `--fault` text with a 3px left border.
  Status pills (run status): text + 1px border, colour `--gate` for `awaiting_user`/`paused`,
  `--fault` for `failed`/`cancelled`, `--signal` for `completed`, `--graphite` otherwise.
- Motion: none except `prefers-reduced-motion`-guarded 120ms background transitions on buttons.
- Responsive: tables scroll horizontally inside an `overflow-x: auto` wrapper; the wizard's step
  list stacks above the content under 48rem.

### Owned files (all under `frontend/`)

`index.html`, `src/style.css` (rewrite), `src/App.tsx` (rewrite), new `src/useRoute.ts`,
new `src/ui/format.ts`, new `src/pages/ProjectsPage.tsx`, new `src/pages/ProjectPage.tsx`,
new `src/pages/RunPage.tsx`, new `src/setup/SetupWizard.tsx`, new `src/setup/DataStep.tsx`,
new `src/setup/SourceStep.tsx`, new `src/setup/EditStep.tsx`, new `src/setup/MappingTable.tsx`,
new `src/setup/SchemaForm.tsx`, new `src/setup/CheckStep.tsx`, new `src/setup/StartStep.tsx`,
new `tests/format.test.ts`, `package.json` (add `tests/format.test.ts` to the test script).

### Pure helpers — `src/ui/format.ts` (tested)

- `slugify(name: string): string`: lowercase; replace each run of characters outside
  `[a-z0-9]` with `-`; trim leading/trailing `-`; cut to 64 chars; trim a trailing `-` again.
- `isValidSlug(slug: string): boolean`: `/^[a-z0-9][a-z0-9_-]{0,63}$/`.
- `formatTime(iso: string | null): string`: null -> `'—'`; invalid date -> the input; else
  `new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })`.
- `lastActivity(createdAt: string, runs: RunSummary[]): string`: the maximum ISO string among
  `createdAt`, every run `started_at` and every non-null `ended_at` (string comparison; ISO UTC).
- `naValuesToText(values: string[]): string`: join with `\n`, mapping `''` to `(empty)`.
  `naValuesFromText(text: string): string[]`: split on `\n`, trim each line, drop lines that are
  empty after trimming, map `(empty)` to `''`, de-duplicate keeping first occurrence.
- `errorText(error: Error): string`: `ApiError` -> its message; else `'Request failed.'`.
- `pathKey(path: Path): string`: string segments joined with `.` (number segments dropped).
- `tests/format.test.ts`: slugify (`'Churn Study 2026!'` -> `'churn-study-2026'`, `'  --  '` -> `''`,
  a 100-char name -> length ≤ 64 without trailing `-`); isValidSlug true/false cases including
  `'-a'` and `'A'`; lastActivity picks an `ended_at` later than all starts; NA text round-trip of
  `['', 'NA', 'null']` and de-duplication; pathKey(`['objective','inference_goals',0,'predictors']`)
  -> `'objective.inference_goals.predictors'`; formatTime(null) -> `'—'`, invalid -> unchanged.

### Routing — `src/useRoute.ts`

`useRoute(): { route: Route; navigate: (route: Route) => void }`: state initialised from
`parseRoute(window.location.hash)`; `useEffect` adds a `hashchange` listener updating it (removed
on cleanup); `navigate` sets `window.location.hash = formatRoute(route)`.

### App shell — `src/App.tsx`

Renders `<header class="topbar">` (link "Statlab" to `#/`, then breadcrumb: on project/setup a
link to the project using the cached `['project', id]` query data name when available, else the
id; on setup also `New run`; on run `Run <runId>`), then `<main class="page">` switching on
`route.name`: projects -> `ProjectsPage`; project -> `ProjectPage`; setup -> `SetupWizard`; run
-> `RunPage`; notFound -> heading "Page not found" and a link "Go to projects". Each page receives
`navigate`. Query keys used across screens: `['projects']`, `['project', projectId]`,
`['datasets', projectId]`, `['contracts', projectId]`,
`['profile', projectId, datasetId, JSON.stringify(options)]`, `['vocabulary']`, `['run', runId]`.

### `src/pages/RunPage.tsx`

Move the current App "Inspect a run" snapshot + stream block into `RunPage({ runId })`, minus
the run-id form: it shows the snapshot facts and stream status for `runId`. Behavior otherwise
unchanged (38 replaces this page).

### `src/pages/ProjectsPage.tsx`

- Heading "Projects". A form "New project": label "Name" (text, required), label "Short name"
  (text; auto-filled with `slugify(name)` until the user edits it; help text "Lowercase letters,
  digits, - and _. Used for folder names."), button "Create project" (disabled while pending or
  when `!isValidSlug(slug)` or name blank). On submit: `createProject(slug, name.trim())`; success
  -> invalidate `['projects']`, `navigate({name:'project', projectId: created.id})`; failure ->
  alert with `errorText` (409/422 messages come from the client's fixed table).
- Table "Your projects" columns: Name (link to project), Short name, Runs, Last activity. Each
  row is a `ProjectListRow` component that runs `useQuery(['project', id], getProject)`; while
  loading show `…` in Runs/Last activity; on error show `—`. Runs = `runs.length`; Last activity
  = `formatTime(lastActivity(created_at, runs))`. Loading state "Loading projects…", error alert,
  empty state "No projects yet. Create one above to upload a dataset."

### `src/pages/ProjectPage.tsx`

`useQuery(['project', projectId])`, `['datasets', projectId]`, `['contracts', projectId]`.
- Header: h1 project name, secondary line short name; primary button "New run" -> setup route.
- Section "Runs" (table): Status (pill), Mode, Started, Ended, Forked from (link to run route or
  `—`), Champion (`Yes` when `champion_model_id` not null else `No`), Run (link "Open" to run
  route). Empty: "No runs yet. Start one with New run."
- Section "Datasets" (table): File, Rows, Columns, Uploaded. Empty: "No datasets yet. Upload a
  CSV when you start a run."
- Section "Contracts" (table): Version, Target (`contract.target.column` via `getAt`), Saved,
  and a `<details>` per row with summary "Show JSON" containing `<pre><code>` of
  `JSON.stringify(contract, null, 2)`. Empty: "No saved contracts."
- Section "Project memory" (table): Fact (`key`), Value (`<code>` of `JSON.stringify(value)`),
  From run (link or `—`), Recorded (`formatTime(ts)`). Empty: "Nothing remembered yet. Completed
  runs add what they learned here."
Each section shows its own loading text and error alert.

### Setup wizard — `src/setup/SetupWizard.tsx`

State (all in the wizard component, `useState`): `step: 1|2|3|4|5`, `datasetId: string | null`,
`datasetOptions: JsonObject` (sparse, `{}` initially), `draft: JsonObject | null`,
`reasoning: string | null`, `validation: SetupValidation | null`.
Queries: `['vocabulary']` (getVocabulary), `['project', projectId]`, profile query
`['profile', projectId, datasetId, JSON.stringify(datasetOptions)]` enabled when `datasetId`
is set.
Layout: two columns — an `<ol class="steps">` with five items "Choose data", "Choose a starting
contract", "Edit the contract", "Check against the data", "Start the run"; the current item has
`aria-current="step"`; earlier items are buttons returning to that step; later items are plain
text (not interactive). Right column renders the step component.
Transitions: Data -> Source requires a loaded profile. Source -> Edit sets `draft` (and
`reasoning` for agent proposals) and also applies `datasetOptions`: when non-empty
`draft = setAt(draft, ['dataset'], datasetOptions)`. Edit -> Check clears `validation`. Going back
to step 1 keeps `draft`; choosing a different dataset resets `draft`, `reasoning`, `validation`.
Vocabulary or profile errors show an alert with "Try again" (refetch).

### `src/setup/DataStep.tsx`

Props: `projectId`, `datasetId`, `options`, `onSelect(datasetId)`, `onOptions(options)`,
`profile` query result, `onContinue`.
- Radio list "Use a dataset you uploaded" from `['datasets', projectId]`: label
  `filename — rows × columns, uploaded <formatTime>`. (Hide the list when empty.)
- Fieldset "Upload a new CSV": file input (`accept=".csv,text/csv"`); a `<details>` "Parsing
  options" with: Delimiter (text, maxLength 4, placeholder `,`), Encoding (text, placeholder
  `utf-8`), Decimal mark (text, maxLength 1, placeholder `.`), Header row (number, min 0,
  placeholder `0`), Missing value markers (textarea, one per line, `(empty)` stands for an empty
  cell, placeholder `naValuesToText(['', 'NA', 'N/A', 'null', 'NULL'])`). Each input writes the
  sparse `options`: empty input -> `unsetAt`, else `setAt` (header row as integer; the markers via
  `naValuesFromText`, unset when the textarea is empty). These options apply to uploads and to the
  profile of the selected dataset.
  Button "Upload CSV" (disabled without a file or while pending): `uploadDataset(projectId, file, file.name, options)`;
  success -> invalidate `['datasets', projectId]`, `onSelect(row.id)`; failure -> alert.
- Profile panel when a dataset is selected: loading "Reading columns…"; error alert; success:
  "`rows` rows, `cols` columns" and a comma list of the first 12 column names (+ "and N more").
- Button "Continue" enabled only when the profile query succeeded.

### `src/setup/SourceStep.tsx`

Props: `projectId`, `datasetId`, `profile: DatasetProfile`, `onDraft(draft, reasoning | null)`.
Radio group "How do you want to start?" with four options and one-line descriptions:
1. "Write it myself" — "Pick the target column, then set the rest yourself."
2. "Let the agent draft it" — "The agent reads the column profile and proposes a full contract. Uses one LLM call."
3. "Reuse a saved contract" — "Start from a contract saved in this project."
4. "Upload a JSON file" — "Use a contract file you already have."
Below, the selected option's controls:
1/2: select "Target column" (profile columns; required). 1: button "Continue" ->
`onDraft({target: {column}}, null)`. 2: button "Draft with the agent" ->
`proposeContract(projectId, datasetId, {target: {column}})`; pending text "The agent is drafting
the contract…"; success -> `onDraft(proposal.contract, proposal.reasoning)`; failure -> alert.
3: radio list of `['contracts', projectId]` rows (`Version N — target <col>, saved <time>`); empty
text "No saved contracts in this project."; button "Use this contract" -> `onDraft(record.contract, null)`.
4: file input `accept=".json,application/json"`; on change read `file.text()` and run
`parseContractText`; error -> alert with its message; ok -> button "Use this file" enabled ->
`onDraft(contract, null)`.

### `src/setup/EditStep.tsx`

Props: `draft`, `onDraft`, `profile`, `vocabulary`, `reasoning`, `onBack`, `onContinue`.
- When `reasoning` is non-null: `<details open>` "Why the agent proposed this" with the text in a
  `<p>` (white-space: pre-wrap).
- Tabs (`role="tablist"`, buttons `role="tab"` with `aria-selected`, panel `role="tabpanel"`):
  "Columns", "Rules", "JSON".
- Columns tab: `MappingTable`. Rules tab: `SchemaForm` at the root. JSON tab: textarea
  (`class="json"`, 24 rows, spellcheck off) initialised with `JSON.stringify(draft, null, 2)`
  whenever the tab is opened; button "Apply JSON" -> `parseContractText`; error -> alert; ok ->
  `onDraft(contract)` and a status line "Applied.".
- Footer: "Back" (secondary) and "Check against the data" (primary; disabled when
  `target.column` is not a string).

### `src/setup/MappingTable.tsx`

Props: `draft`, `onDraft`, `profile`, `roleOptions: string[]` (from
`fieldsOf(schema, 'FeatureRules')` entry `roles`, its `options`).
Table caption "Columns"; columns: Target, Column, Type, Detected role, Role, Use, ID, Missing,
Distinct. Per `columnRows(profile, draft)` row:
- Target: radio `name="target"` checked when `isTarget`; change -> `onDraft(setTarget(draft, name))`.
- Column: name; Type: dtype; Detected role: `inferredRole ?? '—'`.
- Role: select: first option value `''` label `Detected (${inferredRole ?? 'unknown'})`, then
  `roleOptions`; change -> `setRole(draft, name, value === '' ? null : value)`; disabled for the target row.
- Use: checkbox `included`, disabled for the target row -> `setIncluded`.
- ID: checkbox `isId`, disabled for the target row -> `setIdColumn`.
- Missing: `nulls ?? '—'`; Distinct: `unique ?? '—'`. Target row class `is-target` (`--target` tint).
- Every control has an accessible name including the column (e.g. `aria-label={`Use ${name}`}`).

### `src/setup/SchemaForm.tsx`

`SchemaForm({ schema, defName, path, draft, onDraft, columns, vocabulary })` renders
`fieldsOf(schema, defName)`; root call uses `defName = null`, `path = []`.
Skips: at the root the keys `target` is rendered first, then the remaining keys in schema order;
within `FeatureRules` skip `include`, `exclude`, `id_columns`, `roles` (Columns tab owns them).
Each field: `fieldPath = [...path, field.key]`, `value = getAt(draft, fieldPath)`,
`key = pathKey(fieldPath)`; label = `field.title`; help line = `Default: <JSON of defaultValue>`
when `defaultValue !== undefined && defaultValue !== null`, else `Not set by default`.
Writes: `set(v)` = `onDraft(setAt(draft, fieldPath, v))`; `unset()` = `onDraft(unsetAt(draft, fieldPath))`.
Widgets by kind (special cases first, matched on `key`):
- `target.column`, `split.time_column`, `split.group_column`: select of `columns` with first
  option `''` "Not set" (`target.column` has no empty option) -> unset/set.
- `modelling.candidates`: checkbox list of `vocabulary.estimators` (label `name (task)`), only
  those whose `task` equals `getAt(draft, ['objective','task'])` when that is a string; none
  checked = unset; else set to the checked names in vocabulary order.
- `modelling.inferential_model`: select `''` "Chosen from the target type" + `vocabulary.inferentialModels`.
- keys ending in `.predictors`: checkbox list of `columns` (unset when none).
- enum: select `''` (`Default (<default>)` or "Not set") + options.
- boolean: select `''` (`Default (yes|no)` or "Not set"), `true` "Yes", `false` "No".
- integer / number: `<input type="number">` (`step="1"` / `step="any"`), placeholder default;
  local text state; on blur: empty -> unset; `Number(text)` finite (and integer for integer) ->
  set; else inline error "Enter a whole number." / "Enter a number." and no write.
- string: text input (key `notes` -> textarea 4 rows); on blur: empty -> unset; else set.
- string-list: text input of comma-separated values (shown joined with `, `); on blur: split on
  `,`, trim, drop empties; empty -> unset; else set.
- enum-list: checkbox group of options (none -> unset).
- enum-map / list-map / string-map (`features.ordinal_levels`, `preprocessing.missing.per_column`,
  and any other map): a table of entries (Column, Value, Remove button) plus an add row (column
  select of `columns` not yet used, value widget, "Add" button). Value widget: enum-map -> select of
  options; list-map -> comma text (split like string-list); string-map -> text. Removing the last
  entry unsets the map.
- object: `<fieldset><legend>{title}</legend>` + recursive `SchemaForm` with `defName = field.ref`
  and `path = fieldPath`.
- object-list (`objective.inference_goals`): each existing item as a fieldset "Goal N" with a
  recursive `SchemaForm` (`defName = field.ref`, `path = [...fieldPath, index]`) and a "Remove
  goal" button (`unsetAt` of the index; when the list becomes empty unset the list); button "Add
  goal" -> `set([...current, {}])`.
- json (e.g. `target.positive_class`): text input showing `JSON.stringify(value)` when set; on
  blur: empty -> unset; `JSON.parse` must succeed and give a string/number/boolean -> set; else
  inline error `Enter a value such as 1, true or "yes".`.
Every control has a `<label for>`; help lines are linked with `aria-describedby`.

### `src/setup/CheckStep.tsx`

Props: `projectId`, `datasetId`, `draft`, `validation`, `onValidation`, `onBack`, `onContinue`.
On mount and on "Check again": `validateContract(projectId, datasetId, draft)` (useMutation);
pending "Checking the contract against the data…"; failure -> alert.
Result: valid -> status line "The contract matches the data." (colour `--signal`); invalid ->
heading "Fix these before starting" and a list of mismatches: each item `<strong>{field}</strong> {message}`
using string `field`/`message` when present, else `<code>JSON.stringify(item)</code>`.
Warnings (if any) under "Warnings". Buttons: "Back to the contract", "Check again",
"Continue" (enabled only when `validation?.valid === true`).

### `src/setup/StartStep.tsx`

Props: `projectId`, `datasetId`, `datasetName`, `draft`, `navigate`.
- Summary list: Dataset (file name), Target (`target.column`), Task (`objective.task` or "The agent
  decides"), Primary metric (`objective.primary_metric` or "The agent decides").
- Radio group "How should the run proceed?": `guided` "Guided" — "The agent stops before each
  decision so you can approve, edit or redirect it." (default checked); `autonomous`
  "Autonomous" — "The agent runs to the end. You can still pause, step in or rewind."
- Button "Start run": `uploadContract(projectId, draft)` then
  `createRun(projectId, mode, datasetId, contract.id)`; pending text "Saving the contract and
  starting the run…"; success -> invalidate `['project', projectId]` and `['contracts', projectId]`,
  `navigate({name: 'run', runId})`; failure -> alert; the button stays disabled while pending.

### Checks

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build` (genuine exits, test counts).
Main then performs the browser click-through on the H9 offline stack (Playwright): create
project, upload `churn.csv`, all four contract sources, edit columns/rules/JSON, check, start a
guided run, land on the run page.
