# app.json — readable rendering

> Generated from [`app.json`](app.json) (the raw capture). Synthetic demo data, workspace `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com`, captured 2026-10-13T07:53:49+00:00. Personal emails redacted; no tokens recorded.

**Purpose:** Databricks App steel-claims-cockpit: deployment status + authenticated HTTP smoke against the live app URL

## Captured steps

### 1. apps get

- Command: `databricks apps get steel-claims-cockpit --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T07:53:49+00:00 · status: `0`

```json
{
  "active_deployment": {
    "create_time": "2026-10-08T18:05:57Z",
    "creator": "<user>",
    "deployment_artifacts": {
      "source_code_path": "/Workspace/Users/e8d1fa60-42b8-467c-8056-c4eb9ae947f0/src/01f1c342e95719e0b4df3dec33824330"
    },
    "deployment_id": "01f1c342e95719e0b4df3dec33824330",
    "mode": "SNAPSHOT",
    "source_code_path": "/Workspace/Users/<user>/.bundle/steel-claims-cockpit/default/files",
    "status": {
      "message": "App started successfully",
      "state": "SUCCEEDED"
    },
    "update_time": "2026-10-08T18:06:54Z"
  },
  "app_status": {
    "message": "App has status: App is running",
    "state": "RUNNING"
  },
  "compute_size": "MEDIUM",
  "compute_status": {
    "message": "App compute is running.",
    "state": "ACTIVE"
  },
  "create_time": "2026-10-07T16:18:31Z",
  "creator": "<user>",
  "default_source_code_path": "/Workspace/Users/<user>/.bundle/steel-claims-cockpit/default/files",
  "description": "Steel warranty claims adjudication cockpit (adjuster + business user)",
  "effective_budget_policy_id": "b29cd2c3-3d18-3920-88dc-62a4bccb2682",
... (56 more lines — see the .json)
```

### 2. GET /api/whoami

- Command: `HTTP GET https://steel-claims-cockpit-7474646906749000.aws.databricksapps.com/api/whoami (Authorization: Bearer <user OAuth token from `databricks auth token --profile fe-bar-ir-2026`>)`
- Time: 2026-10-13T07:53:50+00:00 · status: `200`

```json
{
  "email": "<user>",
  "user": "77590582545175@7474646906749000",
  "roles": [
    "adjuster",
    "business_user"
  ],
  "defaultRole": "adjuster"
}
```

### 3. GET /api/queue

- Command: `HTTP GET https://steel-claims-cockpit-7474646906749000.aws.databricksapps.com/api/queue (Authorization: Bearer <user OAuth token from `databricks auth token --profile fe-bar-ir-2026`>)`
- Time: 2026-10-13T07:53:52+00:00 · status: `200`

```json
{
  "items": [
    {
      "adjudication_id": "ADJ-0c5218f14eef2431ff77",
      "claim_id": "CLM-DEMO-000099",
      "customer_id": "CUST-0197",
      "claim_type": "material_nonconformance",
      "claim_date": "2026-01-01T00:00:00.000Z",
      "defect_code": "MECH_TENSILE",
      "defect_narrative": "Identical edge failure across delivered coils; request full replacement urgently.",
      "claimed_tonnage": "10.000",
      "claimed_freight": "0.00",
      "verdict": "DENY",
      "recommended_verdict": "DENY",
      "recommended_disposition": "DUPLICATE",
      "approved_amount": "0.00",
      "claimed_amount": "9690.00",
      "confidence": "0.98",
      "duplicate_of_claim_id": "CLM-0000099",
      "fraud_cluster_id": "CLM-0000095",
      "supplier_attributable": false,
      "recommended_at": "2026-10-07T21:06:55.471Z",
      "high_risk": false,
      "risk_reason": null
    },
    {
      "adjudication_id": "ADJ-375828f202aef9cc84fa",
      "claim_id": "CLM-DEMO-000098",
      "customer_id": "CUST-0199",
      "claim_type": "material_nonconformance",
... (46 more lines — see the .json)
```

### 4. GET /api/claims/CLM-DEMO-000099 (cockpit)

- Command: `HTTP GET https://steel-claims-cockpit-7474646906749000.aws.databricksapps.com/api/claims/CLM-DEMO-000099?adjudication_id=ADJ-0c5218f14eef2431ff77 (Authorization: Bearer <user OAuth token from `databricks auth token --profile fe-bar-ir-2026`>)`
- Time: 2026-10-13T07:53:53+00:00 · status: `200`

```json
{
  "adjudication": {
    "adjudication_id": "ADJ-0c5218f14eef2431ff77",
    "claim_id": "CLM-DEMO-000099",
    "verdict": "DENY",
    "recommended_verdict": "DENY",
    "disposition": "DUPLICATE",
    "claimed_amount": "9690.00",
    "approved_amount": "0.00",
    "covered_tonnage": "10.000",
    "freight_covered": false,
    "supplier_attributable": false,
    "recovery_supplier_id": null,
    "defect_failure_mode_code": null,
    "override_flag": false,
    "decision_status": "RECOMMENDED",
    "rationale": "Authoritative duplicate screening identifies this claim as a duplicate of CLM-0000099; per rule, a duplicate must be denied as DUPLICATE and is never payable. Conformance also shows the material is in spec against the cited chemistry, dimensions, and mechanical clauses, which independently aligns with denial for a material nonconformance allegation. Coverage is within term, but duplicate status controls. No narrative conflict with structured fields and no retrieved written exclusion is triggered by the narrative.",
    "cited_clause_ids": [
      "ASTM A653 CS Type B/NA/DEMO-1990/chemistry",
      "ASTM A653 CS Type B/NA/DEMO-1990/dimensions",
      "ASTM A653 CS Type B/NA/DEMO-1990/mechanical"
    ],
    "finalized_at": null,
    "duplicate_of_claim_id": "CLM-0000099",
    "fraud_cluster_id": "CLM-0000095",
    "decided_by": null,
    "override_reason": null,
    "data_provenance": "agent_recommendation",
    "baseline_loaded_at": "2026-10-07T21:06:55.471Z",
    "recommended_disposition": "DUPLICATE",
... (293 more lines — see the .json)
```

### 5. GET /api/history

- Command: `HTTP GET https://steel-claims-cockpit-7474646906749000.aws.databricksapps.com/api/history?limit=5 (Authorization: Bearer <user OAuth token from `databricks auth token --profile fe-bar-ir-2026`>)`
- Time: 2026-10-13T07:53:55+00:00 · status: `200`

```json
{
  "items": [
    {
      "adjudication_id": "ADJ-1cc726b50ab680d4ad08",
      "claim_id": "CLM-DEMO-000007",
      "customer_id": "CUST-0119",
      "claim_type": "coating_warranty",
      "claim_date": "2026-01-01T00:00:00.000Z",
      "defect_code": "RED_RUST",
      "verdict": "APPROVE",
      "disposition": "CREDIT",
      "approved_amount": "14739.00",
      "claimed_amount": "14739.00",
      "override_flag": false,
      "override_reason": null,
      "decided_by": "<user>",
      "finalized_at": "2026-10-09T07:59:26.480Z",
      "recommended_verdict": "APPROVE",
      "recommended_disposition": "CREDIT"
    },
    {
      "adjudication_id": "ADJ-03cec259986537abef5b",
      "claim_id": "CLM-DEMO-000006",
      "customer_id": "CUST-0102",
      "claim_type": "coating_warranty",
      "claim_date": "2026-01-01T00:00:00.000Z",
      "defect_code": "RED_RUST",
      "verdict": "APPROVE",
      "disposition": "CREDIT",
      "approved_amount": "8360.00",
... (67 more lines — see the .json)
```

