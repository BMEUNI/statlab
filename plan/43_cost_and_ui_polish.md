---
status: completed
dependencies: [42]
completion_date: 2026-10-08
implementation_notes: "43a prompt digest + skipped fixed LLM steps; 43b stepper, lucide icons, size-safe layout, mode/fit labels; make check 3117 passed, 100%; npm 79/79"
---

# Chunk 43 — cheaper runs and UI polish

User feedback 2026-10-08 after chunk 42 (verbatim asks, condensed):
- A run costs $0.30–0.40 on DeepSeek; make it cheaper. Chosen: "Trim + cache context",
  "Skip LLM on fixed steps".
- "Fit time 902.5 s of 1200 s" is unclear.
- Projects & runs shows "autonomous" for a run started in guided mode.
- Rework the step progress (preprocessing, features, training, ...) so the order is clear.
- Can I ask questions while a run is waiting for me? (Yes — answered in the report; the Q&A
  route already works at gates. The chat must say so.)
- Everything must fit the screen: dropdowns, tables and panels scroll inside themselves and never
  move the main layout.
- More icons. Chosen: lucide-react.

Evidence for the cost cut (paid run `r_261239930187a0a7`, 40 calls, $0.3241, deepseek-flash):
1.12 M prompt tokens, 26 % cached. Candidate nodes cost $0.23 of it: each candidate call sent
24–32 k tokens because `DomainRuntime.decide` puts every shared boundary dump (≈107 k chars:
full tool logs, long rationales, the wave-1 and wave-2 `experiment_design` both, champion report
sections) plus full sibling evaluation summaries (≈12 k chars each) into the user message, and
the system prefix puts the node's own skill section second, so DeepSeek's prefix cache breaks
after the preamble. Preprocessing and feature engineering (10 calls, $0.067) decide nothing on a
dataset with no missing values: feature engineering never has a free choice.

## Stage 43a — prompt digest, cache-friendly prefix, skip fixed LLM steps (backend)

Goal: cut input tokens per run by roughly 4–6x without changing any node behavior other than
the two skipped LLM calls. Non-goals: Q&A (`control.answer_question`) and gate revision
(`binding.py`) prompts stay as they are (user-triggered, not per-run cost); no model change; no
new dependency; no client change.

Relevant decisions: D18 (provider names only in `llm/`), PLAN prompt-assembly invariants
(stable prefix, untrusted tail). Add PLAN addendum row (main writes it).

### Owned files

- `backend/statlab/llm/prompts.py` — `PromptContext`.
- `backend/statlab/domains/runtime.py` — new `boundary_digest`, `decide` state summary.
- `backend/statlab/domains/candidate_pipeline.py` — `_preprocessing`, `_features`.
- `tests/test_prompt_context.py`, `tests/test_control_qa_status.py` (one assertion),
  new `tests/test_domain_runtime_digest.py`, `tests/test_nodes_candidate_pipeline.py`.

### 1. `prompts.py` — `PromptContext`

- Add a dataclass field after `memory`: `shared: JsonValue = None`.
- `__post_init__` prefix order becomes:
  `"\n\n".join((self.preamble, contract, _canonical(facts), _canonical(self.shared), self.skill.section))`
  (contract and facts are the existing canonical strings). Everything before the skill section
  is identical for every node of a run while shared results do not change, so the provider
  prefix cache hits across candidate calls.
- `messages` unchanged.
- Existing callers (`control.py:156`, `binding.py:115`) pass no `shared`; their prefix gains the
  literal `null` segment. That is accepted.

### 2. `runtime.py` — `boundary_digest`

Module-level, after `DecisionOverride`:
- `_DIGEST_DROP: frozenset[str] = frozenset({"registry", "candidate_set", "sections"})`
- `_SENTENCE = re.compile(r"(?<=[.!?])\s+")` (add `import re`).
- `def _brief(text: str) -> str`: `short = " ".join(_SENTENCE.split(text.strip())[:2])`;
  return `short` if `len(short) <= 400` else `short[:399] + "…"`.
- `def _prune(value: JsonValue, drop: frozenset[str]) -> JsonValue`:
  - dict: new dict over items in original order, skipping keys in `drop`; for key `"rationale"`
    whose value is a `str`, the value becomes `_brief(value)`; every other value is
    `_prune(value, drop)`.
  - list: `[_prune(item, drop) for item in value]`.
  - anything else: returned unchanged.
