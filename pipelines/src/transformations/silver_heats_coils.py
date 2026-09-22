"""silver.heats_coils: incremental serve-down data with Change Data Feed, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.table(
    name=f"`{catalog}`.silver.heats_coils",
    comment="Synthetic demo data; no embeddings",
    table_properties={"delta.enableChangeDataFeed": "true"},
)
@dp.expect_all_or_fail(
    {
        "required_key": "coil_id IS NOT NULL",
        "business_invariant": "prod_date <= ship_date AND unit_price > 0 AND shipped_tonnage > 0",
    }
)
def dataset():
    # Stream the immutable bronze landing files; keep the curated schema unchanged.
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .load(f"/Volumes/{catalog}/bronze/raw_landing/heats_coils")
        .drop("_rescued_data")
    )
