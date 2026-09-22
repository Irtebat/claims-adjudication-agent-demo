"""silver.claims_history: synthetic bootstrap batch, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.materialized_view(
    name=f"`{catalog}`.silver.claims_history", comment="Synthetic demo data; no embeddings"
)
@dp.expect_all_or_fail(
    {
        "required_key": "claim_id IS NOT NULL",
        "business_invariant": "claimed_amount = cast(claimed_tonnage * unit_price + claimed_freight as decimal(18,2)) AND ship_date <= claim_date AND ground_truth_label IN ('clean','in_spec_should_deny','out_of_warranty_or_environment_excluded','duplicate','over_claim','supplier_attributable','fraud_cluster')",
    }
)
def dataset():
    return spark.read.table(f"`{catalog}`.bronze.claims_history").drop("_source_file")
