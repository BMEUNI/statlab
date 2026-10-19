---
status: completed
dependencies: [85]
completion_date: 2026-10-19
implementation_notes: "candidate identity and checkpoint ownership compare parsed specs, so pre-chunk-82 rows boot"
---

# Chunk 86 — boot accepts candidates saved before a spec field was added

User report 2026-10-19: `make dev` exits at startup:
`statlab.errors.PersistenceError: candidate identity mismatch` from
`RestartReconciler._repair_boundaries` → `ensure_candidate` (`repository.py` ~890).

Cause (main, read-only): `ensure_candidate` compares the stored `spec_json` string with
`spec.model_dump_json()`. Chunk 82 added `FeatureSpec.text_encoding` (default `"categorical"`),
so a candidate saved before chunk 82 (the dev DB has three `awaiting_user` runs) has no
`text_encoding` key, while the checkpoint spec re-dumps with it. Same spec, different string.
`validate_checkpoint_ownership` (`storage.py` ~182) has the identical string comparison.

Decision: both checks compare the parsed spec with the live one
(`CandidateSpec.model_validate_json(stored) != spec`). An added field with a default then
matches its old rows; any real difference still refuses. Stored rows are not rewritten.
Non-goals: per-run boot isolation (one bad run still stops boot; separate decision if it
recurs); migrating old rows.

## Stage 86a (detailed spec)

Owned: `backend/statlab/persistence/repository.py`, `backend/statlab/storage.py`,
`tests/test_repository.py`, `tests/test_candidate_fanout.py`. Not alone: preserve others' edits.

1. `repository.py` `ensure_candidate`: the condition `row["spec_json"] != spec.model_dump_json()`
   becomes `CandidateSpec.model_validate_json(row["spec_json"]) != spec` (`CandidateSpec` is
   already imported). Add a comment above the `elif`: `# Parsed, not string, comparison: an added
   spec field with a default matches rows saved before it (chunk 86).`
2. `storage.py` `validate_checkpoint_ownership`: `candidate["spec_json"] !=
   branch.spec.model_dump_json()` becomes `CandidateSpec.model_validate_json(candidate["spec_json"])
   != branch.spec`. Import `CandidateSpec` from `statlab_contracts.candidate` (alphabetical in the
   existing import block). The surrounding `except (sqlite3.Error, ValidationError, ...)` already
   maps an unparsable row to `PersistenceError`.
3. `tests/test_repository.py` `test_candidate_wave_identity_and_status`: after the wave-1
   `pytest.raises` block and before `update_candidate_status`, add:
   - legacy row accepted: with `transaction(migrated_db)`, `UPDATE candidates SET spec_json =
     json_remove(spec_json, '$.features.text_encoding') WHERE id = ?` (`created.id`); assert
     `"text_encoding" not in` the row's `spec_json` (one `SELECT`); assert `ensure_candidate(...,
     spec=spec, wave=2).id == created.id`.
   - real difference refused: `with pytest.raises(PersistenceError, match="identity"):
     ensure_candidate(migrated_db, candidate_id=created.id, run_id=run.id,
     spec=spec.model_copy(update={"label": "Other"}), wave=2)`.
4. `tests/test_candidate_fanout.py` `test_checkpoint_candidate_spec_tamper_is_rejected`, in order:
   a. After the first `validate_checkpoint_ownership(...)` call, read `rows = world.conn.execute(
      "SELECT spec_json FROM candidates WHERE run_id = ?", (state.run_id,)).fetchall()`; assert
      `len(rows) == 1`; `original = rows[0]["spec_json"]`.
   b. The tamper `UPDATE` becomes `UPDATE candidates SET spec_json = json_set(spec_json,
      '$.label', 'tampered') WHERE run_id = ?` (a valid but different spec; `'{}'` would now fail
      parsing with a different message). The existing `pytest.raises(... "checkpoint candidate
      ownership mismatch")` block stays.
   c. After it, in a `transaction(world.conn)`: `UPDATE candidates SET spec_json = json_remove(?,
      '$.features.text_encoding') WHERE run_id = ?` with `(original, state.run_id)`. Then call
      `validate_checkpoint_ownership(world.conn, state.run_id, state)` plainly (must not raise).
If `features` is not a key of the dumped spec JSON in either fixture, stop and report.

Checks (implementer runs exactly these):
- `make test-fast TESTS="tests/test_repository.py tests/test_candidate_fanout.py tests/test_control_recovery.py"`
- `.venv/bin/python -m ruff format` then `--check`, `ruff check`, and `mypy` on the four owned
  files.
Stop and report on any failure or gap. Receipt: changed files, commands, exits, last log lines,
deviations.

Main then replays boot on a copy of the dev DB (`STATLAB_DB_PATH` pointing at the copy).

## Stage 86a acceptance receipt (2026-10-19)

Implementer: focused repository/fanout/recovery selections exit 0; Ruff/mypy on the four owned
files exit 0; one comment wrapped for E501. Main reviewed the diff (matches the recipe). Boot
replay of `scripts/dev.py dev` on a copy of the user's `statlab.db` (3 `awaiting_user` runs with
pre-chunk-82 candidates): API "Application startup complete", no traceback. `make check`
(`check86.log`, temporary) exit 0: coverage 100% (TOTAL 13967 stmts, 0 missed, 3950 branches),
mypy 213 files, Ruff clean, 222 files formatted.
