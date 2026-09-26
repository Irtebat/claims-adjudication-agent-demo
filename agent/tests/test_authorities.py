"""Deterministic authorities produce the expected tool outputs for each Wave-1 pattern.

These are the invariants that used to live in the pipeline's checks.py, now asserted
directly against the authorities using the authored params.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "lakebase/src"))

from policy_schema import parse_policies

from authorities import completed_months, compute_conformance, compute_coverage, compute_settlement

PARSED = parse_policies()
SPEC = next(
    r for r in PARSED["spec_params"] if r["grade"] == "ASTM A653 CS Type B" and r["region"] == "NA"
)
W_V2 = next(
    r
    for r in PARSED["warranty_terms"]
    if r["product_line"] == "galvanized" and r["region"] == "NA" and r["version"] == "V2"
)
W_V1 = next(
    r
    for r in PARSED["warranty_terms"]
    if r["product_line"] == "galvanized" and r["region"] == "NA" and r["version"] == "V1"
)

# The MTC values generate.py stamps: in-spec vs the injected tensile deviation.
IN_SPEC = {
    "carbon_pct": 0.08,
    "manganese_pct": 0.40,
    "yield_mpa": 250.0,
    "tensile_mpa": 400.0,
    "elongation_pct": 30.0,
}
OUT_OF_SPEC = {**IN_SPEC, "tensile_mpa": 620.0}


def test_in_spec_should_deny_conforms():
    # in_spec_should_deny: the MTC proves the material was in spec -> claim denied
    result = compute_conformance(SPEC, IN_SPEC)
    assert result["conforms"] is True
    assert result["nonconforming_properties"] == []


def test_genuine_nonconformance_flags_tensile():
    # clean material claim (real deviation) and over_claim both carry tensile 620
    result = compute_conformance(SPEC, OUT_OF_SPEC)
    assert result["conforms"] is False
    assert "tensile_mpa" in result["nonconforming_properties"]


def test_supplier_attributable_adhesion_failure():
    result = compute_conformance(SPEC, {"coating_adhesion_pass": False})
    assert result["conforms"] is False
    assert "coating_adhesion" in result["nonconforming_properties"]


def test_gauge_out_of_tolerance():
    # |1.20 - 1.00| = 0.20 > 0.05 gauge tolerance
    result = compute_conformance(SPEC, {"gauge_mm": 1.20, "ordered_gauge_mm": 1.00})
    assert result["conforms"] is False
    assert "gauge_mm" in result["nonconforming_properties"]


def test_width_out_of_tolerance():
    # |1205 - 1200| = 5 > 2.0 width tolerance
    result = compute_conformance(SPEC, {"width_mm": 1205.0, "ordered_width_mm": 1200.0})
    assert result["conforms"] is False
    assert "width_mm" in result["nonconforming_properties"]


def test_dimensions_within_tolerance_conform():
    result = compute_conformance(
        SPEC,
        {
            "gauge_mm": 1.01,
            "ordered_gauge_mm": 1.00,
            "width_mm": 1201.0,
            "ordered_width_mm": 1200.0,
            "coating_weight_g_m2": 280.0,
            "coating_adhesion_pass": True,
        },
    )
    assert result["conforms"] is True


def test_coverage_expired_denied():
    result = compute_coverage(
        W_V1,
        {
            "ship_date": "1999-01-01",
            "claim_date": "2026-01-01",
            "environment": "inland",
            "installation": "ventilated",
            "coast_distance_km": 25.0,
        },
    )
    assert result["covered"] is False
    assert "duration_expired" in result["exclusions_hit"]


@pytest.mark.parametrize(
    "field,value,tag",
    [
        ("environment", "marine", "environment:marine"),
        ("installation", "standing_water", "installation:standing_water"),
        ("coast_distance_km", 0.5, "coast_distance"),
    ],
)
def test_coverage_environment_exclusions(field, value, tag):
    claim = {
        "ship_date": "2018-01-01",
        "claim_date": "2026-01-01",
        "environment": "inland",
        "installation": "ventilated",
        "coast_distance_km": 25.0,
    }
    claim[field] = value
    result = compute_coverage(W_V2, claim)
    assert result["covered"] is False
    assert tag in result["exclusions_hit"]


def test_coverage_clean_warranty_covered_with_proration():
    # ship 2014 -> V1 (240/60); ~144 months elapsed -> prorated below 1.0 but > 0
    result = compute_coverage(
        W_V1,
        {
            "ship_date": "2014-01-01",
            "claim_date": "2026-01-01",
            "environment": "inland",
            "installation": "ventilated",
            "coast_distance_km": 25.0,
        },
    )
    assert result["covered"] is True
    assert result["elapsed_months"] == 144
    assert result["proration_factor"] == pytest.approx((240 - 144) / (240 - 60))


def test_settlement_over_claim_is_capped_and_partial():
    result = compute_settlement(
        {
            "claim_type": "material_nonconformance",
            "shipped_tonnage": 10.0,
            "unit_price": 1000.0,
            "claimed_tonnage": 14.0,
            "claimed_freight": 1800.0,
            "freight_cap": 500.0,
        }
    )
    assert result["covered_tonnage"] == 10.0  # capped at shipped
    assert result["freight_amount"] == 500.0  # capped at freight_cap
    assert result["approved_amount"] == 10500.0
    assert result["over_claim_detected"] is True
    assert result["is_partial"] is True


def test_settlement_clean_material_full():
    result = compute_settlement(
        {
            "claim_type": "material_nonconformance",
            "shipped_tonnage": 10.0,
            "unit_price": 1000.0,
            "claimed_tonnage": 10.0,
            "claimed_freight": 0.0,
            "freight_cap": 500.0,
        }
    )
    assert result["approved_amount"] == 10000.0
    assert result["over_claim_detected"] is False
    assert result["is_partial"] is False


def test_settlement_warranty_prorated_no_freight():
    factor = (240 - 144) / (240 - 60)
    result = compute_settlement(
        {
            "claim_type": "coating_warranty",
            "shipped_tonnage": 10.0,
            "unit_price": 1000.0,
            "claimed_tonnage": 10.0,
            "claimed_freight": 0.0,
            "freight_cap": 500.0,
            "proration_factor": factor,
        }
    )
    assert result["freight_amount"] == 0.0
    assert result["approved_amount"] == pytest.approx(10000.0 * factor, abs=0.01)


def test_coverage_at_duration_boundary_is_covered_with_zero_proration():
    # elapsed == duration (240) for V1: still covered (not > duration), proration 0.0
    result = compute_coverage(
        W_V1,
        {
            "ship_date": "2014-01-01",
            "claim_date": "2034-01-01",
            "environment": "inland",
            "installation": "ventilated",
            "coast_distance_km": 25.0,
        },
    )
    assert result["covered"] is True
    assert result["elapsed_months"] == 240
    assert result["proration_factor"] == 0.0


def test_settlement_zero_proration_pays_zero():
    # Regression: a legitimate proration_factor of 0.0 must NOT be coerced to 1.0.
    result = compute_settlement(
        {
            "claim_type": "coating_warranty",
            "shipped_tonnage": 10.0,
            "unit_price": 1000.0,
            "claimed_tonnage": 10.0,
            "claimed_freight": 0.0,
            "freight_cap": 500.0,
            "proration_factor": 0.0,
        }
    )
    assert result["approved_amount"] == 0.0
    assert result["is_partial"] is True


def test_settlement_zero_freight_cap_respected():
    # Regression: a zero freight_cap must cap material freight at 0, not default.
    result = compute_settlement(
        {
            "claim_type": "material_nonconformance",
            "shipped_tonnage": 10.0,
            "unit_price": 1000.0,
            "claimed_tonnage": 10.0,
            "claimed_freight": 1800.0,
            "freight_cap": 0.0,
        }
    )
    assert result["freight_amount"] == 0.0
    assert result["approved_amount"] == 10000.0
    assert result["over_claim_detected"] is True  # claimed_freight 1800 > cap 0


def test_completed_months_helper():
    assert completed_months("2014-01-01", "2026-01-01") == 144
    assert completed_months("2025-12-01", "2026-01-15") == 1
