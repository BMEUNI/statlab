import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  availableActions,
  cellStatus,
  estimatorLabel,
  executionsFor,
  firstSentences,
  foldEvents,
  forkNote,
  datasetColumns,
  decisionLead,
  formatUsd,
  gateEdits,
  gateNotes,
  gateFieldSplit,
  graphLayout,
  groupDecisions,
  groupTone,
  humanize,
  jsonBlock,
  latestOutcome,
  metricLabel,
  modeLabel,
  narrate,
  nodeLabel,
  nowLine,
  parseGateQuestion,
  parseRunView,
  runControls,
  splitLead,
  STEP_PURPOSES,
  statusTone,
  stepShortLabel,
  switchedToAutomatic,
} from '../src/run/model.ts';
import type { ExecutionRecord, GateQuestion, GateSummary, JsonObject, RunEvent, RunExecutions, RunSummary } from '../src/api/types.ts';
import type { RunView } from '../src/run/model.ts';

const question = {
  node: 'intake', candidate_id: '5:r_abc:c_02', phase: 'before', proposal: { rationale: 'x' },
  decision_schema: {}, evidence: [], reasoning: 'why', editable: ['rationale'], default: 'approve',
  options: ['approve', 'approve_with_edits', 'instruct', 'skip_node', 'abort'], context: [], revision: 0,
  history: [], failure: null,
};

function gate(overrides: Partial<GateQuestion> = {}): GateQuestion {
  const parsed = parseGateQuestion(JSON.stringify(question));
  assert.ok(parsed);
  return { ...parsed, ...overrides };
}

test('nodeLabel humanises node names', () => {
  assert.equal(nodeLabel('model_training'), 'Model training');
});

test('parseGateQuestion validates and maps the public candidate id', () => {
  const parsed = parseGateQuestion(JSON.stringify(question));
  assert.equal(parsed?.candidateId, 'c_02');
  assert.equal(parsed?.defaultAction, 'approve');
  assert.equal(parsed?.failure, null);
  assert.equal(parseGateQuestion('{bad'), null);
  assert.equal(parseGateQuestion(JSON.stringify({ ...question, options: ['approve', 'nope'] })), null);
  assert.equal(parseGateQuestion(JSON.stringify({ ...question, phase: 'during' })), null);
});

const result = (status: string): JsonObject => ({
  status, summary: { k: 1 }, artifacts: ['a', 1], metrics: { m: 2, bad: 'x' }, warnings: ['w'], skip_reason: null, error: null,
});

test('parseRunView and graphLayout', () => {
  const spine = [
    { name: 'a', kind: 'SHARED', skippable: false },
    { name: 'b', kind: 'PER_CANDIDATE', skippable: true },
    { name: 'c', kind: 'PER_CANDIDATE', skippable: false },
    { name: 'd', kind: 'SHARED', skippable: false },
  ];
  const state: JsonObject = {
    spine,
    shared_results: [{ node_name: 'a', execution_id: 'e1', result: result('completed') }],
    branches: [{
      public_id: 'c_01', spec: { label: 'Lasso', estimator: { name: 'lasso' } }, results: [], wave: 2,
      retune_count: 1, status: 'running',
    }],
    traversal: { overlap_shared: 'a' },
    budget: { total_fit_ms: 5, limit_ms: 10 },
  };
  const outcome = {
    node: 'a', executionId: 'e1', status: 'completed', summary: { k: 1 }, artifacts: ['a'], metrics: { m: 2 },
    warnings: ['w'], skipReason: null, error: null,
  };
  assert.deepEqual(parseRunView(state), {
    spine,
    shared: [outcome],
    branches: [{
      id: 'c_01', label: 'Lasso', estimator: 'lasso', status: 'running', wave: 2, retuneCount: 1, outcomes: [],
      spec: { label: 'Lasso', estimator: { name: 'lasso' } },
    }],
    overlap: 'a',
    wave: 1,
    budget: { usedMs: 5, limitMs: 10 },
  });
  assert.equal(parseRunView({}), null);
  const view = parseRunView(state);
  assert.ok(view);
  const layout = graphLayout(view.spine);
  assert.deepEqual(layout.before.map((n) => n.name), ['a']);
  assert.deepEqual(layout.perCandidate.map((n) => n.name), ['b', 'c']);
  assert.deepEqual(layout.after.map((n) => n.name), ['d']);
  const flat = graphLayout([{ name: 'x', kind: 'SHARED', skippable: false }]);
  assert.deepEqual(flat.before.map((n) => n.name), ['x']);
  assert.deepEqual([flat.perCandidate, flat.after], [[], []]);
});

