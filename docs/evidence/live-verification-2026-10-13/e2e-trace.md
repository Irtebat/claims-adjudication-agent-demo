# e2e-trace.json — readable rendering

> Generated from [`e2e-trace.json`](e2e-trace.json) (the raw capture). Synthetic demo data, workspace `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com`, captured 2026-10-13T08:31:57+00:00. Personal emails redacted; no tokens recorded.

**Purpose:** End-to-end trace of CLM-DEMO-000006 across every stage (read-only)

## Captured steps

### 1. 1 Lakebase claim row

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:31:57+00:00 · status: `0`

| claim_id | coil_id | customer_id | claim_type | claim_date | install_date | environment | installation | coast_distance_km | defect_code | defect_narrative | claimed_tonnage | claimed_freight | data_provenance | baseline_loaded_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLM-DEMO-000006 | COIL-0000006 | CUST-0102 | coating_warranty | 2026-01-01 | 2014-01-31 | inland | ventilated | 25.0 | RED_RUST | Premature red rust and perforation observed on installed roofing. | 10.000 | 0.00 | synthetic_demo_backlog | 2026-10-07 20:45:23.089966+00:00 |

### 2. 2 Lakebase adjudication (FINAL)

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:02+00:00 · status: `0`

| adjudication_id | claim_id | decision_status | recommended_verdict | verdict | recommended_disposition | disposition | claimed_amount | approved_amount | cited_clause_ids | override_flag | decided_by | recommended_at | finalized_at | decision_record_version |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADJ-03cec259986537abef5b | CLM-DEMO-000006 | FINAL | APPROVE | APPROVE | CREDIT | CREDIT | 8360.00 | 8360.00 | ['galvanized/NA/V2/exclusions', 'galvanized/NA/V2/coverage', 'galvanized/NA/V2/proration'] | False | <user> | 2026-10-07 20:47:29.005854+00:00 | 2026-10-09 07:30:49.980308 | 1 |

### 3. 3 Lakebase decision records

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:03+00:00 · status: `0`

| adjudication_id | record_version | deterministic_verdict | recommended_verdict | approved_amount | cited_clause_ids | invariant_violations | reasoning_endpoint | agent_model_name | prompt_version | schema_version | decided_by | created_at | citations_count |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADJ-03cec259986537abef5b | 1 | APPROVE | APPROVE | 8360 | ['galvanized/NA/V2/exclusions', 'galvanized/NA/V2/coverage', 'galvanized/NA/V2/proration'] | [] | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | None | 2026-10-07 20:47:29.005854+00:00 | 3 |
| ADJ-03cec259986537abef5b | 2 | APPROVE | APPROVE | 8360 | ['galvanized/NA/V2/exclusions', 'galvanized/NA/V2/coverage', 'galvanized/NA/V2/proration'] | [] | system.ai.gpt-5-4 | fe-bar-ir.default.claims_adjudication_agent | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | <user> | 2026-10-09 07:30:49.980308+00:00 | 3 |

### 4. 4 Lakebase outbox event (published_at set by relay)

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:03+00:00 · status: `0`

| event_id | aggregate_id | event_type | created_at | published_at | payload |
|---|---|---|---|---|---|
| adj-ADJ-03cec259986537abef5b | CLM-DEMO-000006 | claim.adjudicated | 2026-10-09 07:30:49.980308+00:00 | 2026-10-09 07:39:23.801469+00:00 | {'verdict': 'APPROVE', 'claim_id': 'CLM-DEMO-000006', 'event_id': 'adj-ADJ-03cec259986537abef5b', 'event_type': 'claim.adjudicated', 'disposition': 'CREDIT', 'finalized_at': '2026-10-09T07:30:50.010Z', 'schema_version': 'claim-event/v1', 'adjudication_id': 'ADJ-03cec259986537abef5b', 'approved_amount': 8360, 'idempotency_key': '03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8', 'recommended_verdict': 'APPROVE', 'recovery_supplier_id': None, 'duplicate_of_claim_id': None, 'supplier_attributable': True} |

### 5. 5 Lakebase settlement (written by settlement consumer)

