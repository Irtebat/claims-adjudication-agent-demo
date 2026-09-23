"""Fraud / quality cluster risk — offline connected-components over shared heats.

Organized fraud and systemic quality problems show up as *clusters*: claims that
share a heat (the traceability batch), often filed under a small set of reused
customer identities (collusion) — PLAN §6.3. This clusters claims by shared
``heat_no`` with connected components (union-find) and scores each by customer
concentration, producing ``gold.customer_heat_risk`` for the agent's ``get_risk``
tool. It is advisory context, not a money authority.

Pure functions here (unit-tested); the thin Spark driver in ``fraud_graph_job.py``
reads ``gold.claims_history``, runs these, and writes the gold table.
"""

from __future__ import annotations

from collections import defaultdict


def _chain_edges(groups: dict) -> list[tuple[str, str]]:
    """Turn same-key groups into a spanning chain of edges (union-find collapses them)."""
    edges: list[tuple[str, str]] = []
    for members in groups.values():
        first = members[0]
        for other in members[1:]:
            edges.append((first, other))
    return edges


def build_edges(claims: list[dict]) -> list[tuple[str, str]]:
    """Edges between claims sharing a heat_no (the traceability batch).

    Heat is the natural cluster key: one heat is a handful of coils. Linking by
    coating-supplier lot instead groups ~a whole production block of unrelated
    claims, drowning the collusion signal, so we cluster by heat and read the
    fraud signal (customer concentration) inside each heat cluster.
    """
    by_heat: dict[str, list[str]] = defaultdict(list)
    for claim in claims:
        if claim.get("heat_no"):
            by_heat[claim["heat_no"]].append(claim["claim_id"])
    return _chain_edges(by_heat)


def connected_components(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, str]:
    """Union-find → map each node id to its component root (lexicographically smallest id)."""
    parent = {n: n for n in node_ids}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:  # path compression
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        parent[hi] = lo  # keep the smallest id as the root for deterministic output

    for a, b in edges:
        if a in parent and b in parent:
            union(a, b)
    return {n: find(n) for n in node_ids}


def score_clusters(claims: list[dict]) -> dict:
    """Return per-cluster stats and per-(customer, heat) risk rows.

    risk_score in [0, 1] is driven by customer concentration within a heat cluster:
    an ordinary heat draws claims from as many customers as coils, but a collusion
    ring reuses a small set of customer identities across the heat's coils.
    """
    node_ids = [c["claim_id"] for c in claims]
    roots = connected_components(node_ids, build_edges(claims))
    by_id = {c["claim_id"]: c for c in claims}

    clusters: dict[str, dict] = defaultdict(
        lambda: {"claims": set(), "customers": set(), "heats": set()}
    )
    for cid, root in roots.items():
        claim = by_id[cid]
        bucket = clusters[root]
        bucket["claims"].add(cid)
        if claim.get("customer_id"):
            bucket["customers"].add(claim["customer_id"])
        if claim.get("heat_no"):
            bucket["heats"].add(claim["heat_no"])

    cluster_stats: dict[str, dict] = {}
    for root, bucket in clusters.items():
        n_claims = len(bucket["claims"])
        n_customers = len(bucket["customers"])
        concentration = 1.0 - (n_customers / n_claims) if n_claims else 0.0
        risk = min(1.0, 1.5 * concentration)
        cluster_stats[root] = {
            "cluster_id": root,
            "n_claims": n_claims,
            "n_customers": n_customers,
            "n_heats": len(bucket["heats"]),
            "repeat_customers": n_customers < n_claims,
            "risk_score": round(risk, 4),
        }

    risk_rows: dict[tuple[str, str], dict] = {}
    for cid, root in roots.items():
        claim = by_id[cid]
        customer, heat = claim.get("customer_id"), claim.get("heat_no")
        if not customer or not heat:
            continue
        stats = cluster_stats[root]
        key = (customer, heat)
        if key not in risk_rows or stats["risk_score"] > risk_rows[key]["risk_score"]:
            risk_rows[key] = {
                "customer_id": customer,
                "heat_no": heat,
                "cluster_id": root,
                "cluster_size": stats["n_claims"],
                "distinct_customers_in_cluster": stats["n_customers"],
                "distinct_heats_in_cluster": stats["n_heats"],
                "repeat_customers": stats["repeat_customers"],
                "risk_score": stats["risk_score"],
            }
    return {"clusters": cluster_stats, "risk_rows": list(risk_rows.values())}
