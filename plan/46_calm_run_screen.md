---
status: completed
dependencies: [45]
completion_date: 2026-10-08
implementation_notes: "exception gates ask once per attempt; one grouped decision box in the chat; no forced scroll; markdown chat; inference picker; lane scores; make check 3128 passed, 100%; npm 86/86"
---

# Chunk 46 — a calm run screen: grouped decisions, no jumping, brief chat

User feedback 2026-10-08 after online run `r_3decbe507e119ab4` (rewound from
`r_226cf43c814a3eb4`), verbatim list in the session; summary:

1. Chat and progress pane jump on every event; the current step, the user's question and the
   reply get lost.
2. Preprocessing asks once per model; wants one clean, small box to accept all, or accept/reject
   one by one.
3. Inferential analysis is always skipped and there is no visible option to run it.
4. Approving works sometimes only in the chat, sometimes only on the right.
5. Chat messages during the run are too long.
6. Evaluation asks once per model too.
7. Explain asks once per model and the user does not understand what is asked.
8. "Show the details" for model evaluation cannot be closed and takes a lot of space.
9. Chat does not render markdown tables or JSON.
10. (Later message) The lane still says "Not scored yet" after the run finished.
Keep: the final summary ("I LOVE THE FINAL SUMMARY") — `RunSummary` is not changed.

## Main's findings (read-only, 2026-10-08)

- Run `r_3decbe507e119ab4` opened 26 gates. 22 are per-candidate: preprocessing after ×4,
  training before+after (c_03), evaluation before+after ×4, interpretation before+after ×4.
- Cause of the doubles: `exception_approval` (`candidate_pipeline.py:97`) gates whenever
  `decision.material_exceptions` or `evidence.warnings` is non-empty. The LLM fills
  `material_exceptions` with routine caveats ("only 100 validation rows"), so the pre-action
  gate fires; `_finish` then calls it again after the action with the *same* decision and the
  *same* exceptions, so the user is asked twice. All 9 after-action gates of training,
  evaluation and interpretation had zero warnings (checked in `statlab.db`): pure repeats.
- Preprocessing after-gates come from the documented `fit_imputer` fallback warning
  ("'plan' is not numeric/datetime; default 'median' falls back to most_frequent"); the tool
  behaviour stays (plan/11 says the fallback "is recorded"); grouping makes it one click.
- The run stays `awaiting_user` until every pending gate is answered (events 26–47), so the UI
  can answer a group of gates one request after another.
- Inferential analysis: `design_inference.py:288` skips with "No fixed inference goals" when
  `objective.inference_goals` is empty. The only way to add goals is the setup drawer's
  Rules tab ("Add goal"), which the user never saw; neither the setup assistant nor the chat
  change parser knows the field.
- "Not scored yet": `RunTimeline` reads `mergedMetrics(branch)` = `NodeResult.metrics`, which
  no node fills; evaluation scores live in `summary.metrics[name].value` (already parsed by
  `compare.ts` `metricsOf` / `candidateRows`). `NodeInspector`'s metrics table has the same
  dead source.
- Jumping: `MessageList` forces `scrollTop = scrollHeight` on every message/draft/pending
  change (streamed answers emit ~280 deltas); pending-gate prompts are appended at the end of
  the chat and vanish when answered; the right pane renders full `GatePanel`s above the
  timeline, so each gate open/close shifts the whole timeline.
- The Q&A answer to "What does inferential analysis mean?" was 1,387 characters; step
  narration (`user_summary`) is 270–600 characters.
- Detail close: `RunTimeline` only closes the detail when the same step is clicked again; no
  close control exists.
- Do not change any Pydantic schema field or description (e.g. `Decision.user_summary`):
  `GateService._validate` compares stored `decision_schema` for non-applied gates, and the old
  superseded run still holds a pending failure gate; a schema change would break boot again
  (chunk 45). Brevity is enforced in prompts and in the UI instead.

PLAN: §8 addendum (chunk 46) records the gate semantics below. D27/D18 untouched.

Non-goals: no new API endpoint, no batch gate endpoint, no change to `GatePanel` itself, to
`RunSummary`, to the MCP tools, to `Decision`/`CandidateDecision` schemas, or to gate policy of
shared nodes. No auto-approval of gates that open later.

---

## Stage 46a — backend (one implementer)

Owned: `backend/statlab/domains/foundation.py`, `backend/statlab/domains/candidate_pipeline.py`,
`backend/statlab/domains/design_inference.py`, `backend/statlab/graph/control.py`,
`backend/statlab/graph/rewind.py`, `backend/statlab/api/assistant.py`; tests
`tests/test_nodes_candidate_pipeline.py`, `tests/test_nodes_candidate_models.py`,
`tests/test_nodes_design_inference.py`.

### 1. `_Evidence.acknowledged` (`foundation.py:232`)

Add a fourth dataclass field `acknowledged: int | None = None` after `warnings`. Meaning: the
number of `warnings` already shown to (or accepted without) the user by a pre-action check;
`None` = no pre-action check ran in this attempt. No other change to `_Evidence`.

### 2. `exception_approval` (`candidate_pipeline.py:97`)

Signature unchanged. New control flow (pseudocode, not a patch):