- Command: `psycopg (read-only SELECT) -> Lakebase projects/fe-bar-operational-plane/branches/production, db databricks_postgres, OAuth credential from `databricks postgres generate-database-credential``
- Time: 2026-10-13T08:32:03+00:00 · status: `0`

| settlement_id | claim_id | status | amount | settled_at | created_at |
|---|---|---|---|---|---|
| STL-CLM-DEMO-000006 | CLM-DEMO-000006 | PENDING_PAYMENT | 8360.00 | None | 2026-10-09 07:40:37.334799+00:00 |

### 6. 6a UC native CDF: cdf.lb_claims_history

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:04+00:00 · status: `0`

| _pg_change_type | _pg_lsn | _pg_xid | _timestamp | _sort_by | claim_id | coil_id | customer_id | claim_type | claim_date | install_date | environment | installation | coast_distance_km | defect_code | defect_narrative | claimed_tonnage | claimed_freight | data_provenance | baseline_loaded_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| insert | 113525368 | 28685 | 2026-10-07T20:45:27.075 | 5006 | CLM-DEMO-000006 | COIL-0000006 | CUST-0102 | coating_warranty | 2026-01-01 | 2014-01-31 | inland | ventilated | 25.0 | RED_RUST | Premature red rust and perforation observed on installed roofing. | 10.000 | 0.00 | synthetic_demo_backlog | 2026-10-07T20:45:23.089Z |

### 7. 6b UC native CDF: cdf.lb_adjudications_history

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:09+00:00 · status: `0`

