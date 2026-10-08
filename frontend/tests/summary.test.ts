import assert from 'node:assert/strict';
import { test } from 'node:test';
import { championPlots, confusionMatrix, stepLines, topFeatures, whyChampion } from '../src/chat/summary.ts';
import type { JsonObject } from '../src/api/types.ts';
import type { Branch, NodeOutcome, RunView } from '../src/run/model.ts';

function out(node: string, summary: JsonObject, status = 'completed', skipReason: string | null = null): NodeOutcome {
  return {
    node, executionId: `x-${node}`, status, summary, artifacts: [], metrics: {}, warnings: [], skipReason, error: null,
  };
}

const curves = {
  roc: { plot: 'a_1', summary: { auc: 0.75132 } },
  pr: { plot: 'a_2', summary: {} },
  calibration: { plot: 'a_3' },
  confusion: { summary: { labels: ['no', 'yes'], matrix: [[5, 1], [2, 7]] } },
};
const importance = {
  metric: 'roc_auc',
  top: [
    { feature: 'age', importance_mean: 0.3 },
    { feature: 'bad', importance_mean: 'x' },
    { feature: 'income', importance_mean: 0.2 },
    { feature: 'zip', importance_mean: 0.1 },
  ],
};

function branch(id: string, outcomes: NodeOutcome[]): Branch {
  return { id, label: id, estimator: null, status: 'completed', wave: 0, retuneCount: 0, outcomes, spec: {} };
}

const view: RunView = {
  spine: [],
  shared: [
    out('intake', { decision: { user_summary: 'Loaded 10 rows.' } }),
    out('experiment_design', { decision: { rationale: 'First plan.' } }),
    out('inferential', {}, 'skipped', 'not requested'),
    out('experiment_design', { decision: { rationale: 'Second plan. More.' } }),
    out('champion_selection', { decision: { rationale: 'Picked c_03. It ranked first. Ties broken. Extra.' } }),
    out('artifact_persistence', {}),
  ],
  branches: [
    branch('c_03', [
      out('model_evaluation', { curves }),
      out('model_interpretation', { diagnostics: { permutation_importance: importance } }),
    ]),
    branch('c_04', []),
  ],
  overlap: null,
  wave: 0,
  budget: null,
};

test('champion summary accessors', () => {
  assert.deepEqual(championPlots(view, 'c_03'), [
    { ref: 'a_1', caption: 'ROC curve (validation), AUC 0.751' },
    { ref: 'a_2', caption: 'Precision-recall curve (validation)' },
    { ref: 'a_3', caption: 'Calibration (validation)' },
  ]);
  assert.deepEqual(confusionMatrix(view, 'c_03'), { labels: ['no', 'yes'], matrix: [[5, 1], [2, 7]] });
  const features = topFeatures(view, 'c_03', 2);
  assert.equal(features?.metric, 'roc_auc');
  assert.deepEqual(features?.items, [{ feature: 'age', value: 0.3 }, { feature: 'income', value: 0.2 }]);
  assert.equal(whyChampion(view), 'Picked c_03. It ranked first.');
  assert.deepEqual(stepLines(view), [
    { node: 'intake', text: 'Loaded 10 rows.' },
    { node: 'experiment_design', text: 'Second plan. More.' },
    { node: 'inferential', text: 'Skipped: not requested' },
  ]);
});

test('missing curves give empty results', () => {
  assert.deepEqual(championPlots(view, 'c_04'), []);
  assert.equal(confusionMatrix(view, 'c_04'), null);
  assert.equal(topFeatures(view, 'c_04', 5), null);
});
