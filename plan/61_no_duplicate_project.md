---
status: completed
dependencies: [60]
completion_date: 2026-10-09
implementation_notes: "create_project with the selected project name is rejected and repaired; make check 100%"
---

# Chunk 61 — the setup chat does not create the current project twice

User report 2026-10-09: "create a new project for multi class classification", then the CSV
upload; the chat answered "Created project ..." again and still offered "Upload a CSV".
The database has `multi-class-classification` (with the dataset) and
`multi-class-classification-2` (empty), created ten seconds apart. On the upload turn the
setup model chose `create_project` with the same name; `SetupAssistant.turn`
(`backend/statlab/api/assistant.py` ~287) created a second project and reset the state to it,
dropping the uploaded dataset. Not related to chunk 59 (setup prompts are unchanged).

Decision: the action check rejects `create_project` when a project is already selected and
the requested name equals its name (trimmed, case-insensitive). The repair round then tells
the model "project ... is already selected; continue with the next step instead of creating
it again". Creating a differently named project, or the same name with no project selected,
works as before (`test_create_project_slugs` stays unchanged).

## Stage 61a

Owned: `backend/statlab/api/assistant.py`, `tests/test_api_assistant.py`. Preserve other
edits.

1. In `turn`, right after `project_ids = [p.id for p in list_projects(conn)]`:
   `current_name = next((p.name for p in list_projects(conn) if p.id == state.project_id),
   None)`.
2. In `check`, `create_project` branch, after the existing empty-name check:
   `if current_name is not None and action.project_name.strip().casefold() ==
   current_name.strip().casefold():` raise `DecisionRejectedError(f"project
   {current_name!r} is already selected; continue with the next step instead of creating it
   again")`.
3. Test `test_create_project_not_repeated_for_selected_project` after
   `test_create_project_slugs`, same fixture: first turn with reply `{"reply": "Created.",
   "action": "create_project", "project_name": "Churn Study"}` -> 200, take
   `state = response.json()["state"]`. Then queue two replies, `{"reply": "Created again.",
   "action": "create_project", "project_name": " churn study "}` and `{"reply": "Upload a
   CSV next.", "action": "none"}`, and send `turn(client, "I uploaded data.csv.",
   state=state)`: 200, `reply == "Upload a CSV next."`, `state["project_id"]` unchanged, and
   the project list has exactly one slug starting with `churn-study`. Model the reply
   queueing on `test_repair_round`; if the repair round consumes replies differently than
   two in a row, stop and report.

Checks: `make test-fast TESTS="tests/test_api_assistant.py"`; Ruff check, Ruff format --check
and mypy on the two owned files.

Main then: `make check`, commit.

## Acceptance receipt (2026-10-09)

Implementer: `tests/test_api_assistant.py` exit 0 (16 tests), Ruff/format and mypy on both
files exit 0. Main reviewed the diff and ran `make check` exit 0: coverage 100 % (TOTAL 12215
stmts, 0 missed), mypy 198 files, Ruff clean (log `check61.log`, temporary). The user's empty
duplicate `multi-class-classification-2` stays until the user decides.
