# dashboard.json — readable rendering

> Generated from [`dashboard.json`](dashboard.json) (the raw capture). Synthetic demo data, workspace `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com`, captured 2026-10-13T08:31:57+00:00. Personal emails redacted; no tokens recorded.

**Purpose:** AI/BI dashboard: deployed definition/status + every dataset query executed live on the SQL warehouse

## Summary

### Governed executive KPIs — `SUCCEEDED`, 1 rows

| claim_count | approval_rate | denial_rate | pend_rate | claimed_amount | approved_amount | in_spec_denial_amount | warranty_exclusion_denial_amount | duplicate_blocked_amount | over_claim_reduction_amount |
|---|---|---|---|---|---|---|---|---|---|
| 5101 | 0.4971574201137032 | 0.4526563418937463 | 0.05018623799255048 | 86278451.00 | 39136448.76 | 19711064.00 | 15935474.00 | 8620568.00 | 6795228.24 |

### Cycle time — `SUCCEEDED`, 1 rows

| average_cycle_time_days |
|---|
| 2.174166666666667 |

### Failure mode Pareto — `SUCCEEDED`, 9 rows

| failure_mode_code | defect_description | line | grade | coating | claims | affected_tonnage | affected_claimed_amount |
|---|---|---|---|---|---|---|---|
| MECH_TENSILE | Mechanical tensile deviation | LINE-1 | ASTM A653 CS Type B | G90 | 1018 | 11200.000 | 10816620.00 |

### Quarantine candidates — `SUCCEEDED`, 50 rows

| heat_no | line | grade | coating | failure_mode_code | recurrence | affected_tonnage | affected_claimed_amount |
|---|---|---|---|---|---|---|---|
| HEAT-000901 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | MECH_TENSILE | 4 | 96.000 | 88944.00 |

### Approved value by disposition — `SUCCEEDED`, 36 rows

| disposition | product | region | approved_amount |
|---|---|---|---|
| CREDIT | prepainted | NA | 5558473.86 |

### Supplier attributable recovery opportunity — `SUCCEEDED`, 2 rows

| supplier | supplier_attributable_amount | claim_count |
|---|---|---|
| Bowman-Bradshaw | 11775285.00 | 750 |

### Fraud cluster risk — `SUCCEEDED`, 30 rows

| cluster_id | customer_name | heat_no | risky_claims | risky_claimed_amount | risk_score |
|---|---|---|---|---|---|
| CLM-0000005 | Huber Group | HEAT-000001 | 4 | 86208.00 | 1.0 |

### Agent-human alignment subset — `SUCCEEDED`, 1 rows

| adjudications | verdict_agreement_rate | override_rate |
|---|---|---|
| 5101 | 0.9988237600470496 | 0.001176239952950402 |

### Citation coverage subset — `SUCCEEDED`, 1 rows

| decision_records | citation_coverage |
|---|---|
| 101 | 0.3465346534653465 |

## Captured steps

### 1. lakeview get (draft)

