import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  EMPTY_STATE, clearSession, loadSession, newSession, nextStep, saveSession, toTurns,
  type ChatMessage, type KeyValueStore,
} from '../src/chat/session.ts';

function memoryStore(): KeyValueStore {
  const data = new Map<string, string>();
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => { data.set(key, value); },
    removeItem: (key) => { data.delete(key); },
  };
}

const throwingStore: KeyValueStore = {
  getItem: () => { throw new Error('x'); },
  setItem: () => { throw new Error('x'); },
  removeItem: () => { throw new Error('x'); },
};

test('nextStep walks the setup order', () => {
  assert.equal(nextStep(EMPTY_STATE), 'project');
  assert.equal(nextStep({ ...EMPTY_STATE, project_id: 'p' }), 'dataset');
  assert.equal(nextStep({ ...EMPTY_STATE, project_id: 'p', dataset_id: 'd' }), 'target');
  assert.equal(nextStep({ ...EMPTY_STATE, project_id: 'p', dataset_id: 'd', target: 't' }), 'contract');
  assert.equal(nextStep({ project_id: 'p', dataset_id: 'd', target: 't', contract_id: 'c', mode: null }), 'mode');
  assert.equal(nextStep({ project_id: 'p', dataset_id: 'd', target: 't', contract_id: 'c', mode: 'guided' }), 'start');
});

test('toTurns drops notes and keeps the last 40', () => {
  const messages: ChatMessage[] = [{ id: 'n', role: 'note', text: 'note' }];
  for (let i = 0; i < 50; i += 1) messages.push({ id: String(i), role: i % 2 === 0 ? 'user' : 'assistant', text: `m${i}` });
  const turns = toTurns(messages);
  assert.equal(turns.length, 40);
  assert.deepEqual(turns[0], { role: 'user', text: 'm10' });
  assert.deepEqual(toTurns([messages[0] as ChatMessage]), []);
});

test('session save/load round trip and clear', () => {
  const store = memoryStore();
  const session = newSession();
  saveSession(store, 'k', session);
  assert.deepEqual(loadSession(store, 'k'), session);
  clearSession(store, 'k');
  assert.equal(loadSession(store, 'k'), null);
});

test('malformed stored sessions load as null', () => {
  const store = memoryStore();
  store.setItem('a', '{not json');
  assert.equal(loadSession(store, 'a'), null);
  store.setItem('b', JSON.stringify({ messages: [{ id: '1', role: 'bot', text: 'x' }], state: EMPTY_STATE }));
  assert.equal(loadSession(store, 'b'), null);
  store.setItem('c', JSON.stringify({ messages: [], state: { mode: 'bogus' } }));
  assert.equal(loadSession(store, 'c'), null);
});

test('unavailable storage is tolerated', () => {
  assert.equal(loadSession(throwingStore, 'k'), null);
  saveSession(throwingStore, 'k', newSession());
  clearSession(throwingStore, 'k');
  assert.equal(loadSession(null, 'k'), null);
  saveSession(null, 'k', newSession());
  clearSession(null, 'k');
});
