"""Build a realistic mixed held-out set spanning structured and narrative decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import mlflow

from build_dataset import LABEL_KEYS, LEAN_CLAIM_COLUMNS, _execute_sql, source_sql
from resolver_oracle import AGENT_SRC, ResolverOracle

HELDOUT_TABLE = "fe-bar-ir.eval.heldout_claims_mixed"
DEFAULT_NORMAL_COUNTS = {
    "R7_credit": 15,
    "R5_supplier_attributable": 10,
    "R6_over_claim_partial": 10,
    "R4_ineligible": 15,
    "R1_duplicate": 10,
    "fraud_ring": 10,
}
DEFAULT_NARRATIVE_COUNTS = {
    "excluded_installation": 10,
    "excluded_environment": 10,
    "coastal_lt_2km": 10,
}
SCENARIO_COUNTS = {**DEFAULT_NORMAL_COUNTS, **DEFAULT_NARRATIVE_COUNTS}
POLICY = {
    "excluded_installation": {
        "section_ref": "warranties.coverage.excluded_installations",
        "clause_id": "/warranties/coverage/excluded_installations",
        "fact": "standing_water",
    },
    "excluded_environment": {
        "section_ref": "warranties.coverage.excluded_environments",
        "clause_id": "/warranties/coverage/excluded_environments",
        "fact": "marine",
    },
    "coastal_lt_2km": {
        "section_ref": "warranties.coverage.min_coast_distance_km",
        "clause_id": "/warranties/coverage/min_coast_distance_km",
        "fact": "coast_distance_km<2.0",
    },
}
NARRATIVES = {
    "excluded_installation": (
        "Photos show rainwater remaining in the panel laps for days after each storm.",
        "The inspection found persistent pooled water beneath the installed sheets.",
        "Drainage is blocked and water stands against the underside of the panels.",
        "Site notes record ponding in the roof channels after otherwise light rainfall.",
    ),
    "excluded_environment": (
        "Salt spray reaches the building directly from the adjacent tidal harbor.",
        "The panels face open sea and receive airborne salt during onshore winds.",
        "Inspection notes salt deposits from regular marine aerosol exposure.",
        "The installation is beside a working seaport with direct seawater mist.",
    ),
    "coastal_lt_2km": (
        "The survey places the building 0.6 km from the shoreline.",
        "The installation coordinates are 1.2 km inland from the coast.",
        "A site map measures 0.9 km between the roof and the high-water line.",
        "The customer facility is 1.7 km from the nearest coastline.",
    ),
}
_ID_NAMESPACE = uuid.UUID("8d142754-2467-4e77-810b-b702595834b8")


def disposition_class(disposition: str | None, verdict: str | None = None) -> str | None:
    if disposition in {"CREDIT", "REWORK", "REPLACEMENT"}:
        return "APPROVE"
    if disposition == "DUPLICATE":
        return "DENY"
    if disposition == "PEND_INVESTIGATE" or verdict == "PEND":
        return "PEND"
    return disposition or verdict


def approval_subchoice(disposition: str | None, verdict: str | None = None) -> str | None:
    return disposition if disposition_class(disposition, verdict) == "APPROVE" else None


def _opaque_id(source_id: str, scenario: str, index: int) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, f"{source_id}|{scenario}|{index}"))


def _string_array(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value]
    return [str(item) for item in value]


def _stable(rows: Iterable[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: hashlib.sha256(str(row["claim_id"]).encode()).hexdigest())


def _normal_stratum(row: dict) -> str | None:
    verdict = row.get("gold_verdict")
    disposition = row.get("gold_disposition")
    provenance = str(row.get("label_provenance") or "").lower()
    if verdict in {"PEND", "PEND_INVESTIGATE"} and "fraud" in provenance:
        return "fraud_ring"
    if disposition == "DUPLICATE" or row.get("duplicate_of_claim_id"):
        return "R1_duplicate"
    if verdict == "DENY":
        return "R4_ineligible"
    if verdict == "APPROVE" and disposition == "REPLACEMENT":
        return "R5_supplier_attributable"
    if verdict == "APPROVE" and disposition == "REWORK":
        return "R6_over_claim_partial"
    if verdict == "APPROVE" and disposition == "CREDIT":
        return "R7_credit"
    return None


def _select_sources(
    reference_rows: Iterable[dict],
    normal_counts: dict[str, int],
    narrative_counts: dict[str, int],
) -> tuple[dict[str, list[dict]], list[dict]]:
    rows = list(reference_rows)
    selected: dict[str, list[dict]] = {}
    for stratum, count in normal_counts.items():
        candidates = _stable(row for row in rows if _normal_stratum(row) == stratum)
        if len(candidates) < count:
            raise ValueError(
                f"insufficient history rows for {stratum}: {len(candidates)} < {count}"
            )
        selected[stratum] = candidates[:count]
    narrative_needed = max(narrative_counts.values(), default=0)
    narrative_sources = _stable(
        row
        for row in rows
        if row.get("claim_type") == "coating_warranty"
        and row.get("gold_verdict") == "APPROVE"
        and row.get("gold_disposition") in {"CREDIT", "REWORK", "REPLACEMENT"}
    )
    if len(narrative_sources) < narrative_needed:
        raise ValueError(
            f"insufficient approved coating-warranty narrative sources: "
            f"{len(narrative_sources)} < {narrative_needed}"
        )
    return selected, narrative_sources[:narrative_needed]


def _expectations(row: dict, group: str, stratum: str) -> dict:
    verdict = "PEND" if row.get("gold_verdict") == "PEND_INVESTIGATE" else row["gold_verdict"]
    disposition = row["gold_disposition"]
    oracle = list(
        dict.fromkeys(
            _string_array(row.get("gold_cited_clause_ids"))
            + _string_array(row.get("_oracle_clause_ids"))
        )
    )
    return {
        "verdict": verdict,
        "disposition": disposition,
        "disposition_class": disposition_class(disposition, verdict),
        "approval_subchoice": approval_subchoice(disposition, verdict),
        "approved_amount": row.get("gold_approved_amount"),
        "group": group,
        "stratum": stratum,
        "scenario_type": stratum,
        "policy_clause_id": oracle[0] if oracle else "structured_authorities",
        "policy_section_ref": oracle[0] if oracle else "structured_authorities",
        "narrative_fact": "agrees_with_structured_fields",
        "oracle_clause_ids": oracle,
    }


def _narrative_record(source: dict, stratum: str, index: int) -> dict:
    claim = {key: source.get(key) for key in LEAN_CLAIM_COLUMNS}
    claim.update(
        claim_id=_opaque_id(str(source["claim_id"]), stratum, index),
        environment="inland",
        installation="ventilated",
        coast_distance_km="10.0",
        defect_narrative=NARRATIVES[stratum][index % len(NARRATIVES[stratum])],
    )
    policy = POLICY[stratum]
    oracle = list(
        dict.fromkeys(
            [policy["clause_id"]]
            + _string_array(source.get("_oracle_clause_ids"))
            + _string_array(source.get("gold_cited_clause_ids"))
        )
    )
    deciding_clause_id = next(
        (clause_id for clause_id in oracle if clause_id.endswith("/exclusions")),
        policy["clause_id"],
    )
    return {
        "inputs": {"claim": claim},
        "expectations": {
            "verdict": "DENY",
            "disposition": "DENY",
            "disposition_class": "DENY",
            "approval_subchoice": None,
            "approved_amount": "0.00",
            "group": "narrative",
            "stratum": stratum,
            "scenario_type": stratum,
            "policy_clause_id": policy["clause_id"],
            "deciding_clause_id": deciding_clause_id,
            "policy_section_ref": policy["section_ref"],
            "narrative_fact": policy["fact"],
            "oracle_clause_ids": oracle,
        },
    }


def _build_from_selected(
    normal_sources: dict[str, list[dict]],
    narrative_sources: list[dict],
    normal_counts: dict[str, int],
    narrative_counts: dict[str, int],
) -> tuple[list[dict], dict]:
    records = []
    for stratum in normal_counts:
        for source in normal_sources[stratum]:
            claim = {key: source.get(key) for key in LEAN_CLAIM_COLUMNS}
            records.append(
                {
                    "inputs": {"claim": claim},
                    "expectations": _expectations(source, "normal", stratum),
                }
            )
    for stratum, count in narrative_counts.items():
        for index in range(count):
            records.append(_narrative_record(narrative_sources[index], stratum, index))
    validate_heldout(records, normal_counts, narrative_counts)
    metadata = {
        "record_count": len(records),
        "group_counts": dict(sorted(Counter(r["expectations"]["group"] for r in records).items())),
        "stratification": dict(
            sorted(Counter(r["expectations"]["stratum"] for r in records).items())
        ),
        "normal_counts": normal_counts,
        "narrative_counts": narrative_counts,
        "policy": POLICY,
        "destination": HELDOUT_TABLE,
        "labels_location": "expectations_only",
    }
    return records, metadata


def build_heldout_records(
    reference_rows: Iterable[dict],
    n: int = 100,
    normal_counts: dict[str, int] | None = None,
    narrative_counts: dict[str, int] | None = None,
) -> tuple[list[dict], dict]:
    normal_counts = dict(normal_counts or DEFAULT_NORMAL_COUNTS)
    narrative_counts = dict(narrative_counts or DEFAULT_NARRATIVE_COUNTS)
    if sum(normal_counts.values()) + sum(narrative_counts.values()) != n:
        raise ValueError("configured stratum counts must sum to n")
    selected, narrative_sources = _select_sources(reference_rows, normal_counts, narrative_counts)
    return _build_from_selected(selected, narrative_sources, normal_counts, narrative_counts)


def validate_heldout(
    records: list[dict], normal_counts: dict[str, int], narrative_counts: dict[str, int]
) -> None:
    expected_counts = {**normal_counts, **narrative_counts}
    if len(records) != sum(expected_counts.values()):
        raise ValueError("held-out record count does not match configured strata")
    counts = Counter()
    groups = Counter()
    for record in records:
        claim = record.get("inputs", {}).get("claim", {})
        leaked = (LABEL_KEYS | {"disposition_class", "approval_subchoice"}) & claim.keys()
        if leaked:
            raise ValueError(f"held-out label leakage in inputs.claim: {sorted(leaked)}")
        if set(claim) != set(LEAN_CLAIM_COLUMNS):
            raise ValueError("held-out inputs must be projected to LEAN_CLAIM_COLUMNS")
        expected = record["expectations"]
        if expected.get("group") not in {"normal", "narrative"} or not expected.get("stratum"):
            raise ValueError("each held-out row requires group and stratum")
        if expected["stratum"] in claim["claim_id"]:
            raise ValueError("held-out claim ids must be opaque")
        counts[expected["stratum"]] += 1
        groups[expected["group"]] += 1
    if dict(counts) != expected_counts:
        raise ValueError(f"invalid held-out stratification: {dict(counts)}")
    if groups != Counter(
        normal=sum(normal_counts.values()), narrative=sum(narrative_counts.values())
    ):
        raise ValueError(f"invalid held-out group mix: {dict(groups)}")


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid amount {value!r}") from exc


def _validate_gate_result(record: dict, structured: dict) -> None:
    expected = record["expectations"]
    verdict = "PEND" if structured["verdict"] == "PEND_INVESTIGATE" else structured["verdict"]
    if expected["group"] == "narrative":
        if verdict == expected["verdict"]:
            raise ValueError("narrative row does not disagree with structured rules")
        return
    if expected["stratum"] == "fraud_ring":
        return
    actual = (verdict, structured["disposition"], _money(structured["approved_amount"]))
    gold = (
        expected["verdict"],
        expected["disposition"],
        _money(expected["approved_amount"]),
    )
    if actual != gold:
        raise ValueError(f"normal row does not agree with structured rules: {actual} != {gold}")


def _structured_outcome(runtime, connection, claim: dict) -> dict:
    from decision_record import deterministic_outcome
    from duplicate import check_duplicate_claim

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
    return deterministic_outcome(claim["claim_type"], conformance, coverage, settlement, duplicate)


def _select_live_sources(
    rows: list[dict],
    profile: str,
    normal_counts: dict[str, int],
    narrative_counts: dict[str, int],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Fill quotas only with rows that pass the unchanged live structured gate."""
    if str(AGENT_SRC) not in sys.path:
        sys.path.insert(0, str(AGENT_SRC))
    from authorities_runtime import AuthorityRuntime
    from db import connect

    selected = {stratum: [] for stratum in normal_counts}
    narrative_sources = []
    narrative_needed = max(narrative_counts.values(), default=0)
    with connect(profile=profile, autocommit=True) as connection:
        runtime = AuthorityRuntime(connection)
        for stratum, count in normal_counts.items():
            for row in _stable(item for item in rows if _normal_stratum(item) == stratum):
                record = {
                    "inputs": {"claim": {key: row.get(key) for key in LEAN_CLAIM_COLUMNS}},
                    "expectations": _expectations(row, "normal", stratum),
                }
                try:
                    _validate_gate_result(
                        record, _structured_outcome(runtime, connection, record["inputs"]["claim"])
                    )
                except ValueError:
                    continue
                selected[stratum].append(row)
                if len(selected[stratum]) == count:
                    break
            if len(selected[stratum]) != count:
                raise ValueError(
                    f"insufficient live-gate-valid rows for {stratum}: "
                    f"{len(selected[stratum])} < {count}"
                )
        candidates = _stable(
            row
            for row in rows
            if row.get("claim_type") == "coating_warranty"
            and row.get("gold_verdict") == "APPROVE"
            and row.get("gold_disposition") in {"CREDIT", "REWORK", "REPLACEMENT"}
        )
        for source in candidates:
            index = len(narrative_sources)
            try:
                for stratum in narrative_counts:
                    record = _narrative_record(source, stratum, index)
                    _validate_gate_result(
                        record, _structured_outcome(runtime, connection, record["inputs"]["claim"])
                    )
            except ValueError:
                continue
            narrative_sources.append(source)
            if len(narrative_sources) == narrative_needed:
                break
    if len(narrative_sources) != narrative_needed:
        raise ValueError(
            f"insufficient live-gate-valid narrative sources: "
            f"{len(narrative_sources)} < {narrative_needed}"
        )
    return selected, narrative_sources


