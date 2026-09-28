-- Business-dashboard KPIs over governed gold (runs on the SQL warehouse, OBO).
-- Served by the analytics plugin (queryKey "business_kpis"); the authz guard makes
-- this a Business-User-only surface (Adjusters are denied the business dashboard).
SELECT
  count(*)                                                      AS decision_records,
  count(*) FILTER (WHERE recommended_verdict = 'APPROVE')       AS approved,
  count(*) FILTER (WHERE recommended_verdict = 'DENY')          AS denied,
  count(*) FILTER (WHERE recommended_verdict = 'PEND_INVESTIGATE') AS pending_investigation,
  count(*) FILTER (WHERE decided_by IS NOT NULL)                AS human_finalized,
  count(*) FILTER (WHERE override_reason IS NOT NULL)           AS overridden
FROM `fe-bar-ir`.gold.adjudication_decision_records