- If `after_action`:
  - `new_warnings` = `evidence.warnings` from index `evidence.acknowledged or 0` to the end.
  - `exceptions` = `decision.material_exceptions` when `evidence.acknowledged is None`,
    otherwise the empty tuple (a pre-action check already covered them).
- Else (pre-action): `new_warnings` = all of `evidence.warnings`;
  `exceptions` = `decision.material_exceptions`.
- If `exceptions` is empty and `new_warnings` is empty: when not `after_action`, set
  `evidence.acknowledged = len(evidence.warnings)`; return `decision`.
- `approval_evidence` = `(*evidence.tools, {"warnings": list(new_warnings)})` (only the new
  warnings, not all).
- `approved = await runtime.approve(invocation, decision, approval_evidence,
  after_action=after_action)` (unchanged call).
- Existing after-action refusal unchanged (`approved is not None and approved != decision`
  raises `_RefusalError(...)`).
- When not `after_action` and `approved is not None`: set
  `evidence.acknowledged = len(evidence.warnings)`.
- Return `approved`.

All callers stay as they are. Effect: an after-action gate opens only for warnings that
appeared after the pre-action check, or for exceptions when no pre-action check ran.

### 3. Inference skip text (`design_inference.py:288-289`)

`skip_reason` becomes the f-string
`f"No inference question was set, so no effect test was run. To add one, tell me for example: test whether <column> affects {problem.target}."`
(`<column>` is literal text).

### 4. Prompt wording (strings only)

- `control.py` `answer_question` (~line 330): append to the second system message content,
  after "...claim actions happened.": `" Reply briefly in plain language: at most three short
  sentences unless the user asks for more detail. Use a markdown list or table only when it
  makes the answer clearer."`
- `rewind.py` (~line 362): the example sentence becomes: `Put setting changes in edits as
  dotted paths into the current run contract with the new value, for example
  {"split.test_size": 0.3} or {"modelling.tuning.cv_folds": 10}; when the user asks to test
  whether inputs affect the target, add {"objective.inference_goals": [{"question": "Does
  <column> affect <target>?", "predictors": ["<column>"]}]} with the real column and target
  names, keeping any existing goals. ` followed by the unchanged "Only include targets and
  settings ..." text.
- `assistant.py` `SYSTEM_PROMPT`: insert after the sentence ending `'for example {"objective":
  {"primary_metric": "f1"}}.'` the text: `"Inferential analysis (testing whether chosen inputs
  affect the target) runs only when objective.inference_goals is set; when the user asks for
  it, use update_contract with for example {\"objective\": {\"inference_goals\":
  [{\"question\": \"Does tenure affect churn?\", \"predictors\": [\"tenure\"]}]}} using real
  column names. Once the contract exists and it has no inference goals, mention this option
  once in one short sentence. "` (keep a single space between sentences).

### 5. Tests

`tests/test_nodes_candidate_pipeline.py` (add imports `exception_approval` from
`statlab.domains.candidate_pipeline`, `_Evidence` from `statlab.domains.foundation`):

`test_exception_check_tracks_acknowledged_warnings(migrated_db, project, tmp_path)`:
- `h, _ = await candidate_harness(...)`; `h.state = h.state.model_copy(update={"mode": "guided"})`.
- `seen: list[tuple[bool, tuple[JsonValue, ...]]] = []`; `approve(inv, decision, evidence)`
  appends `(inv.after_action, evidence)` and returns `None if inv.after_action else decision`;
  `h.runtime = replace(h.runtime, approval=approve)`; `invocation = candidate_invocation(h, 6)`.
- `flagged = PreprocessingDecision(rationale="r", material_exceptions=("Sparse",))`,
  `clean = PreprocessingDecision(rationale="r")`.
- Pre-action clean: `quiet = _Evidence()`; result `is clean`; `quiet.acknowledged == 0`;
  `seen == []`.
- Pre-action flagged: `pre = _Evidence(warnings=["w0"])`; result `== flagged`;
  `pre.acknowledged == 1`; `seen[-1] == (False, ({"warnings": ["w0"]},))`.
- After-action, nothing new: `_Evidence(warnings=["w0"], acknowledged=1)` with `flagged` →
  result `is flagged`; `len(seen) == 1`.
- After-action, new warning: `_Evidence(warnings=["w0", "w1"], acknowledged=1)` → `None`;
  `seen[-1] == (True, ({"warnings": ["w1"]},))`.
- After-action, no pre-action check: `_Evidence()` with `flagged` → `None`;
  `seen[-1] == (True, ({"warnings": []},))`.

`tests/test_nodes_candidate_models.py` (add `BaseModel`, `JsonValue` to the pydantic import):

`test_approved_exceptions_are_not_asked_again_after_scoring(migrated_db, project, tmp_path)`:
`h, _ = await candidate_harness(...)`; `await prepare_candidate(h)`; `await _run_save(h, 8)`;
`_mode(h, "guided")`; `h.llm.responses[EvaluationActions] = EvaluationActions(rationale="Review
actions", material_exceptions=("Single validation split",))`; `phases: list[bool] = []`;
approval callback appends `inv.after_action` and returns `None if inv.after_action else
decision`; `h.runtime = replace(h.runtime, approval=approve)`; `output = await _run(h, 9)`;
assert `output.status == "completed"` and `phases == [False]`.