function execution(id: string, seq: number, status: string, candidateId: string | null = null, attempt = 1): ExecutionRecord {
  return {
    id, candidateId, node: 'intake', seq, attempt, status, summary: null, error: null, startedAt: 's', endedAt: null,
  };
}

test('cellStatus', () => {
  assert.equal(cellStatus('intake', 'c_02', [], [gate()], null), 'awaiting');
  assert.equal(cellStatus('intake', null, [execution('e1', 1, 'failed'), execution('e2', 2, 'completed')], [], null), 'completed');
  assert.equal(cellStatus('intake', null, [execution('e1', 1, 'running')], [], null), 'running');
  assert.equal(cellStatus('intake', null, [], [], null), 'pending');
});

test('executionsFor orders newest first and pairs calls', () => {
  const data: RunExecutions = {
    executions: [execution('e1', 1, 'failed'), execution('e2', 2, 'completed'), execution('e3', 3, 'completed', 'c_01')],
    toolCalls: [{ id: 't1', executionId: 'e2', tool: 't', args: null, result: null, durationMs: 1, ts: 'n' }],
    llmCalls: [{
      id: 'l1', executionId: 'e1', signature: 's', model: 'm', promptTokens: 0, completionTokens: 0, cachedTokens: 0,
      costUsd: 0, durationMs: 1, ts: 'n',
    }],
    costUsd: 0,
  };
  const rows = executionsFor('intake', null, data);
  assert.deepEqual(rows.map((row) => row.execution.id), ['e2', 'e1']);
  assert.deepEqual(rows[0]?.toolCalls.map((c) => c.id), ['t1']);
  assert.deepEqual(rows[0]?.llmCalls, []);
  assert.deepEqual(rows[1]?.llmCalls.map((c) => c.id), ['l1']);
});

test('gateEdits sends only changed editable keys present in the draft', () => {
  const proposal = { a: 1, b: 2, c: 3, d: 4 };
  assert.deepEqual(gateEdits(proposal, { a: 9, b: 2, d: 5 }, ['a', 'b', 'c']), { a: 9 });
});

test('availableActions', () => {
  assert.deepEqual(availableActions(gate(), false), ['approve', 'approve_with_edits', 'instruct', 'abort']);
  assert.deepEqual(availableActions(gate(), true), ['approve', 'approve_with_edits', 'instruct', 'skip_node', 'abort']);
  assert.deepEqual(availableActions(gate({ phase: 'after' }), true), ['approve', 'skip_node', 'abort']);
  assert.deepEqual(
    availableActions(gate({ phase: 'failure', options: ['abort', 'rewind'], editable: [] }), false),
    ['rewind', 'abort'],
  );
});

let nextSeq = 0;
function event(type: string, payload: JsonObject): RunEvent {
  nextSeq += 1;
  return { seq: nextSeq, run_id: 'r1', ts: `t${nextSeq}`, type, payload };
}

test('foldEvents builds the feed', () => {
  nextSeq = 0;
  const feed = foldEvents([
    event('message.user', { message_id: 'm1', session_id: 's', text: 'Hi' }),
    event('message.assistant.delta', { reply_to: 'm1', session_id: 's', text: 'Hel', reset: false }),
    event('message.assistant.delta', { reply_to: 'm1', session_id: 's', text: 'Hello', reset: true }),
    event('message.assistant.complete', {
      message_id: 'a1', reply_to: 'm1', session_id: 's', text: 'Hello there',
      response: { category: 'info', new_run_id: 'r2' },
    }),
    event('message.assistant.delta', { reply_to: 'm2', session_id: 's', text: 'Part', reset: false }),
    event('error', { reply_to: 'm2', message: 'boom', discard_draft: true }),
    event('node.started', { node: 'model_training', candidate_id: 'c_01', execution_id: 'e' }),
    event('gate.opened', { gate_id: 'g', node_execution_id: 'e', question: { node: 'intake', candidate_id: '5:r:c_02' } }),
    event('gate.answered', { gate_id: 'g', answer: { action: 'skip_node', revision: 0 } }),
    event('gate.closed', { gate_id: 'g', revision: 0, new_run_id: 'r3' }),
    event('run.status', { status: 'awaiting_user', current_node: null }),
    event('mystery', {}),
    event('node.started', { node: 1 }),
  ]);
  assert.deepEqual(feed.drafts, {});
  assert.deepEqual(feed.items, [
    { seq: 1, ts: 't1', kind: 'user', messageId: 'm1', text: 'Hi' },
    { seq: 4, ts: 't4', kind: 'assistant', replyTo: 'm1', text: 'Hello there', category: 'info', newRunId: 'r2' },
    { seq: 6, ts: 't6', kind: 'error', replyTo: 'm2', text: 'boom' },
    { seq: 7, ts: 't7', kind: 'activity', text: 'Model training (c_01) started' },
    { seq: 8, ts: 't8', kind: 'activity', text: 'Waiting for your decision at Intake (c_02)' },
    { seq: 9, ts: 't9', kind: 'activity', text: 'You chose: Skip this step' },
    { seq: 10, ts: 't10', kind: 'fork', newRunId: 'r3' },
    { seq: 11, ts: 't11', kind: 'activity', text: 'Run awaiting user' },
  ]);
  const drafting = foldEvents([
    event('message.assistant.delta', { reply_to: 'm1', session_id: 's', text: 'Hel', reset: false }),
    event('message.assistant.delta', { reply_to: 'm1', session_id: 's', text: 'lo', reset: false }),
  ]);
  assert.deepEqual(drafting.drafts, { m1: 'Hello' });
});