- `def boundary_digest(boundaries: Sequence[BoundaryResult], *, keep_tools: bool) -> list[JsonValue]`
  (import `Sequence` from `collections.abc`, `BoundaryResult` from `statlab.graph.state`):
  1. `drop = _DIGEST_DROP if keep_tools else _DIGEST_DROP | {"tools"}`.
  2. `latest: dict[str, BoundaryResult] = {}`; for each boundary in order:
     `latest[boundary.node_name] = boundary`. (Reassigning an existing key keeps its first
     position: the last entry per node sits where the node first appeared.)
  3. For each kept boundary build
     `{"node": node_name, "status": result.status, "summary": _prune(result.summary, drop)}`,
     then add `"warnings": list(result.warnings)` only when non-empty, `"skip_reason"` only when
     not None, `"error": result.error.model_dump(mode="json")` only when not None.
     `execution_id`, `artifacts` and `metrics` are omitted.
  4. Return the list.
- Docstring one line: the digest is prompt context only; code never reads it back.

### 3. `runtime.py` — `decide`

Replace the `PromptContext(...)` construction and the `state_summary` literal:
- `shared` value: `{"boundaries": boundary_digest(invocation.state.shared_results,
  keep_tools=not invocation.state.branches), "checkpointed_candidates": [branch.spec dump for
  each branch]}` (the existing `checkpointed_candidates` comprehension moves here unchanged).
- `prompt = PromptContext(self.skill.preamble, skill, self.contract, self.memory, shared)`.
- `state_summary` keeps only `"node"`, `"candidate"` (unchanged expressions) and
  `"sibling_validation"`; the same comprehension and filters, but each entry becomes
  `{"candidate": branch.public_id, "wave": branch.wave,
  "metrics": boundary.result.summary.get("metrics"),
  "primary_metric": boundary.result.summary.get("primary_metric"),
  "threshold": boundary.result.summary.get("threshold")}`.
- `"boundaries"` and `"checkpointed_candidates"` no longer appear in the user message.

Edge cases: no shared results → `"boundaries": []`; shared phase (no branches) keeps `tools`
so problem definition / profiling / splitting / design still see tool facts; a skipped
inference boundary keeps its `skip_reason`.

### 4. `candidate_pipeline.py` — skip fixed LLM steps

- New module function above `CandidatePipelineExecutor`:
  `def _no_missing_values(null_counts: JsonValue) -> bool` — True only when `null_counts` is a
  non-empty `dict` and every value satisfies `type(value) is int and value == 0` (bools and
  missing data return False).
- Module constants:
  - `_CLEAN_PREPROCESSING_RATIONALE = "No column has missing values (load_csv null counts are
    all zero), so the planned preprocessing runs without an LLM decision: constant-column
    handling follows the contract and no high-missing drop applies."`
  - `_CLEAN_PREPROCESSING_SUMMARY = "No missing values were found, so preprocessing used the
    planned settings without changes."`
  - `_FEATURE_RATIONALE = "Feature engineering applies the approved candidate specification
    exactly; it has no free choices."`
  - `_FEATURE_SUMMARY = "Built the planned features, encoding and scaling for this model."`
- `_preprocessing`, in this order:
  1. `pipeline`, `problem` as now.
  2. `intake = shared_result(invocation.state, "intake")` moves up here (delete the later
     duplicate assignment; later code reuses this variable).
     `null_counts = _tool_summary(intake, "load_csv").get("null_counts")`.
  3. If `_no_missing_values(null_counts)`: `decision = PreprocessingDecision(
     rationale=_CLEAN_PREPROCESSING_RATIONALE, user_summary=_CLEAN_PREPROCESSING_SUMMARY,
     drop_constant=self.runtime.contract.preprocessing.drop_constant)` (threshold stays None).
     Else: the existing `decide` call, with
     `evidence=(pipeline.model_dump(mode="json"), {"null_counts": null_counts})` and the same
     question text.
  4. The rest (exception approval, contract conflict check, drops, imputer, clipper, finish)
     unchanged.
- `_features`: replace the `decide` call, the `exception_approval` call and the
  `awaiting_user` early return with
  `decision = FeatureDecision(rationale=_FEATURE_RATIONALE, user_summary=_FEATURE_SUMMARY)`.
  Everything after (frequency refusal, transforms, `_finish` with its after-action approval)
  unchanged. Keep the `FeatureDecision` docstring.

