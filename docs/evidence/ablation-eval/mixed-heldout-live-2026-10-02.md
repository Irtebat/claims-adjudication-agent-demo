# Mixed held-out live ablation — 2026-10-02

Dataset: `fe-bar-ir.eval.heldout_claims_mixed`; n=100 (70 normal, 30 narrative).

Runs: baseline `5d40e1f8667d4234991ad21089c6656c`; agent `7fef9952af8641399e11a5d19af8aa97`; comparison `af37991c2dbe4b2c91b9946a1bfc054c`.

## Metrics

Each score shows value and N-applicable in parentheses.

| Slice | Candidate | Verdict | Disposition class | Approval sub-choice | Amount | Duplicate | PEND | Citation | Invariant correction | Errors | Tokens/claim | Latency ms/claim |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Overall | deterministic_baseline | 60.0% (100) | 60.0% (100) | 100.0% (35) | 70.0% (100) | 100.0% (100) | 90.0% (100) | N/A (0) | N/A | 0 | N/A | 1480.6 |
| Overall | agent@prod | 60.0% (100) | 60.0% (100) | 42.9% (35) | 70.0% (100) | 100.0% (100) | 90.0% (100) | 100.0% (100) | 0.0% | 0 | 4859.5 | 39787.9 |
| Group: narrative | deterministic_baseline | 0.0% (30) | 0.0% (30) | N/A (0) | 0.0% (30) | 100.0% (30) | 100.0% (30) | N/A (0) | N/A | 0 | N/A | 1368.2 |
| Group: narrative | agent@prod | 0.0% (30) | 0.0% (30) | N/A (0) | 0.0% (30) | 100.0% (30) | 100.0% (30) | 100.0% (30) | 0.0% | 0 | 4671.0 | 39684.3 |
| Group: normal | deterministic_baseline | 85.7% (70) | 85.7% (70) | 100.0% (35) | 100.0% (70) | 100.0% (70) | 85.7% (70) | N/A (0) | N/A | 0 | N/A | 1528.8 |
| Group: normal | agent@prod | 85.7% (70) | 85.7% (70) | 42.9% (35) | 100.0% (70) | 100.0% (70) | 85.7% (70) | 100.0% (70) | 0.0% | 0 | 4940.3 | 39832.3 |
| Stratum: R1_duplicate | deterministic_baseline | 100.0% (10) | 100.0% (10) | N/A (0) | 100.0% (10) | 100.0% (10) | 100.0% (10) | N/A (0) | N/A | 0 | N/A | 1307.8 |
| Stratum: R1_duplicate | agent@prod | 100.0% (10) | 100.0% (10) | N/A (0) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 0.0% | 0 | 4991.4 | 44977.2 |
| Stratum: R4_ineligible | deterministic_baseline | 100.0% (15) | 100.0% (15) | N/A (0) | 100.0% (15) | 100.0% (15) | 100.0% (15) | N/A (0) | N/A | 0 | N/A | 1340.3 |
| Stratum: R4_ineligible | agent@prod | 100.0% (15) | 100.0% (15) | N/A (0) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 0.0% | 0 | 4838.0 | 39639.0 |
| Stratum: R5_supplier_attributable | deterministic_baseline | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | N/A (0) | N/A | 0 | N/A | 1315.6 |
| Stratum: R5_supplier_attributable | agent@prod | 100.0% (10) | 100.0% (10) | 0.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 0.0% | 0 | 5096.1 | 37338.3 |
| Stratum: R6_over_claim_partial | deterministic_baseline | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | N/A (0) | N/A | 0 | N/A | 1356.0 |
| Stratum: R6_over_claim_partial | agent@prod | 100.0% (10) | 100.0% (10) | 0.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 0.0% | 0 | 5035.9 | 38311.1 |
| Stratum: R7_credit | deterministic_baseline | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | N/A (0) | N/A | 0 | N/A | 2212.3 |
| Stratum: R7_credit | agent@prod | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 100.0% (15) | 0.0% | 0 | 4840.7 | 39564.3 |
| Stratum: coastal_lt_2km | deterministic_baseline | 0.0% (10) | 0.0% (10) | N/A (0) | 0.0% (10) | 100.0% (10) | 100.0% (10) | N/A (0) | N/A | 0 | N/A | 1352.3 |
| Stratum: coastal_lt_2km | agent@prod | 0.0% (10) | 0.0% (10) | N/A (0) | 0.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 0.0% | 0 | 4760.3 | 44767.1 |
| Stratum: excluded_environment | deterministic_baseline | 0.0% (10) | 0.0% (10) | N/A (0) | 0.0% (10) | 100.0% (10) | 100.0% (10) | N/A (0) | N/A | 0 | N/A | 1379.7 |
| Stratum: excluded_environment | agent@prod | 0.0% (10) | 0.0% (10) | N/A (0) | 0.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 0.0% | 0 | 4626.4 | 37575.8 |
| Stratum: excluded_installation | deterministic_baseline | 0.0% (10) | 0.0% (10) | N/A (0) | 0.0% (10) | 100.0% (10) | 100.0% (10) | N/A (0) | N/A | 0 | N/A | 1372.6 |
| Stratum: excluded_installation | agent@prod | 0.0% (10) | 0.0% (10) | N/A (0) | 0.0% (10) | 100.0% (10) | 100.0% (10) | 100.0% (10) | 0.0% | 0 | 4626.4 | 36710.0 |
| Stratum: fraud_ring | deterministic_baseline | 0.0% (10) | 0.0% (10) | N/A (0) | 100.0% (10) | 100.0% (10) | 0.0% (10) | N/A (0) | N/A | 0 | N/A | 1393.1 |
| Stratum: fraud_ring | agent@prod | 0.0% (10) | 0.0% (10) | N/A (0) | 100.0% (10) | 100.0% (10) | 0.0% (10) | 100.0% (10) | 0.0% | 0 | 4940.9 | 39394.7 |

