---
status: completed
dependencies: [86]
completion_date: 2026-10-27
implementation_notes: "ground_sections cuts ungrounded numbers before assemble_report; caveat Number check line; summary grounding key"
---

# Chunk 87 — numbers in LLM-written report sections must appear in the evidence

iLLM course integration (see `ILLM_COURSE.md` §1b; PLAN D38). Today the champion node's
`ChampionChoice.sections` are LLM free text passed verbatim to `assemble_report`; the prompt
says "do not invent numbers" (`backend/statlab/domains/champion.py:306`) but nothing checks it.

Goal: before the report is assembled, cut every sentence (or table row) of an LLM-written
section whose number is not in the evidence; drop a section whose heading has such a number;
state the count in the caveats section; list the cut text in the node summary.

Non-goals: checking `ChampionChoice.rationale` (printed only when overriding the top candidate;
also part of the `select_champion` audit, so changing it breaks resume); checking code-written
sections (`_quality_sections`, caveats); repair calls to the LLM (the chunk 84 pattern: drop the
unusable part, never fail the step); frontend changes; changing `ReportSection` or
`ChampionChoice`; word numbers ("twelve").

Prerequisites: chunk 86 accepted (HEAD `7da364a`). Reuse: `ReportSection`
(`contracts/statlab_contracts/reporting.py:17`), `_Evidence.result`
(`backend/statlab/domains/foundation.py:252`), `MemoryRow.value()`
(`backend/statlab/persistence/models.py:337`), `DomainRuntime.memory` / `.contract`
(`backend/statlab/domains/runtime.py:39-44`), fake `h.llm.responses[ChampionChoice]` in
`tests/test_nodes_champion.py:362`.

Owned files: `backend/statlab/domains/grounding.py` (new), `backend/statlab/domains/champion.py`,
`tests/test_grounding.py` (new), `tests/test_nodes_champion.py`. Not alone: preserve others'
edits. No other file, no dependency, no migration.

## 1. `backend/statlab/domains/grounding.py` (new)

Module docstring: `"""Cut LLM-written report sentences whose numbers are not in the evidence
(chunk 87, PLAN D38)."""`

Imports: `from __future__ import annotations`; `import re`; `from collections.abc import
Sequence`; `from dataclasses import dataclass`; `from typing import Final`; `from pydantic import
JsonValue`; `from statlab_contracts.reporting import ReportSection`.

Constants (exact):
- `_NUMBER: Final = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\w)%?")`
  Comment above: `# A written number: optional thousands commas, decimals, exponent, percent.
  Not part of a word (c_01, F1, v1.2).`
- `_MARKER: Final = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")` — a markdown list marker.
- `_SENTENCE: Final = re.compile(r"(?<=[.!?])\s+")`.

Dataclass:
```
@dataclass(frozen=True, slots=True)
class Grounding:
    sections: tuple[ReportSection, ...]
    removed: tuple[str, ...]
```
Docstring: kept sections (unchanged objects when nothing was cut) and the cut text, in order;
a dropped heading is recorded as `"heading: <heading>"`.

Functions (private unless stated), in this order:

1. `def _parse(token: str) -> tuple[float, int, bool, bool]` returns
   `(value, decimals, percent, exponent)`:
   `percent = token.endswith("%")`; `core = token.removesuffix("%").replace(",", "")`;
   `exponent = "e" in core.lower()`; `mantissa = re.split(r"[eE]", core)[0]`;
   `decimals = len(mantissa.partition(".")[2])`; `value = abs(float(core))`.

2. `def _values(source: JsonValue) -> list[float]`, recursive, branch order:
   - `isinstance(source, bool)` or `source is None` → `[]`
   - `isinstance(source, int | float)` → `[abs(float(source))]`
   - `isinstance(source, str)` → `[_parse(m.group())[0] for m in _NUMBER.finditer(source)]`
   - `isinstance(source, list)` → concatenation of `_values(item)` for each item
   - otherwise (dict) → concatenation of `_values(item)` for each value (keys ignored).

3. `def _grounded(token: str, known: Sequence[float]) -> bool`:
   `value, decimals, percent, exponent = _parse(token)`;
   - `if not percent and not exponent and decimals == 0 and value <= 10: return True`
     (small counts: "3 candidates", list numbers);
   - `if exponent: return any(abs(item - value) <= 0.005 * value for item in known)`;
   - `tolerance = 0.5 * 10.0**-decimals`;
     `targets = [(value, tolerance)]`, plus `(value / 100, tolerance / 100)` when `percent`;
     `return any(abs(item - target) <= bound + 1e-12 for target, bound in targets for item in known)`.