### 5. Tests

`tests/test_prompt_context.py::test_prefix_tail_order_and_start_snapshot`: the expected system
content becomes `"\n\n".join(("Shared policy", canonical_contract, canonical_memory, "null",
"## node\nOwn section"))`. Add `test_shared_digest_precedes_skill_section`: `PromptContext("P",
_skill(), minimal_contract, (), {"b": 1})`; `messages(...)[0]["content"]` ends with
`'\n\n{"b":1}\n\n## node\nOwn section'`.

`tests/test_control_qa_status.py:64`: replace the `startswith("Captured policy\n\n## alpha")`
assertion with two: `startswith("Captured policy\n\n")` and `"## alpha" in
messages[0]["content"]`.

New `tests/test_domain_runtime_digest.py` (pure, no fixtures), importing `boundary_digest`
from `statlab.domains.runtime`, `BoundaryResult` from `statlab.graph.state`, `NodeResult` from
`statlab.domains.base`, `Failure` from `statlab_contracts.envelope`. Helper
`_b(node: str, result: NodeResult) -> BoundaryResult` with `execution_id=f"ne_{node}"`.
1. `test_last_entry_per_node_keeps_first_position`: boundaries intake completed
   `{"a": 1}`; experiment_design completed `{"x": 1}`; inferential_analysis skipped
   (`skip_reason="No goals"`); experiment_design completed `{"x": 2}`. With `keep_tools=True`
   expect exactly `[{"node": "intake", "status": "completed", "summary": {"a": 1}},
   {"node": "experiment_design", "status": "completed", "summary": {"x": 2}},
   {"node": "inferential_analysis", "status": "skipped", "summary": {}, "skip_reason": "No goals"}]`.
2. `test_prunes_bulky_keys_and_shortens_rationales`: one completed `champion_selection`
   boundary with summary `{"tools": [{"tool": "t"}], "registry": {"r": 1}, "candidate_set":
   {"c": 1}, "decision": {"rationale": "One. Two! Three? Four.", "candidate_set": {"c": 2},
   "sections": [1]}, "items": [{"rationale": "X. Y. Z.", "tools": 1}], "findings": ["kept"]}`.
   `keep_tools=False` summary equals `{"decision": {"rationale": "One. Two!"}, "items":
   [{"rationale": "X. Y."}], "findings": ["kept"]}`. `keep_tools=True` summary additionally has
   `"tools": [{"tool": "t"}]` and items `[{"rationale": "X. Y.", "tools": 1}]`.
3. `test_long_rationale_is_capped`: summary `{"rationale": "A" * 500}` → digest rationale
   is `"A" * 399 + "…"`.
4. `test_failure_warnings_and_error_are_kept`: failed boundary with `warnings=("w",)` and
   `error=Failure(error_class="data", message="m")` → entry has `"warnings": ["w"]` and
   `"error": Failure(error_class="data", message="m").model_dump(mode="json")`, and no
   `skip_reason` key.

`tests/test_nodes_candidate_pipeline.py`:
- Helper `_set_null_counts(h: Harness, counts: dict[str, int]) -> None`: dump `h.state` with
  `model_dump(mode="json")`, find the `shared_results` entry with `node_name == "intake"`, in its
  `result.summary.tools` list set the `load_csv` entry's `result.summary.null_counts` to
  `counts`, then `h.state = RunState.model_validate(dump)`.
- `test_decide_prompt_carries_digest_in_system_prefix`: `candidate_harness`; run index 6
  (fixture has no `null_counts`, so the LLM is called); `call = next(c for c in h.llm.calls if
  c[0] is PreprocessingDecision)`; `system, user = call[2][0]["content"], call[2][1]["content"]`.
  Assert `system.endswith("\n\nsection")`, `'"node":"intake"' in system`,
  `'"checkpointed_candidates"' in system`, `'"tool":"load_csv"' not in system`;
  `summary = json.loads(user.split("\n\n")[0])`; `set(summary) == {"node", "candidate",
  "sibling_validation"}`, `summary["node"] == "preprocessing"`,
  `summary["sibling_validation"] == []`.
