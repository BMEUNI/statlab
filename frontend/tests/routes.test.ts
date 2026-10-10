import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatRoute, parseRoute, type Route } from '../src/routes.ts';

test('routes round-trip', () => {
  const routes: Route[] = [
    { name: 'home' },
    { name: 'projects' },
    { name: 'project', projectId: 'a/b c' },
    { name: 'setup', projectId: 'a/b c' },
    { name: 'run', runId: 'a/b c' },
    { name: 'admin', section: 'overview' },
    { name: 'admin', section: 'people' },
    { name: 'admin', section: 'projects' },
    { name: 'admin', section: 'runs' },
    { name: 'admin', section: 'defaults' },
  ];
  for (const route of routes) assert.deepEqual(parseRoute(formatRoute(route)), route);
  assert.equal(formatRoute({ name: 'notFound' }), '#/');
  assert.equal(formatRoute({ name: 'admin', section: 'overview' }), '#/admin');
});

test('admin routes', () => {
  assert.deepEqual(parseRoute('#/admin'), { name: 'admin', section: 'overview' });
  assert.deepEqual(parseRoute('#/admin/x'), { name: 'notFound' });
  assert.deepEqual(parseRoute('#/admin/people/x'), { name: 'notFound' });
});

test('root and invalid hashes', () => {
  for (const hash of ['', '#', '#/']) assert.deepEqual(parseRoute(hash), { name: 'home' });
  assert.deepEqual(parseRoute('#/projects'), { name: 'projects' });
  for (const hash of ['#/projects/', '#/x', '#/runs/a/b', '#/projects/%E0%A4%A']) {
    assert.deepEqual(parseRoute(hash), { name: 'notFound' });
  }
});
