import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  formatUsd,
  inheritedText,
  initials,
  isOpenStatus,
  limitText,
  meterFill,
  meterText,
  meterTone,
  monthLabel,
  parseLimit,
} from '../src/admin/budget.ts';

test('formatUsd and meter helpers', () => {
  assert.equal(formatUsd(0), '$0.00');
  assert.equal(formatUsd(1234.5), '$1,234.50');
  assert.equal(formatUsd(0.004), '<$0.01');
  assert.equal(meterTone({ spent_usd: 0.5, limit_usd: null }), 'none');
  assert.equal(meterTone({ spent_usd: 1, limit_usd: 2 }), 'ok');
  assert.equal(meterTone({ spent_usd: 1.6, limit_usd: 2 }), 'near');
  assert.equal(meterTone({ spent_usd: 2, limit_usd: 2 }), 'over');
  assert.equal(meterTone({ spent_usd: 0, limit_usd: 0 }), 'over');
  assert.equal(meterFill({ spent_usd: 1, limit_usd: null }), 0);
  assert.equal(meterFill({ spent_usd: 0, limit_usd: 0 }), 1);
  assert.equal(meterFill({ spent_usd: 0.5, limit_usd: 2 }), 0.25);
  assert.equal(meterFill({ spent_usd: 3, limit_usd: 2 }), 1);
  assert.equal(meterText({ spent_usd: 0.5, limit_usd: null }), '$0.50 spent, no limit');
  assert.equal(meterText({ spent_usd: 0.5, limit_usd: 2 }), '$0.50 of $2.00');
});

test('limit parsing and text', () => {
  assert.equal(parseLimit(''), null);
  assert.equal(parseLimit(' 2.5 '), 2.5);
  assert.equal(parseLimit('$3'), 3);
  assert.equal(parseLimit('1.239'), 1.24);
  assert.equal(parseLimit('$'), 'invalid');
  assert.equal(parseLimit('-1'), 'invalid');
  assert.equal(parseLimit('abc'), 'invalid');
  assert.equal(parseLimit('2000000'), 'invalid');
  assert.equal(limitText(null), '');
  assert.equal(limitText(2), '2.00');
  assert.equal(inheritedText(null), 'No limit');
  assert.equal(inheritedText(5), 'Default $5.00');
});

test('labels, initials and statuses', () => {
  assert.equal(monthLabel('2026-10'), 'October 2026');
  assert.equal(monthLabel('bad'), 'bad');
  assert.equal(initials('Ada Lovelace'), 'AL');
  assert.equal(initials('cher'), 'C');
  assert.equal(initials('  '), '?');
  assert.equal(isOpenStatus('paused'), true);
  assert.equal(isOpenStatus('completed'), false);
});
