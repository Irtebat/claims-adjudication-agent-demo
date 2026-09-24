"""Deterministic adjudication authorities — the money is decided here, not by the LLM.

These are pure functions over a claim's inputs and the structured params fetched
from Lakebase. They are the SINGLE source of the conformance/coverage/settlement
math: exercised directly by the offline test suite and called in-process by
`authorities_runtime.py` on the fetched inputs. (They were previously also
registered as governed UC **Python** functions and invoked over a serverless SQL
warehouse; that added ~5-6 warehouse round-trips per adjudication — tens of seconds
cold — for math that runs in-process in <1µs, so the warehouse was removed from the
operational path.)

The functions are pure: they take the MTC measurements and the structured params
as inputs and return a verdict, with no network or DB I/O. The agent/app runtime
fetches the params (from `spec_params` / `warranty_terms`) and the coil MTC over the
Lakebase psycopg path and passes them straight in.
"""

from __future__ import annotations

from datetime import date


def _as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def completed_months(ship, claim) -> int:
    """Whole completed months between shipment and claim (matches floor months_between)."""
    s, c = _as_date(ship), _as_date(claim)
    months = (c.year - s.year) * 12 + (c.month - s.month)
    if c.day < s.day:
        months -= 1
    return months


def _in_range(value, low, high) -> bool:
    return value is not None and float(low) <= float(value) <= float(high)


def _num(value, default):
    """Return float(value), or the default only when value is None (0.0 is respected)."""
    return default if value is None else float(value)


def compute_conformance(spec_params: dict, measured: dict) -> dict:
    """MTC measured values vs the ordered grade spec ranges (chemistry, mechanicals,
    gauge, width, coating weight, adhesion).

    ``conforms=True`` means the material was actually in spec, so a
    material-nonconformance claim on it should be DENIED. Only measured values that
    are present are checked; a value out of range names its property in
    ``nonconforming_properties``.
    """
    nonconforming = []
    checks = (
        ("carbon_pct", "carbon_pct_min", "carbon_pct_max"),
        ("manganese_pct", "manganese_pct_min", "manganese_pct_max"),
        ("yield_mpa", "yield_mpa_min", "yield_mpa_max"),
        ("tensile_mpa", "tensile_mpa_min", "tensile_mpa_max"),
        ("elongation_pct", "elongation_pct_min", "elongation_pct_max"),
    )
    for prop, lo, hi in checks:
        value = measured.get(prop)
        if value is not None and not _in_range(value, spec_params[lo], spec_params[hi]):
            nonconforming.append(prop)

    gauge, ordered_gauge = measured.get("gauge_mm"), measured.get("ordered_gauge_mm")
    if gauge is not None and ordered_gauge is not None:
        if abs(float(gauge) - float(ordered_gauge)) > float(spec_params["gauge_tolerance_mm"]):
            nonconforming.append("gauge_mm")
    width, ordered_width = measured.get("width_mm"), measured.get("ordered_width_mm")
    if width is not None and ordered_width is not None:
        if abs(float(width) - float(ordered_width)) > float(spec_params["width_tolerance_mm"]):
            nonconforming.append("width_mm")
    coating = measured.get("coating_weight_g_m2")
    if coating is not None and float(coating) < float(spec_params["min_coating_g_m2"]):
        nonconforming.append("coating_weight_g_m2")
    adhesion = measured.get("coating_adhesion_pass")
    if adhesion is not None and spec_params.get("coating_adhesion_required") and not adhesion:
        nonconforming.append("coating_adhesion")

    return {
        "conforms": len(nonconforming) == 0,
        "nonconforming_properties": nonconforming,
    }


def compute_coverage(warranty_terms: dict, claim: dict) -> dict:
    """Warranty duration + environment/installation exclusions (coating warranties).

    ``covered=True`` means the field-corrosion claim is inside the warranty and not
    excluded. Returns the proration factor for the settlement authority.
    """
    elapsed = completed_months(claim["ship_date"], claim["claim_date"])
    duration = int(warranty_terms["duration_months"])
    full = int(warranty_terms["full_coverage_months"])
    exclusions_hit = []
    if elapsed > duration:
        exclusions_hit.append("duration_expired")
    if claim.get("environment") in warranty_terms["excluded_environments"]:
        exclusions_hit.append("environment:" + str(claim.get("environment")))
    if claim.get("installation") in warranty_terms["excluded_installations"]:
        exclusions_hit.append("installation:" + str(claim.get("installation")))
    coast = claim.get("coast_distance_km")
    if coast is not None and float(coast) < float(warranty_terms["min_coast_distance_km"]):
        exclusions_hit.append("coast_distance")

    if elapsed <= full:
        proration = 1.0
    elif duration == full:
        proration = 0.0
    else:
        proration = max(0.0, (duration - elapsed) / (duration - full))

    return {
        "covered": len(exclusions_hit) == 0,
        "elapsed_months": elapsed,
        "proration_factor": round(proration, 6),
        "exclusions_hit": exclusions_hit,
    }


def compute_settlement(inputs: dict) -> dict:
    """Authoritative payout and over-claim guard.

    Covered tonnage is capped at shipped tonnage; material-claim freight is capped
    at ``freight_cap``; coating-warranty claims carry no freight and are prorated.
    A partial approval is simply ``approved_amount < claimed_amount``.
    """
    claim_type = inputs["claim_type"]
    shipped = float(inputs["shipped_tonnage"])
    unit_price = float(inputs["unit_price"])
    claimed_tonnage = float(inputs["claimed_tonnage"])
    # Explicit None checks: a legitimate 0.0 (e.g. proration_factor at the warranty
    # duration boundary, a zero freight cap) must be respected, never coerced to a
    # default by a falsy `or`.
    claimed_freight = _num(inputs.get("claimed_freight"), 0.0)
    freight_cap = _num(inputs.get("freight_cap"), 0.0)
    proration = _num(inputs.get("proration_factor"), 1.0)

    covered_tonnage = min(claimed_tonnage, shipped)
    base = covered_tonnage * unit_price
    if claim_type == "coating_warranty":
        base *= proration
        freight_amount = 0.0
    else:
        freight_amount = min(claimed_freight, freight_cap)
    approved = round(base + freight_amount, 2)
    claimed_amount = round(claimed_tonnage * unit_price + claimed_freight, 2)
    return {
        "approved_amount": approved,
        "claimed_amount": claimed_amount,
        "covered_tonnage": round(covered_tonnage, 3),
        "freight_amount": round(freight_amount, 2),
        "freight_covered": freight_amount > 0,
        "over_claim_detected": claimed_tonnage > shipped or claimed_freight > freight_cap,
        "is_partial": approved < claimed_amount,
    }
