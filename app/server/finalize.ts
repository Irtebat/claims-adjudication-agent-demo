/**
 * Human finalization transaction (Wave 7, Task 3) — the App is the finalizer.
 *
 * Given a human decision, ONE Postgres transaction (App-SP pool):
 *   1. UPDATE public.adjudications -> decision_status='FINAL' with the human verdict,
 *      disposition, approved_amount, override flag/reason, decided_by, finalized_at.
 *      Guarded `WHERE decision_status='RECOMMENDED'` (first-write-wins idempotency).
 *   2. INSERT a NEW immutable record_version into public.adjudication_decision_records
 *      capturing the human-final decision + decided_by + override_reason, while
 *      PRESERVING the deterministic baseline (deterministic_verdict/disposition and
 *      the settlement/conformance/coverage/duplicate structs) copied from the prior
 *      version, so the audit shows BOTH the math's result and the human's decision.
 *   3. INSERT the public.outbox 'claim.adjudicated' row (event_id adj-<id>) whose
 *      payload reflects the FINAL human decision. This is the ONLY place the
 *      claim.adjudicated fan-out is emitted (recommendation writes no outbox row).
 *
 * The deterministic money math (authorities.py) is never re-run here: an adjuster
 * amount/verdict change is persisted as an explicit, logged OVERRIDE, not a
 * recomputation. Overriding without a reason is rejected server-side. The final
 * decision is validated to be internally money-consistent so it cannot poison the
 * silver/gold medallion invariants. All params are bound.
 */

export type AgentVerdict = 'APPROVE' | 'DENY' | 'PEND_INVESTIGATE';
export type OperationalVerdict = 'APPROVE' | 'DENY' | 'PEND';

const APPROVE_DISPOSITIONS = new Set(['CREDIT', 'REPLACEMENT', 'REWORK']);
const DENY_DISPOSITIONS = new Set(['DENY', 'DUPLICATE']);

/** Map an agent verdict to the operational adjudications.verdict value. */
export function operationalVerdict(v: AgentVerdict): OperationalVerdict {
  return v === 'PEND_INVESTIGATE' ? 'PEND' : v;
}

/**
 * Canonical verdict for the "did the human change the recommendation?" comparison only.
 *
 * The agent/UI 'PEND_INVESTIGATE' and the operational 'PEND' are the SAME verdict in two
 * vocabularies: `adjudications.recommended_verdict` is stored operationally ('PEND') while the
 * client submits the agent form ('PEND_INVESTIGATE'). Collapsing both to 'PEND' before comparing
 * means accepting an investigate/hold recommendation UNCHANGED is not mistaken for an override
 * (which would wrongly demand an override reason). This affects ONLY override/differs detection —
 * it does not change which verdicts/dispositions finalize accepts or writes, the operational
 * verdict persisted, or any money/eligibility invariant.
 */
function canonicalVerdict(v: string): string {
  return v === 'PEND_INVESTIGATE' ? 'PEND' : v;
}

export interface FinalizeRequest {
  finalVerdict: AgentVerdict;
  finalDisposition: string;
  approvedAmount: number;
  overrideReason?: string | null;
  decidedBy: string | null;
}

/** The current RECOMMENDED adjudication the human is acting on. */
export interface RecommendationState {
  recommendedVerdict: string;
  recommendedDisposition: string;
  approvedAmount: number;
  claimedAmount: number;
}

export interface Validation {
  ok: boolean;
  errors: string[];
  overrideFlag: boolean;
  operationalVerdict: OperationalVerdict;
  duplicateFlag: boolean;
}

function amountsEqual(a: number, b: number): boolean {
  return Math.abs(a - b) < 1e-9;
}

/**
 * Validate a human finalization against the current recommendation. Pure.
 *
 * Enforces: decided_by present; a valid agent verdict; approved_amount in
 * [0, claimed_amount]; verdict/disposition/amount internal consistency (so the
 * medallion invariants hold); and — the money-safety rule — override_reason is
 * MANDATORY whenever any of {verdict, disposition, approved_amount} differs from the
 * recommendation. `overrideFlag` is true exactly when the decision differs.
 */
