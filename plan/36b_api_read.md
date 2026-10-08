---
status: implemented
dependencies: [33, 34, 35]
completion_date: 2026-10-08
implementation_notes: "make check exit0: 3069 passed, 1 designed skip, TOTAL 11599 stmts/3328 branches 100%, mypy 190, Ruff clean, 199 formatted. Addenda A (proposal exclude_unset) and B (test fixture sparse answer; unpacked tuple assert for mypy 2.3) applied."
---

# Chunk36b — read API for the browser (inputs, executions, artifacts, run files)

## Goal and non-goals

User requirement (2026-10-08): every backend feature must be usable from the UI. PLAN §11 lists
`GET /api/runs/{id}/artifacts`, `GET /api/artifacts/{id}` (download/preview) and
`GET /api/runs/{id}/report`; chunks 34/35 deferred them and no chunk owns them. The browser also
needs read access to stored inputs (datasets, contracts) and to execution detail (decisions, tool
calls, LLM cost) for PLAN §12's "click to inspect decision + tool calls" and "live cost".

Adds read-only GET endpoints only. Non-goals: no new SSE event types (the UI refetches on
`node.*`/`gate.*` events), no `metrics` table writes (the table is unused; validation metrics
live in `RunState` branch results), no schema migration, no frontend, no new packages, no auth
change (D14 loopback stays), no change to existing endpoints' behavior.

Decisions:
- D32 at the UI boundary: an artifact with `derived_from_test` or `split == "test"` is *locked*
  (403) while its run's `test_scored_at` is null. After the run's single test scoring it is
  readable (the report already discloses the score).
- Report links stay relative: `report.md` is served at `/api/runs/{id}/files/report.md` and its
  `plots/<ref>.png` links resolve to `/api/runs/{id}/files/plots/<ref>.png`.
- Frame previews go through the MCP `describe_artifact` tool (backend imports no compute
  libraries). Dispatch outside a run reuses `setup_dataset`'s unaccounted path, extracted into
  one private method; no `tool_calls`/lineage rows are written for previews.
- Artifact files are located with `RunPaths.artifact(ref, kind)`, never the stored `path` column.

## Owned files

`backend/statlab/persistence/repository.py`, `backend/statlab/mcp_client.py`,
`backend/statlab/api/projects.py`, new `backend/statlab/api/inspect.py`,
`backend/statlab/main.py`, `tests/test_mcp_client.py`, new `tests/test_api_inspect.py`.

Reuse: `get_run`, `require_project`, `list_node_executions`, `_contract_row_from` (repository);
`storage.get_artifact`, `storage.list_artifacts`; `ProjectsService.paths` (slug validation);
`statlab_contracts.layout.RunPaths`; `statlab_contracts.artifacts.parse_artifact_ref`;
existing exception handlers in `main.py` (ToolCallError -> 422/503/500, PersistenceError -> 500).

## Recipe

1. `repository.py` — four functions, rows mapped exactly like the neighbouring getters
   (`DatasetRow.model_validate(dict(row))` as `get_dataset`; `_contract_row_from` as
   `get_contract`; `ToolCallRow`/`LlmCallRow.model_validate(dict(row))`):
   - `list_datasets(conn, project_id: str) -> tuple[DatasetRow, ...]`:
     `SELECT * FROM datasets WHERE project_id = ? ORDER BY created_at DESC, id DESC`.
   - `list_contracts(conn, project_id: str) -> tuple[ContractRow, ...]`: same order on
     `contracts`.
   - `list_tool_calls(conn, run_id: str) -> tuple[ToolCallRow, ...]`:
     `... FROM tool_calls WHERE run_id = ? ORDER BY ts, id`.
   - `list_llm_calls(conn, run_id: str) -> tuple[LlmCallRow, ...]`: same on `llm_calls`.
