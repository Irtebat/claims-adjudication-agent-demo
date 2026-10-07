/**
 * Client-side view of the Stage-A backend payloads (server/routes.ts + server/sql.ts).
 *
 * Lakebase numeric/timestamp columns arrive over node-postgres as strings (numeric) or
 * numbers depending on type, so money/score/date fields are typed `Num`/`Str` and are
 * always coerced through lib/format.ts rather than trusted as a specific JS type. jsonb
 * evidence structs mirror the shapes frozen in the gold decision-record transform
 * (pipelines/src/transformations/gold_adjudication_decision_records.py).
 */

/** A numeric column that may arrive as number or string (pg `numeric`), or be null. */
export type Num = number | string | null;
/** A text/timestamp column that may be null. */
export type Str = string | null;

export type Role = 'adjuster' | 'business_user';

export type Verdict = 'APPROVE' | 'DENY' | 'PEND_INVESTIGATE';
/** The operational adjudications.verdict value (PEND_INVESTIGATE collapses to PEND). */
export type OperationalVerdict = 'APPROVE' | 'DENY' | 'PEND';
export type DecisionStatus = 'RECOMMENDED' | 'FINAL';

/**
 * The signed-in user and the FULL set of roles the SERVER resolved for them, plus the
 * default active role (the persona the UI opens in). `roles` is authoritative for what
 * the user may do; the client's chosen active role is a view preference only and never
 * grants access — every backend route re-enforces the permission matrix server-side.
 * A user holding both roles gets a persona switch; an empty `roles` means no access.
 */
export interface Whoami {
  email: Str;
  user: Str;
  roles: Role[];
  defaultRole: Role | null;
}

/** One row of the adjuster work queue (`GET /api/queue`). */
export interface QueueItem {
  adjudication_id: string;
  claim_id: string;
  customer_id: Str;
  claim_type: Str;
  claim_date: Str;
  defect_code: Str;
  defect_narrative: Str;
  claimed_tonnage: Num;
  claimed_freight: Num;
  verdict: Str;
  recommended_verdict: Str;
  recommended_disposition: Str;
  approved_amount: Num;
  claimed_amount: Num;
  confidence: Num;
  duplicate_of_claim_id: Str;
  fraud_cluster_id: Str;
  supplier_attributable: boolean | null;
  recommended_at: Str;
  /**
   * Gold fraud signal joined from reference.customer_heat_risk (risk_score + cluster_size
   * tuned threshold). Unlike fraud_cluster_id — a graph-component id present on virtually
   * every claim — high_risk is the real signal the queue's risk chip gates on. May be null
   * when no customer_heat_risk row matches (or the column predates the fraud workstream).
   */
  high_risk: boolean | null;
  /** Human-readable basis for high_risk (becomes the risk chip's tooltip). May be null. */
  risk_reason: Str;
}

/** One row of claims history (`GET /api/history`, FINAL adjudications). */
export interface HistoryItem {
  adjudication_id: string;
  claim_id: string;
  customer_id: Str;
  claim_type: Str;
  claim_date: Str;
  defect_code: Str;
  verdict: Str;
  disposition: Str;
  approved_amount: Num;
  claimed_amount: Num;
  override_flag: boolean | null;
  override_reason: Str;
  decided_by: Str;
  finalized_at: Str;
  recommended_verdict: Str;
  recommended_disposition: Str;
}

/** The current adjudication for one claim (`a.*` joined to the claim columns). */
export interface Adjudication {
  adjudication_id: string;
  claim_id: string;
  decision_status: DecisionStatus;
  verdict: Str;
  disposition: Str;
  recommended_verdict: Str;
  recommended_disposition: Str;
  approved_amount: Num;
  claimed_amount: Num;
  confidence: Num;
  override_flag: boolean | null;
  override_reason: Str;
  decided_by: Str;
  recommended_at: Str;
  finalized_at: Str;
  duplicate_of_claim_id: Str;
  fraud_cluster_id: Str;
  supplier_attributable: boolean | null;
  recovery_supplier_id: Str;
  // Claim columns joined in cockpitAdjudicationSql.
  coil_id: Str;
  customer_id: Str;
  claim_type: Str;
  claim_date: Str;
  install_date: Str;
  environment: Str;
  installation: Str;
  coast_distance_km: Num;
  defect_code: Str;
  defect_narrative: Str;
  claimed_tonnage: Num;
  claimed_freight: Num;
}

// --- Deterministic evidence structs (frozen jsonb shapes) --------------------

export interface ConformanceEvidence {
  conforms: boolean;
  nonconforming_properties: string[];
}
export interface CoverageEvidence {
  covered: boolean;
  elapsed_months: number;
  proration_factor: number;
  exclusions_hit: string[];
}
export interface SettlementEvidence {
  approved_amount: number;
  claimed_amount: number;
  covered_tonnage: number;
  freight_amount: number;
  freight_covered: boolean;
  over_claim_detected: boolean;
  is_partial: boolean;
}
export interface DuplicateEvidence {
  is_duplicate: boolean;
  duplicate_of_claim_id: Str;
  narrative_similarity: number;
  candidates_considered: number;
}
export interface Citation {
  citation_key: string;
  section_ref: string;
  clause_text_sha256: Str;
}
export interface PrecedentRef {
  claim_id: string;
  verdict: Str;
  approved_amount: Num;
  rrf_score: Num;
}

