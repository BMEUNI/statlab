import { isJsonObject } from '../api/client.ts';
import type { RunSummary } from '../api/types.ts';

export type LineageRow = { run: RunSummary; depth: number; forkTarget: string | null; parentId: string | null };

export function forkTarget(run: RunSummary): string | null {
  try {
    const config: unknown = JSON.parse(run.config_json);
    if (!isJsonObject(config) || !isJsonObject(config.fork)) return null;
    return typeof config.fork.target === 'string' ? config.fork.target : null;
  } catch {
    return null;
  }
}

export function lineage(runs: RunSummary[]): LineageRow[] {
  const ids = new Set(runs.map((run) => run.id));
  const byStart = (a: RunSummary, b: RunSummary) => a.started_at.localeCompare(b.started_at);
  const roots = runs
    .filter((run) => run.forked_from_run_id === null || !ids.has(run.forked_from_run_id))
    .sort((a, b) => byStart(b, a));
  const rows: LineageRow[] = [];
  const visited = new Set<string>();
  const visit = (run: RunSummary, depth: number) => {
    if (visited.has(run.id)) return;
    visited.add(run.id);
    const parent = run.forked_from_run_id;
    rows.push({ run, depth, forkTarget: forkTarget(run), parentId: parent !== null && ids.has(parent) ? parent : null });
    runs.filter((child) => child.forked_from_run_id === run.id).sort(byStart).forEach((child) => visit(child, depth + 1));
  };
  roots.forEach((root) => visit(root, 0));
  return rows;
}
