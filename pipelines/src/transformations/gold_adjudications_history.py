"""gold.adjudications_history: synthetic bootstrap batch, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.materialized_view(
    name=f"`{catalog}`.gold.adjudications_history", comment="Synthetic demo data; no embeddings"
)
@dp.expect_all_or_fail(
    {
        "required_key": "adjudication_id IS NOT NULL",
        "business_invariant": "approved_amount >= 0 AND approved_amount <= claimed_amount AND ((verdict = 'APPROVE' AND disposition IN ('CREDIT','REPLACEMENT','REWORK')) OR (verdict = 'DENY' AND disposition IN ('DENY','DUPLICATE') AND approved_amount = 0) OR (verdict = 'PEND' AND disposition = 'PEND_INVESTIGATE' AND approved_amount = 0))",
    }
)
def dataset():
    return spark.read.table(f"`{catalog}`.silver.adjudications_history").drop("_source_file")