`tests/test_nodes_design_inference.py` `test_no_goals_skips_without_llm_or_tools`: add
`assert output.skip_reason is not None and "test whether <column> affects" in output.skip_reason`.

Existing tests must keep passing unchanged (notably
`test_evaluation_exception_wait_and_out_of_range_after_action_wait`,
`test_after_action_acknowledgment_cannot_change_completed_decision`,
`test_material_exception_gate_precedes_actions`).

### Checks (46a)

`make test-fast TESTS="tests/test_nodes_candidate_pipeline.py tests/test_nodes_candidate_models.py tests/test_nodes_design_inference.py tests/test_control_rewind.py tests/test_control_router.py tests/test_api_assistant.py tests/test_control_gates.py"`;
`.venv/bin/python -m ruff check`, `ruff format --check` and `mypy` on the 9 owned files.

---

## Stage 46b — frontend (one implementer, after 46a's receipt)

Owned (all under `frontend/`): `src/run/model.ts`, `src/run/compare.ts`,
`src/run/DecisionBox.tsx` (new), `src/run/RunTimeline.tsx`, `src/run/NodeInspector.tsx`,
`src/pages/RunPage.tsx`, `src/chat/RunChat.tsx`, `src/chat/MessageList.tsx`,
`src/chat/session.ts`, `src/chat/SetupPane.tsx`, `src/style.css`, `tests/run-model.test.ts`.
Reuse: `Markdown` (`src/report/Markdown.tsx`), `GatePanel` (unchanged), `answerGate`,
`candidateRows`, `cellStatus`, `latestOutcome`, `firstSentences`, `nodeLabel`, `humanize`,
`metricLabel`, `PHASE_LABELS`, `STATUS_LABELS`, `isJsonObject`, `setAt`/`getAt`, `errorText`.

### 1. `model.ts`

Remove `mergedMetrics`. Add (exported):

- `STEP_PURPOSES: Record<string, string>`:
  `preprocessing` → `"Clean the data for each model: fill missing values and drop unusable columns."`,
  `feature_engineering` → `"Build each model's inputs: encode categories and scale numbers."`,
  `model_training` → `"Train each model on the training rows."`,
  `model_evaluation` → `"Score each model on the validation rows."`,
  `model_interpretation` → `"Check which inputs each model relies on and where it makes mistakes."`.
- `gateNotes(question: GateQuestion): string[]` — strings of `question.proposal.material_exceptions`
  (when it is an array; non-strings ignored), then every string in `entry.warnings` for each
  evidence entry that is a JSON object whose `warnings` is an array; duplicates removed keeping
  first order.
- `type DecisionItem = { gate: GateSummary; question: GateQuestion }`;
  `type DecisionGroup = { key: string; node: string; phase: GatePhase; candidate: boolean;
  items: DecisionItem[]; notes: string[] | null }`.
- `groupDecisions(items: DecisionItem[]): DecisionGroup[]` — sort a copy by `gate.ts`
  ascending (stable); key = `gate:<gate.id>` when `question.candidateId === null`, else
  `<node>:<phase>`; groups keep first-appearance order; `candidate` = first item has a
  candidate id; `notes` = the single item's `gateNotes` for one-item groups, the first item's
  notes when every item's notes are equal (JSON-equal), otherwise `null`.
- `decisionLead(group: DecisionGroup): string` — first item's question `q`:
  phase `failure` → `` `This step failed: ${q.failure?.message ?? 'unknown error'}` ``;
  `group.candidate` → purpose = `STEP_PURPOSES[group.node] ?? nodeLabel(group.node) + '.'`, then
  phase `before` → `` `${purpose} I noted points to check before I start.` ``, phase `after` →
  `` `${purpose} Done; I noted points to check.` ``;
  shared → `firstSentences(q.proposal.user_summary, 2)` when that is a non-blank string, else
  `firstSentences(q.reasoning, 2)`.
- `nowLine(status: RunStatus, view: RunView | null, executions: ExecutionRecord[],
  pending: GateQuestion[]): string`:
  - `pending.length > 0`: count pending per node in first-seen order; part = `nodeLabel(node)`
    plus `` ` (${n} models)` `` when that node has a candidate gate count `n > 1`; return
    `` `Waiting for your decision: ${parts.join(', ')}` ``.
  - `status` is `running` or `pending` and `view !== null`: for each spine node in order,
    ids = `[null]` for `SHARED`, else every branch id; `n` = ids whose
    `cellStatus(node.name, id, executions, [], latestOutcome(view, node.name, id)) === 'running'`;
    when `n > 0`, part = `nodeLabel(node.name)` for SHARED, else
    `` `${nodeLabel(node.name)} (${n} model${n === 1 ? '' : 's'})` ``. Parts non-empty →
    `` `Working on: ${parts.join(', ')}` ``; empty → `'Starting the next step…'`.
  - otherwise (including `view === null` while running) → `STATUS_LABELS[status]`.
- `jsonBlock(text: string): string | null` — trimmed text starting with `{` or `[`: try
  `JSON.parse`; when the result is an array or JSON object return
  `JSON.stringify(value, null, 2)`; any parse error or other result → `null`. Other text → `null`.