4. `def _keep(text: str, known: Sequence[float]) -> bool`:
   `return all(_grounded(match.group(), known) for match in _NUMBER.finditer(text))`.

5. `def _filter_body(body: str, known: Sequence[float]) -> tuple[str, list[str]]`:
   ```
   lines: list[str] = []
   removed: list[str] = []
   for line in body.split("\n"):
       if line.lstrip().startswith("|"):          # a table row is one unit
           if _keep(line, known): lines.append(line)
           else: removed.append(line.strip())
           continue
       marker = _MARKER.match(line)
       prefix = marker.group() if marker else ""
       units = _SENTENCE.split(line[len(prefix):])
       kept = [unit for unit in units if _keep(unit, known)]
       removed.extend(unit.strip() for unit in units if not _keep(unit, known))
       if len(kept) == len(units): lines.append(line)      # untouched, whitespace preserved
       elif kept: lines.append(prefix + " ".join(kept))
       # every unit cut: the line is dropped
   return "\n".join(lines), removed
   ```
   (Pseudocode; write normal multi-line Python. Compute `_keep` per unit once if preferred, but
   the order of `removed` must follow unit order.)

6. Public `def ground_sections(sections: Sequence[ReportSection], sources: Sequence[JsonValue]) -> Grounding`:
   ```
   known = [value for source in sources for value in _values(source)]
   kept, removed = [], []
   for section in sections:
       if not _keep(section.heading, known):
           removed.append(f"heading: {section.heading}"); continue
       body, cut = _filter_body(section.body, known)
       removed.extend(cut)
       if not cut: kept.append(section)
       elif body.strip(): kept.append(ReportSection(heading=section.heading, body=body))
       # cut and nothing left: section dropped
   return Grounding(sections=tuple(kept), removed=tuple(removed))
   ```
   Docstring: one paragraph — numbers are matched rounded to their written decimals, `%` also as
   a fraction, signs ignored, exponents within 0.5 %, integers 0-10 always pass; a cut never
   fails the step (chunk 84 pattern).

## 2. `backend/statlab/domains/champion.py`

a. Import `from statlab.domains.grounding import ground_sections`.

b. New module-level function after `eligible_candidates`:
   ```
   def _selection_evidence(
       branches: Sequence[CandidateProgress],
       comparison: dict[str, JsonValue],
       ranking: dict[str, JsonValue],
       excluded: dict[str, JsonValue],
       waves: int,
   ) -> tuple[JsonValue, ...]:
   ```
   Docstring: `"""The evidence the champion decision sees; also the grounding source for its
   sections (D38)."""` Returns exactly the tuple currently inlined at the `decide` call
   (`champion.py:285-301`): `(comparison, ranking, {"candidates": [... same per-branch dict ...],
   "excluded": excluded, "n_waves": waves, "selection_bias": SELECTION_CAVEAT, "fit_timing":
   TIMING_CAVEAT})`. Move that code; do not change its content.

c. In `_execute`, the `decide(... ChampionChoice ...)` call: replace the inline `evidence=(...)`
   with `evidence=_selection_evidence(branches, comparison.summary, ranked.summary, excluded,
   state.wave)`. Change `question` by appending one sentence after "...or a different winner.":
   `" Every number in a section must be copied from this evidence; sentences with other numbers are cut from the report."`
   (keep the leading space; implicit string concatenation like the existing lines).

d. Immediately before `caveats = ReportSection(` (currently `champion.py:430`):
   ```
   grounding = ground_sections(
       choice.sections,
       (
           *_selection_evidence(branches, comparison_summary, ranked.summary, excluded, state.wave),
           *(boundary.result.summary for boundary in state.shared_results),
           self.runtime.contract.model_dump(mode="json"),
           *(row.value() for row in self.runtime.memory),
       ),
   )
   ```
   `comparison_summary` and `ranked` are bound on both the fresh and the resumed path. On the
   resumed path `choice` has no sections, so nothing is cut.

e. Caveats body: append to the existing expression (after the `Excluded branches` part) the
   value of a new module-level helper `_number_check(checked: int, removed: int) -> str`:
   - `checked == 0` → `""`
   - `removed == 0` → `"\n\nNumber check: every number in the LLM-written sections appears in the run's validation evidence."`
   - otherwise → `f"\n\nNumber check: {removed} sentence(s) were cut from the LLM-written sections because their numbers do not appear in the run's validation evidence."`
   Call: `_number_check(len(choice.sections), len(grounding.removed))`.