test('foldEvents records the completion seq', () => {
  nextSeq = 0;
  const folded = foldEvents([
    event('run.status', { status: 'running' }),
    event('run.status', { status: 'completed' }),
    event('message.user', { message_id: 'm9', session_id: 's', text: 'Why?' }),
  ]);
  assert.equal(folded.completedSeq, 2);
  assert.equal(foldEvents([]).completedSeq, null);
});

test('foldEvents dedupes repeated answers and forks', () => {
  nextSeq = 0;
  const answer = { gate_id: 'g', answer: { action: 'approve', revision: 1 } };
  const feed = foldEvents([
    event('gate.answered', answer),
    event('gate.answered', answer),
    event('gate.closed', { gate_id: 'g', revision: 1, new_run_id: 'r9' }),
    event('run.forked', { new_run_id: 'r9' }),
  ]);
  assert.deepEqual(feed.items, [
    { seq: 1, ts: 't1', kind: 'activity', text: 'You chose: Approve' },
    { seq: 3, ts: 't3', kind: 'fork', newRunId: 'r9' },
  ]);
});

test('runControls rows', () => {
  const row = (pause: boolean, resume: boolean, cancel: boolean, rewind: boolean) => ({ pause, resume, cancel, rewind });
  assert.deepEqual(runControls('pending'), row(true, false, true, false));
  assert.deepEqual(runControls('running'), row(true, false, true, true));
  assert.deepEqual(runControls('awaiting_user'), row(false, false, true, true));
  assert.deepEqual(runControls('paused'), row(false, true, true, true));
  assert.deepEqual(runControls('failed'), row(false, false, true, true));
  assert.deepEqual(runControls('cancelled'), row(false, false, false, true));
  assert.deepEqual(runControls('completed'), row(false, false, false, true));
  assert.deepEqual(runControls('superseded'), row(false, false, false, false));
});

test('statusTone and formatUsd', () => {
  assert.equal(statusTone('running'), 'signal');
  assert.equal(statusTone('paused'), 'gate');
  assert.equal(statusTone('failed'), 'fault');
  assert.equal(statusTone('pending'), 'neutral');
  assert.equal(formatUsd(0.12345), '$0.1235');
});

test('datasetColumns', () => {
  const base = { spine: [], branches: [], overlap: null, wave: 1, budget: null };
  const intake = (summary: JsonObject) => ({
    node: 'intake', executionId: 'e1', status: 'completed', summary, artifacts: [], metrics: {}, warnings: [],
    skipReason: null, error: null,
  });
  const view: RunView = {
    ...base,
    shared: [intake({ tools: [
      { tool: 'other' },
      { tool: 'load_csv', result: { summary: { columns: ['a', 3, 'b'] } } },
    ] })],
  };
  assert.deepEqual(datasetColumns(view), ['a', 'b']);
  assert.deepEqual(datasetColumns(null), []);
  assert.deepEqual(datasetColumns({ ...base, shared: [intake({})] }), []);
});

