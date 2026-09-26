from pathlib import Path

import yaml


def test_bootstrap_orders_seed_before_cdf_and_never_full_refreshes_scd2():
    bundle = yaml.safe_load((Path(__file__).parents[1] / "databricks.yml").read_text())
    jobs = bundle["resources"]["jobs"]
    assert jobs["generate_raw"]["tasks"][0]["task_key"] == "generate"
    medallion = jobs["refresh_medallion"]["tasks"][0]
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

    composer = (Path(__file__).parents[2] / "scripts" / "bootstrap.py").read_text()
    generate = composer.index('"pipelines/run.py", "generate"')
    seed = composer.index('"lakebase/run.py", "setup-and-seed"')
    create_cdf = composer.index('"lakebase/run.py", "create-cdf"')
    refresh = composer.index('"pipelines/run.py", "refresh"')
    assert generate < seed < create_cdf < refresh

    runner = (Path(__file__).parents[1] / "run.py").read_text()
    process = runner.index('"bundle", "run", "refresh_medallion"')
    drop_old = runner.index('"DROP MATERIALIZED VIEW IF EXISTS')
    assert drop_old < process
    assert "lakebase_root" not in runner

    lakebase_runner = (Path(__file__).parents[2] / "lakebase" / "run.py").read_text()
    assert 'states == {"STREAMING"}' in lakebase_runner


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


def test_heats_coils_reconciles_snapshots_by_natural_key():
    transforms = Path(__file__).parents[1] / "src" / "transformations"
    source = (transforms / "silver_heats_coils.py").read_text()

    assert "dp.create_streaming_table(" in source
    assert "dp.create_auto_cdc_flow(" in source
    assert 'keys=["coil_id"]' in source
    assert 'sequence_by=F.struct("_source_modified_at", "_source_file")' in source
    assert "stored_as_scd_type=1" in source
    assert '@dp.temporary_view(name="heats_coils_changes")' in source
    assert 'source="heats_coils_changes"' in source
    assert 'except_column_list=["_source_modified_at", "_source_file"]' in source

    checks = (transforms.parent / "checks.py").read_text()
    assert '"duplicate_coil_id"' in checks
    assert "HAVING count(*) > 1" in checks
    assert '"multiple_heats_per_coil"' in checks
    assert "HAVING count(DISTINCT heat_no) > 1" in checks


def test_adjudication_cdf_restores_prior_silver_types():
    transforms = Path(__file__).parents[1] / "src" / "transformations"
    source = (transforms / "silver_adjudications_history.py").read_text()

    assert 'F.lit(None).cast("array<string>")' in source
    assert "F.from_json(" in source
    assert '"array<string>"' in source
    assert 'F.lit("[")' in source and 'F.lit("]")' in source
    assert '.withColumn("finalized_at", F.col("finalized_at").cast("timestamp"))' in source
    assert '.drop("data_provenance", "baseline_loaded_at")' in source
