---
status: implemented
dependencies: [35, 37]
completion_date: 2026-10-08
implementation_notes: "38a + 38b + Addendum A accepted on 49ecde3 dirty tree: make check exit 0 (3075 passed, 100% line/branch, mypy 191 files, ruff); frontend npm test 49/49, typecheck 0, build 0; Playwright acceptance on H9 offline stack covered all gate actions except abort (backend-tested), header pause/resume/stop/rewind, chat reply, inspector, autonomous completion, fork inheritance display."
---

# Chunk 38 — run workspace (graph, inspector, gates, chat, controls)

User requirement (2026-10-08): every backend feature must be usable in the UI. PLAN §12 item 3.
Two stages: 38a backend gate-edit check plus the frontend run data layer (pure, unit-tested);
38b components (spec written after 38a's handoff).

Backend features this chunk must expose: run snapshot (spine, branches, results, budget), node
executions with tool and LLM calls and cost, the event stream (node/gate/run/message events),
gate answers with all seven actions (approve, approve_with_edits, instruct,
approve_all_remaining, skip_node, rewind, abort), lifecycle control (pause, resume, cancel,
rewind fork), chat messages with streamed assistant replies.

Non-goals: artifact browsing/preview and candidate comparison (39), report viewer, downloads,
lineage tree and memory (40).

Verified facts (offline stack, 2026-10-08):
- Snapshot `gates[]` rows: `id, run_id, node_execution_id, question_json, answer_json|null,
  status, ts`. `question_json` = GateQuestion (`backend/statlab/graph/gates.py:30`): `node`,
  `candidate_id` (database id `"<len>:<run_id>:c_NN"` or null), `phase` before|after|failure,
  `proposal`, `decision_schema` (self-contained JSON schema), `decision_type`, `evidence[]`,
  `reasoning`, `editable[]` (empty after/failure), `default`, `options[]`, `context[]`,
  `revision`, `history[]`, `failure` `{error_class, message, ...}`|null, `awaiting_result`.
- Gate rules (`gates.py:287`): edits/instruct only at phase `before`; edit keys must be in
  `editable`; edits validate as `{**proposal, **edits}` through the decision model; failure gates
  offer `abort, rewind[, skip_node]`; skip only for skippable nodes.
- Lifecycle (`graph/lifecycle.py`): pause from pending/running/paused; cancel from
  pending/running/paused/awaiting_user/failed; resume only from paused.
- `state.spine[]` entries `{name, kind: SHARED|PER_CANDIDATE, skippable, ...}`; `state.branches[]`
  `{public_id: "c_NN", spec: {label, estimator: {name, ...}, ...}, results[], wave, position,
  retune_count, status, pending_result}`; results are BoundaryResult
  `{node_name, execution_id, result: {status, summary, artifacts[], metrics{}, warnings[],
  skip_reason, error}}`; `state.budget` `{total_fit_ms, limit_ms}`|null.
- `/api/runs/{id}/executions` rows: execution `candidate_id` is the database id; node event
  payload `candidate_id` is the public id `c_NN`. Execution `summary` is JSON text or null.
- Events: `run.status {status, current_node}`, `node.started {node, execution_id, candidate_id?}`,
  `node.completed|skipped|failed {..., result}`, `gate.opened {gate_id, node_execution_id,
  question}`, `gate.revised {gate_id, question}`, `gate.answered {gate_id, answer}`,
  `gate.closed {gate_id, revision, new_run_id|null}`, `run.forked {new_run_id, ...}`,
  `message.user {message_id, session_id, text}`, `message.assistant.delta {reply_to,
  session_id, text, reset}`, `message.assistant.complete {message_id, reply_to, session_id,
  text, response: {category, references, facts, template, new_run_id}}`, `error {reply_to,
  message, discard_draft}`.
- Invalid gate edits currently return the sanitized 422 "invalid request" with no reason.

## 38a — gate-edit check endpoint and run data layer

Owned files: `backend/statlab/api/projects.py`, `backend/statlab/graph/gates.py`,
`backend/statlab/api/runs.py`, `tests/test_api_runs.py`, `frontend/src/api/types.ts`,
`frontend/src/api/client.ts`, `frontend/src/api/useRunEvents.ts`, new
`frontend/src/run/model.ts`, `frontend/tests/client.test.ts`, new `frontend/tests/run-model.test.ts`,
`frontend/package.json` (test script only). Nothing else.

### Backend

1. `projects.py`: add module-level
   `def validation_issues(exc: ValidationError) -> tuple[ContractIssue, ...]` directly after
   `ContractCheck`, containing exactly the tuple-building expression now inside
   `check_contract` (same `loc` join, `removeprefix("Value error, ")`, same `errors(...)` flags).
   `check_contract` returns `ContractCheck(errors=validation_issues(exc))`. Docstring:
   "Pydantic errors as path and message only; never input, context or URL."
2. `gates.py`, `GateService`: new public method after `_validate`:
   `def check_edits(self, gate: GateRow, edits: dict[str, JsonValue]) -> None` with docstring
   "Validate approve_with_edits edits without answering; raises ValidationError or EngineError."
   Body: `question = self._validate(gate)`; if `not set(edits) <= set(question.editable)`:
   `raise EngineError("edits include a noneditable field")`;
   `self._schema(question).model_validate({**question.proposal, **edits})`.
3. `runs.py`:
   - import `ContractCheck` and `validation_issues` from `statlab.api.projects` (extend the
     existing import line).
   - model `GateEditCheck(BaseModel)`: `model_config = ConfigDict(frozen=True, extra="forbid")`,
     `edits: dict[str, JsonValue]`. Place after `GateSubmission`.
   - `RunRegistry.check_edits(self, run_id: str, gate_id: str, body: GateEditCheck) -> ContractCheck`
     (async), placed after `answer`: `async with self.mutation():` `binding = self.run(run_id)`;
     `gate = get_gate(self.resources.conn, gate_id)`; `None` or `gate.run_id != run_id` ->
     `HTTPException(404, "gate not found")`; `try: binding.gates.check_edits(gate, dict(body.edits))`
     `except ValidationError as exc: return ContractCheck(errors=validation_issues(exc))`
     `except EngineError: raise HTTPException(409, "run transition rejected") from None`;
     return `ContractCheck(errors=())`. No gate task settling, no audit anchor, no events.
   - route in `runs_router` after `answer_endpoint`:
     `@router.post("/api/runs/{run_id}/gates/{gate_id}/check", response_model=ContractCheck)`
     `async def check_edits_endpoint(run_id: str, gate_id: str, body: GateEditCheck) -> ContractCheck`
     returning `await registry().check_edits(run_id, gate_id, body)`.
4. `tests/test_api_runs.py`: new `test_gate_edit_check_reports_issues_without_answering`
   modelled on `test_gate_input_validation_and_stale_revision` (same
   `FixtureRecords(replies=deque([_decision()]))`, `_open_run_api`, `_create_run`, `_gate`):
   - `{"edits": {"rationale": 7}}` -> 200, `errors == [{"path": "rationale", "message": "Input should be a valid string"}]`.
   - `{"edits": {"rationale": "Edited"}}` -> 200, `{"errors": []}`.
   - `{"edits": {"unknown_field": "x"}}` -> 409.
   - `{"edits": []}` -> 422.
   - unknown gate `g_0000000000000000` -> 404; `/api/runs/missing/gates/{gate.id}/check` -> 404.
   - afterwards the gate is unchanged: `(await _gate(api, run_id)).id == gate.id`, its
     `status == "pending"` and `answer_json is None`; then answer it with
     `{"action": "approve"}` -> 200 and finish with `await _pause_release(api, run_id)` exactly
     like the model test.
   If `_decision()`'s schema makes `rationale` not a string field, stop and report.

### Frontend types (`src/api/types.ts`)

Add (keep existing exports; `GateSummary` gains three fields):
- `GateSummary`: add `question_json: string; answer_json: string | null; ts: string`.
- `GateAction = 'approve' | 'approve_with_edits' | 'instruct' | 'approve_all_remaining' | 'skip_node' | 'rewind' | 'abort'`.
- `GatePhase = 'before' | 'after' | 'failure'`.
- `GateQuestion = { node: string; candidateId: string | null; phase: GatePhase; proposal: JsonObject; decisionSchema: JsonObject; evidence: JsonValue[]; reasoning: string; editable: string[]; defaultAction: GateAction; options: GateAction[]; context: string[]; revision: number; history: JsonValue[]; failure: { errorClass: string; message: string } | null }`
  (`candidateId` is the public id).
- `GateSubmission = { revision: number; action: GateAction; edits?: JsonObject; instruction?: string; reason?: string; target?: string }`.
- `ControlReply = { text: string; category: string; newRunId: string | null }`.
- `ExecutionRecord = { id: string; candidateId: string | null; node: string; seq: number; attempt: number; status: string; summary: JsonObject | null; error: string | null; startedAt: string; endedAt: string | null }` (public candidate id).
- `ToolCallRecord = { id: string; executionId: string; tool: string; args: JsonValue | null; result: JsonValue | null; durationMs: number; ts: string }`.
- `LlmCallRecord = { id: string; executionId: string; signature: string; model: string; promptTokens: number; completionTokens: number; cachedTokens: number; costUsd: number; durationMs: number; ts: string }`.
- `RunExecutions = { executions: ExecutionRecord[]; toolCalls: ToolCallRecord[]; llmCalls: LlmCallRecord[]; costUsd: number }`.

### Frontend client (`src/api/client.ts`)

- `parseRunSnapshot`: each gate also reads `question_json: requiredString`, `answer_json: nullableString`, `ts: requiredString`.
- `publicCandidateId(value: string | null): string | null` (exported): null -> null; else the
  substring after the last `':'` (whole string when there is no colon).
- `parseExecutions(value: unknown): RunExecutions` (exported): object with arrays
  `executions`, `tool_calls`, `llm_calls` and finite number `cost_usd`, else invalid response.
  Execution: `id, node_name, status, started_at` required strings; `seq, attempt` required numbers;
  `candidate_id` nullable string mapped through `publicCandidateId`; `summary` nullable string ->
  `null` when null, else `JSON.parse` result when it is a JSON object, else `null` (malformed
  summary text never throws); `error` nullable string; `ended_at` nullable string.
  Tool call: `id, node_execution_id, tool_name, ts` strings, `duration_ms` number, `args_json` and
  `result_json` strings parsed with `JSON.parse` -> JSON value, `null` when unparseable.
  LLM call: `id, node_execution_id, signature, model, ts` strings; `prompt_tokens,
  completion_tokens, cached_tokens, cost_usd, duration_ms` numbers. Any wrong type -> invalid response.
- `parseControlReply(value: unknown): ControlReply` (exported): object with string `text` and
  string `category`; `new_run_id` string -> it, null/absent -> null, other -> invalid response.
- `getExecutions(runId, signal?)`: GET `/api/runs/{enc}/executions` -> `parseExecutions`.
- `answerGate(runId, gateId, submission: GateSubmission)`: POST `/api/runs/{enc}/gates/{enc}`
  with JSON `{revision, action}` plus each of `edits, instruction, reason, target` only when
  defined -> `parseControlReply`.
- `checkGateEdits(runId, gateId, edits: JsonObject)`: POST `/api/runs/{enc}/gates/{enc}/check`
  `{edits}` -> `parseContractCheck` (returns `{field, message}` objects).
- `controlRun(runId, action: 'pause' | 'resume' | 'cancel')`: POST `/api/runs/{enc}/control`
  `{action}` -> `parseControlReply`.
- `rewindRun(runId, target: string, requestId: string)`: POST `/api/runs/{enc}/control`
  `{action: 'rewind', target, request_id: requestId}` -> `requiredString(value, 'new_run_id')`
  after the object check (returns the new run id). Callers pass `crypto.randomUUID()`.
- `sendMessage(runId, text)`: POST `/api/runs/{enc}/messages` `{text}` -> `parseControlReply`.

### Events hook (`src/api/useRunEvents.ts`)

Add `events: RunEvent[]` to both state types: reset to `[]` wherever state resets; on event
append (`[...current.events, event]`); the mismatched-run early return gives `events: []`.
Keep `lastEvent` and `lastSeq`.

### Run model (`src/run/model.ts`, new, pure, no React)

Imports types from `../api/types.ts`, `isJsonObject` and `publicCandidateId` from `../api/client.ts`.

- `GATE_ACTIONS: GateAction[]` in the order approve, approve_with_edits, instruct,
  approve_all_remaining, skip_node, rewind, abort.
- `ACTION_LABELS: Record<GateAction, string>`: approve "Approve", approve_with_edits "Approve with
  my edits", instruct "Ask for a revision", approve_all_remaining "Approve and run the rest
  automatically", skip_node "Skip this step", rewind "Rewind to an earlier step", abort "Stop the run".
- `nodeLabel(name: string): string`: underscores -> spaces, first character upper-cased
  (`model_training` -> `Model training`).
- `parseGateQuestion(text: string): GateQuestion | null`: `JSON.parse` in try (failure -> null);
  must be an object with string `node`, `phase` in GatePhase, object `proposal`, object
  `decision_schema`, array `evidence`, string `reasoning`, string-array `editable`, `default` and
  every `options` entry in `GATE_ACTIONS`, string-array `context`, non-negative integer
  `revision`, array `history`, `candidate_id` string|null (mapped by `publicCandidateId`),
  `failure` null or object with string `error_class` and `message`; anything else -> null.
- `type SpineNode = { name: string; kind: 'SHARED' | 'PER_CANDIDATE'; skippable: boolean }`;
  `type NodeOutcome = { node: string; executionId: string; status: string; summary: JsonObject; artifacts: string[]; metrics: Record<string, number>; warnings: string[]; skipReason: string | null; error: { errorClass: string; message: string } | null }`;
  `type Branch = { id: string; label: string; estimator: string | null; status: string; wave: number; retuneCount: number; outcomes: NodeOutcome[]; spec: JsonObject }`;
  `type RunView = { spine: SpineNode[]; shared: NodeOutcome[]; branches: Branch[]; overlap: string | null; wave: number; budget: { usedMs: number; limitMs: number } | null }`.
- `parseRunView(state: JsonObject): RunView | null`: `spine` must be an array of objects with
  string `name`, `kind` SHARED|PER_CANDIDATE, boolean `skippable` -> else null. `shared` from
  `shared_results` (missing -> []), each via `outcome(entry)`; entries that fail are dropped.
  `outcome(entry)`: object with string `node_name`, string `execution_id`, object `result` with
  string `status` -> NodeOutcome (`summary` object or {}, `artifacts` string items only,
  `metrics` finite-number values only, `warnings` strings only, `skip_reason` string|null,
  `error` object with string `error_class`/`message` or null); else null.
  `branches` (missing -> []): object with string `public_id` (else dropped); `label` =
  `spec.label` string or the id; `estimator` = `spec.estimator.name` string or null; `status`
  string or "pending"; `wave` number or 1; `retuneCount` = `retune_count` number or 0;
  outcomes from `results`; `spec` object or {}. `overlap` = `traversal.overlap_shared` string or
  null; `wave` number or 1; `budget` from `{total_fit_ms, limit_ms}` numbers else null.
- `type GraphLayout = { before: SpineNode[]; perCandidate: SpineNode[]; after: SpineNode[] }`;
  `graphLayout(spine)`: `first`/`last` = first/last index with kind PER_CANDIDATE; none -> all in
  `before`; else `before = spine[0..first)`, `perCandidate` = PER_CANDIDATE entries in
  `[first..last]`, `after = spine(last..]` (SHARED entries inside the range go to `after`).
- `type CellStatus = 'pending' | 'running' | 'awaiting' | 'completed' | 'skipped' | 'failed'`;
  `cellStatus(node, candidateId: string | null, executions: ExecutionRecord[], pending: GateQuestion[]): CellStatus`:
  any pending question with same `node` and `candidateId` -> 'awaiting'; else the execution with
  the highest `seq` among those with same `node` and `candidateId`: none -> 'pending';
  status 'pending'|'running' -> 'running'; 'awaiting_user' -> 'awaiting';
  'completed'|'skipped'|'failed' -> same; anything else -> 'pending'.
- `executionsFor(node, candidateId, data: RunExecutions)`: executions with that node/candidate
  sorted by `seq` descending, each paired with its tool calls and LLM calls (filter by
  `executionId`): returns `{ execution, toolCalls, llmCalls }[]`.
- `gateEdits(proposal: JsonObject, draft: JsonObject, editable: string[]): JsonObject`: for each
  key in `editable` present in `draft` whose `JSON.stringify` differs from `proposal[key]`'s,
  copy `draft[key]`. Keys absent from `draft` are not sent (clearing a field keeps the proposal).
- `availableActions(question: GateQuestion, skippable: boolean): GateAction[]`: `GATE_ACTIONS`
  filtered to `question.options`, minus approve_with_edits and instruct unless phase is
  'before', minus approve_with_edits when `editable` is empty, minus skip_node unless `skippable`.
- `type FeedItem =
  | { kind: 'user'; seq: number; ts: string; messageId: string; text: string }
  | { kind: 'assistant'; seq: number; ts: string; replyTo: string; text: string; category: string | null; newRunId: string | null }
  | { kind: 'error'; seq: number; ts: string; replyTo: string; text: string }
  | { kind: 'activity'; seq: number; ts: string; text: string }
  | { kind: 'fork'; seq: number; ts: string; newRunId: string }`;
  `type RunFeed = { items: FeedItem[]; drafts: Record<string, string> }`.
- `foldEvents(events: RunEvent[]): RunFeed`, events in given order; skip an event whose
  required payload fields have the wrong type. `where(p)` = `nodeLabel(p.node)` plus
  ` (${candidate_id})` when `candidate_id` is a string. Mapping:
  - `message.user` (string message_id, text) -> user item.
  - `message.assistant.delta` (string reply_to, text, boolean reset): `reset` -> drafts[reply_to] = text,
    else drafts[reply_to] = (existing or '') + text.
  - `message.assistant.complete` (string reply_to, text): delete drafts[reply_to]; assistant item
    with `category` = `response.category` string or null, `newRunId` = `response.new_run_id`
    string or null.
  - `error` (string reply_to, message): delete drafts[reply_to]; error item with text = message.
  - `node.started|completed|skipped|failed` (string node): activity `"${where} started"`,
    `"... completed"`, `"... skipped"`, `"... failed"`.
  - `gate.opened` (object question with string node): activity
    `"Waiting for your decision at ${where(question)}"` where the candidate comes from
    `publicCandidateId(question.candidate_id)` when it is a string.
  - `gate.revised` -> activity "The agent revised its proposal".
  - `gate.answered` (object answer with string action in GATE_ACTIONS) -> activity
    `"You chose: ${ACTION_LABELS[action]}"`.
  - `gate.closed` with string `new_run_id` -> fork item; otherwise nothing.
  - `run.forked` (string new_run_id) -> fork item.
  - `run.status` (string status) -> activity `"Run ${status.replace('_', ' ')}"`.
  - anything else ignored.

### Frontend tests

- `frontend/package.json`: append `tests/run-model.test.ts` to the existing `node --test` file list.
- `client.test.ts`: snapshot fixture gate gains `question_json: '{}', answer_json: null, ts: 'now'`;
  add invalid-response case `{ ...snapshot.gates[0], question_json: 1 }`.
  `publicCandidateId`: `null` -> null, `'18:r_x:c_01'` -> `'c_01'`, `'c_02'` -> `'c_02'`.
  `parseExecutions`: one execution (candidate `'5:r_abc:c_01'`, summary `'{"a":1}'`), one with
  summary `'not json'` -> summary null, one tool call with `args_json: '{bad'` -> args null and
  `result_json: '{"ok":true}'`, one LLM call, `cost_usd: 0.5` -> exact mapped object;
  `cost_usd: 'x'` -> invalid response. `parseControlReply`: with/without `new_run_id`, and
  `new_run_id: 1` -> invalid. Using the existing `withFetch` helper: `answerGate('r1', 'g1',
  {revision: 2, action: 'skip_node', reason: 'r'})` posts to `/api/runs/r1/gates/g1` with body
  exactly `{"revision":2,"action":"skip_node","reason":"r"}`; `rewindRun('r1', 'intake', 'u1')`
  with response `{new_run_id: 'r2'}` returns `'r2'` and posts
  `{"action":"rewind","target":"intake","request_id":"u1"}`; `sendMessage` posts `{text}` to
  `/api/runs/r1/messages`; `checkGateEdits` posts `{edits}` to `/api/runs/r1/gates/g1/check`.
- `run-model.test.ts` (new): `nodeLabel`; `parseGateQuestion` valid minimal question (candidate
  `'5:r_abc:c_02'` -> `'c_02'`), invalid JSON -> null, unknown option -> null, bad phase -> null;
  `parseRunView` on a state with spine of 4 entries (SHARED a, PER_CANDIDATE b, PER_CANDIDATE c,
  SHARED d), one shared result, one branch with label and estimator, budget -> exact view;
  spine missing -> null; `graphLayout` -> before [a], perCandidate [b, c], after [d], and an
  all-SHARED spine -> everything in before; `cellStatus` for awaiting (pending question),
  highest-seq wins (seq 1 failed, seq 2 completed -> completed), running, none -> pending;
  `executionsFor` ordering and call pairing; `gateEdits` (changed key, unchanged key, key absent
  from draft, non-editable key ignored); `availableActions` for phase before with all options and
  non-skippable node, phase after (no edits/instruct), failure options; `foldEvents` over a
  sequence: message.user, two deltas (second with reset true), assistant.complete with
  `new_run_id`, error for another reply_to with a draft, node.started with candidate,
  gate.opened, gate.answered, gate.closed with new_run_id, run.status `awaiting_user`, an unknown
  type, and a malformed node.started (node 1) -> exact items and empty drafts; plus a
  delta-only sequence leaving `drafts == { m1: 'Hello' }`.

### Checks

- `make test-fast TESTS="tests/test_api_runs.py tests/test_api_projects.py"`
- `.venv/bin/python -m ruff check backend/statlab/api/projects.py backend/statlab/api/runs.py backend/statlab/graph/gates.py tests/test_api_runs.py`
- `.venv/bin/python -m ruff format --check` on the same files
- `.venv/bin/python -m mypy backend/statlab/api/projects.py backend/statlab/api/runs.py backend/statlab/graph/gates.py tests/test_api_runs.py`
- From `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.
Stop and report on any failure or missing decision; do not change other files.

### 38a handoff (2026-10-08)

Implemented as written; main reviewed the full diff. Implementer evidence: `make test-fast
TESTS="tests/test_api_runs.py tests/test_api_projects.py"` exit 0 (94 tests); ruff check/format
and mypy on the four backend files exit 0 (docstring wrapped for E501). Main reran from
`frontend/`: `npm test` 0 (44 pass), `npm run typecheck` 0, `npm run build` 0, and mypy on
`gates.py` 0. Known backend behaviour 38b must absorb: `gate.answered` is emitted twice per
answer (`gates.py` `answer` and `_prepare`), and a rewind emits both `gate.closed` with
`new_run_id` and `run.forked`.

## 38b — run workspace components

Goal: replace the placeholder run page with a working workspace: header with live facts, cost
and lifecycle controls (pause, resume, stop, rewind); pending decision panels with all gate
actions; a step table (shared steps, candidate columns, final steps) with status marks; a step
inspector (outcome, decision, metrics, attempts, tool calls, agent calls and cost); and a
conversation panel with streamed replies and step activity. Non-goals as in the chunk header.
No component tests (repo tests only `.ts`); pure helpers below get unit tests; main does the
browser acceptance.

Owned files: `frontend/src/run/model.ts`, `frontend/tests/run-model.test.ts`,
`frontend/src/pages/RunPage.tsx` (rewrite), new `frontend/src/run/RunHeader.tsx`,
`frontend/src/run/RunGraph.tsx`, `frontend/src/run/NodeInspector.tsx`,
`frontend/src/run/GatePanel.tsx`, `frontend/src/run/ChatPanel.tsx`,
`frontend/src/run/DecisionView.tsx`, `frontend/src/App.tsx` (one prop), `frontend/src/style.css`
(append only). Reuse: `SchemaForm` (`src/setup/SchemaForm.tsx`, unchanged), `formatTime` and
`errorText` (`src/ui/format.ts`), `formatRoute` and `Route` (`src/routes.ts`), client functions
from 38a, `useRunEvents`.

### `src/run/model.ts` additions

- `foldEvents` dedupe: keep a `Set` of `${gate_id}:${answer.revision}` for `gate.answered`
  (requires string `gate_id` and integer `answer.revision`; a repeat pushes nothing) and a `Set`
  of fork run ids (a `gate.closed`/`run.forked` whose `new_run_id` was already pushed pushes
  nothing).
- `STATUS_LABELS: Record<RunStatus, string>`: pending "Pending", running "Running",
  awaiting_user "Waiting for you", paused "Paused", failed "Failed", cancelled "Stopped",
  completed "Completed", superseded "Replaced by a rewind".
- `statusTone(status: RunStatus): 'signal' | 'gate' | 'fault' | 'neutral'`: running/completed ->
  signal; awaiting_user/paused -> gate; failed/cancelled -> fault; else neutral.
- `CELL_LABELS: Record<CellStatus, string>`: pending "Not started", running "Running",
  awaiting "Waiting for you", completed "Done", skipped "Skipped", failed "Failed".
- `PHASE_LABELS: Record<GatePhase, string>`: before "Before it runs", after "After it ran",
  failure "It failed".
- `formatUsd(value: number): string` -> `` `$${value.toFixed(4)}` ``.
- `runControls(status: RunStatus): { pause: boolean; resume: boolean; cancel: boolean; rewind: boolean }`:
  pause for pending/running; resume for paused; cancel for pending/running/paused/awaiting_user/
  failed; rewind for every status except pending and superseded.
- `latestOutcome(view: RunView, node: string, candidateId: string | null): NodeOutcome | null`:
  null candidate -> last entry of `view.shared` with that node; else the branch with that id ->
  last entry of its `outcomes` with that node; missing -> null.
- `mergedMetrics(branch: Branch): Record<string, number>`: assign every outcome's metrics in order.
- `EMPTY_VOCABULARY: Vocabulary = { contractSchema: {}, estimators: [], inferentialModels: [] }`
  (decision forms never hit SchemaForm's contract-only keys).
- Tests (`run-model.test.ts`): dedupe (two identical `gate.answered` -> one item; `gate.closed`
  then `run.forked` with the same run id -> one fork item); every `runControls` status row;
  `statusTone` for one status per tone; `formatUsd(0.12345)` -> `'$0.1235'`; `latestOutcome`
  shared, branch, missing; `mergedMetrics` with two outcomes.

### `src/run/DecisionView.tsx`

`export default function DecisionView({ value }: { value: JsonObject })`: `<dl className="facts">`
one row per key: `<dt>{nodeLabel(key)}</dt>`; `<dd>`: string -> text in
`<span className="reasoning">`; number/boolean -> `String(v)`; null -> "—"; array of only strings
-> joined with ", " (empty array -> "None"); anything else -> `<pre className="json-view">` of
`JSON.stringify(v, null, 2)`. Empty object -> `<p className="secondary-line">Nothing recorded.</p>`.

### `src/run/RunHeader.tsx`

Props: `run: RunSummary`, `view: RunView | null`, `costUsd: number | null`, `streamStatus:
StreamStatus`, `navigate: (route: Route) => void`.
- `<div className="page-head">`: left `<h1>Run</h1><p className="secondary-line"><code>{run.id}</code></p>`;
  when `run.forked_from_run_id` -> `<p>Rewound from <a href={formatRoute({name:'run', runId: parent})}>run {parent}</a></p>`.
  Right: controls (below).
- `<dl className="facts">`: Mode ("Guided"/"Autonomous"); Status (`<span className={`pill ${tone}`}>`
  with `STATUS_LABELS`, tone class omitted for neutral); Current step (`nodeLabel` or "—");
  Started / Ended (`formatTime`); Agent cost (`costUsd === null` -> "Loading…", else `formatUsd`);
  Fit time (only with `view?.budget`: `` `${(usedMs / 1000).toFixed(1)} s of ${(limitMs / 1000).toFixed(0)} s` ``);
  Live updates (`connecting` "Connecting", `connected` "Live", `reconnecting` "Reconnecting",
  `closed` "Closed", `error` "Stopped. Reload the page to reconnect.").
- Controls from `runControls(run.status)`; one `useMutation` taking
  `{ kind: 'control'; action: 'pause' | 'resume' | 'cancel' } | { kind: 'rewind'; target: string }`:
  control -> `controlRun`; rewind -> `rewindRun(run.id, target, crypto.randomUUID())`.
  Buttons in `.actions`: "Pause" (pause), "Resume" (resume, class primary), "Stop the run"
  (cancel, class danger, only after `window.confirm('Stop this run? It cannot be resumed.')`).
  Rewind (when allowed and `view !== null`): `<details><summary>Rewind to an earlier step</summary>`
  with labelled select "Start again from" (options = `view.spine` names, labels `nodeLabel`,
  default first) and button "Rewind" (after `window.confirm('Start a new run from this step? This run will be replaced.')`).
  All controls disabled while pending. Success: control -> `<p className="ok">{reply.text}</p>`;
  rewind -> `navigate({ name: 'run', runId: newRunId })`; both invalidate `['run', run.id]` and
  `['executions', run.id]`. Error -> `<p role="alert">{errorText(error)}</p>`.

### `src/run/RunGraph.tsx`

Props: `view: RunView`, `executions: ExecutionRecord[]`, `pending: GateQuestion[]`,
`selected: { node: string; candidateId: string | null } | null`, `onSelect(next)`.
`layout = graphLayout(view.spine)`. A local `Mark({ status })` renders
`<span className={`mark mark-${status}`}>{CELL_LABELS[status]}</span>`. Step buttons are
`<button type="button" className="cell-link" aria-pressed={isSelected} onClick=...>`.
- Shared table (only when `layout.before` non-empty): `.table-wrap` table, caption "Shared steps",
  columns Step | Status | Note. Step = button with `nodeLabel(name)`; when `name === view.overlap`
  add `<small className="help">Runs alongside the candidates.</small>`; Status = Mark of
  `cellStatus(name, null, executions, pending)`; Note = `latestOutcome` skip reason, else its
  error message, else empty.
- Candidate table (only when `layout.perCandidate` non-empty): caption "Candidate models". No
  branches -> instead of the table `<p className="secondary-line">Candidate models appear after the experiment design step.</p>`.
  Header: `<th>Step</th>` then per branch `<th scope="col">{branch.label}<small className="help">{branch.id}{branch.estimator ? `, ${branch.estimator}` : ''}{branch.retuneCount > 0 ? `, retuned ${branch.retuneCount} times` : ''}</small></th>`.
  One row per `perCandidate` node: `<th scope="row">{nodeLabel}</th>`, per branch a cell with a
  `cell-link` button containing the Mark of `cellStatus(node, branch.id, ...)`, `aria-label`
  `` `${nodeLabel(node)} for ${branch.label}` ``. Last row `<th scope="row">Validation metrics</th>`:
  per branch `mergedMetrics` entries as lines `` `${key} ${value.toFixed(4)}` `` (none -> "—").
- Final table (only when `layout.after` non-empty): caption "Final steps", same columns as shared.

### `src/run/NodeInspector.tsx`

Props: `view: RunView`, `data: RunExecutions | undefined`, `selected` (as above).
- `selected === null` -> `<section className="panel"><p className="secondary-line">Select a step to see what the agent decided, which tools it ran and what it cost.</p></section>`.
- Else `<section className="panel" aria-labelledby="inspector-heading">`, `<h2 id>`:
  `nodeLabel(node)` plus `` ` for ${branch label}` `` when a candidate is selected (label from
  `view.branches`, fallback the id).
- Outcome (`latestOutcome`): `<h3>Outcome</h3>` facts: Status (raw status text), Skip reason,
  Error (`errorClass: message`), Warnings (each on its own line), Artifacts (count only:
  `` `${n} files` ``; browsing is chunk 39). Metrics table (caption "Metrics"; Metric | Value
  with `toFixed(4)`) when non-empty. Decision: when `outcome.summary.decision` is an object ->
  `<h3>Decision</h3><DecisionView value={...} />`. No outcome -> "No result recorded yet.".
- Attempts: `data === undefined` -> "Loading attempts…". `executionsFor(...)` empty -> "This step
  has not run yet.". Else `<h3>Attempts</h3>` then per entry `<details open={index === 0}>` with
  summary `` `Attempt ${attempt}: ${status}, ${formatTime(startedAt)}` ``; body: duration line
  when `endedAt` (`((Date.parse(endedAt) - Date.parse(startedAt)) / 1000).toFixed(1)` s); error
  `<p role="alert">` when `execution.error`; tool calls table (caption "Tool calls"; Tool |
  Time (ms) | Details where Details = `<details><summary>Arguments and result</summary>` two
  `pre.json-view` blocks of `JSON.stringify(args/result, null, 2)`), only when non-empty; agent
  calls table (caption "Agent calls"; Model | Prompt tokens | Completion tokens | Cached tokens |
  Cost | Time (ms), cost via `formatUsd`) only when non-empty, followed by
  `<p>Agent cost for this attempt: {formatUsd(sum)}</p>`.

### `src/run/GatePanel.tsx`

Props: `runId`, `gate: GateSummary`, `question: GateQuestion`, `skippable: boolean`,
`spine: SpineNode[]`, `navigate`. Parent keys it by `` `${gate.id}:${question.revision}` ``.
State: `draft` (init `question.proposal`), `instruction`, `reason`, `target` (init first spine
name or ''), `issues: JsonObject[]`, `reply: string | null`.
`actions = availableActions(question, skippable)`; `edits = gateEdits(question.proposal, draft, question.editable)`;
`hasEdits = Object.keys(edits).length > 0`; `editable = actions.includes('approve_with_edits')`.
One `useMutation<ControlReply, Error, GateSubmission>` calling `answerGate(runId, gate.id, s)`;
success: `setReply(text)`, invalidate `['run', runId]`/`['executions', runId]`, `newRunId` ->
navigate to that run. Error: alert `errorText(error)` plus, for `ApiError` status 409, the line
"This decision changed or was already answered. The page now shows its latest state." and an
invalidate of `['run', runId]`. Every submission sends `revision: question.revision`.
Layout `<article className="panel gate" aria-labelledby={`gate-${gate.id}`}>`:
1. `<h3 id>`: `` `Decision at ${nodeLabel(question.node)}` `` + `` ` for ${candidateId}` `` when
   present, then `<span className="pill gate">{PHASE_LABELS[phase]}</span>`.
2. `<p className="reasoning">{question.reasoning}</p>`; failure -> `<p role="alert">{failure.message}</p>`.
3. `<details>` blocks (only when non-empty): "Context" (`<ul>` of strings), "Evidence the agent
   used (N)" (`pre.json-view` of evidence), "Earlier proposals (N)" (`pre.json-view` of history).
4. Proposal fieldset: legend "Proposal (you can edit it)" when `editable`, else "Proposal".
   Editable -> `<SchemaForm schema={question.decisionSchema} defName={null} path={[]} draft={draft} onDraft={setDraft} columns={[]} vocabulary={EMPTY_VOCABULARY} />`;
   else `<DecisionView value={question.proposal} />` (phase failure with empty proposal ->
   DecisionView prints "Nothing recorded.").
5. Issues list when non-empty: `<div role="alert"><p>Fix these before approving:</p><ul>` items
   `<strong>{field}</strong> {message}`.
6. Primary `.actions` row:
   - `approve` (when in actions and `!hasEdits`): primary button "Approve" -> `{action: 'approve'}`.
   - `approve_with_edits` (when `hasEdits`): primary button "Approve with my edits" ->
     `checkGateEdits(runId, gate.id, edits)` first (inside the same mutation function: when the
     returned list is non-empty, `setIssues(list)` and return a reply-less sentinel by throwing
     nothing: resolve `{ text: '', category: 'noop', newRunId: null }` and skip invalidation and
     reply when `category === 'noop'`); empty -> `setIssues([])` and answer
     `{action: 'approve_with_edits', edits}`. Plus button "Discard my edits" -> `setDraft(question.proposal)`, `setIssues([])`.
   - `approve_all_remaining`: button "Approve and run the rest automatically", disabled with
     `<p className="help">Approve or discard your edits first.</p>` when `hasEdits`.
7. `<details>` "Ask for a revision" (when `instruct` in actions): textarea labelled "Tell the
   agent what to change" + button "Ask for a revision" (disabled when trimmed text empty) ->
   `{action: 'instruct', instruction: trimmed}`.
8. `<details>` "Skip this step" (when `skip_node`): textarea "Why skip this step?" + button
   "Skip this step" (disabled when blank) -> `{action: 'skip_node', reason: trimmed}`.
9. `<details>` "Rewind to an earlier step" (when `rewind`): select "Start again from" over
   `spine` + button "Rewind" -> confirm (same text as header) -> `{action: 'rewind', target}`.
10. `abort`: button class danger "Stop the run" -> confirm('Stop this run? It cannot be resumed.') -> `{action: 'abort'}`.
All buttons disabled while the mutation is pending. `reply` (non-empty) -> `<p className="ok">`.

### `src/run/ChatPanel.tsx`

Props: `runId`, `feed: RunFeed`, `navigate`. State: `text`, `showActivity` (default true).
`<aside className="chat panel" aria-labelledby="chat-heading">` with `<h2 id>Conversation</h2>`,
checkbox label "Show step activity". `<ol className="feed" aria-live="polite" ref={listRef}>`:
- user -> `<li className="msg user"><span className="who">You</span><p className="reasoning">{text}</p></li>`.
- assistant -> `.msg agent`, who "Agent", text, and when `newRunId` a link "Open the new run"
  (`formatRoute`).
- error -> `<li className="msg error" role="alert">{text}</li>`.
- activity (only when `showActivity`) -> `<li className="activity"><time>{new Date(ts).toLocaleTimeString()}</time> {text}</li>`.
- fork -> `<li className="activity">This run continues as <a href=...>run {newRunId}</a></li>` (always shown).
- then each `feed.drafts` entry -> `.msg agent draft`, who "Agent is writing…", text.
- Empty (no visible items, no drafts) -> `<li className="activity">No messages yet. Ask the agent about the run, or tell it what to change.</li>`.
`useEffect` on `[feed.items.length, Object.values(feed.drafts).join('').length, showActivity]`:
scroll `listRef.current` to its `scrollHeight`.
Composer `<form>`: textarea id `chat-input` label "Message the agent", placeholder "Ask a
question, change the plan, or say pause", rows 3; Cmd/Ctrl+Enter submits; button "Send"
(primary, disabled when trimmed empty or pending). Mutation `sendMessage(runId, trimmed)`;
success -> clear text, invalidate run/executions, `newRunId` -> navigate; error -> alert.

### `src/pages/RunPage.tsx` (rewrite)

Props `{ runId: string; navigate: (route: Route) => void }`; `App.tsx` passes `navigate`.
- Queries: `['run', runId]` `getRun`; `['executions', runId]` `getExecutions`.
- `subscribedId` as today; `stream = useRunEvents(subscribedId)`.
- Refresh: `useEffect` on `[stream.lastSeq, runId]`: when `lastEvent` type starts with `node.`,
  `gate.` or `run.`: `setTimeout(300)` invalidating `['run', runId]` and `['executions', runId]`;
  cleanup clears the timer (a burst refetches once).
- `feed = useMemo(() => foldEvents(stream.events), [stream.events])`.
- `selected` state, default null; reset to null when `runId` changes (key the page content on runId
  in App instead: render `<RunPage key={route.runId} …/>`).
- Loading/error as today. With data: `view = parseRunView(snapshot.state)`; `pending` = gates
  with status `pending`, each `{ gate, question: parseGateQuestion(gate.question_json) }`.
- Render: `<RunHeader …/>`; when `pending` non-empty: `<section aria-labelledby="gates-heading"><h2 id="gates-heading">Decisions waiting for you</h2>`
  each parsed -> `<GatePanel key=… skippable={view?.spine.find(n => n.name === question.node)?.skippable ?? false} spine={view?.spine ?? []} …/>`;
  unparsed -> `<div className="panel gate"><p role="alert">This decision cannot be displayed.</p><pre className="json-view">{gate.question_json}</pre></div>`.
  `snapshot.execution_failed` -> `<p role="alert">The run's executor stopped with an error. Check the attempts below.</p>`.
  Then `<div className="workspace"><div>` RunGraph + NodeInspector (or, when `view === null`,
  `<p role="alert">This run's state cannot be displayed.</p>`) `</div><ChatPanel …/></div>`.

### `src/style.css` (append)

- `.workspace { display: grid; grid-template-columns: minmax(0, 1fr) 22rem; gap: 24px; align-items: start; }`
  and inside `@media (max-width: 64rem)` -> one column.
- `.chat { position: sticky; top: 16px; margin-top: 0; }`; `.feed { list-style: none; margin: 0 0 12px; padding: 0; max-height: 60vh; overflow-y: auto; }`;
  `.msg { margin: 8px 0; padding: 8px 12px; border-radius: 8px; }`; `.msg.user { background: var(--target); }`;
  `.msg.agent { border: 1px solid var(--rule); }`; `.msg.draft { border-style: dashed; }`;
  `.msg .who { display: block; font-size: 0.875rem; font-weight: 600; color: var(--graphite); }`;
  `.msg.error { color: var(--fault); }`; `.activity { margin: 4px 0; color: var(--graphite); font-size: 0.875rem; }`.
- `.gate { border-left: 4px solid var(--gate); }`; `button.danger { color: var(--fault); border-color: var(--fault); }`;
  `.json-view { max-height: 20rem; overflow: auto; padding: 8px; border: 1px solid var(--rule); border-radius: var(--radius); background: var(--paper); }`.
- `.cell-link { padding: 0; border: 0; background: none; color: var(--ink); text-align: left; }`;
  `.cell-link[aria-pressed="true"] { outline: 2px solid var(--signal); outline-offset: 4px; }`.
- Marks: `.mark { white-space: nowrap; }`; `.mark::before { content: ""; display: inline-block; width: 0.75em; height: 0.75em; margin-right: 6px; border: 2px solid currentColor; border-radius: 2px; vertical-align: -0.05em; }`;
  pending color graphite; running color signal; awaiting color gate with filled square
  (`background: currentColor` on `::before`); completed color signal filled; skipped color
  graphite with `border-style: dashed`; failed color fault filled.
  `@media (prefers-reduced-motion: no-preference) { .mark-running::before { animation: mark-pulse 1.2s ease-in-out infinite; } }`
  with `@keyframes mark-pulse { 50% { opacity: 0.3; } }`.

### Checks

From `frontend/`: `npm test`, `npm run typecheck`, `npm run build` (genuine exits, test count).
Stop and report on failures or gaps. Main then performs the browser acceptance on the H9 stack:
guided run with approve, approve with edits (including an invalid edit showing an issue), ask
for a revision, skip, stop; pause/resume; rewind from the header and from a gate; chat message
with reply; step inspector on shared and candidate cells; autonomous run to completion.

### 38b Addendum A — browser acceptance corrections (2026-10-08)

Click-through on the H9 stack passed: guided gates (approve, approve with edits persisted into
the decision, revision, skip with reason, gate rewind to a new run with "Rewound from" link,
approve-all), header pause/resume/stop/rewind, chat status reply, inspector for shared and
candidate cells, autonomous completion with candidate table and metrics. Defects:

1. **Retries share `seq`.** A retried or resumed attempt keeps the execution `seq` and bumps
   `attempt` (observed: `intake` seq 1 attempt 1 `awaiting_user`, attempt 2 `completed`;
   paused `preprocessing` seq 7 attempt 1 `failed`, attempt 2 `completed`). `cellStatus` picks
   the first of equal seqs, so finished steps show "Waiting for you"/"Failed", and the inspector
   lists attempt 1 first.
2. **Forked runs show copied steps as "Not started".** A rewind child has no executions for steps
   it inherited, but `state.shared_results` holds their outcomes.
3. `small.help` renders inline (step name and note run together), and the candidate header
   repeats the estimator when it equals the label.

Recipe (owned: `frontend/src/run/model.ts`, `frontend/tests/run-model.test.ts`,
`frontend/src/run/RunGraph.tsx`, `frontend/src/style.css`):
- `model.ts`: `cellStatus(node, candidateId, executions, pending, outcome: NodeOutcome | null)`.
  Latest execution = highest `seq`, ties broken by highest `attempt`. When no execution matches:
  `outcome === null` -> 'pending'; `outcome.status` 'completed'|'skipped'|'failed' -> same;
  'awaiting_user' -> 'awaiting'; else 'pending'. Pending-question rule stays first.
  `executionsFor` sorts by `seq` descending, then `attempt` descending.
- `RunGraph.tsx`: pass `latestOutcome(view, node, candidateId)` as the fifth argument at every
  `cellStatus` call; candidate header shows `, ${branch.estimator}` only when
  `branch.estimator !== null && branch.estimator !== branch.label`.
- `style.css` (append): `small.help { display: block; }`.
- Tests: update existing `cellStatus` calls with a fifth argument `null`; add (a) seq 7 attempt 1
  `failed` + seq 7 attempt 2 `completed` -> 'completed' (and reversed array order gives the same);
  (b) no executions with outcome status 'skipped' -> 'skipped', 'awaiting_user' -> 'awaiting',
  null -> 'pending'; (c) `executionsFor` with equal seq returns attempt 2 before attempt 1.
- Checks from `frontend/`: `npm test`, `npm run typecheck`, `npm run build`.
