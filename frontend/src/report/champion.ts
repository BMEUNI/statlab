import { isJsonObject, runFileUrl } from '../api/client.ts';
import { candidateRows, type MetricValue } from '../run/compare.ts';
import type { RunView } from '../run/model.ts';

export type Champion = {
  candidateId: string;
  label: string;
  estimator: string | null;
  modelRef: string | null;
  testMetrics: Record<string, MetricValue>;
  primaryMetric: string | null;
  validation: MetricValue | null;
  priorScorings: number | null;
  waves: number | null;
  caveats: string[];
};

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function champion(view: RunView): Champion | null {
  const outcome = [...view.shared].reverse().find(
    (item) => typeof item.summary.candidate_id === 'string' && isJsonObject(item.summary.test_metrics),
  );
  if (outcome === undefined) return null;
  const { summary } = outcome;
  const candidateId = summary.candidate_id as string;
  const testMetrics: Record<string, MetricValue> = {};
  const raw = isJsonObject(summary.test_metrics) && isJsonObject(summary.test_metrics.metrics)
    ? summary.test_metrics.metrics
    : {};
  for (const [name, item] of Object.entries(raw)) {
    if (!isJsonObject(item)) continue;
    const value = finite(item.value);
    if (value !== null) testMetrics[name] = { value, ciLow: finite(item.ci_low), ciHigh: finite(item.ci_high) };
  }
  const row = candidateRows(view).find((item) => item.id === candidateId);
  const primaryMetric = row?.primaryMetric ?? null;
  return {
    candidateId,
    label: view.branches.find((item) => item.id === candidateId)?.label ?? candidateId,
    estimator: typeof summary.estimator === 'string' ? summary.estimator : null,
    modelRef: typeof summary.model === 'string' ? summary.model : null,
    testMetrics,
    primaryMetric,
    validation: primaryMetric === null ? null : row?.metrics[primaryMetric] ?? null,
    priorScorings: finite(summary.prior_test_scorings),
    waves: finite(summary.n_waves),
    caveats: [summary.selection_bias, summary.fit_timing].filter((item): item is string => typeof item === 'string'),
  };
}

export function plotImageUrl(runId: string): (src: string) => string | null {
  return (src) => {
    const match = /^plots\/(a_[0-9a-f]{16}\.png)$/.exec(src);
    return match === null ? null : runFileUrl(runId, `plots/${match[1]}`);
  };
}