- `test_preprocessing_skips_llm_without_missing_values`: `_set_null_counts(h, {"x": 0, "id": 0,
  "excluded": 0, "y": 0})`; set `h.llm.responses[PreprocessingDecision] =
  PreprocessingDecision(rationale="Unused", high_missing_threshold=0.5)`; record
  `before = len(h.llm.calls)`; run index 6 → `completed`; `len(h.llm.calls) == before`; tool names
  contain `"drop_constant_columns"` and not `"drop_high_missing_columns"`;
  `output.summary["decision"] == PreprocessingDecision(rationale=..., user_summary=...,
  drop_constant=True).model_dump(mode="json")` using the two exact constant strings (import the
  constants from `statlab.domains.candidate_pipeline`).
- `test_preprocessing_asks_llm_when_values_are_missing`: `_set_null_counts(h, {"x": 3, "id": 0,
  "excluded": 0, "y": 0})`; same 0.5-threshold response; run index 6 → `completed`; exactly one
  new LLM call and it is `PreprocessingDecision`; its user message contains
  `'{"null_counts":{"excluded":0,"id":0,"x":3,"y":0}}'`; one `drop_high_missing_columns` call
  with `threshold == 0.5`.
- Rename `test_feature_stage_waits_and_refuses_skip` to
  `test_feature_stage_is_deterministic_and_refuses_skip`: keep its preprocessing run/save and
  guided mode and the `FeatureDecision` response with `material_exceptions` (it must now be
  ignored); `before = len(h.llm.calls)`; run index 7 → `completed`; `len(h.llm.calls) == before`;
  `output.summary["decision"] == FeatureDecision(rationale=_FEATURE_RATIONALE,
  user_summary=_FEATURE_SUMMARY).model_dump(mode="json")`; keep the final `ValidationError`
  assertion unchanged. Remove the `awaiting_user`/`material_exceptions`/`offset` assertions.

### Checks (implementer)

- `make test-fast TESTS="tests/test_prompt_context.py tests/test_control_qa_status.py tests/test_domain_runtime_digest.py tests/test_nodes_candidate_pipeline.py tests/test_nodes_candidate_models.py tests/test_nodes_foundation.py tests/test_nodes_design_inference.py tests/test_nodes_candidate_search.py tests/test_candidate_fanout.py tests/test_control_router.py"`
- `.venv/bin/python -m ruff check` and `ruff format --check` on the four owned source/test
  Python files plus the new test file; `.venv/bin/python -m mypy backend/statlab/llm/prompts.py
  backend/statlab/domains/runtime.py backend/statlab/domains/candidate_pipeline.py
  tests/test_domain_runtime_digest.py tests/test_nodes_candidate_pipeline.py`.
- Probe (temporary, in the session scratchpad, not committed): load
  `projects/online-test-1/runs/r_261239930187a0a7/state.json` with `RunState.model_validate`,
  print the length of the canonical JSON (`json.dumps(..., sort_keys=True,
  separators=(",", ":"))`) of the full shared-results dump list versus
  `boundary_digest(state.shared_results, keep_tools=False)`. Report both numbers.

Stop and report on: any other test that breaks because it relied on the old prompt layout or a
feature-stage LLM call (report test id and assertion; do not change it).

### 43a handoff (2026-10-08)

Implemented as specified (implementer). Focused pytest (10 files, 269 tests) exit 0; Ruff check,
format and mypy exit 0. Probe on the paid run: shared results 102,423 chars full versus 26,039
digest (3.9x). One formatting-only deviation (question string re-wrapped, same text). Main
reviewed the diff: matches the recipe. Pre-action exception gates for preprocessing (clean data)
and feature engineering are now unreachable by design; the after-action approval still runs.

## Stage 43b — UI polish (frontend + one read field)

Goal: an ordered, icon-led step progress; icons across the run workspace; no layout break when
details, tables or dropdowns open; honest mode and fit-time labels; the chat says questions are
welcome while a decision waits. Non-goals: no new pages, no gate logic change, no new API
endpoint, no visual-identity change (navy/blue tokens stay).

Browser evidence (paid run, 1280x800, main measured): expanding a shared step (experiment
design, champion selection) widens the side pane to 10,700–17,700 px because `.tl-item` is
`grid-template-columns: 24px 1fr` (min-content track); chat messages overflow by 103 px because
`.msg.agent` is `28px 1fr` and labels like `logistic_regression_numeric_interactions` cannot
wrap. Tables already scroll inside `.table-wrap`.

### Dependency