def _validate_live_narrative_dependence(records: list[dict], profile: str) -> None:
    """Run frozen structured authorities and enforce the mixed-set validity gate."""
    if str(AGENT_SRC) not in sys.path:
        sys.path.insert(0, str(AGENT_SRC))
    from authorities_runtime import AuthorityRuntime
    from db import connect

    with connect(profile=profile, autocommit=True) as connection:
        runtime = AuthorityRuntime(connection)
        for record in records:
            claim = record["inputs"]["claim"]
            structured = _structured_outcome(runtime, connection, claim)
            try:
                _validate_gate_result(record, structured)
            except ValueError as exc:
                raise ValueError(
                    f"mixed held-out gate failed for {claim['claim_id']} "
                    f"({record['expectations']['stratum']}): {exc}"
                ) from exc


def create_live(
    profile: str,
    warehouse_id: str,
    experiment_id: str,
    n: int = 100,
    normal_counts: dict[str, int] | None = None,
    narrative_counts: dict[str, int] | None = None,
) -> tuple[Any, dict]:
    """Create only the isolated mixed UC evaluation dataset; never write Lakebase."""
    normal_counts = dict(normal_counts or DEFAULT_NORMAL_COUNTS)
    narrative_counts = dict(narrative_counts or DEFAULT_NARRATIVE_COUNTS)
    rows = _execute_sql(profile, warehouse_id, source_sql())
    selected, narrative_sources = _select_live_sources(
        rows, profile, normal_counts, narrative_counts
    )
    source_by_id = {
        str(row["claim_id"]): row
        for row in [item for values in selected.values() for item in values] + narrative_sources
    }
    source_values = list(source_by_id.values())
    oracles = ResolverOracle(profile).resolve_rows(source_values)
    for row, oracle in zip(source_values, oracles):
        row["_oracle_clause_ids"] = oracle["oracle_clause_ids"]
    records, metadata = _build_from_selected(
        selected, narrative_sources, normal_counts, narrative_counts
    )
    if len(records) != n:
        raise ValueError(f"configured mixed set produced {len(records)} rows, expected {n}")
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


