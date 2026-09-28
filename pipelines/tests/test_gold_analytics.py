from pathlib import Path

import yaml

from src.checks import gold_analytics_queries

ROOT = Path(__file__).resolve().parents[1]


def test_gold_analytics_defines_fact_and_six_aggregate_materialized_views():
    sql = (ROOT / "src/transformations/gold_analytics.sql").read_text()
    names = [
        "gold_claim_adjudication_fact",
        "gold_quality_kpis",
        "gold_failure_mode_analytics",
        "gold_supplier_recovery_analytics",
        "gold_fraud_cluster_analytics",
        "gold_agent_human_alignment",
        "gold_retrieval_citation_kpis",
    ]
    assert sql.count("CREATE OR REFRESH MATERIALIZED VIEW") == len(names)
    for name in names:
        assert f".gold.{name}" in sql
    assert "row_number() OVER (PARTITION BY customer_id" in sql
    assert "row_number() OVER (PARTITION BY defect_code" in sql
    assert "row_number() OVER (PARTITION BY supplier_id" in sql
    assert "row_number() OVER (PARTITION BY coil_id" in sql
    assert "settlement" not in sql.lower()
    assert "investigation_cases" not in sql


def test_metric_view_is_bundle_managed_and_avoids_windows():
    sql = (ROOT / "src/metric_views/quality_claims_metrics.sql").read_text()
    assert "WITH METRICS" in sql
    assert "version: 1.1" in sql
    assert "window:" not in sql
    assert "In Spec Denial Amount" in sql
    assert "Warranty Exclusion Denial Amount" in sql
    assert "Duplicate Blocked Amount" in sql
    assert "Over Claim Reduction Amount" in sql

    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text())
    task = bundle["resources"]["jobs"]["deploy_metric_views"]["tasks"][0]
    assert task["sql_task"]["file"]["path"].endswith("quality_claims_metrics.sql")


def test_gold_analytics_dq_covers_grain_keys_and_amount_reconciliation():
    checks = gold_analytics_queries("`fe-bar-ir`")
    assert set(checks) == {
        "fact_grain_mismatch",
        "duplicate_fact_adjudication",
        "null_fact_grain_key",
        "approved_amount_reconciliation",
        "quality_approved_amount_reconciliation",
    }
