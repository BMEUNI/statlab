---
status: completed
dependencies: [55]
completion_date: 2026-10-09
implementation_notes: "PATCH /api/projects/{id} renames; pencil button inline form; make check 100%, npm 96/96"
---

# Chunk 56 — rename a project

User request 2026-10-09: "implement feature so I can rename projects".

Decision: rename changes the display `name` only. The `slug` stays, because it names the
project folder (`ProjectPaths(data_dir, slug)`) and every stored path. New endpoint
`PATCH /api/projects/{project_id}` with body `{"name": str}` (same rules as on create:
1-200 chars, not whitespace only, no extra fields). It updates the `projects` row and rewrites
the project's `project.json` mirror (written on create, read by nothing today) so the two
agree. The project page gets a pencil button next to the title that turns it into an inline
form. Non-goals: slug change, renaming from the chat, rename history.

## Stage 56a — backend

Owned: `backend/statlab/persistence/repository.py`, `backend/statlab/api/projects.py`,
`tests/test_api_projects.py`. Preserve other edits.

1. `repository.py`, after `list_projects`:
   `def rename_project(conn: sqlite3.Connection, project_id: str, name: str) -> ProjectRow:`
   inside `with transaction(conn):` run `UPDATE projects SET name = ? WHERE id = ?` with
   `(name, project_id)`; then `return require_project(conn, project_id)`.
2. `projects.py`:
   - Add `AfterValidator` to the pydantic import. Module-level, above `ProjectCreate`:
     `def _not_blank(value: str) -> str:` raising `ValueError("name must not be whitespace
     only")` when `not value.strip()`, else returning `value`; and
     `ProjectName = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(_not_blank)]`.
   - `ProjectCreate.name: ProjectName`; delete its `_name_not_whitespace` method. Remove
     `field_validator` from the import only if Ruff reports it unused.
   - New `class ProjectRename(BaseModel)`: `model_config = ConfigDict(frozen=True,
     extra="forbid")`, `name: ProjectName`.
   - Import `rename_project` in the repository import block (alphabetical).
   - In `projects_router`, right after `detail`:
     `@router.patch("/api/projects/{project_id}", response_model=ProjectRow)`
     `async def rename(project_id: str, body: ProjectRename) -> ProjectRow:`
     `resource = resources()`; `service = ProjectsService(resource)`;
     `project = service.project(project_id)` (404 "project not found" as today);
     `paths = service.paths(project)`; `row = rename_project(resource.conn, project.id,
     body.name)`; then rewrite `project.json`: `fd, tmp_name = tempfile.mkstemp(dir=paths.root)`,
     write `row.model_dump_json().encode("utf-8")` through `os.fdopen(fd, "wb")`,
     `os.replace(tmp_name, paths.project_json)`, with `Path(tmp_name).unlink(missing_ok=True)`
     in `finally`. Wrap the whole write (including `mkstemp`) in `try/except OSError` raising
     `HTTPException(500, "project was renamed but metadata could not be written") from None`.
     Return `row`.
3. Tests in `tests/test_api_projects.py`, after
   `test_project_creation_conflicts_and_metadata_write_failure`, using `_open_project_api`
   (it yields the `demo` project dict):
   - `test_rename_project_updates_row_list_and_metadata`: PATCH
     `/api/projects/{project["id"]}` with `{"name": "Churn study"}` -> 200; body `name ==
     "Churn study"`, `slug == "demo"`, `id` and `created_at` unchanged; GET `/api/projects`
     lists the new name; `json.loads(ProjectPaths(settings.data_dir, "demo").project_json
     .read_text())["name"] == "Churn study"`; no leftover temp file: the set of names in the
     project root equals the set before the PATCH.
   - `test_rename_project_rejects_bad_requests` (parametrized ids `blank`, `long`, `slug`,
     `missing`): bodies `{"name": "   "}`, `{"name": "x" * 201}`, `{"name": "A", "slug":
     "other"}` -> 422; PATCH `/api/projects/p_doesnotexist` with `{"name": "A"}` -> 404.
     For the 422 cases also assert the stored name is unchanged (GET detail).
   - `test_rename_project_metadata_write_failure`: like the create failure test,
     `ProjectPaths(settings.data_dir, "demo").root.chmod(0o500)`, PATCH, restore `0o700` in
     `finally`; assert 500 and detail `"project was renamed but metadata could not be
     written"`.

