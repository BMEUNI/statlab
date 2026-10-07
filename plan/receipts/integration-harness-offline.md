# Receipt — integration harness H1/H1b/H2 + backend fixes F1–F4 (offline), 2026-10-07

Base commit: `6d6bb08`. Spec: [../integration_harness.md](../integration_harness.md).

## Final acceptance (main)
`LOKY_MAX_CPU_COUNT=1 make check` → exit 0: 3010 passed, 1 designed skip (quiet-mode markers
counted: 3010 `.`, 1 `s`, 0 `F`/`E`); coverage TOTAL 11302 stmts / 3240 branches, 100.00%;
mypy "Success: no issues found in 188 source files"; Ruff "All checks passed!"; 197 files formatted.

## Backend fixes (source sha256)
- F1 stored contracts keep explicitness (`model_dump_json(exclude_unset=True)`):
  `backend/statlab/persistence/repository.py` 9cc1dba3…4040, `backend/statlab/api/projects.py`
  9f8288b1…2f48.
- F2 `snapshot_run` needs one *distinct* dataset (guided re-attempts):
  `mcp_server/statlab_tools/tools/reproducibility.py` 67611568…fd90.
- F3 one-hot-skipped high-cardinality columns are target-encoded (PLAN.md:388):
  `backend/statlab/domains/candidate_pipeline.py` 8196bb95…d2c3.
- F4 dedup subset excludes `features.exclude` (matches `verify_split` columns):
  `backend/statlab/domains/foundation.py` b139572a…93e8.
Tests: R9 updated + R9b; sparse upload assertion; 2 snapshot tests; onehot→target test; dedup
subset expectation.

## Harness evidence (offline stand-in LLM, $0 network)
- Full suite `integration/out/20261007T111547Z-offline`: 13/15 PASS; S04/S05 G3 resolved by
  harness addendum F → `integration/out/20261007T112342Z-offline`: S04, S05, S13 PASS.
  All 15 defined scenarios (S01–S08, S10–S13, S17–S19) pass every must-check offline.
- Real data: S18 UCI Adult (32,561 rows) and S19 UCI Auto MPG pass, incl. pipeline replay.
- `integration/out/` is gitignored; facts above are the durable record.

## Open
- Design question (user): proposal attempt of an approved `before` gate stays `awaiting_user`
  with no `node.*` end event (closure only via `gate.closed`).
- Not yet built: S09 rewind/fork, S14 propose, S15 memory, S16 API restart (stage H3).
- Live run ($2 cap) not yet executed.
- Existing DBs keep full-dump contracts (all fields explicit); new uploads are sparse.
