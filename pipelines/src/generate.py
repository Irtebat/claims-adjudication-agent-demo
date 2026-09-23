# Databricks notebook source
# MAGIC %pip install Faker==37.6.0

# COMMAND ----------
import json

import pandas as pd
from pyspark.sql import functions as F

catalog = dbutils.widgets.get("catalog")
claim_count = int(dbutils.widgets.get("claim_count"))
seed = int(dbutils.widgets.get("seed"))
# The warranty version schedule (effective windows + durations) is sourced from the
# single authored policy (agent/src/policy_source.json), passed in by run.py. No
# policy numerics are hardcoded here; edits to the policy propagate into history.
warranty_schedule = json.loads(dbutils.widgets.get("warranty_schedule"))
if not warranty_schedule:
    raise ValueError("warranty_schedule is empty; run via run.py so the policy is sourced")
dbutils.widgets.text("validate_only", "false")
validate_only = dbutils.widgets.get("validate_only").lower() == "true"
validation_results = {}
if claim_count < 100:
    raise ValueError("claim_count must be >=100 so all injected patterns are present")
if not catalog or "`" in catalog or "/" in catalog:
    raise ValueError("Invalid catalog")
for schema in ("bronze", "silver", "gold"):
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.{schema}")
if not validate_only:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS `{catalog}`.bronze.raw_landing")
landing = f"/Volumes/{catalog}/bronze/raw_landing"


def write(name, frame):
    if validate_only:
        frame.createOrReplaceTempView(name)
        # Force evaluation of every column, including Faker, without UC storage.
        metrics = (
            frame.select(F.to_json(F.struct("*")).alias("row"))
            .agg(F.count("row").alias("rows"), F.sum(F.length("row")).alias("serialized_bytes"))
            .first()
        )
        validation_results[name] = metrics.asDict()
        print(name, validation_results[name])
    else:
        frame.write.mode("overwrite").parquet(f"{landing}/{name}")


def read(name):
    return spark.table(name) if validate_only else spark.read.parquet(f"{landing}/{name}")


def schedule_col(field):
    """Column selecting a warranty-version field by the ship-date effective window."""
    expr = F.lit(None)
    for version in warranty_schedule:
        in_window = (F.col("ship_date") >= F.lit(version["effective_from"])) & (
            F.col("ship_date") < F.lit(version["effective_to"])
        )
        expr = F.when(in_window, F.lit(version[field])).otherwise(expr)
    return expr


# Policy standards and coating-warranty terms are no longer generated here. They
# are authored in agent/src/policy_source.json and loaded directly into Lakebase
# by the policy intake (agent/src/policy_intake.py) as the single source of both
# the structured params the deterministic authorities read and the citable text
# clauses. This generator only produces reference/master/history fact data.


@F.pandas_udf("string")
def company_name(ids: pd.Series) -> pd.Series:
    from faker import Faker

    fake = Faker()

    def company(value):
        fake.seed_instance(seed + int(value))
        return fake.company()

    return ids.apply(company)


write(
    "customers",
    spark.range(200, numPartitions=8).select(
        F.format_string("CUST-%04d", F.col("id")).alias("customer_id"),
        company_name("id").alias("customer_name"),
        F.concat(F.lit("contact-"), F.col("id"), F.lit("@example.invalid")).alias("email"),
        F.when(F.col("id") % 5 == 0, "manufacturer").otherwise("service_center").alias("segment"),
    ),
)
write(
    "suppliers",
    spark.range(12, numPartitions=8).select(
        F.format_string("SUP-%02d", F.col("id")).alias("supplier_id"),
        company_name(F.col("id") + 10000).alias("supplier_name"),
        F.when(F.col("id") < 4, "coating").otherwise("raw_material").alias("supplier_type"),
    ),
)
write(
    "defect_codes",
    spark.createDataFrame(
        [
            ("MECH_TENSILE", "Mechanical tensile deviation", "cold_mill"),
            ("COATING_VOID", "Coating adhesion/voids", "coating_line"),
            ("RED_RUST", "Field corrosion/perforation", "field"),
        ],
        "defect_code STRING, description STRING, process_step STRING",
    ),
)

