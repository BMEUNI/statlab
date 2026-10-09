---
status: completed
dependencies: [63]
completion_date: 2026-10-09
implementation_notes: "scenarios expect the data_quality step; D3 carries fixable dirt; offline stand-in applies every reported fix; offline 19/19, live S03 PASS (USD 0.0365)"
---

# Chunk 65 — integration scenarios cover the data quality step

User request 2026-10-09: add the data quality step to the integration scenarios, give one
dataset dirt the step can fix, run the full offline suite, then only the necessary live run.

Gap (read-only): chunk 63 added `data_quality` to `integration/checks.py` `_SHARED_NODES` and
made the offline stand-in answer `QualityDecision` with a rationale only. No scenario requires
the step (`nodes_completed`, `gates_opened_for`, `child_not_rerun` are subset checks in
`integration/checks.py` ~296, ~342, ~508), the stand-in never picks a fix, and D3
`churn_dirty` only has blank/`NA` tokens that `read_csv` already parses as missing, so
`fix_data_quality` never runs in any scenario. Only S01/S07 ran offline after chunk 63.

Decisions:
1. Every expectation list naming the shared spine names `data_quality` between
   `data_profiling` and `data_splitting` (S01, S02, S12, S18, S19 `nodes_completed`; S04
   `gates_opened_for`; S09 `child_not_rerun`).
2. S05 (profiling skipped) also expects `data_quality` completed: the step runs on the raw
   frame (chunk 63b addendum B).
3. D3 gets deterministic, fixable dirt without extra random draws (same seed stream): every
   25th row's `plan` is `?` (placeholder), every 20th row's `region` is upper case (spelling
   variant). S03 expects `data_quality` completed, `check_data_quality` used and the report's
   `Data quality` section.
4. The offline stand-in answers `QualityDecision` by applying every reported fix (the
   `fixable` lists of the `check_data_quality` evidence), so offline S03 exercises
   `fix_data_quality`. Live: which fixes the model picks is model behaviour, not a hard check.
5. Necessary live run: S03 only (live `QualityDecision`, fix choice, quality frame through the
   split and the report section). Guided gates, fork reuse and skipped profiling use the same
   backend paths offline proves.
Non-goals: new expectation fields, changes to `checks.py`, `driver.py` or the backend.

## Stage 65a (detailed spec)

Owned: `integration/offline_llm.py`, `integration/datasets.py`, `integration/scenarios.toml`.
Main edits `integration/SCENARIOS.md`. Not alone in the tree: preserve others' edits.

1. `integration/offline_llm.py`:
   - Remove `"QualityDecision",` from `_RATIONALE_ONLY`.
   - Add, after `_rich_profile`, `def _quality(evidence: list[JsonValue]) -> dict[str,
     JsonValue]:` — iterate `evidence`; `item = _obj(entry, "evidence")`; when
     `item.get("tool") == "check_data_quality"`: `result = _obj(item["result"], "result")`,
     `summary = _obj(result["summary"], "summary")`, `fixable = _obj(summary["fixable"],
     "fixable")`, return `{"rationale": RATIONALE, "mark_missing": fixable["mark_missing"],
     "merge_variants": fixable["merge_variants"]}`. After the loop raise
     `RuntimeError("offline provider: no check_data_quality evidence")`.
   - In `OfflineProvider._answer`, directly before `if title in _RATIONALE_ONLY:`, add: if
     `title == "QualityDecision"`: `_, evidence = _parts(first_user)`; return
     `_quality(evidence)`.
2. `integration/datasets.py` `_churn_dirty`, inside the loop after
   `charges_text = ...` and before `rows.append(...)`: `if i % 25 == 0: plan = "?"`, then
   `if i % 20 == 0: region = region.upper()`. No other change (no new random draw).
3. `integration/scenarios.toml`:
   - Replace every `"data_profiling", "data_splitting"` with `"data_profiling",
     "data_quality", "data_splitting"`; assert exactly 7 replacements (lines ~40, 91, 184,
     512, 638, 691, 757).
   - S03 `[scenario.expect]`: becomes `status = "completed"`, `champion = true`,
     `nodes_completed = ["data_quality"]`, `tools_used = ["drop_duplicate_rows",
     "check_data_quality"]`, `report_contains = ["Data quality"]`.
   - S05 `[scenario.expect]`: add `nodes_completed = ["data_quality"]` after `status`.

Checks: `.venv/bin/python -m ruff check`, `.venv/bin/python -m ruff format --check` and
`.venv/bin/python -m mypy` on `integration/offline_llm.py integration/datasets.py`; then
`.venv/bin/python -m integration.run_scenarios --llm offline --only S03 S05` (offline, no
cost). Report exit codes, the summary table rows, the output folder, and from the S03
folder's `statlab.db` (the run's database; if it is one level up, use the suite folder's)
the output of `sqlite3 -readonly <db> "SELECT tc.tool_name FROM tool_calls tc JOIN
node_executions ne ON ne.id = tc.node_execution_id WHERE ne.node_name = 'data_quality'"` —
`fix_data_quality` must appear. If any check fails or
`fix_data_quality` is absent, stop and report; do not change other files.

Main then: full offline suite, `make check` only if Python product inputs changed (none
expected: integration is outside the coverage scope), live S03 with the user's go-ahead
(given 2026-10-09), receipt, commit.

## Acceptance receipt (2026-10-09)

Implementer: Ruff check/format and mypy on `integration/offline_llm.py` and
`integration/datasets.py` exit 0; 7 asserted replacements in `scenarios.toml`; offline
`--only S03 S05` exit 0, both PASS (`integration/out/20261009T135720Z-offline`); S03's
`data_quality` ran `check_data_quality` and `fix_data_quality`. Main reviewed the diff
(matches the recipe; no extra random draw in D3) and ran the full offline suite: exit 0,
19/19 PASS (`integration/out/20261009T135816Z-offline`). S03 report: placeholder `?` in
`plan` (16 rows) and `region` variants (21 rows) found, both fixed, `## Data quality`
section present. Live S03 (user go-ahead 2026-10-09): exit 0, PASS 12/12, cost USD 0.0365,
25 LLM calls, 90.9 s (`integration/out/20261009T140113Z-live`); the live model chose both
fixes and the report says so. No product source changed, so no `make check` rerun (chunk 63
evidence stands; `integration/` is outside the coverage scope).
