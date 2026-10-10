---
status: completed
dependencies: [80]
completion_date: 2026-10-10
implementation_notes: "quality fixes never blank the split time/group column"
---

# Chunk 81 — data quality never blanks the split's time or group column

User report 2026-10-10: run `r_a2bc035177f8f5c2` (project `teszt`, temporal split on `rowID`,
lags on `ke`/`hupx`) failed at Data splitting: "time_column 'rowID' has 1 missing value(s); a
temporal split needs a time for every row".

Cause (main, read-only): `check_data_quality`'s disguised-missing check reported
`rowID = 9999` (a numeric sentinel) as fixable; the `QualityDecision` chose `mark_missing:
{"rowID": ["9999"]}`; `fix_data_quality` blanked that row's time; `add_lag_features` then
refused the frame. A legitimate time or group value must never be turned into a missing value:
the split needs every row's time/group.

Decision: in the data-quality node, the split's `time_column` and `group_column` (when set) are
removed from the reported `mark_missing` options before the decision is cleaned, so a fix on
them is dropped by chunk 80's `_usable_quality` with its warning. The warning text becomes
`"ignored {n} quality fix(es) that cannot be applied"` (covers both unreported and protected
fixes). Non-goals: changing `check_data_quality` itself (the check may still report the value;
the evidence stays honest); `merge_variants` (text-only, a numeric time column is unaffected;
not touched); editing the stuck run.

## Stage 81a (detailed spec)

Owned: `backend/statlab/domains/foundation.py`, `tests/test_nodes_foundation.py`. Not alone:
preserve others' edits.

1. `foundation.py` `_usable_quality` (~288): warning f-string becomes `f"ignored {dropped}
   quality fix(es) that cannot be applied"` (nothing else changes there).
2. `foundation.py` `_quality`, right after `fixable = _object(check.summary.get("fixable"),
   "quality fixable")` (~617), insert, in order:
   - `split = self.runtime.contract.split`
   - `protected = {column for column in (split.time_column, split.group_column) if column is
     not None}`
   - `reported = _object(fixable.get("mark_missing"), "quality fixable mark_missing")`
   - `fixable = {**fixable, "mark_missing": {column: values for column, values in
     reported.items() if column not in protected}}`
   Add the comment line above the four statements: `# A split time/group value is never
   blanked: the split needs it on every row (chunk 81).` If mypy rejects the dict for
   `dict[str, JsonValue]`, annotate the inner comprehension as a `dict[str, JsonValue]` local
   named `markable` first and use it; nothing else.
3. `tests/test_nodes_foundation.py`:
   - In `test_quality_drops_unreported_fixes` and
     `test_quality_with_only_unreported_fixes_changes_nothing`, the expected warning text
     changes to `"ignored 3 quality fix(es) that cannot be applied"` and `"ignored 1 quality
     fix(es) that cannot be applied"`.
   - New test after them, `test_quality_never_blanks_split_time_column(migrated_db, project,
     tmp_path)` (same fixture signature as the other `_full` tests):
     `h = harness(migrated_db, project, tmp_path, contract=DataContract(target=TargetSpec(
     column="y", type="binary"), features=FeatureRules(id_columns=("id",), exclude=(
     "excluded",)), split=SplitRules(strategy="temporal", time_column="t")),
     spine=TABULAR_SPINE)`; `h.tools.fixable = {"mark_missing": {"t": ["9999"], "city":
     ["?"]}, "merge_variants": []}`; `h.llm.responses[QualityDecision] =
     QualityDecision(rationale="sentinel", mark_missing={"t": ("9999",), "city": ("?",)})`;
     `await h.advance(3)`; `result = await h.step()`; assert `result.status == "completed"`;
     `"ignored 1 quality fix(es) that cannot be applied" in result.warnings`; the
     `fix_data_quality` params list equals `[{"frame": str(RAW), "target": "y",
     "mark_missing": {"city": ["?"]}, "merge_variants": []}]`.

Checks: `make test-fast TESTS="tests/test_nodes_foundation.py"`; `.venv/bin/python -m ruff check
<two files>`; `.venv/bin/python -m ruff format --check <two files>` (run `ruff format` on the
two files first if needed); `.venv/bin/python -m mypy <two files>`. Stop and report on any
failure or gap (e.g. the temporal contract making an earlier step fail in the harness, or the
frame not being `str(RAW)`).

Main then: review, `make check`, commit.

## Stage 81a acceptance receipt (2026-10-10)

Implementer: `tests/test_nodes_foundation.py` exit 0; Ruff check/format and mypy on the two
owned files exit 0; no deviations (ruff format reflow only). Main reviewed the full diff
(matches the recipe). `make check` (`check81.log`, temporary) exit 0: coverage 100 % (TOTAL
13783 stmts, 0 missed), mypy 212 files, Ruff clean.
