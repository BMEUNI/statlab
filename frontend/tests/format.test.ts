import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { RunSummary } from '../src/api/types.ts';
import {
  formatTime, isValidSlug, lastActivity, naValuesFromText, naValuesToText, pathKey, slugify,
} from '../src/ui/format.ts';

test('slugify', () => {
  assert.equal(slugify('Churn Study 2026!'), 'churn-study-2026');
  assert.equal(slugify('  --  '), '');
  const long = slugify(`${'a'.repeat(63)} bbb`);
  assert.ok(long.length <= 64);
  assert.ok(!long.endsWith('-'));
  assert.ok(slugify('x'.repeat(100)).length <= 64);
});

test('isValidSlug', () => {
  assert.equal(isValidSlug('churn_1-a'), true);
  assert.equal(isValidSlug('-a'), false);
  assert.equal(isValidSlug('A'), false);
  assert.equal(isValidSlug(''), false);
});

test('lastActivity', () => {
  const run = (started: string, ended: string | null): RunSummary => ({
    id: 'r', project_id: 'p', started_at: started, mode: 'guided', status: 'completed', current_node: null,
    forked_from_run_id: null, ended_at: ended, contract_id: 'c', champion_model_id: null, config_json: '{}',
    test_scored_at: null, test_fingerprint: null,
  });
  assert.equal(
    lastActivity('2026-01-01T00:00:00Z', [run('2026-01-02T00:00:00Z', '2026-01-05T00:00:00Z'), run('2026-01-03T00:00:00Z', null)]),
    '2026-01-05T00:00:00Z',
  );
  assert.equal(lastActivity('2026-01-01T00:00:00Z', []), '2026-01-01T00:00:00Z');
});

test('NA text', () => {
  const values = ['', 'NA', 'null'];
  assert.deepEqual(naValuesFromText(naValuesToText(values)), values);
  assert.deepEqual(naValuesFromText('NA\n NA \n\n(empty)\n(empty)'), ['NA', '']);
});

test('pathKey and formatTime', () => {
  assert.equal(pathKey(['objective', 'inference_goals', 0, 'predictors']), 'objective.inference_goals.predictors');
  assert.equal(formatTime(null), '—');
  assert.equal(formatTime('not a date'), 'not a date');
});
