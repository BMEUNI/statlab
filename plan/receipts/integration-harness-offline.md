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

## Addendum — F6, F7, H4 (rich offline stand-in), 2026-10-07

Candidate: parent `236a47e` plus the working-tree diff committed with this receipt. sha256 prefixes:
`vocabulary.py bf8e93f7168adf2a`, `candidate.py 1a811233bd679e31`, `decisions.py 3bf902b3867bfb9b`,
`candidate_models.py d2de5456096aa7a4`, `tools/training.py 095396c92a852fbf`,
`integration/offline_llm.py 9bfa79aeb2a9f273`, `integration/run_scenarios.py c06adddb5d17df0a`,
`integration/checks.py 62edfc44069ee6a2`.

- F6: curated per-estimator tunable parameters, validated on `EstimatorSpec` and
  `TrainingDecision` (LLM boundary, so one repair round sees the error).
- F7: `get_model_params` exposes `feature_names`; training prompt lists the estimator's tunable
  parameters; training refuses parameters foreign to the estimator before any tool call.
- H4: `--llm offline-rich` stand-in (spec in `plan/integration_harness.md`, addenda A/B).
- `make check` exit 0: 3051 passed, 1 designed skip; TOTAL 11385 stmts / 3282 branches 100%;
  mypy 188 files; Ruff clean; 197 formatted. (First attempt exit 2 at 99.99%: partial branch
  `vocabulary.py 228->exit`, fixed by F6 addendum B.)
- Full rich suite `integration/out/20261007T144021Z-offline-rich`: 18/19 PASS; S16 failed only the
  shared-once check (wave-2 design confirmation), fixed by H4 addendum B; S16 rerun PASS in both
  modes (`20261007T145213Z-offline-rich`, `20261007T145251Z-offline`). Rich S01 exercised
  `tune_estimator`, one retune, a wave-2 candidate (4 candidates), ROC/PR/calibration curves,
  threshold selection, train CV and learning curve, coefficients, partial dependence, error
  segments, profile plots, and the report section. No tool errors or LLM repairs in any rich
  scenario. Only node failures: expected lifecycle interrupts (S07/S08/S16) and fit budget (S13).
- Full plain suite after all edits `integration/out/20261007T150428Z-offline`: 19/19 PASS (soft:
  S12 x4 inference only).
