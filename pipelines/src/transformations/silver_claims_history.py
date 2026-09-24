"""SCD2 claims history sourced incrementally from native Lakebase CDF."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


cdf_table = ".".join(
    f"`{identifier.replace('`', '``')}`"
    for identifier in spark.conf.get("claims.cdf_claims_table").split(".")
)


@dp.temporary_view(name="claims_cdf_changes")
def claims_cdf_changes():
    return (
        spark.readStream.table(cdf_table)
        .filter(F.col("_pg_change_type") != "update_preimage")
        # Native CDF adds seed provenance fields that were absent from the prior
        # consumer-facing silver schema; all remaining claim types already match.
        .drop("data_provenance", "baseline_loaded_at")
    )


dp.create_streaming_table(
    name=f"`{catalog}`.silver.claims_history",
    comment="SCD Type 2 claims history sourced from native Lakebase CDF",
    expect_all_or_fail={
        "required_key": "claim_id IS NOT NULL",
        "business_invariant": (
            "claim_date IS NOT NULL AND claimed_tonnage >= 0 AND claimed_freight >= 0"
        ),
    },
)

dp.create_auto_cdc_flow(
    target=f"`{catalog}`.silver.claims_history",
    source="claims_cdf_changes",
    keys=["claim_id"],
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
