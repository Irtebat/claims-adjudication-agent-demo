"""Fraud / quality cluster risk — offline connected-components over shared heats.

Organized fraud and systemic quality problems show up as *clusters*: claims that
share a heat (the traceability batch), often filed under a small set of reused
customer identities (collusion) — PLAN §6.3. The plan names supplier-lot patterns
as another possible signal, but this implementation deliberately links only shared
``heat_no`` values because supplier lots span broad, otherwise unrelated production
blocks. It scores each heat cluster by customer concentration and produces
``gold.customer_heat_risk`` for the agent's ``get_risk`` tool. It is advisory
context, not a money authority.

Pure functions here (unit-tested); the thin Spark driver in ``fraud_graph_job.py``
reads ``gold.claims_current``, resolves ``heat_no`` through ``silver.heats_coils``,
runs these, and writes the gold table.
"""

from __future__ import annotations

from collections import defaultdict

# A cluster is treated as high-risk / fraud only when the customer-concentration
# score is strong AND the heat component is more than a couple of claims. Raw
# positive risk is far too common (a single repeat customer on a 2-claim heat) to
# call "fraud"; requiring both a >=0.6 score and >=3 claims keeps the flag rare and
# meaningful (PLAN §6.3). The app renders a chip/tooltip off ``high_risk`` +
# ``risk_reason``; both are persisted here so the read side stays a pure lookup.
HIGH_RISK_SCORE_THRESHOLD = 0.6
HIGH_RISK_MIN_CLUSTER_SIZE = 3


def is_high_risk(risk_score: float, cluster_size: int) -> bool:
    """The tuned fraud flag: strong customer concentration AND a non-trivial heat.

    Inclusive on the score (``>= 0.6``) and the size (``>= 3``). Kept as a small pure
    predicate so the inclusive boundary can be unit-tested directly at values the
    discrete ``1.5 * (1 - customers/claims)`` score can't land on exactly.
    """
    return risk_score >= HIGH_RISK_SCORE_THRESHOLD and cluster_size >= HIGH_RISK_MIN_CLUSTER_SIZE


def _risk_reason(cluster_size: int, n_customers: int, heat_no: str, concentration: float) -> str:
    """One human-readable sentence explaining why a cluster is flagged (for hover)."""
    claims = "claim" if cluster_size == 1 else "claims"
    customers = "customer" if n_customers == 1 else "customers"
    return (
        f"Fraud cluster: {cluster_size} {claims} from {n_customers} {customers} "
        f"on heat {heat_no} (concentration {concentration:.2f})."
    )


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

    Each risk row also carries the app-facing fields ``cluster_size``,
    ``n_customers``, a boolean ``high_risk`` (``risk_score >= 0.6`` AND
    ``cluster_size >= 3``), and a one-sentence ``risk_reason`` (populated only when
    ``high_risk``). These persist to ``gold.customer_heat_risk`` and serve down.
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
            n_claims = stats["n_claims"]
            n_customers = stats["n_customers"]
            concentration = 1.0 - (n_customers / n_claims) if n_claims else 0.0
            high_risk = is_high_risk(stats["risk_score"], n_claims)
            risk_rows[key] = {
                "customer_id": customer,
                "heat_no": heat,
                "cluster_id": root,
                "cluster_size": n_claims,
                "n_customers": n_customers,
                # distinct_customers_in_cluster is the same count under its original
                # name; kept for the existing runtime read (heat_risk.py) contract.
                "distinct_customers_in_cluster": n_customers,
                "distinct_heats_in_cluster": stats["n_heats"],
                "repeat_customers": stats["repeat_customers"],
                "risk_score": stats["risk_score"],
                "high_risk": high_risk,
                # A reason is only meaningful for a flagged cluster; benign rows carry
                # None so the app shows a tooltip only where high_risk is true.
                "risk_reason": (
                    _risk_reason(n_claims, n_customers, heat, concentration)
                    if high_risk
                    else None
                ),
            }
    return {"clusters": cluster_stats, "risk_rows": list(risk_rows.values())}
