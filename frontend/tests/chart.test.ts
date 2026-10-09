import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chartRef, formatNumber, niceScale, parseChart } from '../src/report/chart.ts';

test('niceScale picks round ticks around the data', () => {
  assert.deepEqual(niceScale([0.03, 0.97], false), { min: 0, max: 1, ticks: [0, 0.2, 0.4, 0.6, 0.8, 1] });
  assert.deepEqual(niceScale([5, 5], true), { min: 0, max: 5, ticks: [0, 1, 2, 3, 4, 5] });
  assert.deepEqual(niceScale([], false), { min: 0, max: 1, ticks: [0, 0.2, 0.4, 0.6, 0.8, 1] });
});

test('formatNumber shortens values for labels', () => {
  assert.equal(formatNumber(0), '0');
  assert.equal(formatNumber(1234.5678), '1235');
  assert.equal(formatNumber(0.000123), '1.23e-4');
  assert.equal(formatNumber(123456), '1.23e+5');
  assert.equal(formatNumber(0.5), '0.5');
});

test('chartRef accepts only report plot names', () => {
  assert.equal(chartRef('plots/a_0123456789abcdef.png'), 'a_0123456789abcdef');
  assert.equal(chartRef('x.png'), null);
});

test('parseChart returns a valid line chart and rejects malformed specs', () => {
  const valid = {
    version: 1,
    title: 't',
    note: null,
    panels: [
      {
        kind: 'line',
        title: null,
        x_label: 'a',
        y_label: 'b',
        categories: [],
        series: [{ name: 's', x: [0, 1], y: [1, null] }],
        groups: [],
        hline: null,
      },
    ],
  };
  assert.deepEqual(parseChart(valid), {
    title: 't',
    note: null,
    panels: [
      {
        kind: 'line',
        title: null,
        xLabel: 'a',
        yLabel: 'b',
        series: [{ name: 's', x: [0, 1], y: [1, null] }],
        hline: null,
      },
    ],
  });
  assert.equal(parseChart({ ...valid, version: 2 }), null);
  const panel = valid.panels[0];
  assert.equal(parseChart({ ...valid, panels: [{ ...panel, series: [{ name: 's', x: [0, 1], y: [1] }] }] }), null);
  assert.equal(parseChart({ ...valid, panels: [{ ...panel, kind: 'pie' }] }), null);
  assert.equal(
    parseChart({
      ...valid,
      panels: [
        {
          kind: 'bar',
          title: null,
          x_label: 'a',
          y_label: 'b',
          categories: ['p', 'q'],
          series: [{ name: 's', values: [1] }],
          groups: [],
          hline: null,
        },
      ],
    }),
    null,
  );
});
