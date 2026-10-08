import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { JsonObject, RunSummary } from '../src/api/types.ts';
import { forkTarget, lineage } from '../src/project/lineage.ts';
import { champion, plotImageUrl } from '../src/report/champion.ts';
import type { Branch, NodeOutcome, RunView } from '../src/run/model.ts';

function outcome(node: string, summary: JsonObject): NodeOutcome {
  return { node, executionId: 'e', status: 'completed', summary, artifacts: [], metrics: {}, warnings: [], skipReason: null, error: null };
}

const branch: Branch = {
  id: 'c_01', label: 'Baseline', estimator: 'logreg', status: 'completed', wave: 0, retuneCount: 0, spec: {},
  outcomes: [outcome('model_evaluation', {
    metrics: { roc_auc: { value: 0.81, ci_low: 0.7, ci_high: 0.9 } }, primary_metric: 'roc_auc', greater_is_better: true,
  })],
};

function viewWith(shared: NodeOutcome[]): RunView {
  return { spine: [], shared, branches: [branch], overlap: null, wave: 0, budget: null };
}

test('champion extracts the card data', () => {
  const view = viewWith([outcome('champion_selection', {
    candidate_id: 'c_01', estimator: 'logreg', model: 'art1',
    test_metrics: { task: 'binary', metrics: { roc_auc: { value: 0.8, ci_low: 0.7, ci_high: 0.9 }, accuracy: { value: 0.7 } } },
    prior_test_scorings: 0, n_waves: 1, selection_bias: 'bias', fit_timing: 'timing',
  })]);
  assert.deepEqual(champion(view), {
    candidateId: 'c_01', label: 'Baseline', estimator: 'logreg', modelRef: 'art1',
    testMetrics: {
      roc_auc: { value: 0.8, ciLow: 0.7, ciHigh: 0.9 },
      accuracy: { value: 0.7, ciLow: null, ciHigh: null },
    },
    primaryMetric: 'roc_auc', validation: { value: 0.81, ciLow: 0.7, ciHigh: 0.9 },
    priorScorings: 0, waves: 1, caveats: ['bias', 'timing'],
  });
  assert.equal(champion(viewWith([])), null);
});

test('plotImageUrl maps only plot artifacts', () => {
  const url = plotImageUrl('r1');
  assert.equal(url('plots/a_0123456789abcdef.png'), '/api/runs/r1/files/plots/a_0123456789abcdef.png');
  assert.equal(url('plots/../x.png'), null);
  assert.equal(url('https://x/y.png'), null);
});

function run(id: string, startedAt: string, parent: string | null, config = '{}'): RunSummary {
  return {
    id, project_id: 'p', started_at: `2026-01-01T0${startedAt}:00Z`, mode: 'guided', status: 'completed', current_node: null,
    forked_from_run_id: parent, ended_at: null, contract_id: 'c', champion_model_id: null, config_json: config,
    test_scored_at: null, test_fingerprint: null,
  };
}

test('lineage orders forks under parents', () => {
  const runs = [
    run('A', '1', null), run('A2', '2', 'A'),
    run('A1', '3', 'A', '{"fork": {"target": "problem_definition"}}'),
    run('A1a', '4', 'A1'), run('O', '5', 'gone'), run('B', '6', null),
  ];
  const rows = lineage(runs);
  assert.deepEqual(rows.map((row) => row.run.id), ['B', 'O', 'A', 'A2', 'A1', 'A1a']);
  assert.deepEqual(rows.map((row) => row.depth), [0, 0, 0, 1, 1, 2]);
  assert.deepEqual(rows.map((row) => row.parentId), [null, null, null, 'A', 'A', 'A1']);
  assert.equal(rows[4]?.forkTarget, 'problem_definition');
  assert.equal(forkTarget(run('X', '1', null, '{')), null);
});
