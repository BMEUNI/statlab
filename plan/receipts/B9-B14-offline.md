# B9–B14 offline runtime acceptance receipt — 2026-10-07

Scope: offline runtime/coverage acceptance of implemented B9–B14 (authorized 2026-10-05). No paid
or remote provider calls, no manual live services, no browser smoke, no B15/B16 work. Detailed
stage specs, stops, corrections and per-stage receipts: [offline validation plan](../offline_runtime_validation.md)
(corrections C1–C10, coverage stages S1–S16 with addenda).

## Candidate

Base commit `e3d9315` plus the dirty product diff below (48 files, +4359/−558 before plan/status
edits). sha256 prefixes (12 hex):

```
ca00c2d3a45b backend/statlab/api/projects.py
6e0468aed7f2 backend/statlab/api/runs.py
0ffba4a3c677 backend/statlab/api/stream.py
176371b905a8 backend/statlab/domains/candidate_models.py
ac82b688292c backend/statlab/domains/champion.py
dd258a043ced backend/statlab/domains/design_inference.py
67ab5ff507e4 backend/statlab/domains/foundation.py
e37f7026acf1 backend/statlab/domains/persistence.py
5ecaa4f03f35 backend/statlab/errors.py
e4ee25d0a753 backend/statlab/graph/candidates.py
29fae7a3b918 backend/statlab/graph/control.py
e5024ded5705 backend/statlab/graph/engine.py
0a99c7f197e5 backend/statlab/graph/gates.py
529419a9afdf backend/statlab/graph/reconciliation.py
9f7f68f0da57 backend/statlab/graph/rewind.py
2f1af2e0a2eb backend/statlab/llm/client.py
852e1cdbd179 backend/statlab/main.py
8681db2c86f0 backend/statlab/mcp_client.py
119fec3fbd14 backend/statlab/persistence/repository.py
77918ab9f6e1 tests/test_api_projects.py
5480c52ce34c tests/test_api_runs.py
9311b390a2f5 tests/test_api_stream.py
93325b9c3ad2 tests/test_candidate_fanout.py
4f25d7d2013f tests/test_candidate_fanout_tools.py
b9bcb090b988 tests/test_control_gates.py
a1a4f4db1d41 tests/test_control_lifecycle.py
c00b7cc76b1b tests/test_control_qa_status.py
663895150c7e tests/test_control_rewind.py
97989ead25f4 tests/test_domain_binding.py
3820c6aba9a7 tests/test_domain_executor.py
86e418afa2cc tests/test_errors.py
2b6868c24c63 tests/test_graph_engine.py
9c04d7dcabf6 tests/test_llm_stream.py
23b79d181d12 tests/test_main.py
2fe3fffc0dce tests/test_mcp_client.py
be0951acefe6 tests/test_nodes_candidate_models.py
63b6e44d5016 tests/test_nodes_candidate_pipeline.py
eb93a8ce9e9a tests/test_nodes_candidate_search.py
61b4afae4a3c tests/test_nodes_champion.py
6ac10b271a66 tests/test_nodes_design_inference.py
cd620f3806b1 tests/test_nodes_foundation.py
9bbb53c78399 tests/test_nodes_persistence.py
77a7097f630d tests/test_repository.py
befad354a951 tests/test_storage.py
3c0bc88cf0f3 tests/test_tools_setup.py
```

## Final gate (main, once, stable candidate)

`LOKY_MAX_CPU_COUNT=1 make check` → exit 0. Log: `/private/tmp/statlab-offline-20261007-final3-make-check.log`
(temporary). Results: **3006 passed, 1 designed skip** (F9 200 MB write, `test_storage_fork.py`),
0 failed; coverage **TOTAL 11293 stmts / 3238 branches, 100.00%** (fail-under 100 reached); mypy
"Success: no issues found in 179 source files"; Ruff "All checks passed!"; Ruff format "187 files
already formatted".

## Defects found and fixed

- Gate position (S3, `graph/gates.py`): guided-mode gate on the first candidate node used raw
  `owner.position`; acknowledge/skip failed `RunState` validation and restart rejected the
  unanswered gate. Now uses the effective `max(region_start, position)`.
- Status facts (S8, `graph/control.py`): candidate progress used raw `branch.position` (could
  report position 0); now the effective position.
- Fork reuse (S9a, `graph/rewind.py`): a published fork whose checkpoint is missing raised the
  generic "run checkpoint is missing"; now the specific "published fork checkpoint is unavailable".
- Crash diagnosability (S16): unexpected node-executor exceptions and crashed run tasks were
  swallowed without any log. Now logged at ERROR with exception type and frame locations
  (`file:line in function`) only — never the message or source lines (secrets/data safety).
- Earlier corrections C1–C10 (fixture contracts, fan-out validation, CPU detection, loopback
  socket, execution attempts, producer audit, Q&A failure contract, gate options, awaiting
  recovery, test isolation) are recorded in the plan.

Coverage closure removed only provably unreachable guards (each justified in its stage addendum),
replaced typing-only `None` narrowing with shared helpers (`require_run`, `require_project`,
`resolved_metric`), and added exact negative tests for every reachable guard. No coverage
suppression, no type ignores, no validator weakening.

## Artifact inspection (main)

`tests/test_spine_reporting.py` re-run with retained basetemp (exit 0). Read in full:
- classification `report.md`: champion, test metric with CI marked as the only unbiased estimate,
  validation selection score marked optimistic (D33a), CI-overlap disclosure (D33b), full
  validation comparison table, pre-registered inference with Holm correction and D35 note,
  narrative section (fixture text offline), figure link.
- `pipeline.py`: byte-deterministic, dataset sha256 gate, embedded contract snapshot, replays the
  15 champion-path tool calls through the real MCP envelope; M7b test proves the test metric
  reproduces within ±0.005 and that a tampered dataset is refused.
- `manifest.json`: package versions, platform, python, run hash, 46 file hashes. `statlab-tools`
  is `null` by design (source tree, no distribution metadata, D-f).

## Known limits carried to online validation

- Offline runs use fixture LLM replies; real provider behaviour, prompt quality, cost and latency
  are unproved until the paid online run.
- Boot fails closed: any corrupted non-terminal run stops application startup (existing design).
- Run creation reads `uv.lock` from the process working directory; start the API from the repo
  root.
- The manifest does not record the git commit; note it separately for each online run.
- M12 browser smoke and B15/B16 remain out of scope.
