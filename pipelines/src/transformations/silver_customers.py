"""silver.customers: incremental serve-down data with Change Data Feed, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.table(
    name=f"`{catalog}`.silver.customers",
    comment="Synthetic demo data; no embeddings",
    table_properties={"delta.enableChangeDataFeed": "true"},
)
@dp.expect_all_or_fail({"required_key": "customer_id IS NOT NULL"})
def dataset():
    # Stream the immutable bronze landing files; keep the curated schema unchanged.
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .load(f"/Volumes/{catalog}/bronze/raw_landing/customers")
        .drop("_rescued_data")
    )
