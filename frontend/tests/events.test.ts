import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiError } from '../src/api/client.ts';
import { connectRunEvents, parseRunEvent, type EventSourceHandle, type StreamStatus } from '../src/api/events.ts';
import type { RunEvent } from '../src/api/types.ts';

type FakeSource = EventSourceHandle & { closed: number; setReadyState(value: number): void; emit(seq: number, runId?: string, id?: string, candidateId?: string): void };

function fakeSource(): FakeSource {
  let readyState = 0;
  const source: FakeSource = {
    get readyState() { return readyState; },
    onopen: null,
    onerror: null,
    onmessage: null,
    closed: 0,
    close() { this.closed += 1; },
    setReadyState(value) { readyState = value; },
    emit(seq, runId = 'r1', id = String(seq), candidateId = 'c1') {
      this.onmessage?.(new MessageEvent('message', {
        data: JSON.stringify({ seq, run_id: runId, ts: 'now', type: 'candidate', payload: { candidate_id: candidateId } }),
        lastEventId: id,
      }));
    },
  };
  return source;
}

function assertInvalid(value: unknown, runId: string, id: string): void {
  assert.throws(() => parseRunEvent(value, runId, id), (error: unknown) =>
    error instanceof ApiError && error.message === 'Backend returned an invalid event.',
  );
}

test('event parser validates sequence identity and payload while allowing unknown types', () => {
  const event = { seq: 1, run_id: 'r1', ts: 'now', type: 'future-event', payload: { candidate_id: 'c1' } };
  assert.deepEqual(parseRunEvent(event, 'r1', '1'), event);
  for (const seq of [0, 1.5, Number.MAX_SAFE_INTEGER + 1]) assertInvalid({ ...event, seq }, 'r1', String(seq));
  assertInvalid({ ...event, run_id: 'other' }, 'r1', '1');
  assertInvalid(event, 'r1', '2');
  assertInvalid(event, 'r1', '01');
  assertInvalid(event, 'r1', '');
  assertInvalid({ ...event, payload: [] }, 'r1', '1');
});

test('one native source reconnects, skips duplicates, and continues its total sequence', () => {
  const source = fakeSource();
  const urls: string[] = [];
  const events: RunEvent[] = [];
  const statuses: StreamStatus[] = [];
  const cleanup = connectRunEvents('r/a', (event) => events.push(event), (status) => statuses.push(status), (url) => {
    urls.push(url);
    return source;
  });
  assert.deepEqual(statuses, ['connecting']);
  assert.deepEqual(urls, ['/api/runs/r%2Fa/events?since=0']);
  source.onopen?.(new Event('open'));
  source.emit(1, 'r/a', undefined, 'c_a');
  source.emit(2, 'r/a', undefined, 'c_b');
  source.emit(2, 'r/a', undefined, 'c_b');
  source.setReadyState(0);
  source.onerror?.(new Event('error'));
  source.onopen?.(new Event('open'));
  source.emit(3, 'r/a', undefined, 'c_a');
  source.setReadyState(2);
  source.onerror?.(new Event('error'));
  assert.deepEqual(events.map((event) => event.seq), [1, 2, 3]);
  assert.deepEqual(events.map((event) => event.payload.candidate_id), ['c_a', 'c_b', 'c_a']);
  assert.deepEqual(statuses, ['connecting', 'connected', 'reconnecting', 'connected', 'closed']);
  assert.equal(urls.length, 1);
  cleanup();
});

test('invalid JSON, identity, and sequence gaps close once without delivery', () => {
  for (const emit of [
    (source: FakeSource) => source.onmessage?.(new MessageEvent('message', { data: '{', lastEventId: '1' })),
    (source: FakeSource) => source.emit(1, 'other'),
    (source: FakeSource) => source.emit(2),
  ]) {
    const source = fakeSource();
    const events: number[] = [];
    const statuses: StreamStatus[] = [];
    const cleanup = connectRunEvents('r1', (event) => events.push(event.seq), (status) => statuses.push(status), () => source);
    const onmessage = source.onmessage;
    const onopen = source.onopen;
    const onerror = source.onerror;
    emit(source);
    cleanup();
    cleanup();
    onmessage?.(new MessageEvent('message', { data: '{}', lastEventId: '1' }));
    onopen?.(new Event('open'));
    onerror?.(new Event('error'));
    assert.deepEqual(events, []);
    assert.deepEqual(statuses, ['connecting', 'error']);
    assert.equal(source.closed, 1);
    assert.equal(source.onmessage, null);
    assert.equal(source.onopen, null);
    assert.equal(source.onerror, null);
  }
});

test('cleanup is idempotent and constructor failure never leaks its error', () => {
  const source = fakeSource();
  const statuses: StreamStatus[] = [];
  const cleanup = connectRunEvents('r1', () => undefined, (status) => statuses.push(status), () => source);
  const onopen = source.onopen;
  const onmessage = source.onmessage;
  cleanup();
  cleanup();
  onopen?.(new Event('open'));
  onmessage?.(new MessageEvent('message', { data: '{}', lastEventId: '1' }));
  assert.deepEqual(statuses, ['connecting', 'closed']);
  assert.equal(source.closed, 1);
  assert.equal(source.onopen, null);
  assert.equal(source.onerror, null);
  assert.equal(source.onmessage, null);
  assert.doesNotThrow(() => connectRunEvents('r1', () => undefined, (status) => statuses.push(status), () => { throw new Error('SECRET'); }));
  assert.equal(statuses.at(-1), 'error');
});

test('separate run connections own separate cursors and blank IDs do not connect', () => {
  const first = fakeSource();
  const second = fakeSource();
  const events: string[] = [];
  const stopFirst = connectRunEvents('r1', (event) => events.push(`${event.run_id}:${event.seq}`), () => undefined, () => first);
  const staleMessage = first.onmessage;
  stopFirst();
  const stopSecond = connectRunEvents('r2', (event) => events.push(`${event.run_id}:${event.seq}`), () => undefined, () => second);
  second.emit(1, 'r2');
  staleMessage?.(new MessageEvent('message', { data: JSON.stringify({ seq: 1, run_id: 'r1', ts: 'now', type: 'candidate', payload: { candidate_id: 'c1' } }), lastEventId: '1' }));
  assert.deepEqual(events, ['r2:1']);
  stopSecond();
  let constructed = false;
  const statuses: StreamStatus[] = [];
  connectRunEvents(' ', () => undefined, (status) => statuses.push(status), () => { constructed = true; return fakeSource(); });
  assert.equal(constructed, false);
  assert.deepEqual(statuses, ['connecting', 'error']);
});
