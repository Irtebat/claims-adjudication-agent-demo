-- Least-privilege Lakebase grants for the steel-claims-cockpit App service principal.
-- App SP client_id (= Postgres role name): d5309ee7-a8ea-499f-99d4-4ccbd8369d93
-- Role was auto-provisioned on app deploy (role_id dbrx-apps-d5309ee7-...; CAN_CONNECT_AND_CREATE).
-- Run as a DATABRICKS_SUPERUSER (irtebat.shaukat@databricks.com) against databricks_postgres.
--
-- Surface (from app/server/sql.ts + finalize.ts):
--   READS  (SELECT): public.claims, adjudications, adjudication_decision_records,
--                    spec_params, spec_clauses, warranty_terms, warranty_clauses,
--                    prior_claims; reference.heats_coils, mill_test_certs, customers,
--                    customer_heat_risk (synced tables).
--   WRITES: finalize UPDATEs public.adjudications, INSERTs public.adjudication_decision_records,
--           INSERTs public.outbox.

-- Schema usage (required for any table access under these schemas).
GRANT USAGE ON SCHEMA public TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT USAGE ON SCHEMA reference TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";

-- Read grants (public).
GRANT SELECT ON public.claims TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.adjudications TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.adjudication_decision_records TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.spec_params TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.spec_clauses TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.warranty_terms TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.warranty_clauses TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON public.prior_claims TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";

-- Read grants (reference synced tables the cockpit context query reads).
GRANT SELECT ON reference.heats_coils TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON reference.mill_test_certs TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON reference.customers TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT SELECT ON reference.customer_heat_risk TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";

-- Write grants for the finalize transaction.
GRANT INSERT, UPDATE ON public.adjudications TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT INSERT, UPDATE ON public.adjudication_decision_records TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
GRANT INSERT ON public.outbox TO "d5309ee7-a8ea-499f-99d4-4ccbd8369d93";
