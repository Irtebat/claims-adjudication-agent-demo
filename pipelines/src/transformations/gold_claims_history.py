"""gold.claims_history: synthetic bootstrap batch, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.materialized_view(
    name=f"`{catalog}`.gold.claims_history", comment="Synthetic demo data; no embeddings"
)
@dp.expect_all_or_fail(
    {
        "required_key": "claim_id IS NOT NULL",
        "business_invariant": "claim_date IS NOT NULL AND claimed_tonnage >= 0 AND claimed_freight >= 0",
    }
)
def dataset():
    return spark.read.table(f"`{catalog}`.silver.claims_history").drop("_source_file")
