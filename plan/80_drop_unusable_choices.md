---
status: completed
dependencies: [79]
completion_date: 2026-10-10
implementation_notes: "profiling and quality drop unusable LLM choices with a warning instead of failing the step"
---

# Chunk 80 — unusable profiling/quality choices are dropped, not fatal

User report 2026-10-10: run `r_8a4ae2fc12128d17` stopped at "Data profiling — It failed: LLM
output remained invalid after one repair".

Cause (main, read-only): the failure gate's `field_errors.output` is "compute_correlations
entries must be exact column names; rejected ['Calendar columns hour, weekday, ...', ...]
(unknown or incompatible)". Both `ProfileDecision` calls (original and repair) put sentences
into `correlations`. `_validate_profile` (`backend/statlab/domains/foundation.py` ~456-485) is
passed as the LLM `check`, so one bad entry fails the whole step after one repair.
`_validate_quality` (~263-282) has the same shape for `QualityDecision`.

Decision: the profiling and quality decisions keep only usable entries and drop the rest, with
one node warning naming how many were dropped; the step carries on. No LLM `check` (no paid
repair round for this). A selected tool or plot tool missing from the node skill stays a hard
refusal (configuration bug). The cleaned decision is the one shown at the approval gate, stored
in the summary and executed. Non-goals: other nodes' `_rejecting` checks (candidate_models,
candidate_search, design_inference keep theirs; `_rejecting` stays); prompt text; editing the
stuck run.

## Stage 80a (detailed spec)

Owned: `backend/statlab/domains/foundation.py`, `tests/test_nodes_foundation.py`. Not alone:
preserve others' edits.

### `foundation.py`

1. Replace module function `_validate_quality` (~263-282) with
   `def _usable_quality(decision: QualityDecision, fixable: dict[str, JsonValue], evidence:
   _Evidence) -> QualityDecision:` docstring `"""Keep only reported fixes; drop the rest with a
   warning instead of failing the step (chunk 80)."""`. Body, in order:
   - `reported = _object(fixable.get("mark_missing"), "quality fixable mark_missing")` (as
     today); `dropped = 0`; `mark_missing: dict[str, tuple[str, ...]] = {}`.
   - For `column, values in decision.mark_missing.items()`: `options = reported.get(column)`;
     `allowed = {item for item in options if isinstance(item, str)} if isinstance(options,
     list) else set()` (annotate `allowed: set[str]` if mypy needs it); `usable = tuple(value
     for value in values if value in allowed)`; `dropped += len(values) - len(usable)`; if
     `usable`: `mark_missing[column] = usable`.
   - `allowed_merge = set(_strings(fixable.get("merge_variants"), "quality fixable
     merge_variants"))`; `merge = tuple(column for column in decision.merge_variants if column
     in allowed_merge)`; `dropped += len(decision.merge_variants) - len(merge)`.
   - If `dropped`: `evidence.warnings.append(f"ignored {dropped} quality fix(es) that the
     checks did not report")`.
   - `return decision.model_copy(update={"mark_missing": mark_missing, "merge_variants":
     merge})`.
2. Replace method `_validate_profile` (~456-485) with `def _usable_profile(self, invocation:
   NodeInvocation, evidence: _Evidence, decision: ProfileDecision, schema: dict[str,
   JsonValue], target: str) -> ProfileDecision:` docstring `"""Keep only usable column choices;
   drop the rest with a warning instead of failing the step (chunk 80)."""`. Body, in order:
   - `columns, numeric = self._profile_columns(schema)`; `allowed =
     self.runtime.skill.node(invocation.spec.name).allowed`; `dropped = 0`; `update: dict[str,
     object] = {}`.
   - For `name, selected, tool, compatible in (("numeric", decision.numeric,
     "summarize_numeric", numeric), ("categorical", decision.categorical,
     "summarize_categorical", set(columns) - numeric), ("correlations", decision.correlations,
     "compute_correlations", numeric), ("outliers", decision.outliers, "detect_outliers",
     numeric))`: `usable = tuple(entry for entry in selected if entry in compatible)`;
     `dropped += len(selected) - len(usable)`; if `usable and tool not in allowed`: raise
     `_RefusalError(f"{tool} is unavailable in this node skill")`; `update[name] = usable`.
   - `plots: list[ProfilePlot] = []`; for `plot in decision.plots`: `tool =
     f"plot_{plot.kind}"`; if `tool not in allowed`: raise `_RefusalError(f"{tool} is
     unavailable in this node skill")`; if `plot.column not in columns or (plot.kind ==
     "target_relation" and plot.column == target)`: `dropped += 1`; else `plots.append(plot)`.
     Then `update["plots"] = tuple(plots)`. (Import `ProfilePlot` from
     `statlab.domains.decisions` if not already imported.)
   - If `dropped`: `evidence.warnings.append(f"ignored {dropped} profiling choice(s) that were
     not usable columns")`.
   - `return decision.model_copy(update=update)`.