- `splitLead(text: string): { lead: string; more: string | null }` — `lead =
  firstSentences(text, 1)`; `rest = text.trim().slice(lead.length).trim()`; `more = rest === ''
  ? null : rest`.

### 2. `compare.ts`

Export the existing `metricsOf` (no behaviour change).

### 3. `DecisionBox.tsx` (new)

Props: `runId: string; mode: RunMode; pending: { gate: GateSummary; question: GateQuestion |
null }[]; view: RunView | null; navigate: (route: Route) => void; compact: boolean;
openGate: string | null; onOpenGate: (gateId: string | null) => void`.

- `items` = pending entries with a parsed question; `broken` = entries with `question === null`.
- `groups = groupDecisions(items)`. `canApprove(item)` = options include `'approve'`.
- One `useMutation<void, Error, { gateId: string; revision: number; action: GateAction }[]>`:
  `mutationFn` answers the list strictly in order with `await answerGate(runId, gateId,
  { revision, action })`, stopping at the first throw; `onSettled` invalidates
  `['run', runId]` and `['executions', runId]` (same as `GatePanel.refresh`). While pending,
  every button in the box is disabled.
- `label(item)` = `humanize(branch.label)` of the matching `view.branches` id, fallback the
  candidate id.
- Render `<section className="decision-box" aria-labelledby="decision-heading">`:
  - `<h2 id="decision-heading">` with lucide `Hand` icon and text `` `Waiting for you (${items.length + broken.length})` ``.
  - Each broken entry: `<p role="alert">A decision cannot be displayed.</p>`; when not
    `compact` also `<pre className="json-view">{gate.question_json}</pre>`.
  - Each group: `<article className="decision-group" key={group.key}>`:
    - `<div className="decision-head">`: `<h3>` = `nodeLabel(group.node)` + (`items.length > 1`
      → `` ` · ${n} models` ``; one candidate item → `` ` · ${label(item)}` ``); then
      `<span className="pill gate">{PHASE_LABELS[group.phase]}</span>`.
    - `<p className="decision-lead">{decisionLead(group)}</p>`.
    - When not `compact`, `group.notes` non-empty: `<details className="decision-notes">` with
      summary `` `Points to check (${notes.length})` `` (plus `', the same for every model'`
      when `items.length > 1`) and a `<ul>` of notes.
    - Rows only when not `compact` and `items.length > 1`: `<ul className="decision-rows">`,
      per item `<li>`: `<span className="decision-model">{label(item)}</span>`; when
      `group.notes === null` and the item has notes, an item-level `decision-notes` details
      (`Points to check (k)`); `<span className="decision-row-actions">` with an `Approve`
      button (only if `canApprove`) answering `[{gate approve}]`, and a `link-button`
      "Review or change" with `aria-expanded={openGate === gate.id}` toggling
      `onOpenGate(openGate === gate.id ? null : gate.id)`; when `openGate === gate.id`, a
      `GatePanel` (same props as `RunPage` passes today: `skippable` from `view.spine`,
      `spine = view?.spine ?? []`, key `` `${gate.id}:${question.revision}` ``).
    - `<div className="decision-actions">`: when every item `canApprove`, a `primary` button
      `items.length > 1 ? `Approve all ${n}` : 'Approve'` answering every item with
      `approve` in group order; a button "Review or change" when `compact` (calls
      `onOpenGate(first.gate.id)`) or when not compact and `items.length === 1` (toggles like a
      row); for a non-compact one-item group, the `GatePanel` renders after this div when
      `openGate` equals its gate id.
  - Footer `<div className="decision-footer">` only when `mode === 'guided'`, `broken` is
    empty, every item `canApprove`, and some item's options include `approve_all_remaining`:
    button with lucide `FastForward` icon, text "Approve all and finish automatically",
    answering first the first item (in `groupDecisions` order) that offers
    `approve_all_remaining` with that action, then every other item with `approve`.
  - `answer.isError` → `<p role="alert">{errorText(answer.error)}</p>`.

### 4. `RunPage.tsx`