# Fixed 100-row blocks make proportions stable at any supported scale. Five
# fraud claims per block share a heat, supplier lot, and three customer identities.
base = spark.range(claim_count, numPartitions=8).withColumnRenamed("id", "i")
base = base.withColumn("slot", F.col("i") % 100).withColumn(
    "block", (F.col("i") / 100).cast("long")
)
base = base.withColumn(
    "ground_truth_label",
    F.expr("""CASE
 WHEN slot < 20 THEN 'clean' WHEN slot < 40 THEN 'in_spec_should_deny'
 WHEN slot < 55 THEN 'out_of_warranty_or_environment_excluded'
 WHEN slot < 65 THEN 'duplicate' WHEN slot < 80 THEN 'over_claim'
 WHEN slot < 95 THEN 'supplier_attributable' ELSE 'fraud_cluster' END"""),
)
# Duplicates reuse identity and all economic/narrative fields of clean slots 0–9.
base = base.withColumn(
    "material_i", F.when(F.col("slot").between(55, 64), F.col("i") - 55).otherwise(F.col("i"))
)
base = base.withColumn("m_slot", F.col("material_i") % 100)
base = base.withColumn("product_idx", F.col("material_i") % 3)
base = base.withColumn("is_warranty", (F.col("m_slot") < 5) | F.col("m_slot").between(40, 54))
base = base.withColumn("expired", F.col("m_slot").between(40, 44))
base = base.selectExpr(
    "*",
    "concat('COIL-', lpad(cast(material_i as string), 7, '0')) coil_id",
    "concat('HEAT-', lpad(cast(floor(material_i/5) as string), 6, '0')) heat_no",
    "CASE product_idx WHEN 0 THEN 'ASTM A653 CS Type B' WHEN 1 THEN 'ASTM A792 AZ50' ELSE 'EN 10346 DX51D+Z275' END grade",
    "CASE product_idx WHEN 0 THEN 'galvanized' WHEN 1 THEN 'galvalume' ELSE 'prepainted' END product_line",
    "CASE product_idx WHEN 0 THEN 'G90' WHEN 1 THEN 'AZ50' ELSE 'Z275' END coating_class",
    "CASE WHEN material_i % 4 = 0 THEN 'EU' ELSE 'NA' END region",
    "CASE WHEN m_slot >= 95 THEN concat('CUST-', lpad(cast(197 + material_i % 3 as string),4,'0')) ELSE concat('CUST-', lpad(cast(pmod(material_i * 17,197) as string),4,'0')) END customer_id",
    "CASE WHEN m_slot >= 80 THEN 'SUP-00' ELSE concat('SUP-', lpad(cast(pmod(material_i,4) as string),2,'0')) END coating_supplier_id",
    "concat('SUP-', lpad(cast(4 + pmod(material_i,8) as string),2,'0')) raw_material_supplier_id",
    "date_add(DATE'2026-01-01', cast(pmod(block,180) as int)) claim_date",
    "CASE WHEN expired THEN DATE'1999-01-01' WHEN is_warranty THEN CASE WHEN material_i % 2 = 0 THEN DATE'2014-01-01' ELSE DATE'2018-01-01' END ELSE DATE'2025-12-01' END ship_date",
    "cast(10 + pmod(material_i * 7,21) as decimal(12,3)) shipped_tonnage",
    "cast(650 + pmod(material_i * 31,550) as decimal(18,2)) unit_price",
    "CASE WHEN is_warranty THEN 'RED_RUST' WHEN m_slot BETWEEN 80 AND 94 THEN 'COATING_VOID' ELSE 'MECH_TENSILE' END defect_code",
    "CASE WHEN m_slot BETWEEN 45 AND 49 THEN 'marine' ELSE 'inland' END environment",
    "CASE WHEN m_slot BETWEEN 50 AND 54 THEN 'standing_water' ELSE 'ventilated' END installation",
    "CASE WHEN m_slot BETWEEN 45 AND 49 THEN 0.5 ELSE 25.0 END coast_distance_km",
    "CASE WHEN m_slot >= 95 THEN concat('RING-',block) ELSE cast(NULL as string) END fraud_cluster_id",
)
base = base.withColumn("spec_edition", F.lit("DEMO-1990"))
base = base.withColumn(
    "spec_id",
    F.concat(
        F.when(F.col("product_idx") == 0, F.lit("S-A653"))
        .when(F.col("product_idx") == 1, F.lit("S-A792"))
        .otherwise(F.lit("S-EN10346")),
        F.lit("-"),
        F.col("region"),
        F.lit("-DEMO-1990"),
    ),
)
base = base.withColumn(
    "warranty_id",
    F.concat_ws("-", F.lit("W"), F.col("product_line"), F.col("region"), schedule_col("version")),
)
materials = base.filter("slot NOT BETWEEN 55 AND 64")
write(
    "heats_coils",
    materials.selectExpr(
        "coil_id",
        "heat_no",
        "grade",
        "spec_edition",
        "spec_id",
        "product_line",
        "coating_class",
        "region",
        "customer_id",
        "warranty_id",
        "1.00 ordered_gauge_mm",
        "1.01 gauge_mm",
        "1200.0 ordered_width_mm",
        "1201.0 width_mm",
        "CASE WHEN product_idx = 1 THEN 155.0 ELSE 280.0 END coating_weight_g_m2",
        "concat('LINE-',product_idx + 1) line",
        "date_sub(ship_date, 10) prod_date",
        "ship_date",
        "concat('RAW-',heat_no) raw_material_lot",
        "concat('COAT-',coating_supplier_id,'-',block) coating_supplier_lot",
        "raw_material_supplier_id",
        "coating_supplier_id",
        "shipped_tonnage",
        "unit_price",
    ),
)
write(
    "mill_test_certs",
    materials.selectExpr(
        "concat('MTC-',coil_id) cert_id",
        "coil_id",
        "heat_no",
        "date_sub(ship_date,5) cert_date",
        "0.08 carbon_pct",
        "0.40 manganese_pct",
        "250.0 yield_mpa",
        "CASE WHEN (m_slot BETWEEN 5 AND 19) OR (m_slot BETWEEN 65 AND 79) THEN 620.0 ELSE 400.0 END tensile_mpa",
        "30.0 elongation_pct",
        "CASE WHEN m_slot BETWEEN 80 AND 94 THEN false ELSE true END coating_adhesion_pass",
    ),
)
claims = base.selectExpr(
    "concat('CLM-',lpad(cast(i as string),7,'0')) claim_id",
    "coil_id",
    "heat_no",
    "customer_id",
    "grade",
    "spec_edition",
    "spec_id",
    "product_line",
    "coating_class",
    "region",
    "warranty_id",
    "CASE WHEN is_warranty THEN 'coating_warranty' ELSE 'material_nonconformance' END claim_type",
    "date_add(claim_date, CASE WHEN slot BETWEEN 55 AND 64 THEN 1 ELSE 0 END) claim_date",
    "ship_date",
    "date_add(ship_date,30) install_date",
    "environment",
    "installation",
    "coast_distance_km",
    "defect_code",
    "CASE WHEN m_slot >= 95 THEN 'Identical edge failure across delivered coils; request full replacement urgently.' WHEN is_warranty THEN 'Premature red rust and perforation observed on installed roofing.' WHEN m_slot BETWEEN 80 AND 94 THEN 'Coating detaches during forming; coating voids visible along strip.' ELSE 'Tensile response during forming differs from ordered mechanical requirements.' END defect_narrative",
    "shipped_tonnage",
    "unit_price",
    "cast(CASE WHEN m_slot BETWEEN 65 AND 79 THEN shipped_tonnage * 1.4 ELSE shipped_tonnage END as decimal(12,3)) claimed_tonnage",
    "cast(CASE WHEN m_slot BETWEEN 65 AND 79 THEN 1800 ELSE 0 END as decimal(18,2)) claimed_freight",
    "cast(500 as decimal(18,2)) freight_cap",
    "ground_truth_label",
    "fraud_cluster_id",
    "CASE WHEN slot BETWEEN 55 AND 64 THEN concat('CLM-',lpad(cast(material_i as string),7,'0')) ELSE cast(NULL as string) END duplicate_of_claim_id",
    "coating_supplier_id",
)
claims = claims.withColumn(
    "claimed_amount",
    (F.col("claimed_tonnage") * F.col("unit_price") + F.col("claimed_freight")).cast(
        "decimal(18,2)"
    ),
)
write("claims_history", claims)

