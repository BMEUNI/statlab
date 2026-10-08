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

## Addendum — live run 2 finding, F8, H5, 2026-10-07

- Live S01 (paid, user-confirmed) `integration/out/20261007T183001Z-live`: $0.0041, 3 LLM calls,
  ended `cancelled` at `data_profiling` (LLM put a numeric column in `categorical`; refusal
  bypassed the repair loop). Fixed by F8.
- F8: `LlmClient.complete(check=...)`; `DecisionRejectedError(ValueError)` enters the existing
  one-repair round with its message; profile/design/inference validators wired via `_rejecting`;
  profile refusal names allowed columns and the profile question states the dtype rule.
- H5: rich stand-in answers the first `ProfileDecision` invalidly; profiling then makes 3 LLM
  calls (invalid, repaired, findings) and completes (`integration/out/20261007T183802Z-offline-rich`).
- `make check` exit 0: 3055 passed, 1 designed skip; TOTAL 11402 stmts / 3286 branches 100%;
  mypy 188 files; Ruff clean; 197 formatted.
- Full rich suite `integration/out/20261007T184133Z-offline-rich`: 19/19 PASS (soft: S12 x4).
- Full plain suite `integration/out/20261007T185211Z-offline`: 19/19 PASS (soft: S12 x4).

## Addendum — live run 3 finding and F9, 2026-10-07

- Live S01 (paid, user-confirmed) `integration/out/20261007T190758Z-live`: $0.2191, 39 calls;
  every wave-1 node completed; the wave-2 cumulative `DesignDecision` request failed in 91 ms
  with an unlogged non-transient provider exception -> abort. Balance OK ($5.04).
- F9: provider failures log exception class + HTTP status (never text); litellm
  `InternalServerError`/`BadGatewayError` are transient. Focused: 72 passed.
- `make check` exit 0: 3057 passed, 1 designed skip; TOTAL 11403 stmts / 3286 branches 100%;
  mypy 188; Ruff clean; 197 formatted. Offline suites not rerun: F9 only changes the
  provider-exception path, which the stand-in never takes.

## Addendum — live run 4 finding and F10, 2026-10-07

- Live S01 (paid, user-confirmed) `integration/out/20261007T191756Z-live`: $0.0314, 7 calls;
  the LLM copied `list_estimators` `default_grid` lists into candidate params at design; F6
  rejected twice -> abort. No provider failure (F9 logging idle).
- F10: sequence-valued params get "a parameter takes one value (tuning grids are chosen at
  model_training as param_grid)"; the design question states single values only.
- `make check` exit 0: 3057 passed, 1 designed skip; TOTAL 11405 stmts / 3288 branches 100%;
  mypy 188; Ruff clean; 197 formatted. Offline suites not rerun: the stand-in never sends
  sequences, and the only other change is prompt text.

## Addendum — live run 5 finding, F11, H6, 2026-10-07

- Live S01 (paid, user-confirmed) `integration/out/20261007T192326Z-live`: $0.2384; full wave 1
  completed; the wave-2 search proposal broke the family-justification rule, the planner raised
  `_RefusalError` inside `_fanout`, the run task crashed (`execution_failed`), harness waited
  1200 s.
- F11: search proposals validated inside the repair round (`check=_rejecting(cumulative)`); an
  unrepairable proposal logs a warning and ends search; family refusal names the family. H6:
  harness treats snapshot `execution_failed=true` as terminal.
- `make check` exit 0: 3058 passed, 1 designed skip; TOTAL 11418 stmts / 3290 branches 100%;
  mypy 188; Ruff clean; 197 formatted.
- Full rich suite `integration/out/20261007T195055Z-offline-rich`: 19/19 PASS.

## Addendum — live run 6 finding and F12, 2026-10-08

- Live S01 (paid, user-confirmed, $0.45 cap) `integration/out/20261008T055412Z-live`: $0.1325;
  provider 500s recovered by F9 retry; c_04 `ValidationConclusion` hit the 60 s request timeout
  3 times -> abort. Healthy calls peaked at 50 s.