- New state `openGate: string | null` (initial `null`).
- `pendingQuestions` = parsed questions (as today's timeline prop).
- `now = nowLine(data.run.status, view, executions.data?.executions ?? [], pendingQuestions)`.
- Side pane: `<aside className="side-pane">` now contains `<div className="side-scroll">` with
  `RunHeader`, the execution-failed alert, and the tabs/panel (unchanged); the old "Waiting for
  you" section and its `GatePanel` map are removed (the `Hand` import goes too). After
  `side-scroll`, when `pending.length > 0`: `<div className="decision-dock"><DecisionBox …
  compact={false} openGate={openGate} onOpenGate={setOpenGate} /></div>`.
- `RunChat` props: drop `onShowGate`; add `now={now}` and
  `onOpenGate={(id) => { setOpenGate(id); setPane('side'); }}`.

### 5. `RunChat.tsx`

- Remove `valueText`, `gatePrompt`, the `answer` mutation, the `asked` gate messages, `last`
  and the quick replies (and now-unused imports).
- Props: remove `onShowGate`; add `now: string`, `onOpenGate: (gateId: string) => void`.
- `GUIDED` text: `'\n\nIn guided mode I stop and ask you before each key decision. Answer in
  the box below the chat, or in your own words.'`
- State `sent` (number, starts 0); Composer `onSend` increments `sent` then calls
  `mutation.mutate(text)`.
- `step` items: `const { lead, more } = splitLead(item.text)`; message `text: lead`, plus
  `more` when not null.
- Layout: `chat-tools` holds `<p className="chat-now" aria-live="polite">{now}</p>` before the
  existing checkbox label; `MessageList` gets `followKey={sent}` and keeps only the
  `RunSummary` child; between `MessageList` and `Composer`, when `pending.length > 0`:
  `<div className="decision-dock"><DecisionBox runId mode pending view navigate compact
  openGate={null} onOpenGate={(id) => { if (id !== null) onOpenGate(id); }} /></div>`.

### 6. `session.ts`

`ChatMessage` gains optional `more?: string`.

### 7. `MessageList.tsx`

- Props gain `followKey?: number` (default 0).
- `atBottom = useRef(true)`; state `unseen` (false). `onScroll` on the scroller: `atBottom =
  scrollHeight - scrollTop - clientHeight < 80`; when at bottom set `unseen` false.
- Replace the effect with `useLayoutEffect` on `[messages.length, draftLength, pending]`: when
  `atBottom.current` scroll to the bottom; otherwise set `unseen` true.
- `jump()`: scroll to bottom, `atBottom.current = true`, `unseen` false. A second
  `useLayoutEffect` on `[followKey]` calls `jump()` when `followKey > 0`.
- After the widget slot, when `unseen`: `<button type="button" className="jump-latest"
  onClick={jump}>` with lucide `ArrowDown` icon and text "New messages".
- Assistant message bodies (not drafts, not user/note messages) render through a local
  `RichText({ text })`: `jsonBlock(text)` non-null → `<pre className="json-view">`; else
  `<Markdown source={text} imageUrl={() => null} />`. When `message.more` is set, after the
  text: `<details className="msg-more"><summary>More</summary><RichText text={message.more}
  /></details>`. User messages and drafts keep `Paragraphs`.

### 8. `RunTimeline.tsx`

- Lane score: `const rows = candidateRows(view)` once; per branch `row = rows.find(r => r.id
  === branch.id)`; `score = row?.primaryMetric ? row.metrics[row.primaryMetric] : undefined`;
  text `score === undefined || row?.primaryMetric == null ? 'Not scored yet' :
  `${metricLabel(row.primaryMetric)} ${score.value.toFixed(3)}``.
- Detail block (both places that render `detail`): `<div className="tl-detail"
  ref={detailRef}>` whose first child is `<button type="button" className="link-button
  tl-close" onClick={() => onSelect(null)}>` with lucide `X` icon and text "Close the details",
  then `{detail}`. `detailRef = useRef<HTMLDivElement>(null)`; `useEffect` on
  `[selected?.node, selected?.candidateId]` calls `detailRef.current?.scrollIntoView({ block:
  'nearest' })`.

### 9. `NodeInspector.tsx`

Metrics table source becomes `metricsOf(outcome)` (import from `./compare.ts`): render only
when non-empty; columns "Metric" (`metricLabel(name)`), "Value" (`value.toFixed(4)`),
"95% interval" (`ciLow`/`ciHigh` both non-null → `` `${ciLow.toFixed(3)} to ${ciHigh.toFixed(3)}` ``, else `—`).

### 10. `SetupPane.tsx` (`ContractCard`)

- `save` mutation takes the contract to save: `mutationFn: async (next: JsonObject)` validates
  and uploads `next` (same calls as today); `EditStep onContinue={() => save.mutate(draft)}`;
  on success with a row also reset the new `column` state to `''`.
- State `column` (string, `''`). `target = text(getAt(contract, ['target', 'column']))`;
  `exclude`/`include` = string entries of `features.exclude` / `features.include` arrays (empty
  when absent); `eligible` = `profile.data?.columns` minus `target`, minus `exclude`, and, when
  `include` is non-empty, only members of `include`.
- After the `<dl>` and before "Edit details", when `target !== null` and `profile.data` is
  loaded: `<form className="inline-add">` with `onSubmit` (prevent default) that calls
  `save.mutate(setAt(contract, ['objective', 'inference_goals'], [...existing goals array or [],
  { question: `Does ${column} affect ${target}?`, predictors: [column] }]))`; a `field` with
  label (htmlFor `inference-column`) `` `Also test whether an input affects ${target}
  (inferential analysis)` ``, a `<select id="inference-column">` with an empty "Choose a column"
  option plus `eligible`; submit button "Add question", disabled when `column === ''` or
  `save.isPending`.
- Move the validation-mismatch list and the `save.isError` alert into one local JSX value and
  render it both after the inline form and where it is today inside the dialog.

### 11. `style.css`

- `.side-pane`: replace `overflow-y: auto; … padding: 24px 28px;` with `display: flex;
  flex-direction: column; overflow: hidden; padding: 0;` (keep background and border).
- New `.side-scroll { flex: 1; min-height: 0; overflow-y: auto; padding: 24px 28px; position:
  relative; }`.
- Mobile rule `.split.show-side .side-pane { display: block; … }` → `display: flex`.
- `.chat-tools`: add `display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px
  12px; align-items: center;`. New `.chat-now { margin: 0; color: var(--ink); font-weight: 600; }`.
- `.decision-dock { flex: none; max-height: 45%; overflow-y: auto; padding: 10px 16px;
  border-top: 1px solid var(--rule); background: var(--surface); box-shadow: 0 -4px 12px
  rgba(19, 33, 58, 0.08); }` and `.decision-dock:has(.panel.gate) { max-height: 80%; }`.
- `.decision-box h2 { display: flex; gap: 6px; align-items: center; font-size: 1rem; margin: 0
  0 8px; }`; `.decision-group { border: 1px solid var(--rule); border-left: 4px solid
  var(--gate); border-radius: var(--radius); padding: 8px 12px; margin-bottom: 8px; }`;
  `.decision-head { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }`;
  `.decision-head h3 { margin: 0; font-size: 0.9375rem; }`; `.decision-lead { margin: 4px 0;
  font-size: 0.875rem; color: var(--graphite); }`; `.decision-notes { margin: 4px 0;
  font-size: 0.8125rem; }`; `.decision-notes ul { margin: 4px 0 0; padding-left: 1.1rem; }`;
  `.decision-rows { list-style: none; margin: 6px 0; padding: 0; }`; `.decision-rows > li {
  display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; padding: 4px 0;
  border-top: 1px solid var(--rule); }`; `.decision-model { flex: 1; min-width: 8rem;
  font-size: 0.875rem; font-weight: 600; }`; `.decision-rows .panel.gate { flex-basis: 100%; }`;
  `.decision-actions, .decision-row-actions { display: flex; flex-wrap: wrap; gap: 6px;
  align-items: center; }`; `.decision-actions { margin-top: 6px; }`; `.decision-footer {
  margin-top: 4px; }`; `.decision-box button { padding: 4px 10px; font-size: 0.875rem; }`.
- `.jump-latest { position: sticky; bottom: 8px; display: flex; margin: 8px auto 0;
  box-shadow: var(--shadow); }`; `.tl-detail > .tl-close { display: flex; margin: 0 0 6px
  auto; font-size: 0.875rem; }`; `.msg-more { margin-top: 4px; font-size: 0.875rem; }`;
  `.msg-more summary { color: var(--signal); cursor: pointer; }`; `.inline-add { display:
  flex; flex-wrap: wrap; gap: 6px 8px; align-items: flex-end; margin: 8px 0 12px; }`.

### 12. Tests (`tests/run-model.test.ts`)

Drop `mergedMetrics` from imports and its assertion (keep the `latestOutcome` assertions; rename
the test to `latestOutcome`). Add (use the existing `gate()` helper; GateSummary literals
`{ id, run_id: 'r', node_execution_id: 'e', status: 'pending', question_json: '', answer_json:
null, ts }`):

- `gateNotes`: proposal `material_exceptions: ['A', 3, 'B']`, evidence `[{ tool: 'x' },
  { warnings: ['B', 'C', 4] }]` → `['A', 'B', 'C']`.
- `groupDecisions`: items given out of order — `g2` preprocessing/after/`c_02` ts `t2` with
  evidence warnings `['w']`; `g1` preprocessing/after/`c_01` ts `t1` same warnings; `g0`
  intake/before/null ts `t0`. Result keys `['gate:g0', 'preprocessing:after']`; second group
  items `['g1', 'g2']`, `candidate === true`, `notes` `['w']`; first group `notes` `[]`. With
  `g2`'s warnings changed to `['v']`, the second group's `notes === null`.
- `decisionLead`: candidate before → `STEP_PURPOSES.model_evaluation + ' I noted points to check
  before I start.'`; candidate after → `... + ' Done; I noted points to check.'`; shared with
  `proposal.user_summary: 'One. Two. Three.'` → `'One. Two.'`; shared without → first two
  sentences of `reasoning`; failure with `failure: { errorClass: 'data', message: 'boom' }` →
  `'This step failed: boom'`.
- `nowLine` with `view: RunView = { spine: [{ name: 'a', kind: 'SHARED', skippable: false },
  { name: 'b', kind: 'PER_CANDIDATE', skippable: true }], shared: [], branches: [{ id: 'c_01',
  label: 'A', estimator: null, status: 'running', wave: 1, retuneCount: 0, outcomes: [], spec:
  {} }], overlap: null, wave: 1, budget: null }` (import type `RunView`):
  pending `[gate({ node: 'preprocessing', candidateId: 'c_01' }), gate({ node: 'preprocessing',
  candidateId: 'c_02' }), gate({ node: 'intake', candidateId: null })]` →
  `'Waiting for your decision: Preprocessing (2 models), Intake'`; status `running`, pending
  `[]`, executions `[{ ...execution('x1', 1, 'running', 'c_01'), node: 'b' }, {
  ...execution('x2', 2, 'running'), node: 'a' }]` (existing `execution` helper) →
  `'Working on: A, B (1 model)'`; status `running` with executions `[]` →
  `'Starting the next step…'`; status `completed` → `'Completed'`; status `running` with
  `view` `null` → `'Running'`.
- `jsonBlock`: `'{"a":1}'` → `'{\n  "a": 1\n}'`; `' [1,2] '` → `'[\n  1,\n  2\n]'`;
  `'hello'`, `'{bad'`, `'"str"'` → `null`.
- `splitLead`: `'First one. Second one.'` → `{ lead: 'First one.', more: 'Second one.' }`;
  `'Only.'` → `{ lead: 'Only.', more: null }`.

### Checks (46b)

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.

## Acceptance (main)

Review both diffs; `make check` once; npm checks; offline browser check on serve_ui
:8766 / Vite :5174 (guided run with the offline stand-in): decision box shows grouped per-step
approvals in chat and right pane, approve-all works, chat stays put when scrolled up and shows
"New messages", details close, lane shows the score, markdown table renders, setup pane adds an
inference question. Then commit.

## Addendum A — review correction (46b)

`style.css`: the side pane no longer scrolls; `.side-scroll` does. (1) In the
`@media (max-width: 40rem)` block, `.side-pane { padding: 16px; }` becomes
`.side-scroll { padding: 16px; }` (the `.side-pane .run-band` rule there stays). (2) The rule
`.side-pane, .chat-scroll { scrollbar-gutter: stable; }` becomes
`.side-scroll, .chat-scroll { scrollbar-gutter: stable; }`. Checks: `npm run build`.

## Addendum B — one decision box, chat only (user feedback during acceptance)

User, 2026-10-08, on the first browser check: "the pop up acceptance stuff is duplicated, it just
shows up both in the chat and the progress right pane". Decision: the decision box lives only in
the chat dock, in full (non-compact) form; the right pane shows progress only. Main also saw the
chat not staying at the bottom when the dock appears (the scroll area shrinks, no message is
added, so nothing re-scrolls).

1. `DecisionBox.tsx`: remove the `compact` prop and every compact-only branch; the box always
   renders as the former non-compact form (group notes, per-model rows when `items.length > 1`,
   inline `GatePanel` when `openGate` matches, single-item "Review or change" toggle). The
   `broken` entries always show the `<pre className="json-view">`.
2. `RunChat.tsx`: remove the `onOpenGate` prop; add local state `openGate: string | null`
   (initial `null`); the dock renders `<DecisionBox … openGate={openGate} onOpenGate={setOpenGate} />`
   (no `compact`).
3. `RunPage.tsx`: remove the `openGate` state, the side-pane `decision-dock` and the
   `DecisionBox` import; `RunChat` no longer gets `onOpenGate`. Pane-switch buttons: the chat
   button reads `` `Chat (${pending.length} waiting)` `` when `pending.length > 0`, else `Chat`;
   the progress button always reads `Progress`.
4. `MessageList.tsx`: a `useEffect` with `[]` deps: when `scroller.current` exists, create a
   `ResizeObserver` whose callback scrolls the scroller to the bottom only when
   `atBottom.current` is true; observe the scroller and each of its element children present at
   mount (the messages list and the widget slot); disconnect on cleanup.
5. `style.css`: `.decision-dock:has(.panel.gate)` stays; no other change.

Checks: `npm test`, `npm run typecheck`, `npm run build` (from `frontend/`).

## Addendum C — messages after the run ends go below the final summary

Main's browser check: after the run completed, a new question and its answer were appended to
the message list, which sits above the final summary (`RunSummary` is the widget-slot child), so
both were out of view at the bottom.

1. `model.ts`: `RunFeed` gains `completedSeq: number | null`. `foldEvents` starts it at `null`;
   in the `run.status` case, when `p.status === 'completed'`, set it to that event's `seq`
   (the last such event wins). Returned with `items` and `drafts`.
2. `MessageList.tsx`: new optional prop `tail?: ChatMessage[]`. Move the per-message rendering
   into a local function used by both lists. After the widget slot (and before the jump
   button) always render a second `<ol className="messages tail">` containing the `tail`
   messages (empty when `tail` is undefined). Drafts and the typing indicator render at the end
   of the tail list when `tail !== undefined`, otherwise at the end of the first list as today.
   The ResizeObserver code is unchanged (the tail list exists at mount, so it is observed).
3. `RunChat.tsx`: build a second array `tail: ChatMessage[]`. While walking `feed.items`, push
   an item's message to `tail` instead of `messages` when `status === 'completed'`,
   `feed.completedSeq !== null` and `item.seq > feed.completedSeq`; otherwise to `messages` as
   today. `extra` messages go to `tail` when `status === 'completed'`, else to `messages`.
   Pass `tail={status === 'completed' ? tail : undefined}` to `MessageList`.
4. `style.css`: `.messages.tail { margin-top: 14px; }` and `.messages.tail:empty { display:
   none; }`.
5. Test (`tests/run-model.test.ts`, new test after `foldEvents builds the feed`): set
   `nextSeq = 0`; with the existing `event()` helper, events `run.status` `{ status: 'running' }`
   (seq 1), `run.status` `{ status: 'completed' }` (seq 2), `message.user` `{ message_id: 'm9',
   session_id: 's', text: 'Why?' }` (seq 3) → `foldEvents(...).completedSeq === 2`;
   `foldEvents([]).completedSeq === null`.

Checks: `npm test`, `npm run typecheck`, `npm run build` (from `frontend/`).

## Addendum D — phone padding order (46b)

Browser check at 390 px: `.side-scroll` keeps `padding: 24px 28px` because the base rule
(appended later in `style.css`) overrides the earlier `@media (max-width: 40rem)` rule, so the
`.side-pane .run-band` negative margins (-16px) no longer match. Fix in `style.css`: remove
`.side-scroll { padding: 16px; }` from the existing `@media (max-width: 40rem)` block and add at
the end of the file `@media (max-width: 40rem) { .side-scroll { padding: 16px; } }`. Checks:
`npm run build`.

## Acceptance receipt (2026-10-08)

Candidate: `c425a0a` plus this chunk's working-tree diff (backend 6 files, tests 3, frontend 12 + new
`DecisionBox.tsx`). Implementer focused evidence: 46a `make test-fast` (7 files) exit 0, 184 passed;
Ruff/format/mypy on 9 files exit 0. 46b plus Addenda A–D: `npm test` 86/86, typecheck and build
exit 0.