From `frontend/`: `npm install --save-exact lucide-react@1.53.0` (peer React ^19 ok; main
verified the icon names below exist in 1.53.0's type declarations). Adds `"lucide-react":
"1.53.0"` to `dependencies` and updates `package-lock.json`. Import named icons only, e.g.
`import { Plus } from 'lucide-react'`. Every icon gets `className="icon"` (plus the extra
classes named below) and `aria-hidden="true"`; text labels stay, so no accessible name changes.

### Owned files

Backend: `backend/statlab/persistence/repository.py`, `backend/statlab/api/projects.py`,
`tests/test_repository.py`, `tests/test_api_projects.py`.
Frontend: `package.json`, `package-lock.json`, new `src/ui/icons.tsx`, `src/api/types.ts`,
`src/api/client.ts`, `src/run/model.ts`, `src/run/RunTimeline.tsx`, `src/run/RunHeader.tsx`,
`src/pages/RunPage.tsx`, `src/pages/ProjectPage.tsx`, `src/chat/RunChat.tsx`,
`src/chat/MessageList.tsx`, `src/chat/Composer.tsx`, `src/chat/RunSummary.tsx`, `src/App.tsx`,
`src/style.css`, `tests/run-model.test.ts`, `tests/client.test.ts`.

### 1. Backend: which runs switched from guided to automatic

`repository.py`, after `list_runs`:
`def runs_switched_to_autonomous(conn: sqlite3.Connection, project_id: str) -> tuple[str, ...]`
— docstring: "Runs where a gate answer chose approve_all_remaining (mode became autonomous)."
Body (same style as `list_runs`, no try/except): execute
`SELECT DISTINCT g.run_id FROM gates g JOIN runs r ON r.id = g.run_id WHERE r.project_id = ?
AND g.answer_json IS NOT NULL AND json_valid(g.answer_json)
AND json_extract(g.answer_json, '$.action') = 'approve_all_remaining' ORDER BY g.run_id`
with `(project_id,)`; return `tuple(str(row["run_id"]) for row in rows)`.

`projects.py`: `ProjectDetail` gains `switched_runs: tuple[str, ...]` (after `runs`); the detail
endpoint passes `switched_runs=runs_switched_to_autonomous(resource.conn, project.id)` (import
it next to `list_runs`).

Tests: `tests/test_repository.py` new `test_runs_switched_to_autonomous(migrated_db, project)`:
`first = champion_run(conn, project)`, `second = champion_run(conn, project)`; one execution per
run (`create_node_execution(conn, run_id=..., node_name="alpha", seq=1)`); on `first` a gate
transitioned with `answer_json=json.dumps({"action": "approve_all_remaining"})`; on `second` one
gate transitioned with `{"action": "approve"}` and one pending gate (phase "b", no answer);
assert `runs_switched_to_autonomous(conn, project.id) == (first.id,)`.
`tests/test_api_projects.py` after line ~311: `assert detail.json()["switched_runs"] == []`.

### 2. `src/api` — types and parser

`types.ts`: `ProjectDetail` gains `switched_runs: string[]`. `client.ts` `parseProjectDetail`
adds `switched_runs: stringArray(value.switched_runs)`. `tests/client.test.ts` (~line 270):
the `detail` fixture gains `switched_runs: ['r_1']`; add
`assert.deepEqual(parseProjectDetail(detail).switched_runs, ['r_1'])` and
`isInvalidResponse(() => parseProjectDetail({ ...detail, switched_runs: [1] }))`.

### 3. `src/run/model.ts` — helpers

- `export function switchedToAutomatic(gates: GateSummary[]): boolean` — true when any gate's
  `answer_json` parses (JSON.parse inside try; parse failure counts as no) to an object whose
  `action` is `'approve_all_remaining'`. Use `isJsonObject` for the object check.
- `export function modeLabel(mode: RunMode, switched: boolean): string` — `switched` →
  `'Guided, then automatic'`; else `'Guided'` for guided, `'Autonomous'` for autonomous.
- `export const STEP_SHORT_LABELS: Record<string, string> = { preprocessing: 'Clean',
  feature_engineering: 'Features', model_training: 'Train', model_evaluation: 'Evaluate',
  model_interpretation: 'Explain' }` and `export function stepShortLabel(node: string): string`
  returning the map entry or `nodeLabel(node)`.
- Tests in `tests/run-model.test.ts` (one `test` each): `switchedToAutomatic` false for `[]`,
  false for a gate with `answer_json: null`, false for `'{'`, false for
  `'{"action":"approve"}'`, true when one of two gates has `'{"action":"approve_all_remaining"}'`
  (build GateSummary literals with all seven fields); `modeLabel('guided', false) === 'Guided'`,
  `modeLabel('autonomous', false) === 'Autonomous'`, `modeLabel('autonomous', true) ===
  'Guided, then automatic'`; `stepShortLabel('model_training') === 'Train'`,
  `stepShortLabel('custom_step') === 'Custom step'`.

### 4. New `src/ui/icons.tsx`

- `NODE_ICONS: Record<string, LucideIcon>`: intake `FileInput`, problem_definition `Target`,
  data_profiling `ChartColumn`, data_splitting `Split`, experiment_design `FlaskConical`,
  inferential_analysis `Sigma`, preprocessing `Eraser`, feature_engineering `Shapes`,
  model_training `Cpu`, model_evaluation `Gauge`, model_interpretation `Lightbulb`,
  champion_selection `Trophy`, artifact_persistence `Archive`.
- `export function NodeIcon({ node }: { node: string })` renders `NODE_ICONS[node] ?? CircleDot`
  with `className="icon"`.
- `STATUS_ICONS: Record<CellStatus, LucideIcon>`: pending `CircleDashed`, running
  `LoaderCircle`, awaiting `Hand`, completed `CircleCheck`, skipped `SkipForward`, failed
  `CircleX`. `export function StatusIcon({ status }: { status: CellStatus })` renders it with
  `className={`icon status-icon icon-${status}`}`.
- `LucideIcon` is a type import from `lucide-react`; `CellStatus` from `../run/model.ts`.

### 5. `RunTimeline.tsx` — ordered stepper and icons

- Shared items: inside `tl-marker` render `<NodeIcon node={node.name} />`. In the status
  `mark` span, render `<StatusIcon status={status} />` before the label text.
- Candidate group heading "Candidate models": add a line under it,
  `<p className="tl-note">Each model goes through these steps in order: {layout.perCandidate.map(n => stepShortLabel(n.name)).join(', then ')}.</p>`
  (shown only when `view.branches.length > 0`).
- Lane head: champion pill becomes `<span className="pill signal"><Trophy …/>Champion</span>`.
  Under `lane-title` paragraph add `<p className="lane-progress">{done} of {n} steps done</p>`
  where `n = layout.perCandidate.length` and `done` counts per-candidate statuses equal to
  `'completed'` or `'skipped'`.
- Replace the `lane-steps` list with
  `<ol className="stepper" style={{ '--steps': n } as CSSProperties} aria-label=…same…>`;
  each `<li className={`step step-${status}`}>` holds the existing button, now with class
  `step-button` (keep `aria-pressed`, `onClick`), whose children are:
  `<span className="step-dot"><StatusIcon status={status} /></span>`,
  `<span className="step-name">{stepShortLabel(node.name)}</span>`,
  `<span className="sr-only">{nodeLabel(node.name)}: {CELL_LABELS[status]}</span>`, and the
  button gets `title={`${nodeLabel(node.name)}: ${CELL_LABELS[status]}`}`.
  Import `type CSSProperties` from react.

### 6. `RunHeader.tsx`

- New prop `switched: boolean`. Kicker text: `` `${modeLabel(run.mode, switched)} run` ``.
- Buttons get leading icons: Pause `Pause`, Resume `Play`, Stop the run `Square`, Rewind
  `RotateCcw`.
- Stats: each `dt` gets a leading icon (Agent cost `Coins`, fit time `Timer`, Started `Clock`).
  The fit-time `dt` text becomes `Model training time`; `dd` becomes
  `` `${used} s of ${limit} s allowed` `` (same number formatting as now).
- In the "Run details and rewind" `facts`, after "Live updates", when `view?.budget` exists add
  `<dt>Training time limit</dt><dd>The total time all candidate models may spend training.
  When it is used up, no further models start training.</dd>`.

### 7. `RunPage.tsx`

- Pass `switched={switchedToAutomatic(data.gates)}` to `RunHeader`.
- Tabs: each tab item gains an icon rendered before its label: Progress `ListChecks`,
  Candidates `Table2`, Files `FolderOpen`, Report `FileText`.
- "Waiting for you" `h2` gets a leading `Hand` icon.

### 8. `ProjectPage.tsx`

Mode cell: `{modeLabel(run.mode, project.data.switched_runs.includes(run.id))}`.

### 9. Chat

- `RunChat.tsx`: `gatePrompt` last part becomes `'Reply with the buttons below, or tell me in
  your own words what to change. You can also ask me a question first, for example "what does
  inferential analysis mean?".'` Quick-reply icons: Approve `ThumbsUp`, Approve and run the
  rest automatically `FastForward`, Review the details `SlidersHorizontal`. `INTRO` second line
  becomes `You can write to me at any time, also while I wait for your decision:`.
- `MessageList.tsx`: inside each `span.avatar` render `<Bot className="icon" aria-hidden="true" />`;
  the "Show the details" link-button gets a leading `ScanSearch` icon.
- `Composer.tsx`: Send button gets a leading `Send` icon (keep the text "Send").
- `RunSummary.tsx`: the `h3` "The run is finished" gets a leading `Trophy` icon.
- `App.tsx`: "New chat" gets `Plus`, "Projects & runs" gets `FolderOpen`.

### 10. `style.css` (append at the end, before nothing else; keep existing rules except the
three edits listed)

