"""Fresh-workspace behaviour of the decision-record flow and the gold fact's join.

Executes ``gold_adjudication_decision_records.py`` for real on a local Spark session,
with only ``pyspark.pipelines`` stubbed (to capture what the module registers) and the
catalog existence probe answered as "absent" / "present". With the CDF source absent,
the module must register NO streaming table or flow, yet still define the
``decision_records_for_fact`` view as an EMPTY frame; the gold fact's own
decision-record SQL (extracted from ``gold_analytics.sql``) must then run against it
and yield one row per adjudication with NULL agent columns. With the source present,
the view must expose the identical schema.

Needs pyspark and a local Java runtime:
    uv run --with pytest --with pyyaml --with pyspark==4.0.1 pytest -q pipelines/tests
"""

import importlib.util
import re
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")
from pyspark.sql import SparkSession  # noqa: E402

TRANSFORMS = Path(__file__).resolve().parents[1] / "src" / "transformations"
MODULE = TRANSFORMS / "gold_adjudication_decision_records.py"
CDF_TABLE = "fe-bar-ir.cdf.lb_adjudication_decision_records_history"


@pytest.fixture(scope="module")
def spark():
    try:
        session = (
            SparkSession.builder.master("local[1]")
            .appName("decision-records-bootstrap-test")
            .config("spark.ui.enabled", "false")
            .config("spark.sql.shuffle.partitions", "1")
            .getOrCreate()
        )
    except Exception as exc:  # pragma: no cover - no Java runtime
        pytest.skip(f"local Spark unavailable: {exc}")
    session.conf.set("claims.catalog", "fe-bar-ir")
    session.conf.set("claims.cdf_decision_records_table", CDF_TABLE)
    yield session
    session.stop()


class _Pipelines(types.ModuleType):
    """Stand-in for pyspark.pipelines that records what the module registers."""

    def __init__(self):
        super().__init__("pyspark.pipelines")
        self.views, self.streaming_tables, self.flows = {}, [], []

    def temporary_view(self, name):
        def register(fn):
            self.views[name] = fn
            return fn

        return register

    def create_streaming_table(self, **kwargs):
        self.streaming_tables.append(kwargs)

    def create_auto_cdc_flow(self, **kwargs):
        self.flows.append(kwargs)


class _Session:
    """The real session, except the information_schema probe and gold read are scripted."""

    def __init__(self, real, source_exists, gold_frame=None):
        self._real, self._exists, self._gold = real, source_exists, gold_frame
        self.probes = []
        self.read = types.SimpleNamespace(table=self._read_table)

    def sql(self, query):
        if "information_schema.tables" in query:
            self.probes.append(query)
            return self._real.sql(f"SELECT {1 if self._exists else 0} AS c")
        return self._real.sql(query)

    def _read_table(self, name):
        assert name == "`fe-bar-ir`.gold.adjudication_decision_records"
        return self._gold

    def __getattr__(self, name):
        return getattr(self._real, name)


