# Databricks notebook source
"""Build the prior-claims precedent corpus in Unity Catalog -> gold.prior_claims_corpus.

Replaces the native Lakebase ``prior_claims`` table and its embed-once backfill. The
corpus is one row per claim whose current adjudication is FINAL (latest finalized
adjudication wins), enriched with grade/coating from the deduplicated coil master and
embedded with the governed gateway helper (``system.ai.gte-large-en``, 1024-dim,
L2-normalized, cosine). The table is served down to Lakebase as the Triggered synced
table ``reference.prior_claims_corpus``, where ``lakebase_ann`` / ``lakebase_bm25``
indexes are built on it (see ``lakebase/scripts/synced_tables.py``).

Incremental by construction: an existing embedding is reused when the narrative hash
and the embedding provenance still match, so only new or edited narratives are sent
to the gateway. The candidate set is written once to a staging Delta table and every
later step reads that snapshot, so the embedding plan and the MERGE source cannot see
two different reads of the gold views. Serverless rejects DataFrame caching and
persisting, and nothing here needs either. The table is written with a MERGE that updates only changed rows and
deletes claims that left the corpus, so its Delta CDF (required by the Triggered sync)
carries only real changes. Advisory data only — precedent never decides money.
"""

# COMMAND ----------
import datetime as dt
import json

from pyspark.sql import Window
from pyspark.sql import functions as F

from gateway_embed import PROVENANCE, embed_texts
from prior_claims_corpus import CORPUS_TABLE, STAGING_TABLE, vector_literal

dbutils.widgets.text("catalog", "fe-bar-ir")
catalog = dbutils.widgets.get("catalog")
if not catalog or "`" in catalog or "/" in catalog:
    raise ValueError("Invalid catalog")
target = f"`{catalog}`.gold.{CORPUS_TABLE}"
staging = f"`{catalog}`.gold.{STAGING_TABLE}"

# COMMAND ----------
# Candidates: current claims x latest FINAL adjudication x deduplicated coil master.
latest_final = Window.partitionBy("claim_id").orderBy(
    F.col("finalized_at").desc_nulls_last(), F.col("adjudication_id").desc()
)
final_adjudications = (
    spark.table(f"`{catalog}`.gold.adjudications_current")
    .filter(F.col("decision_status") == "FINAL")
    .withColumn("_rn", F.row_number().over(latest_final))
    .filter("_rn = 1")
    .select("claim_id", "verdict", F.col("approved_amount").cast("decimal(18,2)"))
)
latest_coil = Window.partitionBy("coil_id").orderBy(
    F.col("ship_date").desc(), F.col("prod_date").desc()
)
coils = (
    spark.table(f"`{catalog}`.silver.heats_coils")
    .withColumn("_rn", F.row_number().over(latest_coil))
    .filter("_rn = 1")
    .select("coil_id", "grade", "coating_class")
)
# Null bytes break the synced-table pipeline; strip them at the source.
narrative = F.regexp_replace(F.col("defect_narrative"), "\u0000", "")
candidate_rows = (
    spark.table(f"`{catalog}`.gold.claims_current")
    .select("claim_id", "coil_id", "defect_code", narrative.alias("defect_narrative"), "claim_date")
    .join(final_adjudications, "claim_id")
    .join(coils, "coil_id")
    .filter(
        "defect_narrative IS NOT NULL AND grade IS NOT NULL AND coating_class IS NOT NULL "
        "AND defect_code IS NOT NULL AND claim_date IS NOT NULL AND verdict IS NOT NULL"
    )
    .withColumn("narrative_sha256", F.sha2(F.col("defect_narrative"), 256))
)
# Materialize the candidate snapshot once (overwritten every run, dropped on success).
candidate_rows.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(staging)
candidates = spark.table(staging)