Edits to existing rules:
- `button, .button`: `display: inline-flex; align-items: center; justify-content: center;
  gap: 6px;` replace `display: inline-block`.
- `.tl-item`: `grid-template-columns: 28px minmax(0, 1fr)`; `.tl-item::before` `left: 13px;
  top: 30px`; `.tl-marker` `width: 28px; height: 28px; color: var(--graphite)`.
- Delete the two rules `.tl-done .tl-marker::after` and `.tl-fault .tl-marker::after`, and the
  whole `.lane-steps`, `.step-chip*`, `.chip-icon`, `.chip-*` block (lines ~273–284).

Appended rules (exact values):
- `.icon { width: 1em; height: 1em; flex: none; vertical-align: -0.125em; }`
- `.tl-done .tl-marker { color: var(--signal-ink); }` `.tl-fault .tl-marker { color: #fff; }`
  `.tl-current .tl-marker, .tl-awaiting .tl-marker { color: var(--signal); }`
- `.mark .status-icon { margin-right: 4px; }`
- `.split > * { min-width: 0; }`
- `.messages .msg.agent { grid-template-columns: 28px minmax(0, 1fr); }`
  `.msg-body { min-width: 0; overflow-wrap: anywhere; }`
  `.avatar { display: grid; place-items: center; color: #fff; font-size: 16px; }`
