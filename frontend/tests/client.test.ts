import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ApiError,
  answerGate,
  checkGateEdits,
  createProject,
  createRun,
  datasetQuery,
  getRun,
  isJsonValue,
  listProjects,
  parseContractCheck,
  parseControlReply,
  parseExecutions,
  publicCandidateId,
  rewindRun,
  sendMessage,
  parseContracts,
  parseProfile,
  parseProjectDetail,
  parseProjects,
  parseRunSnapshot,
  parseVocabulary,
  uploadContract,
  validateContract,
  uploadDataset,
} from '../src/api/client.ts';

const project = { id: 'p1', slug: 'sample', name: 'Sample', created_at: '2026-01-01T00:00:00Z' };
const snapshot = {
  run: {
    id: 'r1', project_id: 'p1', started_at: '2026-01-01T00:00:00Z', mode: 'guided',
    status: 'awaiting_user', current_node: 'plan_models', forked_from_run_id: null, ended_at: null,
    contract_id: 'c1', champion_model_id: null, config_json: '{}', test_scored_at: null, test_fingerprint: null,
  },
  state: {},
  gates: [{ id: 'g1', run_id: 'r1', node_execution_id: 'n1', status: 'pending', question_json: '{}', answer_json: null, ts: 'now' }],
  execution_failed: false,
};

function isInvalidResponse(action: () => unknown): void {
  assert.throws(action, (error: unknown) =>
    error instanceof ApiError && error.message === 'Backend returned an invalid response.',
  );
}

test('project parser validates summaries and ignores extra backend fields', () => {
  assert.deepEqual(parseProjects([{ ...project, ignored: true }]), [project]);
  isInvalidResponse(() => parseProjects([{ id: 'p1' }]));
  isInvalidResponse(() => parseProjects({ project }));
  isInvalidResponse(() => parseProjects([null]));
});

test('run snapshot parser validates identity, unions, nullable fields, state, and gates', () => {
  assert.equal(parseRunSnapshot(snapshot, 'r1').run.status, 'awaiting_user');
  assert.equal(parseRunSnapshot({ ...snapshot, run: { ...snapshot.run, mode: 'autonomous', status: 'superseded' } }, 'r1').run.status, 'superseded');
  for (const value of [
    { ...snapshot, run: { ...snapshot.run, mode: 'auto' } },
    { ...snapshot, run: { ...snapshot.run, status: 'unknown' } },
    { ...snapshot, run: { ...snapshot.run, ended_at: undefined } },
    { ...snapshot, run: { ...snapshot.run, id: 'other' } },
    { ...snapshot, run: { ...snapshot.run, config_json: 1 } },
    { ...snapshot, gates: [{ ...snapshot.gates[0], run_id: 'other' }] },
    { ...snapshot, gates: [{ ...snapshot.gates[0], question_json: 1 }] },
    { ...snapshot, state: [] },
    { ...snapshot, execution_failed: 0 },
  ]) isInvalidResponse(() => parseRunSnapshot(value, 'r1'));
});

test('JSON values require finite numbers and recursive JSON types', () => {
  assert.equal(isJsonValue({ nested: [null, true, 4, 'x'] }), true);
  assert.equal(isJsonValue(Number.NaN), false);
  assert.equal(isJsonValue(Number.POSITIVE_INFINITY), false);
  assert.equal(isJsonValue(undefined), false);
  assert.equal(isJsonValue(() => undefined), false);
});

