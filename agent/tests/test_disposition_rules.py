"""The deterministic disposition/routing rules R1-R7 and the no-LLM baseline.

Each rule is exercised at its boundary through the real authorities, the baseline
is proven to pay exactly the settlement authority amount (or zero) and to validate
as the agent's ``Recommendation``, the agent's ``enforce_invariants`` is proven to
keep a valid agent disposition while recording the rule disposition, the advisory
R3 fraud review is proven never to change a verdict, and the rules module is
proven to load no network/LLM/DB code.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

import agent_tools
from authorities import compute_conformance, compute_coverage, compute_settlement
from decision_record import (
    Recommendation,
    deterministic_outcome,
    deterministic_recommendation,
    enforce_invariants,
)
from disposition_rules import (
    FRAUD_REVIEW_RISK_THRESHOLD,
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
    return rec["recommended_verdict"], rec["recommended_disposition"]


def _det(ctx):
    return deterministic_outcome(
        ctx["claim"]["claim_type"],
        ctx["conformance"],
        ctx["coverage"],
        ctx["settlement"],
        ctx["duplicate"],
    )


def _fired(rec):
    """Deciding rules that fired (the advisory R3 entry is reported separately)."""
    return [t["rule"] for t in rec["rule_trace"] if t["fired"] and not t.get("advisory")]


def _r3(rec):
    (entry,) = [t for t in rec["rule_trace"] if t["rule"] == "R3_fraud_review"]
    return entry


NONCONFORMING = {"tensile_mpa": 620.0}
ADHESION_FAIL = {"coating_adhesion_pass": False}


# --- R1 duplicate ----------------------------------------------------------- #
def test_r1_duplicate_is_deny_duplicate_even_with_high_risk():
    rec = deterministic_recommendation(
        _context(mtc=NONCONFORMING, duplicate=DUP, risk={"risk_score": 1.0})
    )
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == ("DENY", "DUPLICATE")
    assert rec["approved_amount"] == 0.0
    assert _fired(rec) == ["R1_duplicate"]


# --- R2 unknown claim type -------------------------------------------------- #
def test_r2_unknown_claim_type_is_held():
    ctx = _context(mtc=NONCONFORMING)
    ctx["claim"]["claim_type"] = "something_else"
    rec = deterministic_recommendation(ctx)
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == (
        "PEND_INVESTIGATE",
        "PEND_INVESTIGATE",
    )
    assert rec["approved_amount"] == 0.0


# --- R3 fraud review (advisory) / R4 ineligible ---------------------------- #
@pytest.mark.parametrize(
    ("risk", "suggested"),
    [
        (None, False),
        ({"risk_score": 0.0}, False),
        ({"risk_score": FRAUD_REVIEW_RISK_THRESHOLD - 0.0001}, False),
        ({"risk_score": FRAUD_REVIEW_RISK_THRESHOLD}, True),
        ({"risk_score": 0.6}, True),
        ({"risk_score": 1.0}, True),
    ],
)
def test_r3_is_advisory_and_r4_denies_in_spec_claim_at_any_risk(risk, suggested):
    rec = deterministic_recommendation(_context(risk=risk))
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == ("DENY", "DENY")
    assert rec["approved_amount"] == 0.0
    assert _fired(rec) == ["R4_ineligible"]
    assert rec["flags"]["fraud_review_suggested"] is suggested
    assert _r3(rec) == {"rule": "R3_fraud_review", "fired": suggested, "advisory": True}


def test_r3_does_not_change_an_uncovered_warranty_denial():
    ctx = _context(claim_type="coating_warranty", environment="marine", risk={"risk_score": 0.75})
    assert _outcome(ctx) == ("DENY", "DENY")


def test_r3_does_not_hold_an_eligible_claim():
    rec = deterministic_recommendation(_context(mtc=NONCONFORMING, risk={"risk_score": 1.0}))
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == ("APPROVE", "CREDIT")
    assert rec["flags"]["fraud_review_suggested"] is True
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
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == (
        "APPROVE",
        "REPLACEMENT",
    )
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
    assert rec["recommended_disposition"] == "REPLACEMENT" and rec["flags"]["over_claim"] is True
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
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == ("APPROVE", disposition)
    expected = round(min(tonnage, SHIPPED) * PRICE + min(freight, CAP), 2)
    assert rec["approved_amount"] == expected


def test_r6_requires_partial_payout():
    assert over_claim_partial({"over_claim_detected": True, "is_partial": False}) is False
    assert over_claim_partial({"over_claim_detected": False, "is_partial": True}) is False
    assert over_claim_partial({"over_claim_detected": True, "is_partial": True}) is True


# --- R7 credit -------------------------------------------------------------- #
def test_r7_prorated_warranty_claim_is_credit_not_rework():
    # 2014 ship + 2026 claim: past full coverage, so prorated (partial) but not over-claimed.
    ctx = _context(claim_type="coating_warranty", ship_date="2014-01-01")
    rec = deterministic_recommendation(ctx)
    assert _det(ctx)["eligible"] is True
    assert 0.0 < rec["approved_amount"] < SHIPPED * PRICE
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == ("APPROVE", "CREDIT")
    assert _fired(rec) == ["R7_credit"]


def test_rule_trace_records_every_rule_up_to_the_decision():
    rec = deterministic_recommendation(_context(mtc=NONCONFORMING))
    assert [t["rule"] for t in rec["rule_trace"]] == [
        "R1_duplicate",
        "R2_unknown_claim_type",
        "R4_ineligible",
        "R5_supplier_attributable",
        "R6_over_claim_partial",
        "R7_credit",
        "R3_fraud_review",
    ]
    assert "R7_credit" in rec["rationale"]


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
    "out_of_warranty": _context(claim_type="coating_warranty", ship_date="1990-01-01"),
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
    assert rec["approved_amount"] == (authority if rec["recommended_verdict"] == "APPROVE" else 0.0)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_baseline_has_the_agent_recommendation_shape(name):
    rec = deterministic_recommendation(SCENARIOS[name])
    # The same validator the agent applies to the LLM's structured output.
    validated = Recommendation.model_validate_json(json.dumps(rec))
    assert set(Recommendation.model_fields) | {"approved_amount", "rule_trace"} == set(rec)
    dumped = validated.model_dump()
    for field in Recommendation.model_fields:
        if field != "flags":
            assert dumped[field] == rec[field], field
    assert {k: rec["flags"][k] for k in dumped["flags"]} == dumped["flags"]
    assert rec["cited_clause_ids"] == [] and rec["precedent"] == []


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_baseline_passes_the_agent_invariants_unchanged(name):
    rec = deterministic_recommendation(SCENARIOS[name])
    corrected, violations = enforce_invariants(rec, _det(SCENARIOS[name]))
    assert violations == []
    assert corrected["recommended_verdict"] == rec["recommended_verdict"]
    assert corrected["recommended_disposition"] == rec["recommended_disposition"]
    assert corrected["approved_amount"] == rec["approved_amount"]
    assert corrected["disposition_agrees_with_rule"] is True


# --- enforce_invariants: money/eligibility only; agent disposition kept ---- #
@pytest.mark.parametrize(("name", "rule"), [("over_claim", "REWORK"), ("supplier", "REPLACEMENT")])
def test_valid_agent_disposition_is_kept_and_rule_recorded(name, rule):
    rec = deterministic_recommendation(SCENARIOS[name])
    llm = {
        "recommended_verdict": "APPROVE",
        "recommended_disposition": "CREDIT",
        "settlement_estimate": rec["settlement_estimate"],
    }
    corrected, violations = enforce_invariants(llm, _det(SCENARIOS[name]))
    assert corrected["recommended_disposition"] == "CREDIT"
    assert corrected["rule_disposition"] == rule
    assert corrected["disposition_agrees_with_rule"] is False
    assert corrected["approved_amount"] == rec["approved_amount"]
    assert violations == []


def test_invalid_agent_approve_disposition_is_a_violation_and_uses_the_rule():
    rec = deterministic_recommendation(SCENARIOS["over_claim"])
    llm = {
        "recommended_verdict": "APPROVE",
        "recommended_disposition": "DUPLICATE",
        "settlement_estimate": rec["settlement_estimate"],
    }
    corrected, violations = enforce_invariants(llm, _det(SCENARIOS["over_claim"]))
    assert corrected["recommended_disposition"] == "REWORK"
    assert corrected["disposition_agrees_with_rule"] is True
    assert violations == ["invalid_approve_disposition"]


def test_decision_record_carries_rule_disposition_for_analysis():
    from decision_record import build_decision_record

    ctx = SCENARIOS["over_claim"]
    det = _det(ctx)
    llm = {
        "recommended_verdict": "APPROVE",
        "recommended_disposition": "CREDIT",
        "settlement_estimate": ctx["settlement"]["approved_amount"],
        "rationale": "agent",
        "confidence": 0.9,
    }
    corrected, violations = enforce_invariants(llm, det)
    record = build_decision_record(
        claim={"claim_id": "CLM-1", "claim_type": "material_nonconformance"},
        resolved={},
        measured={},
        conformance=ctx["conformance"],
        coverage=ctx["coverage"],
        settlement=ctx["settlement"],
        duplicate=ctx["duplicate"],
        deterministic=det,
        recommendation=corrected,
        invariant_violations=violations,
        citations=[],
        advisory_risk=None,
        reproducibility={"authorities_source_sha256": "x"},
    )
    assert record["recommended_disposition"] == "CREDIT"
    assert record["deterministic_disposition"] == record["rule_disposition"] == "REWORK"
    assert record["disposition_agrees_with_rule"] is False


def _core(ctx, **extra):
    return {
        **ctx,
        "claim_type": ctx["claim"]["claim_type"],
        "deterministic": _det(ctx),
        "citations": [{"citation_key": "G/NA/E/mechanical"}],
        **extra,
    }


def test_default_recommendation_is_the_baseline():
    rec = agent_tools.default_recommendation(_core(SCENARIOS["supplier"]))
    Recommendation.model_validate(rec)
    assert (rec["recommended_verdict"], rec["recommended_disposition"]) == (
        "APPROVE",
        "REPLACEMENT",
    )
    assert rec["flags"]["supplier_attributable"] is True
    assert rec["cited_clause_ids"] == ["G/NA/E/mechanical"]
    assert "R5_supplier_attributable" in rec["rationale"]


@pytest.mark.parametrize("risk_score", [0.0, FRAUD_REVIEW_RISK_THRESHOLD, 0.6, 0.75, 1.0])
@pytest.mark.parametrize("name", ["in_spec", "out_of_warranty"])
def test_fallback_verdict_parity_with_main_for_ineligible_high_risk(name, risk_score):
    # Before this change default_recommendation returned the deterministic outcome's
    # verdict/disposition/amount unconditionally (main @ 1c6de5c); risk only set the
    # advisory fraud_risk flag at >= 0.5. The fallback must still do exactly that.
    ctx = {**SCENARIOS[name], "risk": {"risk_score": risk_score}}
    det = _det(ctx)
    rec = agent_tools.default_recommendation(_core(ctx))
    assert det["verdict"] == "DENY"
    assert rec["recommended_verdict"] == det["verdict"]
    assert rec["recommended_disposition"] == det["disposition"] == "DENY"
    assert rec["settlement_estimate"] == float(det["settlement_authority_amount"])
    assert rec["approved_amount"] == 0.0
    assert rec["flags"]["fraud_risk"] is (risk_score >= 0.5)
    corrected, violations = enforce_invariants(rec, det)
    assert (corrected["recommended_verdict"], violations) == ("DENY", [])


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
