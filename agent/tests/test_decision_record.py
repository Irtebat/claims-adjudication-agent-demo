"""The money invariants: the LLM can never override an authority's number or verdict.

These tests exercise the deterministic outcome and ``enforce_invariants`` against
the injected label patterns, plus the recommendation schema validation and the
canonical decision-record payload shape.
"""

import pytest

from decision_record import (
    DECISION_RECORD_COLUMNS,
    JSONB_COLUMNS,
    Recommendation,
    adjudication_verdict,
    build_decision_record,
    build_idempotency_key,
    deterministic_outcome,
    enforce_invariants,
    recommendation_json_schema,
)

APPROVE_SETTLEMENT = {
    "approved_amount": 5000.0,
    "claimed_amount": 9000.0,
    "covered_tonnage": 5.0,
    "freight_amount": 0.0,
    "freight_covered": False,
    "over_claim_detected": True,
    "is_partial": True,
}
NO_DUP = {"is_duplicate": False, "duplicate_of_claim_id": None}
DUP = {"is_duplicate": True, "duplicate_of_claim_id": "CLM-1"}
CONFORMS = {"conforms": True, "nonconforming_properties": []}
NONCONFORMS = {"conforms": False, "nonconforming_properties": ["tensile_mpa"]}
COVERED = {"covered": True, "elapsed_months": 10, "proration_factor": 1.0, "exclusions_hit": []}
NOT_COVERED = {
    "covered": False,
    "elapsed_months": 400,
    "proration_factor": 0.0,
    "exclusions_hit": ["duration_expired"],
}


def _rec(verdict, disposition, estimate, confidence=0.8):
    return {
        "recommended_verdict": verdict,
        "recommended_disposition": disposition,
        "settlement_estimate": estimate,
        "cited_clause_ids": ["A653/NA/DEMO-1990/mechanical"],
        "precedent": [],
        "rationale": "test",
        "flags": {"supplier_attributable": False, "fraud_risk": False, "over_claim": True},
        "confidence": confidence,
    }


# --- deterministic outcome -------------------------------------------------- #
def test_in_spec_material_is_denied():
    out = deterministic_outcome(
        "material_nonconformance", CONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    assert out["verdict"] == "DENY" and out["disposition"] == "DENY"
    assert out["approved_amount"] == 0.0 and out["reason"] == "in_spec_per_mtc"


def test_nonconforming_material_is_approved_for_authority_amount():
    out = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    assert out["verdict"] == "APPROVE" and out["approved_amount"] == 5000.0


def test_out_of_warranty_is_denied():
    out = deterministic_outcome(
        "coating_warranty", CONFORMS, NOT_COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    assert out["verdict"] == "DENY" and out["reason"] == "not_covered"


def test_duplicate_is_denied_as_duplicate():
    out = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, DUP
    )
    assert out["verdict"] == "DENY" and out["disposition"] == "DUPLICATE"
    assert out["approved_amount"] == 0.0 and out["duplicate_of_claim_id"] == "CLM-1"


def test_unknown_claim_type_is_held_for_investigation():
    out = deterministic_outcome("unknown_type", CONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP)
    assert out["verdict"] == "PEND_INVESTIGATE"
    assert out["eligible"] is not True
    assert out["approved_amount"] == 0.0


# --- invariant enforcement (the LLM never wins) ----------------------------- #
def test_duplicate_cannot_be_recommended_for_payment():
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, DUP
    )
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 5000.0), det)
    assert corrected["recommended_verdict"] == "DENY"
    assert corrected["recommended_disposition"] == "DUPLICATE"
    assert corrected["approved_amount"] == 0.0
    assert "duplicate_must_be_deny_duplicate" in violations


def test_cannot_approve_an_in_spec_claim():
    det = deterministic_outcome(
        "material_nonconformance", CONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 5000.0), det)
    assert corrected["recommended_verdict"] == "DENY"
    assert corrected["approved_amount"] == 0.0
    assert "cannot_approve_ineligible_claim" in violations


def test_cannot_approve_an_unknown_claim_type():
    det = deterministic_outcome("unknown_type", CONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP)
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 5000.0), det)
    assert corrected["recommended_verdict"] == det["verdict"]
    assert corrected["recommended_disposition"] == det["disposition"]
    assert corrected["approved_amount"] == 0.0
    assert "cannot_approve_ineligible_claim" in violations


