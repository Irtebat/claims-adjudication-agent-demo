"""Canonical decision record, recommendation schema, and the money invariants.

This module is the money-critical spine shared by the agent, the transactional
writer, and the append-only decision-record table. It is pure Python (only
``pydantic`` + stdlib) so the invariants are unit-testable without LangGraph,
MLflow, or Lakebase.

The contract enforced here is the CORE PRINCIPLE: deterministic tools decide
money; the LLM only reasons, cites, and recommends. The deterministic
authorities (``authorities.py``) and the duplicate gate produce the eligibility
and the amount; ``enforce_invariants`` guarantees the LLM's structured
recommendation can never override an authority's number or verdict — on any
violation the recommendation is corrected to the deterministic outcome and the
violation is recorded, so the LLM never wins.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from disposition_rules import approve_disposition, fraud_risk, supplier_attributable

# Bump when the persisted payload shape or the recommendation schema changes; the
# value is stamped on every decision record for reproducibility.
SCHEMA_VERSION = "adjudication-decision-record/v2"
PROMPT_VERSION = "claims-adjudication-prompt/v2-narrative-escalation"

# Recommendation verdicts (agent-facing) and the value stored on the
# `public.adjudications.verdict` column (which the silver history contract
# constrains to APPROVE/DENY/PEND). PEND_INVESTIGATE is a safe hold that moves no
# money; it maps to PEND on the operational row.
VERDICTS = ("APPROVE", "DENY", "PEND_INVESTIGATE")
APPROVE_DISPOSITIONS = ("CREDIT", "REPLACEMENT", "REWORK")
_ADJUDICATION_VERDICT = {"APPROVE": "APPROVE", "DENY": "DENY", "PEND_INVESTIGATE": "PEND"}


def adjudication_verdict(recommended_verdict: str) -> str:
    """Map an agent verdict to the operational `adjudications.verdict` value."""
    return _ADJUDICATION_VERDICT[recommended_verdict]


# --------------------------------------------------------------------------- #
# Structured recommendation schema (validated by Pydantic / JSON-Schema).
# --------------------------------------------------------------------------- #
class Flags(BaseModel):
    """Advisory flags. supplier_attributable/over_claim are informed by the
    deterministic outputs; fraud_risk is advisory only and never moves money."""

    supplier_attributable: bool = False
    fraud_risk: bool = False
    over_claim: bool = False


class Precedent(BaseModel):
    claim_id: str
    verdict: str | None = None
    approved_amount: float | None = None
    rrf_score: float | None = None


class NarrativeConflict(BaseModel):
    """A model-identified conflict that can only trigger a human-review hold."""

    clause: str
    narrative_quote: str
    structured_field: str


class Recommendation(BaseModel):
    """The LLM's structured recommendation. ``settlement_estimate`` is copied from
    the settlement authority; ``cited_clause_ids`` are natural clause keys."""

    recommended_verdict: Literal["APPROVE", "DENY", "PEND_INVESTIGATE"]
    recommended_disposition: Literal[
        "CREDIT", "REPLACEMENT", "REWORK", "DENY", "DUPLICATE", "PEND_INVESTIGATE"
    ]
    settlement_estimate: float
    cited_clause_ids: list[str] = Field(default_factory=list)
    precedent: list[Precedent] = Field(default_factory=list)
    rationale: str
    flags: Flags = Field(default_factory=Flags)
    confidence: float = Field(ge=0.0, le=1.0)
    narrative_conflict: NarrativeConflict | None = None


def recommendation_json_schema() -> dict:
    """JSON Schema for the non-streaming ``response_format`` structured-output call."""
    schema = Recommendation.model_json_schema()
    schema["additionalProperties"] = False
    return {
        "type": "json_schema",
        "json_schema": {"name": "adjudication_recommendation", "schema": schema, "strict": False},
    }


def validate_narrative_conflict(
    recommendation: dict, claim: dict, retrieved_clauses: list[dict]
) -> dict:
    """Drop an ungrounded narrative conflict instead of fabricating evidence."""
    validated = dict(recommendation)
    conflict = validated.get("narrative_conflict")
    if not isinstance(conflict, dict):
        validated.pop("narrative_conflict", None)
        return validated
    narrative = claim.get("defect_narrative")
    quote = conflict.get("narrative_quote")
    clause = conflict.get("clause")
    retrieved = {item.get("citation_key") for item in retrieved_clauses}
    if (
        not isinstance(narrative, str)
        or not isinstance(quote, str)
        or not quote
        or quote not in narrative
        or clause not in retrieved
    ):
        validated.pop("narrative_conflict", None)
    return validated


# --------------------------------------------------------------------------- #
# Deterministic outcome — the authorities + duplicate gate decide money/verdict.
# --------------------------------------------------------------------------- #
def deterministic_outcome(
    claim_type: str,
    conformance: dict,
    coverage: dict,
    settlement: dict,
    duplicate: dict,
) -> dict:
    """Decide verdict / disposition / approved amount from the authorities alone.

    A material-nonconformance claim on in-spec material is DENIED; a coating
    warranty claim outside coverage is DENIED; a duplicate is DENIED as DUPLICATE.
    Otherwise the claim is eligible, the approved amount is exactly the settlement
    authority's output, and the APPROVE disposition comes from the shared rules in
    ``disposition_rules`` (REPLACEMENT / REWORK / CREDIT). No LLM input participates.
    """
    settlement_amount = float(settlement["approved_amount"])
    if duplicate.get("is_duplicate"):
        return {
            "verdict": "DENY",
            "disposition": "DUPLICATE",
            "approved_amount": 0.0,
            "settlement_authority_amount": settlement_amount,
            "eligible": False,
            "reason": "duplicate_claim",
            "duplicate_of_claim_id": duplicate.get("duplicate_of_claim_id"),
        }
    if claim_type == "material_nonconformance":
        eligible = not conformance["conforms"]
        reason = "nonconforming" if eligible else "in_spec_per_mtc"
    elif claim_type == "coating_warranty":
        eligible = bool(coverage["covered"])
        reason = "covered" if eligible else "not_covered"
    else:
        return {
            "verdict": "PEND_INVESTIGATE",
            "disposition": "PEND_INVESTIGATE",
            "approved_amount": 0.0,
            "settlement_authority_amount": settlement_amount,
            "eligible": None,
            "reason": "unknown_claim_type",
            "duplicate_of_claim_id": None,
        }
    if not eligible:
        return {
            "verdict": "DENY",
            "disposition": "DENY",
            "approved_amount": 0.0,
            "settlement_authority_amount": settlement_amount,
            "eligible": False,
            "reason": reason,
            "duplicate_of_claim_id": None,
        }
    disposition, disposition_rule = approve_disposition(claim_type, conformance, settlement)
    return {
        "verdict": "APPROVE",
        "disposition": disposition,
        "approved_amount": settlement_amount,
        "settlement_authority_amount": settlement_amount,
        "eligible": True,
        "reason": reason,
        "duplicate_of_claim_id": None,
        "disposition_rule": disposition_rule,
    }


def deterministic_recommendation(context: dict) -> dict:
    """The full no-LLM recommendation from the authority outputs alone (pure, no I/O).

    ``context`` carries the already-computed ``conformance``, ``coverage``,
    ``settlement`` and ``duplicate`` results, the claim type (``claim_type`` or
    ``claim["claim_type"]``), and optionally the advisory ``risk`` row. Applies rules
    R1-R7 (see ``disposition_rules``) and returns the agent's recommendation shape
    (it validates as a ``Recommendation``) plus ``approved_amount`` (the settlement
    authority amount on APPROVE, else 0) and a ``rule_trace`` of the deciding rules
    followed by the advisory R3 fraud-review check. Verdict, disposition, and amount
    are exactly the deterministic outcome's; R3 only sets a flag.
    """
    claim_type = context.get("claim_type") or (context.get("claim") or {}).get("claim_type")
    conformance = context["conformance"]
    settlement = context["settlement"]
    det = deterministic_outcome(
        claim_type, conformance, context["coverage"], settlement, context["duplicate"]
    )
    if det["reason"] == "duplicate_claim":
        deciding = "R1_duplicate"
    elif det["eligible"] is None:
        deciding = "R2_unknown_claim_type"
    elif det["eligible"] is False:
        deciding = "R4_ineligible"
    else:
        deciding = det["disposition_rule"]
    chain = (
        "R1_duplicate",
        "R2_unknown_claim_type",
        "R4_ineligible",
        "R5_supplier_attributable",
        "R6_over_claim_partial",
        "R7_credit",
    )
    trace = [
        {"rule": rule, "fired": rule == deciding} for rule in chain[: chain.index(deciding) + 1]
    ]
    review = fraud_risk(context.get("risk"))
    trace.append({"rule": "R3_fraud_review", "fired": review, "advisory": True})
    authority_amount = float(det["settlement_authority_amount"])
    return {
        "recommended_verdict": det["verdict"],
        "recommended_disposition": det["disposition"],
        "approved_amount": authority_amount if det["verdict"] == "APPROVE" else 0.0,
        "settlement_estimate": authority_amount,
        "cited_clause_ids": [],
        "precedent": [],
        "rationale": (
            f"Deterministic rules: {det['verdict']}/{det['disposition']} by {deciding}"
            + ("; fraud review suggested (R3, advisory)." if review else ".")
        ),
        "flags": {
            "supplier_attributable": supplier_attributable(claim_type, conformance),
            "fraud_risk": review,
            "over_claim": bool(settlement.get("over_claim_detected")),
            "fraud_review_suggested": review,
        },
        "confidence": 0.6,
        "narrative_conflict": None,
        "rule_trace": trace,
    }


def _amounts_equal(a: Any, b: Any) -> bool:
    """Compare authority money exactly using decimal text representations."""
    from decimal import Decimal, InvalidOperation

    if a is None:
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, TypeError, ValueError):
        return False


def enforce_invariants(recommendation: dict, deterministic: dict) -> tuple[dict, list[str]]:
    """Correct the LLM recommendation to the deterministic outcome; the LLM never wins.

    Enforces: (i) a duplicate cannot be recommended for payment (duplicate =>
    DENY/DUPLICATE), (ii) ``settlement_estimate`` equals the settlement authority
    output exactly and the persisted ``approved_amount`` is the authority amount
    when APPROVE else 0, (iii) the verdict is consistent with conformance/coverage
    eligibility — an ineligible claim can never be recommended for APPROVE, and an
    eligible claim can never be silently denied (only held as PEND_INVESTIGATE).
    Only money and eligibility are enforced: a valid APPROVE disposition
    (CREDIT/REPLACEMENT/REWORK) is the agent's call and is kept; an invalid one is
    a violation and falls back to the rule disposition. The rule disposition
    (``disposition_rules`` via the deterministic outcome) is recorded alongside as
    ``rule_disposition`` / ``disposition_agrees_with_rule`` for agent-vs-rules
    analysis. Returns (corrected_recommendation, violations).
    """
    corrected = dict(recommendation)
    violations: list[str] = []
    authority_amount = float(deterministic["settlement_authority_amount"])

    # (i) duplicate is DENY/DUPLICATE, no exceptions (not even a PEND hold).
    if deterministic["reason"] == "duplicate_claim":
        if (
            corrected.get("recommended_verdict") != "DENY"
            or corrected.get("recommended_disposition") != "DUPLICATE"
        ):
            violations.append("duplicate_must_be_deny_duplicate")
        corrected["recommended_verdict"] = "DENY"
        corrected["recommended_disposition"] = "DUPLICATE"
    else:
        det_verdict = deterministic["verdict"]
        if (
            deterministic.get("eligible") is not True
            and corrected.get("recommended_verdict") == "APPROVE"
        ):
            # An ineligible or unresolved claim cannot be paid.
            violations.append("cannot_approve_ineligible_claim")
            corrected["recommended_verdict"] = det_verdict
            corrected["recommended_disposition"] = deterministic["disposition"]
        elif det_verdict == "APPROVE" and corrected.get("recommended_verdict") == "DENY":
            # An eligible claim cannot be silently denied; hold it instead.
            violations.append("cannot_deny_eligible_claim")
            corrected["recommended_verdict"] = "PEND_INVESTIGATE"
            corrected["recommended_disposition"] = "PEND_INVESTIGATE"

    # Normalize disposition to the verdict class.
    verdict = corrected["recommended_verdict"]
    if verdict == "PEND_INVESTIGATE":
        corrected["recommended_disposition"] = "PEND_INVESTIGATE"
    elif verdict == "DENY" and corrected.get("recommended_disposition") not in (
        "DENY",
        "DUPLICATE",
    ):
        corrected["recommended_disposition"] = "DENY"
    elif verdict == "APPROVE":
        if corrected.get("recommended_disposition") not in APPROVE_DISPOSITIONS:
            violations.append("invalid_approve_disposition")
            fallback = deterministic.get("disposition")
            corrected["recommended_disposition"] = (
                fallback if fallback in APPROVE_DISPOSITIONS else "CREDIT"
            )
    corrected["rule_disposition"] = deterministic["disposition"]
    corrected["disposition_agrees_with_rule"] = (
        corrected["recommended_disposition"] == deterministic["disposition"]
    )

    # (ii) settlement_estimate is the authority number, copied — never invented.
    if not _amounts_equal(corrected.get("settlement_estimate"), authority_amount):
        violations.append("settlement_estimate_overridden")
    corrected["settlement_estimate"] = authority_amount

    # The persisted approved amount is deterministic: the authority amount only when
    # the corrected verdict is APPROVE, otherwise zero.
    corrected["approved_amount"] = authority_amount if verdict == "APPROVE" else 0.0
    return corrected, violations


# --------------------------------------------------------------------------- #
# Canonical decision-record payload.
# --------------------------------------------------------------------------- #
# Column order for the append-only `public.adjudication_decision_records` table.
# JSONB columns carry nested structs/arrays; the rest are scalar.
DECISION_RECORD_COLUMNS = [
    "adjudication_id",
    "claim_id",
    "record_version",
    "idempotency_key",
    "claim_type",
    "claim_input",
    "spec_provenance",
    "warranty_provenance",
    "spec_params",
    "warranty_terms",
    "freight_cap",
    "coil",
    "mtc_measured",
    "conformance",
    "coverage",
    "settlement",
    "duplicate",
    "claimed_amount",
    "approved_amount",
    "over_claim_flag",
    "duplicate_flag",
    "deterministic_verdict",
    "deterministic_disposition",
    "recommended_verdict",
    "recommended_disposition",
    "rationale",
    "confidence",
    "flags",
    "advisory_risk",
    "precedent",
    "invariant_violations",
    "citations",
    "cited_clause_ids",
    "authorities_git_sha",
    "authorities_source_sha256",
    "agent_model_name",
    "agent_model_version",
    "reasoning_endpoint",
    "prompt_version",
    "schema_version",
    "mlflow_trace_id",
]
# Columns whose values are nested (dict/list) and must be bound as JSONB.
JSONB_COLUMNS = frozenset(
    {
        "claim_input",
        "spec_provenance",
        "warranty_provenance",
        "spec_params",
        "warranty_terms",
        "coil",
        "mtc_measured",
        "conformance",
        "coverage",
        "settlement",
        "duplicate",
        "flags",
        "advisory_risk",
        "precedent",
        "invariant_violations",
        "citations",
    }
)


def source_sha256(path: str) -> str:
    """SHA-256 of a source file's bytes (e.g. authorities.py) for reproducibility."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def clause_text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_idempotency_key(
    claim_id: str, authorities_source_sha256: str, record_version: int
) -> str:
    """A stable key for a (claim, authorities version, record version); retries dedup on it."""
    material = f"{claim_id}|{authorities_source_sha256}|{SCHEMA_VERSION}|{record_version}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_decision_record(
    *,
    claim: dict,
    resolved: dict,
    measured: dict,
    conformance: dict,
    coverage: dict,
    settlement: dict,
    duplicate: dict,
    deterministic: dict,
    recommendation: dict,
    invariant_violations: list[str],
    citations: list[dict],
    advisory_risk: dict | None,
    reproducibility: dict,
    record_version: int = 1,
    idempotency_key: str | None = None,
    adjudication_id: str | None = None,
) -> dict:
    """Assemble the full canonical payload for one adjudication decision record.

    Returns a dict keyed by ``DECISION_RECORD_COLUMNS``; nested values in
    ``JSONB_COLUMNS`` stay as dict/list for the writer to bind as JSONB.
    """
    authorities_src = reproducibility["authorities_source_sha256"]
    key = idempotency_key or build_idempotency_key(
        claim["claim_id"], authorities_src, record_version
    )
    adj_id = adjudication_id or f"ADJ-{key[:20]}"
    precedent = recommendation.get("precedent", [])
    narrative_conflict = recommendation.get("narrative_conflict")
    flags = dict(recommendation.get("flags", {}))
    if narrative_conflict:
        flags["narrative_conflict"] = narrative_conflict
    return {
        "adjudication_id": adj_id,
        "claim_id": claim["claim_id"],
        "record_version": record_version,
        "idempotency_key": key,
        "claim_type": claim.get("claim_type"),
        "claim_input": claim,
        "spec_provenance": resolved.get("spec_provenance"),
        "warranty_provenance": resolved.get("warranty_provenance"),
        "spec_params": resolved.get("spec_params"),
        "warranty_terms": resolved.get("warranty_terms"),
        "freight_cap": _to_float(resolved.get("freight_cap")),
        "coil": resolved.get("coil"),
        "mtc_measured": measured,
        "conformance": conformance,
        "coverage": coverage,
        "settlement": settlement,
        "duplicate": duplicate,
        "claimed_amount": _to_float(settlement.get("claimed_amount")),
        "approved_amount": float(recommendation["approved_amount"]),
        "over_claim_flag": bool(settlement.get("over_claim_detected")),
        "duplicate_flag": bool(duplicate.get("is_duplicate")),
        "deterministic_verdict": deterministic["verdict"],
        "deterministic_disposition": deterministic["disposition"],
        # Not persisted columns (deterministic_disposition already is the rule
        # disposition); surfaced for agent-vs-rules analysis in custom_outputs.
        "rule_disposition": deterministic["disposition"],
        "disposition_agrees_with_rule": (
            recommendation["recommended_disposition"] == deterministic["disposition"]
        ),
        "recommended_verdict": recommendation["recommended_verdict"],
        "recommended_disposition": recommendation["recommended_disposition"],
        "rationale": recommendation.get("rationale"),
        "confidence": _to_float(recommendation.get("confidence")),
        "flags": flags,
        "narrative_conflict": narrative_conflict,
        "advisory_risk": advisory_risk,
        "precedent": precedent,
        "invariant_violations": invariant_violations,
        "citations": citations,
        "cited_clause_ids": [c["citation_key"] for c in citations],
        "authorities_git_sha": reproducibility.get("authorities_git_sha"),
        "authorities_source_sha256": authorities_src,
        "agent_model_name": reproducibility.get("agent_model_name"),
        "agent_model_version": reproducibility.get("agent_model_version"),
        "reasoning_endpoint": reproducibility.get("reasoning_endpoint"),
        "prompt_version": reproducibility.get("prompt_version", PROMPT_VERSION),
        "schema_version": reproducibility.get("schema_version", SCHEMA_VERSION),
        "mlflow_trace_id": reproducibility.get("mlflow_trace_id"),
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