## Paired results

There were 20 output disagreements: 10 in R5 supplier-attributable and 10 in R6 over-claim. The baseline matched all 20 full gold outputs; the agent used CREDIT instead of REPLACEMENT/REWORK. Verdict McNemar cells by group: normal both-correct 60, both-wrong 10, baseline-only 0, agent-only 0; narrative both-wrong 30, all other cells 0. Full per-dimension paired cells are in the JSON evidence.

Two examples:

- `CLM-0001866` (R6_over_claim_partial): gold APPROVE/REWORK amount 7960.00; baseline APPROVE/REWORK amount 7960.0; agent APPROVE/CREDIT amount 7960.0. Right: deterministic_baseline.

- `CLM-0004375` (R6_over_claim_partial): gold APPROVE/REWORK amount 17075.00; baseline APPROVE/REWORK amount 17075.0; agent APPROVE/CREDIT amount 17075.0. Right: deterministic_baseline.

## Complete examples

### Normal: `CLM-0001866` (R6_over_claim_partial)

Inputs:

```json
{
  "claimed_tonnage": "14.000",
  "customer_id": "CUST-0005",
  "claimed_freight": "1800.00",
  "defect_code": "MECH_TENSILE",
  "claim_id": "CLM-0001866",
  "claim_date": "2026-01-19",
  "installation": "ventilated",
  "coil_id": "COIL-0001866",
  "environment": "inland",
  "claim_type": "material_nonconformance",
  "install_date": "2025-12-31",
  "defect_narrative": "Tensile response during forming differs from ordered mechanical requirements.",
  "coast_distance_km": "25.0"
}
```

Expectations:

```json
{
  "disposition": "REWORK",
  "oracle_clause_ids": [
    "ASTM A653 CS Type B/NA/DEMO-1990/mechanical",
    "ASTM A653 CS Type B/NA/DEMO-1990/dimensions",
    "ASTM A653 CS Type B/NA/DEMO-1990/chemistry"
  ],
  "narrative_fact": "agrees_with_structured_fields",
  "verdict": "APPROVE",
  "disposition_class": "APPROVE",
  "scenario_type": "R6_over_claim_partial",
  "stratum": "R6_over_claim_partial",
  "policy_clause_id": "ASTM A653 CS Type B/NA/DEMO-1990/mechanical",
  "approved_amount": "7960.00",
  "group": "normal",
  "approval_subchoice": "REWORK",
  "policy_section_ref": "ASTM A653 CS Type B/NA/DEMO-1990/mechanical"
}
```

Baseline prediction:

```json
{
  "verdict": "APPROVE",
  "disposition": "REWORK",
  "approved_amount": 7960.0,
  "rationale": "Deterministic rules: APPROVE/REWORK by R6_over_claim_partial.",
  "citations": null,
  "error": null
}
```

Agent prediction:

