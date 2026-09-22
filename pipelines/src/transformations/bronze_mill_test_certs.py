"""bronze.mill_test_certs: synthetic bootstrap batch, managed by Lakeflow."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.materialized_view(
    name=f"`{catalog}`.bronze.mill_test_certs", comment="Synthetic demo data; no embeddings"
)
@dp.expect_all_or_fail({"required_key": "cert_id IS NOT NULL"})
def dataset():
    return spark.read.parquet(f"/Volumes/{catalog}/bronze/raw_landing/mill_test_certs").withColumn(
        "_source_file", F.col("_metadata.file_path")
    )
