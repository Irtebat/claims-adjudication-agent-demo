# serving.json — readable rendering

> Generated from [`serving.json`](serving.json) (the raw capture). Synthetic demo data, workspace `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com`, captured 2026-10-13T07:56:52+00:00. Personal emails redacted; no tokens recorded.

**Purpose:** Model serving: claims adjudication agent endpoint state + one live invocation with custom_inputs.persist=false (no Lakebase write)

## Summary

```json
{
  "claim_id": "CLM-0000002",
  "output_text": [
    [
      {
        "annotations": [],
        "text": "Recommendation for claim CLM-0000002: APPROVE / CREDIT (approved 9113.59). Deterministic verdict APPROVE; invariant violations: none.",
        "type": "output_text"
      }
    ]
  ],
  "recommended_verdict": "APPROVE",
  "recommended_disposition": "CREDIT",
  "deterministic": {
    "approved_amount": 9113.59,
    "disposition": "CREDIT",
    "disposition_rule": "R7_credit",
    "duplicate_of_claim_id": null,
    "eligible": true,
    "reason": "covered",
    "settlement_authority_amount": 9113.59,
    "verdict": "APPROVE"
  },
  "invariant_violations": [],
  "cited_clause_ids": [
    "prepainted/NA/V1/exclusions",
    "prepainted/NA/V1/coverage",
    "prepainted/NA/V1/proration"
  ],
  "llm_used": true,
  "genie_consultations_count": 0,
  "write_result": {
    "persisted": false
  }
}
```

## Captured steps

### 1. serving-endpoints get

- Command: `databricks serving-endpoints get agents_fe-bar-ir-default-claims_adjudication_agent --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T07:56:52+00:00 · status: `0`

```json
{
  "ai_gateway": {
    "inference_table_config": {
      "catalog_name": "fe-bar-ir",
      "enabled": true,
      "schema_name": "default",
      "table_name_prefix": "claims_adjudication_agent_3"
    },
    "usage_tracking_config": {
      "enabled": false
    }
  },
  "config": {
    "config_version": 2,
    "served_entities": [
      {
        "creation_timestamp": 1791455286000,
        "creator": "<user>",
        "entity_name": "fe-bar-ir.default.claims_adjudication_agent",
        "entity_version": "5",
        "environment_vars": {
          "APP_SP_CLIENT_ID": "{{secrets/claims-agent/app-sp-client-id}}",
          "APP_SP_CLIENT_SECRET": "{{secrets/claims-agent/app-sp-client-secret}}",
          "DATABRICKS_HOST": "https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com",
          "ENABLE_LANGCHAIN_STREAMING": "true",
          "ENABLE_MLFLOW_TRACING": "true",
          "LAKEBASE_DB_USER": "{{secrets/claims-agent/lakebase-db-user}}",
          "RETURN_REQUEST_ID_IN_RESPONSE": "true"
        },
        "name": "fe-bar-ir-default-claims_adjudication_agent_5",
... (109 more lines — see the .json)
```

### 2. model version alias @prod

- Command: `databricks model-versions get-by-alias fe-bar-ir.default.claims_adjudication_agent prod --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T07:56:54+00:00 · status: `0`

```json
{
  "catalog_name": "fe-bar-ir",
  "created_at": 1791452209454,
  "created_by": "<user>",
  "id": "ff4f8fff-989b-4647-8ff0-778b4822bd3b",
  "metastore_id": "85d5656c-5d67-4718-93ed-c2d0ce7b92d5",
  "model_name": "claims_adjudication_agent",
  "run_id": "7cad2b5e9bcc4728bdc191d45f04a9ec",
  "run_workspace_id": 7474646906749000,
  "schema_name": "default",
  "source": "models:/m-b93e146611f44b2e8cdd09ed477de4d1",
  "status": "READY",
  "storage_location": "s3://fe-bar-ir-bucket/models/0207b1ab-af09-4fd1-baff-b691c8e95422/versions/ff4f8fff-989b-4647-8ff0-778b4822bd3b",
  "updated_at": 1791452222864,
  "updated_by": "<user>",
  "version": 6
}
```

### 3. pick a queued (RECOMMENDED) APPROVE claim as the representative input

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T07:57:01+00:00 · status: `0`

| claim_id | coil_id | customer_id | claim_type | claim_date | install_date | environment | installation | coast_distance_km | defect_code | defect_narrative | claimed_tonnage | claimed_freight | data_provenance | baseline_loaded_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLM-0000002 | COIL-0000002 | CUST-0034 | coating_warranty | 2026-01-01 | 2014-01-31 | inland | ventilated | 25.0 | RED_RUST | Premature red rust and perforation observed on installed roofing. | 24.000 | 0.00 | synthetic_wave_2_baseline | 2026-10-07 11:09:14.804442+00:00 |

### 4. invoke (persist=false)

- Command: `databricks api post /serving-endpoints/agents_fe-bar-ir-default-claims_adjudication_agent/invocations --json <request_body> --profile fe-bar-ir-2026`
- Time: 2026-10-13T07:57:06+00:00 · status: `0`

```json
{
  "custom_outputs": {
    "adjudication_id": "ADJ-c5b5328de3983e7a142b",
    "cited_clause_ids": [
      "prepainted/NA/V1/exclusions",
      "prepainted/NA/V1/coverage",
      "prepainted/NA/V1/proration"
    ],
    "deterministic": {
      "approved_amount": 9113.59,
      "disposition": "CREDIT",
      "disposition_rule": "R7_credit",
      "duplicate_of_claim_id": null,
      "eligible": true,
      "reason": "covered",
      "settlement_authority_amount": 9113.59,
      "verdict": "APPROVE"
    },
    "genie_consultations": [],
    "invariant_violations": [],
    "llm_used": true,
    "recommendation": {
      "approved_amount": 9113.59,
      "cited_clause_ids": [
        "prepainted/NA/V1/coverage",
        "prepainted/NA/V1/proration",
        "prepainted/NA/V1/exclusions"
      ],
      "confidence": 0.78,
      "disposition_agrees_with_rule": true,
... (67 more lines — see the .json)
```

### 5. reasoning endpoint + agent version recorded on the most recent persisted decision records (read-only)

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T07:57:24+00:00 · status: `0`

| claim_id | record_version | reasoning_endpoint | agent_model_name | agent_model_version | prompt_version | schema_version | created_at |
|---|---|---|---|---|---|---|---|
| CLM-DEMO-000007 | 2 | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | None | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | 2026-10-09 07:59:26.480290+00:00 |
| CLM-DEMO-000006 | 2 | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | None | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | 2026-10-09 07:30:49.980308+00:00 |
| CLM-DEMO-000005 | 2 | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | None | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | 2026-10-08 16:32:05.389204+00:00 |
| CLM-DEMO-000099 | 1 | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | None | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | 2026-10-07 21:06:55.471286+00:00 |
| CLM-DEMO-000098 | 1 | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | None | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | 2026-10-07 21:06:44.481194+00:00 |

### 6. reasoning endpoints across all persisted decision records (read-only)

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T07:57:24+00:00 · status: `0`

| reasoning_endpoint | agent_model_version | records | latest |
|---|---|---|---|
| system.ai.gpt-5-4 | None | 104 | 2026-10-09 07:59:26.480290+00:00 |

