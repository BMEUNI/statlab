---
status: completed
dependencies: [65]
completion_date: 2026-10-09
implementation_notes: "DELETE /api/projects/{id}: refuse with open runs, cascade rows, remove folder; delete button on the project page; make check 100%, npm 100/100"
---

# Chunk 66 — delete a project

User request 2026-10-09: "I cant delete projects I need that as well just a quick feature".

Findings (read-only):
- Schema (`backend/statlab/persistence/migrations/001_initial.sql`): datasets, contracts, runs,
  sessions and memory reference `projects(id) ON DELETE CASCADE`; every run child cascades
  from `runs`. The non-cascading references (`runs.contract_id`, `runs.champion_model_id`,
  `models.artifact_id`, `memory.source_run_id`) all point at rows deleted by the same
  statement, so SQLite's end-of-statement check passes. Probe on a scratch copy of the
  offline suite database (`integration/out/20261009T135816Z-offline/statlab.db`, foreign keys
  on): deleting project `s09` (parent and fork child runs, champion models, memory rows with
  `source_run_id`) succeeded and `PRAGMA foreign_key_check` returned nothing.
- Files live under `ProjectPaths(settings.data_dir, slug).root`
  (`backend/statlab/api/projects.py` `_project_paths` ~438).
- Rename (`projects.py` ~522) is the pattern: `ProjectsService.project` (404),
  `service.paths`, `has_open_runs` -> 409.
- `RunRegistry.bindings` may keep closed runs of the deleted project in memory; every run
  endpoint reads `get_run` first and returns 404 for a missing row, so the stale entries are
  harmless (memory only until restart).

Decisions:
1. `DELETE /api/projects/{project_id}` -> 204. Missing project 404. Any open run (pending,
   running, awaiting_user, paused) -> 409 "stop or finish this project's open runs before
   deleting it". Then one transaction deletes the project row (cascade), then the folder is
   removed. A folder that is already missing is fine. A folder that cannot be removed -> 500
   "project was deleted but its folder could not be removed" (the rows are gone).
2. UI: a trash icon button next to the rename button on the project page, with a browser
   confirm; on success go to the projects list. 409 shows a plain sentence.
Non-goals: delete from the projects list, undo/trash, deleting single runs or datasets,
evicting registry bindings.

## Stage 66a (detailed spec)

Owned: `backend/statlab/persistence/repository.py`, `backend/statlab/api/projects.py`,
`tests/test_api_projects.py`, `frontend/src/api/client.ts`, `frontend/src/pages/ProjectPage.tsx`,
`frontend/tests/client.test.ts`. Not alone in the tree: preserve others' edits.

### Backend

1. `repository.py`, directly after `rename_project`: `def delete_project(conn:
   sqlite3.Connection, project_id: str) -> None:` with docstring "Delete a project; its
   datasets, contracts, runs, sessions and memory go with it (ON DELETE CASCADE, chunk 66)."
   Body: `with transaction(conn): conn.execute("DELETE FROM projects WHERE id = ?",
   (project_id,))`.
2. `projects.py`: add `import shutil` (stdlib block); add `delete_project` to the existing
   `from statlab.persistence.repository import (...)` list. In `projects_router`, directly
   after the `rename` route, add:
   `@router.delete("/api/projects/{project_id}", status_code=204)` `async def
   delete(project_id: str) -> Response:` —
   1. `resource = resources()`; `service = ProjectsService(resource)`; `project =
      service.project(project_id)`; `paths = service.paths(project)`.
   2. If `has_open_runs(resource.conn, project.id)`: raise `HTTPException(409, "stop or
      finish this project's open runs before deleting it")`.
   3. `delete_project(resource.conn, project.id)`.
   4. `shutil.rmtree(paths.root, ignore_errors=True)`; if `os.path.lexists(paths.root)`:
      raise `HTTPException(500, "project was deleted but its folder could not be removed")`.
   5. `return Response(status_code=204)`.
   (`Response` is already imported from fastapi.)

### Backend tests (`tests/test_api_projects.py`, after the rename tests)

Use `_open_project_api` (project "Demo", slug `demo`), `_seed_run`, `_write`, `ProjectPaths`.
1. `test_delete_project_removes_rows_and_folder`: upload a dataset as in
   `test_rename_project_moves_folder_and_paths`; `root = ProjectPaths(settings.data_dir,
   "demo").root`; `_seed_run(settings.db_path, str(project["id"]), root, "completed")`;
   `_write(settings.db_path, "INSERT INTO memory (id, project_id, key, value_json,
   source_run_id, ts) VALUES (?, ?, ?, ?, ?, ?)", ("m_seed", str(project["id"]), "k", "{}",
   "r_seed", "2026-01-01T00:00:00Z"))`; create another project via `POST /api/projects`
   `{"slug": "other", "name": "Other"}` (201). `DELETE /api/projects/{id}` -> 204 and
   `response.content == b""`. Assert `not root.exists()`, `ProjectPaths(settings.data_dir,
   "other").root.is_dir()`, `GET /api/projects/{id}` -> 404, `GET /api/projects` names ==
   `["Other"]`, and with a `sqlite3.connect(settings.db_path)` connection (closed in
   `finally`) `SELECT count(*) FROM <t>` is 0 for each of `datasets`, `contracts`, `runs`,
   `artifacts`, `memory`.