test('latestOutcome', () => {
  const outcome = (node: string, executionId: string, metrics: Record<string, number>) => ({
    node, executionId, status: 'ok', summary: {}, artifacts: [], metrics, warnings: [], skipReason: null, error: null,
  });
  const branch = {
    id: 'c_01', label: 'A', estimator: null, status: 'pending', wave: 1, retuneCount: 0, spec: {},
    outcomes: [outcome('fit', 'e1', { a: 1 }), outcome('fit', 'e2', { a: 2, b: 3 })],
  };
  const view = {
    spine: [], shared: [outcome('intake', 's1', {}), outcome('intake', 's2', {})], branches: [branch],
    overlap: null, wave: 1, budget: null,
  };
  assert.equal(latestOutcome(view, 'intake', null)?.executionId, 's2');
  assert.equal(latestOutcome(view, 'fit', 'c_01')?.executionId, 'e2');
  assert.equal(latestOutcome(view, 'fit', 'c_09'), null);
  assert.equal(latestOutcome(view, 'nope', null), null);
});

test('cellStatus ties and inherited outcomes', () => {
  const a = [execution('e1', 7, 'failed'), execution('e2', 7, 'completed', null, 2)];
  assert.equal(cellStatus('intake', null, a, [], null), 'completed');
  assert.equal(cellStatus('intake', null, [...a].reverse(), [], null), 'completed');
  const out = (status: string) => ({
    node: 'intake', executionId: 'x', status, summary: {}, artifacts: [], metrics: {}, warnings: [], skipReason: null, error: null,
  });
  assert.equal(cellStatus('intake', null, [], [], out('skipped')), 'skipped');
  assert.equal(cellStatus('intake', null, [], [], out('awaiting_user')), 'awaiting');
  assert.equal(cellStatus('intake', null, [], [], null), 'pending');
  const data = { executions: a, toolCalls: [], llmCalls: [], costUsd: 0 };
  assert.deepEqual(executionsFor('intake', null, data).map((e) => e.execution.attempt), [2, 1]);
});

test('groupTone', () => {
  assert.equal(groupTone([]), 'upcoming');
  assert.equal(groupTone(['completed', 'failed']), 'fault');
  assert.equal(groupTone(['awaiting', 'running']), 'awaiting');
  assert.equal(groupTone(['running', 'pending']), 'current');
  assert.equal(groupTone(['completed', 'skipped']), 'done');
  assert.equal(groupTone(['completed', 'pending']), 'current');
  assert.equal(groupTone(['pending', 'pending']), 'upcoming');
});

test('label helpers humanise ids', () => {
  assert.equal(humanize('hist_gradient_boosting_default_capacity'), 'Hist gradient boosting default capacity');
  assert.equal(humanize('a__b '), 'A b');
  assert.equal(estimatorLabel('hist_gb_classifier'), 'Gradient boosting');
  assert.equal(estimatorLabel(null), 'Model');
  assert.equal(estimatorLabel('xgb'), 'Xgb');
  assert.equal(metricLabel('roc_auc'), 'ROC AUC');
  assert.equal(metricLabel('custom_thing'), 'Custom thing');
});

test('firstSentences and narrate', () => {
  assert.equal(firstSentences('A b. C d! E f? G', 2), 'A b. C d!');
  assert.equal(narrate({ decision: { user_summary: ' Loaded 10 rows. ', rationale: 'x' } }, 'completed', null, null), 'Loaded 10 rows.');
  assert.equal(narrate({ decision: { rationale: 'One. Two. Three.' } }, 'completed', null, null), 'One. Two.');
  assert.equal(narrate({}, 'completed', null, null), 'Done.');
  assert.equal(narrate({}, 'failed', null, 'boom'), 'It failed: boom');
  assert.equal(narrate({}, 'failed', null, null), 'It failed.');
  assert.equal(narrate({}, 'skipped', 'not needed', null), 'Skipped: not needed');
  assert.equal(narrate({}, 'skipped', null, null), 'Skipped.');
});