- Command: `databricks lakeview get 01f1c269ddc4143aa9bf584f267bd10b --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:31:57+00:00 · status: `0`

```json
{
  "create_time": "2026-10-07T16:12:17.360Z",
  "dashboard_id": "01f1c269ddc4143aa9bf584f267bd10b",
  "display_name": "Steel Quality Claims Analytics",
  "etag": "-1629398363",
  "lifecycle_state": "ACTIVE",
  "parent_path": "/Users/<user>/.bundle/steel-claims-dashboards/prod/resources",
  "path": "/Users/<user>/.bundle/steel-claims-dashboards/prod/resources/Steel Quality Claims Analytics.lvdash.json",
  "update_time": "2026-10-07T16:12:17.545Z",
  "warehouse_id": "a323b5700ae25d85",
  "serialized_dashboard_summary": {
    "datasets": [
      "executive_kpis",
      "cycle_time",
      "quality_pareto",
      "quarantine",
      "finance",
      "supplier",
      "fraud",
      "alignment",
      "citations"
    ],
    "pages": [
      {
        "name": "executive",
        "displayName": "Executive COPQ",
        "widgets": 8
      },
      {
        "name": "quality",
... (16 more lines — see the .json)
```

### 2. lakeview get-published

- Command: `databricks lakeview get-published 01f1c269ddc4143aa9bf584f267bd10b --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:31:58+00:00 · status: `0`

```json
{
  "display_name": "Steel Quality Claims Analytics",
  "embed_credentials": false,
  "revision_create_time": "2026-10-07T16:12:18.003Z",
  "warehouse_id": "a323b5700ae25d85"
}
```

### 3. dataset executive_kpis

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:00+00:00 · status: `0`

| claim_count | approval_rate | denial_rate | pend_rate | claimed_amount | approved_amount | in_spec_denial_amount | warranty_exclusion_denial_amount | duplicate_blocked_amount | over_claim_reduction_amount |
|---|---|---|---|---|---|---|---|---|---|
| 5101 | 0.4971574201137032 | 0.4526563418937463 | 0.05018623799255048 | 86278451.00 | 39136448.76 | 19711064.00 | 15935474.00 | 8620568.00 | 6795228.24 |

### 4. dataset cycle_time

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:05+00:00 · status: `0`

| average_cycle_time_days |
|---|
| 2.174166666666667 |

### 5. dataset quality_pareto

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:10+00:00 · status: `0`

| failure_mode_code | defect_description | line | grade | coating | claims | affected_tonnage | affected_claimed_amount |
|---|---|---|---|---|---|---|---|
| MECH_TENSILE | Mechanical tensile deviation | LINE-1 | ASTM A653 CS Type B | G90 | 1018 | 11200.000 | 10816620.00 |
| MECH_TENSILE | Mechanical tensile deviation | LINE-2 | ASTM A792 AZ50 | AZ50 | 1014 | 18972.000 | 18014577.00 |
| MECH_TENSILE | Mechanical tensile deviation | LINE-3 | EN 10346 DX51D+Z275 | Z275 | 1013 | 26760.000 | 25201536.00 |
| RED_RUST | Field corrosion/perforation | LINE-3 | EN 10346 DX51D+Z275 | Z275 | 431 | 10344.000 | 9533088.00 |
| RED_RUST | Field corrosion/perforation | LINE-2 | ASTM A792 AZ50 | AZ50 | 430 | 7310.000 | 6745345.00 |
| RED_RUST | Field corrosion/perforation | LINE-1 | ASTM A653 CS Type B | G90 | 430 | 4300.000 | 3956700.00 |
| COATING_VOID | Coating adhesion/voids | LINE-2 | ASTM A792 AZ50 | AZ50 | 255 | 4335.000 | 4002565.00 |
| COATING_VOID | Coating adhesion/voids | LINE-3 | EN 10346 DX51D+Z275 | Z275 | 255 | 6120.000 | 5652720.00 |
| COATING_VOID | Coating adhesion/voids | LINE-1 | ASTM A653 CS Type B | G90 | 255 | 2550.000 | 2355300.00 |

### 6. dataset quarantine

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:15+00:00 · status: `0`

| heat_no | line | grade | coating | failure_mode_code | recurrence | affected_tonnage | affected_claimed_amount |
|---|---|---|---|---|---|---|---|
| HEAT-000901 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | MECH_TENSILE | 4 | 96.000 | 88944.00 |
| HEAT-000800 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | RED_RUST | 4 | 96.000 | 93840.00 |
| HEAT-000340 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | RED_RUST | 4 | 96.000 | 110064.00 |
| HEAT-000221 | LINE-2 | ASTM A792 AZ50 | AZ50 | MECH_TENSILE | 4 | 68.000 | 57902.00 |
| HEAT-000541 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | MECH_TENSILE | 4 | 96.000 | 91344.00 |
| HEAT-000161 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | MECH_TENSILE | 4 | 96.000 | 89520.00 |
| HEAT-000261 | LINE-1 | ASTM A653 CS Type B | G90 | MECH_TENSILE | 4 | 40.000 | 40060.00 |
| HEAT-000681 | LINE-2 | ASTM A792 AZ50 | AZ50 | MECH_TENSILE | 4 | 68.000 | 65110.00 |
| HEAT-000400 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | RED_RUST | 4 | 96.000 | 105264.00 |
| HEAT-000281 | LINE-3 | EN 10346 DX51D+Z275 | Z275 | MECH_TENSILE | 4 | 96.000 | 79920.00 |

### 7. dataset finance

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:19+00:00 · status: `0`

| disposition | product | region | approved_amount |
|---|---|---|---|
| CREDIT | prepainted | NA | 5558473.86 |
| REWORK | prepainted | NA | 4564000.00 |
| REPLACEMENT | prepainted | NA | 4089984.00 |
| CREDIT | galvalume | NA | 3956463.83 |
| REWORK | galvalume | NA | 3253177.00 |
| REPLACEMENT | galvalume | NA | 2893383.00 |
| CREDIT | galvanized | NA | 2318453.88 |
| REWORK | galvanized | NA | 1954190.00 |
| REPLACEMENT | galvanized | NA | 1688350.00 |
| CREDIT | prepainted | EU | 1563613.34 |

### 8. dataset supplier

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:23+00:00 · status: `0`

| supplier | supplier_attributable_amount | claim_count |
|---|---|---|
| Bowman-Bradshaw | 11775285.00 | 750 |
| Unassigned | 362524.00 | 61 |

### 9. dataset fraud

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:28+00:00 · status: `0`

| cluster_id | customer_name | heat_no | risky_claims | risky_claimed_amount | risk_score |
|---|---|---|---|---|---|
| CLM-0000005 | Huber Group | HEAT-000001 | 4 | 86208.00 | 1.0 |
| CLM-0000000 | Ford-Price | HEAT-000000 | 5 | 85440.00 | 1.0 |
| CLM-0000065 | Russell LLC | HEAT-000013 | 2 | 78057.60 | 0.75 |
| CLM-0000005 | Gaines PLC | HEAT-000001 | 4 | 77280.00 | 1.0 |
| CLM-0000065 | Vaughn Ltd | HEAT-000013 | 2 | 71808.00 | 0.75 |
| CLM-0000075 | Meyer-Thompson | HEAT-000015 | 2 | 59846.40 | 0.75 |
| CLM-0000070 | Foley-Diaz | HEAT-000014 | 2 | 59292.00 | 0.75 |
| CLM-0000005 | Harris, Jackson and Hebert | HEAT-000001 | 4 | 58956.00 | 1.0 |
| CLM-0001505 | Owens, Norris and Bryan | HEAT-000301 | 2 | 57504.00 | 0.75 |
| CLM-0004805 | Duran, Cortez and Gibson | HEAT-000961 | 2 | 57504.00 | 0.75 |

### 10. dataset alignment

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:31+00:00 · status: `0`

| adjudications | verdict_agreement_rate | override_rate |
|---|---|---|
| 5101 | 0.9988237600470496 | 0.001176239952950402 |

### 11. dataset citations

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85, catalog=fe-bar-ir, schema=gold)`
- Time: 2026-10-13T08:32:35+00:00 · status: `0`

| decision_records | citation_coverage |
|---|---|
| 101 | 0.3465346534653465 |

