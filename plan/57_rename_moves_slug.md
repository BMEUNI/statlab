---
status: completed
dependencies: [56]
completion_date: 2026-10-09
implementation_notes: "rename re-derives slug, moves folder, rewrites stored paths; refused with open runs; make check 100%, npm 96/96"
---

# Chunk 57 — renaming a project also renames its short name

User report 2026-10-09: after renaming `online-test-1` to "binary classification" the short
name stayed `online-test-1`; it should follow the name (lower case, `-` between words).

Facts (read-only check of the user's `statlab.db` and `projects/`): the slug is the project
folder (`ProjectPaths(data_dir, slug)`). Absolute paths under that folder are stored in
`datasets.path`, `contracts.path` and `artifacts.path`. `tool_calls.args_json` records
`"project": <slug>` as history only (replay re-injects the current slug,
`reproducibility.py` `_INJECTED_ARGS`). No run file embeds the slug. Every other reader
builds paths from `project.slug` at call time. An open run (pending, running, awaiting_user,
paused) holds live paths in memory, so its folder must not move.

Decision: the rename endpoint derives the new slug from the new name with the chat's rule
(`assistant.py` `_slug_base`: lower case, runs of non `[a-z0-9]` become `-`, trimmed, max 60,
fallback `project`; suffix `-2`, `-3`, ... when taken). A slug is taken when another project
row has it or a folder of that name exists in the data dir; the project's own current slug is
never taken. When the slug changes: refuse with 409 if the project has an open run; move the
folder; in one transaction update the project's name and slug and rewrite the path prefix of
its dataset, contract and artifact rows; when that transaction fails, move the folder back.
`tool_calls` history is left as is. Non-goals: renaming with open runs, editing the slug by
hand.

## Stage 57a — backend

Owned: `backend/statlab/persistence/repository.py`, `backend/statlab/api/projects.py`,
`backend/statlab/api/assistant.py`, `tests/test_api_projects.py`. Preserve other edits.

1. `repository.py`:
   - Replace `rename_project` with
     `def rename_project(conn: sqlite3.Connection, project_id: str, *, name: str, slug: str,
     old_root: str, new_root: str) -> ProjectRow:` inside one `with transaction(conn):` run, in
     order: `UPDATE projects SET name = ?, slug = ? WHERE id = ?`;
     `UPDATE datasets SET path = ? || substr(path, ?) WHERE project_id = ? AND substr(path, 1, ?) = ?`;
     the same for `contracts`; and
     `UPDATE artifacts SET path = ? || substr(path, ?) WHERE run_id IN (SELECT id FROM runs WHERE project_id = ?) AND substr(path, 1, ?) = ?`;
     each path statement with params `(new_root, len(old_root) + 1, project_id,
     len(old_root), old_root)`. Then `return require_project(conn, project_id)`. Docstring:
     "Rename a project; `old_root`/`new_root` are folder prefixes ending in a separator, and
     stored paths under `old_root` move to `new_root` (chunk 57)."
   - New, after it: `def has_open_runs(conn: sqlite3.Connection, project_id: str) -> bool:`
     returning whether `SELECT 1 FROM runs WHERE project_id = ? AND status IN ('pending',
     'running', 'awaiting_user', 'paused') LIMIT 1` returns a row.
2. `projects.py`:
   - Move `_slug_base` from `assistant.py` here unchanged as public `slug_base` (above
     `create_project_record`). Add below it:
     `def unique_slug(resources: APIResources, name: str, *, own: str | None = None) -> str:`
     `base = slug_base(name)`; inner `taken(slug: str) -> bool` is `slug != own and
     (get_project_by_slug(resources.conn, slug) is not None or
     os.path.lexists(ProjectPaths(resources.settings.data_dir, slug).root))`; return `base`
     when not taken, else the first `f"{base}-{n}"` for `n = 2, 3, ...` not taken.
   - Import `PersistenceError` from `statlab.errors` and `has_open_runs` from the repository.
   - Rewrite the `rename` route body after `paths = service.paths(project)`:
     1. `slug = unique_slug(resource, body.name, own=project.slug)`;
        `new_paths = _project_paths(resource.settings, slug)`.
     2. When `slug != project.slug`: if `has_open_runs(resource.conn, project.id)` raise
        `HTTPException(409, "stop or finish this project's open runs before renaming it")`;
        then `try: paths.root.rename(new_paths.root)` `except OSError: raise
        HTTPException(500, "cannot move the project folder") from None`.
     3. `try: row = rename_project(resource.conn, project.id, name=body.name, slug=slug,
        old_root=f"{paths.root}{os.sep}", new_root=f"{new_paths.root}{os.sep}")`
        `except (PersistenceError, sqlite3.Error):` when `slug != project.slug`, move back with
        `new_paths.root.rename(paths.root)`; then `raise HTTPException(500, "project rename
        failed") from None`.
     4. The existing `project.json` rewrite uses `new_paths` instead of `paths`. Return `row`.
3. `assistant.py`: delete `_slug_base` and `_unique_slug`; import `unique_slug` from
   `statlab.api.projects`; line ~304 becomes `create_project_record(self.resources,
   unique_slug(self.resources, name), name)`. Remove `get_project_by_slug` (and any other)
   import only if Ruff reports it unused.
4. Tests in `tests/test_api_projects.py` (`_write` and `_open_project_api` exist; the fixture
   project is slug `demo`, name `Demo`). Helper (module level, next to `_write`):
   `def _seed_run(db_path: Path, project_id: str, root: Path, status: str) -> None` that, via
   `_write`, inserts `contracts (id, project_id, path, json, version, created_at)` values
   `('ct_seed', project_id, str(root / "contracts" / "ct_seed.json"), '{}', 1,
   '2026-01-01T00:00:00Z')` (write the `1` as an SQL literal), `runs (id, project_id,
   contract_id, mode, status, started_at, config_json)` values `('r_seed', project_id,
   'ct_seed', 'autonomous', status, '2026-01-01T00:00:00Z', '{}')`, and `artifacts (id,
   run_id, kind, path, meta_json, produced_by, ts)` values `('a_seed', 'r_seed', 'table',
   str(root / "runs" / "r_seed" / "artifacts" / "a_seed.json"), '{}', 'seed',
   '2026-01-01T00:00:00Z')`. If any of these columns is NOT NULL without a default that this
   recipe omits, stop and report.
   - Replace `test_rename_project_updates_row_list_and_metadata` with
     `test_rename_project_moves_folder_and_paths`: upload `data.csv` (`b"a,b\n1,2\n3,4\n"`,
     as in `test_stored_dataset_corruption_and_missing_file_return_500`);
     `old = ProjectPaths(settings.data_dir, "demo").root`; `_seed_run(settings.db_path,
     project["id"], old, "completed")`; PATCH `{"name": "Churn Study"}` -> 200, body `slug ==
     "churn-study"`, `name == "Churn Study"`, `id`/`created_at` unchanged;
     `new = ProjectPaths(settings.data_dir, "churn-study").root`; `not old.exists()`,
     `new.is_dir()`; `json.loads((new / "project.json").read_text())["slug"] ==
     "churn-study"`; reading the DB with `sqlite3.connect(settings.db_path)`: every
     `datasets.path`, `contracts.path` and `artifacts.path` row starts with
     `f"{new}{os.sep}"`; the uploaded dataset file exists at its stored path; GET
     `/api/projects` lists name "Churn Study".
   - `test_rename_project_keeps_slug_when_base_matches`: PATCH `{"name": "DEMO"}` -> 200,
     slug `demo`, folder `demo` still exists.
   - `test_rename_project_slug_skips_taken_names`: POST `/api/projects` `{"slug":
     "churn-study", "name": "Other"}`; mkdir `settings.data_dir / "churn-study-2"`; PATCH demo
     to `{"name": "Churn Study"}` -> slug `churn-study-3`.
   - `test_rename_project_refused_with_open_run`: `_seed_run(..., "awaiting_user")`; PATCH
     `{"name": "Churn Study"}` -> 409 with detail `"stop or finish this project's open runs
     before renaming it"`; folder `demo` still exists; stored name still `Demo`.
   - `test_rename_project_folder_move_failure`: `settings.data_dir.chmod(0o500)`, PATCH
     `{"name": "Churn Study"}`, restore `0o700` in `finally`; 500, detail `"cannot move the
     project folder"`; name still `Demo`.
   - `test_rename_project_db_failure_moves_folder_back`: `monkeypatch.setattr(
     projects_module, "rename_project", <function raising PersistenceError("boom")>)` where
     `projects_module` is `statlab.api.projects` (import it as the other tests import
     modules); PATCH `{"name": "Churn Study"}` -> 500 `"project rename failed"`; folder
     `demo` exists, `churn-study` does not.
   - `test_rename_project_metadata_write_failure`: change the name sent to `"DEMO"` so the
     slug stays `demo` (the chmod/restore of the `demo` root then still applies).
   - Existing assistant tests must keep passing unchanged (the slug rule is the same).

Checks: `make test-fast TESTS="tests/test_api_projects.py tests/test_api_assistant.py"`;
Ruff check, Ruff format --check and mypy on the four owned files.

## Stage 57b — frontend error text (after 57a)

Owned: `frontend/src/pages/ProjectPage.tsx`. Preserve other edits.

The client maps every 409 to "Run transition rejected.". In the rename branch replace
`errorText(rename.error)` with `rename.error instanceof ApiError && rename.error.status ===
409 ? 'Stop or finish this project’s open runs before renaming it.' :
errorText(rename.error)` (import `ApiError` from `../api/client.ts`). Below the rename form,
always while renaming, add `<p className="help">The short name follows the new name.</p>`.

Checks, from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

Main then: `make check`, browser check, commit; tell the user that `binary classification`
gets the short name `binary-classification` when renamed again (any name change, e.g. save
the same name with different case, re-derives it).

### Addendum 57a-A (coverage)

`make check`: `projects.py` branch 550->552 (DB failure with the slug unchanged) uncovered.
Parametrize `test_rename_project_db_failure_moves_folder_back` over `name` in
`["Churn Study", "DEMO"]` (ids `moved`, `same`): both expect 500 `"project rename failed"` and
folder `demo` existing; only `moved` additionally asserts `churn-study` does not exist.
Checks: `make test-fast TESTS="tests/test_api_projects.py"`, Ruff check/format --check on the
test file.

## Acceptance receipt (2026-10-09)

57a implementer: project/assistant API tests exit 0, Ruff/format/mypy on four files exit 0;
addendum A test exit 0. 57b implementer: `npm test` 96/96, typecheck 0, build 0. Main reviewed
the diffs (unparenthesized `except A, B:` is Ruff format output, valid Python 3.14) and ran
`make check`: first run `check57.log` 99.99 % (projects.py 550->552), after addendum A
`check57b.log` exit 0, coverage 100 % (TOTAL 12195 stmts, 0 missed), mypy 197 files, Ruff
clean (logs temporary). Browser check on the offline stack: project `online-test-1` with a
dataset and a completed run renamed to "Binary classification" shows slug
`binary-classification`; the data folder moved, dataset and artifact rows point into it, the
run report and an artifact preview still return 200. Screenshot
`.playwright-mcp/57-renamed.png`.