def _counts(value: str) -> dict[str, int]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(count, int) and count >= 0
        for key, count in parsed.items()
    ):
        raise argparse.ArgumentTypeError("counts must be a JSON object of non-negative integers")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--warehouse-id")
    parser.add_argument("--experiment", default="/Shared/claims-adjudication-ablation")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--normal-counts", type=_counts, default=DEFAULT_NORMAL_COUNTS)
    parser.add_argument("--narrative-counts", type=_counts, default=DEFAULT_NARRATIVE_COUNTS)
    args = parser.parse_args()
    if args.create:
        if not args.profile or not args.warehouse_id:
            parser.error("--create requires explicit --profile and --warehouse-id")
        mlflow.set_tracking_uri("databricks")
        experiment = mlflow.set_experiment(args.experiment)
        _, metadata = create_live(
            args.profile,
            args.warehouse_id,
            experiment.experiment_id,
            n=args.n,
            normal_counts=args.normal_counts,
            narrative_counts=args.narrative_counts,
        )
        print(json.dumps(metadata, indent=2))
    elif args.describe:
        print(
            json.dumps(
                {
                    "destination": HELDOUT_TABLE,
                    "normal_counts": args.normal_counts,
                    "narrative_counts": args.narrative_counts,
                },
                indent=2,
            )
        )
    else:
        parser.error("choose --describe or --create")


if __name__ == "__main__":
    main()
