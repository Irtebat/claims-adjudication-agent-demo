"""True-duplicate detection — deterministic record linkage, a leakage gate that denies money.

A duplicate is the same *identity*, not similar *text* (PLAN §6.2). We block by
coil/heat and a claim-date window, then match on exact defect code, numeric
proximity of tonnage/amount, and ``pg_trgm`` narrative similarity. Vector
similarity is deliberately NOT the duplicate authority.

The pure ``duplicate_decision`` encodes the match rule and is unit-tested; the
SQL blocking query runs in Lakebase (``pg_trgm``), GA-only.
"""

from __future__ import annotations

from datetime import date
from typing import Any

DEFAULTS = {
    "window_days": 7,
    "narrative_similarity_threshold": 0.9,
    "amount_tolerance": 0.01,
    "tonnage_tolerance": 0.001,
}


def _as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def duplicate_decision(
    incoming: dict,
    candidate: dict,
    narrative_similarity: float,
    config: dict | None = None,
) -> dict:
    """Decide whether ``candidate`` (a prior claim) is a true duplicate of ``incoming``."""
    cfg = {**DEFAULTS, **(config or {})}
    reasons: list[str] = []

    same_coil = incoming.get("coil_id") is not None and incoming["coil_id"] == candidate.get(
        "coil_id"
    )
    if not same_coil:
        reasons.append("different_coil")
    date_diff = (_as_date(incoming["claim_date"]) - _as_date(candidate["claim_date"])).days
    within_window = 0 <= date_diff <= cfg["window_days"]
    if not within_window:
        reasons.append("outside_date_window")
    same_defect = incoming.get("defect_code") == candidate.get("defect_code")
    if not same_defect:
        reasons.append("different_defect_code")
    freight_close = (
        abs(float(incoming.get("claimed_freight", 0)) - float(candidate.get("claimed_freight", 0)))
        <= cfg["amount_tolerance"]
    )
    if not freight_close:
        reasons.append("freight_differs")
    tonnage_close = (
        abs(float(incoming["claimed_tonnage"]) - float(candidate["claimed_tonnage"]))
        <= cfg["tonnage_tolerance"]
    )
    if not tonnage_close:
        reasons.append("tonnage_differs")
    narrative_match = narrative_similarity >= cfg["narrative_similarity_threshold"]
    if not narrative_match:
        reasons.append("narrative_below_threshold")

    is_duplicate = (
        same_coil
        and within_window
        and same_defect
        and freight_close
        and tonnage_close
        and narrative_match
    )
    return {
        "is_duplicate": is_duplicate,
        "duplicate_of_claim_id": candidate.get("claim_id") if is_duplicate else None,
        "narrative_similarity": round(float(narrative_similarity), 4),
        "date_diff_days": date_diff,
        "reasons": [] if is_duplicate else reasons,
        "verdict": "DENY" if is_duplicate else None,
        "disposition": "DUPLICATE" if is_duplicate else None,
        "decision_status": "FINAL" if is_duplicate else None,
    }


# The blocking query: same coil, a bounded forward date window, exclude the claim
# itself, and surface pg_trgm narrative similarity for the decision step.
BLOCKING_SQL = """
SELECT claim_id, coil_id, defect_code, claimed_freight, claimed_tonnage, claim_date,
       similarity(defect_narrative, %(narrative)s) AS narrative_similarity
FROM claims
WHERE coil_id = %(coil_id)s
  AND claim_id <> %(claim_id)s
  AND claim_date BETWEEN %(claim_date)s::date - make_interval(days => %(window_days)s)
                     AND %(claim_date)s::date
ORDER BY narrative_similarity DESC
LIMIT %(limit)s
"""


def check_duplicate_claim(conn: Any, incoming: dict, config: dict | None = None) -> dict:
    """Run the Lakebase record-linkage blocking query and apply the deterministic rule."""
    cfg = {**DEFAULTS, **(config or {})}
    params = {
        "narrative": incoming.get("defect_narrative", ""),
        "coil_id": incoming["coil_id"],
        "claim_id": incoming.get("claim_id", ""),
        "claim_date": str(incoming["claim_date"]),
        "window_days": cfg["window_days"],
        "limit": 25,
    }
    with conn.cursor() as cur:
        cur.execute(BLOCKING_SQL, params)
        columns = [c.name for c in cur.description]
        candidates = [dict(zip(columns, row)) for row in cur.fetchall()]
    for candidate in candidates:
        decision = duplicate_decision(incoming, candidate, candidate["narrative_similarity"], cfg)
        if decision["is_duplicate"]:
            return {**decision, "candidates_considered": len(candidates)}
    return {
        "is_duplicate": False,
        "duplicate_of_claim_id": None,
        "candidates_considered": len(candidates),
        "reasons": ["no_matching_candidate"],
    }
