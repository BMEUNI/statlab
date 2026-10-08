import { ApiError } from '../api/client.ts';
import type { RunSummary } from '../api/types.ts';
import type { Path } from '../contract/draft.ts';

export function slugify(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
  return slug.replace(/-+$/, '');
}

export function isValidSlug(slug: string): boolean {
  return /^[a-z0-9][a-z0-9_-]{0,63}$/.test(slug);
}

export function formatTime(iso: string | null): string {
  if (iso === null) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function lastActivity(createdAt: string, runs: RunSummary[]): string {
  let latest = createdAt;
  for (const run of runs) {
    if (run.started_at > latest) latest = run.started_at;
    if (run.ended_at !== null && run.ended_at > latest) latest = run.ended_at;
  }
  return latest;
}

export function naValuesToText(values: string[]): string {
  return values.map((value) => (value === '' ? '(empty)' : value)).join('\n');
}

export function naValuesFromText(text: string): string[] {
  const result: string[] = [];
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (trimmed === '') continue;
    const value = trimmed === '(empty)' ? '' : trimmed;
    if (!result.includes(value)) result.push(value);
  }
  return result;
}

export function errorText(error: Error): string {
  return error instanceof ApiError ? error.message : 'Request failed.';
}

export function pathKey(path: Path): string {
  return path.filter((segment): segment is string => typeof segment === 'string').join('.');
}