- `.side-pane, .chat-scroll { scrollbar-gutter: stable; }`
- `.tl-detail { min-width: 0; overflow-x: auto; }`
- `.json-view, .tl-detail pre { white-space: pre-wrap; overflow-wrap: anywhere; }` (user report
  2026-10-08: champion selection and artifact persistence "Arguments and result" blocks hold
  long unbroken text/JSON that widened the panel; JSON keeps its indentation, long lines wrap).
- `.facts { grid-template-columns: minmax(8rem, 16rem) minmax(0, 1fr); }`
  `.side-pane .facts { grid-template-columns: max-content minmax(0, 1fr); }`
- `input[type="text"], input[type="number"], select, textarea { min-width: 0; }`
- `.lane-progress { margin: 2px 0 0; color: var(--graphite); font-size: 0.8125rem; }`
- Stepper:
  `.stepper { list-style: none; margin: 12px 0 0; padding: 0; display: grid;
  grid-template-columns: repeat(var(--steps), minmax(0, 1fr)); }`
  `.step { position: relative; display: flex; justify-content: center; }`
  `.step + .step::before { content: ""; position: absolute; top: 15px; right: 50%; width: 100%;
  height: 2px; background: var(--rule); }`
  `.step-completed::before, .step-skipped::before, .step-running::before,
  .step-awaiting::before { background: var(--signal); }`
  `.step-button { position: relative; z-index: 1; flex-direction: column; gap: 4px; padding: 0
  2px; border: 0; background: none; box-shadow: none; min-width: 0; max-width: 100%; }`
  `.step-dot { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%;
  border: 2px solid var(--rule); background: var(--surface); color: var(--graphite);
  font-size: 16px; }`
  `.step-completed .step-dot { background: var(--signal); border-color: var(--signal); color:
  var(--signal-ink); }`
  `.step-running .step-dot { border-color: var(--signal); color: var(--signal); box-shadow: 0 0 0
  4px var(--target); }`
  `.step-awaiting .step-dot { border-color: var(--gate); background: var(--gate-soft); color:
  var(--gate); }`
  `.step-failed .step-dot { border-color: var(--fault); background: var(--fault-soft); color:
  var(--fault); }`
  `.step-skipped .step-dot { border-style: dashed; }`
  `.step-name { font-size: 0.75rem; line-height: 1.2; text-align: center; color: var(--ink);
  overflow-wrap: anywhere; }`
  `.step-button[aria-pressed="true"] .step-dot { outline: 2px solid var(--ink); outline-offset:
  2px; }`
  `.step-button:hover .step-dot { border-color: var(--signal); }`
