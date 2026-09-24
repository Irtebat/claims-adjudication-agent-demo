"""SCD2 adjudications history sourced incrementally from native Lakebase CDF."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


cdf_table = ".".join(
    f"`{identifier.replace('`', '``')}`"
    for identifier in spark.conf.get("claims.cdf_adjudications_table").split(".")
)


@dp.temporary_view(name="adjudications_cdf_changes")
def adjudications_cdf_changes():
    changes = spark.readStream.table(cdf_table).filter(
        F.col("_pg_change_type") != "update_preimage"
    )
    # Native Lakebase CDF serializes Postgres text[] as an array-literal string.
    # Convert the outer braces to JSON brackets so the established ARRAY<STRING>
    # silver contract remains intact; NULL stays NULL and {} becomes an empty array.
    cited_clause_ids = F.when(
        F.col("cited_clause_ids").isNull(),
        F.lit(None).cast("array<string>"),
    ).otherwise(
        F.from_json(
            F.concat(
                F.lit("["),
                F.substring(
                    F.col("cited_clause_ids"),
                    2,
                    F.length("cited_clause_ids") - 2,
                ),
                F.lit("]"),
            ),
            "array<string>",
        )
    )
    return (
        changes.withColumn("cited_clause_ids", cited_clause_ids)
        # Postgres `timestamp` arrives as TIMESTAMP_NTZ, while the preceding
        # Parquet-backed silver contract exposed finalized_at as TIMESTAMP.
        .withColumn("finalized_at", F.col("finalized_at").cast("timestamp"))
        # Seed provenance is operational metadata introduced with Lakebase and
        # was never part of the consumer-facing silver history contract.
        .drop("data_provenance", "baseline_loaded_at")
    )


dp.create_streaming_table(
    name=f"`{catalog}`.silver.adjudications_history",
    comment="SCD Type 2 adjudications history sourced from native Lakebase CDF",
    expect_all_or_fail={
        "required_key": "adjudication_id IS NOT NULL",
        "business_invariant": (
            "approved_amount >= 0 AND approved_amount <= claimed_amount AND "
            "((verdict = 'APPROVE' AND disposition IN ('CREDIT','REPLACEMENT','REWORK')) "
            "OR (verdict = 'DENY' AND disposition IN ('DENY','DUPLICATE') "
            "AND approved_amount = 0) OR (verdict = 'PEND' "
            "AND disposition = 'PEND_INVESTIGATE' AND approved_amount = 0))"
        ),
    },
)

dp.create_auto_cdc_flow(
    target=f"`{catalog}`.silver.adjudications_history",
    source="adjudications_cdf_changes",
    keys=["adjudication_id"],
    sequence_by=F.col("_sort_by"),
    apply_as_deletes=F.expr("_pg_change_type = 'delete'"),
    except_column_list=[
        "_pg_change_type",
        "_pg_lsn",
        "_pg_xid",
        "_timestamp",
        "_sort_by",
    ],
    stored_as_scd_type=2,
)
