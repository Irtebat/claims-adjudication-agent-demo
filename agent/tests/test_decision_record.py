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
    validate_narrative_conflict,
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


def test_narrative_conflict_requires_verbatim_quote_and_retrieved_clause():
    claim = {"defect_narrative": "The site is 0.6 km from the shoreline."}
    clauses = [{"citation_key": "galvanized/NA/V2/exclusions"}]
    rec = {
        **_rec("PEND_INVESTIGATE", "PEND_INVESTIGATE", 5000.0),
        "narrative_conflict": {
            "clause": "galvanized/NA/V2/exclusions",
            "narrative_quote": "0.6 km from the shoreline",
            "structured_field": "coast_distance_km=10.0",
        },
    }
    assert (
        validate_narrative_conflict(rec, claim, clauses)["narrative_conflict"]
        == rec["narrative_conflict"]
    )
    rec["narrative_conflict"]["narrative_quote"] = "1.2 km from the shoreline"
    assert "narrative_conflict" not in validate_narrative_conflict(rec, claim, clauses)
    rec["narrative_conflict"]["narrative_quote"] = "0.6 km from the shoreline"
    assert "narrative_conflict" not in validate_narrative_conflict(rec, claim, [])


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


def test_validated_narrative_conflict_is_persisted_in_flags():
    rec = _rec("PEND_INVESTIGATE", "PEND_INVESTIGATE", 5000.0)
    rec["narrative_conflict"] = {
        "clause": "G/NA/E/mechanical",
        "narrative_quote": "measured below minimum",
        "structured_field": "tensile_mpa",
    }
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(rec, det)
    payload = _payload()
    payload = build_decision_record(
        claim=payload["claim_input"],
        resolved={
            "spec_provenance": payload["spec_provenance"],
            "warranty_provenance": payload["warranty_provenance"],
            "spec_params": payload["spec_params"],
            "warranty_terms": payload["warranty_terms"],
            "coil": payload["coil"],
            "freight_cap": payload["freight_cap"],
        },
        measured=payload["mtc_measured"],
        conformance=NONCONFORMS,
        coverage=COVERED,
        settlement=APPROVE_SETTLEMENT,
        duplicate=NO_DUP,
        deterministic=det,
        recommendation=corrected,
        invariant_violations=violations,
        citations=payload["citations"],
        advisory_risk=payload["advisory_risk"],
        reproducibility={"authorities_source_sha256": "deadbeef"},
    )
    assert payload["narrative_conflict"] == rec["narrative_conflict"]
    assert payload["flags"]["narrative_conflict"] == rec["narrative_conflict"]


