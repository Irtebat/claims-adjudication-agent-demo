-- Least-privilege Lakebase grants for the steel-claims-cockpit App service principal.
-- App SP client_id (= Postgres role name): e8d1fa60-42b8-467c-8056-c4eb9ae947f0
-- Role was auto-provisioned on app deploy (role_id dbrx-apps-e8d1fa60-...; CAN_CONNECT_AND_CREATE).
-- Run as a DATABRICKS_SUPERUSER (irtebat.shaukat@databricks.com) against databricks_postgres.
--
-- Surface (from app/server/sql.ts + finalize.ts):
--   READS  (SELECT): public.claims, adjudications, adjudication_decision_records,
--                    spec_params, spec_clauses, warranty_terms, warranty_clauses,
--                    reference.heats_coils, mill_test_certs, customers,
--                    customer_heat_risk, prior_claims_corpus (synced tables).
--   WRITES: finalize UPDATEs public.adjudications, INSERTs public.adjudication_decision_records,
--           INSERTs public.outbox.

-- Schema usage (required for any table access under these schemas).
GRANT USAGE ON SCHEMA public TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT USAGE ON SCHEMA reference TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";

-- Read grants (public).
GRANT SELECT ON public.claims TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON public.adjudications TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON public.adjudication_decision_records TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON public.spec_params TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON public.spec_clauses TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON public.warranty_terms TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON public.warranty_clauses TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";

-- Read grants (reference synced tables the cockpit context query reads).
GRANT SELECT ON reference.heats_coils TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON reference.mill_test_certs TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON reference.customers TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON reference.customer_heat_risk TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT SELECT ON reference.prior_claims_corpus TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";

-- Write grants for the finalize transaction.
GRANT INSERT, UPDATE ON public.adjudications TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT INSERT, UPDATE ON public.adjudication_decision_records TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
GRANT INSERT ON public.outbox TO "e8d1fa60-42b8-467c-8056-c4eb9ae947f0";
