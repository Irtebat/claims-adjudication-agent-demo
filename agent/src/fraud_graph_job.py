# Databricks notebook source
"""Batch fraud/quality cluster-risk job -> gold.customer_heat_risk.

Reads current claims, resolves each claim's heat through the coil master, builds the
shared-heat graph, runs connected components and customer-concentration cluster
scoring (pure logic in fraud_graph.py), and overwrites the gold risk table the
agent's get_risk tool reads (synced down to Lakebase separately). Supplier-lot
linking is intentionally out of scope because those broad lots drown the heat-level
collusion signal. Serverless; no GraphFrames dependency — the edge set at demo scale
is small enough to score on the driver.
"""

# COMMAND ----------
import datetime as dt

from pyspark.sql import functions as F
from pyspark.sql import types as T

from fraud_graph import score_clusters

dbutils.widgets.text("catalog", "fe-bar-ir")
catalog = dbutils.widgets.get("catalog")
if not catalog or "`" in catalog or "/" in catalog:
    raise ValueError("Invalid catalog")

heat_map = spark.table(f"`{catalog}`.silver.heats_coils").select("coil_id", "heat_no").distinct()
ambiguous_coils = (
    heat_map.groupBy("coil_id")
    .agg(F.countDistinct("heat_no").alias("heat_count"))
    .filter(F.col("heat_count") != 1)
)
if ambiguous_coils.limit(1).count():
    raise ValueError("silver.heats_coils contains an ambiguous coil_id -> heat_no mapping")

claims = (
    spark.table(f"`{catalog}`.gold.claims_current")
    .select("claim_id", "customer_id", "coil_id")
    .join(heat_map, on="coil_id", how="inner")
    .select("claim_id", "customer_id", "heat_no")
)
rows = [r.asDict() for r in claims.collect()]
result = score_clusters(rows)

schema = T.StructType(
    [
        T.StructField("customer_id", T.StringType()),
        T.StructField("heat_no", T.StringType()),
        T.StructField("cluster_id", T.StringType()),
        T.StructField("cluster_size", T.IntegerType()),
        T.StructField("distinct_customers_in_cluster", T.IntegerType()),
        T.StructField("distinct_heats_in_cluster", T.IntegerType()),
        T.StructField("repeat_customers", T.BooleanType()),
        T.StructField("risk_score", T.DoubleType()),
    ]
)
computed_at = dt.datetime.now(dt.timezone.utc)
frame = spark.createDataFrame(result["risk_rows"], schema).withColumn(
    "computed_at", F.lit(computed_at.isoformat()).cast("timestamp")
)
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.gold")
frame.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    f"`{catalog}`.gold.customer_heat_risk"
)

summary = {
    "catalog": catalog,
    "claims_scored": len(rows),
    "clusters": len(result["clusters"]),
    "risk_rows": len(result["risk_rows"]),
    "high_risk_rows": sum(1 for r in result["risk_rows"] if r["risk_score"] >= 0.5),
}
print(summary)
dbutils.notebook.exit(str(summary))