f. `assemble_report` `sections` argument: replace `*choice.sections` with `*grounding.sections`.

g. Completed result summary (the final `evidence.result("completed", {...})`): add key
   `"grounding": {"checked_sections": len(choice.sections), "removed": list(grounding.removed)}`
   after `"report"`.

## 3. Edge cases (input → expected)

| Input | Known evidence | Result |
|---|---|---|
| "AUC was 0.82 on validation." | 0.8234 | kept (|0.8234-0.82| ≤ 0.005) |
| "About 82% of rows." | 0.8234 | kept (82% → 0.82 ± 0.005) |
| "1,200 rows were used." | "rows 1,200 kept" (string source) | kept |
| "Test AUC was 0.91." | 0.8234 only | cut |
| "- 3 candidates ran." | none | kept (small integer) |
| "11. Rank eleven claim." | none | kept (11 is the list marker) |
| "12 folds were used." | none | line dropped |
| "\| b \| 0.7 \|" | 0.5 | row cut whole |
| "p was 1e-4." / "q was 3e-2." | 0.0001 | kept / cut |
| heading "Gain of 98.7%" | none | section dropped, removed `"heading: Gain of 98.7%"` |
| body "Score 0.99." only | none | section dropped |
| "1.0 was seen." | `{"flag": True}` | cut (bools are not numbers) |
| "Coefficient fell by 0.05." | -0.05 | kept (signs ignored) |
| "c_01 beat c_02 on F1." | none | kept (no standalone number) |

## 4. Tests

### `tests/test_grounding.py` (new)

Import `ReportSection`, `ground_sections`, `pytest`. Helper-free; each test builds sections with
typed kwargs `ReportSection(heading=..., body=...)`.

1. `test_grounded_numbers_keep_the_section_unchanged`: sources `({"metrics": {"auc": 0.8234}},
   "rows 1,200 kept")`; section heading `"Why c_01"`, body
   `"AUC was 0.82 on validation. About 82% of rows.\n\n1,200 rows were used. c_01 beat c_02 on F1."`;
   assert `result.sections == (section,)` and `result.sections[0] is section` and
   `result.removed == ()`.
2. `test_ungrounded_sentence_is_cut_from_its_line`: sources `({"auc": 0.8234},)`; body
   `"- AUC was 0.82. Test AUC was 0.91. Done."`; assert body
   `"- AUC was 0.82. Done."`, removed `("Test AUC was 0.91.",)`.
3. `test_small_integers_and_list_markers_pass_and_unmatched_lines_drop`: sources `()`; body
   `"- 3 candidates ran.\n\n11. Rank eleven claim.\n12 folds were used."`; assert body
   `"- 3 candidates ran.\n\n11. Rank eleven claim."`, removed `("12 folds were used.",)`.
4. `test_table_rows_are_cut_whole`: sources `({"v": 0.5},)`; body `"| a | 0.5 |\n| b | 0.7 |"`;
   assert body `"| a | 0.5 |"`, removed `("| b | 0.7 |",)`.
5. `test_exponents_match_within_half_a_percent`: sources `({"p": 0.0001},)`; body
   `"p was 1e-4. q was 3e-2."`; assert body `"p was 1e-4."`, removed `("q was 3e-2.",)`.
6. `test_heading_or_emptied_body_drops_the_section`: sources `()`; sections
   `(ReportSection(heading="Gain of 98.7%", body="x"), ReportSection(heading="Score", body="Score 0.99."), ReportSection(heading="Kept", body="No numbers here."))`;
   assert `[s.heading for s in result.sections] == ["Kept"]`, removed
   `("heading: Gain of 98.7%", "Score 0.99.")`.
7. `test_source_walk_ignores_bools_and_none_and_signs`: parametrize
   `(source, body, kept)`:
   - `({"flag": True}, "1.0 was seen.", False)`
   - `({"a": [None, {"b": 2.5}]}, "2.5 was seen.", True)`
   - `("12% of rows", "12.0 rows.", True)`
   - `({"coef": -0.05}, "Coefficient fell by 0.05.", True)`
   For each: `result = ground_sections((ReportSection(heading="h", body=body),), (source,))`;
   assert `bool(result.removed) is not kept`.

### `tests/test_nodes_champion.py`