Main: `make check` exit 0 after 46a (backend unchanged afterwards; 46b touched only `frontend/`):
3128 passed, 1 designed skip, coverage 100 % (12,054 statements, 3,478 branches), mypy (195 files)
and Ruff clean (log `check46.log`, temporary). Final `npm test` 86/86, `npm run typecheck` and
`npm run build` exit 0.

Offline browser check (serve_ui :8766, Vite :5174, guided run `r_d0936b1a5d1a0f14`, churn.csv):
setup card added an inference question; one decision box in the chat only; preprocessing
appeared as "Preprocessing · 3 models" and "Approve all 3" answered all three; "Now" line tracked
the waiting step; chat stayed at the bottom when the dock appeared; lanes showed "ROC AUC 0.813 /
0.751 / 0.720" after completion; "Show the details" scrolled into view and "Close the details"
closed it; questions after completion render below the final summary; sending while scrolled up
jumps to the latest message; 390 px width has no horizontal overflow and correct padding. The
inferential-analysis failure in that run is an offline stand-in limit (it answers from the
scenario contract, not the edited one), not a product defect. Not exercised live: holding position
while scrolled up during a running run (logic covered by review only).

## Addendum E — setup pane lost its padding (post-commit user report)

User, 2026-10-08: "the right panel now doesnt have a left margin ... this looks terrible". Cause:
`SetupPane` (`frontend/src/chat/SetupPane.tsx`, default export, `<aside className="side-pane">`)
also uses `.side-pane`, whose padding and scrolling moved to `.side-scroll` in 46b; only
`RunPage` got the wrapper. Fix: in `SetupPane`'s returned JSX, wrap every child of the `<aside>`
(the `h2`, the `secondary-line` paragraph, the `ol.timeline` and the `ready` block) in one
`<div className="side-scroll">`. No CSS change. Checks: `npm run typecheck`, `npm run build`.

