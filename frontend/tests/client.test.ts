import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ApiError,
  assistantTurn,
  parseAssistantReply,
  artifactUrl,
  parseArtifactEntries,
  parseArtifactPreview,
  previewArtifact,
  getValidation,
  parseValidationPage,
  parsePredictionPreview,
  predictUnlabelled,
  answerGate,
  checkGateEdits,
  createProject,
  createRun,
  datasetQuery,
  deleteProject,
  getRun,
  getRunFile,
  runFileUrl,
  isJsonValue,
  listProjects,
  renameProject,
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
  onSignedOut,
  signIn,
  parseAdminOverview,
  parseMembers,
  parseRunBudget,
  updateAdminUser,
  saveAdminSettings,
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
  view_only: false,
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
    { ...snapshot, view_only: 'no' },
  ]) isInvalidResponse(() => parseRunSnapshot(value, 'r1'));
  assert.equal(parseRunSnapshot({ ...snapshot, view_only: true }, 'r1').view_only, true);
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
      [401, 'Sign in to continue.'], [402, 'A budget limit is reached. Ask an admin to raise it.'],
      [429, 'Too many failed sign-ins. Try again in 15 minutes.'],
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
    assert.equal(new Headers(requests[0]?.init?.headers).get('X-Statlab-Csrf'), '1');
  });
});

test('onSignedOut fires on 401 only', async () => {
  const originalFetch = globalThis.fetch;
  let count = 0;
  let removedRan = false;
  const stop = onSignedOut(() => { count += 1; });
  const stopRemoved = onSignedOut(() => { removedRan = true; });
  stopRemoved();
  try {
    globalThis.fetch = async () => new Response('x', { status: 401 });
    await assert.rejects(listProjects());
    assert.equal(count, 1);
    globalThis.fetch = async () => new Response('x', { status: 404 });
    await assert.rejects(listProjects());
    assert.equal(count, 1);
    assert.equal(removedRan, false);
  } finally {
    stop();
    globalThis.fetch = originalFetch;
  }
});

