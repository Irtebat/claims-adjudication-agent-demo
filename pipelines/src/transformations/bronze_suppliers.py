"""bronze.suppliers: synthetic bootstrap batch, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.materialized_view(
    name=f"`{catalog}`.bronze.suppliers", comment="Synthetic demo data; no embeddings"
)
@dp.expect_all_or_fail({"required_key": "supplier_id IS NOT NULL"})
def dataset():
    return spark.read.parquet(f"/Volumes/{catalog}/bronze/raw_landing/suppliers").withColumn(
        "_source_file", F.col("_metadata.file_path")
    )
