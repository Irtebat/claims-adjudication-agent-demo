"""The single authored source parses into aligned params + citable clauses."""

import copy
import json
from pathlib import Path

from policy_schema import SOURCE_PATH, parse_policies


def test_params_and_clauses_share_one_source(tmp_path):
    source = json.loads(SOURCE_PATH.read_text())
    changed = copy.deepcopy(source)
    changed["specs"]["entries"][0]["mechanical"]["tensile_mpa"]["max"] = 512.5
    changed["warranties"]["versions"][0]["duration_months"] = 252
    path = tmp_path / "source.json"
    path.write_text(json.dumps(changed))
    parsed = parse_policies(path)

    spec = next(r for r in parsed["spec_params"] if r["grade"] == "ASTM A653 CS Type B")
    assert spec["tensile_mpa_max"] == 512.5
    clause = next(
        c
        for c in parsed["spec_clauses"]
        if c["grade"] == spec["grade"]
        and c["region"] == spec["region"]
        and c["section_ref"] == "mechanical"
    )
    assert "512.5" in clause["clause_text"]

    v1 = next(r for r in parsed["warranty_terms"] if r["version"] == "V1")
    assert v1["duration_months"] == 252
    coverage_clause = next(
        c
        for c in parsed["warranty_clauses"]
        if c["product_line"] == v1["product_line"]
        and c["region"] == v1["region"]
        and c["version"] == v1["version"]
        and c["section_ref"] == "coverage"
    )
    assert "252 months" in coverage_clause["clause_text"]


def test_clause_identity_and_metadata():
    parsed = parse_policies()
    clauses = parsed["spec_clauses"] + parsed["warranty_clauses"]
    keys = [
        (
            c.get("grade", c.get("product_line")),
            c["region"],
            c.get("spec_edition", c.get("version")),
            c["section_ref"],
        )
        for c in clauses
    ]
    assert len(set(keys)) == len(clauses)

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
        assert row["freight_cap"] == 500.0


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


def test_live_claim_schema_has_no_ground_truth_label():
    ddl = (Path(__file__).parents[2] / "lakebase/src/setup_and_seed.py").read_text()
    claims_ddl = ddl.split("CREATE TABLE IF NOT EXISTS claims (", 1)[1].split(");", 1)[0]
    assert "ground_truth_label" not in claims_ddl