def test_cannot_deny_an_eligible_claim_only_hold():
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(_rec("DENY", "DENY", 5000.0), det)
    assert corrected["recommended_verdict"] == "PEND_INVESTIGATE"
    assert corrected["approved_amount"] == 0.0
    assert "cannot_deny_eligible_claim" in violations


def test_approved_amount_equals_settlement_authority_exactly_on_approve():
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 5000.0), det)
    assert corrected["approved_amount"] == APPROVE_SETTLEMENT["approved_amount"]
    assert corrected["settlement_estimate"] == APPROVE_SETTLEMENT["approved_amount"]
    assert violations == []


def test_llm_inventing_a_bigger_amount_is_corrected():
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 999999.0), det)
    assert corrected["settlement_estimate"] == 5000.0
    assert corrected["approved_amount"] == 5000.0
    assert "settlement_estimate_overridden" in violations


def test_pend_hold_is_allowed_and_pays_zero():
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(
        _rec("PEND_INVESTIGATE", "PEND_INVESTIGATE", 5000.0), det
    )
    assert corrected["recommended_verdict"] == "PEND_INVESTIGATE"
    assert corrected["approved_amount"] == 0.0
    assert violations == []


# --- schema + verdict mapping ---------------------------------------------- #
def test_recommendation_schema_validates_and_rejects_bad_confidence():
    Recommendation.model_validate(_rec("APPROVE", "CREDIT", 5000.0))
    with pytest.raises(Exception):
        Recommendation.model_validate(_rec("APPROVE", "CREDIT", 5000.0, confidence=2.0))


def test_recommendation_json_schema_is_response_format():
    schema = recommendation_json_schema()
    assert schema["type"] == "json_schema"
    assert schema["json_schema"]["name"] == "adjudication_recommendation"


def test_verdict_mapping_to_operational_value():
    assert adjudication_verdict("PEND_INVESTIGATE") == "PEND"
    assert adjudication_verdict("APPROVE") == "APPROVE"
    assert adjudication_verdict("DENY") == "DENY"


# --- canonical payload ------------------------------------------------------ #
def _payload():
    claim = {"claim_id": "CLM-9", "coil_id": "COIL-1", "claim_type": "material_nonconformance"}
    resolved = {
        "spec_provenance": {"grade": "G", "spec_edition": "E", "region": "NA"},
        "warranty_provenance": {"product_line": "galvanized", "region": "NA", "version": "V1"},
        "spec_params": {"carbon_pct_min": 0.02},
        "warranty_terms": {"duration_months": 240},
        "coil": {"ship_date": "2020-01-01"},
        "freight_cap": 500.0,
    }
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 5000.0), det)
    citations = [
        {
            "citation_key": "G/NA/E/mechanical",
            "section_ref": "mechanical",
            "clause_text_sha256": "abc",
        }
    ]
    return build_decision_record(
        claim=claim,
        resolved=resolved,
        measured={"tensile_mpa": 620.0},
        conformance=NONCONFORMS,
        coverage=COVERED,
        settlement=APPROVE_SETTLEMENT,
        duplicate=NO_DUP,
        deterministic=det,
        recommendation=corrected,
        invariant_violations=violations,
        citations=citations,
        advisory_risk={"risk_score": 0.1},
        reproducibility={"authorities_source_sha256": "deadbeef", "agent_model_name": "m"},
    )


def test_decision_record_has_all_columns_and_derived_fields():
    record = _payload()
    assert set(DECISION_RECORD_COLUMNS) <= set(record)
    assert record["cited_clause_ids"] == ["G/NA/E/mechanical"]
    assert record["approved_amount"] == 5000.0
    assert record["duplicate_flag"] is False
    assert record["over_claim_flag"] is True
    assert record["deterministic_verdict"] == "APPROVE"
    # JSONB columns stay as dict/list for the writer to bind.
    for column in JSONB_COLUMNS:
        assert isinstance(record[column], (dict, list))


def test_idempotency_key_is_stable():
    assert build_idempotency_key("CLM-9", "deadbeef", 1) == build_idempotency_key(
        "CLM-9", "deadbeef", 1
    )
    assert build_idempotency_key("CLM-9", "deadbeef", 1) != build_idempotency_key(
        "CLM-9", "deadbeef", 2
    )
