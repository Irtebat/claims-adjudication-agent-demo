#!/usr/bin/env bash
set -euo pipefail

profile="fe-bar"
project="fe-bar-operational-plane"
branch="projects/${project}/branches/production"
database="databricks_postgres"
lakebase_catalog="fe_bar_operational"
storage_catalog="fe-bar-ir"
storage_schema="default"

create_sync() {
  local table="$1"
  local key="$2"
  local resource="synced_tables/${lakebase_catalog}.reference.${table}"
  if databricks postgres get-synced-table "${resource}" \
    --profile "${profile}" -o json >/dev/null 2>&1; then
    echo "Synced table ${table} already exists; skipping."
    return
  fi
  databricks postgres create-synced-table "${lakebase_catalog}.reference.${table}" \
    --json "{\"spec\":{\"source_table_full_name\":\"fe-bar-ir.silver.${table}\",\"primary_key_columns\":[\"${key}\"],\"scheduling_policy\":\"TRIGGERED\",\"branch\":\"${branch}\",\"postgres_database\":\"${database}\",\"create_database_objects_if_missing\":true,\"new_pipeline_spec\":{\"storage_catalog\":\"${storage_catalog}\",\"storage_schema\":\"${storage_schema}\"}}}" \
    --profile "${profile}" -o json
}

# spec_standards and coating_warranty_terms are no longer synced down: the policy
# corpus (structured params + citable clauses + embeddings) is authored directly
# into Lakebase by the policy intake (agent/src/policy_intake.py), so it is native
# operational data, not a UC serve-down.
create_sync heats_coils coil_id
create_sync mill_test_certs cert_id
create_sync customers customer_id
create_sync suppliers supplier_id
create_sync defect_codes defect_code