test('signIn posts credentials and maps 401', async () => {
  const user = { id: 'u_1', email: 'a@b.co', name: 'A', is_admin: false };
  await withFetch(user, async (requests) => {
    assert.deepEqual(await signIn('a@b.co', 'pw-1234567'), user);
    assert.equal(requests[0]?.input, '/api/auth/login');
    assert.equal(requests[0]?.init?.method, 'POST');
    assert.equal(requests[0]?.init?.body, JSON.stringify({ email: 'a@b.co', password: 'pw-1234567' }));
  });
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response('x', { status: 401 });
    await assert.rejects(signIn('a@b.co', 'bad'), { message: 'Email or password is incorrect.' });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

const adminRun = {
  id: 'r1', project_id: 'p1', project_name: 'Sample', status: 'paused', started_at: 't',
  limit_usd: null,
  budget: {
    run: { spent_usd: 1, limit_usd: 1 }, project: { spent_usd: 0, limit_usd: null },
    person: { spent_usd: 2, limit_usd: 5 }, started_by: 'u_1', blocked: 'run limit reached',
  },
};
const adminOverview = {
  month: '2026-10', month_spent_usd: 1, total_spent_usd: 2, users: 2, admins: 1,
  disabled_users: 0, projects: 1, runs: 1, open_runs: 1, blocked_runs: [adminRun],
  settings: {
    default_monthly_limit_usd: null, default_project_limit_usd: null,
    default_run_limit_usd: 1, signup_open: true,
  },
};

test('admin parsers', () => {
  assert.deepEqual(parseAdminOverview(adminOverview), adminOverview);
  isInvalidResponse(() => parseAdminOverview({
    ...adminOverview, blocked_runs: [{ ...adminRun, status: 'boss' }],
  }));
  isInvalidResponse(() => parseMembers([{ user_id: 'u', email: 'e', name: 'n', role: 'boss', added_at: 't' }]));
  assert.deepEqual(parseRunBudget(adminRun.budget), adminRun.budget);
});

test('admin writes use PATCH and PUT', async () => {
  const adminUser = {
    id: 'u/1', email: 'a@b.co', name: 'A', is_admin: false, disabled: false, created_at: 't',
    last_login_at: null, monthly_limit_usd: 2, month: { spent_usd: 0, limit_usd: 2 },
    total_spent_usd: 0, projects: 0, runs: 0,
  };
  await withFetch(adminUser, async (requests) => {
    assert.deepEqual(await updateAdminUser('u/1', { monthly_limit_usd: 2 }), adminUser);
    assert.equal(requests[0]?.input, '/api/admin/users/u%2F1');
    assert.equal(requests[0]?.init?.method, 'PATCH');
    assert.equal(requests[0]?.init?.body, '{"monthly_limit_usd":2}');
  });
  const settings = {
    default_monthly_limit_usd: null, default_project_limit_usd: null,
    default_run_limit_usd: null, signup_open: false,
  };
  await withFetch(settings, async (requests) => {
    await saveAdminSettings(settings);
    assert.equal(requests[0]?.init?.method, 'PUT');
    assert.equal(requests[0]?.input, '/api/admin/settings');
  });
});

test('deleteProject sends DELETE', async () => {
  const originalFetch = globalThis.fetch;
  const requests: Recorded[] = [];
  globalThis.fetch = async (input, init) => {
    requests.push({ input, init });
    return new Response(null, { status: 204 });
  };
  try {
    assert.equal(await deleteProject('p1'), undefined);
    assert.equal(requests[0]?.input, '/api/projects/p1');
    assert.equal(requests[0]?.init?.method, 'DELETE');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('renameProject PATCHes the name', async () => {
  await withFetch(project, async (requests) => {
    assert.deepEqual(await renameProject('p1', 'Sample'), project);
    assert.equal(requests[0]?.input, '/api/projects/p1');
    assert.equal(requests[0]?.init?.method, 'PATCH');
    assert.equal(requests[0]?.init?.body, JSON.stringify({ name: 'Sample' }));
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
    switched_runs: ['r_1'],
    memory: [{ id: 'm1', key: 'k', value_json: '{"a":1}', source_run_id: null, ts: 'now' }],
  };
  assert.deepEqual(parseProjectDetail(detail).memory[0]?.value, { a: 1 });
  assert.deepEqual(parseProjectDetail(detail).switched_runs, ['r_1']);
  isInvalidResponse(() => parseProjectDetail({ ...detail, switched_runs: [1] }));
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

const artifactRow = (id: string, meta: string, prunedAt: string | null, locked: boolean) => ({
  artifact: { id, run_id: 'r1', kind: 'dataframe', path: 'p', meta_json: meta, produced_by: 'n1', ts: 't', pruned_at: prunedAt },
  locked,
});

test('parseArtifactEntries reads meta, tolerates bad meta and rejects wrong types', () => {
  const meta = JSON.stringify({ ref: 'x', label: 'Train', rows: 10, cols: 3, split: 'train' });
  assert.deepEqual(parseArtifactEntries([artifactRow('a1', meta, null, false), artifactRow('a2', '{bad', 'then', true)]), [
    { id: 'a1', kind: 'dataframe', label: 'Train', split: 'train', rows: 10, cols: 3, producedBy: 'n1', ts: 't', pruned: false, locked: false },
    { id: 'a2', kind: 'dataframe', label: null, split: null, rows: null, cols: null, producedBy: 'n1', ts: 't', pruned: true, locked: true },
  ]);
  isInvalidResponse(() => parseArtifactEntries([{ ...artifactRow('a1', meta, null, false), locked: 'no' }]));
});

test('parseArtifactPreview maps columns from dtypes', () => {
  assert.deepEqual(parseArtifactPreview({ rows: 5, cols: 2, dtypes: { a: 'int64', b: 'object' }, head: [{ a: 1, b: 'x' }] }), {
    rows: 5, cols: 2, columns: ['a', 'b'], dtypes: { a: 'int64', b: 'object' }, head: [{ a: 1, b: 'x' }],
  });
});

test('artifactUrl encodes and previewArtifact requests the preview path', async () => {
  assert.equal(artifactUrl('a/b'), '/api/artifacts/a%2Fb');
  await withFetch({ rows: 1, cols: 1, dtypes: { a: 'int64' }, head: [] }, async (requests) => {
    await previewArtifact('a1');
    assert.equal(requests[0]?.input, '/api/artifacts/a1/preview');
    assert.equal(requests[0]?.init?.method, 'GET');
  });
});

test('runFileUrl encodes the run id only and getRunFile returns text', async () => {
  assert.equal(runFileUrl('r 1', 'plots/a.png'), '/api/runs/r%201/files/plots/a.png');
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response('# Report', { status: 200 });
  try {
    assert.equal(await getRunFile('r1', 'report.md'), '# Report');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

const assistantReply = {
  reply: 'Which dataset?',
  state: { project_id: 'p1', dataset_id: null, target: null, contract_id: null, mode: 'guided' },
  start: false,
  issues: ['Missing target'],
};

test('assistant reply parser validates fields', () => {
  assert.deepEqual(parseAssistantReply(assistantReply), {
    ...assistantReply,
    state: { project_id: 'p1', dataset_id: null, target: null, contract_id: null, mode: 'guided' },
  });
  assert.equal(parseAssistantReply({ ...assistantReply, state: { project_id: 'p1' } }).state.mode, null);
  isInvalidResponse(() => parseAssistantReply({ ...assistantReply, state: { mode: 'bogus' } }));
  isInvalidResponse(() => parseAssistantReply({ ...assistantReply, start: 'yes' }));
});

test('assistantTurn POSTs messages and state', async () => {
  await withFetch(assistantReply, async (requests) => {
    const state = { project_id: null, dataset_id: null, target: null, contract_id: null, mode: null };
    const messages = [{ role: 'user' as const, text: 'hi' }];
    assert.equal((await assistantTurn(messages, state)).reply, 'Which dataset?');
    assert.equal(requests[0]?.input, '/api/assistant/turn');
    assert.equal(requests[0]?.init?.body, JSON.stringify({ messages, state }));
  });
});

test('getValidation builds the validation query and returns the parsed camelCase page', async () => {
  const body = {
    task: 'classification', total: 2, shown_total: 2, offset: 50, limit: 50, correct: 1,
    mean_abs_error: null, threshold: 0.4, columns: ['x'],
    rows: [{ row: 3, actual: 'a', predicted: 'b', confidence: 0.75, error: null, correct: false, values: { x: 1 } }],
  };
  await withFetch(body, async (requests) => {
    const page = await getValidation('r1', { candidate: 'c_01', worstFirst: true, offset: 50, limit: 50 });
    assert.equal(requests[0]?.input, '/api/runs/r1/validation?candidate=c_01&worst_first=true&offset=50&limit=50');
    assert.equal(page.shownTotal, 2);
    assert.equal(page.meanAbsError, null);
    assert.deepEqual(page.rows[0], { row: 3, actual: 'a', predicted: 'b', confidence: 0.75, error: null, correct: false, values: { x: 1 } });
  });
});

test('parseValidationPage rejects a bad task and a row whose values are not an object', () => {
  const page = { task: 'regression', total: 0, shown_total: 0, offset: 0, limit: 50, rows: [], columns: [] };
  assert.equal(parseValidationPage(page).task, 'regression');
  assert.throws(() => parseValidationPage({ ...page, task: 'other' }), ApiError);
  assert.throws(() => parseValidationPage({ ...page, rows: [{ row: 1, actual: 1, predicted: 1, confidence: null, error: 0, correct: null, values: 'x' }] }), ApiError);
});

test('predictUnlabelled POSTs and parses the preview', async () => {
  const body = { task: 'classification', rows: 3, columns: ['row', 'x', 'prediction'], head: [{ row: 4, x: 1, prediction: 'a' }] };
  await withFetch(body, async (requests) => {
    const preview = await predictUnlabelled('r1');
    assert.equal(requests[0]?.input, '/api/runs/r1/predictions');
    assert.equal(requests[0]?.init?.method, 'POST');
    assert.equal(new Headers(requests[0]?.init?.headers).get('X-Statlab-Csrf'), '1');
    assert.deepEqual(preview, body);
  });
});

test('parsePredictionPreview rejects a bad task, columns and head', () => {
  const preview = { task: 'regression', rows: 0, columns: [], head: [] };
  assert.equal(parsePredictionPreview(preview).task, 'regression');
  assert.throws(() => parsePredictionPreview({ ...preview, task: 'other' }), ApiError);
  assert.throws(() => parsePredictionPreview({ ...preview, columns: [1] }), ApiError);
  assert.throws(() => parsePredictionPreview({ ...preview, head: ['x'] }), ApiError);
  assert.throws(() => parsePredictionPreview({ ...preview, rows: 'many' }), ApiError);
});
