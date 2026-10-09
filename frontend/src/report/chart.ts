import { isJsonObject } from '../api/client.ts';

export type XYSeries = { name: string; x: (number | null)[]; y: (number | null)[] };
export type BarSeries = { name: string; values: (number | null)[] };
export type BoxGroup = {
  name: string;
  n: number;
  low: number;
  q1: number;
  median: number;
  q3: number;
  high: number;
};
type XYFields = {
  title: string | null;
  xLabel: string;
  yLabel: string;
  series: XYSeries[];
  hline: number | null;
};
export type ChartPanel =
  | (XYFields & { kind: 'line' })
  | (XYFields & { kind: 'scatter' })
  | {
      kind: 'bar';
      title: string | null;
      xLabel: string;
      yLabel: string;
      categories: string[];
      series: BarSeries[];
    }
  | {
      kind: 'box';
      title: string | null;
      xLabel: string;
      yLabel: string;
      groups: BoxGroup[];
    };
export type ChartSpec = { title: string; note: string | null; panels: ChartPanel[] };

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function nullableNumber(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value);
}

function list(value: unknown): unknown[] | null {
  if (!Array.isArray(value)) return null;
  const items: unknown[] = value;
  return items;
}

function nullableNumbers(value: unknown): (number | null)[] | null {
  const items = list(value);
  if (items === null) return null;
  return items.every(nullableNumber) ? items : null;
}

function stringArray(value: unknown): string[] | null {
  const items = list(value);
  if (items === null) return null;
  return items.every((item): item is string => typeof item === 'string') ? items : null;
}

function parseAll<T>(value: unknown, parse: (item: unknown) => T | null): T[] | null {
  const items = list(value);
  if (items === null) return null;
  const out: T[] = [];
  for (const item of items) {
    const parsed = parse(item);
    if (parsed === null) return null;
    out.push(parsed);
  }
  return out;
}

function parseXY(value: unknown): XYSeries | null {
  if (!isJsonObject(value)) return null;
  const name = value.name;
  const x = nullableNumbers(value.x);
  const y = nullableNumbers(value.y);
  if (typeof name !== 'string' || x === null || y === null || x.length !== y.length) return null;
  return { name, x, y };
}

function parseBar(value: unknown, count: number): BarSeries | null {
  if (!isJsonObject(value)) return null;
  const name = value.name;
  const values = nullableNumbers(value.values);
  if (typeof name !== 'string' || values === null || values.length !== count) return null;
  return { name, values };
}

function parseBoxGroup(value: unknown): BoxGroup | null {
  if (!isJsonObject(value)) return null;
  const { name, n, low, q1, median, q3, high } = value;
  if (
    typeof name !== 'string' ||
    !isFiniteNumber(n) ||
    !isFiniteNumber(low) ||
    !isFiniteNumber(q1) ||
    !isFiniteNumber(median) ||
    !isFiniteNumber(q3) ||
    !isFiniteNumber(high)
  ) {
    return null;
  }
  return { name, n, low, q1, median, q3, high };
}

function parsePanel(value: unknown): ChartPanel | null {
  if (!isJsonObject(value)) return null;
  const { kind, title, x_label: xLabel, y_label: yLabel } = value;
  if (typeof xLabel !== 'string' || typeof yLabel !== 'string') return null;
  if (title !== null && typeof title !== 'string') return null;
  if (kind === 'line' || kind === 'scatter') {
    const series = parseAll(value.series, parseXY);
    if (series === null || !nullableNumber(value.hline)) return null;
    return { kind, title, xLabel, yLabel, series, hline: value.hline };
  }
  if (kind === 'bar') {
    const categories = stringArray(value.categories);
    if (categories === null) return null;
    const series = parseAll(value.series, (item) => parseBar(item, categories.length));
    if (series === null) return null;
    return { kind, title, xLabel, yLabel, categories, series };
  }
  if (kind === 'box') {
    const groups = parseAll(value.groups, parseBoxGroup);
    if (groups === null) return null;
    return { kind, title, xLabel, yLabel, groups };
  }
  return null;
}

export function parseChart(value: unknown): ChartSpec | null {
  if (!isJsonObject(value) || value.version !== 1) return null;
  const { title, note } = value;
  if (typeof title !== 'string') return null;
  if (note !== null && typeof note !== 'string') return null;
  const panels = parseAll(value.panels, parsePanel);
  if (panels === null || panels.length === 0) return null;
  return { title, note, panels };
}

export function niceStep(raw: number): number {
  const exp = 10 ** Math.floor(Math.log10(raw));
  const f = raw / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nice * exp;
}

export function niceScale(values: number[], includeZero: boolean): { min: number; max: number; ticks: number[] } {
  const finite = values.filter((v) => Number.isFinite(v));
  let lo = finite.length > 0 ? Math.min(...finite) : 0;
  let hi = finite.length > 0 ? Math.max(...finite) : 1;
  if (includeZero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const step = niceStep((hi - lo) / 5);
  const round = (v: number) => Number(v.toPrecision(12));
  const min = round(Math.floor(lo / step) * step);
  const max = round(Math.ceil(hi / step) * step);
  const ticks = Array.from(
    { length: Math.round((max - min) / step) + 1 },
    (_, i) => round(min + i * step),
  );
  return { min, max, ticks };
}

export function formatNumber(v: number): string {
  if (v === 0) return '0';
  if (Math.abs(v) >= 1e5 || Math.abs(v) < 1e-3) return v.toExponential(2);
  return String(Number(v.toPrecision(4)));
}

export function chartRef(src: string): string | null {
  return /^plots\/(a_[0-9a-f]{16})\.png$/.exec(src)?.[1] ?? null;
}
