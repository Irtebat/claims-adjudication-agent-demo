"""Deterministic tool logic and the resolve-once decision core (no LLM, no MLflow).

This module holds the in-process tool callables and the deterministic core that
runs BEFORE the LLM sees anything: resolve the coil to a FROZEN policy snapshot
once, then run the authorities + duplicate gate (which decide money and verdict
eligibility) and gather the advisory context (clauses, precedent, risk). It
depends only on the existing runtime modules, so the tool wrappers and the core
are unit-testable with a fake psycopg connection — the LangGraph/MLflow wiring
lives in ``agent.py``.
"""

from __future__ import annotations

from typing import Any, Callable

from authorities_runtime import AuthorityRuntime, FrozenAdjudicationContext
from decision_record import clause_text_sha256, deterministic_outcome
from duplicate import check_duplicate_claim
from heat_risk import get_customer_heat_risk
from retrieval import find_similar_prior_claims, retrieve_policy_clauses

EmbedFn = Callable[[list[str]], list[list[float]]]


def resolve_heat_no(conn: Any, coil_id: str) -> str | None:
    """The heat number for a coil, from the Lakebase reference master (advisory-risk key)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT heat_no FROM reference.heats_coils WHERE coil_id = %(coil_id)s",
            {"coil_id": coil_id},
        )
        row = cur.fetchone()
    return row[0] if row else None


def settlement_inputs(claim: dict, coverage: dict) -> dict:
    """Assemble the settlement authority inputs; proration applies only to warranty claims."""
    proration = (
        coverage.get("proration_factor", 1.0)
        if claim.get("claim_type") == "coating_warranty"
        else 1.0
    )
    return {
        "coil_id": claim["coil_id"],
        "claim_type": claim.get("claim_type"),
        "claimed_tonnage": claim.get("claimed_tonnage") or 0,
        "claimed_freight": claim.get("claimed_freight") or 0,
        "proration_factor": proration,
    }


def _spec_filters(resolved: dict) -> dict:
    prov = resolved.get("spec_provenance") or {}
    return {k: prov.get(k) for k in ("grade", "spec_edition", "region")}


def _warranty_filters(resolved: dict) -> dict:
    coil = resolved.get("coil") or {}
    prov = resolved.get("warranty_provenance") or {}
    return {
        "product_line": prov.get("product_line") or coil.get("product_line"),
        "coating_class": coil.get("coating_class"),
        "region": prov.get("region") or coil.get("region"),
        "ship_date": coil.get("ship_date"),
    }


def build_citations(clauses: list[dict]) -> list[dict]:
    """Natural clause keys + section_ref + clause-text hash — the citation provenance."""
    citations = []
    for clause in clauses:
        citations.append(
            {
                "citation_key": clause["citation_key"],
                "section_ref": clause.get("section_ref"),
                "clause_text_sha256": clause_text_sha256(clause.get("clause_text", "")),
            }
        )
    return citations


def retrieve_clauses(
    conn: Any, frozen: FrozenAdjudicationContext, claim: dict, embed_fn: EmbedFn | None = None
) -> list[dict]:
    """BM25 clause citation over the corpus that matches the claim type, metadata-filtered."""
    corpus = "warranty" if claim.get("claim_type") == "coating_warranty" else "spec"
    query = claim.get("defect_narrative") or claim.get("defect_code") or ""
    filters = (
        _warranty_filters(frozen.resolved)
        if corpus == "warranty"
        else _spec_filters(frozen.resolved)
    )
    return retrieve_policy_clauses(conn, embed_fn, query, corpus, filters)


def find_precedent(
    conn: Any, frozen: FrozenAdjudicationContext, claim: dict, embed_fn: EmbedFn
) -> list[dict]:
    """Advisory similar-prior-claims (hybrid). Best-effort — never decides money."""
    coil = frozen.resolved.get("coil") or {}
    text = claim.get("defect_narrative") or claim.get("defect_code") or ""
    filters = {"grade": coil.get("grade"), "coating_class": coil.get("coating_class")}
    return find_similar_prior_claims(conn, embed_fn, text, claim["coil_id"], filters)


def run_deterministic_core(conn: Any, claim: dict, embed_fn: EmbedFn | None = None) -> dict:
    """Resolve once, then run the authorities + duplicate gate and gather advisory context.

    The authorities and the duplicate gate here are the ONLY things that decide
    money and verdict eligibility. Retrieval, precedent, and risk are advisory. The
    returned ``deterministic`` outcome is what the invariant checks later enforce
    the LLM's recommendation against.
    """
    runtime = AuthorityRuntime(conn)
    frozen = runtime.freeze(claim["coil_id"])
    heat_no = resolve_heat_no(conn, claim["coil_id"])

    conformance = frozen.conformance()
    coverage = frozen.coverage(claim)
    settlement = frozen.settlement(settlement_inputs(claim, coverage))
    duplicate = check_duplicate_claim(conn, claim)
    deterministic = deterministic_outcome(
        claim.get("claim_type"), conformance, coverage, settlement, duplicate
    )

    clauses = retrieve_clauses(conn, frozen, claim, embed_fn)
    citations = build_citations(clauses)
    precedent: list[dict] = []
    if embed_fn is not None:
        try:
            precedent = find_precedent(conn, frozen, claim, embed_fn)
        except Exception:  # advisory only — a retrieval failure never blocks the decision
            precedent = []
    try:
        risk = get_customer_heat_risk(conn, claim.get("customer_id"), heat_no)
    except Exception:
        risk = {"risk_score": 0.0, "found": False, "cluster_id": None}

    return {
        "frozen": frozen,
        "resolved": frozen.resolved,
        "measured": frozen.measured,
        "heat_no": heat_no,
        "conformance": conformance,
        "coverage": coverage,
        "settlement": settlement,
        "duplicate": duplicate,
        "deterministic": deterministic,
        "clauses": clauses,
        "citations": citations,
        "precedent": precedent,
        "risk": risk,
    }


def default_recommendation(core: dict) -> dict:
    """A deterministic recommendation aligned to the authorities.

    Used as the baseline the LLM refines and as the fallback when the LLM is
    unreachable. Already satisfies the invariants (verdict/disposition/amount come
    straight from the deterministic outcome), so it is always a safe recommendation.
    """
    det = core["deterministic"]
    settlement = core["settlement"]
    risk = core.get("risk") or {}
    precedent = [
        {
            "claim_id": p.get("claim_id"),
            "verdict": p.get("verdict"),
            "approved_amount": _to_float(p.get("approved_amount")),
            "rrf_score": _to_float(p.get("rrf_score")),
        }
        for p in core.get("precedent", [])
    ]
    return {
        "recommended_verdict": det["verdict"],
        "recommended_disposition": det["disposition"],
        "settlement_estimate": float(det["settlement_authority_amount"]),
        "cited_clause_ids": [c["citation_key"] for c in core.get("citations", [])],
        "precedent": precedent,
        "rationale": _deterministic_rationale(det, core),
        "flags": {
            "supplier_attributable": False,
            "fraud_risk": float(risk.get("risk_score") or 0.0) >= 0.5,
            "over_claim": bool(settlement.get("over_claim_detected")),
        },
        "confidence": 0.6,
    }


def _deterministic_rationale(det: dict, core: dict) -> str:
    reason = det["reason"]
    verdict = det["verdict"]
    if reason == "duplicate_claim":
        return (
            f"Duplicate of claim {det.get('duplicate_of_claim_id')}: same coil, defect, tonnage "
            "and near-identical narrative within the date window. Denied as DUPLICATE."
        )
    if reason == "in_spec_per_mtc":
        props = core["conformance"].get("nonconforming_properties") or []
        return (
            "Material conforms to the ordered grade spec per its MTC "
            f"(no nonconforming properties: {props}); the nonconformance claim is denied."
        )
    if reason == "not_covered":
        return (
            "Coating-warranty claim falls outside coverage "
            f"({core['coverage'].get('exclusions_hit')}); denied."
        )
    if verdict == "APPROVE":
        return (
            f"Eligible ({reason}); settlement authority approves "
            f"{det['settlement_authority_amount']} (covered tonnage capped at shipped, "
            "freight/proration applied)."
        )
    return f"Deterministic outcome: {verdict} ({reason})."


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