3. `_profile`: remove the `check=_rejecting(...)` argument from the `ProfileDecision` decide
   (keep the question text unchanged). Line `self._validate_profile(invocation, decision, ...)`
   becomes `decision = self._usable_profile(invocation, evidence, decision, schema.summary,
   problem.target)`; after the approval `None` return, `self._validate_profile(invocation,
   approved, ...)` becomes `approved = self._usable_profile(invocation, evidence, approved,
   schema.summary, problem.target)`. Order otherwise unchanged (summary built from the cleaned
   `decision`, `_approve` gets the cleaned `decision`).
4. `_quality`: remove the `check=_rejecting(lambda d: _validate_quality(d, fixable))` argument;
   `_validate_quality(decision, fixable)` becomes `decision = _usable_quality(decision, fixable,
   evidence)`; `_validate_quality(approved, fixable)` becomes `approved =
   _usable_quality(approved, fixable, evidence)`.
5. Keep `_rejecting` and its imports (other modules import it). Remove only imports left
   unused (Ruff will say).

### `tests/test_nodes_foundation.py`

Foundation fixture facts: profiling is the third step (`advance(2)` then `step()`), mode
autonomous; schema columns `x` (numeric) and `excluded`, `id`, `y` (non-numeric); target `y`.
`_full` harness: quality is the fourth step (`advance(3)` then `step()`); fixture `fixable` is
`{"mark_missing": {"city": ["?"]}, "merge_variants": ["city"]}`.

6. Replace `test_profile_selection_refuses_before_optional_tools` with
   `test_profile_drops_unusable_choice(foundation, decision)` keeping a parametrize over:
   `ProfileDecision(rationale="unknown", numeric=("missing",))`, `ProfileDecision(rationale=
   "wrong dtype", numeric=("id",))`, `ProfileDecision(rationale="target mismatch", plots=(
   ProfilePlot(kind="target_relation", column="y"),))`, `ProfileDecision(rationale="ghost
   plot", plots=(ProfilePlot(kind="distribution", column="ghost"),))`. Body: `await
   foundation.advance(2)`; set the response; `result = await foundation.step()`; assert
   `result.status == "completed"`; `"ignored 1 profiling choice(s) that were not usable
   columns" in result.warnings`; `result.summary["decision"] ==
   ProfileDecision(rationale=decision.rationale).model_dump(mode="json")`; and `not any(tool in
   {"summarize_numeric", "plot_distribution", "plot_target_relation"} for _, tool, _ in
   foundation.tools.calls)`.
7. New `test_profile_keeps_usable_choices(foundation)`: response `ProfileDecision(rationale=
   "mixed", numeric=("x", "id"), categorical=("x", "excluded"), correlations=("x vs y",))`;
   `advance(2)`; `result = await foundation.step()`; assert completed; `"ignored 3 profiling
   choice(s) that were not usable columns" in result.warnings`; the `summarize_numeric` call
   params `columns == ["x"]` (exactly one call); the `summarize_categorical` call `columns ==
   ["excluded"]` (exactly one call); no `compute_correlations` call.
