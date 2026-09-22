import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from policies import policy_rows


def test_parameter_and_clause_come_from_same_source(tmp_path):
    source = json.loads(
        (Path(__file__).resolve().parents[1] / "src/policy_source.json").read_text()
    )
    changed = copy.deepcopy(source)
    changed["standards"][0]["mechanical"]["tensile_mpa"]["max"] = 512.5
    changed["warranty_versions"][0]["duration_months"] = 252
    path = tmp_path / "source.json"
    path.write_text(json.dumps(changed))
    specs, warranties = policy_rows(path)
    spec = next(
        r for r in specs if r["grade"] == "ASTM A653 CS Type B" and r["section_ref"] == "mechanical"
    )
    warranty = next(
        r for r in warranties if r["warranty_id"].endswith("V1") and r["section_ref"] == "coverage"
    )
    assert spec["structured_params"]["mechanical"]["tensile_mpa"]["max"] == 512.5
    assert "512.5" in spec["clause_text"]
    assert warranty["structured_params"]["duration_months"] == 252
    assert "252 months" in warranty["clause_text"]


def test_policy_metadata_ranges_and_clause_identity():
    specs, warranties = policy_rows()
    assert len({r["clause_id"] for r in specs + warranties}) == len(specs + warranties)
    for row in specs:
        assert "product_line" not in row
        assert all(
            row[k] for k in ("grade", "spec_edition", "region", "clause_text", "source_sha256")
        )
        for group in ("chemistry", "mechanical"):
            assert all(v["min"] <= v["max"] for v in row["structured_params"][group].values())
    for row in warranties:
        assert all(
            row[k]
            for k in ("product_line", "coating_class", "region", "effective_from", "effective_to")
        )
        assert row["effective_from"] < row["effective_to"]
        params = row["structured_params"]
        assert params["duration_months"] > params["full_coverage_months"] >= 0
        assert params["min_coating_g_m2"] > 0
        assert "marine" in params["excluded_environments"]
    assert all(
        not any("embedding" in k or "vector" in k for k in row) for row in specs + warranties
    )


def test_effective_windows_unique_at_sale_boundary():
    _, warranties = policy_rows()
    versions = [
        r
        for r in warranties
        if r["section_ref"] == "coverage"
        and r["product_line"] == "galvanized"
        and r["region"] == "NA"
    ]
    for ship, version in [("2014-12-31", "V1"), ("2015-01-01", "V2")]:
        matching = [r for r in versions if r["effective_from"] <= ship < r["effective_to"]]
        assert len(matching) == 1
        assert matching[0]["warranty_id"].endswith(version)