## Addendum F — "Read the full reasoning" repeats the lead (post-commit user report)

User, 2026-10-08: "Read the full reasoning always start repeating itself even though the first
one describes it.. so the first X sentence is always duplicated". Cause:
`frontend/src/run/GatePanel.tsx:100-110` shows `firstSentences(reasoning, 3)` and then the whole
`reasoning` inside the details block.

1. `model.ts` `splitLead(text: string, n = 1)`: `lead = firstSentences(text, n)`; rest unchanged.
   Existing callers keep the default.
2. `GatePanel.tsx`: `const reasoning = splitLead(question.reasoning, 3)` (import `splitLead`;
   drop the `firstSentences` import if it becomes unused). When `question.reasoning.trim() !==
   ''`: `<p>{reasoning.lead}</p>`, then, when `reasoning.more !== null`, `<details><summary>Read
   more</summary><p className="reasoning">{reasoning.more}</p></details>`.
3. Test (`tests/run-model.test.ts`, test `jsonBlock and splitLead`): add
   `assert.deepEqual(splitLead('A. B. C. D.', 3), { lead: 'A. B. C.', more: 'D.' })`.

Checks: `npm test`, `npm run typecheck`, `npm run build` (from `frontend/`).

## Addendum G — "finish automatically" moves into the box header (user request)

User, 2026-10-08: "approve all and finish automatically takes up a ton of space, move it
somewhere it doesnt take up place from the process right panel and also from the left chat
panel". Decision: put it on the decision box's own header line, right-aligned, as a small link.

1. `DecisionBox.tsx`: wrap the existing `<h2 id="decision-heading">…</h2>` in
   `<div className="decision-top">`. Move the button from the `decision-footer` div into that
   wrapper after the `h2`, with the same render condition and the same `onClick` answer list;
   give it `className="link-button"` and
   `title="Approve every waiting decision, then run the rest without asking"`; its content is the
   `FastForward` icon plus the text `Finish automatically`. Delete the `decision-footer` div.
2. `style.css`: replace the `.decision-footer { margin-top: 4px; }` rule with
   `.decision-top { display: flex; flex-wrap: wrap; justify-content: space-between; align-items:
   center; gap: 4px 12px; margin-bottom: 8px; }` and `.decision-top h2 { margin: 0; }`; add
   `.decision-top .link-button { font-size: 0.875rem; }`.

Checks: `npm run typecheck`, `npm run build` (from `frontend/`).
