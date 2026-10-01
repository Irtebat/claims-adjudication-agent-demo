"""The deterministic disposition/routing rules R1-R7 and the no-LLM baseline.

Each rule is exercised at its boundary through the real authorities, the baseline
is proven to pay exactly the settlement authority amount (or zero), the agent's
``enforce_invariants`` is proven to apply the same disposition, and the rules
module is proven to load no network/LLM/DB code.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import agent_tools
from authorities import compute_conformance, compute_coverage, compute_settlement
from decision_record import deterministic_recommendation, enforce_invariants
from disposition_rules import (
    FRAUD_HOLD_RISK_THRESHOLD,
    approve_disposition,
    over_claim_partial,
    supplier_attributable,
)

SRC = Path(__file__).resolve().parents[1] / "src"

SPEC = {  # ASTM A653 CS Type B, policy_source.json
    "carbon_pct_min": 0.0,
    "carbon_pct_max": 0.15,
    "manganese_pct_min": 0.0,
    "manganese_pct_max": 0.6,
    "yield_mpa_min": 140.0,
    "yield_mpa_max": 350.0,
    "tensile_mpa_min": 270.0,
    "tensile_mpa_max": 550.0,
    "elongation_pct_min": 20.0,
    "elongation_pct_max": 60.0,
    "gauge_tolerance_mm": 0.05,
    "width_tolerance_mm": 2.0,
    "min_coating_g_m2": 275.0,
    "coating_adhesion_required": True,
}
WARRANTY = {
    "duration_months": 360,
    "full_coverage_months": 120,
    "excluded_environments": ["marine", "industrial_aggressive"],
    "excluded_installations": ["unventilated", "standing_water"],
    "min_coast_distance_km": 2.0,
}
IN_SPEC_MTC = {
    "carbon_pct": 0.08,
    "manganese_pct": 0.40,
    "yield_mpa": 250.0,
    "tensile_mpa": 400.0,
    "elongation_pct": 30.0,
    "coating_weight_g_m2": 280.0,
    "coating_adhesion_pass": True,
}
NO_DUP = {"is_duplicate": False, "duplicate_of_claim_id": None}
DUP = {"is_duplicate": True, "duplicate_of_claim_id": "CLM-1"}
SHIPPED, PRICE, CAP = 10.0, 1000.0, 500.0


def _context(
    claim_type="material_nonconformance",
    mtc=None,
    claimed_tonnage=SHIPPED,
    claimed_freight=0.0,
    ship_date="2025-12-01",
    claim_date="2026-01-01",
    environment="inland",
    duplicate=NO_DUP,
    risk=None,
):
    """Run the real authorities over one claim and return the baseline context."""
    measured = {**IN_SPEC_MTC, **(mtc or {})}
    claim = {
        "claim_type": claim_type,
        "ship_date": ship_date,
        "claim_date": claim_date,
        "environment": environment,
        "installation": "ventilated",
        "coast_distance_km": 25.0,
    }
    coverage = compute_coverage(WARRANTY, claim)
    settlement = compute_settlement(
        {
            "claim_type": claim_type,
            "shipped_tonnage": SHIPPED,
            "unit_price": PRICE,
            "claimed_tonnage": claimed_tonnage,
            "claimed_freight": claimed_freight,
            "freight_cap": CAP,
            "proration_factor": coverage["proration_factor"],
        }
    )
    return {
        "claim": claim,
        "conformance": compute_conformance(SPEC, measured),
        "coverage": coverage,
        "settlement": settlement,
        "duplicate": duplicate,
        "risk": risk,
    }


def _outcome(ctx):
    rec = deterministic_recommendation(ctx)
    return rec["verdict"], rec["disposition"]


def _fired(rec):
    return [t["rule"] for t in rec["rule_trace"] if t["fired"]]


NONCONFORMING = {"tensile_mpa": 620.0}
ADHESION_FAIL = {"coating_adhesion_pass": False}


# --- R1 duplicate ----------------------------------------------------------- #
def test_r1_duplicate_is_deny_duplicate_and_beats_fraud_hold():
    rec = deterministic_recommendation(
        _context(mtc=NONCONFORMING, duplicate=DUP, risk={"risk_score": 1.0})
    )
    assert (rec["verdict"], rec["disposition"]) == ("DENY", "DUPLICATE")
    assert rec["approved_amount"] == 0.0
    assert _fired(rec) == ["R1_duplicate"]


# --- R2 unknown claim type -------------------------------------------------- #
def test_r2_unknown_claim_type_is_held():
    ctx = _context(mtc=NONCONFORMING)
    ctx["claim"]["claim_type"] = "something_else"
    rec = deterministic_recommendation(ctx)
    assert (rec["verdict"], rec["disposition"]) == ("PEND_INVESTIGATE", "PEND_INVESTIGATE")
    assert rec["approved_amount"] == 0.0


# --- R3 fraud hold / R4 ineligible ----------------------------------------- #
@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (None, ("DENY", "DENY")),
        ({"risk_score": 0.0}, ("DENY", "DENY")),
        ({"risk_score": FRAUD_HOLD_RISK_THRESHOLD - 0.0001}, ("DENY", "DENY")),
        ({"risk_score": FRAUD_HOLD_RISK_THRESHOLD}, ("PEND_INVESTIGATE", "PEND_INVESTIGATE")),
        ({"risk_score": 0.6}, ("PEND_INVESTIGATE", "PEND_INVESTIGATE")),
    ],
)
def test_r3_r4_in_spec_claim_is_denied_or_held_on_risk_threshold(risk, expected):
    rec = deterministic_recommendation(_context(risk=risk))
    assert (rec["verdict"], rec["disposition"]) == expected
    assert rec["approved_amount"] == 0.0
    assert _fired(rec) == [
        "R3_fraud_hold" if expected[0] == "PEND_INVESTIGATE" else "R4_ineligible"
    ]


def test_r3_applies_to_uncovered_warranty_claim():
    ctx = _context(claim_type="coating_warranty", environment="marine", risk={"risk_score": 0.75})
    assert _outcome(ctx) == ("PEND_INVESTIGATE", "PEND_INVESTIGATE")


def test_r3_does_not_hold_an_eligible_claim_on_risk_alone():
    rec = deterministic_recommendation(_context(mtc=NONCONFORMING, risk={"risk_score": 1.0}))
    assert (rec["verdict"], rec["disposition"]) == ("APPROVE", "CREDIT")
    assert rec["flags"]["fraud_risk"] is True
    assert rec["approved_amount"] == rec["settlement_estimate"] == SHIPPED * PRICE


def test_r4_uncovered_warranty_claim_is_denied():
    assert _outcome(_context(claim_type="coating_warranty", environment="marine")) == (
        "DENY",
        "DENY",
    )


# --- R5 supplier attributable ---------------------------------------------- #
@pytest.mark.parametrize(
    "mtc",
    [
        ADHESION_FAIL,
        {"coating_weight_g_m2": 274.9},
        {**ADHESION_FAIL, "tensile_mpa": 620.0},
    ],
)
def test_r5_coating_sourced_failure_is_replacement(mtc):
    rec = deterministic_recommendation(_context(mtc=mtc))
    assert (rec["verdict"], rec["disposition"]) == ("APPROVE", "REPLACEMENT")
    assert rec["flags"]["supplier_attributable"] is True
    assert rec["approved_amount"] == SHIPPED * PRICE


def test_r5_coating_weight_at_minimum_conforms_and_is_denied():
    assert _outcome(_context(mtc={"coating_weight_g_m2": 275.0})) == ("DENY", "DENY")


def test_r5_mill_process_failure_is_not_supplier_attributable():
    for mtc in (NONCONFORMING, {"yield_mpa": 351.0}, {"carbon_pct": 0.16}):
        assert (
            supplier_attributable(
                "material_nonconformance", compute_conformance(SPEC, {**IN_SPEC_MTC, **mtc})
            )
            is False
        )


def test_r5_only_applies_to_material_claims():
    conformance = {"conforms": False, "nonconforming_properties": ["coating_adhesion"]}
    assert supplier_attributable("coating_warranty", conformance) is False
    assert supplier_attributable("material_nonconformance", conformance) is True


def test_r5_takes_precedence_over_r6():
    rec = deterministic_recommendation(
        _context(mtc=ADHESION_FAIL, claimed_tonnage=14.0, claimed_freight=1800.0)
    )
    assert rec["disposition"] == "REPLACEMENT" and rec["flags"]["over_claim"] is True
    assert rec["approved_amount"] == SHIPPED * PRICE + CAP


# --- R6 over-claim partial -------------------------------------------------- #
@pytest.mark.parametrize(
    ("tonnage", "freight", "disposition"),
    [
        (SHIPPED, 0.0, "CREDIT"),
        (SHIPPED, CAP, "CREDIT"),  # freight exactly at the cap is not an over-claim
        (SHIPPED + 0.001, 0.0, "REWORK"),
        (SHIPPED, CAP + 0.01, "REWORK"),
        (14.0, 1800.0, "REWORK"),
        (SHIPPED - 1.0, 0.0, "CREDIT"),  # under-claim is paid as claimed
    ],
)
def test_r6_over_claim_boundaries(tonnage, freight, disposition):
    rec = deterministic_recommendation(
        _context(mtc=NONCONFORMING, claimed_tonnage=tonnage, claimed_freight=freight)
    )
    assert (rec["verdict"], rec["disposition"]) == ("APPROVE", disposition)
    expected = round(min(tonnage, SHIPPED) * PRICE + min(freight, CAP), 2)
    assert rec["approved_amount"] == expected


def test_r6_requires_partial_payout():
    assert over_claim_partial({"over_claim_detected": True, "is_partial": False}) is False
    assert over_claim_partial({"over_claim_detected": False, "is_partial": True}) is False
    assert over_claim_partial({"over_claim_detected": True, "is_partial": True}) is True


# --- R7 credit -------------------------------------------------------------- #
def test_r7_prorated_warranty_claim_is_credit_not_rework():
    # 2014 ship + 2026 claim: past full coverage, so prorated (partial) but not over-claimed.
    rec = deterministic_recommendation(
        _context(claim_type="coating_warranty", ship_date="2014-01-01")
    )
    assert rec["deterministic"]["eligible"] is True
    assert 0.0 < rec["approved_amount"] < SHIPPED * PRICE
    assert (rec["verdict"], rec["disposition"]) == ("APPROVE", "CREDIT")
    assert _fired(rec) == ["R7_credit"]


def test_rule_trace_records_every_rule_up_to_the_decision():
    rec = deterministic_recommendation(_context(mtc=NONCONFORMING))
    assert [t["rule"] for t in rec["rule_trace"]] == [
        "R1_duplicate",
        "R2_unknown_claim_type",
        "R3_fraud_hold",
        "R4_ineligible",
        "R5_supplier_attributable",
        "R6_over_claim_partial",
        "R7_credit",
    ]


def test_approve_disposition_function_matches_baseline():
    ctx = _context(mtc=NONCONFORMING, claimed_tonnage=14.0)
    disposition, rule = approve_disposition(
        "material_nonconformance", ctx["conformance"], ctx["settlement"]
    )
    assert (disposition, rule) == ("REWORK", "R6_over_claim_partial")


# --- the money never changes ----------------------------------------------- #
SCENARIOS = {
    "clean_material": _context(mtc=NONCONFORMING),
    "clean_warranty": _context(claim_type="coating_warranty", ship_date="2018-01-01"),
    "in_spec": _context(),
    "out_of_warranty": _context(claim_type="coating_warranty", ship_date="1999-01-01"),
    "duplicate": _context(mtc=NONCONFORMING, duplicate=DUP),
    "over_claim": _context(mtc=NONCONFORMING, claimed_tonnage=14.0, claimed_freight=1800.0),
    "supplier": _context(mtc=ADHESION_FAIL),
    "fraud": _context(risk={"risk_score": 0.6}),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_baseline_amount_is_exactly_the_settlement_authority(name):
    ctx = SCENARIOS[name]
    rec = deterministic_recommendation(ctx)
    authority = ctx["settlement"]["approved_amount"]
    assert rec["settlement_estimate"] == authority
    assert rec["approved_amount"] == (authority if rec["verdict"] == "APPROVE" else 0.0)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_baseline_passes_the_agent_invariants_unchanged(name):
    rec = deterministic_recommendation(SCENARIOS[name])
    llm_shaped = {
        "recommended_verdict": rec["verdict"],
        "recommended_disposition": rec["disposition"],
        "settlement_estimate": rec["settlement_estimate"],
    }
    corrected, violations = enforce_invariants(llm_shaped, rec["deterministic"])
    assert violations == []
    assert corrected["recommended_verdict"] == rec["verdict"]
    assert corrected["recommended_disposition"] == rec["disposition"]
    assert corrected["approved_amount"] == rec["approved_amount"]


# --- enforce_invariants uses the same disposition rules -------------------- #
@pytest.mark.parametrize(("name", "rule"), [("over_claim", "REWORK"), ("supplier", "REPLACEMENT")])
def test_agent_disposition_is_corrected_to_the_rule(name, rule):
    rec = deterministic_recommendation(SCENARIOS[name])
    llm = {
        "recommended_verdict": "APPROVE",
        "recommended_disposition": "CREDIT",
        "settlement_estimate": rec["settlement_estimate"],
    }
    corrected, violations = enforce_invariants(llm, rec["deterministic"])
    assert corrected["recommended_disposition"] == rule
    assert corrected["disposition_corrected_from"] == "CREDIT"
    assert corrected["approved_amount"] == rec["approved_amount"]
    assert violations == []


def test_invalid_agent_approve_disposition_is_a_violation_and_uses_the_rule():
    rec = deterministic_recommendation(SCENARIOS["over_claim"])
    llm = {
        "recommended_verdict": "APPROVE",
        "recommended_disposition": "DUPLICATE",
        "settlement_estimate": rec["settlement_estimate"],
    }
    corrected, violations = enforce_invariants(llm, rec["deterministic"])
    assert corrected["recommended_disposition"] == "REWORK"
    assert violations == ["invalid_approve_disposition"]


def test_default_recommendation_is_the_baseline():
    core = {
        **SCENARIOS["supplier"],
        "claim_type": "material_nonconformance",
        "deterministic": deterministic_recommendation(SCENARIOS["supplier"])["deterministic"],
        "citations": [],
    }
    rec = agent_tools.default_recommendation(core)
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == (
        "APPROVE",
        "REPLACEMENT",
    )
    assert rec["flags"]["supplier_attributable"] is True
    assert "R5_supplier_attributable" in rec["rationale"]


# --- purity: no network / LLM / DB --------------------------------------- #
# Network/LLM/DB client packages. Stdlib ``socket`` is not listed: pydantic's
# stdlib ``email`` import loads it without opening anything; actual socket use is
# covered by ``test_baseline_runs_with_sockets_disabled``.
FORBIDDEN_MODULES = (
    "urllib3",
    "requests",
    "httpx",
    "psycopg",
    "mlflow",
    "databricks",
    "openai",
    "langchain",
    "langchain_core",
    "langgraph",
    "gateway_chat",
    "gateway_embed",
    "db",
)


def test_rules_module_imports_only_future():
    tree = ast.parse((SRC / "disposition_rules.py").read_text())
    imported = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported == {"__future__"}


def test_baseline_loads_no_network_or_llm_module():
    probe = (
        "import sys, disposition_rules, decision_record\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN_MODULES!r})\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=SRC, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""


def test_baseline_runs_with_sockets_disabled(monkeypatch):
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("deterministic_recommendation opened a socket")

    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    for ctx in SCENARIOS.values():
        deterministic_recommendation(ctx)
