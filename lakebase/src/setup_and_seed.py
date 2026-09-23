# Databricks notebook source
# ruff: noqa: F821, SIM117
"""Create the operational schema and idempotently seed the synthetic baseline."""

import json

import psycopg
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("catalog", "fe-bar-ir")
dbutils.widgets.text(
    "endpoint",
    "projects/fe-bar-operational-plane/branches/production/endpoints/primary",
)
dbutils.widgets.text("postgres_database", "databricks_postgres")

catalog = dbutils.widgets.get("catalog")
endpoint = dbutils.widgets.get("endpoint")
postgres_database = dbutils.widgets.get("postgres_database")

w = WorkspaceClient()
endpoint_details = w.postgres.get_endpoint(name=endpoint)
credential = w.postgres.generate_database_credential(endpoint=endpoint)
username = w.current_user.me().user_name
host = endpoint_details.status.hosts.host

DDL = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS lakebase_vector;
CREATE EXTENSION IF NOT EXISTS lakebase_text;

CREATE TABLE IF NOT EXISTS claims (
  claim_id text PRIMARY KEY,
  coil_id text, customer_id text, claim_type text, claim_date date,
  install_date date, environment text, installation text,
  coast_distance_km numeric(3,1), defect_code text, defect_narrative text,
  claimed_tonnage numeric(12,3), claimed_freight numeric(18,2),
  data_provenance text NOT NULL,
  baseline_loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS adjudications (
  adjudication_id text PRIMARY KEY, claim_id text, verdict text,
  recommended_verdict text, disposition text, claimed_amount numeric(18,2),
  approved_amount numeric(18,2), covered_tonnage numeric(12,3),
  freight_covered boolean, supplier_attributable boolean,
  recovery_supplier_id text, defect_failure_mode_code text,
  override_flag boolean, decision_status text, rationale text,
  cited_clause_ids text[], finalized_at timestamp,
  duplicate_of_claim_id text, fraud_cluster_id text,
  data_provenance text NOT NULL,
  baseline_loaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS prior_claims (
  claim_id text PRIMARY KEY, coil_id text NOT NULL, grade text NOT NULL,
  coating_class text NOT NULL, defect_code text NOT NULL, defect_narrative text NOT NULL,
  claim_date date NOT NULL, verdict text NOT NULL, approved_amount numeric(18,2),
  embedding vector(1024), narrative_tsv tsvector NOT NULL
);
ALTER TABLE adjudications
  ADD COLUMN IF NOT EXISTS duplicate_of_claim_id text,
  ADD COLUMN IF NOT EXISTS fraud_cluster_id text;
ALTER TABLE claims
  DROP COLUMN IF EXISTS heat_no, DROP COLUMN IF EXISTS grade,
  DROP COLUMN IF EXISTS spec_edition,
  DROP COLUMN IF EXISTS product_line, DROP COLUMN IF EXISTS coating_class,
  DROP COLUMN IF EXISTS region,
  DROP COLUMN IF EXISTS ship_date, DROP COLUMN IF EXISTS shipped_tonnage,
  DROP COLUMN IF EXISTS unit_price, DROP COLUMN IF EXISTS freight_cap,
  DROP COLUMN IF EXISTS ground_truth_label, DROP COLUMN IF EXISTS fraud_cluster_id,
  DROP COLUMN IF EXISTS duplicate_of_claim_id, DROP COLUMN IF EXISTS coating_supplier_id,
  DROP COLUMN IF EXISTS claimed_amount;
CREATE INDEX IF NOT EXISTS prior_claims_lb_ann ON prior_claims
  USING lakebase_ann (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS prior_claims_lb_bm25 ON prior_claims
  USING lakebase_bm25 (narrative_tsv tsvector_bm25_ops);
CREATE TABLE IF NOT EXISTS outbox (
  event_id text PRIMARY KEY, aggregate_id text NOT NULL, event_type text NOT NULL,
  payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz
);
CREATE TABLE IF NOT EXISTS settlements (
  settlement_id text PRIMARY KEY, claim_id text NOT NULL, status text NOT NULL,
  amount numeric(18,2), settled_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS investigation_cases (
  investigation_case_id text PRIMARY KEY, claim_id text NOT NULL,
  status text NOT NULL, opened_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz, notes text
);
CREATE TABLE IF NOT EXISTS supplier_recovery_cases (
  supplier_recovery_case_id text PRIMARY KEY, claim_id text NOT NULL,
  supplier_id text NOT NULL, status text NOT NULL, recovery_amount numeric(18,2),
  created_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz
);
CREATE TABLE IF NOT EXISTS claims_pending (
  claim_id text PRIMARY KEY, reason text NOT NULL,
  queued_at timestamptz NOT NULL DEFAULT now(), retry_count integer NOT NULL DEFAULT 0
);

ALTER TABLE claims REPLICA IDENTITY FULL;
ALTER TABLE adjudications REPLICA IDENTITY FULL;
ALTER TABLE outbox REPLICA IDENTITY FULL;
ALTER TABLE settlements REPLICA IDENTITY FULL;
ALTER TABLE investigation_cases REPLICA IDENTITY FULL;
ALTER TABLE supplier_recovery_cases REPLICA IDENTITY FULL;
ALTER TABLE claims_pending REPLICA IDENTITY FULL;
"""

CLAIM_COLUMNS = [
    "claim_id", "coil_id", "customer_id", "claim_type", "claim_date", "install_date", "environment",
    "installation", "coast_distance_km", "defect_code", "defect_narrative",
    "claimed_tonnage", "claimed_freight",
]
ADJUDICATION_COLUMNS = [
    "adjudication_id", "claim_id", "verdict", "recommended_verdict",
    "disposition", "claimed_amount", "approved_amount", "covered_tonnage",
    "freight_covered", "supplier_attributable", "recovery_supplier_id",
    "defect_failure_mode_code", "override_flag", "decision_status", "rationale",
    "cited_clause_ids", "finalized_at",
    "duplicate_of_claim_id", "fraud_cluster_id",
]


def upsert_sql(table, columns, key):
    all_columns = columns + ["data_provenance"]
    updates = [column for column in all_columns if column != key]
    return (
        f"INSERT INTO {table} ({', '.join(all_columns)}) VALUES "
        f"({', '.join(['%s'] * len(all_columns))}) ON CONFLICT ({key}) DO UPDATE SET "
        + ", ".join(f"{column} = EXCLUDED.{column}" for column in updates)
    )


def rows(table, columns):
    for row in spark.table(table).select(*columns).toLocalIterator():
        values = row.asDict(recursive=True)
        yield tuple(values[column] for column in columns) + ("synthetic_wave_2_baseline",)


with psycopg.connect(
    host=host,
    dbname=postgres_database,
    user=username,
    password=credential.token,
    sslmode="require",
) as connection:
    with connection.cursor() as cursor:
        cursor.execute(DDL)
        # Replace only this deterministic fixture set. Keeping stale synthetic
        # rows would make a smaller future fixture non-idempotent, while rows
        # from real intake retain their independent provenance.
        cursor.execute(
            "DELETE FROM adjudications WHERE data_provenance = %s",
            ("synthetic_wave_2_baseline",),
        )
        cursor.execute(
            "DELETE FROM claims WHERE data_provenance = %s",
            ("synthetic_wave_2_baseline",),
        )
        cursor.executemany(
            upsert_sql("claims", CLAIM_COLUMNS, "claim_id"),
            rows(f"`{catalog}`.gold.claims_history", CLAIM_COLUMNS),
        )
        cursor.executemany(
            upsert_sql("adjudications", ADJUDICATION_COLUMNS, "adjudication_id"),
            rows(f"`{catalog}`.gold.adjudications_history", ADJUDICATION_COLUMNS),
        )
        cursor.execute(
            """
            INSERT INTO prior_claims
              (claim_id, coil_id, grade, coating_class, defect_code, defect_narrative,
               claim_date, verdict, approved_amount, narrative_tsv)
            SELECT c.claim_id, c.coil_id, h.grade, h.coating_class, c.defect_code,
                   c.defect_narrative, c.claim_date, a.verdict, a.approved_amount,
                   to_tsvector('english', c.defect_narrative)
            FROM claims c JOIN adjudications a USING (claim_id)
            JOIN reference.heats_coils h USING (coil_id)
            ON CONFLICT (claim_id) DO UPDATE SET
              defect_narrative=EXCLUDED.defect_narrative,
              narrative_tsv=EXCLUDED.narrative_tsv,
              verdict=EXCLUDED.verdict,
              approved_amount=EXCLUDED.approved_amount
            """
        )
        cursor.execute("SELECT count(*) FROM claims")
        claim_count = cursor.fetchone()[0]
        cursor.execute("SELECT count(*) FROM adjudications")
        adjudication_count = cursor.fetchone()[0]
        cursor.execute(
            """
            SELECT c.relname, array_agg(a.attname ORDER BY k.ordinality),
                   CASE c.relreplident
                     WHEN 'f' THEN 'full' WHEN 'd' THEN 'default'
                     WHEN 'n' THEN 'nothing' WHEN 'i' THEN 'index'
                   END
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary
            JOIN unnest(i.indkey) WITH ORDINALITY k(attnum, ordinality) ON true
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
            WHERE n.nspname = 'public'
              AND c.relname IN (
                'claims', 'adjudications', 'outbox', 'settlements',
                'investigation_cases', 'supplier_recovery_cases', 'claims_pending'
              )
            GROUP BY c.relname, c.relreplident
            ORDER BY c.relname
            """
        )
        table_identity = [
            {"table": row[0], "primary_key": row[1], "replica_identity": row[2]}
            for row in cursor.fetchall()
        ]
        cursor.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'vector') ORDER BY extname"
        )
        extensions = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT to_regnamespace('wal2delta') IS NOT NULL")
        native_cdf_enabled = cursor.fetchone()[0]

result = {
    "claims": claim_count,
    "adjudications": adjudication_count,
    "source_claims": spark.table(f"`{catalog}`.gold.claims_history").count(),
    "source_adjudications": spark.table(f"`{catalog}`.gold.adjudications_history").count(),
    "extensions": extensions,
    "native_cdf_enabled": native_cdf_enabled,
    "provenance": "synthetic_wave_2_baseline",
    "tables": table_identity,
}
dbutils.notebook.exit(json.dumps(result, sort_keys=True))