- `@media (prefers-reduced-motion: no-preference) { .icon-running { animation: spin 1.2s linear
  infinite; } }` and `@keyframes spin { to { transform: rotate(360deg); } }`

Note: the `.tl-head .mark::before { display: none; }` rule already hides the old square, so the
status icon replaces it.

### Checks (implementer)

Backend: `make test-fast TESTS="tests/test_repository.py tests/test_api_projects.py"`; Ruff
check + format --check and mypy on `backend/statlab/persistence/repository.py`,
`backend/statlab/api/projects.py`, `tests/test_repository.py`, `tests/test_api_projects.py`.
Frontend (from `frontend/`): `npm test`, `npm run typecheck`, `npm run build` (capture each exit;
the known `use client` bundler warnings are acceptable).
Main then re-runs the browser overflow measurement and a visual review.

Stop and report on: an icon name that fails to import, a type error you cannot fix with a
mechanical import/annotation, or any test failure outside the files you changed.

### 43b handoff (2026-10-08)

Implemented (implementer, resumed once after an API usage limit). Backend focused pytest exit 0
(123 tests), Ruff/format/mypy exit 0; `npm test` 79/79, typecheck and build exit 0. Main browser
review on the offline stack (guided run `r_c8abd6fb44b74ddf`): gate prompt, quick replies, Q&A
while a gate waits ("Offline stand-in answer"), "Guided, then automatic run" after
approve_all_remaining, stepper, summary. Overflow: 28 states (every step detail with all
details open, every tab) plus an injected 5,000-char unbroken line in a JSON block and a chat
message: side pane, chat and page all 0 px horizontal overflow.

### 43b Addendum A — page scrolls behind the app

Main found `document.scrollHeight` 3,862 px at an 800 px viewport: five `.sr-only` spans
(`position: absolute`) inside list items have no positioned ancestor within the scroll panes,
so they extend the document and the whole app (header included) scrolls by 56 px when content is
scrolled into view. Correction in `frontend/src/style.css`, appended:
1. `.side-pane, .chat-scroll { position: relative; }` (absolute children are laid out and
   clipped inside their scroll pane).
2. `.run-stats dt .icon { margin-right: 4px; }` (icons in the run band stats touch their text).
Checks: `npm run build` (exit 0). Main re-measures `document.documentElement.scrollHeight`.
3. (Addendum A correction, main re-measure) Run page fixed (document 800 px = viewport). The
   project page still grew to 943 px at 390x844 from a `caption.sr-only` inside `.app-main.scroll`.
   Change rule 1 to `.side-pane, .chat-scroll, .app-main { position: relative; }`.
   Check: `npm run build`.

## Acceptance receipt (2026-10-08)

Candidate: `651d2a2` plus the chunk 43 working-tree diff (files staged in the chunk 43 commit).
- `make check`: exit 0; 3,117 passed, 1 designed skip; coverage 100 % (11,985 statements,
  3,446 branches); mypy "Success: no issues found in 195 source files"; Ruff "All checks
  passed!". Log: session scratchpad `check43.log` (temporary).
- Frontend: `npm test` exit 0 (79/79), `npm run typecheck` exit 0, `npm run build` exit 0.
- 43a probe: shared-results context 102,423 to 26,039 chars on the paid run's state.
- Browser (offline stack, guided run `r_c8abd6fb44b74ddf`, 1280x800 and 390x844): gate prompt
  and quick replies, Q&A answered while a gate waited, "Guided, then automatic" in run band and
  projects list, ordered stepper, run summary; 0 px horizontal overflow in 28 states plus
  injected 5,000-char unbroken lines; document height equals viewport on run and project pages.
- Covers the chunk 42 deferred guided-gate browser review.
- Not yet measured: the real per-run cost after 43a (needs a paid run with user confirmation).
