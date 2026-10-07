import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parents[2]


def test_bootstrap_orders_seed_before_cdf_and_never_full_refreshes_scd2():
    bundle = yaml.safe_load((Path(__file__).parents[1] / "databricks.yml").read_text())
    jobs = bundle["resources"]["jobs"]
    assert jobs["generate_raw"]["tasks"][0]["task_key"] == "generate"
    medallion = jobs["refresh_medallion"]["tasks"][0]
    assert medallion["task_key"] == "medallion"
    assert "full_refresh" not in medallion["pipeline_task"]

    seed_source = (Path(__file__).parents[2] / "lakebase" / "src" / "setup_and_seed.py").read_text()
    adjudication_delete = seed_source.index(
        '"DELETE FROM adjudications WHERE data_provenance = %s"'
    )
    adjudication_load = seed_source.index('upsert_sql("adjudications"')
    assert adjudication_delete < adjudication_load
    # The precedent corpus is lakehouse-built and synced down; the seed no longer
    # creates, deletes, or loads a native prior_claims table.
    assert "prior_claims" not in seed_source.replace("prior_claims_corpus", "")
    # Finalization columns are part of the day-1 adjudications schema.
    create_adjudications = seed_source[
        seed_source.index("CREATE TABLE IF NOT EXISTS adjudications") :
    ].split(");")[0]
    assert "decided_by text, override_reason text" in create_adjudications
    assert "/bronze/raw_landing/claims_history" in seed_source
    assert "/bronze/raw_landing/adjudications_history" in seed_source
    assert ".gold.claims_history" not in seed_source
    assert ".gold.adjudications_history" not in seed_source

    runner = (Path(__file__).parents[1] / "run.py").read_text()
    # refresh is a normal incremental medallion run. The stale DROP MATERIALIZED VIEW
    # migration — which errored with DROP_COMMAND_TYPE_MISMATCH once the *_history
    # datasets stopped being materialized views — has been removed.
    assert '"bundle", "run", "refresh_medallion"' in runner
    assert "DROP MATERIALIZED VIEW" not in runner
    assert "lakebase_root" not in runner

    lakebase_runner = (Path(__file__).parents[2] / "lakebase" / "run.py").read_text()
    assert 'states == {"STREAMING"}' in lakebase_runner


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap", REPO / "scripts" / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_main_issues_exact_commands_in_order(monkeypatch):
    bootstrap = _load_bootstrap()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((tuple(args), kwargs))

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    bootstrap.main()

    wrapper = ("uv", "run", "--with", "pyyaml", "python")
    target = ("--target", "prod", "--profile", "fe-bar-ir-2026")
    root = {"cwd": REPO, "check": True}
    agent = {"cwd": REPO / "agent", "check": True}
    assert calls == [
        (("databricks", "bundle", "deploy", *target), {"cwd": REPO / "pipelines", "check": True}),
        ((*wrapper, "pipelines/run.py", "generate"), root),
        ((*wrapper, "lakebase/run.py", "setup-and-seed"), root),
        ((*wrapper, "lakebase/run.py", "create-cdf"), root),
        (("databricks", "bundle", "deploy", *target), agent),
        # First fraud_graph precedes the first medallion run: it publishes the empty
        # typed gold.customer_heat_risk the gold fact joins on a fresh workspace.
        (("databricks", "bundle", "run", "fraud_graph", *target), agent),
        ((*wrapper, "pipelines/run.py", "refresh", "--allow-missing-decision-records"), root),
        (("databricks", "bundle", "run", "fraud_graph", *target), agent),
        (("databricks", "bundle", "run", "prior_claims_corpus", *target), agent),
        ((*wrapper, "pipelines/run.py", "refresh", "--allow-missing-decision-records"), root),
    ]


def test_fraud_graph_bootstraps_an_empty_risk_table_before_first_medallion():
    job = (REPO / "agent" / "src" / "fraud_graph_job.py").read_text()
    guard = job.index('if not _table_exists("gold", "claims_current"):')
    # The empty, typed table is published and the job exits BEFORE any claims read.
    assert guard < job.index('spark.table(f"`{catalog}`.gold.claims_current")')
    branch = job[guard : job.index("heat_map = ")]
    assert "spark.createDataFrame([], schema)" in branch
    assert "dbutils.notebook.exit" in branch
    # It never overwrites an existing risk table on that path.
    assert 'if not _table_exists("gold", "customer_heat_risk"):' in branch


def test_bootstrap_main_stops_at_first_failing_step(monkeypatch):
    bootstrap = _load_bootstrap()
    calls = []

    def fake_run(args, **kwargs):
        calls.append(tuple(args))
        if args[-1] == "setup-and-seed":
            raise bootstrap.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)
    with pytest.raises(bootstrap.subprocess.CalledProcessError):
        bootstrap.main()

    assert [call[-1] for call in calls] == ["fe-bar-ir-2026", "generate", "setup-and-seed"]


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
