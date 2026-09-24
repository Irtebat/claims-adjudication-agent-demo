"""Exact release-gate scorers and separately declared diagnostic judges."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from mlflow.genai.scorers import Correctness, Guidelines, RetrievalGroundedness, Safety, scorer

JUDGE_MODEL = "databricks:/databricks-meta-llama-3-3-70b-instruct"
RELEASE_THRESHOLDS = {
    "verdict_exact_match": 0.95,
    "disposition_exact_match": 0.95,
    "no_payable_duplicate": 1.0,
    "amount_matches_authority": 1.0,
    "verdict_matches_eligibility": 1.0,
    "invariant_clean": 0.95,
    "citations_in_resolved_policy": 1.0,
    "citation_recall": 0.8,
    "citation_reciprocal_rank": 0.8,
}


def _custom(outputs: dict) -> dict:
    return outputs.get("custom_outputs", outputs)


def _money(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@scorer
def verdict_exact_match(outputs: dict, expectations: dict) -> bool:
    return _custom(outputs).get("recommendation", {}).get(
        "recommended_verdict"
    ) == expectations.get("verdict")


@scorer
def disposition_exact_match(outputs: dict, expectations: dict) -> bool:
    return _custom(outputs).get("recommendation", {}).get(
        "recommended_disposition"
    ) == expectations.get("disposition")


@scorer
def no_payable_duplicate(outputs: dict) -> bool:
    data = _custom(outputs)
    deterministic = data.get("deterministic", {})
    if deterministic.get("reason") != "duplicate_claim":
        return True
    recommendation = data.get("recommendation", {})
    return (
        recommendation.get("recommended_verdict") == "DENY"
        and recommendation.get("recommended_disposition") == "DUPLICATE"
        and _money(recommendation.get("approved_amount")) == Decimal("0.00")
    )


@scorer
def amount_matches_authority(outputs: dict) -> bool:
    data = _custom(outputs)
    return _money(data.get("recommendation", {}).get("settlement_estimate")) == _money(
        data.get("deterministic", {}).get("settlement_authority_amount")
    )


@scorer
def verdict_matches_eligibility(outputs: dict) -> bool:
    data = _custom(outputs)
    det = data.get("deterministic", {})
    rec = data.get("recommendation", {})
    if det.get("reason") == "duplicate_claim":
        return rec.get("recommended_verdict") == "DENY"
    if det.get("eligible") is True:
        return rec.get("recommended_verdict") in ("APPROVE", "PEND_INVESTIGATE")
    if det.get("eligible") is False:
        return rec.get("recommended_verdict") != "APPROVE"
    return rec.get("recommended_verdict") == "PEND_INVESTIGATE"


@scorer
def invariant_clean(outputs: dict) -> bool:
    return not _custom(outputs).get("invariant_violations")


@scorer
def citations_in_resolved_policy(outputs: dict, expectations: dict) -> bool:
    return set(_custom(outputs).get("cited_clause_ids") or []) <= set(
        expectations.get("oracle_clause_ids") or []
    )


@scorer
def citation_recall(outputs: dict, expectations: dict) -> float:
    gold = set(expectations.get("gold_cited_clause_ids") or [])
    return (
        1.0
        if not gold
        else len(set(_custom(outputs).get("cited_clause_ids") or []) & gold) / len(gold)
    )


@scorer
def citation_reciprocal_rank(outputs: dict, expectations: dict) -> float:
    gold = set(expectations.get("gold_cited_clause_ids") or [])
    if not gold:
        return 1.0
    for rank, clause_id in enumerate(_custom(outputs).get("cited_clause_ids") or [], start=1):
        if clause_id in gold:
            return 1.0 / rank
    return 0.0


EXACT_SCORERS = [
    verdict_exact_match,
    disposition_exact_match,
    no_payable_duplicate,
    amount_matches_authority,
    verdict_matches_eligibility,
    invariant_clean,
    citations_in_resolved_policy,
    citation_recall,
    citation_reciprocal_rank,
]
PRIMARY_JUDGE_SCORERS = [
    Guidelines(
        name="authority_guidelines",
        guidelines=(
            "The deterministic authority is controlling. Never invent or alter an amount; cite only "
            "natural clause keys supplied by the resolved policy."
        ),
        model=JUDGE_MODEL,
    ),
    RetrievalGroundedness(model=JUDGE_MODEL),
]
OPTIONAL_JUDGE_SCORERS = [Correctness(model=JUDGE_MODEL), Safety(model=JUDGE_MODEL)]
