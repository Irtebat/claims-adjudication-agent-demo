import pytest

from scorers import (
    amount_matches_authority,
    amount_matches_gold,
    citation_recall,
    citation_reciprocal_rank,
    citations_in_resolved_policy,
    disposition_exact_match,
    invariant_clean,
    no_payable_duplicate,
    verdict_exact_match,
    verdict_matches_eligibility,
)


@pytest.fixture
def output():
    return {
        "custom_outputs": {
            "recommendation": {
                "recommended_verdict": "DENY",
                "recommended_disposition": "DUPLICATE",
                "settlement_estimate": "12.30",
                "approved_amount": "0.00",
            },
            "deterministic": {
                "reason": "duplicate_claim",
                "eligible": False,
                "settlement_authority_amount": "12.30",
            },
            "invariant_violations": [],
            "cited_clause_ids": ["policy/a", "policy/b"],
        }
    }


def test_exact_and_invariant_scorers(output):
    expected = {
        "verdict": "DENY",
        "disposition": "DUPLICATE",
        "approved_amount": "0.00",
        "oracle_clause_ids": ["policy/a", "policy/b", "policy/c"],
        "gold_cited_clause_ids": ["policy/b", "policy/c"],
    }
    assert verdict_exact_match(outputs=output, expectations=expected)
    assert disposition_exact_match(outputs=output, expectations=expected)
    assert no_payable_duplicate(outputs=output)
    assert amount_matches_authority(outputs=output)
    assert amount_matches_gold(outputs=output, expectations=expected)
    assert verdict_matches_eligibility(outputs=output)
    assert invariant_clean(outputs=output)
    assert citations_in_resolved_policy(outputs=output, expectations=expected)
    assert citation_recall(outputs=output, expectations=expected) == 0.5
    assert citation_reciprocal_rank(outputs=output, expectations=expected) == 0.5


def test_money_is_exact_to_cents(output):
    output["custom_outputs"]["recommendation"]["settlement_estimate"] = "12.31"
    assert not amount_matches_authority(outputs=output)


def test_gold_money_is_exact(output):
    assert amount_matches_gold(outputs=output, expectations={"approved_amount": "0.00"})
    assert not amount_matches_gold(outputs=output, expectations={"approved_amount": "0.01"})


def test_pend_verdict_normalizes_but_disposition_already_matches(output):
    recommendation = output["custom_outputs"]["recommendation"]
    recommendation["recommended_verdict"] = "PEND_INVESTIGATE"
    recommendation["recommended_disposition"] = "PEND_INVESTIGATE"
    expectations = {"verdict": "PEND", "disposition": "PEND_INVESTIGATE"}
    assert verdict_exact_match(outputs=output, expectations=expectations)
    assert disposition_exact_match(outputs=output, expectations=expectations)