2. `mcp_client.py`
   - Extract private `async def _dispatch_direct(self, spec: ToolSpec, arguments: dict[str, JsonValue], *, read_timeout_seconds: int) -> ToolSuccess`
     holding `setup_dataset`'s current body from the strict-JSON `json.dumps(..., allow_nan=False)`
     check through `if isinstance(validated, ToolFailure): raise ToolCallError(...)` and
     `return validated`, with every literal `"setup_dataset"` tool name replaced by `spec.name`
     and the `900` timeout replaced by `read_timeout_seconds`. Behavior and messages unchanged.
   - `setup_dataset` keeps its entered/project/registry-metadata checks and argument building,
     then `validated = await self._dispatch_direct(spec, arguments, read_timeout_seconds=900)`,
     then its existing "setup tool returned unexpected artifacts" check, then returns.
   - New public
     `async def describe_artifact(self, *, project: str, run: str, artifact: str, kind: ArtifactKind, head_rows: int = 20) -> ToolSuccess`
     (docstring: "Dispatch the read-only describe tool for a UI preview without run-scoped
     accounting."): if `self._session is None` raise `EngineError("MCP client is not entered")`;
     if `get_project_by_slug(self.conn, project) is None` raise
     `EngineError("describe project does not exist")`; `spec = self.tools.get("describe_artifact")`;
     if `spec is None or spec.phase != "artifacts" or spec.allow_in != ("*",) or spec.heavy or spec.transform_only`
     raise `EngineError("describe_artifact registry metadata is invalid")`; arguments
     `{"project": project, "run": run, "artifact": artifact, "kind": kind, "head_rows": head_rows}`;
     `validated = await self._dispatch_direct(spec, arguments, read_timeout_seconds=60)`; if
     `validated.artifacts`: raise `ToolCallError("describe_artifact", Failure(error_class="bug", message="describe tool returned unexpected artifacts"))`;
     return `validated`.
3. `projects.py` (`projects_router`), two routes beside `detail`:
   - `GET /api/projects/{project_id}/datasets`, `response_model=tuple[DatasetRow, ...]`:
     `service.project(project_id)` (404 when unknown) then `list_datasets`.
   - `GET /api/projects/{project_id}/contracts`, `response_model=tuple[ContractRow, ...]`:
     same with `list_contracts`.
4. New `backend/statlab/api/inspect.py`:
   - Models (frozen, `extra="forbid"`): `RunExecutions(executions: tuple[NodeExecutionRow, ...], tool_calls: tuple[ToolCallRow, ...], llm_calls: tuple[LlmCallRow, ...], cost_usd: float)`;
     `ArtifactEntry(artifact: ArtifactRow, locked: bool)`.
   - Constants: `RUN_FILES: dict[str, tuple[tuple[str, ...], str]]` mapping public name to
     (path parts under the run root, media type): `"report.md": (("report", "report.md"), "text/markdown; charset=utf-8")`,
     `"pipeline.py": (("pipeline.py",), "text/x-python; charset=utf-8")`,
     `"manifest.json": (("manifest.json",), "application/json")`,
     `"comparison.json": (("comparison.json",), "application/json")`,
     `"contract.snapshot.json": (("contract.snapshot.json",), "application/json")`,
     `"skill.snapshot.md": (("skill.snapshot.md",), "text/markdown; charset=utf-8")`.
     `ARTIFACT_MEDIA: dict[ArtifactKind, str]`: `plot` -> `image/png`; `table`, `curve` ->
     `application/json`; `report` -> `text/markdown; charset=utf-8`; every other kind ->
     `application/octet-stream` (list all nine kinds explicitly). `PLOT_NAME = re.compile(r"^a_[0-9a-f]{16}\.png\Z")`.
   - `def _locked(artifact: ArtifactRow, run: RunRow) -> bool`: `(artifact.derived_from_test or artifact.split == "test") and run.test_scored_at is None`.
   - `def _run(resource: APIResources, run_id: str) -> tuple[RunRow, RunPaths]`: `run = get_run(conn, run_id)`;
     None -> `HTTPException(404, "run not found")`; `project = require_project(conn, run.project_id)`;
     `ProjectsService(resource).paths(project)` (slug validation, result unused);
     return `run, RunPaths(resource.settings.data_dir, project.slug, run.id)`.
   - `def _artifact(resource, artifact_id: str) -> tuple[ArtifactRow, RunPaths]`:
     `row = get_artifact(conn, artifact_id)`; None -> 404 `"artifact not found"`;
     `run, paths = _run(resource, row.run_id)`; `row.pruned_at is not None` -> 410
     `"artifact was pruned"`; `_locked(row, run)` -> 403
     `"test-derived artifact is locked until the test score is recorded"`; return `row, paths`.
   - `def inspect_router(resources: Callable[[], APIResources]) -> APIRouter` with routes:
     a. `GET /api/runs/{run_id}/executions` -> `RunExecutions`: `_run`; lists via
        `list_node_executions`, `list_tool_calls`, `list_llm_calls`;
        `cost_usd = math.fsum(call.cost_usd for call in llm_calls)`.
     b. `GET /api/runs/{run_id}/artifacts` -> `tuple[ArtifactEntry, ...]`: `_run`; rows
        `list_artifacts(conn, run.id, include_pruned=True)`; each
        `ArtifactEntry(artifact=row, locked=_locked(row, run))`.
     c. `GET /api/artifacts/{artifact_id}/preview` -> `dict[str, JsonValue]`: `_artifact`; kind
        not in (`dataframe`, `predictions`) -> 422 `"preview supports dataframe and predictions artifacts"`;
        `result = await resource.mcp.describe_artifact(project=paths.slug, run=row.run_id, artifact=row.id, kind=row.kind, head_rows=20)`;
        return `result.summary`.
     d. `GET /api/artifacts/{artifact_id}` -> `FileResponse`: `_artifact`;
        `path = paths.artifact(parse_artifact_ref(row.id), row.kind)`; not `path.is_file()` ->
        404 `"artifact file is missing"`; `FileResponse(path, media_type=ARTIFACT_MEDIA[row.kind])`.
        Register route c before d.
     e. `GET /api/runs/{run_id}/files/plots/{plot}` -> `FileResponse`: `_run`; `PLOT_NAME` no
        match -> 404 `"run file not found"`; `path = paths.report / "plots" / plot`; not a file
        -> same 404; `FileResponse(path, media_type="image/png")`.
     f. `GET /api/runs/{run_id}/files/{name}` -> `FileResponse`: `_run`; name not in
        `RUN_FILES` -> 404 `"run file not found"`; `path = paths.root.joinpath(*parts)`; not a
        file -> same 404; `FileResponse(path, media_type=media)`. Register e before f.
   Every handler calls `resource = resources()` first, like `projects_router`.
5. `main.py`: import `inspect_router` and `app.include_router(inspect_router(resources))`
   directly after the projects router.

## Tests

`tests/test_api_inspect.py` (new). Use `_open_project_api` and `SetupSession` from
`tests.test_api_projects` (project slug `demo`). Seed through a second connection
`connect(settings.db_path)` (from `statlab.persistence.db`), closed after seeding, with a helper
`_seed(settings: Settings, project_id: str) -> _Seed` (frozen dataclass: `run_id`,
`execution_id`, `plot`, `frame`, `test_frame`, `table`, `pruned`, `report_plot: str`):
- `create_contract(conn, project_id=..., path=settings.data_dir / "seed.json", contract=DataContract(target=TargetSpec(column="a")))`;
  `create_run(conn, project_id=..., contract_id=..., mode="autonomous", config_json="{}")`;
  `create_node_execution(conn, run_id=..., node_name="intake", seq=0)`;
  `record_tool_call(..., tool_name="load_csv", args_json='{"x":1}', result_json='{"status":"ok"}', duration_ms=5)`;
  two `record_llm_call(..., signature="s", model="m", prompt_tokens=1, completion_tokens=1, cached_tokens=0, cost_usd=0.25 / 0.5, duration_ms=10)`.
- `paths = RunPaths(settings.data_dir, "demo", run.id)`; `ensure_run_layout(paths)`. Five
  artifacts, each file written at `paths.artifact(ref, kind)` (create parent dirs) then
  `create_artifact(conn, ref=ref, run_id=run.id, kind=kind, path=that path, meta=ArtifactMeta(ref=ref, kind=kind, split=split), produced_by="seed", split=split)`:
  plot (`b"\x89PNG"`), frame `dataframe` (`b"PAR1"`), test_frame `dataframe` split `"test"`
  (`b"PAR1"`), table (`b'{"k": 1}'`), pruned `plot` then `mark_pruned(conn, (pruned,))`.
- Run files: `paths.pipeline_py` = `"print(1)\n"`; `report_plot = f"{new_artifact_ref()}.png"`;
  `paths.report / "plots" / report_plot` = `b"png"`; `paths.report / "report.md"` =
  `f"# Report\n![p](plots/{report_plot})\n"`.

Tests:
1. `test_project_dataset_and_contract_lists`: upload `data.csv` (`b"a,b\n1,2\n3,4\n"`, as in
   `test_proposal_partial_unresolved_mismatch_and_missing_target_paths`); GET datasets -> 200, one
   row with the uploaded id; after `_seed`, GET contracts -> 200 containing the seeded contract
   id; both routes with `/api/projects/missing/...` -> 404.
2. `test_run_executions_and_cost`: GET executions -> 200; one execution (id = seeded),
   `tool_calls[0]["tool_name"] == "load_csv"`, two llm calls, `cost_usd == 0.75`;
   `/api/runs/missing/executions` -> 404.
3. `test_artifact_list_lock_download_and_errors`: GET run artifacts -> 200 with all five ids;
   `locked` true only for `test_frame`. Downloads: plot 200 body `b"\x89PNG"` and content-type
   `image/png`; table content-type starts with `application/json`; frame content-type
   `application/octet-stream`; test_frame 403; pruned 410; `/api/artifacts/a_0000000000000000`
   404. Delete the frame file, GET -> 404. Then `claim_test_scoring(conn, run_id)` via a new
   connection (closed after): test_frame download 200 and the list shows it unlocked.
4. `test_artifact_preview_uses_describe_tool`: append
   `ToolSuccess(summary={"rows": 2, "cols": 1, "dtypes": {"a": "int64"}, "head": [{"a": 1}]})`
   to `session.responses`; GET `/api/artifacts/{frame}/preview` -> 200 with exactly that summary;
   `session.calls[-1] == ("describe_artifact", {"project": "demo", "run": run_id, "artifact": frame, "kind": "dataframe", "head_rows": 20}, 60)`.
   Plot preview -> 422 and no new session call; test_frame preview -> 403.
5. `test_run_files_and_report_plots`: `files/pipeline.py` 200 text `"print(1)\n"`, content-type
   starts with `text/x-python`; `files/report.md` 200 containing `f"plots/{report_plot}"`;
   `files/plots/{report_plot}` 200 body `b"png"`; `files/plots/x.png` 404;
   `files/manifest.json` 404 (absent); `files/secret.txt` 404; `/api/runs/missing/files/pipeline.py` 404.

`tests/test_mcp_client.py` (reuse `world`, `FakeSession`, `served_tool`, `settings`):
6. `test_describe_artifact_dispatches_without_audit`: schema with properties `project`, `run`,
   `artifact`, `kind` (strings) and `head_rows` (integer), all required,
   `additionalProperties: False`; `served_tool("describe_artifact", schema, allow_in=["*"], phase="artifacts")`;
   `session.envelope = ToolSuccess(summary={"rows": 1}).model_dump(mode="json")`; result summary
   `{"rows": 1}`; `session.calls == [("describe_artifact", {...exact arguments...}, 60)]`;
   `tool_calls` count 0.
7. `test_describe_artifact_guards`: not entered -> `EngineError("MCP client is not entered")`;
   entered with unknown project slug -> `EngineError`; `FakeSession(())` (tool missing) ->
   `EngineError`; tool served with `phase="intake"` -> `EngineError` and no calls; served
   correctly but envelope `ToolSuccess(artifacts=(ArtifactMeta(ref=new_artifact_ref(), kind="table"),))`
   -> `ToolCallError`. Existing setup_dataset tests stay unchanged and must pass.

Checks: `LOKY_MAX_CPU_COUNT=1 make test-fast TESTS='tests/test_api_inspect.py tests/test_api_projects.py tests/test_api_runs.py tests/test_mcp_client.py'`;
Ruff check + format --check and mypy on all seven owned files.

Acceptance: main reviews diff, runs `make check` once; receipt in this file's frontmatter and
`plan/receipts/integration-harness-offline.md`.

## Addendum A — proposal keeps field explicitness

The UI saves the proposed contract as-is, and explicitness is semantic (`model_fields_set`
decides e.g. whether `tuning.method` binds training; live S14). `propose_contract` currently
serializes every default. Fix: the `POST /api/projects/{project_id}/contracts/propose` route
decorator gains `response_model=ContractProposal, response_model_exclude_unset=True`.
Test (in `tests/test_api_projects.py::test_proposal_partial_unresolved_mismatch_and_missing_target_paths`):
after `success.status_code == 200`, assert `"constraints" not in success.json()["contract"]` and
`"preprocessing" not in success.json()["contract"]` (`_complete_contract()` sets neither) and
`success.json()["contract"]["split"]["seed"] == 7`. Same checks as the main recipe.

## Addendum B — review corrections

1. Addendum A test fixture: `tests/test_api_projects.py::_open_project_api`'s fake `complete`
   serializes `answer.model_dump(mode="json", exclude_unset=True)` (a real LLM reply contains only
   the fields it writes). Production decorator change stays as is.
2. mypy `unreachable` at `tests/test_api_inspect.py:228` (mypy 2.3 narrows the whole-tuple
   `session.calls[-1] == (...)` comparison): replace that tuple assertion with
   `name, arguments, timeout = session.calls[-1]` followed by three asserts: `name == "describe_artifact"`,
   `arguments == {...same dict...}`, `timeout == 60`. Everything else unchanged.
3. Rerun the full 36b check list (pytest selector incl. tests/test_api_projects.py, Ruff check,
   Ruff format --check, mypy on all owned files incl. tests/test_api_projects.py); report the
   final genuine exits. If mypy still reports unreachable, stop and report the exact output.