- F12: `_REQUEST_TIMEOUT = 180`. `make check` exit 0: 3058 passed, 1 designed skip; TOTAL
  11418 stmts / 3290 branches 100%; mypy 188; Ruff clean; 197 formatted. Offline suites not
  rerun (constant only affects real provider waits).

## Addendum — F13 + H7 logging, 2026-10-08

- F13: `api.log` always carries `LLM decide` lines, full LLM request messages, raw responses,
  rejected outputs with errors, and tool calls with arguments (INFO); tool results at DEBUG.
  No reasoning text. Secret scan of `integration/out/20261008T063646Z-offline/api.log`: 0 hits.
- H7: terminal prints scenario start/end and one line per SSE event; `--verbose` enables DEBUG.
- Offline S01 `integration/out/20261008T063646Z-offline`: PASS 24/24; api.log 27 requests,
  27 responses, 27 decides, 85 tool calls.
- `make check` exit 0: 3058 passed, 1 designed skip; TOTAL 11431 stmts / 3290 branches 100%;
  mypy 188; Ruff clean; 197 formatted.

## Addendum — first live PASS, 2026-10-08

- Live S01 (paid, user-confirmed, $0.45 cap) `integration/out/20261008T064037Z-live`: PASS
  24/24, 0 soft warnings, $0.2034, 34 LLM calls, 184 s. Champion c_01 (logistic), test roc_auc
  0.6822; planted inference found. Wave-2 `SearchDecision` rejected twice (grids in params, then
  wrong `varies_from_baseline`); F11 ended search gracefully with a warning.

## Addendum — full live suite and F15, 2026-10-08

- Full live suite (paid, user-confirmed) `integration/out/20261008T070208Z-live` at 59abc4d:
  6/19 PASS (S06 S08 S10 S11 S13 S17), $3.4739, 506 LLM calls. Failure breakdown recorded in
  `plan/integration_harness.md`.
- F15: client drops stray trailing `}`/`]` after one complete JSON value (S01/S15); design
  refusal for non-numeric interaction columns (S02). `make check` exit 0: 3062 passed,
  1 designed skip; TOTAL 11447 stmts / 3294 branches 100%; mypy 188; Ruff clean; 197 formatted.

## Addendum — F16 + H8, 2026-10-08

- F16: profile/evaluation/setup refusals now feed the LLM repair round (`check`); FeatureDecision
  has no `skip_reason`; inference reference levels optional (tool default most frequent,
  recorded); prompts state variation, training-key, intake-escalation and column-name rules.
  Addendum A: multiclass diagnostics test uses confusion only.
- H8: G1 matches ERROR log level only; per-scenario cost caps recalibrated; S05 instruction
  asks for exactly two candidates.
- Implementer focused selector (8 files) exit 0, 303 passed; Ruff/mypy clean on owned files.
- `make check` exit 0: 3062 passed, 1 designed skip; TOTAL 11448 stmts / 3292 branches 100%;
  mypy 188; Ruff clean; 197 formatted.
- Offline suite `integration/out/20261008T081322Z-offline`: 19/19 PASS. Rich suite
  `integration/out/20261008T082137Z-offline-rich`: 19/19 PASS.

## Addendum — live S14 and F17, 2026-10-08

- Live S14 (paid, user-confirmed, $0.10 cap) `integration/out/20261008T083426Z-live` at 694eefb:
  $0.0845, 19 calls. Setup proposal passed (F16); run cancelled at model_training: explicit
  contract `tuning.method="grid"` vs LLM `direct`, refused after the LLM call.
- F17: `_validate_training` runs as `check` and post-approval; question states the contract's
  fixed tuning method. Addendum A covers the explicit-grid path.
- `make check` exit 0: 3062 passed, 1 designed skip; TOTAL 11462 stmts / 3300 branches 100%;
  mypy 188; Ruff clean; 197 formatted.
- Offline `integration/out/20261008T084054Z-offline` and rich
  `integration/out/20261008T084855Z-offline-rich`: 19/19 PASS each (source identical; addendum A
  is test-only).
