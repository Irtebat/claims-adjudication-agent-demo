#!/usr/bin/env bash
set -euo pipefail

profile="fe-bar"
project="fe-bar-operational-plane"
branch="projects/${project}/branches/production"
database="databricks_postgres"
lakebase_catalog="fe_bar_operational"
storage_catalog="fe-bar-ir"
storage_schema="default"

# Roles that must retain SELECT on the reference.* synced tables. A delete+recreate
# makes the new table owned by a different role and drops prior grants — the app-SP
# losing SELECT on reference.customer_heat_risk 500'd the cockpit on the go-live run.
# Ids are documented in docs/evidence/app-deploy/grants.sql (app SP) and
# docs/evidence/serving-endpoint/README.md (serving SP); override via env if rotated.
app_sp_principal="${APP_SP_PRINCIPAL:-d5309ee7-a8ea-499f-99d4-4ccbd8369d93}"
serving_sp_principal="${SERVING_SP_PRINCIPAL:-47643eb1-dbd5-40a6-a51d-5da6b8e2da7a}"

create_sync() {
  local table="$1"
  # Comma-separated primary key column(s); the third arg overrides the source schema.
  local key="$2"
  local source_schema="${3:-silver}"
  local resource="synced_tables/${lakebase_catalog}.reference.${table}"
  local pk_json
  pk_json="$(printf '%s' "${key}" | awk -F, '{for(i=1;i<=NF;i++){printf "%s\"%s\"", (i>1?",":""), $i}}')"
  if databricks postgres get-synced-table "${resource}" \
    --profile "${profile}" -o json >/dev/null 2>&1; then
    echo "Synced table ${table} already exists; skipping."
    return
  fi
  databricks postgres create-synced-table "${lakebase_catalog}.reference.${table}" \
    --json "{\"spec\":{\"source_table_full_name\":\"fe-bar-ir.${source_schema}.${table}\",\"primary_key_columns\":[${pk_json}],\"scheduling_policy\":\"TRIGGERED\",\"branch\":\"${branch}\",\"postgres_database\":\"${database}\",\"create_database_objects_if_missing\":true,\"new_pipeline_spec\":{\"storage_catalog\":\"${storage_catalog}\",\"storage_schema\":\"${storage_schema}\"}}}" \
    --profile "${profile}" -o json
}

# spec_standards and coating_warranty_terms are no longer synced down: the policy
# corpus (structured params + citable clauses + embeddings) is authored directly
# into Lakebase by the policy intake (lakebase/src/policy_intake.py), so it is native
# operational data, not a UC serve-down.
create_sync heats_coils coil_id
create_sync mill_test_certs cert_id
create_sync customers customer_id
create_sync suppliers supplier_id
create_sync defect_codes defect_code
# Advisory customer/heat risk, produced by the offline fraud-graph job in gold and
# served down for the agent's get_customer_heat_risk tool (advisory only).
create_sync customer_heat_risk "customer_id,heat_no" gold

# Re-grant SELECT to the documented consumers (app-SP + serving-SP). Recreating a
# synced table drops its grants, so without this a re-sync silently breaks the app's
# reads until a manual GRANT is restored. Idempotent — safe to run every time.
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
  python "${script_dir}/regrant_synced_table_selects.py" \
  --profile "${profile}" \
  --app-principal "${app_sp_principal}" \
  --serving-principal "${serving_sp_principal}"
