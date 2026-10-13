# lakebase-and-pipeline.json — readable rendering

> Generated from [`lakebase-and-pipeline.json`](lakebase-and-pipeline.json) (the raw capture). Synthetic demo data, workspace `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com`, captured 2026-10-13T08:31:56+00:00. Personal emails redacted; no tokens recorded.

**Purpose:** Lakebase + Lakeflow: live OLTP row counts, synced-table states, steel-claims pipeline latest update, UC cdf/silver/gold counts

## Captured steps

### 1. list base tables in public/reference

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:31:56+00:00 · status: `0`

| table_schema | table_name |
|---|---|
| public | adjudication_decision_records |
| public | adjudications |
| public | claims |
| public | investigation_cases |
| public | outbox |
| public | settlements |
| public | spec_clauses |
| public | spec_params |
| public | supplier_recovery_cases |
| public | warranty_clauses |
| public | warranty_terms |
| reference | customer_heat_risk |
| reference | customers |
| reference | defect_codes |
| reference | heats_coils |
| reference | mill_test_certs |
| reference | prior_claims_corpus |
| reference | suppliers |

### 2. row counts, all public/reference base tables

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:03+00:00 · status: `0`

| tbl | n |
|---|---|
| public.adjudication_decision_records | 104 |
| public.adjudications | 5101 |
| public.claims | 5100 |
| public.investigation_cases | 0 |
| public.outbox | 3 |
| public.settlements | 3 |
| public.spec_clauses | 18 |
| public.spec_params | 6 |
| public.supplier_recovery_cases | 0 |
| public.warranty_clauses | 36 |
| public.warranty_terms | 12 |
| reference.customer_heat_risk | 4400 |
| reference.customers | 200 |
| reference.defect_codes | 3 |
| reference.heats_coils | 4500 |
| reference.mill_test_certs | 4500 |
| reference.prior_claims_corpus | 5003 |
| reference.suppliers | 12 |

### 3. adjudications by decision_status

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:03+00:00 · status: `0`

| decision_status | n |
|---|---|
| FINAL | 5003 |
| RECOMMENDED | 98 |

### 4. outbox publish state

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:04+00:00 · status: `0`

| event_type | n | published | latest_published |
|---|---|---|---|
| claim.adjudicated | 3 | 3 | 2026-10-09 08:00:49.894410+00:00 |

### 5. synced table customers

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.customers --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:04+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.customers",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-07T11:34:42Z",
        "delta_commit_version": 2
      },
      "sync_end_time": "2026-10-07T12:02:15.749072Z",
      "sync_start_time": "2026-10-07T12:02:04.174079Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/2dcf7835-289c-48e1-8b1f-b8ce831ca85b/updates/b5cc899a-76a1-453f-8c2e-13ae1e2edd60.",
    "pipeline_id": "2dcf7835-289c-48e1-8b1f-b8ce831ca85b",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.customers",
  "uid": "6a6fed50-0b5b-4bb2-a759-2c2be75d4a93"
}
```

### 6. synced table suppliers

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.suppliers --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:06+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.suppliers",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-07T11:35:03Z",
        "delta_commit_version": 2
      },
      "sync_end_time": "2026-10-07T12:02:21.474873Z",
      "sync_start_time": "2026-10-07T12:02:12.686891Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/64dc4c5c-f9a3-44d5-9563-badb3fadb50b/updates/8ea129dc-6d3c-4b22-87ad-41176c6efa89.",
    "pipeline_id": "64dc4c5c-f9a3-44d5-9563-badb3fadb50b",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.suppliers",
  "uid": "c28037ee-6e79-4be8-9024-7fa0bb59190e"
}
```

