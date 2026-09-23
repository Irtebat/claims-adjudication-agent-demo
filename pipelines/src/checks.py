"""Semantic fixture invariants shared by serverless smoke checks and live evidence.

Policy standards and coating-warranty terms now live in Lakebase (loaded by the
policy intake), so the pipeline no longer holds those tables. The three former
checks that cross-referenced them (warranty_version_mismatch, in_spec_not_conforming,
exclusion_not_present) move to the deterministic-authority unit tests
(agent/tests/test_authorities.py), which assert conformance/coverage/settlement
against the same Wave-1 injected label patterns using the authored params.
"""


def integrity_queries(c):
    return {
        "missing_coil": f"SELECT count(*) n FROM {c}.gold.claims_history c LEFT ANTI JOIN {c}.silver.heats_coils m USING(coil_id)",
        "missing_mtc": f"SELECT count(*) n FROM {c}.silver.heats_coils m LEFT ANTI JOIN {c}.silver.mill_test_certs t USING(coil_id)",
        "missing_customer": f"SELECT count(*) n FROM {c}.gold.claims_history c LEFT ANTI JOIN {c}.silver.customers d USING(customer_id)",
        "missing_supplier": f"SELECT count(*) n FROM {c}.silver.heats_coils m LEFT ANTI JOIN {c}.silver.suppliers s ON m.coating_supplier_id=s.supplier_id",
        "missing_raw_supplier": f"SELECT count(*) n FROM {c}.silver.heats_coils m LEFT ANTI JOIN {c}.silver.suppliers s ON m.raw_material_supplier_id=s.supplier_id",
        "missing_defect": f"SELECT count(*) n FROM {c}.gold.claims_history c LEFT ANTI JOIN {c}.silver.defect_codes d USING(defect_code)",
        "missing_adjudication": f"SELECT count(*) n FROM {c}.gold.claims_history c LEFT ANTI JOIN {c}.gold.adjudications_history a USING(claim_id)",
        "invalid_duplicate": f"SELECT count(*) n FROM {c}.gold.claims_history d LEFT JOIN {c}.gold.claims_history o ON d.duplicate_of_claim_id=o.claim_id WHERE d.ground_truth_label='duplicate' AND (o.claim_id IS NULL OR NOT(d.coil_id=o.coil_id AND d.customer_id=o.customer_id AND d.defect_code=o.defect_code AND d.claimed_amount=o.claimed_amount AND d.claimed_tonnage=o.claimed_tonnage AND d.defect_narrative=o.defect_narrative AND datediff(d.claim_date,o.claim_date) BETWEEN 0 AND 7))",
        "over_claim_not_partial": f"SELECT count(*) n FROM {c}.gold.claims_history c JOIN {c}.gold.adjudications_history a USING(claim_id) WHERE c.ground_truth_label='over_claim' AND NOT(c.claimed_tonnage>c.shipped_tonnage AND c.claimed_freight>c.freight_cap AND a.approved_amount<c.claimed_amount AND a.approved_amount=cast(c.shipped_tonnage*c.unit_price+c.freight_cap as decimal(18,2)))",
        "supplier_not_traceable": f"SELECT count(*) n FROM {c}.gold.claims_history c JOIN {c}.silver.heats_coils m USING(coil_id) JOIN {c}.silver.mill_test_certs t USING(coil_id) JOIN {c}.gold.adjudications_history a USING(claim_id) WHERE c.ground_truth_label='supplier_attributable' AND NOT(a.supplier_attributable AND a.recovery_supplier_id=m.coating_supplier_id AND NOT t.coating_adhesion_pass)",
        "invalid_money": f"SELECT count(*) n FROM {c}.gold.adjudications_history WHERE approved_amount<0 OR approved_amount>claimed_amount OR (verdict<>'APPROVE' AND (approved_amount<>0 OR disposition IS NOT NULL))",
    }