/** One immutable decision-record version (`GET /api/claims/:id` -> decision_records). */
export interface DecisionRecord {
  record_version: number;
  deterministic_verdict: Str;
  deterministic_disposition: Str;
  recommended_verdict: Str;
  recommended_disposition: Str;
  approved_amount: Num;
  over_claim_flag: boolean | null;
  duplicate_flag: boolean | null;
  conformance: ConformanceEvidence | null;
  coverage: CoverageEvidence | null;
  settlement: SettlementEvidence | null;
  duplicate: DuplicateEvidence | null;
  citations: Citation[] | null;
  cited_clause_ids: string[] | null;
  precedent: PrecedentRef[] | null;
  advisory_risk: Record<string, unknown> | null;
  rationale: Str;
  confidence: Num;
  invariant_violations: string[] | null;
  decided_by: Str;
  override_reason: Str;
  created_at: Str;
}

/** A prior claim referenced as precedent (`GET /api/claims/:id` -> prior_claims). */
export interface PriorClaim {
  claim_id: string;
  coil_id: Str;
  grade: Str;
  coating_class: Str;
  defect_code: Str;
  defect_narrative: Str;
  claim_date: Str;
  verdict: Str;
  approved_amount: Num;
}

/**
 * The customer-heat-risk row (jsonb from `to_jsonb(r)` over reference.customer_heat_risk).
 *
 * `high_risk` and `risk_reason` are the gold/synced fraud columns added by the parallel
 * fraud-tuning workstream (high_risk = risk_score >= 0.6 AND cluster_size >= 3). They flow
 * through the server's `to_jsonb(r)` context select automatically, so they are typed
 * OPTIONAL here and read defensively — a row predating those columns simply omits them,
 * and the cockpit shows the fraud chip only when `high_risk` is strictly true. The index
 * signature preserves the "render whatever columns exist" behavior of the context panel.
 */
export interface CustomerHeatRisk {
  customer_id?: Str;
  heat_no?: Str;
  cluster_id?: Str;
  cluster_size?: Num;
  risk_score?: Num;
  /** Gold/synced fraud flag: risk_score >= 0.6 AND cluster_size >= 3. May be absent. */
  high_risk?: boolean | null;
  /** Human-readable basis for the flag (e.g. "Fraud cluster: 4 claims…"). May be absent. */
  risk_reason?: Str;
  [key: string]: unknown;
}

/** Cockpit context: coil/heat, MTC, customer, and customer-heat risk (jsonb rows). */
export interface CockpitContext {
  heats_coils: Record<string, unknown> | null;
  mill_test_cert: Record<string, unknown> | null;
  customer: Record<string, unknown> | null;
  customer_heat_risk: CustomerHeatRisk | null;
}

/** The full cockpit detail payload (`GET /api/claims/:id`). */
export interface ClaimDetail {
  adjudication: Adjudication;
  decision_records: DecisionRecord[];
  context: CockpitContext;
  prior_claims: PriorClaim[];
}

// --- Finalize ----------------------------------------------------------------

export interface FinalizeBody {
  final_verdict: Verdict;
  final_disposition: string;
  approved_amount: number;
  override_reason?: string | null;
}

export type FinalizeResult =
  | { status: 'finalized'; adjudicationId: string; decidedBy: string; overrideFlag: boolean; eventId: string }
  | { status: 'already_final'; adjudicationId: string }
  | { status: 'not_found'; adjudicationId: string }
  | { status: 'invalid'; adjudicationId: string; errors: string[] };

// --- Source drill-through (read-only) ----------------------------------------

/**
 * The whitelisted underlying sources a cockpit evidence item can drill through to. Mirrors
 * the server's SOURCE_REGISTRY (server/sql.ts) exactly — a source not in this union is not
 * fetchable. Clause tables are identified by their persisted `citation_key`; the others by
 * their primary key (customer_heat_risk additionally by heat_no).
 */
export type SourceKind =
  | 'spec_clauses'
  | 'warranty_clauses'
  | 'prior_claims'
  | 'heats_coils'
  | 'mill_test_certs'
  | 'customers'
  | 'customer_heat_risk';

/** A drill-through request: which source, which row, and how to label the detail panel. */
export interface SourceTarget {
  source: SourceKind;
  /** The primary identifier (a scalar PK, or the composite citation_key for clauses). */
  id: string;
  /** Extra bound key values (e.g. `{ heat_no }` for customer_heat_risk). */
  extra?: Record<string, string>;
  /** Human-readable panel heading (e.g. "Warranty clause · coverage"). */
  title: string;
}

/** The fetched row detail (`GET /api/source/:source/:id`) — the ENTIRE row + provenance. */
export interface SourceRowDetail {
  source: SourceKind;
  /** Fully-qualified source table name shown as provenance (e.g. `public.spec_clauses`). */
  table: string;
  /** Every column of the underlying row, as returned by `SELECT *`. */
  row: Record<string, unknown>;
}

/** Business dashboard wiring (`GET /api/business/dashboard`). */
export interface BusinessDashboardConfig {
  genie_chat_alias: string;
  dashboard_id: Str;
  embed_url: Str;
  embeddable: boolean;
}

export interface ListFilters {
  claim_type?: string;
  customer_id?: string;
  verdict?: string;
  sort?: string;
  order?: 'asc' | 'desc';
  limit?: number;
  offset?: number;
}