export function validateFinalize(req: FinalizeRequest, rec: RecommendationState): Validation {
  const errors: string[] = [];
  const verdict = req.finalVerdict;
  const disposition = req.finalDisposition;
  const amount = req.approvedAmount;

  if (!req.decidedBy || req.decidedBy.trim().length === 0) {
    errors.push('decided_by_required');
  }
  if (verdict !== 'APPROVE' && verdict !== 'DENY' && verdict !== 'PEND_INVESTIGATE') {
    errors.push('invalid_verdict');
  }
  if (typeof amount !== 'number' || Number.isNaN(amount) || amount < 0) {
    errors.push('invalid_approved_amount');
  } else if (amount > rec.claimedAmount + 1e-9) {
    errors.push('approved_amount_exceeds_claimed');
  }

  // verdict/disposition/amount consistency — matches the silver + gold invariants.
  if (verdict === 'APPROVE') {
    if (!APPROVE_DISPOSITIONS.has(disposition)) errors.push('invalid_approve_disposition');
  } else if (verdict === 'DENY') {
    if (!DENY_DISPOSITIONS.has(disposition)) errors.push('invalid_deny_disposition');
    if (!amountsEqual(amount, 0)) errors.push('deny_must_be_zero_amount');
  } else if (verdict === 'PEND_INVESTIGATE') {
    if (disposition !== 'PEND_INVESTIGATE') errors.push('invalid_pend_disposition');
    if (!amountsEqual(amount, 0)) errors.push('pend_must_be_zero_amount');
  }

  // Compare verdicts on a canonical basis so the operational 'PEND' recommendation and an
  // unchanged 'PEND_INVESTIGATE' acceptance are not treated as a change. A genuine change —
  // PEND -> APPROVE/DENY, any disposition change, or any amount change — still flips `differs`.
  const differs =
    canonicalVerdict(verdict) !== canonicalVerdict(rec.recommendedVerdict) ||
    disposition !== rec.recommendedDisposition ||
    !amountsEqual(amount, rec.approvedAmount);
  if (differs && (!req.overrideReason || req.overrideReason.trim().length === 0)) {
    errors.push('override_reason_required');
  }

  return {
    ok: errors.length === 0,
    errors,
    overrideFlag: differs,
    operationalVerdict: operationalVerdict(verdict),
    // duplicate_flag on the human-final decision-record version reflects the FINAL
    // decision (keeps the gold money_invariant valid); the preserved `duplicate`
    // struct still records what the automated gate found.
    duplicateFlag: verdict === 'DENY' && disposition === 'DUPLICATE',
  };
}

export interface Sql {
  text: string;
  params: unknown[];
}

/**
 * Load the current adjudication under a row lock for the finalize transaction.
 *
 * Scoped to BOTH adjudication_id AND claim_id: the claim_id comes from the route path
 * and the adjudication_id from the request body, so a caller cannot finalize an
 * adjudication that belongs to a DIFFERENT claim by substituting an arbitrary
 * adjudication_id. A mismatched (claim_id, adjudication_id) pair matches no row — the
 * same not_found outcome as a truly-absent adjudication (no write, no outbox).
 */
export function selectForFinalizeSql(adjudicationId: string, claimId: string): Sql {
  return {
    text: `SELECT claim_id, recommended_verdict, recommended_disposition,
                  approved_amount, claimed_amount, decision_status,
                  supplier_attributable, recovery_supplier_id, duplicate_of_claim_id,
                  idempotency_key
             FROM public.adjudications
            WHERE adjudication_id = $1
              AND claim_id = $2
            FOR UPDATE`,
    params: [adjudicationId, claimId],
  };
}

/**
 * UPDATE the adjudications row to FINAL, guarded on RECOMMENDED (first-write-wins).
 *
 * Also scoped to claim_id (from the route path) in addition to adjudication_id, so the
 * atomic write cannot touch an adjudication belonging to a different claim even if the
 * SELECT guard were bypassed — a mismatched pair updates zero rows.
 */
export function finalizeUpdateSql(
  adjudicationId: string,
  claimId: string,
  v: {
    operationalVerdict: OperationalVerdict;
    disposition: string;
    approvedAmount: number;
    overrideFlag: boolean;
    overrideReason: string | null;
    decidedBy: string;
  }
): Sql {
  return {
    text: `UPDATE public.adjudications
              SET decision_status = 'FINAL',
                  verdict = $2,
                  disposition = $3,
                  approved_amount = $4,
                  override_flag = $5,
                  override_reason = $6,
                  decided_by = $7,
                  finalized_at = now()
            WHERE adjudication_id = $1
              AND claim_id = $8
              AND decision_status = 'RECOMMENDED'
        RETURNING claim_id, idempotency_key, supplier_attributable,
                  recovery_supplier_id, duplicate_of_claim_id`,
    params: [
      adjudicationId,
      v.operationalVerdict,
      v.disposition,
      v.approvedAmount,
      v.overrideFlag,
      v.overrideReason,
      v.decidedBy,
      claimId,
    ],
  };
}

/**
 * INSERT a new immutable record_version by copying the latest existing version
 * forward, overriding ONLY the human-decision columns and preserving the
 * deterministic baseline (deterministic_verdict/disposition, settlement, and every
 * other authored column) so the immutable audit shows both.
 *
 * No ON CONFLICT clause: the row-locked UPDATE guard in runFinalize serializes
 * finalization so this insert runs at most once per adjudication, and the caller
 * asserts rowCount === 1 — a record_version collision or missing baseline therefore
 * fails the whole transaction rather than being silently swallowed.
 */
