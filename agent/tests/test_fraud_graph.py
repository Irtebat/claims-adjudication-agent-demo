"""Heat-cluster risk: customer concentration surfaces a collusion ring, not normal heats."""

import math

import pytest

from fraud_graph import (
    HIGH_RISK_MIN_CLUSTER_SIZE,
    HIGH_RISK_SCORE_THRESHOLD,
    build_edges,
    connected_components,
    is_high_risk,
    score_clusters,
)

# A collusion ring: one heat, five claims, but only three customer identities reused
# across its coils (customer concentration). A normal heat draws one customer per coil.
RING = [
    {"claim_id": "R1", "customer_id": "CUST-0197", "heat_no": "HEAT-1"},
    {"claim_id": "R2", "customer_id": "CUST-0198", "heat_no": "HEAT-1"},
    {"claim_id": "R3", "customer_id": "CUST-0199", "heat_no": "HEAT-1"},
    {"claim_id": "R4", "customer_id": "CUST-0197", "heat_no": "HEAT-1"},
    {"claim_id": "R5", "customer_id": "CUST-0198", "heat_no": "HEAT-1"},
]
NORMAL = [
    {"claim_id": "N1", "customer_id": "CUST-0010", "heat_no": "HEAT-2"},
    {"claim_id": "N2", "customer_id": "CUST-0020", "heat_no": "HEAT-2"},
    {"claim_id": "N3", "customer_id": "CUST-0030", "heat_no": "HEAT-2"},
]
ISOLATED = {"claim_id": "S1", "customer_id": "CUST-0042", "heat_no": "HEAT-9"}


def test_shared_heat_forms_one_component():
    claims = RING + NORMAL + [ISOLATED]
    roots = connected_components([c["claim_id"] for c in claims], build_edges(claims))
    assert len({roots[c["claim_id"]] for c in RING}) == 1  # the ring heat is one cluster
    assert len({roots[c["claim_id"]] for c in NORMAL}) == 1
    assert roots["S1"] not in {roots["R1"], roots["N1"]}  # separate heat, separate cluster


def test_ring_scores_above_normal_and_isolated():
    result = score_clusters(RING + NORMAL + [ISOLATED])
    ring = [r for r in result["risk_rows"] if r["customer_id"].startswith("CUST-019")]
    normal = [r for r in result["risk_rows"] if r["heat_no"] == "HEAT-2"]
    isolated = [r for r in result["risk_rows"] if r["customer_id"] == "CUST-0042"]

    assert ring and all(r["repeat_customers"] for r in ring)
    assert all(r["distinct_customers_in_cluster"] == 3 and r["cluster_size"] == 5 for r in ring)
    assert min(r["risk_score"] for r in ring) > 0.5
    # a normal heat (one customer per coil) and an isolated claim are not risky
    assert all(not r["repeat_customers"] and r["risk_score"] == 0.0 for r in normal)
    assert all(r["risk_score"] == 0.0 for r in isolated)


def test_scoring_is_deterministic():
    assert score_clusters(RING + NORMAL)["risk_rows"] == score_clusters(RING + NORMAL)["risk_rows"]


# --------------------------------------------------------------------------- #
# Sensitivity tuning: cluster_size / n_customers, the high_risk threshold, and the
# one-sentence reason. These are the fields the App chip/tooltip render, persisted
# to gold.customer_heat_risk and served down.
# --------------------------------------------------------------------------- #


def _heat(heat_no: str, n_claims: int, n_customers: int) -> list[dict]:
    """One heat with n_claims claims spread across exactly n_customers identities."""
    assert 1 <= n_customers <= n_claims
    return [
        {"claim_id": f"{heat_no}-{i}", "customer_id": f"C{i % n_customers}", "heat_no": heat_no}
        for i in range(n_claims)
    ]


def test_cluster_size_and_n_customers_counts():
    # 6 claims, 2 reused identities on one heat.
    rows = score_clusters(_heat("H-SIZE", 6, 2))["risk_rows"]
    assert rows  # one row per (customer, heat) — two customers here
    assert all(r["cluster_size"] == 6 for r in rows)
    assert all(r["n_customers"] == 2 for r in rows)
    # n_customers mirrors the original distinct_customers_in_cluster count.
    assert all(r["n_customers"] == r["distinct_customers_in_cluster"] for r in rows)


