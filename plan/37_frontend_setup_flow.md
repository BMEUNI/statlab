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
