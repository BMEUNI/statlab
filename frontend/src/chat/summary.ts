import { isJsonObject } from '../api/client.ts';
import type { JsonObject } from '../api/types.ts';
import { latestOutcome, narrateOutcome, type RunView } from '../run/model.ts';

export type Plot = { ref: string; caption: string };

const PLOT_CAPTIONS: [string, string][] = [
  ['roc', 'ROC curve (validation)'],
  ['pr', 'Precision-recall curve (validation)'],
  ['calibration', 'Calibration (validation)'],
];

function curvesOf(view: RunView, candidateId: string): JsonObject {
  const curves = latestOutcome(view, 'model_evaluation', candidateId)?.summary.curves;
  return isJsonObject(curves) ? curves : {};
}

export function championPlots(view: RunView, candidateId: string): Plot[] {
  const curves = curvesOf(view, candidateId);
  const plots: Plot[] = [];
  for (const [key, caption] of PLOT_CAPTIONS) {
    const entry = curves[key];
    if (!isJsonObject(entry) || typeof entry.plot !== 'string') continue;
    const auc = isJsonObject(entry.summary) ? entry.summary.auc : undefined;
    const withAuc = (key === 'roc' || key === 'pr') && typeof auc === 'number' && Number.isFinite(auc);
    plots.push({ ref: entry.plot, caption: withAuc ? `${caption}, AUC ${auc.toFixed(3)}` : caption });
  }
  return plots;
}

export function confusionMatrix(
  view: RunView,
  candidateId: string,
): { labels: string[]; matrix: number[][] } | null {
  const confusion = curvesOf(view, candidateId).confusion;
  const summary = isJsonObject(confusion) ? confusion.summary : undefined;
  if (!isJsonObject(summary)) return null;
  const { labels, matrix } = summary;
  if (!Array.isArray(labels) || !labels.every((item): item is string => typeof item === 'string')) return null;
  if (!Array.isArray(matrix) || matrix.length !== labels.length) return null;
  const rows: number[][] = [];
  for (const row of matrix) {
    if (!Array.isArray(row) || row.length !== labels.length) return null;
    if (!row.every((item): item is number => typeof item === 'number')) return null;
    rows.push(row);
  }
  return { labels, matrix: rows };
}

export function topFeatures(
  view: RunView,
  candidateId: string,
  n: number,
): { metric: string; items: { feature: string; value: number }[] } | null {
  const diagnostics = latestOutcome(view, 'model_interpretation', candidateId)?.summary.diagnostics;
  const importance = isJsonObject(diagnostics) ? diagnostics.permutation_importance : undefined;
  if (!isJsonObject(importance) || typeof importance.metric !== 'string' || !Array.isArray(importance.top)) return null;
  const items: { feature: string; value: number }[] = [];
  for (const entry of importance.top) {
    if (items.length >= n) break;
    if (
      isJsonObject(entry) && typeof entry.feature === 'string' &&
      typeof entry.importance_mean === 'number' && Number.isFinite(entry.importance_mean)
    ) items.push({ feature: entry.feature, value: entry.importance_mean });
  }
  return items.length === 0 ? null : { metric: importance.metric, items };
}

export function whyChampion(view: RunView): string | null {
  const outcome = view.shared.filter((item) => item.node === 'champion_selection').at(-1);
  if (outcome === undefined) return null;
  const text = narrateOutcome(outcome);
  return text === 'Done.' ? null : text;
}

export function stepLines(view: RunView): { node: string; text: string }[] {
  const lines = new Map<string, string>();
  for (const outcome of view.shared) {
    if (outcome.node === 'champion_selection' || outcome.node === 'artifact_persistence') continue;
    lines.set(outcome.node, narrateOutcome(outcome));
  }
  return [...lines].map(([node, text]) => ({ node, text }));
}