export function decisionRecordInsertSql(
  adjudicationId: string,
  v: {
    finalVerdict: AgentVerdict;
    finalDisposition: string;
    approvedAmount: number;
    duplicateFlag: boolean;
    decidedBy: string;
    overrideReason: string | null;
  }
): Sql {
  return {
    text: `INSERT INTO public.adjudication_decision_records (
             adjudication_id, claim_id, record_version, idempotency_key, claim_type,
             claim_input, spec_provenance, warranty_provenance, spec_params,
             warranty_terms, freight_cap, coil, mtc_measured, conformance, coverage,
             settlement, duplicate, claimed_amount, approved_amount, over_claim_flag,
             duplicate_flag, deterministic_verdict, deterministic_disposition,
             recommended_verdict, recommended_disposition, rationale, confidence,
             flags, advisory_risk, precedent, invariant_violations, citations,
             cited_clause_ids, authorities_git_sha, authorities_source_sha256,
             agent_model_name, agent_model_version, reasoning_endpoint, prompt_version,
             schema_version, mlflow_trace_id, decided_by, override_reason
           )
           SELECT
             adjudication_id, claim_id,
             (SELECT max(record_version) + 1 FROM public.adjudication_decision_records
               WHERE adjudication_id = $1),
             idempotency_key, claim_type, claim_input, spec_provenance,
             warranty_provenance, spec_params, warranty_terms, freight_cap, coil,
             mtc_measured, conformance, coverage, settlement, duplicate, claimed_amount,
             $2::numeric AS approved_amount,      -- FINAL human amount
             over_claim_flag,
             $3::boolean AS duplicate_flag,        -- reflects FINAL decision
             deterministic_verdict, deterministic_disposition,   -- PRESERVED baseline
             $4::text AS recommended_verdict,      -- FINAL human verdict
             $5::text AS recommended_disposition,  -- FINAL human disposition
             rationale, confidence, flags, advisory_risk, precedent,
             invariant_violations, citations, cited_clause_ids, authorities_git_sha,
             authorities_source_sha256, agent_model_name, agent_model_version,
             reasoning_endpoint, prompt_version, schema_version, mlflow_trace_id,
             $6::text AS decided_by, $7::text AS override_reason
           FROM public.adjudication_decision_records
           WHERE adjudication_id = $1
           ORDER BY record_version DESC
           LIMIT 1`,
    params: [
      adjudicationId,
      v.approvedAmount,
      v.duplicateFlag,
      v.finalVerdict,
      v.finalDisposition,
      v.decidedBy,
      v.overrideReason,
    ],
  };
}

export const EVENT_CLAIM_ADJUDICATED = 'claim.adjudicated';
export const EVENT_SCHEMA_VERSION = 'claim-event/v1';

/**
 * Build the claim.adjudicated outbox event reflecting the FINAL human decision.
 * Shape mirrors services/src/events.py:build_adjudicated_payload — the relay and
 * consumers read `verdict` (operational), `approved_amount`, `supplier_attributable`,
 * `recovery_supplier_id`, and `duplicate_of_claim_id`.
 */
export function buildAdjudicatedEvent(
  adjudicationId: string,
  row: {
    claim_id: string;
    idempotency_key: string;
    supplier_attributable: boolean | null;
    recovery_supplier_id: string | null;
    duplicate_of_claim_id: string | null;
  },
  v: {
    finalVerdict: AgentVerdict;
    operationalVerdict: OperationalVerdict;
    disposition: string;
    approvedAmount: number;
  },
  now: () => string = () => new Date().toISOString()
): { eventId: string; sql: Sql } {
  const eventId = `adj-${adjudicationId}`;
  const payload = {
    event_id: eventId,
    event_type: EVENT_CLAIM_ADJUDICATED,
    schema_version: EVENT_SCHEMA_VERSION,
    claim_id: row.claim_id,
    adjudication_id: adjudicationId,
    verdict: v.operationalVerdict,
    recommended_verdict: v.finalVerdict,
    disposition: v.disposition,
    approved_amount: v.approvedAmount,
    supplier_attributable: Boolean(row.supplier_attributable),
    recovery_supplier_id: row.recovery_supplier_id,
    duplicate_of_claim_id: row.duplicate_of_claim_id,
    idempotency_key: row.idempotency_key,
    finalized_at: now(),
  };
  return {
    eventId,
    sql: {
      text: `INSERT INTO public.outbox (event_id, aggregate_id, event_type, payload)
             VALUES ($1, $2, $3, $4::jsonb)
             ON CONFLICT (event_id) DO NOTHING`,
      params: [eventId, row.claim_id, EVENT_CLAIM_ADJUDICATED, JSON.stringify(payload)],
    },
  };
}