def _execute_module(monkeypatch, session):
    dp = _Pipelines()
    monkeypatch.setitem(sys.modules, "pyspark.pipelines", dp)
    monkeypatch.setattr(pyspark, "pipelines", dp, raising=False)
    monkeypatch.setattr(SparkSession, "getActiveSession", classmethod(lambda cls: session))
    spec = importlib.util.spec_from_file_location("decision_records_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, dp


def _fact_decision_record_sql():
    """The fact's latest_decision_records CTE and every dr.* output expression, verbatim."""
    fact = (TRANSFORMS / "gold_analytics.sql").read_text()
    first = fact.index("CREATE OR REFRESH MATERIALIZED VIEW")
    fact = fact[first : fact.index("CREATE OR REFRESH MATERIALIZED VIEW", first + 1)]
    cte = re.search(r"latest_decision_records AS \((.*?)\n\),\n", fact, re.S).group(1)
    select_list = fact[fact.index("\nSELECT\n") : fact.index("\nFROM current_adjudications a")]
    columns = [
        line.strip().rstrip(",")
        for line in select_list.splitlines()
        if re.search(r"\bdr\.", line) and not line.strip().startswith("--")
    ]
    assert "FROM decision_records_for_fact d" in cte
    assert len(columns) >= 15  # every agent/decision-record column of the fact
    return (
        f"WITH latest_decision_records AS ({cte}\n)\n"
        f"SELECT a.adjudication_id, {', '.join(columns)}\n"
        "FROM adjudications a\n"
        "LEFT JOIN latest_decision_records dr ON a.adjudication_id = dr.adjudication_id"
    )


def test_absent_source_registers_no_flow_and_an_empty_typed_view(monkeypatch, spark):
    session = _Session(spark, source_exists=False)
    module, dp = _execute_module(monkeypatch, session)

    assert session.probes, "the module must probe for the CDF source table"
    assert dp.streaming_tables == [] and dp.flows == []
    frame = dp.views[module.FACT_VIEW]()
    assert frame.count() == 0
    assert (
        frame.schema.simpleString()
        == spark.createDataFrame([], module.FACT_SCHEMA).schema.simpleString()
    )


def test_gold_fact_decision_record_sql_runs_when_the_table_is_absent(monkeypatch, spark):
    module, dp = _execute_module(monkeypatch, _Session(spark, source_exists=False))
    dp.views[module.FACT_VIEW]().createOrReplaceTempView("decision_records_for_fact")
    spark.createDataFrame(
        [("ADJ-1", "APPROVE"), ("ADJ-2", "DUPLICATE")], "adjudication_id string, disposition string"
    ).createOrReplaceTempView("adjudications")

    rows = {
        r["adjudication_id"]: r.asDict() for r in spark.sql(_fact_decision_record_sql()).collect()
    }

    # One row per adjudication survives the LEFT JOIN; nothing is invented.
    assert set(rows) == {"ADJ-1", "ADJ-2"}
    for row in rows.values():
        assert row["decision_record_version"] is None
        assert row["agent_recommended_verdict"] is None
        assert row["agent_recommended_amount"] is None
        assert row["fraud_risk_flag"] is False and row["over_claim_flag"] is False
    # Fallbacks that do not depend on a decision record still apply.
    assert rows["ADJ-1"]["duplicate_flag"] is False
    assert rows["ADJ-2"]["duplicate_flag"] is True


def test_present_source_exposes_the_same_schema_and_real_rows(monkeypatch, spark):
    # A gold frame shaped like the real table: extra columns, looser source types.
    gold = spark.createDataFrame(
        [
            (
                "ADJ-1",
                2,
                "idem-1",
                False,
                False,
                "APPROVE",
                "REPAIR",
                Decimal("125.50"),
                "m",
                "1",
                "p1",
                "s1",
                ["A653/NA/X/mech"],
                "sha",
                "tr-1",
                "extra",
            )
        ],
        "adjudication_id string, record_version bigint, idempotency_key string, "
        "over_claim_flag boolean, duplicate_flag boolean, recommended_verdict string, "
        "recommended_disposition string, approved_amount decimal(18,2), "
        "agent_model_name string, agent_model_version string, prompt_version string, "
        "schema_version string, cited_clause_ids array<string>, "
        "authorities_source_sha256 string, mlflow_trace_id string, unused_column string",
    )
    from pyspark.sql import functions as F

    module, _ = _execute_module(monkeypatch, _Session(spark, source_exists=False))
    for column in ("created_at", "flags", "conformance", "coverage", "citations"):
        gold = gold.withColumn(column, F.lit(None).cast(module.FACT_COLUMNS[column]))

    module, dp = _execute_module(monkeypatch, _Session(spark, source_exists=True, gold_frame=gold))
    assert len(dp.streaming_tables) == 1 and len(dp.flows) == 1  # the real flow is defined
    frame = dp.views[module.FACT_VIEW]()
    empty = spark.createDataFrame([], module.FACT_SCHEMA)
    assert frame.schema.simpleString() == empty.schema.simpleString()
    (row,) = frame.collect()
    assert row["record_version"] == 2 and row["approved_amount"] == Decimal("125.50")
