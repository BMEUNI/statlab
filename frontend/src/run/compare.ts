import { isJsonObject } from '../api/client.ts';
import type { ArtifactEntry, JsonObject } from '../api/types.ts';
import type { NodeOutcome, RunView } from './model.ts';

export type MetricValue = { value: number; ciLow: number | null; ciHigh: number | null };
export type CandidateRow = {
  id: string;
  label: string;
  estimator: string | null;
  isBaseline: boolean;
  varies: string[];
  rationale: string | null;
  status: string;
  primaryMetric: string | null;
  greaterIsBetter: boolean | null;
  metrics: Record<string, MetricValue>;
  rank: number | null;
  eligible: boolean | null;
  ineligibleReason: string | null;
  champion: boolean;
};
export type SortKey = { metric: string | null; direction: 'asc' | 'desc' };

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function metricsOf(outcome: NodeOutcome): Record<string, MetricValue> {
  const result: Record<string, MetricValue> = {};
  const metrics = outcome.summary.metrics;
  if (!isJsonObject(metrics)) return result;
  for (const [name, item] of Object.entries(metrics)) {
    if (!isJsonObject(item)) continue;
    const value = finite(item.value);
    if (value === null) continue;
    result[name] = { value, ciLow: finite(item.ci_low), ciHigh: finite(item.ci_high) };
  }
  return result;
}

export function scored(outcomes: NodeOutcome[]): NodeOutcome | null {
  for (let index = outcomes.length - 1; index >= 0; index -= 1) {
    const outcome = outcomes[index];
    if (outcome !== undefined && Object.keys(metricsOf(outcome)).length > 0) return outcome;
  }
  return null;
}

function rankingOf(outcome: NodeOutcome): JsonObject[] | null {
  const ranking = outcome.summary.ranking;
  if (!isJsonObject(ranking) || !Array.isArray(ranking.ranking)) return null;
  return ranking.ranking.filter(isJsonObject);
}

export function candidateRows(view: RunView): CandidateRow[] {
  const championOutcome = [...view.shared].reverse().find((item) => rankingOf(item) !== null);
  const ranking = championOutcome === undefined ? [] : rankingOf(championOutcome) ?? [];
  return view.branches.map((branch) => {
    const { spec } = branch;
    const evaluation = scored(branch.outcomes);
    const entry = ranking.find((item) => item.candidate === branch.id);
    const varies = Array.isArray(spec.varies_from_baseline)
      ? spec.varies_from_baseline.filter((item): item is string => typeof item === 'string')
      : [];
    const summary = evaluation?.summary;
    return {
      id: branch.id,
      label: branch.label,
      estimator: branch.estimator,
      isBaseline: spec.is_baseline === true,
      varies,
      rationale: typeof spec.rationale === 'string' ? spec.rationale : null,
      status: branch.status,
      primaryMetric: typeof summary?.primary_metric === 'string' ? summary.primary_metric : null,
      greaterIsBetter: typeof summary?.greater_is_better === 'boolean' ? summary.greater_is_better : null,
      metrics: evaluation === null ? {} : metricsOf(evaluation),
      rank: finite(entry?.rank),
      eligible: typeof entry?.eligible === 'boolean' ? entry.eligible : null,
      ineligibleReason: typeof entry?.ineligible_reason === 'string' ? entry.ineligible_reason : null,
      champion: championOutcome?.summary.candidate_id === branch.id,
    };
  });
}

export function metricColumns(rows: CandidateRow[]): string[] {
  const names = new Set(rows.flatMap((row) => Object.keys(row.metrics)));
  const primary = rows.find((row) => row.primaryMetric !== null)?.primaryMetric ?? null;
  const rest = [...names].filter((name) => name !== primary).sort();
  return primary !== null && names.has(primary) ? [primary, ...rest] : rest;
}

export function defaultSort(rows: CandidateRow[]): SortKey {
  const first = rows.find((row) => row.primaryMetric !== null);
  return { metric: first?.primaryMetric ?? null, direction: first?.greaterIsBetter === false ? 'asc' : 'desc' };
}

export function sortRows(rows: CandidateRow[], key: SortKey): CandidateRow[] {
  const byId = (a: CandidateRow, b: CandidateRow) => a.id.localeCompare(b.id);
  const { metric, direction } = key;
  return [...rows].sort((a, b) => {
    if (metric === null) {
      if (a.rank === null && b.rank === null) return byId(a, b);
      if (a.rank === null) return 1;
      if (b.rank === null) return -1;
      return a.rank - b.rank || byId(a, b);
    }
    const left = a.metrics[metric]?.value;
    const right = b.metrics[metric]?.value;
    if (left === undefined && right === undefined) return byId(a, b);
    if (left === undefined) return 1;
    if (right === undefined) return -1;
    return (direction === 'asc' ? left - right : right - left) || byId(a, b);
  });
}

export function formatMetric(metric: MetricValue): string {
  const base = metric.value.toFixed(4);
  return metric.ciLow !== null && metric.ciHigh !== null
    ? `${base} (${metric.ciLow.toFixed(4)} to ${metric.ciHigh.toFixed(4)})`
    : base;
}

function distinct(values: (string | null)[]): string[] {
  return [...new Set(values.filter((item): item is string => item !== null))].sort();
}

export function artifactKinds(entries: ArtifactEntry[]): string[] {
  return distinct(entries.map((entry) => entry.kind));
}

export function artifactSplits(entries: ArtifactEntry[]): string[] {
  return distinct(entries.map((entry) => entry.split));
}

export function filterArtifacts(entries: ArtifactEntry[], kind: string, split: string): ArtifactEntry[] {
  return entries.filter((entry) => (kind === '' || entry.kind === kind) && (split === '' || entry.split === split));
}
