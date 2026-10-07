import { describe, it, expect } from 'vitest';
import { queueSql, cockpitAdjudicationSql, historySql, sourceRowSql, isSourceName, SOURCE_REGISTRY } from './sql';

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

  it('LEFT JOINs customer_heat_risk and surfaces the gold high_risk signal (not fraud_cluster_id alone)', () => {
    // Bug 2: the queue chip must gate on the tuned `high_risk` flag, which lives on
    // reference.customer_heat_risk — not on `fraud_cluster_id`, which is on ~every claim. The
    // joins are LEFT so a claim with no matching heat-risk row is listed (high_risk NULL), not
    // dropped or duplicated.
    const t = flat(queueSql().text);
    expect(t).toContain('LEFT JOIN reference.heats_coils hc ON hc.coil_id = c.coil_id');
    expect(t).toContain(
      'LEFT JOIN reference.customer_heat_risk r ON r.customer_id = c.customer_id AND r.heat_no = hc.heat_no'
    );
    expect(t).toContain('r.high_risk');
    expect(t).toContain('r.risk_reason');
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

describe('sourceRowSql — whitelisted, parameterized, read-only row drill-through', () => {
  it('rejects any source that is not in the whitelist (no arbitrary table access)', () => {
    expect(isSourceName('claims')).toBe(false);
    expect(isSourceName('adjudications')).toBe(false);
    expect(isSourceName('pg_authid')).toBe(false);
    // A non-whitelisted source yields no SQL at all.
    expect(sourceRowSql({ source: 'claims', id: 'CLM-1' })).toBeNull();
    expect(sourceRowSql({ source: 'users; DROP TABLE claims', id: 'x' })).toBeNull();
  });

  it('whitelists exactly the six cockpit evidence sources', () => {
    expect(Object.keys(SOURCE_REGISTRY).sort()).toEqual(
      [
        'customer_heat_risk',
        'customers',
        'heats_coils',
        'mill_test_certs',
        'prior_claims',
        'spec_clauses',
        'warranty_clauses',
      ].sort()
    );
  });

  it('binds a scalar-key source (prior_claims) and reads the whole row', () => {
    const q = sourceRowSql({ source: 'prior_claims', id: 'CLM-42' });
    expect(q).not.toBeNull();
    const t = flat(q!.text);
    expect(t).toBe('SELECT * FROM reference.prior_claims_corpus WHERE claim_id = $1 LIMIT 1');
    expect(q!.params).toEqual(['CLM-42']);
  });

  it('reads reference tables from the reference schema (coil / cert / customer)', () => {
    expect(flat(sourceRowSql({ source: 'heats_coils', id: 'C-1' })!.text)).toBe(
      'SELECT * FROM reference.heats_coils WHERE coil_id = $1 LIMIT 1'
    );
    expect(flat(sourceRowSql({ source: 'mill_test_certs', id: 'MTC-9' })!.text)).toBe(
      'SELECT * FROM reference.mill_test_certs WHERE cert_id = $1 LIMIT 1'
    );
    expect(flat(sourceRowSql({ source: 'customers', id: 'CU-3' })!.text)).toBe(
      'SELECT * FROM reference.customers WHERE customer_id = $1 LIMIT 1'
    );
  });

  it('resolves a cited clause by reconstructing the persisted citation_key (concat_ws)', () => {
    // spec: citation_key = grade/region/spec_edition/section_ref (agent/src/retrieval.py).
    const spec = sourceRowSql({ source: 'spec_clauses', id: 'A653/NA/DEMO-1990/mechanical' })!;
    const st = flat(spec.text);
    expect(st).toContain('FROM public.spec_clauses');
    expect(st).toContain("WHERE concat_ws('/', grade, region, spec_edition, section_ref) = $1");
    expect(spec.params).toEqual(['A653/NA/DEMO-1990/mechanical']);
    // warranty: citation_key = product_line/region/version/section_ref.
    const war = sourceRowSql({ source: 'warranty_clauses', id: 'galvanized/NA/V2/exclusions' })!;
    expect(flat(war.text)).toContain("WHERE concat_ws('/', product_line, region, version, section_ref) = $1");
    expect(war.params).toEqual(['galvanized/NA/V2/exclusions']);
  });

  it('does NOT split the citation_key client/server side — the whole key is a single bound param', () => {
    // A spec_edition containing a slash must round-trip intact (matched via concat_ws).
    const q = sourceRowSql({ source: 'spec_clauses', id: 'G550/NA/A653/A653M-20/mechanical' })!;
    expect(q.params).toEqual(['G550/NA/A653/A653M-20/mechanical']);
    expect(q.params).toHaveLength(1);
  });

  it('binds the composite customer_heat_risk key (customer_id + heat_no)', () => {
    const q = sourceRowSql({ source: 'customer_heat_risk', id: 'CU-3', extra: { heat_no: 'H-4821' } })!;
    const t = flat(q.text);
    expect(t).toBe('SELECT * FROM reference.customer_heat_risk WHERE customer_id = $1 AND heat_no = $2 LIMIT 1');
    expect(q.params).toEqual(['CU-3', 'H-4821']);
  });

  it('returns null when a required composite key value is missing (route -> 400)', () => {
    expect(sourceRowSql({ source: 'customer_heat_risk', id: 'CU-3' })).toBeNull();
    expect(sourceRowSql({ source: 'customer_heat_risk', id: 'CU-3', extra: { heat_no: '' } })).toBeNull();
  });

  it('never interpolates request input — only $-placeholders carry values', () => {
    const q = sourceRowSql({ source: 'prior_claims', id: "'; DROP TABLE claims; --" })!;
    // The malicious id is a bound parameter, not part of the SQL text.
    expect(q.text).not.toContain('DROP TABLE');
    expect(q.params).toEqual(["'; DROP TABLE claims; --"]);
  });
});