def test_is_high_risk_inclusive_threshold():
    # Direct check of the tuned decision at the inclusive >= 0.75 score boundary.
    # nextafter pins the immediately adjacent representable floats without relying on
    # exact equality for a computed score. Size is held at the floor.
    just_below = math.nextafter(HIGH_RISK_SCORE_THRESHOLD, -math.inf)
    just_above = math.nextafter(HIGH_RISK_SCORE_THRESHOLD, math.inf)
    assert is_high_risk(HIGH_RISK_SCORE_THRESHOLD, HIGH_RISK_MIN_CLUSTER_SIZE) is True
    assert is_high_risk(just_above, HIGH_RISK_MIN_CLUSTER_SIZE) is True
    assert is_high_risk(just_below, HIGH_RISK_MIN_CLUSTER_SIZE) is False
    # The size floor still gates even when the score qualifies.
    assert is_high_risk(HIGH_RISK_SCORE_THRESHOLD, HIGH_RISK_MIN_CLUSTER_SIZE - 1) is False
    assert is_high_risk(1.0, HIGH_RISK_MIN_CLUSTER_SIZE - 1) is False


def test_high_risk_score_boundary():
    # The cluster score is DISCRETE: risk = min(1.0, 1.5 * (1 - n_customers/n_claims)),
    # so only certain values occur and floats aren't exact — compare with pytest.approx.
    # AT the inclusive boundary: 2 of 4 identities -> concentration 0.5 -> risk ~0.75,
    # size 4 -> flagged.
    at = score_clusters(_heat("H-AT", 4, 2))["risk_rows"]
    assert at
    assert all(r["risk_score"] == pytest.approx(HIGH_RISK_SCORE_THRESHOLD) for r in at)
    assert all(r["high_risk"] is True for r in at)
    # A practical value BELOW 0.75 from the discrete formula: 4 of 7 identities ->
    # concentration 3/7 -> risk ~0.6429, size 7 -> not flagged.
    below = score_clusters(_heat("H-BELOW", 7, 4))["risk_rows"]
    assert below
    assert all(r["risk_score"] == pytest.approx(0.6429) for r in below)
    assert all(r["risk_score"] < HIGH_RISK_SCORE_THRESHOLD for r in below)
    assert all(r["high_risk"] is False for r in below)


def test_high_risk_cluster_size_boundary():
    # High score (risk 1.0) but size == 3 -> flagged (meets the minimum).
    at = score_clusters(_heat("H-3", HIGH_RISK_MIN_CLUSTER_SIZE, 1))["risk_rows"]
    assert at and all(r["cluster_size"] == HIGH_RISK_MIN_CLUSTER_SIZE for r in at)
    assert all(r["risk_score"] >= HIGH_RISK_SCORE_THRESHOLD and r["high_risk"] for r in at)
    # High score (risk 0.75) but only 2 claims -> below the size floor, not flagged.
    below = score_clusters(_heat("H-2", HIGH_RISK_MIN_CLUSTER_SIZE - 1, 1))["risk_rows"]
    assert below and all(r["cluster_size"] == 2 for r in below)
    assert all(r["risk_score"] >= HIGH_RISK_SCORE_THRESHOLD for r in below)
    assert all(r["high_risk"] is False for r in below)


def test_risk_reason_format_and_null_for_benign():
    # 4 claims from a single reused identity on heat H-4821 -> flagged.
    flagged = score_clusters(_heat("H-4821", 4, 1))["risk_rows"]
    assert len(flagged) == 1
    reason = flagged[0]["risk_reason"]
    assert reason == "Fraud cluster: 4 claims from 1 customer on heat H-4821 (concentration 0.75)."
    # Benign heat (one customer per claim, risk 0) carries no reason.
    benign = score_clusters(_heat("H-OK", 3, 3))["risk_rows"]
    assert benign and all(not r["high_risk"] and r["risk_reason"] is None for r in benign)