# COMMAND ----------
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS {target} (
      claim_id STRING NOT NULL,
      coil_id STRING NOT NULL,
      grade STRING NOT NULL,
      coating_class STRING NOT NULL,
      defect_code STRING NOT NULL,
      defect_narrative STRING NOT NULL,
      claim_date DATE NOT NULL,
      verdict STRING NOT NULL,
      approved_amount DECIMAL(18,2),
      embedding STRING COMMENT 'pgvector text literal, 1024-dim, L2-normalized',
      narrative_sha256 STRING NOT NULL,
      embedding_provenance STRING,
      embedded_at TIMESTAMP
    )
    COMMENT 'Prior-claims precedent corpus (FINAL adjudications), served down to Lakebase'
    TBLPROPERTIES (delta.enableChangeDataFeed = true)
    """
)
# Delta CDF is required by the Triggered synced table; keep it set on every run.
spark.sql(f"ALTER TABLE {target} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")

# Reuse an embedding only when the narrative and the embedding provenance both match.
existing = spark.table(target).select(
    "claim_id",
    F.col("narrative_sha256").alias("_prior_sha"),
    F.col("embedding").alias("_prior_embedding"),
    F.col("embedding_provenance").alias("_prior_provenance"),
    F.col("embedded_at").alias("_prior_embedded_at"),
)
planned = candidates.join(existing, "claim_id", "left").withColumn(
    "_reuse",
    (F.col("_prior_sha") == F.col("narrative_sha256"))
    & (F.col("_prior_provenance") == F.lit(PROVENANCE))
    & F.col("_prior_embedding").isNotNull(),
)
to_embed = [
    (row["claim_id"], row["defect_narrative"])
    for row in planned.filter(~F.coalesce(F.col("_reuse"), F.lit(False)))
    .select("claim_id", "defect_narrative")
    .orderBy("claim_id")
    .collect()
]

# COMMAND ----------
# In a job the SDK resolves runtime auth; the helper's default provider assumes a CLI
# profile, so pass a provider backed by the notebook's own WorkspaceClient.
from databricks.sdk import WorkspaceClient  # noqa: E402

_client = WorkspaceClient()


def _token_provider():
    headers = _client.config.authenticate()
    return _client.config.host.rstrip("/"), headers["Authorization"]


embedded_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
fresh = None
CHUNK = 500  # bound the driver->Spark payload (each literal is ~10 KB)
for start in range(0, len(to_embed), CHUNK):
    batch = to_embed[start : start + CHUNK]
    vectors = embed_texts([text for _, text in batch], token_provider=_token_provider)
    part = spark.createDataFrame(
        [(cid, vector_literal(vec)) for (cid, _), vec in zip(batch, vectors)],
        "claim_id STRING, _new_embedding STRING",
    )
    fresh = part if fresh is None else fresh.unionByName(part)
if fresh is None:
    fresh = spark.createDataFrame([], "claim_id STRING, _new_embedding STRING")

corpus = (
    planned.join(fresh, "claim_id", "left")
    .withColumn(
        "embedding",
        F.when(F.col("_reuse"), F.col("_prior_embedding")).otherwise(F.col("_new_embedding")),
    )
    .withColumn("embedding_provenance", F.lit(PROVENANCE))
    .withColumn(
        "embedded_at",
        F.when(F.col("_reuse"), F.col("_prior_embedded_at")).otherwise(
            F.lit(embedded_at).cast("timestamp")
        ),
    )
    .select(
        "claim_id",
        "coil_id",
        "grade",
        "coating_class",
        "defect_code",
        "defect_narrative",
        "claim_date",
        "verdict",
        "approved_amount",
        "embedding",
        "narrative_sha256",
        "embedding_provenance",
        "embedded_at",
    )
)
missing = corpus.filter("embedding IS NULL").count()
if missing:
    raise RuntimeError(f"{missing} corpus rows have no embedding; refusing to publish")
corpus.createOrReplaceTempView("prior_claims_corpus_source")

# COMMAND ----------
# Change-only MERGE: untouched rows produce no CDF, so the Triggered sync moves only
# real changes. Claims that left the corpus (no longer FINAL) are deleted.
_compare = [
    "coil_id",
    "grade",
    "coating_class",
    "defect_code",
    "defect_narrative",
    "claim_date",
    "verdict",
    "approved_amount",
    "embedding",
    "narrative_sha256",
    "embedding_provenance",
]
changed = " OR ".join(f"NOT (t.{c} <=> s.{c})" for c in _compare)
merge = spark.sql(
    f"""
    MERGE INTO {target} t
    USING prior_claims_corpus_source s
    ON t.claim_id = s.claim_id
    WHEN MATCHED AND ({changed}) THEN UPDATE SET *
    WHEN NOT MATCHED THEN INSERT *
    WHEN NOT MATCHED BY SOURCE THEN DELETE
    """
).first()

summary = {
    "catalog": catalog,
    "table": f"{catalog}.gold.{CORPUS_TABLE}",
    "corpus_rows": spark.table(target).count(),
    "embedded_now": len(to_embed),
    "provenance": PROVENANCE,
    "merge": merge.asDict() if merge is not None else None,
}
spark.sql(f"DROP TABLE IF EXISTS {staging}")
print(json.dumps(summary, sort_keys=True, default=str))
dbutils.notebook.exit(json.dumps(summary, sort_keys=True, default=str))
