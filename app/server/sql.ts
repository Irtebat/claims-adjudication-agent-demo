/**
 * Read-side SQL for the cockpit backend (App-SP Lakebase pool). Every query binds
 * its parameters; sort/order come from allowlists (never interpolated raw).
 */

import type { Sql } from './finalize';

const QUEUE_SORTS: Record<string, string> = {
  recommended_at: 'a.recommended_at',
  approved_amount: 'a.approved_amount',
  claimed_amount: 'a.claimed_amount',
  claim_date: 'c.claim_date',
  confidence: 'a.confidence',
};
const HISTORY_SORTS: Record<string, string> = {
  finalized_at: 'a.finalized_at',
  approved_amount: 'a.approved_amount',
  claim_date: 'c.claim_date',
};

export interface ListFilters {
  claimType?: string;
  customerId?: string;
  verdict?: string;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
}

function orderClause(sorts: Record<string, string>, fallback: string, f: ListFilters): string {
  const col = (f.sort && sorts[f.sort]) || sorts[fallback];
  const dir = f.order?.toLowerCase() === 'asc' ? 'ASC' : 'DESC';
  return `ORDER BY ${col} ${dir} NULLS LAST`;
}

function paging(f: ListFilters, params: unknown[]): string {
  const limit = Math.min(Math.max(Number(f.limit) || 50, 1), 500);
  const offset = Math.max(Number(f.offset) || 0, 0);
  params.push(limit, offset);
  return `LIMIT $${params.length - 1} OFFSET $${params.length}`;
}

function whereFilters(alias: string, claimAlias: string, f: ListFilters, params: unknown[]): string {
  const clauses: string[] = [];
  if (f.claimType) {
    params.push(f.claimType);
    clauses.push(`${claimAlias}.claim_type = $${params.length}`);
  }
  if (f.customerId) {
    params.push(f.customerId);
    clauses.push(`${claimAlias}.customer_id = $${params.length}`);
  }
  if (f.verdict) {
    params.push(f.verdict);
    clauses.push(`${alias}.verdict = $${params.length}`);
  }
  return clauses.length ? ' AND ' + clauses.join(' AND ') : '';
}

/** Adjuster queue: claims ⋈ adjudications where decision_status='RECOMMENDED'. */
export function queueSql(f: ListFilters = {}): Sql {
  const params: unknown[] = [];
  const where = whereFilters('a', 'c', f, params);
  const text = `
    SELECT a.adjudication_id, a.claim_id, c.customer_id, c.claim_type, c.claim_date,
           c.defect_code, c.defect_narrative, c.claimed_tonnage, c.claimed_freight,
           a.verdict, a.recommended_verdict, a.recommended_disposition,
           a.approved_amount, a.claimed_amount, a.confidence,
           a.duplicate_of_claim_id, a.fraud_cluster_id, a.supplier_attributable,
           a.recommended_at
      FROM public.adjudications a
      JOIN public.claims c ON c.claim_id = a.claim_id
     WHERE a.decision_status = 'RECOMMENDED'${where}
     ${orderClause(QUEUE_SORTS, 'recommended_at', f)}
     ${paging(f, params)}`;
  return { text, params };
}

/** Claims history: FINAL adjudications (with decided_by / override metadata). */
export function historySql(f: ListFilters = {}): Sql {
  const params: unknown[] = [];
  const where = whereFilters('a', 'c', f, params);
  const text = `
    SELECT a.adjudication_id, a.claim_id, c.customer_id, c.claim_type, c.claim_date,
           c.defect_code, a.verdict, a.disposition, a.approved_amount, a.claimed_amount,
           a.override_flag, a.override_reason, a.decided_by, a.finalized_at,
           a.recommended_verdict, a.recommended_disposition
      FROM public.adjudications a
      JOIN public.claims c ON c.claim_id = a.claim_id
     WHERE a.decision_status = 'FINAL'${where}
     ${orderClause(HISTORY_SORTS, 'finalized_at', f)}
     ${paging(f, params)}`;
  return { text, params };
}

/** Cockpit: the current adjudication (recommendation) for one claim. */
export function cockpitAdjudicationSql(claimId: string): Sql {
  return {
    text: `SELECT a.*, c.coil_id, c.customer_id, c.claim_type, c.claim_date,
                  c.install_date, c.environment, c.installation, c.coast_distance_km,
                  c.defect_code, c.defect_narrative, c.claimed_tonnage, c.claimed_freight
             FROM public.adjudications a
             JOIN public.claims c ON c.claim_id = a.claim_id
            WHERE a.claim_id = $1
            ORDER BY a.recommended_at DESC NULLS LAST
            LIMIT 1`,
    params: [claimId],
  };
}

/** Cockpit: the full immutable decision-record trail for one adjudication. */
export function cockpitDecisionRecordsSql(adjudicationId: string): Sql {
  return {
    text: `SELECT record_version, deterministic_verdict, deterministic_disposition,
                  recommended_verdict, recommended_disposition, approved_amount,
                  over_claim_flag, duplicate_flag, conformance, coverage, settlement,
                  duplicate, citations, cited_clause_ids, precedent, advisory_risk,
                  rationale, confidence, invariant_violations, decided_by,
                  override_reason, created_at
             FROM public.adjudication_decision_records
            WHERE adjudication_id = $1
            ORDER BY record_version ASC`,
    params: [adjudicationId],
  };
}

/** Cockpit context: heat/coil, MTC, customer, and customer-heat risk for a claim. */
export function cockpitContextSql(coilId: string, customerId: string): Sql {
  return {
    text: `SELECT
             (SELECT to_jsonb(h) FROM reference.heats_coils h WHERE h.coil_id = $1) AS heats_coils,
             (SELECT to_jsonb(m) FROM reference.mill_test_certs m
                JOIN reference.heats_coils h ON h.heat_no = m.heat_no
               WHERE h.coil_id = $1
               ORDER BY m.cert_date DESC NULLS LAST, m.cert_id DESC
               LIMIT 1) AS mill_test_cert,
             (SELECT to_jsonb(cu) FROM reference.customers cu WHERE cu.customer_id = $2) AS customer,
             (SELECT to_jsonb(r) FROM reference.customer_heat_risk r
               WHERE r.customer_id = $2
                 AND r.heat_no = (SELECT heat_no FROM reference.heats_coils WHERE coil_id = $1)
               LIMIT 1) AS customer_heat_risk`,
    params: [coilId, customerId],
  };
}

/** Cockpit precedent: prior_claims rows referenced by the decision record. */
export function cockpitPrecedentSql(claimIds: string[]): Sql {
  return {
    text: `SELECT claim_id, coil_id, grade, coating_class, defect_code, defect_narrative,
                  claim_date, verdict, approved_amount
             FROM public.prior_claims
            WHERE claim_id = ANY($1::text[])`,
    params: [claimIds],
  };
}
