from pathlib import Path

import yaml


def test_bootstrap_orders_seed_before_cdf_and_never_full_refreshes_scd2():
    bundle = yaml.safe_load((Path(__file__).parents[1] / "databricks.yml").read_text())
    jobs = bundle["resources"]["jobs"]
    assert jobs["generate_raw"]["tasks"][0]["task_key"] == "generate"
    medallion = jobs["process_cdf"]["tasks"][0]
    assert medallion["task_key"] == "medallion"
    assert "full_refresh" not in medallion["pipeline_task"]

    seed_source = (Path(__file__).parents[2] / "lakebase" / "src" / "setup_and_seed.py").read_text()
    prior_delete = seed_source.index('"DELETE FROM prior_claims WHERE data_provenance = %s"')
    adjudication_load = seed_source.index('upsert_sql("adjudications"')
    prior_load = seed_source.index("INSERT INTO prior_claims")

    assert prior_delete < adjudication_load < prior_load
    assert "WHERE a.decision_status = 'FINAL'" in seed_source
    assert "/bronze/raw_landing/claims_history" in seed_source
    assert "/bronze/raw_landing/adjudications_history" in seed_source
    assert ".gold.claims_history" not in seed_source
    assert ".gold.adjudications_history" not in seed_source

    runner = (Path(__file__).parents[1] / "run.py").read_text()
    generate = runner.index('"bundle", "run", "generate_raw"')
    seed = runner.index('"bundle", "run", "setup_and_seed"')
    create_cdf = runner.index('"create-cdf-config"')
    wait_for_streaming = runner.index('states == {"STREAMING"}')
    process = runner.index('"bundle", "run", "process_cdf"')
    assert generate < seed < create_cdf < wait_for_streaming < process
    assert "Refusing to reseed Lakebase" in runner


def test_history_uses_native_cdf_auto_cdc_scd2():
    transforms = Path(__file__).parents[1] / "src" / "transformations"
    metadata = ["_pg_change_type", "_pg_lsn", "_pg_xid", "_timestamp", "_sort_by"]
    for entity, key in (("claims", "claim_id"), ("adjudications", "adjudication_id")):
        source = (transforms / f"silver_{entity}_history.py").read_text()
        assert "dp.create_streaming_table(" in source
        assert "dp.create_auto_cdc_flow(" in source
        assert f'keys=["{key}"]' in source
        assert 'sequence_by=F.col("_sort_by")' in source
        assert "_pg_change_type = 'delete'" in source
        assert '!= "update_preimage"' in source
        assert "stored_as_scd_type=2" in source
        assert "identifier.replace('`', '``')" in source
        for column in metadata:
            assert f'"{column}"' in source

    views = (transforms / "gold_history_views.sql").read_text()
    assert "gold.claims_current" in views
    assert "gold.adjudications_current" in views
    assert views.count("WHERE __END_AT IS NULL") == 2