# Historical outcomes are synthetic ground truth, never an LLM's calculation. The
# duration/full-coverage months and version boundaries come from warranty_schedule
# (the authored policy, via run.py) so an edit to the policy propagates here. The
# authoritative live coverage math is compute_coverage/compute_settlement reading
# the params loaded into Lakebase, never this generator.
claims = read("claims_history")
adj = claims.withColumn("_duration_months", schedule_col("duration_months"))
adj = adj.withColumn("_full_coverage_months", schedule_col("full_coverage_months"))
adj = adj.withColumn("elapsed_months", F.floor(F.months_between("claim_date", "ship_date")))
adj = adj.withColumn(
    "proration_factor",
    F.when(F.col("elapsed_months") <= F.col("_full_coverage_months"), F.lit(1.0)).otherwise(
        F.greatest(
            F.lit(0.0),
            (F.col("_duration_months") - F.col("elapsed_months"))
            / (F.col("_duration_months") - F.col("_full_coverage_months")),
        )
    ),
)
adj = adj.withColumn(
    "verdict",
    F.when(
        F.col("ground_truth_label").isin(
            "in_spec_should_deny", "out_of_warranty_or_environment_excluded", "duplicate"
        ),
        "DENY",
    )
    .when(F.col("ground_truth_label") == "fraud_cluster", "PEND_INVESTIGATE")
    .otherwise("APPROVE"),
)
adj = adj.withColumn(
    "approved_amount",
    F.when(
        F.col("verdict") == "APPROVE",
        F.col("shipped_tonnage")
        * F.col("unit_price")
        * F.when(F.col("claim_type") == "coating_warranty", F.col("proration_factor")).otherwise(
            F.lit(1.0)
        )
        + F.when(F.col("ground_truth_label") == "over_claim", F.col("freight_cap")).otherwise(
            F.lit(0)
        ),
    )
    .otherwise(F.lit(0))
    .cast("decimal(18,2)"),
)
write(
    "adjudications_history",
    adj.selectExpr(
        "concat('ADJ-', claim_id) adjudication_id",
        "claim_id",
        "verdict",
        "verdict recommended_verdict",
        "CASE WHEN verdict = 'APPROVE' THEN CASE WHEN ground_truth_label = 'supplier_attributable' THEN 'replacement' WHEN ground_truth_label = 'over_claim' THEN 'rework' ELSE 'credit' END ELSE cast(NULL as string) END disposition",
        "claimed_amount",
        "approved_amount",
        "CASE WHEN verdict = 'APPROVE' THEN shipped_tonnage ELSE cast(NULL as decimal(12,3)) END covered_tonnage",
        "CASE WHEN verdict = 'APPROVE' THEN ground_truth_label = 'over_claim' ELSE cast(NULL as boolean) END freight_covered",
        "ground_truth_label = 'supplier_attributable' supplier_attributable",
        "CASE WHEN ground_truth_label = 'supplier_attributable' THEN coating_supplier_id ELSE cast(NULL as string) END recovery_supplier_id",
        "defect_code defect_failure_mode_code",
        "false override_flag",
        "'FINAL' decision_status",
        "concat('Synthetic adjudication: ',ground_truth_label) rationale",
        "CASE WHEN claim_type = 'coating_warranty' THEN array(concat(warranty_id, ':coverage'),concat(warranty_id, ':exclusions'),concat(warranty_id, ':proration')) ELSE array(concat(spec_id, ':mechanical'),concat(spec_id, ':dimensions')) END cited_clause_ids",
        "cast(date_add(claim_date, 2) as timestamp) finalized_at",
    ),
)
print(
    json.dumps({"catalog": catalog, "claim_count": claim_count, "seed": seed, "landing": landing})
)

if validate_only:
    from checks import integrity_queries

    queries = integrity_queries("`fixture`")
    check_sql = (
        " UNION ALL ".join(
            f"SELECT '{name}' check_name, n violations FROM ({query})"
            for name, query in queries.items()
        )
        .replace("`fixture`.silver.", "")
        .replace("`fixture`.gold.", "")
    )
    failures = spark.sql(check_sql).filter("violations > 0")
    assert failures.isEmpty(), failures.show(truncate=False)
    print(f"{len(queries)} semantic integrity checks passed")
    assert validation_results["claims_history"]["rows"] == claim_count
    assert validation_results["adjudications_history"]["rows"] == claim_count
    dbutils.notebook.exit(
        json.dumps(
            {
                "mode": "validate_only_no_data_landed",
                "tables": validation_results,
                "integrity_checks_passed": len(queries),
            }
        )
    )
