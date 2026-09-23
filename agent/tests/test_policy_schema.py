"""The single authored source parses into aligned params + citable clauses."""

import copy
import json
from pathlib import Path

from policy_schema import SOURCE_PATH, parse_policies


def test_params_and_clauses_share_one_source(tmp_path):
    source = json.loads(SOURCE_PATH.read_text())
    changed = copy.deepcopy(source)
    changed["standards"][0]["mechanical"]["tensile_mpa"]["max"] = 512.5
    changed["warranty_versions"][0]["duration_months"] = 252
    path = tmp_path / "source.json"
    path.write_text(json.dumps(changed))
    parsed = parse_policies(path)

    spec = next(r for r in parsed["spec_params"] if r["grade"] == "ASTM A653 CS Type B")
    assert spec["tensile_mpa_max"] == 512.5
    clause = next(
        c
        for c in parsed["spec_clauses"]
        if c["parent_clause_id"] == spec["spec_id"] and c["section_ref"] == "mechanical"
    )
    assert "512.5" in clause["clause_text"]

    v1 = next(r for r in parsed["warranty_terms"] if r["version"] == "V1")
    assert v1["duration_months"] == 252
    coverage_clause = next(
        c
        for c in parsed["warranty_clauses"]
        if c["parent_clause_id"] == v1["warranty_id"] and c["section_ref"] == "coverage"
    )
    assert "252 months" in coverage_clause["clause_text"]


def test_clause_identity_and_metadata():
    parsed = parse_policies()
    clauses = parsed["spec_clauses"] + parsed["warranty_clauses"]
    assert len({c["clause_id"] for c in clauses}) == len(clauses)

    # spec params: three grades x two regions; ranges ordered; specs carry no product_line
    assert len(parsed["spec_params"]) == 6
    for row in parsed["spec_params"]:
        assert "product_line" not in row
        assert row["carbon_pct_min"] <= row["carbon_pct_max"]
        assert row["tensile_mpa_min"] <= row["tensile_mpa_max"]
        assert all(row[k] for k in ("grade", "spec_edition", "region", "source_sha256"))

    # warranty terms: three products x two versions x two regions
    assert len(parsed["warranty_terms"]) == 12
    for row in parsed["warranty_terms"]:
        assert row["effective_from"] < row["effective_to"]
        assert row["duration_months"] > row["full_coverage_months"] >= 0
        assert "marine" in row["excluded_environments"]


def test_clauses_carry_numbers_identifiers_and_negation():
    parsed = parse_policies()
    warranty_exclusion = next(
        c for c in parsed["warranty_clauses"] if c["section_ref"] == "exclusions"
    )
    assert "Does not cover" in warranty_exclusion["clause_text"]  # negation retrieval needs
    spec_dims = next(c for c in parsed["spec_clauses"] if c["section_ref"] == "dimensions")
    assert "g/m" in spec_dims["clause_text"]  # numeric threshold retained
    # no embedding/vector columns are produced by the parser (added only in Lakebase)
    assert all(
        not any("embedding" in k or "vector" in k for k in row)
        for row in parsed["spec_clauses"] + parsed["warranty_clauses"]
    )


def test_source_path_exists():
    assert Path(SOURCE_PATH).is_file()
