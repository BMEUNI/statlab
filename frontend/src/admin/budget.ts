import type { Meter, RunStatus } from '../api/types.ts';

export function formatUsd(value: number): string {
  if (value > 0 && value < 0.005) return '<$0.01';
  return '$' + value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export type MeterTone = 'none' | 'ok' | 'near' | 'over';

export function meterTone(meter: Meter): MeterTone {
  if (meter.limit_usd === null) return 'none';
  if (meter.spent_usd >= meter.limit_usd) return 'over';
  if (meter.limit_usd > 0 && meter.spent_usd / meter.limit_usd >= 0.8) return 'near';
  return 'ok';
}

export function meterFill(meter: Meter): number {
  if (meter.limit_usd === null) return 0;
  if (meter.limit_usd === 0) return 1;
  return Math.min(1, meter.spent_usd / meter.limit_usd);
}

export function meterText(meter: Meter): string {
  if (meter.limit_usd === null) return `${formatUsd(meter.spent_usd)} spent, no limit`;
  return `${formatUsd(meter.spent_usd)} of ${formatUsd(meter.limit_usd)}`;
}

export function parseLimit(text: string): number | null | 'invalid' {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const rest = trimmed.startsWith('$') ? trimmed.slice(1) : trimmed;
  const value = Number(rest);
  if (rest === '' || !Number.isFinite(value) || value < 0 || value > 1_000_000) return 'invalid';
  return Math.round(value * 100) / 100;
}

export function limitText(value: number | null): string {
  return value === null ? '' : value.toFixed(2);
}

export function inheritedText(value: number | null): string {
  return value === null ? 'No limit' : `Default ${formatUsd(value)}`;
}

export function monthLabel(month: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(month);
  if (match === null) return month;
  const year = Number(match[1]);
  const monthIndex = Number(match[2]) - 1;
  return new Date(Date.UTC(year, monthIndex, 1)).toLocaleString('en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter((word) => word !== '').slice(0, 2);
  const letters = words.map((word) => word.charAt(0).toUpperCase()).join('');
  return letters === '' ? '?' : letters;
}

export function isOpenStatus(status: RunStatus): boolean {
  return status === 'pending' || status === 'running' || status === 'awaiting_user' || status === 'paused';
}
