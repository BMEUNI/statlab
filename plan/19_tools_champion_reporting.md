---
status: completed
dependencies: [14, 15, 16, 18]
completion_date: 2026-10-02
implementation_notes: >
  select_champion + render_metrics_table + assemble_report implemented. D-a added ci_low/ci_high
  to rank_models ranking rows and top_eligible/top_two_eligible_distinguishable/selection_note/
  n_candidates to its table payload (chunk 14 tests read only metric/ranking, unedited). ReportSection
  lives in contracts/statlab_contracts/reporting.py (frozen, extra=forbid, stdlib+pydantic). evaluation
  _ScoreTable/_load_score_table made public (ScoreTable/load_score_table), no behaviour change, reused
  by reporting. D-e formatting (.4g, v [lo,hi], em-dash null, cell | escape + newline fold, single-line
  headings) in reporting.py. D-d: check_lineage gained a `phase` param and the node-12 reporting-permit
  clause (phase=="reporting" after the one score); driver reads meta.phase; new guard cases both ways.
  Gates: 2046 passed + 1 skipped, suite ~53s; 100% line+branch (reporting/champion/evaluation/contracts
  all 100%, TOTAL 0 missed); mypy clean 99 files; ruff check+format clean. All 7 DoD mutations killed;
  every check_lineage clause (incl. the new reporting one) killed by deletion. Zero Any, zero
  pragma: no cover, zero new production # type: ignore. One test # type: ignore[arg-type] on a
  get_args comparison. No backend/ touched; the only contracts/ change is ReportSection.
---

# Chunk 19 — Champion and reporting tools

**Goal.** Node 12's tools after `rank_models`: `select_champion`, `render_metrics_table` and
`assemble_report`. The report's **numbers come from artifacts, never from LLM text**. The tool
writes the result, the comparison and the inference sections itself, including every statement
D32c, D33a–b and D35 require. The LLM contributes narrative sections only.

Batch B5 runs this first and chunk 20 after it.

---

## 1. Prerequisites & Context

### State

Committed; see `git log -1`. 2002 tests (2001 passed, 1 skipped by design), 100 % line and
branch coverage, mypy clean on 95 files, suite ≈ 46 s.

Read first:
- `tools/champion.py` (`rank_models`) and `tools/evaluation.py` (score-table payloads,
  `_load_score_table`, `compare_models`);
- `tools/inference.py` (the `coefficient_table` payload: `terms`, `goals`);
- `statlab_tools/artifacts.py` (`save_json`, `load_json`);
- `RunPaths.report` in `statlab_contracts/layout.py`;
- `tests/test_skeleton.py` (`Driver`, `check_lineage`, `_run_spine`, `SpineResult`).

### Decisions this chunk makes

**D-a. `rank_models` carries what the champion needs.** Its ranking rows gain `ci_low` and
`ci_high`, copied from the comparison rows it already reads. Its table payload gains what is today
only in its summary: `top_eligible`, `top_two_eligible_distinguishable`, `selection_note` and
`n_candidates`. Chunk 14 tests read only `metric` and `ranking` from the payload, so they pass
unedited.

**D-b. `select_champion` is a validated pointer.** It refuses an ineligible candidate. It allows
choosing other than `top_eligible`, because D33e trades accuracy against interpretability and
complexity, but records `overrides_top: true`, and the report prints the rationale next to it.
The backend records the pointer (`RunState.champion_candidate_id`, which opens the D32 test
lock) and refuses a second `select_champion` once the test score exists. A tool cannot see the
DB, so this is noted in `00_index.md` rows 21 and 29.

**D-c. The report states the facts itself.** `assemble_report` renders four fixed parts from
artifacts:
- **Result:** the champion; the test score with its CI, labelled "the only unbiased performance
  estimate"; the validation score with its CI, labelled a *selection score* over N candidates and
  W waves (D33a); "not distinguishable on this validation set" when the top two eligible CIs
  overlap (D33b); the cross-run exposure sentence when `prior_test_scorings > 0` (D32c); and the
  override rationale.
- **Validation comparison:** the `render_metrics_table` markdown.
- **Inference** (optional): one row per pre-registered goal, with the D35 note that confirmatory
  claims are limited to those goals.
- **Figures:** plots copied to `report/plots/`.

The LLM's `sections` (heading plus markdown body) go between Inference and Figures, verbatim. N,
the prior scorings and W come from the DB, so they are orchestrator-supplied.

