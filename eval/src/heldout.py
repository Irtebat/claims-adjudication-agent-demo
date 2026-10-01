"""Build a narrative-dependent, leakage-free held-out claims dataset.

The builder is deliberately storage-agnostic.  Live callers must provide an MLflow
evaluation-dataset writer or a writer for ``fe-bar-ir.eval.heldout_claims``; this
module never writes claims, adjudications, or medallion tables.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

HELDOUT_TABLE = "fe-bar-ir.eval.heldout_claims"
SCENARIO_COUNTS = {
    "narrative_installation_misuse": 20,
    "narrative_environment_exclusion": 20,
    "narrative_defect_mode_override": 20,
    "narrative_prior_repair": 20,
    "neutral_control": 20,
}
SCENARIO_POLICY = {
    "narrative_installation_misuse": (
        "Narrative evidence of installation misuse triggers the written installation exclusion, "
        "even when the structured installation field is benign."
    ),
    "narrative_environment_exclusion": (
        "Narrative evidence of excluded exposure triggers the written environment exclusion, "
        "even when the structured environment field is benign."
    ),
    "narrative_defect_mode_override": (
        "The described physical failure mode controls classification when it contradicts the "
        "structured defect code."
    ),
    "narrative_prior_repair": (
        "A disclosed prior unsuccessful repair requires investigation rather than automatic "
        "approval under the written escalation policy."
    ),
    "neutral_control": (
        "The narrative agrees with structured fields, so the ordinary structured-field outcome "
        "is unchanged."
    ),
}
NARRATIVES = {
    "narrative_installation_misuse": (
        "Inspection found panels fastened through drainage channels contrary to installation "
        "instructions; trapped water originated at those fasteners."
    ),
    "narrative_environment_exclusion": (
        "The installed material is continuously exposed to marine salt spray at the shoreline, "
        "an excluded environment, despite the intake field stating inland."
    ),
    "narrative_defect_mode_override": (
        "Measurements are within mechanical tolerance; the observed failure is coating "
        "delamination and exposed substrate, not the recorded tensile defect."
    ),
    "narrative_prior_repair": (
        "The customer reports that an earlier authorized patch repair failed at the same location; "
        "escalation and repair-history review are required."
    ),
    "neutral_control": (
        "Inspection confirms the reported defect mode and the stated installation and environment "
        "conditions; no prior repair was attempted."
    ),
}


def _stable_rows(rows: Iterable[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: hashlib.sha256(str(row["claim_id"]).encode()).hexdigest())


def _gold(scenario: str, source: dict) -> dict:
    """Return policy-derived labels; callers place these under expectations only."""
    if scenario in {"narrative_installation_misuse", "narrative_environment_exclusion"}:
        return {"verdict": "DENY", "disposition_class": "DENY", "approval_subchoice": None}
    if scenario == "narrative_prior_repair":
        return {
            "verdict": "PEND",
            "disposition_class": "PEND",
            "approval_subchoice": "PEND_INVESTIGATE",
        }
    if scenario == "narrative_defect_mode_override":
        return {
            "verdict": "APPROVE",
            "disposition_class": "APPROVE",
            "approval_subchoice": "REWORK",
        }
    disposition = source.get("gold_disposition") or "CREDIT"
    verdict = source.get("gold_verdict") or "APPROVE"
    return {
        "verdict": verdict,
        "disposition_class": disposition_class(disposition, verdict),
        "approval_subchoice": approval_subchoice(disposition, verdict),
    }


def disposition_class(disposition: str | None, verdict: str | None = None) -> str | None:
    if disposition in {"CREDIT", "REWORK", "REPLACEMENT"}:
        return "APPROVE"
    if disposition == "PEND_INVESTIGATE":
        return "PEND"
    return disposition or verdict


def approval_subchoice(disposition: str | None, verdict: str | None = None) -> str | None:
    return (
        disposition
        if disposition_class(disposition, verdict) == "APPROVE"
        else (disposition if disposition == "PEND_INVESTIGATE" else None)
    )


def build_heldout_records(reference_rows: Iterable[dict], n: int = 100) -> tuple[list[dict], dict]:
    """Create exactly 100 records from real reference-linked claim rows.

    ``reference_rows`` must already contain valid claim/coil/customer identifiers from the
    existing data.  Rows are cloned and only the narrative plus synthetic claim id changes,
    preserving resolver-compatible reference keys.
    """
    if n != 100:
        raise ValueError("the narrative held-out study is fixed at exactly 100 claims")
    rows = _stable_rows(reference_rows)
    if len(rows) < max(SCENARIO_COUNTS.values()):
        raise ValueError("at least 20 resolver-compatible reference rows are required")
    records: list[dict] = []
    for scenario, count in SCENARIO_COUNTS.items():
        for index in range(count):
            source = copy.deepcopy(rows[index % len(rows)])
            source["claim_id"] = f"HELDOUT-{scenario}-{index:03d}"
            source["defect_narrative"] = NARRATIVES[scenario]
            if scenario == "narrative_installation_misuse":
                source.update(claim_type="coating_warranty", installation="ventilated")
            elif scenario == "narrative_environment_exclusion":
                source.update(claim_type="coating_warranty", environment="inland")
            elif scenario == "narrative_defect_mode_override":
                source.update(claim_type="material_nonconformance", defect_code="MECH")
            for key in tuple(source):
                if key.startswith("gold_") or key in {"label", "expectations"}:
                    source.pop(key)
            records.append(
                {
                    "inputs": {"claim": source},
                    "expectations": {
                        **_gold(scenario, rows[index % len(rows)]),
                        "scenario_type": scenario,
                        "policy_basis": SCENARIO_POLICY[scenario],
                    },
                }
            )
    stratification = Counter(r["expectations"]["scenario_type"] for r in records)
    metadata = {
        "record_count": len(records),
        "stratification": dict(sorted(stratification.items())),
        "scenario_policy": SCENARIO_POLICY,
        "destination": HELDOUT_TABLE,
        "labels_location": "expectations_only",
    }
    validate_heldout(records)
    return records, metadata


def validate_heldout(records: list[dict]) -> None:
    if len(records) != 100:
        raise ValueError(f"held-out set must contain 100 claims, got {len(records)}")
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        claim = record.get("inputs", {}).get("claim", {})
        leaked = {
            "verdict",
            "disposition",
            "disposition_class",
            "approval_subchoice",
        } & claim.keys()
        if leaked:
            raise ValueError(f"held-out label leakage in inputs.claim: {sorted(leaked)}")
        counts[record["expectations"]["scenario_type"]] += 1
    if dict(counts) != SCENARIO_COUNTS:
        raise ValueError(f"invalid held-out stratification: {dict(counts)}")


def persist(records: list[dict], writer: Callable[[str, list[dict]], Any]) -> Any:
    """Persist only to the isolated eval destination via an explicitly supplied writer."""
    validate_heldout(records)
    return writer(HELDOUT_TABLE, records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true", help="print design; perform no writes")
    args = parser.parse_args()
    if not args.describe:
        parser.error("live creation requires an explicit application-owned writer; use --describe")
    print(json.dumps({"destination": HELDOUT_TABLE, "stratification": SCENARIO_COUNTS}, indent=2))


if __name__ == "__main__":
    main()