/** Minimal pg client/pool surface (so runFinalize is unit-testable with a fake). */
export interface QueryClient {
  query(text: string, params?: unknown[]): Promise<{ rows: Record<string, unknown>[]; rowCount: number }>;
  release(): void;
}
export interface Pool {
  connect(): Promise<QueryClient>;
}

export type FinalizeResult =
  | { status: 'finalized'; adjudicationId: string; decidedBy: string; overrideFlag: boolean; eventId: string }
  | { status: 'already_final'; adjudicationId: string }
  | { status: 'not_found'; adjudicationId: string }
  | { status: 'invalid'; adjudicationId: string; errors: string[] };

/**
 * Execute the finalization atomically. Idempotent + first-write-wins: a re-finalize
 * of an already-FINAL adjudication is a no-op (no double outbox / no new version).
 */
export async function runFinalize(
  pool: Pool,
  adjudicationId: string,
  claimId: string,
  req: FinalizeRequest,
  now: () => string = () => new Date().toISOString()
): Promise<FinalizeResult> {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    // Scope the whole transaction to BOTH the body adjudication_id AND the path claim_id,
    // so a caller cannot finalize an adjudication that belongs to a different claim. A
    // mismatched pair locks no row below → not_found (no write, no outbox).
    const sel = selectForFinalizeSql(adjudicationId, claimId);
    const cur = await client.query(sel.text, sel.params);
    if (cur.rowCount === 0) {
      await client.query('ROLLBACK');
      return { status: 'not_found', adjudicationId };
    }
    const cr = cur.rows[0];
    if (cr.decision_status === 'FINAL') {
      await client.query('ROLLBACK');
      return { status: 'already_final', adjudicationId };
    }
    const v = validateFinalize(req, {
      recommendedVerdict: String(cr.recommended_verdict),
      recommendedDisposition: String(cr.recommended_disposition),
      approvedAmount: Number(cr.approved_amount),
      claimedAmount: Number(cr.claimed_amount),
    });
    if (!v.ok) {
      await client.query('ROLLBACK');
      return { status: 'invalid', adjudicationId, errors: v.errors };
    }
    const decidedBy = req.decidedBy as string;
    const overrideReason = v.overrideFlag ? (req.overrideReason ?? null) : null;

    const upd = finalizeUpdateSql(adjudicationId, claimId, {
      operationalVerdict: v.operationalVerdict,
      disposition: req.finalDisposition,
      approvedAmount: req.approvedAmount,
      overrideFlag: v.overrideFlag,
      overrideReason,
      decidedBy,
    });
    const updated = await client.query(upd.text, upd.params);
    if (updated.rowCount === 0) {
      // Lost a race to another finalizer between SELECT and UPDATE — no-op.
      await client.query('ROLLBACK');
      return { status: 'already_final', adjudicationId };
    }
    const adj = updated.rows[0];

    const rec = decisionRecordInsertSql(adjudicationId, {
      finalVerdict: req.finalVerdict,
      finalDisposition: req.finalDisposition,
      approvedAmount: req.approvedAmount,
      duplicateFlag: v.duplicateFlag,
      decidedBy,
      overrideReason,
    });
    const recWritten = await client.query(rec.text, rec.params);
    if (recWritten.rowCount !== 1) {
      // The immutable human-final version MUST be written for the finalization to be
      // valid — otherwise we'd COMMIT a FINAL adjudication + outbox event with no
      // audit version (an SCD2/audit-integrity hole). Zero rows here means either no
      // baseline record to copy or a record_version collision; throw so the WHOLE
      // transaction rolls back (no outbox emit, no FINAL flip).
      throw new Error(
        `finalize decision-record version not written for ${adjudicationId} (rowCount=${recWritten.rowCount})`
      );
    }

    const event = buildAdjudicatedEvent(
      adjudicationId,
      {
        claim_id: String(adj.claim_id),
        idempotency_key: String(adj.idempotency_key),
        supplier_attributable: adj.supplier_attributable as boolean | null,
        recovery_supplier_id: (adj.recovery_supplier_id as string | null) ?? null,
        duplicate_of_claim_id: (adj.duplicate_of_claim_id as string | null) ?? null,
      },
      {
        finalVerdict: req.finalVerdict,
        operationalVerdict: v.operationalVerdict,
        disposition: req.finalDisposition,
        approvedAmount: req.approvedAmount,
      },
      now
    );
    await client.query(event.sql.text, event.sql.params);

    await client.query('COMMIT');
    return {
      status: 'finalized',
      adjudicationId,
      decidedBy,
      overrideFlag: v.overrideFlag,
      eventId: event.eventId,
    };
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}
