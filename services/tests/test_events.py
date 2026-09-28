"""The event payload contract + stable dedup ids (single source of truth)."""

import json
from datetime import date
from decimal import Decimal

import events


def test_submitted_event_shape_and_stable_id():
    claim = {f: f"v-{f}" for f in events.CLAIM_FIELDS}
    claim["claim_id"] = "CLM-1"
    ev = events.build_submitted_event(claim, source={"cdf_change_type": "insert"})
    assert ev["event_id"] == "sub-CLM-1"  # stable: one submission id per claim
    assert ev["event_type"] == events.EVENT_CLAIM_SUBMITTED
    assert ev["schema_version"] == events.SCHEMA_VERSION
    assert ev["claim_id"] == "CLM-1"
    assert set(ev["claim"]) == set(events.CLAIM_FIELDS)
    assert ev["source"]["cdf_change_type"] == "insert"


def test_submitted_event_coerces_non_json_scalars():
    claim = {f: None for f in events.CLAIM_FIELDS}
    claim["claim_id"] = "CLM-2"
    claim["claimed_tonnage"] = Decimal("12.5")
    claim["claim_date"] = date(2026, 1, 2)
    ev = events.build_submitted_event(claim)
    # Round-trips through JSON without error (Decimal -> float, date -> isoformat).
    reloaded = json.loads(events.serialize(ev))
    assert reloaded["claim"]["claimed_tonnage"] == 12.5
    assert reloaded["claim"]["claim_date"] == "2026-01-02"


def test_adjudicated_payload_shape_and_verdict_passthrough():
    record = {
        "claim_id": "CLM-9",
        "adjudication_id": "ADJ-abc",
        "recommended_verdict": "PEND_INVESTIGATE",
        "recommended_disposition": "PEND_INVESTIGATE",
        "approved_amount": Decimal("0"),
        "recovery_supplier_id": "SUP-7",
        "idempotency_key": "abc",
        "flags": {"supplier_attributable": True},
        "duplicate": {"duplicate_of_claim_id": "CLM-1"},
    }
    ev = events.build_adjudicated_payload(
        record, verdict="PEND", event_id=events.adjudicated_event_id("ADJ-abc")
    )
    assert ev["event_id"] == "adj-ADJ-abc"
    assert ev["event_type"] == events.EVENT_CLAIM_ADJUDICATED
    assert ev["verdict"] == "PEND"
    assert ev["recommended_verdict"] == "PEND_INVESTIGATE"
    assert ev["supplier_attributable"] is True
    assert ev["recovery_supplier_id"] == "SUP-7"
    assert ev["duplicate_of_claim_id"] == "CLM-1"
    assert ev["approved_amount"] == 0.0  # Decimal coerced


def test_case_ids_are_deterministic():
    assert events.submitted_event_id("CLM-1") == "sub-CLM-1"
    assert events.adjudicated_event_id("ADJ-1") == "adj-ADJ-1"
    assert events.settlement_id("CLM-1") == "STL-CLM-1"
    assert events.investigation_case_id("CLM-1") == "INV-CLM-1"
    assert events.supplier_recovery_case_id("CLM-1") == "SRC-CLM-1"


def test_serialize_deserialize_round_trip():
    ev = {"event_id": "sub-1", "claim_id": "1", "n": 3}
    assert events.deserialize(events.serialize(ev)) == ev
    assert events.deserialize(events.serialize(ev).decode("utf-8")) == ev