a. In `test_champion_selects_before_exact_replay_and_one_task_score`, after the existing
   `"Data quality" not in ...` assertion add: `assert "Number check" not in str(report["sections"])`
   and `assert output.summary["grounding"] == {"checked_sections": 0, "removed": []}`.

b. New test after `test_report_lists_data_quality_checks_and_fixes`:
   `test_report_cuts_section_numbers_missing_from_evidence` (`@pytest.mark.asyncio`):
   `h, tools = await champion_harness(migrated_db, project, tmp_path)`; set
   `h.llm.responses[ChampionChoice] = ChampionChoice(candidate_id="c_01", rationale="Validation and simplicity", sections=(ReportSection(heading="Why c_01", body="c_01 was simpler. Its test AUC was 0.987654. Two candidates competed."), ReportSection(heading="Gain of 98.7654%", body="x")))`;
   `output = await ChampionExecutor(h.runtime)(tail_invocation(h))`; assert
   `output.status == "completed"`; `report = next(...assemble_report...)` as in the neighbouring
   test; `sections = report["sections"]`; assert `sections[0] == {"heading": "Why c_01", "body":
   "c_01 was simpler. Two candidates competed."}`; assert `"Gain of 98.7654%" not in
   str(sections)`; last section body contains `"Number check: 2 sentence(s) were cut"`; assert
   `output.summary["grounding"] == {"checked_sections": 2, "removed": ["Its test AUC was 0.987654.", "heading: Gain of 98.7654%"]}`.
   Import `ReportSection` from `statlab_contracts.reporting` if not imported.

## 5. Checks (implementer runs exactly these)

- `make test-fast TESTS="tests/test_grounding.py tests/test_nodes_champion.py"`
- `.venv/bin/python -m ruff check backend/statlab/domains/grounding.py backend/statlab/domains/champion.py tests/test_grounding.py tests/test_nodes_champion.py`
- `.venv/bin/python -m ruff format --check` on the same four files
- `.venv/bin/python -m mypy backend/statlab/domains/grounding.py backend/statlab/domains/champion.py tests/test_grounding.py tests/test_nodes_champion.py`

No mutations required (the node test asserts the cut directly). Main runs `make check` once at
acceptance; 100 % branch coverage of `grounding.py` comes from §4.

Stop and report on: any failing pre-existing champion test, a mypy error needing a type ignore or
`Any`, or `comparison_summary`/`ranked` not bound at §2d.

Receipt: changed files, commands, exits, results, deviations.

Hazard for chunk 88: it adds a "Earlier runs used" line to the same caveats body; keep
`_number_check` last.

## Addendum A (review correction, 2026-10-27)

First `make check`: tests pass, coverage 99.99 % — `champion.py` `_number_check` branch
`removed == 0` with `checked > 0` (line 207) is unreached. Correction (tests only):
`tests/test_nodes_champion.py`, new test after `test_report_cuts_section_numbers_missing_from_evidence`:
`test_report_confirms_grounded_sections` (`@pytest.mark.asyncio`): `champion_harness`; set
`h.llm.responses[ChampionChoice] = ChampionChoice(candidate_id="c_01", rationale="Validation and simplicity", sections=(ReportSection(heading="Why c_01", body="c_01 was simpler."),))`;
run the executor; assert completed; the report's first section equals
`{"heading": "Why c_01", "body": "c_01 was simpler."}`; the last section body contains
`"Number check: every number in the LLM-written sections appears"`; and
`output.summary["grounding"] == {"checked_sections": 1, "removed": []}`.
Checks: `make test-fast TESTS="tests/test_nodes_champion.py"`, Ruff check/format and mypy on
`tests/test_nodes_champion.py`. Main reruns `make check`.

## Acceptance receipt (2026-10-27)

Candidate: HEAD `7da364a` plus working-tree changes. SHA-256: `grounding.py` 04f69827…29fc,
`champion.py` 551211c9…1146, `tests/test_grounding.py` 5d0d54e0…baa, `tests/test_nodes_champion.py`
ae31df00…f4e1. Implementer focused checks: 38 then 29 champion tests passed; Ruff/format/mypy exit 0.
First `make check` exit 2: coverage 99.99 % (`champion.py:207`), fixed by Addendum A (test only).
Final `make check` exit 0: 3343 passed, 1 designed skip (no failures), coverage TOTAL 100.00 %
(14057 statements, 3986 branches), mypy "no issues found in 215 source files", Ruff "All checks
passed!", "225 files already formatted". No mutation required. Live/offline scenario not run.
