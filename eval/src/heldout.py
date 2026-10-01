"""Policy-grounded narrative-dependent held-out dataset builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter
from typing import Any, Iterable

import mlflow

from build_dataset import LABEL_KEYS, LEAN_CLAIM_COLUMNS, _execute_sql, source_sql
from resolver_oracle import ResolverOracle

HELDOUT_TABLE = "fe-bar-ir.eval.heldout_claims"
SCENARIO_COUNTS = {
    "narrative_excluded_installation": 25,
    "narrative_excluded_environment": 25,
    "narrative_coastal_proximity": 25,
    "neutral_control": 25,
}
POLICY = {
    "narrative_excluded_installation": {
        "section_ref": "warranties.coverage.excluded_installations",
        "clause_id": "/warranties/coverage/excluded_installations",
        "fact": "standing_water",
    },
    "narrative_excluded_environment": {
        "section_ref": "warranties.coverage.excluded_environments",
        "clause_id": "/warranties/coverage/excluded_environments",
        "fact": "marine",
    },
    "narrative_coastal_proximity": {
        "section_ref": "warranties.coverage.min_coast_distance_km",
        "clause_id": "/warranties/coverage/min_coast_distance_km",
        "fact": "coast_distance_km<2.0",
    },
    "neutral_control": {
        "section_ref": "warranties.coverage",
        "clause_id": "/warranties/coverage",
        "fact": "structured_and_narrative_agree",
    },
}
NARRATIVES = {
    "narrative_excluded_installation": (
        "Photos show rainwater remaining in the panel laps for days after each storm.",
        "The inspection found persistent pooled water beneath the installed sheets.",
        "Drainage is blocked and water stands against the underside of the panels.",
        "Site notes record ponding in the roof channels after otherwise light rainfall.",
    ),
    "narrative_excluded_environment": (
        "Salt spray reaches the building directly from the adjacent tidal harbor.",
        "The panels face open sea and receive airborne salt during onshore winds.",
        "Inspection notes salt deposits from regular marine aerosol exposure.",
        "The installation is beside a working seaport with direct seawater mist.",
    ),
    "narrative_coastal_proximity": (
        "The survey places the building 0.6 km from the shoreline.",
        "The installation coordinates are 1.2 km inland from the coast.",
        "A site map measures 0.9 km between the roof and the high-water line.",
        "The customer facility is 1.7 km from the nearest coastline.",
    ),
    "neutral_control": (
        "The inland roof is ventilated and remains dry beneath the panels.",
        "Inspection confirms free drainage, inland exposure, and no retained water.",
        "The site is well inland with ventilated installation and clear drainage paths.",
        "No salt exposure or ponding was observed at the ventilated inland site.",
    ),
}
_ID_NAMESPACE = uuid.UUID("8d142754-2467-4e77-810b-b702595834b8")


def disposition_class(disposition: str | None, verdict: str | None = None) -> str | None:
    if disposition in {"CREDIT", "REWORK", "REPLACEMENT"}:
        return "APPROVE"
    if disposition == "DUPLICATE":
        return "DENY"
    if disposition == "PEND_INVESTIGATE":
        return "PEND"
    return disposition or verdict


def approval_subchoice(disposition: str | None, verdict: str | None = None) -> str | None:
    return disposition if disposition_class(disposition, verdict) == "APPROVE" else None


def _opaque_id(source_id: str, scenario: str, index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{source_id}|{scenario}|{index}"))


def _source_rows(rows: Iterable[dict]) -> list[dict]:
    eligible = [
        row
        for row in rows
        if row.get("claim_type") == "coating_warranty"
        and row.get("gold_verdict") == "APPROVE"
        and row.get("gold_disposition") in {"CREDIT", "REWORK", "REPLACEMENT"}
    ]
    return sorted(
        eligible, key=lambda row: hashlib.sha256(str(row["claim_id"]).encode()).hexdigest()
    )


def build_heldout_records(reference_rows: Iterable[dict], n: int = 100) -> tuple[list[dict], dict]:
    """Clone real resolver-compatible approved warranty rows into four fixed strata."""
    if n != 100:
        raise ValueError("the narrative held-out study is fixed at exactly 100 claims")
    rows = _source_rows(reference_rows)
    if len(rows) < 25:
        raise ValueError("at least 25 approved real coating-warranty reference rows are required")
    records = []
    for scenario, count in SCENARIO_COUNTS.items():
        for index in range(count):
            source = rows[index]
            claim = {key: source.get(key) for key in LEAN_CLAIM_COLUMNS}
            claim.update(
                claim_id=_opaque_id(str(source["claim_id"]), scenario, index),
                environment="inland",
                installation="ventilated",
                coast_distance_km="10.0",
                defect_narrative=NARRATIVES[scenario][index % len(NARRATIVES[scenario])],
            )
            if scenario == "neutral_control":
                verdict, disposition = source["gold_verdict"], source["gold_disposition"]
            else:
                verdict, disposition = "DENY", "DENY"
            records.append(
                {
                    "inputs": {"claim": claim},
                    "expectations": {
                        "verdict": verdict,
                        "disposition": disposition,
                        "disposition_class": disposition_class(disposition, verdict),
                        "approval_subchoice": approval_subchoice(disposition, verdict),
                        "scenario_type": scenario,
                        "policy_clause_id": POLICY[scenario]["clause_id"],
                        "policy_section_ref": POLICY[scenario]["section_ref"],
                        "narrative_fact": POLICY[scenario]["fact"],
                        "oracle_clause_ids": source.get("_oracle_clause_ids"),
                    },
                }
            )
    validate_heldout(records)
    metadata = {
        "record_count": len(records),
        "stratification": dict(
            sorted(Counter(r["expectations"]["scenario_type"] for r in records).items())
        ),
        "policy": POLICY,
        "destination": HELDOUT_TABLE,
        "labels_location": "expectations_only",
    }
    return records, metadata


def validate_heldout(records: list[dict]) -> None:
    if len(records) != 100:
        raise ValueError(f"held-out set must contain 100 claims, got {len(records)}")
    counts = Counter()
    for record in records:
        claim = record.get("inputs", {}).get("claim", {})
        leaked = (LABEL_KEYS | {"disposition_class", "approval_subchoice"}) & claim.keys()
        if leaked:
            raise ValueError(f"held-out label leakage in inputs.claim: {sorted(leaked)}")
        if set(claim) != set(LEAN_CLAIM_COLUMNS):
            raise ValueError("held-out inputs must be projected to LEAN_CLAIM_COLUMNS")
        expected = record["expectations"]
        if not expected.get("policy_clause_id") or not expected.get("policy_section_ref"):
            raise ValueError("each held-out row must cite its written policy")
        counts[expected["scenario_type"]] += 1
    if dict(counts) != SCENARIO_COUNTS:
        raise ValueError(f"invalid held-out stratification: {dict(counts)}")


def _validate_live_narrative_dependence(records: list[dict], profile: str) -> None:
    """Run the actual frozen structured authorities and verify every narrative contrast."""
    from authorities_runtime import AuthorityRuntime
    from db import connect
    from decision_record import deterministic_outcome
    from duplicate import check_duplicate_claim

    with connect(profile=profile, autocommit=True) as connection:
        runtime = AuthorityRuntime(connection)
        for record in records:
            claim = record["inputs"]["claim"]
            frozen = runtime.freeze(claim["coil_id"])
            conformance = frozen.conformance()
            coverage = frozen.coverage(claim)
            settlement = frozen.settlement(
                {
                    "coil_id": claim["coil_id"],
                    "claim_type": claim["claim_type"],
                    "claimed_tonnage": claim.get("claimed_tonnage") or 0,
                    "claimed_freight": claim.get("claimed_freight") or 0,
                    "proration_factor": coverage.get("proration_factor", 1.0),
                }
            )
            duplicate = check_duplicate_claim(connection, claim)
            structured = deterministic_outcome(
                claim["claim_type"], conformance, coverage, settlement, duplicate
            )
            expected = record["expectations"]
            treatment = expected["scenario_type"] != "neutral_control"
            differs = structured["verdict"] != expected["verdict"]
            if differs != treatment:
                raise ValueError(
                    "narrative-dependence validation failed for "
                    f"{claim['claim_id']}: structured={structured['verdict']}, "
                    f"gold={expected['verdict']}, scenario={expected['scenario_type']}"
                )


def create_live(profile: str, warehouse_id: str, experiment_id: str) -> tuple[Any, dict]:
    """Explicit live creation path; writes only the isolated UC evaluation dataset."""
    rows = _source_rows(_execute_sql(profile, warehouse_id, source_sql()))
    oracles = ResolverOracle(profile).resolve_rows(rows[:25])
    for row, oracle in zip(rows[:25], oracles):
        row["_oracle_clause_ids"] = oracle["oracle_clause_ids"]
    records, metadata = build_heldout_records(rows)
    _validate_live_narrative_dependence(records, profile)
    try:
        dataset = mlflow.genai.datasets.get_dataset(name=HELDOUT_TABLE)
    except Exception as exc:
        if not any(text in str(exc).lower() for text in ("not found", "does not exist")):
            raise
        dataset = mlflow.genai.datasets.create_dataset(
            name=HELDOUT_TABLE, experiment_id=experiment_id
        )
    dataset.merge_records(records)
    return dataset, metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--warehouse-id")
    parser.add_argument("--experiment", default="/Shared/claims-adjudication-ablation")
    args = parser.parse_args()
    if args.create:
        if not args.profile or not args.warehouse_id:
            parser.error("--create requires explicit --profile and --warehouse-id")
        mlflow.set_tracking_uri("databricks")
        experiment = mlflow.set_experiment(args.experiment)
        _, metadata = create_live(args.profile, args.warehouse_id, experiment.experiment_id)
        print(json.dumps(metadata, indent=2))
    elif args.describe:
        print(
            json.dumps({"destination": HELDOUT_TABLE, "stratification": SCENARIO_COUNTS}, indent=2)
        )
    else:
        parser.error("choose --describe or --create")


if __name__ == "__main__":
    main()
