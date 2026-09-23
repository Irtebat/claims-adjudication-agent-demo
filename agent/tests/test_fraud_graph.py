"""Heat-cluster risk: customer concentration surfaces a collusion ring, not normal heats."""

from fraud_graph import build_edges, connected_components, score_clusters

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
