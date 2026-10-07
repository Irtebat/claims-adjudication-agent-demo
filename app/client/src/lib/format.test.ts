import { describe, it, expect } from 'vitest';
import { dispositionsFor, uiVerdict, DISPOSITIONS_FOR } from './format';

/**
 * Bug 1 — the cockpit crashed on investigate claims with
 * `TypeError: Cannot read properties of undefined (reading 'includes')`.
 *
 * Root cause (confirmed against live data on fe-bar-ir-2026): `adjudications.recommended_verdict`
 * is the OPERATIONAL verdict, so an investigate claim arrives as 'PEND' (with disposition
 * 'PEND_INVESTIGATE'). DISPOSITIONS_FOR has no 'PEND' key, so `DISPOSITIONS_FOR['PEND']` was
 * `undefined` and `.includes(...)` threw inside DecisionForm's validation useMemo.
 *
 * These two helpers are the fix: `uiVerdict` reconciles 'PEND' -> 'PEND_INVESTIGATE', and
 * `dispositionsFor` is a safe accessor that can never return undefined.
 */
describe('dispositionsFor', () => {
  it('returns the valid dispositions for each defined UI verdict', () => {
    expect(dispositionsFor('APPROVE')).toEqual(['CREDIT', 'REPLACEMENT', 'REWORK']);
    expect(dispositionsFor('DENY')).toEqual(['DENY', 'DUPLICATE']);
    expect(dispositionsFor('PEND_INVESTIGATE')).toEqual(['PEND_INVESTIGATE']);
  });

  it('returns an empty array (never undefined) for the operational PEND verdict', () => {
    // This is the exact value that crashed the cockpit.
    expect(dispositionsFor('PEND')).toEqual([]);
  });

  it('returns an empty array for unknown / null / undefined verdicts', () => {
    expect(dispositionsFor('REVIEWED')).toEqual([]);
    expect(dispositionsFor(null)).toEqual([]);
    expect(dispositionsFor(undefined)).toEqual([]);
    expect(dispositionsFor('')).toEqual([]);
  });

  it('never throws on .includes for an unexpected verdict (the crash site)', () => {
    // Mirrors DecisionForm's `dispositionsFor(verdict).includes(disposition)`.
    expect(() => dispositionsFor('PEND').includes('PEND_INVESTIGATE')).not.toThrow();
    expect(() => dispositionsFor('nonsense').includes('X')).not.toThrow();
    expect(dispositionsFor('PEND').includes('PEND_INVESTIGATE')).toBe(false);
  });

  it('does not mutate or alias the DISPOSITIONS_FOR source arrays', () => {
    expect(dispositionsFor('APPROVE')).toBe(DISPOSITIONS_FOR.APPROVE);
  });
});

describe('uiVerdict', () => {
  it('collapses the operational PEND to the UI PEND_INVESTIGATE', () => {
    expect(uiVerdict('PEND')).toBe('PEND_INVESTIGATE');
  });

  it('passes the agent/UI verdicts through unchanged', () => {
    expect(uiVerdict('PEND_INVESTIGATE')).toBe('PEND_INVESTIGATE');
    expect(uiVerdict('APPROVE')).toBe('APPROVE');
    expect(uiVerdict('DENY')).toBe('DENY');
  });

  it('falls back to APPROVE (a defined, non-empty verdict) for null/unknown', () => {
    expect(uiVerdict(null)).toBe('APPROVE');
    expect(uiVerdict(undefined)).toBe('APPROVE');
    expect(uiVerdict('REVIEWED')).toBe('APPROVE');
  });

  it('composes with dispositionsFor so an investigate recommendation resolves safely', () => {
    // The end-to-end form init path: operational 'PEND' -> defined disposition set.
    const v = uiVerdict('PEND');
    expect(dispositionsFor(v)).toEqual(['PEND_INVESTIGATE']);
    expect(dispositionsFor(v).includes('PEND_INVESTIGATE')).toBe(true);
  });
});
