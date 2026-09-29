-- Additive analytics layer. Reference inputs are deduplicated at their natural keys
-- before joining so replayed silver snapshots cannot multiply the adjudication grain.
CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_claim_adjudication_fact
COMMENT 'One row per current adjudication, enriched for quality, value, supplier, fraud, and agent analytics'
AS
WITH
current_adjudications AS (
  SELECT * FROM `${claims.catalog}`.gold.adjudications_current
),
latest_decision_records AS (
  SELECT * EXCEPT (_rn)
  FROM (
    SELECT d.*,
      row_number() OVER (
        PARTITION BY adjudication_id
        ORDER BY record_version DESC, created_at DESC, idempotency_key DESC
      ) AS _rn
    FROM `${claims.catalog}`.gold.adjudication_decision_records d
  )
  WHERE _rn = 1
),
heats_coils AS (
  SELECT * EXCEPT (_rn)
  FROM (
    SELECT h.*, row_number() OVER (PARTITION BY coil_id ORDER BY ship_date DESC, prod_date DESC) AS _rn
    FROM `${claims.catalog}`.silver.heats_coils h
  )
  WHERE _rn = 1
),
customers AS (
  SELECT customer_id, customer_name, segment
  FROM (
    SELECT c.*, row_number() OVER (PARTITION BY customer_id ORDER BY customer_name, segment) AS _rn
    FROM `${claims.catalog}`.silver.customers c
  )
  WHERE _rn = 1
),
defect_codes AS (
  SELECT defect_code, description, process_step
  FROM (
    SELECT d.*, row_number() OVER (PARTITION BY defect_code ORDER BY description, process_step) AS _rn
    FROM `${claims.catalog}`.silver.defect_codes d
  )
  WHERE _rn = 1
),
suppliers AS (
  SELECT supplier_id, supplier_name, supplier_type
  FROM (
    SELECT s.*, row_number() OVER (PARTITION BY supplier_id ORDER BY supplier_name, supplier_type) AS _rn
    FROM `${claims.catalog}`.silver.suppliers s
  )
  WHERE _rn = 1
),
heat_risk AS (
  SELECT * EXCEPT (_rn)
  FROM (
    SELECT r.*, row_number() OVER (
      PARTITION BY customer_id, heat_no ORDER BY computed_at DESC, risk_score DESC
    ) AS _rn
    FROM `${claims.catalog}`.gold.customer_heat_risk r
  )
  WHERE _rn = 1
)
SELECT
  a.adjudication_id,
  c.claim_id,
  c.claim_date,
  c.claim_type,
  c.customer_id,
  cu.customer_name,
  cu.segment AS customer_segment,
  c.coil_id,
  h.heat_no,
  h.product_line AS product,
  h.grade,
  h.coating_class AS coating,
  h.region,
  h.line,
  h.prod_date,
  h.ship_date,
  h.shipped_tonnage,
  h.unit_price,
  c.claimed_tonnage,
  c.claimed_freight,
  c.defect_code,
  coalesce(a.defect_failure_mode_code, c.defect_code) AS failure_mode_code,
  dc.description AS defect_description,
  dc.process_step,
  a.verdict,
  a.disposition,
  a.decision_status,
  a.recommended_verdict,
  a.recommended_disposition,
  a.override_flag,
  a.claimed_amount,
  a.approved_amount,
  a.covered_tonnage,
  a.finalized_at,
  datediff(to_date(a.finalized_at), c.claim_date) AS cycle_time_days,
  a.supplier_attributable,
  a.recovery_supplier_id,
  rs.supplier_name AS recovery_supplier_name,
  rs.supplier_type AS recovery_supplier_type,
  a.fraud_cluster_id,
  coalesce(dr.flags.fraud_risk, false) AS fraud_risk_flag,
  coalesce(dr.over_claim_flag, dr.flags.over_claim, false) AS over_claim_flag,
  coalesce(dr.duplicate_flag, a.disposition = 'DUPLICATE') AS duplicate_flag,
  dr.conformance.conforms AS material_in_spec,
  dr.coverage.covered AS warranty_covered,
  dr.record_version AS decision_record_version,
  dr.recommended_verdict AS agent_recommended_verdict,
  dr.recommended_disposition AS agent_recommended_disposition,
  cast(dr.approved_amount AS decimal(18,2)) AS agent_recommended_amount,
  dr.agent_model_name,
  dr.agent_model_version,
  dr.prompt_version,
  dr.schema_version,
  dr.cited_clause_ids,
  dr.citations,
  dr.authorities_source_sha256,
  dr.mlflow_trace_id,
  hr.cluster_id AS graph_cluster_id,
  hr.cluster_size AS graph_cluster_size,
  hr.n_customers AS graph_cluster_customers,
  hr.risk_score AS customer_heat_risk_score,
  coalesce(hr.risk_score > 0, false) AS customer_heat_risky,
  -- Tuned fraud flag (risk_score >= 0.6 AND cluster_size >= 3) + its hover reason,
  -- surfaced so downstream analytics and the App read one consistent signal.
  coalesce(hr.high_risk, false) AS customer_heat_high_risk,
  hr.risk_reason AS customer_heat_risk_reason