```json
{
  "verdict": "APPROVE",
  "disposition": "CREDIT",
  "approved_amount": 7960.0,
  "rationale": "Authoritative conformance finding is nonconforming, with tensile_mpa failing the ASTM A653 CS Type B mechanical requirement, supporting a material_nonconformance claim under ASTM A653 CS Type B/NA/DEMO-1990/mechanical. Coverage is authoritatively in scope: covered=true, elapsed_months=1, no exclusions, and proration_factor=1.0. Duplicate screening is clear: is_duplicate=false, so no duplicate denial applies. Settlement must follow the deterministic authority: approve as CREDIT for 7960.0, which is a partial settlement versus the claimed 12244.0 due to over-claim detection; covered tonnage is 10.0 and freight of 500.0 is covered. Chemistry and dimensions clauses were reviewed as background specification references, but the decisive nonconformance is mechanical tensile_mpa.",
  "citations": [
    "ASTM A653 CS Type B/NA/DEMO-1990/mechanical",
    "ASTM A653 CS Type B/NA/DEMO-1990/chemistry",
    "ASTM A653 CS Type B/NA/DEMO-1990/dimensions"
  ],
  "error": null
}
```

Right: deterministic_baseline.

### Narrative: `34b78180-bc32-5a8f-ae7d-5d81c0fdc68e` (excluded_installation)

Inputs:

```json
{
  "claimed_tonnage": "10.000",
  "customer_id": "CUST-0046",
  "claimed_freight": "0.00",
  "defect_code": "RED_RUST",
  "claim_id": "34b78180-bc32-5a8f-ae7d-5d81c0fdc68e",
  "claim_date": "2026-01-06",
  "installation": "ventilated",
  "coil_id": "COIL-0000501",
  "environment": "inland",
  "claim_type": "coating_warranty",
  "install_date": "2018-01-31",
  "defect_narrative": "Drainage is blocked and water stands against the underside of the panels.",
  "coast_distance_km": "10.0"
}
```

Expectations:

```json
{
  "disposition": "DENY",
  "oracle_clause_ids": [
    "/warranties/coverage/excluded_installations",
    "galvanized/NA/V2/coverage",
    "galvanized/NA/V2/exclusions",
    "galvanized/NA/V2/proration"
  ],
  "narrative_fact": "standing_water",
  "verdict": "DENY",
  "disposition_class": "DENY",
  "scenario_type": "excluded_installation",
  "stratum": "excluded_installation",
  "policy_clause_id": "/warranties/coverage/excluded_installations",
  "approved_amount": "0.00",
  "group": "narrative",
  "approval_subchoice": null,
  "policy_section_ref": "warranties.coverage.excluded_installations"
}
```

Baseline prediction:

```json
{
  "verdict": "APPROVE",
  "disposition": "CREDIT",
  "approved_amount": 7810.0,
  "rationale": "Deterministic rules: APPROVE/CREDIT by R7_credit.",
  "citations": null,
  "error": null
}
```

Agent prediction:

```json
{
  "verdict": "APPROVE",
  "disposition": "CREDIT",
  "approved_amount": 7810.0,
  "rationale": "Approve as CREDIT because the deterministic outcome is authoritative and finds the claim eligible, covered, and payable at 7810.0. The duplicate gate is clear because the claim is not a duplicate. Coverage is satisfied under galvanized/NA/V2/coverage for this coating_warranty claim at 96 elapsed months, no exclusions were triggered under galvanized/NA/V2/exclusions, and the payment level is consistent with galvanized/NA/V2/proration with a 1.0 proration factor. The settlement estimate matches the settlement authority exactly. Although conformance indicates the material conforms, this is a covered coating warranty claim and the authoritative coverage determination governs. Advisory precedent trends to denial, but those cases do not override the authoritative covered outcome here. Elevated advisory risk supports monitoring only and does not change verdict or amount.",
  "citations": [
    "galvanized/NA/V2/exclusions",
    "galvanized/NA/V2/coverage",
    "galvanized/NA/V2/proration"
  ],
  "error": null
}
```

Right: neither.

## Read

Rules are sufficient for every structured path in the registered data except the intentionally unsupported fraud-ring PEND labels. Neither candidate used narrative-only exclusions: both scored 0/30 on narrative verdict, disposition, and amount. The agent added explanations and citations but regressed all 20 R5/R6 approval sub-choices to CREDIT, indicating the registered model carries older embedded disposition logic than the harness baseline. Citation=100% only means the cited set was non-empty and a subset of the oracle; it does not prove the deciding narrative clause was cited. No candidate errors, invariant corrections, persistence, or Lakebase public writes occurred.
