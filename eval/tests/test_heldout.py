import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

import heldout
from build_dataset import LABEL_KEYS, LEAN_CLAIM_COLUMNS
from heldout import (
    DEFAULT_NARRATIVE_COUNTS,
    DEFAULT_NORMAL_COUNTS,
    HELDOUT_TABLE,
    NARRATIVES,
    POLICY,
    build_heldout_records,
)

POLICY_SOURCE = Path(__file__).resolve().parents[2] / "lakebase" / "src" / "policy_source.json"


def _reference_rows():
    rows = []
    specs = (
        ("R7_credit", 15, "APPROVE", "CREDIT", "clean", "coating_warranty"),
        (
            "R5_supplier_attributable",
            10,
            "APPROVE",
            "REPLACEMENT",
            "supplier coating",
            "material_nonconformance",
        ),
        ("R6_over_claim_partial", 10, "APPROVE", "REWORK", "over_claim", "coating_warranty"),
        ("R4_ineligible", 15, "DENY", "DENY", "out_of_warranty", "coating_warranty"),
        ("R1_duplicate", 10, "DENY", "DUPLICATE", "duplicate", "coating_warranty"),
        ("fraud_ring", 10, "PEND", "PEND_INVESTIGATE", "fraud ring", "coating_warranty"),
    )
    material = 0
    for _, count, verdict, disposition, provenance, claim_type in specs:
        for _ in range(count):
            material += 1
            row = {key: None for key in LEAN_CLAIM_COLUMNS}
            row.update(
                claim_id=f"CLM-{material:07d}",
                coil_id=f"COIL-{material:07d}",
                customer_id=f"CUST-{material % 197:04d}",
                claim_type=claim_type,
                claim_date="2026-01-15",
                install_date="2018-02-01",
                environment="inland",
                installation="ventilated",
                coast_distance_km="25.0",
                defect_code="RED_RUST",
                defect_narrative="The inspection narrative agrees with the structured intake.",
                claimed_tonnage="10.0",
                claimed_freight="0.0",
                gold_verdict=verdict,
                gold_disposition=disposition,
                gold_approved_amount="100.00" if verdict == "APPROVE" else "0.00",
                gold_cited_clause_ids='["/structured/rule"]',
                label_provenance=provenance,
                duplicate_of_claim_id=(
                    f"CLM-{material - 1:07d}" if disposition == "DUPLICATE" else None
                ),
            )
            rows.append(row)
    return rows


def test_default_mix_proportions_strata_projection_and_opaque_ids():
    records, metadata = build_heldout_records(_reference_rows())

    assert len(records) == 100
    assert metadata["group_counts"] == {"narrative": 30, "normal": 70}
    assert metadata["stratification"] == {
        **DEFAULT_NORMAL_COUNTS,
        **DEFAULT_NARRATIVE_COUNTS,
    }
    assert metadata["destination"] == HELDOUT_TABLE
    assert len({row["inputs"]["claim"]["claim_id"] for row in records}) == 100
    for record in records:
        claim = record["inputs"]["claim"]
        expected = record["expectations"]
        assert set(claim) == set(LEAN_CLAIM_COLUMNS)
        assert not LABEL_KEYS.intersection(claim)
        assert expected["group"] in {"normal", "narrative"}
        assert expected["stratum"] not in claim["claim_id"]


def test_custom_mix_is_configurable():
    normal = {"R7_credit": 2, "fraud_ring": 1}
    narrative = {"excluded_environment": 2}
    records, metadata = build_heldout_records(
        _reference_rows(), n=5, normal_counts=normal, narrative_counts=narrative
    )
    assert Counter(row["expectations"]["group"] for row in records) == {
        "normal": 3,
        "narrative": 2,
    }
    assert metadata["stratification"] == {**normal, **narrative}


def test_policy_references_exist_and_narratives_are_varied():
    policy = json.loads(POLICY_SOURCE.read_text())["warranties"]["coverage"]
    assert "marine" in policy["excluded_environments"]
    assert "standing_water" in policy["excluded_installations"]
    assert policy["min_coast_distance_km"] == 2.0
    assert all(len(phrases) >= 4 for phrases in NARRATIVES.values())
    assert set(POLICY) == set(DEFAULT_NARRATIVE_COUNTS)


def test_gate_requires_normal_agreement_and_narrative_disagreement_but_skips_fraud():
    normal = {
        "expectations": {
            "group": "normal",
            "stratum": "R7_credit",
            "verdict": "APPROVE",
            "disposition": "CREDIT",
            "approved_amount": "100.00",
        }
    }
    heldout._validate_gate_result(
        normal, {"verdict": "APPROVE", "disposition": "CREDIT", "approved_amount": 100}
    )
    with pytest.raises(ValueError, match="does not agree"):
        heldout._validate_gate_result(
            normal, {"verdict": "DENY", "disposition": "DENY", "approved_amount": 0}
        )

    narrative = {
        "expectations": {
            "group": "narrative",
            "stratum": "excluded_environment",
            "verdict": "DENY",
        }
    }
    heldout._validate_gate_result(
        narrative, {"verdict": "APPROVE", "disposition": "CREDIT", "approved_amount": 100}
    )
    with pytest.raises(ValueError, match="does not disagree"):
        heldout._validate_gate_result(
            narrative, {"verdict": "DENY", "disposition": "DENY", "approved_amount": 0}
        )

    fraud = {"expectations": {"group": "normal", "stratum": "fraud_ring"}}
    heldout._validate_gate_result(
        fraud, {"verdict": "APPROVE", "disposition": "CREDIT", "approved_amount": 100}
    )


def test_duplicate_rows_keep_resolvable_history_identity():
    records, _ = build_heldout_records(_reference_rows())
    duplicates = [row for row in records if row["expectations"]["stratum"] == "R1_duplicate"]
    assert all(row["inputs"]["claim"]["claim_id"].startswith("CLM-") for row in duplicates)


def test_live_create_uses_new_named_mlflow_dataset(monkeypatch):
    rows = _reference_rows()
    monkeypatch.setattr(heldout, "_execute_sql", lambda *args: rows)
    monkeypatch.setattr(
        heldout,
        "ResolverOracle",
        lambda profile: SimpleNamespace(
            resolve_rows=lambda values: [
                {"oracle_clause_ids": ["/structured/rule"]} for _ in values
            ]
        ),
    )
    monkeypatch.setattr(heldout, "_validate_live_narrative_dependence", lambda *args: None)
    monkeypatch.setattr(
        heldout.mlflow.genai.datasets,
        "get_dataset",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("does not exist")),
    )
    created = []
    merged = []
    dataset = SimpleNamespace(merge_records=lambda records: merged.extend(records))
    monkeypatch.setattr(
        heldout.mlflow.genai.datasets,
        "create_dataset",
        lambda **kwargs: created.append(kwargs) or dataset,
    )

    result, metadata = heldout.create_live("profile", "warehouse", "experiment")

    assert result is dataset
    assert metadata["record_count"] == 100
    assert len(merged) == 100
    assert created == [{"name": HELDOUT_TABLE, "experiment_id": "experiment"}]
