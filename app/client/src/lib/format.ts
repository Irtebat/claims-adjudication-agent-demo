/**
 * Presentation helpers. All numeric formatters coerce first, because Lakebase numeric
 * columns arrive over node-postgres as strings. Everything degrades to a dash ("—")
 * rather than rendering "NaN", "null", or "[object Object]".
 */

import type { Num, Str, Verdict } from './types';

export const DASH = '—';

/** Coerce a pg numeric/number/string to a finite number, or null. */
export function toNum(v: Num | undefined): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = typeof v === 'number' ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** USD money, no cents (claims settle in whole-dollar amounts). */
export function money(v: Num | undefined): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

/** A percentage from a 0..1 fraction (confidence, proration, similarity). */
export function pct(v: Num | undefined, digits = 0): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return `${(n * 100).toFixed(digits)}%`;
}

export function num(v: Num | undefined, digits = 1): string {
  const n = toNum(v);
  if (n === null) return DASH;
  return n.toLocaleString('en-US', { maximumFractionDigits: digits });
}

/** Short date (e.g. "12 Mar 2026"), or a dash. */
export function shortDate(v: Str | undefined): string {
  if (!v) return DASH;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleDateString('en-US', { day: '2-digit', month: 'short', year: 'numeric' });
}

/** Compact relative age from a timestamp ("3d", "5h", "12m", "now"). */
export function age(v: Str | undefined): string {
  if (!v) return DASH;
  const then = new Date(v).getTime();
  if (Number.isNaN(then)) return DASH;
  const secs = Math.max(0, (Date.now() - then) / 1000);
  if (secs < 60) return 'now';
  const mins = secs / 60;
  if (mins < 60) return `${Math.floor(mins)}m`;
  const hrs = mins / 60;
  if (hrs < 24) return `${Math.floor(hrs)}h`;
  return `${Math.floor(hrs / 24)}d`;
}

/** Age in whole hours, for sorting the queue by how long a claim has waited. */
export function ageHours(v: Str | undefined): number {
  if (!v) return -1;
  const then = new Date(v).getTime();
  if (Number.isNaN(then)) return -1;
  return (Date.now() - then) / 3_600_000;
}

const VERDICT_LABELS: Record<string, string> = {
  APPROVE: 'Approve',
  DENY: 'Deny',
  PEND_INVESTIGATE: 'Investigate',
  PEND: 'Investigate',
};

export function verdictLabel(v: Str | undefined): string {
  if (!v) return DASH;
  return VERDICT_LABELS[v] ?? titleCase(v);
}

const DISPOSITION_LABELS: Record<string, string> = {
  CREDIT: 'Credit',
  REPLACEMENT: 'Replacement',
  REWORK: 'Rework',
  DENY: 'Deny',
  DUPLICATE: 'Duplicate',
  PEND_INVESTIGATE: 'Investigate',
};

export function dispositionLabel(v: Str | undefined): string {
  if (!v) return DASH;
  return DISPOSITION_LABELS[v] ?? titleCase(v);
}

export function titleCase(v: string): string {
  return v
    .toLowerCase()
    .split(/[_\s]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/** Humanize a snake_case object key for a key/value context list. */
export function humanizeKey(k: string): string {
  return titleCase(k.replace(/_/g, ' '));
}

/** Render an unknown jsonb scalar for a compact key/value list. */
export function scalar(v: unknown): string {
  if (v === null || v === undefined || v === '') return DASH;
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return v.toLocaleString('en-US', { maximumFractionDigits: 3 });
  if (typeof v === 'string') return v;
  if (Array.isArray(v)) return v.length ? v.map(scalar).join(', ') : DASH;
  return JSON.stringify(v);
}

/** The set of dispositions valid for each verdict (mirrors server/finalize.ts). */
export const DISPOSITIONS_FOR: Record<Verdict, string[]> = {
  APPROVE: ['CREDIT', 'REPLACEMENT', 'REWORK'],
  DENY: ['DENY', 'DUPLICATE'],
  PEND_INVESTIGATE: ['PEND_INVESTIGATE'],
};

/**
 * Safe accessor for DISPOSITIONS_FOR: the dispositions valid for a verdict, or an EMPTY
 * array for any value that is not one of the three UI verdicts — the operational 'PEND',
 * a null, or any unrecognized string. Callers do `dispositionsFor(v).includes(…)` and map
 * over the result, so returning `[]` (never `undefined`) means an unexpected verdict can
 * never crash the cockpit with `Cannot read properties of undefined (reading 'includes')`.
 */
export function dispositionsFor(v: Str | undefined): string[] {
  return (v != null && DISPOSITIONS_FOR[v as Verdict]) || [];
}

/**
 * Reconcile a persisted verdict to the UI Verdict vocabulary. `adjudications.recommended_verdict`
 * (and `.verdict`) store the OPERATIONAL verdict 'APPROVE' | 'DENY' | 'PEND', where 'PEND' is how
 * the agent's 'PEND_INVESTIGATE' hold is recorded (agent/src/writer.py maps it via
 * adjudication_verdict). The decision form and DISPOSITIONS_FOR speak the agent/UI vocabulary, so
 * collapse the operational 'PEND' back to 'PEND_INVESTIGATE', pass APPROVE/DENY through, and fall
 * back to the safe, defined 'APPROVE' for anything unrecognized or null (its disposition set is
 * non-empty). The submit flow then sends 'PEND_INVESTIGATE', which server/finalize.ts accepts and
 * re-maps to the operational 'PEND'.
 */
export function uiVerdict(v: Str | undefined): Verdict {
  if (v === 'PEND' || v === 'PEND_INVESTIGATE') return 'PEND_INVESTIGATE';
  if (v === 'DENY') return 'DENY';
  return 'APPROVE';
}
