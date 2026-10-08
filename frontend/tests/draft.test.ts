import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { DatasetProfile } from '../src/api/types.ts';
import {
  columnRows, listWith, listWithout, parseContractText, setAt, setIdColumn, setIncluded, setTarget, unsetAt,
} from '../src/contract/draft.ts';

const profile: DatasetProfile = {
  rows: 3, cols: 3, columns: ['id', 'x', 'y'], dtypes: { id: 'int', x: 'float', y: 'int' },
  null_counts: { id: 0, x: 1 }, n_unique: { id: 3 }, roles: { x: 'numeric' }, role_evidence: {}, all_null: [],
};

test('setAt creates containers immutably', () => {
  const root = {};
  assert.deepEqual(setAt(root, ['a', 'b', 0], 1), { a: { b: [1] } });
  assert.deepEqual(root, {});
  assert.deepEqual(setAt(root, [], 1), root);
});

test('unsetAt prunes empty parents and keeps siblings', () => {
  assert.deepEqual(unsetAt({ features: { roles: { a: 'numeric' } } }, ['features', 'roles', 'a']), {});
  assert.deepEqual(
    unsetAt({ features: { roles: { a: 'numeric' }, id_columns: ['a'] } }, ['features', 'roles', 'a']),
    { features: { id_columns: ['a'] } },
  );
});

test('list helpers', () => {
  const once = listWith({}, ['l'], 'a');
  assert.deepEqual(listWith(once, ['l'], 'a'), { l: ['a'] });
  assert.deepEqual(listWithout(once, ['l'], 'a'), {});
});

test('column editing', () => {
  assert.deepEqual(setTarget({ features: { exclude: ['y'] } }, 'y'), { target: { column: 'y' } });
  const start = { target: { column: 'y' } };
  const excluded = setIncluded(start, 'x', false);
  assert.deepEqual(excluded, { target: { column: 'y' }, features: { exclude: ['x'] } });
  assert.deepEqual(setIncluded(excluded, 'x', true), start);
  assert.deepEqual(setIncluded({ features: { include: ['x'] } }, 'id', true), { features: { include: ['x', 'id'] } });
  const withId = setIdColumn(start, 'id', true);
  assert.deepEqual(withId, { target: { column: 'y' }, features: { id_columns: ['id'] } });
  assert.deepEqual(setIdColumn(withId, 'id', false), start);
});

test('columnRows reports inclusion', () => {
  const rows = columnRows(profile, { target: { column: 'y' }, features: { exclude: ['id'], include: ['id', 'y'], roles: { y: 'binary' } } });
  assert.deepEqual(rows.map((row) => row.included), [false, false, true]);
  assert.equal(rows[2]?.isTarget, true);
  assert.equal(rows[2]?.role, 'binary');
  assert.equal(rows[1]?.inferredRole, 'numeric');
  assert.equal(rows[1]?.nulls, 1);
  assert.equal(rows[1]?.unique, null);
});

test('parseContractText', () => {
  assert.deepEqual(parseContractText('{'), { ok: false, error: 'Not valid JSON.' });
  assert.deepEqual(parseContractText('[1]'), { ok: false, error: 'The contract must be a JSON object.' });
  assert.deepEqual(parseContractText('{}'), { ok: false, error: 'The contract needs target.column.' });
  assert.deepEqual(parseContractText('{"target":{"column":"y"}}'), { ok: true, contract: { target: { column: 'y' } } });
});