| _pg_change_type | _pg_lsn | _pg_xid | _timestamp | _sort_by | adjudication_id | claim_id | verdict | recommended_verdict | disposition | claimed_amount | approved_amount | covered_tonnage | freight_covered | supplier_attributable | recovery_supplier_id | defect_failure_mode_code | override_flag | decision_status | rationale | cited_clause_ids | finalized_at | duplicate_of_claim_id | fraud_cluster_id | decided_by | override_reason | data_provenance | baseline_loaded_at | recommended_disposition | confidence | flags | advisory_risk | precedent | idempotency_key | decision_record_version | recommended_at |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| update_preimage | 128773400 | 137223 | 2026-10-09T07:30:54.667 | 5103 | ADJ-03cec259986537abef5b | CLM-DEMO-000006 | APPROVE | APPROVE | CREDIT | 8360.00 | 8360.00 | 10.000 | false | true | None | None | false | RECOMMENDED | Approve and credit in line with the deterministic authorities. The claim is not a duplicate, conformance is failed due to tensile_mpa, and coating warranty coverage is active at 1 elapsed month under galvanized/NA/V2/coverage with full proration under galvanized/NA/V2/proration. No exclusion was hit in the structured coverage result, and the structured fields (inland, ventilated, 25.0 km from coast) do not contradict the exclusion conditions listed in galvanized/NA/V2/exclusions. Settlement estimate matches the settlement authority approved amount exactly. Advisory precedent is mixed and not controlling; elevated risk score supports monitoring only and does not change eligibility or amount. | {galvanized/NA/V2/exclusions,galvanized/NA/V2/coverage,galvanized/NA/V2/proration} | None | None | CLM-0000005 | None | None | agent_recommendation | 2026-10-07T20:47:29.005Z | CREDIT | 0.95 | {"fraud_risk":true,"over_claim":false,"supplier_attributable":true} | {"found":true,"heat_no":"HEAT-000001","cluster_id":"CLM-0000005","risk_score":0.75,"computed_at":"2026-10-07T11:49:00.767481+00:00","customer_id":"CUST-0102","cluster_size":10,"repeat_customers":true,"distinct_heats_in_cluster":1,"distinct_customers_in_cluster":5} | [{"verdict":"APPROVE","claim_id":"CLM-0000300","rrf_score":0.016393,"approved_amount":6133.33},{"verdict":"DENY","claim_id":"CLM-0000642","rrf_score":0.016393,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000645","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0002442","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000648","rrf_score":0.015873,"approved_amount":0.0}] | 03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8 | 1 | 2026-10-07T20:47:29.005Z |
| update_postimage | 128773400 | 137223 | 2026-10-09T07:30:54.669 | 5104 | ADJ-03cec259986537abef5b | CLM-DEMO-000006 | APPROVE | APPROVE | CREDIT | 8360.00 | 8360.00 | 10.000 | false | true | None | None | false | FINAL | Approve and credit in line with the deterministic authorities. The claim is not a duplicate, conformance is failed due to tensile_mpa, and coating warranty coverage is active at 1 elapsed month under galvanized/NA/V2/coverage with full proration under galvanized/NA/V2/proration. No exclusion was hit in the structured coverage result, and the structured fields (inland, ventilated, 25.0 km from coast) do not contradict the exclusion conditions listed in galvanized/NA/V2/exclusions. Settlement estimate matches the settlement authority approved amount exactly. Advisory precedent is mixed and not controlling; elevated risk score supports monitoring only and does not change eligibility or amount. | {galvanized/NA/V2/exclusions,galvanized/NA/V2/coverage,galvanized/NA/V2/proration} | 2026-10-09T07:30:49.980 | None | CLM-0000005 | <user> | None | agent_recommendation | 2026-10-07T20:47:29.005Z | CREDIT | 0.95 | {"fraud_risk":true,"over_claim":false,"supplier_attributable":true} | {"found":true,"heat_no":"HEAT-000001","cluster_id":"CLM-0000005","risk_score":0.75,"computed_at":"2026-10-07T11:49:00.767481+00:00","customer_id":"CUST-0102","cluster_size":10,"repeat_customers":true,"distinct_heats_in_cluster":1,"distinct_customers_in_cluster":5} | [{"verdict":"APPROVE","claim_id":"CLM-0000300","rrf_score":0.016393,"approved_amount":6133.33},{"verdict":"DENY","claim_id":"CLM-0000642","rrf_score":0.016393,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000645","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0002442","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000648","rrf_score":0.015873,"approved_amount":0.0}] | 03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8 | 1 | 2026-10-07T20:47:29.005Z |
| insert | 113781280 | 28712 | 2026-10-07T20:47:41.971 | 5007 | ADJ-03cec259986537abef5b | CLM-DEMO-000006 | APPROVE | APPROVE | CREDIT | 8360.00 | 8360.00 | 10.000 | false | true | None | None | false | RECOMMENDED | Approve and credit in line with the deterministic authorities. The claim is not a duplicate, conformance is failed due to tensile_mpa, and coating warranty coverage is active at 1 elapsed month under galvanized/NA/V2/coverage with full proration under galvanized/NA/V2/proration. No exclusion was hit in the structured coverage result, and the structured fields (inland, ventilated, 25.0 km from coast) do not contradict the exclusion conditions listed in galvanized/NA/V2/exclusions. Settlement estimate matches the settlement authority approved amount exactly. Advisory precedent is mixed and not controlling; elevated risk score supports monitoring only and does not change eligibility or amount. | {galvanized/NA/V2/exclusions,galvanized/NA/V2/coverage,galvanized/NA/V2/proration} | None | None | CLM-0000005 | None | None | agent_recommendation | 2026-10-07T20:47:29.005Z | CREDIT | 0.95 | {"fraud_risk":true,"over_claim":false,"supplier_attributable":true} | {"found":true,"heat_no":"HEAT-000001","cluster_id":"CLM-0000005","risk_score":0.75,"computed_at":"2026-10-07T11:49:00.767481+00:00","customer_id":"CUST-0102","cluster_size":10,"repeat_customers":true,"distinct_heats_in_cluster":1,"distinct_customers_in_cluster":5} | [{"verdict":"APPROVE","claim_id":"CLM-0000300","rrf_score":0.016393,"approved_amount":6133.33},{"verdict":"DENY","claim_id":"CLM-0000642","rrf_score":0.016393,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000645","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0002442","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000648","rrf_score":0.015873,"approved_amount":0.0}] | 03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8 | 1 | 2026-10-07T20:47:29.005Z |

### 8. 6c UC native CDF: cdf.lb_adjudication_decision_records_history (selected cols)

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:15+00:00 · status: `0`

| adjudication_id | record_version | recommended_verdict | approved_amount | reasoning_endpoint | created_at |
|---|---|---|---|---|---|
| ADJ-03cec259986537abef5b | 2 | APPROVE | 8360 | system.ai.gpt-5-4 | 2026-10-09T07:30:49.980Z |
| ADJ-03cec259986537abef5b | 1 | APPROVE | 8360 | system.ai.gpt-5-4 | 2026-10-07T20:47:29.005Z |

### 9. 6d UC native CDF: cdf.lb_outbox_history

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:20+00:00 · status: `0`

| _pg_change_type | _pg_lsn | _pg_xid | _timestamp | _sort_by | event_id | aggregate_id | event_type | payload | created_at | published_at |
|---|---|---|---|---|---|---|---|---|---|---|
| update_preimage | 128831136 | 137239 | 2026-10-09T07:39:23.801 | 4 | adj-ADJ-03cec259986537abef5b | CLM-DEMO-000006 | claim.adjudicated | {"verdict":"APPROVE","claim_id":"CLM-DEMO-000006","event_id":"adj-ADJ-03cec259986537abef5b","event_type":"claim.adjudicated","disposition":"CREDIT","finalized_at":"2026-10-09T07:30:50.010Z","schema_version":"claim-event/v1","adjudication_id":"ADJ-03cec259986537abef5b","approved_amount":8360,"idempotency_key":"03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8","recommended_verdict":"APPROVE","recovery_supplier_id":null,"duplicate_of_claim_id":null,"supplier_attributable":true} | 2026-10-09T07:30:49.980Z | None |
| update_postimage | 128831136 | 137239 | 2026-10-09T07:39:23.801 | 5 | adj-ADJ-03cec259986537abef5b | CLM-DEMO-000006 | claim.adjudicated | {"verdict":"APPROVE","claim_id":"CLM-DEMO-000006","event_id":"adj-ADJ-03cec259986537abef5b","event_type":"claim.adjudicated","disposition":"CREDIT","finalized_at":"2026-10-09T07:30:50.010Z","schema_version":"claim-event/v1","adjudication_id":"ADJ-03cec259986537abef5b","approved_amount":8360,"idempotency_key":"03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8","recommended_verdict":"APPROVE","recovery_supplier_id":null,"duplicate_of_claim_id":null,"supplier_attributable":true} | 2026-10-09T07:30:49.980Z | 2026-10-09T07:39:23.801Z |
| insert | 128783400 | 137223 | 2026-10-09T07:31:02.826 | 3 | adj-ADJ-03cec259986537abef5b | CLM-DEMO-000006 | claim.adjudicated | {"verdict":"APPROVE","claim_id":"CLM-DEMO-000006","event_id":"adj-ADJ-03cec259986537abef5b","event_type":"claim.adjudicated","disposition":"CREDIT","finalized_at":"2026-10-09T07:30:50.010Z","schema_version":"claim-event/v1","adjudication_id":"ADJ-03cec259986537abef5b","approved_amount":8360,"idempotency_key":"03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8","recommended_verdict":"APPROVE","recovery_supplier_id":null,"duplicate_of_claim_id":null,"supplier_attributable":true} | 2026-10-09T07:30:49.980Z | None |

### 10. 6e UC native CDF: cdf.lb_settlements_history

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:25+00:00 · status: `0`

| _pg_change_type | _pg_lsn | _pg_xid | _timestamp | _sort_by | settlement_id | claim_id | status | amount | settled_at | created_at |
|---|---|---|---|---|---|---|---|---|---|---|
| insert | 128837624 | 137242 | 2026-10-09T07:40:41.193 | 1 | STL-CLM-DEMO-000006 | CLM-DEMO-000006 | PENDING_PAYMENT | 8360.00 | None | 2026-10-09T07:40:37.334Z |

### 11. 7a silver.claims_history

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:27+00:00 · status: `0`

| claim_id | coil_id | customer_id | claim_type | claim_date | install_date | environment | installation | coast_distance_km | defect_code | defect_narrative | claimed_tonnage | claimed_freight | __START_AT | __END_AT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLM-DEMO-000006 | COIL-0000006 | CUST-0102 | coating_warranty | 2026-01-01 | 2014-01-31 | inland | ventilated | 25.0 | RED_RUST | Premature red rust and perforation observed on installed roofing. | 10.000 | 0.00 | 5006 | None |

### 12. 7b silver.adjudications_history

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:30+00:00 · status: `0`

| adjudication_id | claim_id | verdict | recommended_verdict | disposition | claimed_amount | approved_amount | covered_tonnage | freight_covered | supplier_attributable | recovery_supplier_id | defect_failure_mode_code | override_flag | decision_status | rationale | cited_clause_ids | finalized_at | duplicate_of_claim_id | fraud_cluster_id | decided_by | override_reason | recommended_disposition | confidence | flags | advisory_risk | precedent | idempotency_key | decision_record_version | recommended_at | __START_AT | __END_AT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADJ-03cec259986537abef5b | CLM-DEMO-000006 | APPROVE | APPROVE | CREDIT | 8360.00 | 8360.00 | 10.000 | false | true | None | None | false | RECOMMENDED | Approve and credit in line with the deterministic authorities. The claim is not a duplicate, conformance is failed due to tensile_mpa, and coating warranty coverage is active at 1 elapsed month under galvanized/NA/V2/coverage with full proration under galvanized/NA/V2/proration. No exclusion was hit in the structured coverage result, and the structured fields (inland, ventilated, 25.0 km from coast) do not contradict the exclusion conditions listed in galvanized/NA/V2/exclusions. Settlement estimate matches the settlement authority approved amount exactly. Advisory precedent is mixed and not controlling; elevated risk score supports monitoring only and does not change eligibility or amount. | None | None | None | CLM-0000005 | None | None | CREDIT | 0.95 | {"fraud_risk":true,"over_claim":false,"supplier_attributable":true} | {"found":true,"heat_no":"HEAT-000001","cluster_id":"CLM-0000005","risk_score":0.75,"computed_at":"2026-10-07T11:49:00.767481+00:00","customer_id":"CUST-0102","cluster_size":10,"repeat_customers":true,"distinct_heats_in_cluster":1,"distinct_customers_in_cluster":5} | [{"verdict":"APPROVE","claim_id":"CLM-0000300","rrf_score":0.016393,"approved_amount":6133.33},{"verdict":"DENY","claim_id":"CLM-0000642","rrf_score":0.016393,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000645","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0002442","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000648","rrf_score":0.015873,"approved_amount":0.0}] | 03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8 | 1 | 2026-10-07T20:47:29.005Z | 5007 | 5104 |
| ADJ-03cec259986537abef5b | CLM-DEMO-000006 | APPROVE | APPROVE | CREDIT | 8360.00 | 8360.00 | 10.000 | false | true | None | None | false | FINAL | Approve and credit in line with the deterministic authorities. The claim is not a duplicate, conformance is failed due to tensile_mpa, and coating warranty coverage is active at 1 elapsed month under galvanized/NA/V2/coverage with full proration under galvanized/NA/V2/proration. No exclusion was hit in the structured coverage result, and the structured fields (inland, ventilated, 25.0 km from coast) do not contradict the exclusion conditions listed in galvanized/NA/V2/exclusions. Settlement estimate matches the settlement authority approved amount exactly. Advisory precedent is mixed and not controlling; elevated risk score supports monitoring only and does not change eligibility or amount. | None | 2026-10-09T07:30:49.980Z | None | CLM-0000005 | <user> | None | CREDIT | 0.95 | {"fraud_risk":true,"over_claim":false,"supplier_attributable":true} | {"found":true,"heat_no":"HEAT-000001","cluster_id":"CLM-0000005","risk_score":0.75,"computed_at":"2026-10-07T11:49:00.767481+00:00","customer_id":"CUST-0102","cluster_size":10,"repeat_customers":true,"distinct_heats_in_cluster":1,"distinct_customers_in_cluster":5} | [{"verdict":"APPROVE","claim_id":"CLM-0000300","rrf_score":0.016393,"approved_amount":6133.33},{"verdict":"DENY","claim_id":"CLM-0000642","rrf_score":0.016393,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000645","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0002442","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000648","rrf_score":0.015873,"approved_amount":0.0}] | 03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8 | 1 | 2026-10-07T20:47:29.005Z | 5104 | None |

### 13. 8a gold.claims_current

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:32+00:00 · status: `0`

| claim_id | coil_id | customer_id | claim_type | claim_date | install_date | environment | installation | coast_distance_km | defect_code | defect_narrative | claimed_tonnage | claimed_freight | __START_AT | __END_AT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| CLM-DEMO-000006 | COIL-0000006 | CUST-0102 | coating_warranty | 2026-01-01 | 2014-01-31 | inland | ventilated | 25.0 | RED_RUST | Premature red rust and perforation observed on installed roofing. | 10.000 | 0.00 | 5006 | None |

### 14. 8b gold.adjudications_current

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:35+00:00 · status: `0`

| adjudication_id | claim_id | verdict | recommended_verdict | disposition | claimed_amount | approved_amount | covered_tonnage | freight_covered | supplier_attributable | recovery_supplier_id | defect_failure_mode_code | override_flag | decision_status | rationale | cited_clause_ids | finalized_at | duplicate_of_claim_id | fraud_cluster_id | decided_by | override_reason | recommended_disposition | confidence | flags | advisory_risk | precedent | idempotency_key | decision_record_version | recommended_at | __START_AT | __END_AT |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADJ-03cec259986537abef5b | CLM-DEMO-000006 | APPROVE | APPROVE | CREDIT | 8360.00 | 8360.00 | 10.000 | false | true | None | None | false | FINAL | Approve and credit in line with the deterministic authorities. The claim is not a duplicate, conformance is failed due to tensile_mpa, and coating warranty coverage is active at 1 elapsed month under galvanized/NA/V2/coverage with full proration under galvanized/NA/V2/proration. No exclusion was hit in the structured coverage result, and the structured fields (inland, ventilated, 25.0 km from coast) do not contradict the exclusion conditions listed in galvanized/NA/V2/exclusions. Settlement estimate matches the settlement authority approved amount exactly. Advisory precedent is mixed and not controlling; elevated risk score supports monitoring only and does not change eligibility or amount. | None | 2026-10-09T07:30:49.980Z | None | CLM-0000005 | <user> | None | CREDIT | 0.95 | {"fraud_risk":true,"over_claim":false,"supplier_attributable":true} | {"found":true,"heat_no":"HEAT-000001","cluster_id":"CLM-0000005","risk_score":0.75,"computed_at":"2026-10-07T11:49:00.767481+00:00","customer_id":"CUST-0102","cluster_size":10,"repeat_customers":true,"distinct_heats_in_cluster":1,"distinct_customers_in_cluster":5} | [{"verdict":"APPROVE","claim_id":"CLM-0000300","rrf_score":0.016393,"approved_amount":6133.33},{"verdict":"DENY","claim_id":"CLM-0000642","rrf_score":0.016393,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000645","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0002442","rrf_score":0.016129,"approved_amount":0.0},{"verdict":"DENY","claim_id":"CLM-0000648","rrf_score":0.015873,"approved_amount":0.0}] | 03cec259986537abef5b99a4c6905a7353ec83639d6c023472774932c4ebfbf8 | 1 | 2026-10-07T20:47:29.005Z | 5104 | None |

### 15. 8c gold.gold_claim_adjudication_fact

- Command: `databricks api post /api/2.0/sql/statements --profile fe-bar-ir-2026 (warehouse_id=a323b5700ae25d85)`
- Time: 2026-10-13T08:32:37+00:00 · status: `0`

| adjudication_id | claim_id | claim_date | claim_type | customer_id | customer_name | customer_segment | coil_id | heat_no | product | grade | coating | region | line | prod_date | ship_date | shipped_tonnage | unit_price | claimed_tonnage | claimed_freight | defect_code | failure_mode_code | defect_description | process_step | verdict | disposition | decision_status | recommended_verdict | recommended_disposition | override_flag | claimed_amount | approved_amount | covered_tonnage | finalized_at | cycle_time_days | supplier_attributable | recovery_supplier_id | recovery_supplier_name | recovery_supplier_type | fraud_cluster_id | fraud_risk_flag | over_claim_flag | duplicate_flag | material_in_spec | warranty_covered | decision_record_version | agent_recommended_verdict | agent_recommended_disposition | agent_recommended_amount | agent_model_name | agent_model_version | prompt_version | schema_version | cited_clause_ids | citations | authorities_source_sha256 | mlflow_trace_id | graph_cluster_id | graph_cluster_size | graph_cluster_customers | customer_heat_risk_score | customer_heat_risky | customer_heat_high_risk | customer_heat_risk_reason |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ADJ-03cec259986537abef5b | CLM-DEMO-000006 | 2026-01-01 | coating_warranty | CUST-0102 | Johnson-Miller | service_center | COIL-0000006 | HEAT-000001 | galvanized | ASTM A653 CS Type B | G90 | NA | LINE-1 | 2025-11-21 | 2025-12-01 | 10.000 | 836.00 | 10.000 | 0.00 | RED_RUST | RED_RUST | Field corrosion/perforation | field | APPROVE | CREDIT | FINAL | APPROVE | CREDIT | false | 8360.00 | 8360.00 | 10.000 | 2026-10-09T07:30:49.980Z | 281 | true | None | None | None | CLM-0000005 | true | false | false | false | true | 2 | APPROVE | CREDIT | 8360.00 | fe-bar-ir.default.claims_adjudication_agent | None | claims-adjudication-prompt/v2-narrative-escalation | adjudication-decision-record/v2 | None | [{"citation_key":"galvanized/NA/V2/exclusions","section_ref":"exclusions","clause_text_sha256":"6479b1ee3aea4091168f865795b69da7cbcba2403fd5abb31e7660e937fb8809"},{"citation_key":"galvanized/NA/V2/coverage","section_ref":"coverage","clause_text_sha256":"603da73447148c66d3fa2ea4e99532df523a79b6e768585484747499141dec43"},{"citation_key":"galvanized/NA/V2/proration","section_ref":"proration","clause_text_sha256":"71c150608fa3402231abb5ebdbcd36019cb23cf696c0eb9c8c062a19b41f6f43"}] | 6373424cdb86f7eb513346c36687ee9c297aea9a05d551ccd42897f12f24a348 | tr-7bf659ecc79ebf80456afd568eb03bf9 | CLM-0000005 | 20 | 5 | 1.0 | true | true | Fraud cluster: 20 claims from 5 customers on heat HEAT-000001 (concentration 0.75). |

### 16. 8d steel-claims pipeline latest update (explains silver/gold lag)

- Command: `databricks pipelines list-updates 736c9bbe-b9b0-4c8c-8478-54d0c6c0e578 --max-results 1 --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:41+00:00 · status: `0`

```json
{
  "next_page_token": "CAIQhff7opM0GiQwMDJmMTBhZS1hMDE0LTQ1YjYtYTk5Mi0yM2NjOTUyMTU5YmI=",
  "updates": [
    {
      "cause": "JOB_TASK",
      "creation_time": 1791880002437,
      "full_refresh": false,
      "pipeline_id": "736c9bbe-b9b0-4c8c-8478-54d0c6c0e578",
      "state": "COMPLETED",
      "update_id": "002f10ae-a014-45b6-a992-23cc952159bb",
      "validate_only": false
    }
  ]
}
```

### 17. 9 operational Genie: ask about the claim

- Command: `databricks genie start-conversation 01f1c269ca3c1adea7feb9f248ab3445 Show the claim_id, decision_status, verdict, disposition and approved_amount for claim_id exactly 'CLM-DEMO-000006'. --timeout 5m --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:42+00:00 · status: `0`

```json
{
  "attachments": [
    {
      "attachment_id": "01f1c6e0ac0d1b12b2cf418425433ac4",
      "query": {
        "description": "You want to see the claim ID, decision status, verdict, disposition, and approved amount for the claim with the exact ID 'CLM-DEMO-000006'.",
        "query": "SELECT `c`.`claim_id`, `a`.`decision_status`, `a`.`verdict`, `a`.`disposition`, `a`.`approved_amount`\nFROM `fe-bar-ir`.`gold`.`claims_current` AS `c`\nJOIN `fe-bar-ir`.`gold`.`adjudications_current` AS `a`\n  ON `c`.`claim_id` = `a`.`claim_id`\nWHERE `c`.`claim_id` = 'CLM-DEMO-000006'",
        "query_result_metadata": {
          "row_count": 1
        },
        "statement_id": "01f1c6e0-ac17-1a32-ba71-3d78c91c65ae",
        "thoughts": [
          {
            "content": "You want to see the claim ID, decision status, verdict, disposition, and approved amount for the claim with the exact ID 'CLM-DEMO-000006'.",
            "thought_type": "THOUGHT_TYPE_DESCRIPTION"
          },
          {
            "content": "- fe-bar-ir.gold.claims_current\n- fe-bar-ir.gold.adjudications_current",
            "thought_type": "THOUGHT_TYPE_DATA_SOURCING"
          },
          {
            "content": "- Retrieve the claim ID from the current claims data.\n- Find the matching adjudication record for that claim ID.\n- Get the decision status, verdict, disposition, and approved amount from the adjudication record.\n- Filter results to only include the claim with ID 'CLM-DEMO-000006'.",
            "thought_type": "THOUGHT_TYPE_STEPS"
          }
        ]
      }
    },
    {
      "attachment_id": "01f1c6e0ad571531beb041818cb9e4ab",
      "suggested_questions": {
... (28 more lines — see the .json)
```

### 18. 9b operational Genie: query result

- Command: `databricks genie get-message-attachment-query-result 01f1c269ca3c1adea7feb9f248ab3445 01f1c6e0a95d1a48a9de6ba913806615 01f1c6e0a9651d548777a544b9158c02 01f1c6e0ac0d1b12b2cf418425433ac4 --profile fe-bar-ir-2026 -o json`
- Time: 2026-10-13T08:32:58+00:00 · status: `0`

```json
{
  "statement_response": {
    "manifest": {
      "chunks": [
        {
          "byte_count": 928,
          "chunk_index": 0,
          "row_count": 1,
          "row_offset": 0
        }
      ],
      "format": "JSON_ARRAY",
      "schema": {
        "column_count": 5,
        "columns": [
          {
            "name": "claim_id",
            "position": 0,
            "type_name": "STRING",
            "type_text": "STRING"
          },
          {
            "name": "decision_status",
            "position": 1,
            "type_name": "STRING",
            "type_text": "STRING"
          },
          {
            "name": "verdict",
            "position": 2,
... (44 more lines — see the .json)
```

### 19. 10 App: /api/history (filtered client-side to the claim)

- Command: `HTTP GET https://steel-claims-cockpit-7474646906749000.aws.databricksapps.com/api/history?limit=50 (Authorization: Bearer <user OAuth token from `databricks auth token --profile fe-bar-ir-2026`>)`
- Time: 2026-10-13T08:33:01+00:00 · status: `200`

```json
{
  "count": 50,
  "items_matching_claim": [
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
      "claimed_amount": "8360.00",
      "override_flag": false,
      "override_reason": null,
      "decided_by": "<user>",
      "finalized_at": "2026-10-09T07:30:49.980Z",
      "recommended_verdict": "APPROVE",
      "recommended_disposition": "CREDIT"
    }
  ]
}
```

### 20. 11 App: cockpit detail for the claim

- Command: `HTTP GET https://steel-claims-cockpit-7474646906749000.aws.databricksapps.com/api/claims/CLM-DEMO-000006?adjudication_id=ADJ-03cec259986537abef5b (Authorization: Bearer <user OAuth token from `databricks auth token --profile fe-bar-ir-2026`>)`
- Time: 2026-10-13T08:33:03+00:00 · status: `200`

```json
{
  "adjudication": {
    "adjudication_id": "ADJ-03cec259986537abef5b",
    "claim_id": "CLM-DEMO-000006",
    "decision_status": "FINAL",
    "verdict": "APPROVE",
    "disposition": "CREDIT",
    "approved_amount": "8360.00",
    "decided_by": "<user>",
    "finalized_at": "2026-10-09T07:30:49.980Z"
  },
  "decision_records": [
    {
      "record_version": 1,
      "recommended_verdict": "APPROVE",
      "approved_amount": "8360",
      "cited_clause_ids": [
        "galvanized/NA/V2/exclusions",
        "galvanized/NA/V2/coverage",
        "galvanized/NA/V2/proration"
      ],
      "reasoning_endpoint": null,
      "decided_by": null
    },
    {
      "record_version": 2,
      "recommended_verdict": "APPROVE",
      "approved_amount": "8360",
      "cited_clause_ids": [
        "galvanized/NA/V2/exclusions",
... (8 more lines — see the .json)
```

