---
status: completed
dependencies: [74]
completion_date: 2026-10-10
implementation_notes: "saving a contract identical to one already in the project returns the saved row"
---

# Chunk 75 — reusing a saved contract does not save a copy

User report 2026-10-10: "every run creates a new contract even when I select an old one".

Cause: the setup wizard's "Reuse a saved contract" copies the saved contract into the draft;
`StartStep` (`frontend/src/setup/StartStep.tsx` ~24-28) always uploads the draft and starts the
run with the new row. `ProjectsService.persist_contract` (`backend/statlab/api/projects.py`
~262) always writes a new file and row. User DB (read-only): project "binary classification"
has 3 contracts with identical JSON, one per run.

Intended part: contracts are immutable; any change saves a new version so earlier runs keep
their exact setup. The setup chat drafts a fresh contract (one LLM call) for each target choice;
it has no saved-contract picker (non-goal here).

Decision: `persist_contract` (the one path for wizard uploads, chat-assistant drafts and edits,
and run-chat edits) first looks for a contract in the same project whose stored JSON equals the
new contract's `model_dump_json(exclude_unset=True)` as parsed JSON (key order ignored; set vs
unset fields stay distinct because `model_fields_set` carries meaning, e.g.
`design_inference.py` ~148). On a match it returns that row: no new file, row or version. The
upload route keeps status 201. Two identical concurrent uploads may still both save; harmless.
Non-goals: removing existing duplicates, a chat saved-contract picker.

## Stage 75a (detailed spec)

Owned: `backend/statlab/api/projects.py`, `tests/test_api_projects.py`. Not alone: preserve
others' edits.

1. `projects.py` `ProjectsService.persist_contract`: directly after `project =
   self.project(project_id)`, add:
   - `wanted = json.loads(contract.model_dump_json(exclude_unset=True))`
   - `for row in list_contracts(self.resources.conn, project.id):` if
     `json.loads(row.contract_json) == wanted`: `return row`.
   Add the docstring `"""An identical contract already saved in the project is returned
   instead of saving a copy (chunk 75)."""` as the first statement. `json` and `list_contracts`
   are already imported. Nothing else changes.
2. `tests/test_api_projects.py` `test_contract_upload_versions_validation_findings_and_no_runs`:
   - Replace `assert first_row["version"] == 1 and second.json()["version"] == 2` with
     `assert first_row["version"] == 1 and second.json() == first_row`.
   - After `assert sparse.status_code == 201` add `assert sparse.json()["version"] == 2`
     (the sparse body sets fewer fields, so it differs from `body`, the full dump).
   - Directly after that, add: `again = await client.post(path, files={"file":
     ("reordered.json", b'{"target": {"column": "a"}}', "application/json")})`;
     `assert again.status_code == 201 and again.json() == sparse.json()`; and
     `assert len((await client.get(path)).json()) == 2`.

Checks: `make test-fast TESTS="tests/test_api_projects.py tests/test_api_assistant.py
tests/test_api_runs.py tests/test_repository.py"`; `.venv/bin/python -m ruff check
backend/statlab/api/projects.py tests/test_api_projects.py`; `.venv/bin/python -m ruff format
--check backend/statlab/api/projects.py tests/test_api_projects.py`; `.venv/bin/python -m mypy
backend/statlab/api/projects.py tests/test_api_projects.py`. If any other existing test fails
because it expects an identical contract to get a new id or version, stop and report the test
and assertion; do not edit it.

Main then: review, `make check`, commit.

## Acceptance receipt (2026-10-10)

Implementer: focused selectors (api projects, api assistant, api runs, repository) exit 0,
223 passed; Ruff check/format and mypy on the two files exit 0. No other test expected a new
id for an identical contract. Main reviewed the diff (matches the recipe) and confirmed the
wizard only changes a reused draft when the user edits it (`SetupWizard.tsx` ~47 sets dataset
options only when non-empty). `make check` (`check75.log`, temporary) exit 0: coverage 100 %,
mypy clean, Ruff clean. Existing duplicate contracts in the user DB stay (non-goal).
