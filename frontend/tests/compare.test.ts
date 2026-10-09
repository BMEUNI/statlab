import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ArtifactEntry, JsonObject, ValidationPage } from '../src/api/types.ts';
import {
  artifactKinds,
  artifactSplits,
  candidateRows,
  defaultSort,
  filterArtifacts,
  formatMetric,
  metricColumns,
  sortRows,
  cellText,
  validationModels,
  validationSummary,
} from '../src/run/compare.ts';
import { humanize, type Branch, type NodeOutcome, type RunView } from '../src/run/model.ts';

function outcome(node: string, summary: JsonObject): NodeOutcome {
  return { node, executionId: 'e', status: 'completed', summary, artifacts: [], metrics: {}, warnings: [], skipReason: null, error: null };
}

function branch(id: string, spec: JsonObject, outcomes: NodeOutcome[]): Branch {
  return { id, label: `Label ${id}`, estimator: 'logreg', status: 'completed', wave: 0, retuneCount: 0, outcomes, spec };
}

const view: RunView = {
  spine: [],
  shared: [outcome('champion_selection', {
    candidate_id: 'c_01',
    ranking: { ranking: [
      { rank: 1, candidate: 'c_01', eligible: true, ineligible_reason: null },
      { rank: 2, candidate: 'c_02', eligible: false, ineligible_reason: 'r' },
    ] },
  })],
  branches: [
    branch('c_01', { is_baseline: true, rationale: 'base' }, [outcome('model_evaluation', {
      metrics: { roc_auc: { value: 0.81, ci_low: 0.7, ci_high: 0.9 }, accuracy: { value: 0.7 } },
      primary_metric: 'roc_auc', greater_is_better: true,
    })]),
    branch('c_02', { varies_from_baseline: ['estimator', 3] }, [outcome('model_evaluation', {
      metrics: { roc_auc: { value: 0.75, ci_low: null, ci_high: null } }, primary_metric: 'roc_auc', greater_is_better: true,
    })]),
    branch('c_03', {}, [outcome('model_evaluation', { metrics: {} })]),
  ],
  overlap: null,
  wave: 0,
  budget: null,
};

test('candidateRows builds rows with ranking and champion', () => {
  const rows = candidateRows(view);
  assert.deepEqual(rows[0], {
    id: 'c_01', label: 'Label c_01', estimator: 'logreg', isBaseline: true, varies: [], rationale: 'base',
    status: 'completed', primaryMetric: 'roc_auc', greaterIsBetter: true,
    metrics: { roc_auc: { value: 0.81, ciLow: 0.7, ciHigh: 0.9 }, accuracy: { value: 0.7, ciLow: null, ciHigh: null } },
    rank: 1, eligible: true, ineligibleReason: null, champion: true,
  });
  assert.deepEqual(rows[1], {
    id: 'c_02', label: 'Label c_02', estimator: 'logreg', isBaseline: false, varies: ['estimator'], rationale: null,
    status: 'completed', primaryMetric: 'roc_auc', greaterIsBetter: true,
    metrics: { roc_auc: { value: 0.75, ciLow: null, ciHigh: null } },
    rank: 2, eligible: false, ineligibleReason: 'r', champion: false,
  });
  assert.deepEqual(rows[2], {
    id: 'c_03', label: 'Label c_03', estimator: 'logreg', isBaseline: false, varies: [], rationale: null,
    status: 'completed', primaryMetric: null, greaterIsBetter: null, metrics: {},
    rank: null, eligible: null, ineligibleReason: null, champion: false,
  });
});

test('metricColumns, defaultSort and sortRows', () => {
  const rows = candidateRows(view);
  assert.deepEqual(metricColumns(rows), ['roc_auc', 'accuracy']);
  assert.deepEqual(defaultSort(rows), { metric: 'roc_auc', direction: 'desc' });
  assert.deepEqual(sortRows(rows, { metric: 'roc_auc', direction: 'desc' }).map((row) => row.id), ['c_01', 'c_02', 'c_03']);
  assert.deepEqual(sortRows(rows, { metric: 'roc_auc', direction: 'asc' }).map((row) => row.id), ['c_02', 'c_01', 'c_03']);
  assert.deepEqual(sortRows([...rows].reverse(), { metric: null, direction: 'asc' }).map((row) => row.id), ['c_01', 'c_02', 'c_03']);
  const lower = rows.map((row) => ({ ...row, greaterIsBetter: false }));
  assert.deepEqual(defaultSort(lower), { metric: 'roc_auc', direction: 'asc' });
});

test('formatMetric prints the interval only when both bounds exist', () => {
  assert.equal(formatMetric({ value: 0.81, ciLow: 0.7, ciHigh: 0.9 }), '0.8100 (0.7000 to 0.9000)');
  assert.equal(formatMetric({ value: 0.81, ciLow: null, ciHigh: 0.9 }), '0.8100');
});

test('artifact kind and split helpers filter entries', () => {
  const make = (id: string, kind: string, split: string | null): ArtifactEntry => ({
    id, kind, label: null, split, rows: null, cols: null, producedBy: 'n', ts: 't', pruned: false, locked: false,
  });
  const entries = [make('a', 'plot', null), make('b', 'dataframe', 'train'), make('c', 'dataframe', 'test')];
  assert.deepEqual(artifactKinds(entries), ['dataframe', 'plot']);
  assert.deepEqual(artifactSplits(entries), ['test', 'train']);
  assert.deepEqual(filterArtifacts(entries, 'dataframe', 'train').map((entry) => entry.id), ['b']);
  assert.deepEqual(filterArtifacts(entries, '', '').length, 3);
});

test('validationModels keeps candidates with a completed model_evaluation and marks the champion', () => {
  const failed = { ...branch('c_03', {}, [{ ...outcome('model_evaluation', {}), status: 'failed' }]) };
  const local: RunView = { ...view, branches: [view.branches[0] as Branch, view.branches[1] as Branch, failed] };
  assert.deepEqual(validationModels(local), [
    { id: 'c_01', label: humanize('Label c_01'), champion: true },
    { id: 'c_02', label: humanize('Label c_02'), champion: false },
  ]);
});

test('cellText renders nulls, numbers, booleans, strings and other JSON', () => {
  assert.equal(cellText(null), '—');
  assert.equal(cellText(3), '3');
  assert.equal(cellText(0.123456), '0.1235');
  assert.equal(cellText(true), 'yes');
  assert.equal(cellText('x'), 'x');
  assert.equal(cellText([1]), '[1]');
});

test('validationSummary describes classification and regression pages', () => {
  const page: ValidationPage = {
    task: 'classification', total: 4, shownTotal: 4, offset: 0, limit: 50, correct: 3,
    meanAbsError: null, threshold: 0.4, columns: [], rows: [],
  };
  assert.equal(validationSummary(page), '3 of 4 validation rows predicted correctly (75.0%). Classes use the tuned threshold 0.4.');
  const regression: ValidationPage = { ...page, task: 'regression', total: 2, correct: null, meanAbsError: 1.5, threshold: null };
  assert.equal(validationSummary(regression), 'Mean absolute error 1.5 over 2 validation rows.');
  assert.equal(validationSummary({ ...regression, total: 0 }), 'No validation rows.');
});
