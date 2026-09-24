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
        "duplicate_coil_id": f"SELECT count(*) n FROM (SELECT coil_id FROM {c}.silver.heats_coils GROUP BY coil_id HAVING count(*) > 1)",
        "multiple_heats_per_coil": f"SELECT count(*) n FROM (SELECT coil_id FROM {c}.silver.heats_coils GROUP BY coil_id HAVING count(DISTINCT heat_no) > 1)",
        "missing_coil": f"SELECT count(*) n FROM {c}.gold.claims_current c LEFT ANTI JOIN {c}.silver.heats_coils m USING(coil_id)",
        "missing_mtc": f"SELECT count(*) n FROM {c}.silver.heats_coils m LEFT ANTI JOIN {c}.silver.mill_test_certs t USING(coil_id)",
        "missing_customer": f"SELECT count(*) n FROM {c}.gold.claims_current c LEFT ANTI JOIN {c}.silver.customers d USING(customer_id)",
        "missing_supplier": f"SELECT count(*) n FROM {c}.silver.heats_coils m LEFT ANTI JOIN {c}.silver.suppliers s ON m.coating_supplier_id=s.supplier_id",
        "missing_raw_supplier": f"SELECT count(*) n FROM {c}.silver.heats_coils m LEFT ANTI JOIN {c}.silver.suppliers s ON m.raw_material_supplier_id=s.supplier_id",
        "missing_defect": f"SELECT count(*) n FROM {c}.gold.claims_current c LEFT ANTI JOIN {c}.silver.defect_codes d USING(defect_code)",
        "missing_adjudication": f"SELECT count(*) n FROM {c}.gold.claims_current c LEFT ANTI JOIN {c}.gold.adjudications_current a USING(claim_id)",
        "invalid_duplicate": f"SELECT count(*) n FROM {c}.gold.adjudications_current d LEFT JOIN {c}.gold.claims_current c USING(claim_id) LEFT JOIN {c}.gold.claims_current o ON d.duplicate_of_claim_id=o.claim_id WHERE d.disposition='DUPLICATE' AND (d.verdict<>'DENY' OR o.claim_id IS NULL OR c.coil_id<>o.coil_id)",
        "invalid_final_status": f"SELECT count(*) n FROM {c}.gold.adjudications_current WHERE decision_status='FINAL' AND verdict NOT IN ('APPROVE','DENY','PEND')",
        "invalid_money": f"SELECT count(*) n FROM {c}.gold.adjudications_current WHERE approved_amount<0 OR approved_amount>claimed_amount OR (verdict<>'APPROVE' AND approved_amount<>0)",
        "multiple_current_claims": f"SELECT count(*) n FROM (SELECT claim_id FROM {c}.silver.claims_history WHERE __END_AT IS NULL GROUP BY claim_id HAVING count(*) > 1)",
        "multiple_current_adjudications": f"SELECT count(*) n FROM (SELECT adjudication_id FROM {c}.silver.adjudications_history WHERE __END_AT IS NULL GROUP BY adjudication_id HAVING count(*) > 1)",
        "invalid_claim_intervals": f"SELECT count(*) n FROM {c}.silver.claims_history WHERE __END_AT IS NOT NULL AND __START_AT >= __END_AT",
        "invalid_adjudication_intervals": f"SELECT count(*) n FROM {c}.silver.adjudications_history WHERE __END_AT IS NOT NULL AND __START_AT >= __END_AT",
        "overlapping_claim_intervals": f"SELECT count(*) n FROM (SELECT __START_AT, lag(__END_AT) OVER (PARTITION BY claim_id ORDER BY __START_AT) previous_end, row_number() OVER (PARTITION BY claim_id ORDER BY __START_AT) version_number FROM {c}.silver.claims_history) WHERE version_number > 1 AND (previous_end IS NULL OR __START_AT < previous_end)",
        "overlapping_adjudication_intervals": f"SELECT count(*) n FROM (SELECT __START_AT, lag(__END_AT) OVER (PARTITION BY adjudication_id ORDER BY __START_AT) previous_end, row_number() OVER (PARTITION BY adjudication_id ORDER BY __START_AT) version_number FROM {c}.silver.adjudications_history) WHERE version_number > 1 AND (previous_end IS NULL OR __START_AT < previous_end)",
        "orphan_current_adjudication": f"SELECT count(*) n FROM {c}.gold.adjudications_current a LEFT ANTI JOIN {c}.gold.claims_current c USING(claim_id)",
        "cdf_metadata_in_gold": f"SELECT count(*) n FROM {c}.information_schema.columns WHERE table_schema = 'gold' AND table_name IN ('claims_current','adjudications_current','claims_history','adjudications_history') AND column_name IN ('_pg_change_type','_pg_lsn','_pg_xid','_timestamp','_sort_by')",
    }
