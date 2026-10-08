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
