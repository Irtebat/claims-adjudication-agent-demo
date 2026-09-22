"""silver.coating_warranty_terms: incremental serve-down data with Change Data Feed, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.table(
    name=f"`{catalog}`.silver.coating_warranty_terms",
    comment="Synthetic demo data; no embeddings",
    table_properties={"delta.enableChangeDataFeed": "true"},
)
@dp.expect_all_or_fail(
    {
        "required_key": "clause_id IS NOT NULL",
        "business_invariant": "effective_from < effective_to AND structured_params.duration_months > structured_params.full_coverage_months AND length(clause_text) > 0",
    }
)
def dataset():
    # Stream the immutable bronze landing files; keep the curated schema unchanged.
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .load(f"/Volumes/{catalog}/bronze/raw_landing/coating_warranty_terms")
        .drop("_rescued_data")
    )