### 7. synced table defect_codes

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.defect_codes --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:08+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.defect_codes",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-07T11:35:19Z",
        "delta_commit_version": 2
      },
      "sync_end_time": "2026-10-07T12:02:32.760358Z",
      "sync_start_time": "2026-10-07T12:02:22.061042Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/ef044d0e-ef73-4c41-af77-c81f5e8cc703/updates/fff3c4b5-79f5-49d5-b883-cc72413be823.",
    "pipeline_id": "ef044d0e-ef73-4c41-af77-c81f5e8cc703",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.defect_codes",
  "uid": "4c0fb7cf-7ce7-4aa5-9bbf-a953bf5f63a6"
}
```

### 8. synced table heats_coils

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.heats_coils --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:09+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.heats_coils",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-07T11:35:30Z",
        "delta_commit_version": 2
      },
      "sync_end_time": "2026-10-07T12:01:58.161612Z",
      "sync_start_time": "2026-10-07T12:01:46.272942Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/d45b0aa9-2a68-4ff0-aa90-31648768b31e/updates/bfc020f6-1a9d-402d-95aa-10cd61f8dc42.",
    "pipeline_id": "d45b0aa9-2a68-4ff0-aa90-31648768b31e",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.heats_coils",
  "uid": "7c7b1d94-d31b-4647-b8f6-1528a30f4ed0"
}
```

### 9. synced table mill_test_certs

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.mill_test_certs --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:11+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.mill_test_certs",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-07T11:34:47Z",
        "delta_commit_version": 2
      },
      "sync_end_time": "2026-10-07T12:02:03.163900Z",
      "sync_start_time": "2026-10-07T12:01:53.751968Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/74412a42-2fd3-4304-b0da-9edc573155b3/updates/a97792b4-18d2-4022-9333-f64692716c8c.",
    "pipeline_id": "74412a42-2fd3-4304-b0da-9edc573155b3",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.mill_test_certs",
  "uid": "bceb3616-f4fd-47db-985a-df582f8f9cbc"
}
```

### 10. synced table customer_heat_risk

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.customer_heat_risk --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:13+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.customer_heat_risk",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-13T08:13:27Z",
        "delta_commit_version": 6
      },
      "sync_end_time": "2026-10-13T08:24:11.447215Z",
      "sync_start_time": "2026-10-13T08:24:10.425343Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/0129c55c-ff49-4c0c-b1c0-b1685c4eb545/updates/1fe27d2a-b02f-4ec7-a274-d26c3537fe14.",
    "pipeline_id": "0129c55c-ff49-4c0c-b1c0-b1685c4eb545",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.customer_heat_risk",
  "uid": "4a1c6422-5c53-4380-91ee-748298c6faa5"
}
```

### 11. synced table prior_claims_corpus

- Command: `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.prior_claims_corpus --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:15+00:00 · status: `0`

```json
{
  "name": "synced_tables/fe_bar_operational.reference.prior_claims_corpus",
  "status": {
    "detailed_state": "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE",
    "last_sync": {
      "delta_table_sync_info": {
        "delta_commit_time": "2026-10-13T08:19:38Z",
        "delta_commit_version": 6
      },
      "sync_end_time": "2026-10-13T08:25:40.416066Z",
      "sync_start_time": "2026-10-13T08:25:25.758114Z"
    },
    "message": "Synced table creation succeeded using Delta Live Tables: https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com#joblist/pipelines/28d0c102-1a35-4fff-b010-a3d561c6621f/updates/d39bc93c-3991-493a-89bf-57555445fb30.",
    "pipeline_id": "28d0c102-1a35-4fff-b010-a3d561c6621f",
    "project": "projects/fe-bar-operational-plane",
    "unity_catalog_provisioning_state": "ACTIVE"
  },
  "synced_table_id": "fe_bar_operational.reference.prior_claims_corpus",
  "uid": "29daf288-8319-4435-88f8-10c46f884739"
}
```

### 12. pipeline get steel-claims

- Command: `databricks pipelines get 736c9bbe-b9b0-4c8c-8478-54d0c6c0e578 --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:17+00:00 · status: `0`