test('foldEvents posts step updates', () => {
  nextSeq = 0;
  const done = { status: 'completed', summary: { decision: { user_summary: 'Loaded 10 rows.' } } };
  const feed = foldEvents([
    event('node.completed', { node: 'intake', execution_id: 'e1', result: done }),
    event('node.completed', { node: 'preprocessing', candidate_id: '5:r_x:c_02', result: done }),
    event('node.completed', { node: 'model_evaluation', candidate_id: 'r_x:c_02', result: done }),
    event('node.failed', {
      node: 'model_training', candidate_id: 'r_x:c_02', result: { status: 'failed', error: { message: 'boom' } },
    }),
    event('node.started', { node: 'intake', execution_id: 'e2' }),
  ]);
  const steps = feed.items.flatMap((item) => (item.kind === 'step' ? [item] : []));
  assert.equal(feed.items.filter((item) => item.kind === 'activity').length, 5);
  assert.equal(steps.length, 3);
  assert.deepEqual(
    steps.map((item) => [item.node, item.candidateId, item.text]),
    [
      ['intake', null, 'Loaded 10 rows.'],
      ['model_evaluation', 'c_02', 'Loaded 10 rows.'],
      ['model_training', 'c_02', 'It failed: boom'],
    ],
  );
});

test('gateFieldSplit picks required or non-default editable fields', () => {
  const base = {
    node: 'intake', candidateId: null, phase: 'before', evidence: [], reasoning: '', defaultAction: 'approve',
    options: ['approve'], context: [], revision: 0, history: [], failure: null,
    proposal: { rationale: 'r', task: 'classification', metric: 'roc_auc', folds: 10, notes: [], user_summary: 's' },
    decisionSchema: {
      required: ['rationale', 'task'],
      properties: {
        rationale: { type: 'string' },
        task: { enum: ['classification', 'regression'], default: null },
        metric: { type: 'string', default: 'roc_auc' },
        folds: { type: 'integer', default: 5 },
        notes: { type: 'array', items: { type: 'string' }, default: [] },
        user_summary: { type: 'string' },
      },
    },
  } satisfies Omit<GateQuestion, 'editable'> & { proposal: JsonObject };
  const all = ['rationale', 'task', 'metric', 'folds', 'notes', 'user_summary'];
  assert.deepEqual(gateFieldSplit({ ...base, editable: all }), { main: ['task', 'folds'], rest: ['rationale', 'metric', 'notes'] });
  assert.deepEqual(gateFieldSplit({ ...base, editable: [] }), { main: [], rest: [] });
});

const gateWith = (answer: string | null) => ({
  id: 'g', run_id: 'r', node_execution_id: 'e', status: 'answered', question_json: '{}', answer_json: answer, ts: 't',
});

test('switchedToAutomatic reads approve_all_remaining answers', () => {
  assert.equal(switchedToAutomatic([]), false);
  assert.equal(switchedToAutomatic([gateWith(null)]), false);
  assert.equal(switchedToAutomatic([gateWith('{')]), false);
  assert.equal(switchedToAutomatic([gateWith('{"action":"approve"}')]), false);
  assert.equal(switchedToAutomatic([gateWith('{"action":"approve"}'), gateWith('{"action":"approve_all_remaining"}')]), true);
});

test('modeLabel and stepShortLabel', () => {
  assert.equal(modeLabel('guided', false), 'Guided');
  assert.equal(modeLabel('autonomous', false), 'Autonomous');
  assert.equal(modeLabel('autonomous', true), 'Guided, then automatic');
  assert.equal(stepShortLabel('model_training'), 'Train');
  assert.equal(stepShortLabel('custom_step'), 'Custom step');
});

function forkRun(forkedFrom: string | null, config: string): RunSummary {
  return {
    id: 'r_child', project_id: 'p_1', started_at: '2026-01-01T00:00:00Z', mode: 'guided', status: 'running',
    current_node: null, forked_from_run_id: forkedFrom, ended_at: null, contract_id: 'ct_1',
    champion_model_id: null, config_json: config, test_scored_at: null, test_fingerprint: null,
  };
}

test('forkNote explains where a forked run restarts and what changed', () => {
  assert.equal(forkNote(forkRun(null, '{"fork":{"target":"intake"}}')), null);
  assert.equal(forkNote(forkRun('r_parent', '{')), null);
  const plain = 'This run continues from run r_parent and starts again at Data profiling; earlier steps are reused.';
  assert.equal(forkNote(forkRun('r_parent', '{"fork":{"target":"data_profiling"}}')), plain);
  assert.equal(
    forkNote(forkRun('r_parent', '{"fork":{"target":"intake","edits":{"split.test_size":0.3}}}')),
    'This run continues from run r_parent and starts again at Intake; earlier steps are reused. Changed settings: split.test_size = 0.3.',
  );
});

const summary = (id: string, ts: string): GateSummary => ({
  id, run_id: 'r', node_execution_id: 'e', status: 'pending', question_json: '', answer_json: null, ts,
});

