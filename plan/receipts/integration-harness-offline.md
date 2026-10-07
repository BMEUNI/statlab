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

## Addendum 2026-10-07 — H3, F5, abort; live-run readiness
- Live run 1 (`20261007T112809Z-live`, stopped by main): exposed F5 (LLM put `random_state` in
  candidate params). Spend recorded $0.1781 / 44 calls.
- F5: `RESERVED_ESTIMATOR_PARAMS` in `statlab_contracts.vocabulary`; validators on
  `EstimatorSpec` and `TrainingDecision`; tool reuses the constant; training prompt hint.
- H3: S09 fork, S14 propose, S15 memory, S16 API restart; addendum A (harness shutdown calls
  `begin_shutdown`, mirrors `statlab.main._RunServer`); addendum G (Ctrl-C/TERM abort → exit 130,
  services and MCP stopped; probe: no leftover process).
- `make check` (with F5) exit 0: 3014 passed, 1 designed skip; TOTAL 11315 stmts / 3244
  branches 100%; mypy 188 files; Ruff clean; 197 formatted.
- Full offline suite `integration/out/20261007T114213Z-offline`: 19/19 PASS (only soft warning:
  S12 x4 inference). 452 stand-in LLM calls.
- User rule: no paid run without explicit confirmation of that run.
