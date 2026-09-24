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
        "invalid_duplicate": f"SELECT count(*) n FROM {c}.gold.adjudications_history d LEFT JOIN {c}.gold.claims_history c USING(claim_id) LEFT JOIN {c}.gold.claims_history o ON d.duplicate_of_claim_id=o.claim_id WHERE d.disposition='DUPLICATE' AND (d.verdict<>'DENY' OR o.claim_id IS NULL OR c.coil_id<>o.coil_id)",
        "invalid_final_status": f"SELECT count(*) n FROM {c}.gold.adjudications_history WHERE decision_status='FINAL' AND verdict NOT IN ('APPROVE','DENY','PEND')",
        "invalid_money": f"SELECT count(*) n FROM {c}.gold.adjudications_history WHERE approved_amount<0 OR approved_amount>claimed_amount OR (verdict<>'APPROVE' AND approved_amount<>0)",
    }
