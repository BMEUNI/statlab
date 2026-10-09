---
status: completed
dependencies: [61]
completion_date: 2026-10-09
implementation_notes: "json instruction names allowed keys, forbids echoing the schema; confusion table scrolls; make check 100%, npm 97/97"
---

# Chunk 62 — the model returns values, not the schema; a wide confusion matrix scrolls

User reports 2026-10-09:
1. Run `r_d7ae9066c0ba9c0b` (7-class obesity data): two of five models (c_01, c_04) failed at
   model training with `LLM output remained invalid after one repair`, field error
   `{"type": "Extra inputs are not permitted"}`.
2. The run summary's 7-class confusion matrix is wider than the chat; the whole chat scrolls
   sideways instead of the table.

Cause 1 (read-only): no decision model carries a schema example, so in json mode
`_schema_instruction` (`backend/statlab/llm/client.py` ~585) sends
`Example (syntax only; schema defines required fields): {}` plus the raw JSON schema.
DeepSeek sometimes echoes the schema: the live log
`integration/out/20261008T090422Z-live/api.log` ~1462 shows a TrainingDecision answer
`{"type":"json_object","additionalProperties":false,"properties":{...}}` (errors `type`,
`properties`, `additionalProperties` extra, `rationale` missing). In the user's run the repair
answer still carried a top-level `type` key, so the decision failed. No dev transcript of that
run exists; the log above is the same failure shape. With chunk 58 the failed models ended
only their branch.

Cause 2: `table.confusion` in `frontend/src/chat/RunSummary.tsx` ~72 has no scroll container;
`.msg-body` has `min-width: 0`, so a wrapper with `overflow-x: auto` contains it.

Decision:
1. The json-mode instruction without an example names the allowed top-level keys (the
   schema's `properties`) and forbids returning the schema or adding other keys. The
   `\nSchema: ` suffix stays last (the offline stand-in, `integration/offline_llm.py` ~507,
   parses it). Instructions with an example and prompt mode are unchanged. No key stripping:
   validators stay strict.
2. The confusion table sits in a horizontal scroll wrapper.
Non-goals: per-decision examples, changing the repair message.

## Stage 62a

Owned: `backend/statlab/llm/client.py`, `tests/test_llm_client.py`,
`frontend/src/chat/RunSummary.tsx`, `frontend/src/style.css`. Preserve other edits.

1. `client.py` `_schema_instruction`, the `if selected_example is None:` branch: replace its
   three statements with `keys = ", ".join(schema.get("properties", {}))` and
   `return (f"{prefix}Fill in the values and return one JSON object with only these top-level
   keys: {keys}. Never return the schema itself and add no other key (no \"type\",
   \"properties\" or \"additionalProperties\").\nSchema: {schema_json}")`. Nothing else
   changes.
2. `tests/test_llm_client.py` `test_json_tier_repairs_once_and_records_cached_flash_cost`
   (~344): replace `assert "syntax only" in first_messages[0]["content"]` with
   `assert "return one JSON object with only these top-level keys: value." in
   first_messages[0]["content"]` and `assert "Never return the schema itself" in
   first_messages[0]["content"]`. If the output model there has fields other than `value`,
   use the real key list and report it.
3. `RunSummary.tsx` ~72: wrap the `<table className="confusion">…</table>` in
   `<div className="scroll-x">…</div>` (inside the `confusion !== null && (...)`).
4. `style.css` line `table.confusion { width: auto; margin-top: 12px; }` becomes
   `table.confusion { width: auto; }`, and directly below it add
   `.scroll-x { overflow-x: auto; max-width: 100%; margin-top: 12px; }`.

Checks: `make test-fast TESTS="tests/test_llm_client.py tests/test_llm_stream.py"`; Ruff
check, Ruff format --check and mypy on `backend/statlab/llm/client.py` and
`tests/test_llm_client.py`; `.venv/bin/python -m integration.run_scenarios --llm offline
--only S01` (offline, no cost) with exit code and summary line; from `frontend/`: `npm test`,
`npm run typecheck`, `npm run build`.

Main then: `make check`, commit. A live check needs the user's go-ahead.

## Acceptance receipt (2026-10-09)

Implementer: `tests/test_llm_client.py tests/test_llm_stream.py` exit 0 (111 tests), Ruff
check/format (after `ruff format` reflow) and mypy on both Python files exit 0, offline S01
exit 0 PASS (`integration/out/20261009T114333Z-offline`), `npm test` 97/97, typecheck 0,
build 0. Main reviewed the diff (matches recipe) and ran `make check` exit 0: coverage 100 %
(TOTAL 12214 stmts, 0 missed), mypy 198 files, Ruff clean (log `check62.log`, temporary). The
schema echo cannot occur offline; the user's next live run is the end-to-end check.
