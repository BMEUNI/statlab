import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { JsonObject } from '../src/api/types.ts';
import { fieldsOf, refName, resolveDef } from '../src/contract/schema.ts';

const schema: JsonObject = {
  properties: {
    version: { const: 1, type: 'integer' },
    target: { $ref: '#/$defs/Target' },
    notes: { type: 'string', title: 'Notes', description: 'Free text.' },
  },
  $defs: {
    Target: {
      properties: {
        task: { anyOf: [{ enum: ['a', 'b'], type: 'string' }, { type: 'null' }] },
        metric: { anyOf: [{ enum: ['m1'], type: 'string' }, { enum: ['m2', 'm3'], type: 'string' }] },
        union: { anyOf: [{ type: 'string' }, { type: 'integer' }, { type: 'boolean' }, { type: 'null' }] },
        refs: { type: 'array', items: { $ref: '#/$defs/Item' } },
        multi: { type: 'array', items: { anyOf: [{ enum: ['p'] }, { enum: ['q'] }] } },
        emap: { type: 'object', additionalProperties: { enum: ['u', 'v'] } },
        lmap: { type: 'object', additionalProperties: { type: 'array', items: { type: 'string' } } },
        ratio: { type: 'number', default: 0.5 },
        flag: { type: 'boolean' },
      },
    },
  },
};

test('fieldsOf classifies root and definition properties', () => {
  assert.deepEqual(fieldsOf(schema, null), [
    { key: 'target', title: 'target', description: null, kind: 'object', nullable: false, options: [], defaultValue: undefined, ref: 'Target' },
    { key: 'notes', title: 'Notes', description: 'Free text.', kind: 'string', nullable: false, options: [], defaultValue: undefined, ref: null },
  ]);
  const base = { title: '', description: null, nullable: false, options: [], defaultValue: undefined, ref: null };
  const expected = [
    { ...base, key: 'task', kind: 'enum', nullable: true, options: ['a', 'b'] },
    { ...base, key: 'metric', kind: 'enum', options: ['m1', 'm2', 'm3'] },
    { ...base, key: 'union', kind: 'json', nullable: true },
    { ...base, key: 'refs', kind: 'object-list', ref: 'Item' },
    { ...base, key: 'multi', kind: 'enum-list', options: ['p', 'q'] },
    { ...base, key: 'emap', kind: 'enum-map', options: ['u', 'v'] },
    { ...base, key: 'lmap', kind: 'list-map' },
    { ...base, key: 'ratio', kind: 'number', defaultValue: 0.5 },
    { ...base, key: 'flag', kind: 'boolean' },
  ].map((field) => ({ ...field, title: field.key }));
  assert.deepEqual(fieldsOf(schema, 'Target'), expected);
  assert.equal(fieldsOf(schema, 'Missing').length, 0);
  assert.equal(resolveDef(schema, 'Missing'), null);
  assert.equal(refName({ $ref: 'other' }), null);
});