2. `test_delete_project_refused_with_open_run`: `_seed_run(..., "running")`; DELETE -> 409
   with detail `"stop or finish this project's open runs before deleting it"`; root
   `is_dir()`; `GET /api/projects/{id}` -> 200.
3. `test_delete_missing_project_returns_404`: `DELETE /api/projects/p_doesnotexist` -> 404.
4. `test_delete_project_with_missing_folder`: `shutil.rmtree(root)` (import `shutil` in the
   test module), then DELETE -> 204; `GET /api/projects/{id}` -> 404.
5. `test_delete_project_folder_failure`: `settings.data_dir.chmod(0o500)`; in `try` DELETE,
   `finally` `settings.data_dir.chmod(0o700)`; assert 500 with detail `"project was deleted
   but its folder could not be removed"`; `GET /api/projects/{id}` -> 404.

### Frontend

1. `client.ts`: `RequestOptions.method` becomes `'GET' | 'POST' | 'PATCH' | 'DELETE'`. After
   `renameProject` add `export async function deleteProject(projectId: string):
   Promise<void> { await request(projectPath(projectId), { method: 'DELETE', text: true }); }`
   (`text: true` reads the empty 204 body without JSON parsing).
2. `ProjectPage.tsx`:
   - Imports: `Trash` next to `Pencil` from `lucide-react`; `deleteProject` in the
     `../api/client.ts` import.
   - After the `rename` mutation: `const remove = useMutation({ mutationFn: () =>
     deleteProject(projectId), onSuccess: async () => { queryClient.removeQueries({ queryKey:
     ['project', projectId] }); await queryClient.invalidateQueries({ queryKey: ['projects']
     }); navigate({ name: 'projects' }); } });`
   - In the `title-row` div, after the rename button: a `<button type="button"
     className="icon-button" aria-label="Delete project" title="Delete project"
     disabled={remove.isPending} onClick={() => { if (window.confirm(`Delete
     "${project.data.project.name}" with all its runs, datasets and files? This cannot be
     undone.`)) remove.mutate(); }}>` containing `<Trash size={18} aria-hidden="true" />`.
   - Directly after `<p className="secondary-line">{project.data.project.slug}</p>`:
     `{remove.isError && (<p role="alert">{remove.error instanceof ApiError &&
     remove.error.status === 409 ? 'Stop or finish this project’s open runs before deleting
     it.' : errorText(remove.error)}</p>)}`.
3. `frontend/tests/client.test.ts`: add `deleteProject` to the import list. New test
   `test('deleteProject sends DELETE', ...)`: replace `globalThis.fetch` (restore in
   `finally`, as `withFetch` does) with one recording the request and returning `new
   Response(null, { status: 204 })`; assert `await deleteProject('p1')` resolves to
   `undefined`, the input is `'/api/projects/p1'` and the method is `'DELETE'`.

### Checks (implementer)

`make test-fast TESTS="tests/test_api_projects.py"`; Ruff check, Ruff format --check and mypy
on `backend/statlab/persistence/repository.py backend/statlab/api/projects.py
tests/test_api_projects.py`; from `frontend/`: `npm test`, `npm run typecheck`,
`npm run build`. Stop and report on any failure or gap; do not work around it.

Main then: review, `make check`, commit.

### Addendum 66a-A (Ruff S608 in the row-count assertion)

An f-string table name trips S608. Replace the loop in
`test_delete_project_removes_rows_and_folder` with one literal query:
`connection.execute("SELECT (SELECT count(*) FROM datasets), (SELECT count(*) FROM contracts),
(SELECT count(*) FROM runs), (SELECT count(*) FROM artifacts), (SELECT count(*) FROM
memory)").fetchone()` and assert it equals `(0, 0, 0, 0, 0)`. No suppression.

## Acceptance receipt (2026-10-09)

Implementer (after addendum A and a spacing fix): `tests/test_api_projects.py` exit 0; Ruff
check/format and mypy on the three Python files exit 0; `npm test`, typecheck, build exit 0.
Main reviewed the diff against the recipe (404/409 before any change, one-transaction
cascade, folder removal with the leftover check, confirm before the call, 409 sentence).
`make check` (`check66.log`, temporary) exit 0: coverage 100 % (TOTAL 12579 stmts, 0
missed), mypy 200 files, Ruff clean; `npm test` 100/100, build 0.