8. `test_profile_check_feeds_llm_repair` → rename `test_profile_prompt_names_typed_columns`;
   delete `check = foundation.llm.checks[ProfileDecision]`, the two `pytest.raises(
   DecisionRejectedError, ...)` blocks and `assert check(... "dup" ...) is None`; keep the
   dedupe assertions, the message assertions and the caplog assertion.
9. `test_profile_refuses_unavailable_tool_and_unknown_plot_column` → rename
   `test_profile_refuses_unavailable_tool`; parametrize `("removed", "response", "message")`
   over `("summarize_numeric", None, "summarize_numeric is unavailable in this node skill")`
   and `("plot_distribution", ProfileDecision(rationale="plot", plots=(ProfilePlot(kind=
   "distribution", column="x"),)), "plot_distribution is unavailable in this node skill")`;
   the skill removes `{removed}` instead of `{"summarize_numeric"}`; if `response is not None`
   set `h.llm.responses[ProfileDecision] = response` before `advance(2)`; assert failed and
   `out.error.message == message`. Annotate `response: ProfileDecision | None`.
10. Delete `test_profile_refuses_unknown_plot_column` (covered by step 6).
11. `test_quality_check_rejects_unreported_fixes` → rename `test_quality_drops_unreported_fixes`:
    `h = _full(...)`; `h.llm.responses[QualityDecision] = QualityDecision(rationale="mixed",
    mark_missing={"city": ("?", "n/a"), "other": ("?",)}, merge_variants=("city", "other"))`;
    `await h.advance(3)`; `result = await h.step()`; assert completed; `"ignored 3 quality
    fix(es) that the checks did not report" in result.warnings`; the `fix_data_quality` params
    list equals `[{"frame": str(RAW), "target": "y", "mark_missing": {"city": ["?"]},
    "merge_variants": ["city"]}]` (same shape as `test_quality_fixes_feed_split`).
12. `test_quality_refuses_unreported_fix_after_decision` → rename
    `test_quality_with_only_unreported_fixes_changes_nothing`: same setup (response
    `merge_variants=("other",)`, `advance(3)`, `step()`); assert completed; `"ignored 1 quality
    fix(es) that the checks did not report" in result.warnings`; no `fix_data_quality` call;
    `result.summary["decision"]["merge_variants"] == []`.
13. Remove `DecisionRejectedError` from the `statlab.llm.client` import if nothing else uses
    it. Leave `OfflineLlm.checks` as is.

Checks: `make test-fast TESTS="tests/test_nodes_foundation.py"`; `.venv/bin/python -m ruff check
<two files>`; `.venv/bin/python -m ruff format --check <two files>`; `.venv/bin/python -m mypy
<two files>`. Stop and report on any failure or gap (e.g. another test in the file expecting a
refusal, or `frame` in step 11 not being `str(RAW)`).

Main then: review, `make check`, commit.

### Addendum 80a-A (gap and wrapping)

1. Step 8: also delete `assert check(ProfileDecision(rationale="ok", numeric=("x",))) is None`
   (the line right after `check = ...`).
2. Lines over 100 characters: wrap each docstring over two lines at a word boundary (closing
   `"""` stays on the last text line); split the long f-strings of the two warnings into two
   adjacent string literals inside the same call (text unchanged); let `ruff format` wrap the
   remaining code lines (run `.venv/bin/python -m ruff format` on the two owned files, then the
   `--check`). Then continue with steps 6-13 and run all four checks.

### Addendum 80a-B (typed assertion)

Step 12's last assertion becomes `assert QualityDecision.model_validate(result.summary["decision"])
.merge_variants == ()` (one logical line; let `ruff format` wrap it). Rerun the pytest selector,
Ruff check/format and mypy.

## Stage 80a acceptance receipt (2026-10-10)

Implementer (after addenda A and B): `tests/test_nodes_foundation.py` exit 0; Ruff check/format
and mypy on the two owned files exit 0. Accepted deviations: `ProfilePlot` import; docstrings
and warning f-strings wrapped. Main reviewed the full diff (matches recipe and addenda). `make
check` (`check80.log`, temporary) exit 0: coverage 100 % (TOTAL 13779 stmts, 0 missed), mypy
212 files, Ruff clean.
