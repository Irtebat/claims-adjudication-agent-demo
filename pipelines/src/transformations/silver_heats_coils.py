"""silver.heats_coils: keyed serve-down snapshot with Change Data Feed."""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")


@dp.temporary_view(name="heats_coils_changes")
def changes():
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "parquet")
        .load(f"/Volumes/{catalog}/bronze/raw_landing/heats_coils")
        .withColumn("_source_modified_at", F.col("_metadata.file_modification_time"))
        .withColumn("_source_file", F.col("_metadata.file_path"))
        .drop("_rescued_data")
    )


dp.create_streaming_table(
    name=f"`{catalog}`.silver.heats_coils",
    comment="Synthetic demo data; no embeddings",
    table_properties={"delta.enableChangeDataFeed": "true"},
    expect_all_or_fail={
        "required_key": "coil_id IS NOT NULL",
        "business_invariant": "prod_date <= ship_date AND unit_price > 0 AND shipped_tonnage > 0",
    },
)

dp.create_auto_cdc_flow(
    target=f"`{catalog}`.silver.heats_coils",
    source="heats_coils_changes",
    keys=["coil_id"],
    sequence_by=F.struct("_source_modified_at", "_source_file"),
    stored_as_scd_type=1,
    except_column_list=["_source_modified_at", "_source_file"],
)