Checks: `make test-fast TESTS="tests/test_api_projects.py tests/test_persistence_repository.py"`
(if the second file does not exist, run only the first and say so); Ruff check, Ruff
format --check and mypy on the three owned files.

## Stage 56b — frontend (after 56a)

Owned: `frontend/src/api/client.ts`, `frontend/src/pages/ProjectPage.tsx`,
`frontend/src/style.css`, `frontend/tests/client.test.ts`. Preserve other edits.

1. `client.ts`: `RequestOptions.method` becomes `'GET' | 'POST' | 'PATCH'`. After
   `getProject`: `export async function renameProject(projectId: string, name: string):
   Promise<ProjectSummary>` returning `parseProjectRow(await request(projectPath(projectId),
   { method: 'PATCH', json: { name } }))`.
2. `ProjectPage.tsx`:
   - Imports: `useMutation`, `useQueryClient` from `@tanstack/react-query`; `Pencil` from
     `lucide-react`; `renameProject`; `type FormEvent` from react.
   - State: `const [renaming, setRenaming] = useState(false)`, `const [draftName,
     setDraftName] = useState('')`. `const queryClient = useQueryClient()`.
   - `const rename = useMutation({ mutationFn: (name: string) => renameProject(projectId,
     name), onSuccess: async () => { setRenaming(false); await Promise.all([
     queryClient.invalidateQueries({ queryKey: ['project', projectId] }),
     queryClient.invalidateQueries({ queryKey: ['projects'] })]); } })`.
   - `function submitRename(event: FormEvent<HTMLFormElement>)`: `preventDefault()`;
     `rename.mutate(draftName.trim())`.
   - In `.page-head`'s first `<div>`, replace `<h1>{...name}</h1>`:
     - not renaming: `<div className="title-row"><h1>{name}</h1><button type="button"
       className="icon-button" aria-label="Rename project" title="Rename project" onClick={()
       => { setDraftName(name); rename.reset(); setRenaming(true); }}><Pencil size={18}
       aria-hidden="true" /></button></div>`;
     - renaming: `<form className="rename-form" onSubmit={submitRename}><input type="text"
       aria-label="Project name" value={draftName} maxLength={200} autoFocus onChange={(e) =>
       setDraftName(e.target.value)} onKeyDown={(e) => { if (e.key === 'Escape')
       setRenaming(false); }} /><button type="submit" className="primary"
       disabled={rename.isPending || draftName.trim() === '' || draftName.trim() === name}>Save
       </button><button type="button" onClick={() => setRenaming(false)}>Cancel</button></form>`
       followed by `{rename.isError && <p role="alert">{errorText(rename.error)}</p>}`.
     (`name` = `project.data.project.name`.) The slug line stays.
3. `style.css` after `.page-head`: `.title-row { display: flex; align-items: center; gap: 8px; }`
   `.title-row h1 { margin: 0; }`
   `.rename-form { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }`
   `.rename-form input { font-size: 1.25rem; min-width: min(20rem, 100%); }`
4. `client.test.ts`: import `renameProject`; after `createProject POSTs JSON`, test
   `renameProject PATCHes the name`: `withFetch(project, ...)`; result deep-equals `project`;
   `input === '/api/projects/p1'`, `method === 'PATCH'`, body `JSON.stringify({ name: 'Sample' })`,
   Content-Type `application/json`.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: browser check on the offline stack (rename, cancel, Escape, blank disabled),
`make check`, commit.

### Addendum 56a-A (Ruff PTH105)

Use `Path(tmp_name).replace(paths.project_json)` instead of `os.replace(...)` (same
behaviour). No suppression.

## Acceptance receipt (2026-10-09)

56a implementer: `tests/test_api_projects.py` exit 0 (44 tests), Ruff/format/mypy on the three
files exit 0 after addendum A. 56b implementer: `npm test` 96/96, typecheck 0, build 0. Main
reviewed both diffs and ran `make check` exit 0: coverage 100 % (TOTAL 12174 stmts, 0
missed), mypy 197 files, Ruff clean, 206 files formatted (log `check56.log`, temporary).
Browser check on the offline stack: pencil opens the form, Save renames (title, project list
and `project.json` show the new name, slug `demo` unchanged), blank name disables Save,
Escape cancels without saving. Screenshots `.playwright-mcp/56-rename-form.png`,
`56-renamed.png`.
