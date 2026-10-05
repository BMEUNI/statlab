import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  ApiError,
  getRun,
  isJsonValue,
  listProjects,
  parseProjects,
  parseRunSnapshot,
} from '../src/api/client.ts';

const project = { id: 'p1', slug: 'sample', name: 'Sample', created_at: '2026-01-01T00:00:00Z' };
const snapshot = {
  run: {
    id: 'r1', project_id: 'p1', started_at: '2026-01-01T00:00:00Z', mode: 'guided',
    status: 'awaiting_user', current_node: 'plan_models', forked_from_run_id: null, ended_at: null,
  },
  state: {},
  gates: [{ id: 'g1', run_id: 'r1', node_execution_id: 'n1', status: 'pending' }],
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
    { ...snapshot, gates: [{ ...snapshot.gates[0], run_id: 'other' }] },
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
