from pathlib import Path

import yaml


def test_bootstrap_replaces_deterministic_fixture_state():
    bundle = yaml.safe_load((Path(__file__).parents[1] / "databricks.yml").read_text())
    tasks = bundle["resources"]["jobs"]["bootstrap"]["tasks"]
    medallion = next(task for task in tasks if task["task_key"] == "medallion")

    assert medallion["pipeline_task"]["full_refresh"] is True

    seed_source = (Path(__file__).parents[2] / "lakebase" / "src" / "setup_and_seed.py").read_text()
    prior_delete = seed_source.index('"DELETE FROM prior_claims WHERE data_provenance = %s"')
    adjudication_load = seed_source.index('upsert_sql("adjudications"')
    prior_load = seed_source.index("INSERT INTO prior_claims")

    assert prior_delete < adjudication_load < prior_load
    assert "WHERE a.decision_status = 'FINAL'" in seed_source
