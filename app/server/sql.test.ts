import { describe, it, expect } from 'vitest';
import { queueSql, cockpitAdjudicationSql, historySql } from './sql';

/** Collapse whitespace so assertions about SQL structure ignore formatting. */
function flat(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

describe('queueSql', () => {
  it('excludes a claim that has ANY human-final (FINAL/REVIEWED) sibling adjudication', () => {
    const { text } = queueSql();
    const t = flat(text);
    // Still the RECOMMENDED queue…
    expect(t).toContain("a.decision_status = 'RECOMMENDED'");
    // …but a claim drops out once a human-final sibling exists, keyed on claim_id.
    expect(t).toContain('AND NOT EXISTS ( SELECT 1 FROM public.adjudications h');
    expect(t).toContain('WHERE h.claim_id = a.claim_id');
    expect(t).toContain("AND h.decision_status IN ('FINAL', 'REVIEWED')");
  });

  it('allows multiple RECOMMENDED rows per claim (the exclusion is only on human-final siblings)', () => {
    // The correlated subquery matches ONLY FINAL/REVIEWED, never another RECOMMENDED row,
    // so a claim with several RECOMMENDED adjudications stays in the queue.
    const t = flat(queueSql().text);
    const notExists = t.slice(t.indexOf('NOT EXISTS'));
    expect(notExists).not.toContain("'RECOMMENDED'");
  });

  it('still applies caller filters, ordering, and paging alongside the exclusion', () => {
    const { text, params } = queueSql({ claimType: 'coating_warranty', limit: 25, offset: 5 });
    const t = flat(text);
    expect(t).toContain('c.claim_type = $1');
    expect(t).toContain('ORDER BY a.recommended_at DESC NULLS LAST');
    // limit + offset are the last two bound params.
    expect(params.slice(-2)).toEqual([25, 5]);
  });
});

describe('cockpitAdjudicationSql', () => {
  it('resolves by claim only when no adjudication_id is given (RECOMMENDED-first safety net)', () => {
    const { text, params } = cockpitAdjudicationSql('CLM-9');
    const t = flat(text);
    expect(params).toEqual(['CLM-9']);
    expect(t).toContain('WHERE a.claim_id = $1');
    expect(t).not.toContain('a.adjudication_id = $2');
    // RECOMMENDED-first ordering is preserved as the fallback.
    expect(t).toContain("ORDER BY CASE WHEN a.decision_status = 'RECOMMENDED' THEN 0 ELSE 1 END");
    expect(t).toContain('LIMIT 1');
  });

  it('opens the EXACT adjudication when an adjudication_id is passed', () => {
    const { text, params } = cockpitAdjudicationSql('CLM-9', 'ADJ-abc');
    const t = flat(text);
    expect(params).toEqual(['CLM-9', 'ADJ-abc']);
    expect(t).toContain('WHERE a.claim_id = $1 AND a.adjudication_id = $2');
    // The RECOMMENDED-first ordering stays in place as a safety net (harmless once the
    // exact filter has narrowed to a single row).
    expect(t).toContain("ORDER BY CASE WHEN a.decision_status = 'RECOMMENDED' THEN 0 ELSE 1 END");
    expect(t).toContain('LIMIT 1');
  });
});

describe('historySql', () => {
  it('lists FINAL adjudications (unchanged by the queue-correctness fix)', () => {
    const t = flat(historySql().text);
    expect(t).toContain("a.decision_status = 'FINAL'");
    expect(t).not.toContain('NOT EXISTS');
  });
});