test('GET client sends same-origin path, accept header, and abort signal', async () => {
  const originalFetch = globalThis.fetch;
  const controller = new AbortController();
  const requests: Array<{ input: RequestInfo | URL; init: RequestInit | undefined }> = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ input, init });
    return new Response(JSON.stringify([project]), { status: 200 });
  };
  try {
    assert.deepEqual(await listProjects(controller.signal), [project]);
    assert.equal(requests[0]?.input, '/api/projects');
    assert.equal(requests[0]?.init?.method, 'GET');
    assert.equal(new Headers(requests[0]?.init?.headers).get('Accept'), 'application/json');
    assert.equal(requests[0]?.init?.signal, controller.signal);
    const requestedSnapshot = {
      ...snapshot,
      run: { ...snapshot.run, id: 'r/a ?' },
      gates: [{ ...snapshot.gates[0], run_id: 'r/a ?' }],
    };
    globalThis.fetch = async (input, init) => {
      requests.push({ input, init });
      return new Response(JSON.stringify(requestedSnapshot), { status: 200 });
    };
    assert.equal((await getRun('r/a ?', controller.signal)).run.id, 'r/a ?');
    assert.equal(requests[1]?.input, '/api/runs/r%2Fa%20%3F');
    const before = requests.length;
    await assert.rejects(getRun('  '), { message: 'Run ID is required.' });
    assert.equal(requests.length, before);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('HTTP and network failures expose fixed messages and never read error bodies', async () => {
  const originalFetch = globalThis.fetch;
  let bodyReads = 0;
  try {
    for (const [status, message] of [
      [403, 'Locked until the test score is recorded.'], [410, 'Artifact was pruned.'],
      [413, 'Upload is too large.'], [502, 'The agent could not produce a valid answer.'],
      [404, 'Resource not found.'], [409, 'Run transition rejected.'], [422, 'Invalid request.'],
      [503, 'Backend is unavailable.'], [500, 'Backend request failed.'],
    ] as const) {
      const response = new Response('SECRET', { status });
      response.json = async () => { bodyReads += 1; return { secret: 'SECRET' }; };
      globalThis.fetch = async () => response;
      await assert.rejects(listProjects(), (error: unknown) =>
        error instanceof ApiError && error.message === message && error.status === status,
      );
    }
    assert.equal(bodyReads, 0);
    globalThis.fetch = async () => { throw new Error('SECRET'); };
    await assert.rejects(listProjects(), (error: unknown) =>
      error instanceof ApiError && error.message === 'Cannot reach the backend.' && error.status === null,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('malformed success JSON is fixed and cancellation identity is preserved', async () => {
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response('{', { status: 200 });
    await assert.rejects(listProjects(), { message: 'Backend returned an invalid response.' });
    const controller = new AbortController();
    controller.abort();
    const fetchAbort = new DOMException('cancelled', 'AbortError');
    globalThis.fetch = async () => { throw fetchAbort; };
    await assert.rejects(listProjects(controller.signal), (error: unknown) => error === fetchAbort);
    const jsonAbort = new DOMException('cancelled', 'AbortError');
    globalThis.fetch = async () => new Response(null, { status: 200 });
    const response = await globalThis.fetch('/');
    response.json = async () => { throw jsonAbort; };
    globalThis.fetch = async () => response;
    await assert.rejects(listProjects(), (error: unknown) => error === jsonAbort);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

type Recorded = { input: RequestInfo | URL; init: RequestInit | undefined };

async function withFetch(body: unknown, action: (requests: Recorded[]) => Promise<void>): Promise<void> {
  const originalFetch = globalThis.fetch;
  const requests: Recorded[] = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ input, init });
    return new Response(JSON.stringify(body), { status: 200 });
  };
  try {
    await action(requests);
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test('createProject POSTs JSON', async () => {
  await withFetch(project, async (requests) => {
    assert.deepEqual(await createProject('sample', 'Sample'), project);
    assert.equal(requests[0]?.input, '/api/projects');
    assert.equal(requests[0]?.init?.method, 'POST');
    assert.equal(requests[0]?.init?.body, JSON.stringify({ slug: 'sample', name: 'Sample' }));
    assert.equal(new Headers(requests[0]?.init?.headers).get('Content-Type'), 'application/json');
  });
});

const dataset = {
  id: 'd1', project_id: 'p1', filename: 'data.csv', sha256: 'abc', n_rows: 3, n_cols: 2,
  created_at: '2026-01-01T00:00:00Z', path: 'ignored',
};

test('uploadDataset sends query and multipart form without a Content-Type header', async () => {
  await withFetch(dataset, async (requests) => {
    await uploadDataset('p1', new Blob(['a,b']), 'data.csv', { delimiter: ';', na_values: ['NA', '-'] });
    assert.equal(requests[0]?.input, '/api/projects/p1/datasets?delimiter=%3B&na_values=NA&na_values=-');
    assert.equal(requests[0]?.init?.method, 'POST');
    const body = requests[0]?.init?.body;
    assert.ok(body instanceof FormData);
    const file = body.get('file');
    assert.ok(file instanceof File);
    assert.equal(file.name, 'data.csv');
    assert.equal(new Headers(requests[0]?.init?.headers).get('Content-Type'), null);
  });
});

test('uploadContract sends the contract as contract.json', async () => {
  const contract = { target: { column: 'y' } };
  const row = { id: 'c1', project_id: 'p1', version: 1, created_at: 'now', contract_json: JSON.stringify(contract) };
  await withFetch(row, async (requests) => {
    assert.deepEqual((await uploadContract('p1', contract)).contract, contract);
    const body = requests[0]?.init?.body;
    assert.ok(body instanceof FormData);
    const file = body.get('file');
    assert.ok(file instanceof File);
    assert.equal(file.name, 'contract.json');
    assert.equal(await file.text(), JSON.stringify(contract));
  });
});

test('createRun POSTs the run request and returns the run id', async () => {
  await withFetch({ run_id: 'r1' }, async (requests) => {
    assert.equal(await createRun('p1', 'guided', 'd1', 'c1'), 'r1');
    assert.equal(requests[0]?.input, '/api/projects/p1/runs');
    assert.equal(requests[0]?.init?.body, JSON.stringify({ mode: 'guided', dataset_ids: ['d1'], contract_id: 'c1' }));
  });
});

test('datasetQuery serialises supported option values only', () => {
  assert.equal(datasetQuery({}), '');
  assert.equal(datasetQuery({ header_row: 0, x: null }), '?header_row=0');
});

test('setup parsers validate and transform backend payloads', () => {
  const row = { id: 'c1', project_id: 'p1', version: 1, created_at: 'now' };
  assert.deepEqual(parseContracts([{ ...row, contract_json: '{"a":1}' }])[0]?.contract, { a: 1 });
  isInvalidResponse(() => parseContracts([{ ...row, contract_json: '[1]' }]));
  isInvalidResponse(() => parseContracts([{ ...row, contract_json: '{' }]));
  const vocabulary = parseVocabulary({
    contract_schema: {},
    registry: {
      estimators: { ridge: { task: 'regression' }, lr: { task: 'classification' }, bad: 1 },
      inferential_models: { ols: {}, logit: {} },
    },
  });
  assert.deepEqual(vocabulary.estimators, [
    { name: 'lr', task: 'classification' }, { name: 'ridge', task: 'regression' },
  ]);
  assert.deepEqual(vocabulary.inferentialModels, ['logit', 'ols']);
  assert.deepEqual(parseVocabulary({ contract_schema: {}, registry: {} }).estimators, []);
  assert.deepEqual(
    parseContractCheck({ errors: [{ path: 'split.strategy', message: 'm' }, { path: '', message: 'n' }] }),
    [{ field: 'split.strategy', message: 'm' }, { field: 'contract', message: 'n' }],
  );
  isInvalidResponse(() => parseContractCheck({ errors: [{ path: 1, message: 'm' }] }));
  isInvalidResponse(() => parseContractCheck({}));
  const profile = {
    rows: 2, cols: 1, columns: ['a'], dtypes: { a: 'int' }, null_counts: { a: 0 }, n_unique: { a: 2 },
    roles: { a: 'numeric' }, role_evidence: { a: 'x' }, all_null: [],
  };
  assert.equal(parseProfile(profile).columns[0], 'a');
  isInvalidResponse(() => parseProfile({ ...profile, columns: [1] }));
  const detail = {
    project,
    runs: [],
    memory: [{ id: 'm1', key: 'k', value_json: '{"a":1}', source_run_id: null, ts: 'now' }],
  };
  assert.deepEqual(parseProjectDetail(detail).memory[0]?.value, { a: 1 });
  isInvalidResponse(() => parseProjectDetail({ ...detail, memory: [{ ...detail.memory[0], value_json: '{' }] }));
});

test('validateContract checks structure before the typed validate call', async () => {
  const originalFetch = globalThis.fetch;
  const contract = { target: { column: 'y' } };
  const urls: string[] = [];
  const bodies: unknown[] = [];
  let issues: unknown[] = [{ path: 'zzz', message: 'Extra inputs are not permitted' }];
  globalThis.fetch = async (input, init) => {
    urls.push(String(input));
    bodies.push(init?.body);
    const body = String(input) === '/api/contracts/check'
      ? { errors: issues }
      : { valid: true, mismatches: [], warnings: [] };
    return new Response(JSON.stringify(body), { status: 200 });
  };
  try {
    assert.deepEqual(await validateContract('p1', 'd1', contract), {
      valid: false,
      mismatches: [{ field: 'zzz', message: 'Extra inputs are not permitted' }],
      warnings: [],
    });
    assert.deepEqual(urls, ['/api/contracts/check']);
    assert.equal(bodies[0], JSON.stringify({ contract }));
    issues = [];
    urls.length = 0;
    assert.deepEqual(await validateContract('p1', 'd1', contract), { valid: true, mismatches: [], warnings: [] });
    assert.deepEqual(urls, ['/api/contracts/check', '/api/projects/p1/contracts/validate']);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('publicCandidateId strips the database prefix', () => {
  assert.equal(publicCandidateId(null), null);
  assert.equal(publicCandidateId('18:r_x:c_01'), 'c_01');
  assert.equal(publicCandidateId('c_02'), 'c_02');
});

test('parseExecutions maps rows and tolerates malformed embedded JSON', () => {
  const execution = {
    id: 'e1', run_id: 'r1', node_name: 'intake', candidate_id: '5:r_abc:c_01', seq: 1, attempt: 1,
    status: 'completed', summary: '{"a":1}', error: null, started_at: 's', ended_at: 'e',
  };
  const payload = {
    executions: [execution, { ...execution, id: 'e2', candidate_id: null, summary: 'not json', ended_at: null }],
    tool_calls: [{
      id: 't1', node_execution_id: 'e1', tool_name: 'profile', args_json: '{bad', result_json: '{"ok":true}',
      duration_ms: 3, ts: 'now',
    }],
    llm_calls: [{
      id: 'l1', node_execution_id: 'e1', signature: 'sig', model: 'm', prompt_tokens: 1, completion_tokens: 2,
      cached_tokens: 0, cost_usd: 0.25, duration_ms: 9, ts: 'now',
    }],
    cost_usd: 0.5,
  };
  assert.deepEqual(parseExecutions(payload), {
    executions: [
      {
        id: 'e1', candidateId: 'c_01', node: 'intake', seq: 1, attempt: 1, status: 'completed',
        summary: { a: 1 }, error: null, startedAt: 's', endedAt: 'e',
      },
      {
        id: 'e2', candidateId: null, node: 'intake', seq: 1, attempt: 1, status: 'completed',
        summary: null, error: null, startedAt: 's', endedAt: null,
      },
    ],
    toolCalls: [{ id: 't1', executionId: 'e1', tool: 'profile', args: null, result: { ok: true }, durationMs: 3, ts: 'now' }],
    llmCalls: [{
      id: 'l1', executionId: 'e1', signature: 'sig', model: 'm', promptTokens: 1, completionTokens: 2,
      cachedTokens: 0, costUsd: 0.25, durationMs: 9, ts: 'now',
    }],
    costUsd: 0.5,
  });
  isInvalidResponse(() => parseExecutions({ ...payload, cost_usd: 'x' }));
});

test('parseControlReply reads the optional new run id', () => {
  assert.deepEqual(parseControlReply({ text: 't', category: 'c', new_run_id: 'r2' }), { text: 't', category: 'c', newRunId: 'r2' });
  assert.deepEqual(parseControlReply({ text: 't', category: 'c' }), { text: 't', category: 'c', newRunId: null });
  isInvalidResponse(() => parseControlReply({ text: 't', category: 'c', new_run_id: 1 }));
});

test('run actions POST the documented bodies', async () => {
  const reply = { text: 't', category: 'c' };
  await withFetch(reply, async (requests) => {
    await answerGate('r1', 'g1', { revision: 2, action: 'skip_node', reason: 'r' });
    assert.equal(requests[0]?.input, '/api/runs/r1/gates/g1');
    assert.equal(requests[0]?.init?.body, '{"revision":2,"action":"skip_node","reason":"r"}');
    await sendMessage('r1', 'hi');
    assert.equal(requests[1]?.input, '/api/runs/r1/messages');
    assert.equal(requests[1]?.init?.body, '{"text":"hi"}');
  });
  await withFetch({ new_run_id: 'r2' }, async (requests) => {
    assert.equal(await rewindRun('r1', 'intake', 'u1'), 'r2');
    assert.equal(requests[0]?.input, '/api/runs/r1/control');
    assert.equal(requests[0]?.init?.body, '{"action":"rewind","target":"intake","request_id":"u1"}');
  });
  await withFetch({ errors: [] }, async (requests) => {
    assert.deepEqual(await checkGateEdits('r1', 'g1', { a: 1 }), []);
    assert.equal(requests[0]?.input, '/api/runs/r1/gates/g1/check');
    assert.equal(requests[0]?.init?.body, '{"edits":{"a":1}}');
  });
});