test('gateNotes', () => {
  const q = gate({ proposal: { material_exceptions: ['A', 3, 'B'] }, evidence: [{ tool: 'x' }, { warnings: ['B', 'C', 4] }] });
  assert.deepEqual(gateNotes(q), ['A', 'B', 'C']);
});

test('groupDecisions', () => {
  const grouped = (g2Warnings: string[]) => groupDecisions([
    {
      gate: summary('g2', 't2'),
      question: gate({ node: 'preprocessing', phase: 'after', candidateId: 'c_02', evidence: [{ warnings: g2Warnings }] }),
    },
    {
      gate: summary('g1', 't1'),
      question: gate({ node: 'preprocessing', phase: 'after', candidateId: 'c_01', evidence: [{ warnings: ['w'] }] }),
    },
    { gate: summary('g0', 't0'), question: gate({ node: 'intake', phase: 'before', candidateId: null }) },
  ]);
  const groups = grouped(['w']);
  assert.deepEqual(groups.map((group) => group.key), ['gate:g0', 'preprocessing:after']);
  const second = groups[1];
  assert.deepEqual(second?.items.map((item) => item.gate.id), ['g1', 'g2']);
  assert.equal(second?.candidate, true);
  assert.deepEqual(second?.notes, ['w']);
  assert.deepEqual(groups[0]?.notes, []);
  assert.equal(grouped(['v'])[1]?.notes, null);
});

test('decisionLead', () => {
  const lead = (question: GateQuestion, candidate: boolean) => decisionLead({
    key: 'k', node: question.node, phase: question.phase, candidate, items: [{ gate: summary('a', 't'), question }], notes: null,
  });
  const purpose = STEP_PURPOSES.model_evaluation;
  assert.equal(
    lead(gate({ node: 'model_evaluation', candidateId: 'c_01', phase: 'before' }), true),
    `${purpose} I noted points to check before I start.`,
  );
  assert.equal(
    lead(gate({ node: 'model_evaluation', candidateId: 'c_01', phase: 'after' }), true),
    `${purpose} Done; I noted points to check.`,
  );
  assert.equal(lead(gate({ proposal: { user_summary: 'One. Two. Three.' } }), false), 'One. Two.');
  assert.equal(lead(gate({ reasoning: 'Alpha. Beta. Gamma.' }), false), 'Alpha. Beta.');
  assert.equal(
    lead(gate({ phase: 'failure', failure: { errorClass: 'data', message: 'boom' } }), false),
    'This step failed: boom',
  );
});

test('nowLine', () => {
  const view: RunView = {
    spine: [{ name: 'a', kind: 'SHARED', skippable: false }, { name: 'b', kind: 'PER_CANDIDATE', skippable: true }],
    shared: [],
    branches: [{ id: 'c_01', label: 'A', estimator: null, status: 'running', wave: 1, retuneCount: 0, outcomes: [], spec: {} }],
    overlap: null,
    wave: 1,
    budget: null,
  };
  const pending = [
    gate({ node: 'preprocessing', candidateId: 'c_01' }),
    gate({ node: 'preprocessing', candidateId: 'c_02' }),
    gate({ node: 'intake', candidateId: null }),
  ];
  assert.equal(nowLine('running', view, [], pending), 'Waiting for your decision: Preprocessing (2 models), Intake');
  const running = [{ ...execution('x1', 1, 'running', 'c_01'), node: 'b' }, { ...execution('x2', 2, 'running'), node: 'a' }];
  assert.equal(nowLine('running', view, running, []), 'Working on: A, B (1 model)');
  assert.equal(nowLine('running', view, [], []), 'Starting the next step…');
  assert.equal(nowLine('completed', view, [], []), 'Completed');
  assert.equal(nowLine('running', null, [], []), 'Running');
});

test('jsonBlock and splitLead', () => {
  assert.equal(jsonBlock('{"a":1}'), '{\n  "a": 1\n}');
  assert.equal(jsonBlock(' [1,2] '), '[\n  1,\n  2\n]');
  assert.equal(jsonBlock('hello'), null);
  assert.equal(jsonBlock('{bad'), null);
  assert.equal(jsonBlock('"str"'), null);
  assert.deepEqual(splitLead('First one. Second one.'), { lead: 'First one.', more: 'Second one.' });
  assert.deepEqual(splitLead('Only.'), { lead: 'Only.', more: null });
  assert.deepEqual(splitLead('A. B. C. D.', 3), { lead: 'A. B. C.', more: 'D.' });
});