```json
{
  "creator_user_name": "<user>",
  "effective_budget_policy_id": "b29cd2c3-3d18-3920-88dc-62a4bccb2682",
  "effective_publishing_mode": "DEFAULT_PUBLISHING_MODE",
  "effective_serverless_compute_id": "4df662d0-bd4b-3b2c-b9af-b2ab3c600df1",
  "last_modified": 1791371969046,
  "latest_updates": [
    {
      "creation_time": "2026-10-13T08:26:42.437Z",
      "state": "COMPLETED",
      "update_id": "002f10ae-a014-45b6-a992-23cc952159bb"
    },
    {
      "creation_time": "2026-10-13T08:08:48.552Z",
      "state": "COMPLETED",
      "update_id": "f0857233-2ca2-4a67-b06e-ab800661a437"
    },
    {
      "creation_time": "2026-10-07T11:27:36.364Z",
      "state": "COMPLETED",
      "update_id": "189fc19f-ac9e-4eee-a215-38b55ebc2910"
    },
    {
      "creation_time": "2026-10-07T11:24:29.847Z",
      "state": "FAILED",
      "update_id": "3b5e4749-a523-49e5-b0ea-d4b23b45218b"
    },
    {
      "creation_time": "2026-10-07T11:22:42.052Z",
      "state": "FAILED",
... (18 more lines — see the .json)
```

### 13. pipeline list-updates steel-claims (latest 3)

- Command: `databricks pipelines list-updates 736c9bbe-b9b0-4c8c-8478-54d0c6c0e578 --max-results 3 --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:19+00:00 · status: `0`

```json
{
  "next_page_token": "CAIQrOP5sJE0GiQxODlmYzE5Zi1hYzllLTRlZWUtYTIxNS0zOGI1NWViYzI5MTA=",
  "updates": [
    {
      "cause": "JOB_TASK",
      "creation_time": 1791880002437,
      "full_refresh": false,
      "pipeline_id": "736c9bbe-b9b0-4c8c-8478-54d0c6c0e578",
      "state": "COMPLETED",
      "update_id": "002f10ae-a014-45b6-a992-23cc952159bb",
      "validate_only": false
    },
    {
      "cause": "JOB_TASK",
      "creation_time": 1791878928552,
      "full_refresh": false,
      "pipeline_id": "736c9bbe-b9b0-4c8c-8478-54d0c6c0e578",
      "state": "COMPLETED",
      "update_id": "f0857233-2ca2-4a67-b06e-ab800661a437",
      "validate_only": false
    },
    {
      "cause": "RETRY_ON_FAILURE",
      "creation_time": 1791372456364,
      "full_refresh": false,
      "pipeline_id": "736c9bbe-b9b0-4c8c-8478-54d0c6c0e578",
      "state": "COMPLETED",
      "update_id": "189fc19f-ac9e-4eee-a215-38b55ebc2910",
      "validate_only": false
    }
... (2 more lines — see the .json)
```

### 14. UC row counts: cdf / silver / gold

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:20+00:00 · status: `0`

| tbl | n |
|---|---|
| silver.claims_history | 5100 |
| silver.adjudications_history | 5104 |
| silver.heats_coils | 4500 |
| gold.claims_current | 5100 |
| gold.adjudications_current | 5101 |
| gold.adjudication_decision_records | 104 |
| cdf.lb_claims_history | 5100 |
| cdf.lb_adjudications_history | 5107 |
| cdf.lb_adjudication_decision_records_history | 104 |
| cdf.lb_outbox_history | 9 |
| cdf.lb_settlements_history | 3 |
| cdf.lb_investigation_cases_history | 0 |
| cdf.lb_supplier_recovery_cases_history | 0 |
| silver.customers | 200 |
| silver.suppliers | 12 |
| silver.defect_codes | 3 |
| silver.mill_test_certs | 4500 |
| gold.gold_claim_adjudication_fact | 5101 |
| gold.gold_quality_kpis | 2413 |
| gold.gold_failure_mode_analytics | 2739 |
| gold.gold_fraud_cluster_analytics | 4403 |
| gold.gold_supplier_recovery_analytics | 6 |
| gold.gold_agent_human_alignment | 4 |
| gold.gold_retrieval_citation_kpis | 2 |
| gold.customer_heat_risk | 4400 |
| gold.prior_claims_corpus | 5003 |

