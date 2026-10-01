"""Deterministic disposition + routing rules — derived from policy semantics, not labels.

The authorities (``authorities.py``) decide eligibility and money. These rules decide
*which remedy* an eligible claim gets. They read only authority outputs
(conformance, coverage, settlement), the duplicate gate, and the advisory heat risk
— never a ground-truth label — and they never change an amount or a verdict: the
verdict is the authorities' eligibility, an APPROVE pays exactly the settlement
authority's number, and every other verdict pays zero.

Pure stdlib, no I/O. Used by the no-LLM baseline
(``decision_record.deterministic_recommendation``) and by
``decision_record.deterministic_outcome``, whose disposition the agent's
``enforce_invariants`` records as ``rule_disposition`` next to the agent's own
(a valid agent disposition is kept, so agent-vs-rules stays measurable).

Rules, in precedence order (first match wins):

R1 duplicate          The duplicate gate matched a prior claim -> DENY / DUPLICATE.
                      A duplicate is never payable and never held.
R2 unknown_claim_type The claim type has no authority -> PEND_INVESTIGATE.
R3 fraud_review       ADVISORY ONLY — decides nothing. When the customer/heat risk
                      score is >= ``FRAUD_REVIEW_RISK_THRESHOLD`` the recommendation
                      carries ``fraud_review_suggested`` for the adjuster. Heat risk
                      is advisory and no policy defines a hold threshold, so it never
                      changes a verdict, disposition, or amount.
R4 ineligible         Not eligible -> DENY / DENY.
R5 supplier_attributable
                      Eligible material-nonconformance claim whose MTC fails a
                      coating-sourced property (coating adhesion or minimum coating
                      weight) -> APPROVE / REPLACEMENT. Coating failures trace to the
                      coating supplier's lot, so the coil is replaced and the cost is
                      recoverable from that supplier. Takes precedence over R6: the
                      root cause sets the remedy; an over-claim is still flagged and
                      the amount is still capped by the settlement authority.
R6 over_claim_partial Eligible claim the settlement authority caught over-claiming
                      (claimed tonnage > shipped, or claimed freight > freight cap)
                      AND that is therefore only partially payable (approved <
                      claimed) -> APPROVE / REWORK: the claim is restated to the
                      payable tonnage/freight.
R7 credit             Any other eligible claim (mill-process nonconformance in
                      mechanicals/chemistry/dimensions, or a covered coating-warranty
                      claim) -> APPROVE / CREDIT.
"""

from __future__ import annotations

# Nonconforming MTC properties that originate in the coating supplier's input
# rather than the mill's own process (compute_conformance's property names).
SUPPLIER_ATTRIBUTABLE_PROPERTIES = frozenset({"coating_adhesion", "coating_weight_g_m2"})

# The advisory customer/heat risk score at which a fraud review is suggested
# (inclusive). Same threshold as the recommendation's advisory ``fraud_risk`` flag.
FRAUD_REVIEW_RISK_THRESHOLD = 0.5


def supplier_attributable(claim_type: str | None, conformance: dict) -> bool:
    """R5 predicate: a material claim failing a coating-supplier-sourced property."""
    if claim_type != "material_nonconformance" or conformance.get("conforms", True):
        return False
    failed = set(conformance.get("nonconforming_properties") or [])
    return bool(failed & SUPPLIER_ATTRIBUTABLE_PROPERTIES)


def over_claim_partial(settlement: dict) -> bool:
    """R6 predicate: the settlement authority capped an over-claim to a partial payout."""
    return bool(settlement.get("over_claim_detected")) and bool(settlement.get("is_partial"))


def fraud_risk(risk: dict | None) -> bool:
    """R3 advisory signal: risk score at or above the review threshold."""
    return float((risk or {}).get("risk_score") or 0.0) >= FRAUD_REVIEW_RISK_THRESHOLD


def approve_disposition(
    claim_type: str | None, conformance: dict, settlement: dict
) -> tuple[str, str]:
    """(disposition, rule id) for an ELIGIBLE claim: R5 REPLACEMENT, R6 REWORK, R7 CREDIT."""
    if supplier_attributable(claim_type, conformance):
        return "REPLACEMENT", "R5_supplier_attributable"
    if over_claim_partial(settlement):
        return "REWORK", "R6_over_claim_partial"
    return "CREDIT", "R7_credit"
