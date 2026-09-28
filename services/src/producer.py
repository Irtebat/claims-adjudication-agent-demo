# Databricks notebook source
# ruff: noqa: F821
"""Checkpointed Spark Structured Streaming producer: CDF insert -> claim.submitted Kafka.

Reads INSERT events from the native CDF table and emits claim.submitted events to Kafka.
Kafka key = claim_id (stable for consumer dedup). Value = claim.submitted JSON event.
Initial snapshot: fresh checkpoint replays all ~5000 seeded claims once; then only new
claims stream. Checkpoint advances after broker ack so re-delivery is skipped next run.
"""

import sys
from pathlib import Path

from pyspark.sql import functions as F
from pyspark.sql.types import StringType

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import producer_core

# Widgets for parameterization.
dbutils.widgets.text(
    "cdf_claims_table",
    "fe-bar-ir.cdf.lb_claims_history",
    label="CDF claims history table",
)
dbutils.widgets.text(
    "checkpoint",
    "/Volumes/fe-bar-ir/bronze/raw_landing/_kafka_checkpoints/claim_submitted",
    label="Checkpoint path for availableNow stream",
)
dbutils.widgets.text(
    "topic",
    "claim.submitted",
    label="Kafka topic for claim.submitted events",
)

cdf_claims_table = dbutils.widgets.get("cdf_claims_table")
checkpoint = dbutils.widgets.get("checkpoint")
topic = dbutils.widgets.get("topic")

print(f"CDF table: {cdf_claims_table}")
print(f"Checkpoint: {checkpoint}")
print(f"Topic: {topic}")

# Kafka configuration from secrets.
cfg = config.kafka_config_from_secrets(dbutils)
spark_options = cfg.spark_kafka_options()

print("Kafka config loaded from scope:", config.KAFKA_SCOPE)


# Backtick-quote each dotted identifier (catalog.schema.table).
def quote_identifier(s):
    """Quote and escape backticks in an identifier."""
    return "`" + s.replace("`", "``") + "`"


cdf_table_quoted = ".".join(quote_identifier(part) for part in cdf_claims_table.split("."))

print(f"CDF table (quoted): {cdf_table_quoted}")

# Read the CDF table and filter for insert changes.
df = spark.readStream.table(cdf_table_quoted).filter(
    F.col("_pg_change_type") == producer_core.SUBMISSION_CHANGE_TYPE
)

print(f"Schema after filtering for inserts: {df.schema}")

# Build key (claim_id) and value (claim.submitted JSON) columns using Spark functions.
# This mirrors events.build_submitted_event in pure Spark SQL.
event_struct = F.struct(
    F.concat(F.lit("sub-"), F.col("claim_id")).alias("event_id"),
    F.lit("claim.submitted").alias("event_type"),
    F.lit("claim-event/v1").alias("schema_version"),
    F.col("claim_id"),
    F.struct(
        F.col("claim_id"),
        F.col("coil_id"),
        F.col("customer_id"),
        F.col("claim_type"),
        F.col("claim_date"),
        F.col("install_date"),
        F.col("environment"),
        F.col("installation"),
        F.col("coast_distance_km"),
        F.col("defect_code"),
        F.col("defect_narrative"),
        F.col("claimed_tonnage"),
        F.col("claimed_freight"),
    ).alias("claim"),
    F.struct(
        F.col("_pg_change_type").alias("cdf_change_type"),
        F.col("_pg_lsn").alias("cdf_commit_version"),
        F.col("_timestamp").alias("cdf_commit_timestamp"),
    ).alias("source"),
    F.date_format(F.current_timestamp(), "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").alias("emitted_at"),
)

df_with_kafka = df.select(
    F.col("claim_id").cast(StringType()).alias("key"),
    F.to_json(event_struct).alias("value"),
)

print("Writing to Kafka with availableNow trigger...")

# Write to Kafka with checkpointing (trigger availableNow for batch-like behavior).
query = (
    df_with_kafka.writeStream.format("kafka")
    .options(**spark_options)
    .option("topic", topic)
    .option("checkpointLocation", checkpoint)
    .trigger(availableNow=True)
    .start()
)

query.awaitTermination()
print("Producer completed.")

# ============================================================================
# CONTINUOUS MODE TOGGLE (for live demo):
# Replace the writeStream trigger above with:
#
#   .trigger(processingTime='30 seconds')
#
# Then run this notebook under a continuous job (not availableNow).
# The job will then stream new claims every 30 seconds. Comment out the
# previous availableNow block and uncomment this one:
#
# ============================================================================
# query = (
#     df_with_kafka.writeStream.format("kafka")
#     .options(**spark_options)
#     .option("topic", topic)
#     .option("checkpointLocation", checkpoint)
#     .trigger(processingTime='30 seconds')
#     .start()
# )
#
# # For continuous job: don't await; let the job runtime orchestrate.
# print("Producer streaming...")
# ============================================================================
