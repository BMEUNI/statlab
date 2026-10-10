---
status: completed
dependencies: [77]
completion_date: 2026-10-10
implementation_notes: "the setup check flags the fields a run needs; 409/422 reasons reach the screen for run start and predictions"
---

# Chunk 78 — say why a run cannot start

User report 2026-10-10: "Start run" shows "Invalid request." three times (API log: `POST
/api/projects/p_b851b91e48127fad/runs` 422 each time).

Cause (main, read-only): the saved contract (`ct_59e655126ecacd13`, v3) is
`{"target":{"column":"target_flag"},"features":{"exclude":["target_reg"]}}`. Run start calls
`require_complete` (`api/projects.py` ~444), which refuses a contract without `target.type`,
`objective.task`, `objective.primary_metric` and `split.strategy` (422 "contract is missing
required setup fields"). The wizard's Check step (`/contracts/validate`) does not check those
fields, so it said "The contract matches the data", and the Start step even says "The agent
decides" for task and metric. The frontend `request` helper (`frontend/src/api/client.ts`
~617-630) replaces every 422 with "Invalid request." and every 409 with "Run transition
rejected.", which also hides 77c/77e's reasons ("this run has no champion model yet", "the
dataset has no rows without a target to predict", ...).

Decisions:
1. The `/contracts/validate` route (the wizard's Check step only; the service method's other
   callers — run start, propose, assistant — are unchanged) appends one mismatch per missing
   setup field, `{"field": <name>, "message": "is required to start a run"}`, and then reports
   `valid: false`. The Check step already lists mismatches and blocks Continue.
2. The Start step shows `—` instead of "The agent decides".
3. `request` gains an opt-in `detail` flag: for a 409 or 422 whose JSON body has a non-blank
   string `detail`, the error message is that detail as a sentence (first letter upper-case,
   final period added when missing). `createRun`, `predictUnlabelled` and `predictUpload` opt
   in. Static `messages` overrides still win; everything else keeps today's generic messages.
Non-goals: showing details for other endpoints or statuses; letting the agent choose task or
metric.

## Stage 78a (detailed spec)

Owned: `backend/statlab/api/projects.py`, `tests/test_api_projects.py`,
`frontend/src/api/client.ts`, `frontend/src/setup/StartStep.tsx`,
`frontend/tests/client.test.ts`. Not alone: preserve others' edits.

1. `projects.py` route `validate_contract` (~714): body becomes:
   `validation = await ProjectsService(resources()).validate(project_id, body.dataset_id,
   body.contract)`; `missing = missing_setup_fields(body.contract)`; if `not missing`: return
   `validation`; otherwise return `SetupValidation(valid=False, mismatches=(
   *validation.mismatches, *({"field": name, "message": "is required to start a run"} for
   name in missing)), warnings=validation.warnings)`. Add the docstring `"""The wizard's check:
   data mismatches plus the fields a run needs before it can start (chunk 78)."""`.
2. `tests/test_api_projects.py`: new `test_validate_reports_fields_a_run_needs(tmp_path,
   monkeypatch)`: `_open_project_api`; upload `("data.csv", b"a,b\n1,2\n3,4\n", "text/csv")`
   to `/api/projects/{project['id']}/datasets`; POST `/contracts/validate` with
   `{"dataset_id": row["id"], "contract": {"target": {"column": "a"}}}`; assert 200 and JSON
   `{"valid": False, "mismatches": [{"field": name, "message": "is required to start a run"}
   for name in ("target.type", "objective.task", "objective.primary_metric",
   "split.strategy")], "warnings": []}`. Then POST the same with `"contract":
   _contract().model_dump(mode="json")` and assert `valid` is `True`.
3. `client.ts`:
   - `RequestOptions` gains `detail?: boolean;`.
   - New helper above `request`: `function sentence(text: string): string` returning
     `` `${text.charAt(0).toUpperCase()}${text.slice(1)}${text.endsWith('.') ? '' : '.'}` ``.
   - In `request`'s `!response.ok` branch, after the 401 listener line and before `const
     message`: `let reason: string | null = null;` `if (options.detail === true &&
     (response.status === 409 || response.status === 422)) { try { const body: unknown =
     await response.json(); if (isJsonObject(body) && typeof body.detail === 'string' &&
     body.detail.trim() !== '') reason = sentence(body.detail); } catch { /* keep the generic
     message */ } }`. The `const message = options.messages?.[response.status] ?? (...)`
     expression becomes `options.messages?.[response.status] ?? reason ?? (...)` (the generic
     chain unchanged).
   - `createRun`, `predictUnlabelled`, `predictUpload`: add `detail: true` to their `request`
     options.
4. `StartStep.tsx`: both `'The agent decides'` fallbacks become `'—'`.
5. `frontend/tests/client.test.ts` (`ApiError`, `predictUnlabelled`, `createRun`,
   `predictUpload` and `createProject` are already imported): add after the
   `predictUpload` test:
   `test('opted-in requests show the backend reason for 409 and 422', async () => { ... })`
   that saves `const originalFetch = globalThis.fetch;` and in `try`/`finally` (restoring it):
   - `globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'this run has no
     champion model yet' }), { status: 409 });` then `await assert.rejects(predictUnlabelled(
     'r1'), (error: unknown) => error instanceof ApiError && error.message === 'This run has no
     champion model yet.');`
   - same with status 422 and detail `'contract is missing required setup fields.'` against
     `createRun('p1', 'guided', 'd1', 'c1')`, expecting `'Contract is missing required setup
     fields.'`.
   - `new Response('not json', { status: 422 })` against `predictUpload('r1', new Blob(['a']),
     'x.csv')`, expecting `'Invalid request.'`.
   - `new Response(JSON.stringify({ detail: 'hidden' }), { status: 422 })` against
     `createProject('s', 'S')`, expecting `'Invalid request.'` (not opted in).

Checks: `make test-fast TESTS="tests/test_api_projects.py"`; Ruff check, Ruff format --check
and mypy on `backend/statlab/api/projects.py tests/test_api_projects.py`; from `frontend/`:
`npm run typecheck`, `npm test`, `npm run build`. If an existing test fails because it
validates an incomplete contract and expects `valid: true`, stop and report the test name and
assertion. Stop and report on any failure or gap.

Main then: review, `make check`, browser check of the wizard Check step with an incomplete
contract (scratch servers), commit.

### Addendum 78a-A (typing and line length)

1. Build the extra mismatches as an annotated list first: `required: list[dict[str,
   JsonValue]] = [{"field": name, "message": "is required to start a run"} for name in
   missing]`, then `mismatches=(*validation.mismatches, *required)`.
2. Wrap the docstring over two lines: `"""The wizard's check: data mismatches plus the fields a
   run needs` / `before it can start (chunk 78)."""`.
Rerun the Python checks (pytest selector, Ruff check/format, mypy).

## Stage 78a acceptance receipt (2026-10-10)

Implementer (after addendum A): `tests/test_api_projects.py` 56 passed; Ruff check/format and
mypy on the two Python files exit 0; `npm run typecheck`, `npm test` (117/117), `npm run
build` exit 0. Main reviewed the diff (matches the recipe and addendum). `make check`
(`check78.log`, temporary) exit 0: coverage 100 % (TOTAL 13759 stmts, 0 missed), mypy 212
files, Ruff clean; main reran `npm test` 117/117 and the build, exit 0. Browser check skipped:
the Check step's mismatch list and Continue block are unchanged code, and the new route output
is asserted by `test_validate_reports_fields_a_run_needs`.
