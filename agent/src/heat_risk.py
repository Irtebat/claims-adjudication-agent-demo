"""Advisory customer/heat risk lookup — reads the synced-down graph score.

``gold.customer_heat_risk`` (built by the offline fraud-graph job) is synced down
to Lakebase ``reference.customer_heat_risk`` as a Triggered synced table. This
runtime tool reads that row over the same psycopg path the other tools use. Risk
is ADVISORY only: it may raise a fraud flag or a PEND hold, but it never changes
an authority's amount or verdict.
"""

from __future__ import annotations

from typing import Any

RISK_SQL = """
SELECT customer_id, heat_no, cluster_id, cluster_size,
       distinct_customers_in_cluster, distinct_heats_in_cluster,
       repeat_customers, risk_score, computed_at
FROM reference.customer_heat_risk
WHERE customer_id = %(customer_id)s AND heat_no = %(heat_no)s
ORDER BY computed_at DESC
LIMIT 1
"""


def get_customer_heat_risk(conn: Any, customer_id: str, heat_no: str) -> dict:
    """Return the advisory risk row for a (customer, heat), or a zero-risk default.

    A missing row (no cluster membership, or the score not yet computed) is not an
    error — it means no elevated risk was found, so a neutral advisory row is
    returned. ``risk_score`` is in [0, 1]; ``found`` distinguishes a real score.
    """
    with conn.cursor() as cur:
        cur.execute(RISK_SQL, {"customer_id": customer_id, "heat_no": heat_no})
        columns = [c.name for c in cur.description]
        rows = cur.fetchall()
    if not rows:
        return {
            "customer_id": customer_id,
            "heat_no": heat_no,
            "risk_score": 0.0,
            "cluster_id": None,
            "cluster_size": None,
            "repeat_customers": False,
            "found": False,
        }
    row = dict(zip(columns, rows[0]))
    row["risk_score"] = float(row["risk_score"]) if row.get("risk_score") is not None else 0.0
    row["found"] = True
    return row
