"""Offline validation — run the agent on a labeled sample and capture the evidence.

Selects claims spanning every injected label pattern (clean approve,
in-spec-should-DENY, over-claim, duplicate, out-of-warranty, supplier-attributable,
fraud-cluster), runs the in-process agent (the same ``agent.py`` that is registered
and deployed) on each, and proves the CORE PRINCIPLE: the LLM's recommendation
never overrides an authority. It records, per claim, the recommendation vs the
deterministic authority outputs and any invariant corrections; reads back the
decision records written to Lakebase; and writes the evidence under
``docs/evidence/claims-adjudication-agent/``.

Run with the reasoning endpoint reachable and ``DATABRICKS_CONFIG_PROFILE=fe-bar``;
``LAKEBASE_PROFILE=fe-bar`` selects the Lakebase workspace for the agent.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import mlflow

# Patterns and the SQL that picks a representative claim for each, joining the
# seeded FINAL adjudications. Each returns a single claim_id.
PATTERN_SQL = {
    "clean_approve": "verdict = 'APPROVE' AND disposition IN ('CREDIT','REPLACEMENT','REWORK') AND (duplicate_of_claim_id IS NULL) AND approved_amount = claimed_amount",
    "over_claim_partial": "verdict = 'APPROVE' AND approved_amount < claimed_amount AND duplicate_of_claim_id IS NULL",
    "in_spec_or_warranty_deny": "verdict = 'DENY' AND disposition = 'DENY'",
    "duplicate": "disposition = 'DUPLICATE'",
    "pend_investigate": "verdict = 'PEND'",
    "supplier_attributable": "supplier_attributable = true",
    "fraud_cluster": "fraud_cluster_id IS NOT NULL",
}
CLAIM_COLUMNS = [
    "claim_id",
    "coil_id",
    "customer_id",
    "claim_type",
    "claim_date",
    "install_date",
    "environment",
    "installation",
    "coast_distance_km",
    "defect_code",
    "defect_narrative",
    "claimed_tonnage",
    "claimed_freight",
]


def _select_sample(conn) -> dict[str, dict]:
    """One claim per label pattern; skip a pattern that has no seeded example."""
    sample: dict[str, dict] = {}
    seen: set[str] = set()
    with conn.cursor() as cur:
        for pattern, predicate in PATTERN_SQL.items():
            cur.execute(
                f"SELECT c.{', c.'.join(CLAIM_COLUMNS)} FROM claims c "
                "JOIN adjudications a USING (claim_id) "
                f"WHERE a.decision_status = 'FINAL' AND c.coil_id IS NOT NULL AND ({predicate}) "
                "ORDER BY c.claim_id LIMIT 5"
            )
            columns = [d.name for d in cur.description]
            for row in cur.fetchall():
                claim = dict(zip(columns, row))
                if claim["claim_id"] in seen:
                    continue
                claim = {
                    k: (str(v) if hasattr(v, "isoformat") else _num(v)) for k, v in claim.items()
                }
                sample[pattern] = claim
                seen.add(claim["claim_id"])
                break
    return sample


def _num(value):
    from decimal import Decimal

    return float(value) if isinstance(value, Decimal) else value


def run(profile: str, experiment: str, destination: Path) -> dict:
    os.environ["LAKEBASE_PROFILE"] = profile
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(experiment)

    import sys

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from agent import AGENT
    from db import connect

    with connect(profile=profile, autocommit=True) as conn:
        sample = _select_sample(conn)

    results = []
    for pattern, claim in sample.items():
        outcome = AGENT.adjudicate(claim, persist=True)
        record = outcome["record"]
        det = outcome["deterministic"]
        # The proof: the persisted verdict/amount equal the deterministic authority's.
        approved_matches = (
            abs(
                record["approved_amount"]
                - (det["settlement_authority_amount"] if det["verdict"] == "APPROVE" else 0.0)
            )
            <= 0.005
        )
        duplicate_not_paid = not (record["duplicate_flag"] and record["approved_amount"] != 0)
        results.append(
            {
                "pattern": pattern,
                "claim_id": claim["claim_id"],
                "claim_type": claim.get("claim_type"),
                "deterministic_verdict": det["verdict"],
                "deterministic_disposition": det["disposition"],
                "settlement_authority_amount": det["settlement_authority_amount"],
                "recommended_verdict": record["recommended_verdict"],
                "recommended_disposition": record["recommended_disposition"],
                "approved_amount": record["approved_amount"],
                "invariant_violations": outcome["invariant_violations"],
                "llm_used": outcome["llm_used"],
                "cited_clause_ids": record["cited_clause_ids"],
                "adjudication_id": record["adjudication_id"],
                "approved_amount_matches_authority": approved_matches,
                "duplicate_never_paid": duplicate_not_paid,
                "authority_never_overridden": approved_matches and duplicate_not_paid,
            }
        )

    # Read back the decision records from Lakebase to prove the transactional write.
    adjudication_ids = [r["adjudication_id"] for r in results]
    written = []
    with connect(profile=profile, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT adjudication_id, claim_id, record_version, recommended_verdict, "
                "approved_amount, duplicate_flag, over_claim_flag, "
                "array_length(cited_clause_ids, 1) AS n_citations, "
                "invariant_violations, schema_version "
                "FROM adjudication_decision_records WHERE adjudication_id = ANY(%(ids)s) "
                "ORDER BY adjudication_id",
                {"ids": adjudication_ids},
            )
            columns = [d.name for d in cur.description]
            written = [
                {k: _num(v) for k, v in dict(zip(columns, row)).items()} for row in cur.fetchall()
            ]

    summary = {
        "sample_size": len(results),
        "patterns": sorted(sample),
        "authority_never_overridden": all(r["authority_never_overridden"] for r in results),
        "llm_used_count": sum(1 for r in results if r["llm_used"]),
        "decision_records_written": len(written),
        "results": results,
        "decision_records_readback": written,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "offline-validation.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "fe-bar"))
    parser.add_argument(
        "--experiment",
        default="/Users/irtebat.shaukat@databricks.com/claims_adjudication_agent",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "docs/evidence/claims-adjudication-agent",
    )
    args = parser.parse_args()
    summary = run(args.profile, args.experiment, args.destination)
    print(
        json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2, sort_keys=True)
    )


if __name__ == "__main__":
    main()
