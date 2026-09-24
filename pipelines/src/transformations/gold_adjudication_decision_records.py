"""Append-only adjudication decision records, sourced from native Lakebase CDF.

The canonical decision record per adjudication is IMMUTABLE: exactly one row per
``(adjudication_id, record_version)``, and no updates/deletes are ever propagated.
Only insert changes are consumed; the AUTO CDC flow is SCD Type 1 keyed on the
composite key with no delete application, so a re-inserted key rewrites an
identical row and a source mutation (should one ever occur) is ignored downstream.

The JSONB payload is parsed into proper structs/arrays for analytics — the
decision-critical structs into typed columns, the bulk param/blob columns into
queryable VARIANT — and the Postgres ``text[]`` clause keys are converted with the
same brace technique used for ``cited_clause_ids`` in the CDF history work.

The flow is defined only when its CDF source table exists, so the medallion
pipeline stays valid whether or not the decision-record table has been created and
picked up by the schema-scoped native CDF config yet.
"""

from pyspark import pipelines as dp
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.getActiveSession()
catalog = spark.conf.get("claims.catalog")

_cdf_name = spark.conf.get("claims.cdf_decision_records_table")
_parts = _cdf_name.split(".")
cdf_table = ".".join(f"`{identifier.replace('`', '``')}`" for identifier in _parts)

_exists = (
    len(_parts) == 3
    and spark.sql(
        f"SELECT count(*) AS c FROM `{_parts[0]}`.information_schema.tables "
        f"WHERE table_schema = '{_parts[1]}' AND table_name = '{_parts[2]}'"
    ).first()["c"]
    > 0
)

# Typed structs for the decision-critical JSONB columns.
_STRUCT_SCHEMAS = {
    "conformance": "struct<conforms:boolean,nonconforming_properties:array<string>>",
    "coverage": (
        "struct<covered:boolean,elapsed_months:int,proration_factor:double,"
        "exclusions_hit:array<string>>"
    ),
    "settlement": (
        "struct<approved_amount:double,claimed_amount:double,covered_tonnage:double,"
        "freight_amount:double,freight_covered:boolean,over_claim_detected:boolean,"
        "is_partial:boolean>"
    ),
    "duplicate": (
        "struct<is_duplicate:boolean,duplicate_of_claim_id:string,"
        "narrative_similarity:double,candidates_considered:int>"
    ),
    "spec_provenance": "struct<grade:string,spec_edition:string,region:string>",
    "warranty_provenance": "struct<product_line:string,region:string,version:string>",
    "flags": "struct<supplier_attributable:boolean,fraud_risk:boolean,over_claim:boolean>",
    "citations": "array<struct<citation_key:string,section_ref:string,clause_text_sha256:string>>",
    "precedent": "array<struct<claim_id:string,verdict:string,approved_amount:double,rrf_score:double>>",
    "invariant_violations": "array<string>",
}
# Bulk param/blob JSONB columns parsed to queryable VARIANT.
_VARIANT_COLUMNS = [
    "claim_input",
    "spec_params",
    "warranty_terms",
    "coil",
    "mtc_measured",
    "advisory_risk",
]


def _parse_payload(changes):
    for column, schema in _STRUCT_SCHEMAS.items():
        changes = changes.withColumn(column, F.from_json(F.col(column).cast("string"), schema))
    for column in _VARIANT_COLUMNS:
        changes = changes.withColumn(column, F.parse_json(F.col(column).cast("string")))
    # Postgres text[] arrives as an array-literal string; convert braces to JSON brackets.
    cited = F.when(F.col("cited_clause_ids").isNull(), F.lit(None).cast("array<string>")).otherwise(
        F.from_json(
            F.concat(
                F.lit("["),
                F.substring(F.col("cited_clause_ids"), 2, F.length("cited_clause_ids") - 2),
                F.lit("]"),
            ),
            "array<string>",
        )
    )
    return changes.withColumn("cited_clause_ids", cited).withColumn(
        "created_at", F.col("created_at").cast("timestamp")
    )


if _exists:

    @dp.temporary_view(name="decision_records_cdf_changes")
    def decision_records_cdf_changes():
        # Immutable: consume inserts only; updates/deletes are never propagated.
        changes = spark.readStream.table(cdf_table).filter(F.col("_pg_change_type") == "insert")
        return _parse_payload(changes)

    dp.create_streaming_table(
        name=f"`{catalog}`.gold.adjudication_decision_records",
        comment="Append-only, immutable adjudication decision records from native Lakebase CDF",
        expect_all_or_fail={
            "required_key": "adjudication_id IS NOT NULL AND record_version IS NOT NULL",
            "money_invariant": (
                "approved_amount >= 0 AND "
                "(recommended_verdict = 'APPROVE' OR approved_amount = 0) AND "
                "(NOT duplicate_flag OR (recommended_verdict = 'DENY' AND approved_amount = 0))"
            ),
        },
    )

    dp.create_auto_cdc_flow(
        target=f"`{catalog}`.gold.adjudication_decision_records",
        source="decision_records_cdf_changes",
        keys=["adjudication_id", "record_version"],
        sequence_by=F.col("_sort_by"),
        except_column_list=[
            "_pg_change_type",
            "_pg_lsn",
            "_pg_xid",
            "_timestamp",
            "_sort_by",
        ],
        stored_as_scd_type=1,
    )