FROM current_adjudications a
JOIN `${claims.catalog}`.gold.claims_current c USING (claim_id)
JOIN heats_coils h ON c.coil_id = h.coil_id
LEFT JOIN customers cu ON c.customer_id = cu.customer_id
LEFT JOIN defect_codes dc ON c.defect_code = dc.defect_code
LEFT JOIN latest_decision_records dr ON a.adjudication_id = dr.adjudication_id
LEFT JOIN suppliers rs ON a.recovery_supplier_id = rs.supplier_id
LEFT JOIN heat_risk hr ON c.customer_id = hr.customer_id AND h.heat_no = hr.heat_no;

CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_quality_kpis
COMMENT 'Daily quality outcomes with separate, governed value categories; cycle time is synthetic and expected to be nearly flat'
AS
SELECT
  claim_date,
  claim_type,
  region,
  product,
  verdict,
  disposition,
  count(*) AS claim_throughput,
  count_if(verdict = 'APPROVE') AS approval_count,
  count_if(verdict = 'DENY') AS denial_count,
  count_if(verdict = 'PEND') AS pend_count,
  count_if(verdict = 'APPROVE') / count(*)::double AS approval_rate,
  count_if(verdict = 'DENY') / count(*)::double AS denial_rate,
  count_if(verdict = 'PEND') / count(*)::double AS pend_rate,
  sum(claimed_amount) AS claimed_amount,
  sum(approved_amount) AS approved_amount,
  sum(CASE WHEN verdict = 'DENY' AND claim_type = 'material_nonconformance'
      AND coalesce(material_in_spec, true) THEN claimed_amount ELSE 0 END) AS in_spec_denial_amount,
  sum(CASE WHEN verdict = 'DENY' AND claim_type = 'coating_warranty'
      AND coalesce(warranty_covered, false) = false THEN claimed_amount ELSE 0 END) AS warranty_exclusion_denial_amount,
  sum(CASE WHEN duplicate_flag THEN claimed_amount ELSE 0 END) AS duplicate_blocked_amount,
  sum(CASE WHEN verdict = 'APPROVE' AND approved_amount < claimed_amount
      THEN claimed_amount - approved_amount ELSE 0 END) AS over_claim_reduction_amount,
  avg(cycle_time_days) AS average_cycle_time_days
FROM `${claims.catalog}`.gold.gold_claim_adjudication_fact
GROUP BY claim_date, claim_type, region, product, verdict, disposition;

CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_failure_mode_analytics
COMMENT 'Monthly failure-mode Pareto and recurrence by production dimensions and heat'
AS
SELECT
  date_trunc('MONTH', claim_date) AS period,
  failure_mode_code,
  defect_description,
  process_step,
  line,
  grade,
  coating,
  heat_no,
  count(*) AS claim_count,
  count(DISTINCT customer_id) AS affected_customers,
  sum(claimed_tonnage) AS affected_tonnage,
  sum(claimed_amount) AS affected_claimed_amount,
  sum(approved_amount) AS affected_approved_amount,
  count(*) > 1 OR count_if(verdict = 'PEND' OR fraud_risk_flag) > 0 AS quarantine_candidate
FROM `${claims.catalog}`.gold.gold_claim_adjudication_fact
GROUP BY date_trunc('MONTH', claim_date), failure_mode_code, defect_description,
  process_step, line, grade, coating, heat_no;

CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_supplier_recovery_analytics
COMMENT 'Supplier-attributable adjudication value by recovery supplier and production dimensions; workflow backlog is intentionally excluded'
AS
SELECT
  recovery_supplier_id AS supplier_id,
  recovery_supplier_name AS supplier,
  recovery_supplier_type AS supplier_type,
  product,
  line,
  count_if(supplier_attributable) AS supplier_attributable_count,
  sum(CASE WHEN supplier_attributable THEN approved_amount ELSE 0 END) AS supplier_attributable_amount,
  sum(CASE WHEN supplier_attributable THEN claimed_amount ELSE 0 END) AS supplier_attributable_claimed_amount
FROM `${claims.catalog}`.gold.gold_claim_adjudication_fact
WHERE supplier_attributable
GROUP BY recovery_supplier_id, recovery_supplier_name, recovery_supplier_type, product, line;

CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_fraud_cluster_analytics
COMMENT 'Fraud and graph-risk incidence by cluster, customer, and heat'
AS
SELECT
  coalesce(fraud_cluster_id, graph_cluster_id, 'UNCLUSTERED') AS cluster_id,
  customer_id,
  customer_name,
  heat_no,
  count(*) AS claim_count,
  count_if(fraud_risk_flag OR customer_heat_risky OR fraud_cluster_id IS NOT NULL) AS risky_claim_count,
  sum(CASE WHEN fraud_risk_flag OR customer_heat_risky OR fraud_cluster_id IS NOT NULL
      THEN claimed_amount ELSE 0 END) AS risky_claimed_amount,
  count_if(verdict = 'PEND') / count(*)::double AS pend_rate,
  max(customer_heat_risk_score) AS customer_heat_risk_score
FROM `${claims.catalog}`.gold.gold_claim_adjudication_fact
GROUP BY coalesce(fraud_cluster_id, graph_cluster_id, 'UNCLUSTERED'),
  customer_id, customer_name, heat_no;

CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_agent_human_alignment
COMMENT 'Agent recommendation and final adjudication alignment by governed model, prompt, schema, and claim type'
AS
SELECT
  coalesce(agent_model_name, 'historical') AS model_name,
  coalesce(agent_model_version, 'historical') AS model_version,
  coalesce(prompt_version, 'historical') AS prompt_version,
  coalesce(schema_version, 'historical') AS schema_version,
  claim_type,
  count(*) AS adjudication_count,
  count_if(coalesce(agent_recommended_verdict, recommended_verdict) = verdict) AS verdict_agreement_count,
  count_if(coalesce(agent_recommended_verdict, recommended_verdict) = verdict) / count(*)::double AS verdict_agreement_rate,
  count_if(override_flag OR coalesce(agent_recommended_verdict, recommended_verdict) <> verdict) / count(*)::double AS override_rate,
  count_if(coalesce(agent_recommended_disposition, recommended_disposition, disposition) = disposition) / count(*)::double AS disposition_agreement_rate,
  avg(abs(coalesce(agent_recommended_amount, approved_amount) - approved_amount)) AS average_amount_delta
FROM `${claims.catalog}`.gold.gold_claim_adjudication_fact
GROUP BY coalesce(agent_model_name, 'historical'), coalesce(agent_model_version, 'historical'),
  coalesce(prompt_version, 'historical'), coalesce(schema_version, 'historical'), claim_type;

CREATE OR REFRESH MATERIALIZED VIEW `${claims.catalog}`.gold.gold_retrieval_citation_kpis
COMMENT 'Citation, authority-hash, and trace coverage for persisted agent decision records'
AS
SELECT
  agent_model_name AS model_name,
  agent_model_version AS model_version,
  prompt_version,
  claim_type,
  count(*) AS decision_record_count,
  count_if(size(cited_clause_ids) > 0) / count(*)::double AS citation_coverage,
  avg(coalesce(size(cited_clause_ids), 0)) AS average_citation_count,
  count_if(authorities_source_sha256 IS NOT NULL AND authorities_source_sha256 <> '') / count(*)::double AS authoritative_hash_coverage,
  count_if(mlflow_trace_id IS NOT NULL AND mlflow_trace_id <> '') / count(*)::double AS trace_coverage
FROM `${claims.catalog}`.gold.gold_claim_adjudication_fact
WHERE decision_record_version IS NOT NULL
GROUP BY agent_model_name, agent_model_version, prompt_version, claim_type;