**D-d. Reading test results is allowed for reporting, after the one score.** D32b allows only
transform-only tools and one `score_*` to receive test-derived artifacts in node 12, but the
report must read the champion's test score table. Amended rule: in `champion_selection`, **after**
the single `score_*`, tools with `phase == "reporting"` (`render_metrics_table`,
`assemble_report`) may receive test-derived artifacts. `select_champion` (phase `champion`) never
may: choosing must not see test. Applied to `PLAN.md` D32b and `00_index.md` row 21 with this
spec. The skeleton's `check_lineage` reads `meta.phase` and gains guard cases.

**D-e. Formatting.**
- Numbers use `f"{v:.4g}"`, and a value with a CI renders as `v [lo, hi]`.
- `null` renders as `—`.
- Table cells escape `|` and fold newlines into a space.
- Section headings are single-line: newlines and leading `#` are stripped.
- The report is markdown by D19. **The frontend must render it with raw HTML disabled**, because
  bodies are LLM text (noted in `00_index.md`'s frontend report row).

---

## 2. Files to Create / Modify

| Action | Path |
|---|---|
| modify | `tools/champion.py`: D-a in `rank_models`; add `select_champion` |
| create | `mcp_server/statlab_tools/tools/reporting.py`: `render_metrics_table`, `assemble_report` |
| create | `contracts/statlab_contracts/reporting.py`: `ReportSection(heading: str 1–80, body: str ≤ 8000)`, frozen, `extra="forbid"`. Node 12's LLM schema (chunk 29) produces it, so it lives in the shared package |
| modify | `tools/evaluation.py`: make `_load_score_table` and the `.4g` formatting helper public if reused; no behaviour change |
| modify | `tools/__init__.py`: one import line |
| modify | `tests/test_skeleton.py`: `check_lineage` gains `phase` (D-d); the driver reads `meta.phase`; guard cases |
| create | `tests/test_tools_reporting.py`, plus `select_champion` tests in `tests/test_tools_champion.py` |
| create | `tests/test_spine_reporting.py`: the end to end of §4 |
| modify | this file's frontmatter, `00_index.md` row 19, and a short "Chunk 19 — completed" section in `WHATS_NEXT.md`. Chunk 20's stage writes the state table and "Next" |

---

## 3. Implementation Spec

**Conventions:**
- `select_champion`: `phase="champion"`. `render_metrics_table` and `assemble_report`:
  `phase="reporting"`. All three: `allow_in=["champion_selection"]`.
- No compute libraries beyond what `artifacts.py` already uses; these are JSON and text tools.

### 3.1 `select_champion(ranking, candidate: str, rationale: str (1–2000))`

- `ranking` must be a `rank_models` table (it has `ranking` and `top_eligible`), else
  `tool_param`.
- An unknown candidate is `tool_param` naming the labels. An ineligible one is `tool_param` with
  its `ineligible_reason`. If no candidate is eligible, `select_champion` cannot be called
  successfully: node 12 escalates.
- Writes a `table` artifact (label `champion`) with: `candidate`, `estimator`, `rank`, `metric`,
  `value`, `ci_low`, `ci_high`, `top_eligible`, `overrides_top`,
  `top_two_eligible_distinguishable`, `selection_note`, `n_candidates` and `rationale`. The
  summary holds the same fields.

### 3.2 `render_metrics_table(scores: dict[str, ArtifactRef] (min 1), metrics: tuple[Metric, ...] = ())`

- Each value is a `score_*` table. Mixed tasks are `tool_param`. Differing `truth_fingerprint`s
  are `tool_param` too: a table compares rows, and rows scored on different data are not
  comparable (chunk 14 D-d).
- `metrics=()` means every metric of the task, in vocabulary order. A metric not of the task is
  `tool_param`.
- Rows are candidates in the given order; columns are `| candidate | m1 | m2 | … |`; cells per
  D-e.
- Writes a `report` artifact (`.md`, the table only). The summary has `markdown`, `metrics` and
  `n_rows`.

### 3.3 `assemble_report(title, champion, test_score, validation_table, sections: tuple[ReportSection, ...] = (), coefficient_table: ArtifactRef | None = None, plots: tuple[ArtifactRef, ...] = (), prior_test_scorings: <required, ge 0>, n_waves: <required, ge 1>)`

- `prior_test_scorings` and `n_waves` are `ORCHESTRATOR_SUPPLIED`.
- **Input checks:**
  - `champion` must be a `select_champion` table, else `tool_param`;
  - `test_score` must be a score table of the champion's task that has the champion's `metric`;
  - `validation_table` must be a `report` artifact;
  - `coefficient_table` must have `goals`;
  - each plot must be an existing `plot` artifact.
- Writes `RunPaths.report / "report.md"` (overwrite) and copies each plot to
  `report/plots/<ref>.png`.
- **Layout**, exactly:
  - `# {title}`
  - `## Result` — D-c's sentences, each present only when its condition holds; the test line
    always present.
  - `## Validation comparison` — the table.
  - `## Inference` (only with a coefficient table) — a table with columns `| question | terms
    (df) | statistic | p | p (Holm) | α | decision |`, where decision is `reject H0`, `not
    rejected` or `untestable`, followed by the D35 note.
  - `## {heading}` + `body`, for each section.
  - `## Figures` (only with plots) — `![{label or ref}](plots/{ref}.png)`.
- Returns no artifact. The summary has the relative `path`, `n_sections`, `n_plots` and the
  `sha256` of the file.

---

## 4. Test Suite Spec (~45)

**`rank_models` and `select_champion`.**
- The rank_models payload has D-a's keys and rows with CIs.
- Selecting the top eligible gives `overrides_top: false`; selecting rank 2 (eligible) gives
  `true`.
- An ineligible candidate is `tool_param` with the reason; an unknown label and a non-ranking
  table are `tool_param`.

**`render_metrics_table`.**
- A known markdown string for two candidates.
- `|` in a label is escaped. `null` renders as `—`.
- Differing fingerprints, mixed tasks, and a regression metric on classification tables are each
  `tool_param`.
- `metrics=()` lists all of the task's metrics.

**`assemble_report`.**
- Golden-section assertions: the test line contains the champion's metric value and CI exactly as
  in the score table, formatted per D-e. The validation line says "selection score" with N and W.
- The not-distinguishable sentence appears iff the flag is false.
- The exposure sentence appears iff `prior_test_scorings > 0`, with the count.
- The override sentence appears iff `overrides_top`.
- The inference table shows Holm p and decisions; `untestable` renders.
- Section order is exactly §3.3. A heading with a newline and a leading `#` is sanitised.
- Plots are copied, and their links resolve relative to `report.md`.
- A score table of the wrong task, or one lacking the metric, is `tool_param`; a missing plot is
  `tool_param`.
- Re-running overwrites the file.

**Skeleton guard cases** (`check_lineage`):
- a reporting tool with a test-derived input **before** the score is rejected, and accepted after;
- `select_champion` with a test-derived input is rejected in every state.

**End to end** (`tests/test_spine_reporting.py`, over MCP; reuse the skeleton's
`_open_driver`/`_run_spine` rather than copying them). For the classification fixture:
- after the spine, call `compute_roc_curve` and `plot_curve` on the champion's valid
  probabilities (in `model_evaluation`);
- then, in `champion_selection`: `select_champion` (the top eligible), `render_metrics_table`
  (the three valid scores), and `assemble_report` with one LLM-style section, the node-6
  coefficient table, the plot, `prior_test_scorings=0` and `n_waves=1`.

`report.md` exists and contains the champion's test value as recorded, "selection score",
"not distinguishable", both goal questions, and the image link. The regression fixture runs the
same flow without the plot.

---

## 5. Definition of Done

- [ ] `make test` exits 0, suite under 75 s. Coverage **100 %** line and branch. Chunk 14 and 16
      tests pass, with edits only where §2 names them.
- [ ] **Mutation evidence** (each makes at least one test fail; restore after each):
  - `select_champion` accepts an ineligible candidate;
  - `overrides_top` is always false;
  - the report takes the test value from the validation row;
  - the exposure sentence is dropped;
  - the not-distinguishable sentence is printed regardless of the flag;
  - `render_metrics_table` skips the fingerprint check;
  - `check_lineage` lets a reporting tool read test before the score.
- [ ] `make typecheck`, `make lint` exit 0. No `Any`, no `pragma: no cover`, no new
      `# type: ignore`.
- [ ] Housekeeping per §2. **Do not commit.**

### Stop and ask if

Chunk 14 tests need edits; the skeleton gate changes beyond D-d; anything needs `backend/`; a
`contracts/` change beyond `ReportSection`.

### Report back with

1. Gate numbers and suite time.
2. The mutation table.
3. The classification end-to-end `report.md`, verbatim.
4. For chunk 20: the `SpineResult`/`Driver` fields chunk 20 needs to build a call log.

---

## 6. Addendum — review corrections (2026-10-02)

- **The reporting clause was wider than D-d.** `check_lineage` let a `reporting`-phase tool read
  test-derived artifacts in `champion_selection` at any time. D-d allows it only **after** the one
  score. The clause is now `phase == "reporting" and test_scores >= 1`, with a guard case (a
  reporting tool in node 12 before the score is rejected) that only this condition decides.
  Deleting the condition, or the whole clause, now fails a test.
- The end-to-end `report.md` was read in full. The test line matches the score table (0.7304
  [0.6092, 0.8348]); the validation line says "selection score over 3 candidate(s) and 1
  wave(s)"; the not-distinguishable sentence and the Holm table are present.