def test_genie_consultations_persisted_in_flags():
    # The live Genie tool audit trail is folded into flags (JSONB) so it persists on
    # the decision record and the adjudications row without a schema change — the same
    # mechanism as narrative_conflict. Advisory audit only; never affects money.
    det = deterministic_outcome(
        "material_nonconformance", NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    corrected, violations = enforce_invariants(_rec("APPROVE", "CREDIT", 5000.0), det)
    consultations = [
        {
            "space": "operational",
            "space_id": "01f1c269ca3c1adea7feb9f248ab3445",
            "question": "how were prior claims on this heat adjudicated?",
            "status": "ok",
            "answer": "3 prior claims on this heat; all denied as in-spec.",
            "generated_sql": "SELECT ...",
            "latency_ms": 1200,
        }
    ]
    kwargs = dict(
        claim={"claim_id": "CLM-9", "coil_id": "COIL-1", "claim_type": "material_nonconformance"},
        resolved={},
        measured={},
        conformance=NONCONFORMS,
        coverage=COVERED,
        settlement=APPROVE_SETTLEMENT,
        duplicate=NO_DUP,
        deterministic=det,
        recommendation=corrected,
        invariant_violations=violations,
        citations=[],
        advisory_risk=None,
        reproducibility={"authorities_source_sha256": "deadbeef"},
    )
    with_genie = build_decision_record(**kwargs, genie_consultations=consultations)
    assert with_genie["flags"]["genie_consultations"] == consultations
    # Absent / empty consultations leave flags clean (no spurious key).
    assert "genie_consultations" not in build_decision_record(**kwargs)["flags"]
    assert (
        "genie_consultations"
        not in build_decision_record(**kwargs, genie_consultations=[])["flags"]
    )


# --- supplier-recovery derivation + routing -------------------------------- #
# Materially-nonconforming coating-adhesion failure => supplier-attributable (R5).
COATING_NONCONFORMS = {"conforms": False, "nonconforming_properties": ["coating_adhesion"]}


def _supplier_record(*, attributable: bool, coating_supplier_id="SUP-00"):
    """Build a decision record for a material-nonconformance adjudication whose coil
    carries a coating supplier, flagging supplier_attributable per the argument."""
    claim = {"claim_id": "CLM-9", "coil_id": "COIL-1", "claim_type": "material_nonconformance"}
    resolved = {
        "spec_provenance": {"grade": "G", "spec_edition": "E", "region": "NA"},
        "warranty_provenance": {"product_line": "galvanized", "region": "NA", "version": "V1"},
        "spec_params": {},
        "warranty_terms": {},
        "coil": {"ship_date": "2020-01-01", "coating_supplier_id": coating_supplier_id},
        "freight_cap": 500.0,
    }
    det = deterministic_outcome(
        "material_nonconformance", COATING_NONCONFORMS, COVERED, APPROVE_SETTLEMENT, NO_DUP
    )
    rec = _rec("APPROVE", "REPLACEMENT", 5000.0)
    rec["flags"] = {"supplier_attributable": attributable, "fraud_risk": False, "over_claim": True}
    corrected, violations = enforce_invariants(rec, det)
    return build_decision_record(
        claim=claim,
        resolved=resolved,
        measured={},
        conformance=COATING_NONCONFORMS,
        coverage=COVERED,
        settlement=APPROVE_SETTLEMENT,
        duplicate=NO_DUP,
        deterministic=det,
        recommendation=corrected,
        invariant_violations=violations,
        citations=[],
        advisory_risk=None,
        reproducibility={"authorities_source_sha256": "deadbeef"},
    )


def test_supplier_attributable_adjudication_routes_recovery_to_coil_coating_supplier():
    """supplier-attributable -> recovery_supplier_id populated from the coil's coating
    supplier, projected onto the adjudications row the finalize event sources from, and
    the supplier-recovery consumer predicate passes. Mirrors the seeded derivation."""
    import writer

    record = _supplier_record(attributable=True)
    # (1) Derivation: the coil's coating supplier becomes the recovery target.
    assert record["recovery_supplier_id"] == "SUP-00"

    # (2) Persistence: the writer projects it (and the matching supplier_attributable
    # flag) onto the adjudications row — the exact row the App's finalize transaction
    # reads to build the claim.adjudicated outbox event's recovery_supplier_id.
    adj = writer._adjudication_row(record)
    assert adj["supplier_attributable"] is True
    assert adj["recovery_supplier_id"] == "SUP-00"

    # (3) Consumer-path predicate (services/src/consumer_core.py SUPPLIER_RECOVERY guard:
    # `supplier_attributable AND recovery_supplier_id`) fires -> a case is opened.
    assert bool(adj["supplier_attributable"] and adj["recovery_supplier_id"]) is True


def test_non_attributable_adjudication_leaves_recovery_null_and_consumer_skips():
    """not supplier-attributable -> recovery_supplier_id is None even though the coil has
    a coating supplier, so the supplier-recovery consumer correctly skips the event."""
    import writer

    record = _supplier_record(attributable=False)
    assert record["recovery_supplier_id"] is None

    adj = writer._adjudication_row(record)
    assert adj["supplier_attributable"] is False
    assert adj["recovery_supplier_id"] is None
    assert bool(adj["supplier_attributable"] and adj["recovery_supplier_id"]) is False


def test_idempotency_key_is_stable():
    assert build_idempotency_key("CLM-9", "deadbeef", 1) == build_idempotency_key(
        "CLM-9", "deadbeef", 1
    )
    assert build_idempotency_key("CLM-9", "deadbeef", 1) != build_idempotency_key(
        "CLM-9", "deadbeef", 2
    )
