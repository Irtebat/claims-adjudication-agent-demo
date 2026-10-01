import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from authorities import compute_coverage
from decision_record import deterministic_outcome

import heldout
from build_dataset import LABEL_KEYS, LEAN_CLAIM_COLUMNS
from heldout import HELDOUT_TABLE, NARRATIVES, POLICY, SCENARIO_COUNTS, build_heldout_records

POLICY_SOURCE = Path(__file__).resolve().parents[2] / "lakebase" / "src" / "policy_source.json"


def _reference_rows(count=25):
    rows = []
    for block in range(count):
        material = block * 100 + 1
        row = {key: None for key in LEAN_CLAIM_COLUMNS}
        row.update(
            claim_id=f"CLM-{material:07d}",
            coil_id=f"COIL-{material:07d}",
            customer_id=f"CUST-{(material * 17) % 197:04d}",
            claim_type="coating_warranty",
            claim_date="2026-01-15",
            install_date="2018-02-01",
            environment="inland",
            installation="ventilated",
            coast_distance_km="25.0",
            defect_code="RED_RUST",
            defect_narrative="Premature red rust observed on installed roofing.",
            claimed_tonnage="10.0",
            claimed_freight="0.0",
            gold_verdict="APPROVE",
            gold_disposition="CREDIT",
        )
        rows.append(row)
    return rows


def test_exact_stratification_opaque_ids_projection_policy_and_no_leakage():
    records, metadata = build_heldout_records(_reference_rows())
    assert len(records) == 100
    assert metadata["stratification"] == SCENARIO_COUNTS
    assert metadata["destination"] == HELDOUT_TABLE
    assert len({row["inputs"]["claim"]["claim_id"] for row in records}) == 100
    for record in records:
        claim = record["inputs"]["claim"]
        expected = record["expectations"]
        assert set(claim) == set(LEAN_CLAIM_COLUMNS)
        assert not LABEL_KEYS.intersection(claim)
        assert expected["scenario_type"] not in claim["claim_id"]
        assert expected["policy_clause_id"]
        assert expected["policy_section_ref"]


def test_policy_references_exist_and_narratives_are_varied_facts():
    policy = json.loads(POLICY_SOURCE.read_text())["warranties"]["coverage"]
    assert "marine" in policy["excluded_environments"]
    assert "standing_water" in policy["excluded_installations"]
    assert policy["min_coast_distance_km"] == 2.0
    assert all(len(phrases) >= 4 for phrases in NARRATIVES.values())
    assert {value["section_ref"] for value in POLICY.values()} >= {
        "warranties.coverage.excluded_environments",
        "warranties.coverage.excluded_installations",
        "warranties.coverage.min_coast_distance_km",
    }


def test_current_structured_authority_differs_for_treatments_and_matches_controls():
    """Current authority ignores narrative: neutral fields approve all source-valid rows."""
    records, _ = build_heldout_records(_reference_rows())
    terms = {
        "duration_months": 360,
        "full_coverage_months": 120,
        "excluded_environments": ["marine", "industrial_aggressive"],
        "excluded_installations": ["unventilated", "standing_water"],
        "min_coast_distance_km": 2.0,
    }
    for record in records:
        claim = {**record["inputs"]["claim"], "ship_date": "2018-01-01"}
        coverage = compute_coverage(terms, claim)
        structured = deterministic_outcome(
            "coating_warranty",
            {"conforms": True},
            coverage,
            {"approved_amount": 100.0},
            {"is_duplicate": False},
        )
        assert structured["verdict"] == "APPROVE"
        expected = record["expectations"]
        if expected["scenario_type"] == "neutral_control":
            assert expected["verdict"] == structured["verdict"]
        else:
            assert expected["verdict"] != structured["verdict"]


def test_control_labels_must_come_from_source():
    rows = _reference_rows()
    rows[0].pop("gold_verdict")
    with pytest.raises(ValueError, match="25 approved real"):
        build_heldout_records(rows)


def test_live_create_uses_named_mlflow_dataset(monkeypatch):
    rows = _reference_rows()
    monkeypatch.setattr(heldout, "_execute_sql", lambda *args: rows)
    monkeypatch.setattr(
        heldout,
        "ResolverOracle",
        lambda profile: SimpleNamespace(
            resolve_rows=lambda values: [{"oracle_clause_ids": ["coverage"]} for _ in values]
        ),
    )
    monkeypatch.setattr(heldout, "_validate_live_narrative_dependence", lambda *args: None)
    monkeypatch.setattr(
        heldout.mlflow.genai.datasets,
        "get_dataset",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("does not exist")),
    )
    created = []
    dataset = SimpleNamespace(merge_records=lambda records: None)
    monkeypatch.setattr(
        heldout.mlflow.genai.datasets,
        "create_dataset",
        lambda **kwargs: created.append(kwargs) or dataset,
    )
    result, metadata = heldout.create_live("profile", "warehouse", "experiment")
    assert result is dataset
    assert metadata["record_count"] == 100
    assert created == [{"name": HELDOUT_TABLE, "experiment_id": "experiment"}]
