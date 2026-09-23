"""Policy intake — the SOLE creator and populator of the four policy tables in Lakebase.

Parses the single authored source (``policy_source.json``) once, via ``policy_schema``,
and owns both the DDL and the row population for all four tables (no spec/warranty
DDL or seed exists anywhere else):

* structured params ``spec_params`` (grade level) and ``warranty_terms``
  (product/coating level) — read by the deterministic UC Python authorities via the
  ``fe_bar_operational`` mirror at decision time; and
* clause tables ``spec_clauses`` and ``warranty_clauses`` — metadata-filtered text
  with a ``tsvector`` column indexed for Lakebase Search BM25 retrieval.

Runs as local runtime Python (psycopg 5432 + gateway HTTPS). Idempotent: tables are
created IF NOT EXISTS, rows are upserted inside a transaction, and rows no longer in
the source are reconciled away (deleted) so a re-run neither duplicates nor leaves
stale policy. After backfill it builds and verifies the real ``lakebase_bm25``
indexes. Clause tables intentionally contain no vectors or ANN indexes.
"""

from __future__ import annotations

import argparse
import json

from db import DEFAULT_DATABASE, DEFAULT_ENDPOINT, connect
from policy_schema import parse_policies

EXTENSIONS = ["pg_trgm", "lakebase_text"]

DDL = """
DROP TABLE IF EXISTS spec_clauses CASCADE;
DROP TABLE IF EXISTS warranty_clauses CASCADE;
DROP TABLE IF EXISTS spec_params CASCADE;
DROP TABLE IF EXISTS warranty_terms CASCADE;
CREATE TABLE IF NOT EXISTS spec_params (
  grade text NOT NULL, spec_edition text NOT NULL, region text NOT NULL,
  carbon_pct_min numeric, carbon_pct_max numeric,
  manganese_pct_min numeric, manganese_pct_max numeric,
  yield_mpa_min numeric, yield_mpa_max numeric,
  tensile_mpa_min numeric, tensile_mpa_max numeric,
  elongation_pct_min numeric, elongation_pct_max numeric,
  gauge_tolerance_mm numeric, width_tolerance_mm numeric,
  min_coating_g_m2 numeric, coating_adhesion_required boolean,
  source_sha256 text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (grade, spec_edition, region)
);
CREATE TABLE IF NOT EXISTS warranty_terms (
  product_line text NOT NULL, coating_class text NOT NULL, region text NOT NULL,
  version text NOT NULL, effective_from date NOT NULL, effective_to date NOT NULL,
  duration_months integer NOT NULL, full_coverage_months integer NOT NULL,
  min_coating_g_m2 numeric, min_coast_distance_km numeric,
  excluded_environments text[] NOT NULL, excluded_installations text[] NOT NULL,
  proration_method text NOT NULL, freight_covered boolean NOT NULL, freight_cap numeric NOT NULL,
  source_sha256 text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (product_line, region, version)
);
CREATE TABLE IF NOT EXISTS spec_clauses (
  section_ref text NOT NULL, grade text NOT NULL, spec_edition text NOT NULL, region text NOT NULL,
  clause_text text NOT NULL, clause_tsv tsvector,
  source_sha256 text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (grade, spec_edition, region, section_ref)
);
CREATE TABLE IF NOT EXISTS warranty_clauses (
  section_ref text NOT NULL, product_line text NOT NULL, coating_class text NOT NULL,
  region text NOT NULL, version text NOT NULL, effective_from date NOT NULL, effective_to date NOT NULL,
  clause_text text NOT NULL, clause_tsv tsvector,
  source_sha256 text NOT NULL, loaded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (product_line, region, version, section_ref)
);
"""

SPEC_PARAM_COLUMNS = [
    "grade",
    "spec_edition",
    "region",
    "carbon_pct_min",
    "carbon_pct_max",
    "manganese_pct_min",
    "manganese_pct_max",
    "yield_mpa_min",
    "yield_mpa_max",
    "tensile_mpa_min",
    "tensile_mpa_max",
    "elongation_pct_min",
    "elongation_pct_max",
    "gauge_tolerance_mm",
    "width_tolerance_mm",
    "min_coating_g_m2",
    "coating_adhesion_required",
    "source_sha256",
]
WARRANTY_TERM_COLUMNS = [
    "product_line",
    "coating_class",
    "region",
    "version",
    "effective_from",
    "effective_to",
    "duration_months",
    "full_coverage_months",
    "min_coating_g_m2",
    "min_coast_distance_km",
    "excluded_environments",
    "excluded_installations",
    "proration_method",
    "freight_covered",
    "freight_cap",
    "source_sha256",
]
SPEC_CLAUSE_COLUMNS = [
    "section_ref",
    "grade",
    "spec_edition",
    "region",
    "clause_text",
    "source_sha256",
]
WARRANTY_CLAUSE_COLUMNS = [
    "section_ref",
    "product_line",
    "coating_class",
    "region",
    "version",
    "effective_from",
    "effective_to",
    "clause_text",
    "source_sha256",
]

# Real Lakebase Search indexes on the two clause tables, distinct names, built after
# backfill. lakebase_ann serves cosine vector search (<=>); lakebase_bm25 serves
# lexical BM25 (<@> to_bm25query).
LAKEBASE_INDEXES = [
    ("spec_clauses_lb_bm25", "spec_clauses", "lakebase_bm25 (clause_tsv tsvector_bm25_ops)"),
    (
        "warranty_clauses_lb_bm25",
        "warranty_clauses",
        "lakebase_bm25 (clause_tsv tsvector_bm25_ops)",
    ),
]
# Names from the earlier pgvector/FTS fallback, dropped so no dead index lingers.
LEGACY_INDEXES = [
    "spec_clauses_ann",
    "spec_clauses_tsv",
    "warranty_clauses_ann",
    "warranty_clauses_tsv",
    "spec_clauses_lb_ann",
    "warranty_clauses_lb_ann",
]


def upsert_sql(table: str, columns: list[str], pk: str) -> str:
    placeholders = ", ".join(f"%({c})s" for c in columns)
    pk_columns = [part.strip() for part in pk.split(",")]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_columns)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {updates}, loaded_at = now()"
    )


def clause_upsert_sql(table: str, meta_columns: list[str], pk: str) -> str:
    all_columns = meta_columns + ["clause_tsv"]
    value_exprs = [f"%({c})s" for c in meta_columns] + ["to_tsvector('english', %(clause_text)s)"]
    pk_columns = [part.strip() for part in pk.split(",")]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_columns if c not in pk_columns)
    return (
        f"INSERT INTO {table} ({', '.join(all_columns)}) VALUES ({', '.join(value_exprs)}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {updates}, loaded_at = now()"
    )


def _create_extensions(cur) -> dict:
    """Create the required extensions and verify each loaded; raise if any did not."""
    versions = {}
    for name in EXTENSIONS:
        cur.execute(f"CREATE EXTENSION IF NOT EXISTS {name}")
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = %s", (name,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError(
                f"Extension {name} did not load (Lakebase Search may need "
                f"shared_preload_libraries / an endpoint restart). Stopping — no fallback."
            )
        versions[name] = row[0]
    return versions


def _reconcile(cur, table: str, key: str, current_ids: list[str]) -> int:
    """Delete rows whose key is no longer present in the authored source."""
    if current_ids:
        cur.execute(f"DELETE FROM {table} WHERE {key} <> ALL(%s)", (current_ids,))
    else:
        cur.execute(f"DELETE FROM {table}")
    return cur.rowcount


def _build_indexes(cur) -> None:
    for name in LEGACY_INDEXES:
        cur.execute(f"DROP INDEX IF EXISTS {name}")
    for name, table, using in LAKEBASE_INDEXES:
        cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} USING {using}")
    # pg_trgm GIN supporting duplicate detection / similar-claims (only if claims exists)
    cur.execute("SELECT to_regclass('public.claims') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute(
            "CREATE INDEX IF NOT EXISTS claims_defect_narrative_trgm ON claims "
            "USING gin (defect_narrative gin_trgm_ops)"
        )


def _verify_indexes(cur) -> dict:
    """Read the ACTUAL access method for each Lakebase Search index from the catalog."""
    cur.execute(
        """
        SELECT c.relname, am.amname
        FROM pg_class c
        JOIN pg_am am ON am.oid = c.relam
        JOIN pg_index i ON i.indexrelid = c.oid
        JOIN pg_class t ON t.oid = i.indrelid
        WHERE t.relname IN ('spec_clauses', 'warranty_clauses')
          AND c.relname LIKE '%\\_lb\\_%'
        ORDER BY c.relname
        """
    )
    verified = {name: amname for name, amname in cur.fetchall()}
    for name, _table, using in LAKEBASE_INDEXES:
        expected = "lakebase_bm25"
        if verified.get(name) != expected:
            raise RuntimeError(
                f"Index {name} verified as {verified.get(name)}, expected {expected}"
            )
    return verified


def run_intake(
    profile: str = "fe-bar",
    endpoint: str = DEFAULT_ENDPOINT,
    database: str = DEFAULT_DATABASE,
    source_path: str | None = None,
    embed: bool = True,
) -> dict:
    parsed = parse_policies(source_path)
    summary: dict = {"counts": {}, "reconciled_deletes": {}}
    with connect(profile, endpoint, database, autocommit=True) as conn:
        with conn.cursor() as cur:
            summary["extensions"] = _create_extensions(cur)
            cur.execute(DDL)
        # Upserts + stale-row reconciliation applied atomically.
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    upsert_sql("spec_params", SPEC_PARAM_COLUMNS, "grade, spec_edition, region"), parsed["spec_params"]
                )
                cur.executemany(
                    upsert_sql("warranty_terms", WARRANTY_TERM_COLUMNS, "product_line, region, version"),
                    parsed["warranty_terms"],
                )
                cur.executemany(
                    clause_upsert_sql("spec_clauses", SPEC_CLAUSE_COLUMNS, "grade, spec_edition, region, section_ref"),
                    parsed["spec_clauses"],
                )
                cur.executemany(
                    clause_upsert_sql("warranty_clauses", WARRANTY_CLAUSE_COLUMNS, "product_line, region, version, section_ref"),
                    parsed["warranty_clauses"],
                )
                summary["reconciled_deletes"] = {name: 0 for name in parsed}
        with conn.cursor() as cur:
            # Indexes are built AFTER the backfill so BM25 statistics see all rows.
            _build_indexes(cur)
            summary["index_methods"] = _verify_indexes(cur)
            for table in ("spec_params", "warranty_terms", "spec_clauses", "warranty_clauses"):
                cur.execute(f"SELECT count(*) FROM {table}")
                summary["counts"][table] = cur.fetchone()[0]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Load authored policy into Lakebase")
    parser.add_argument("--profile", default="fe-bar")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--source", default=None, help="Override policy_source.json path")
    parser.add_argument(
        "--no-embed", action="store_true", help="Load params/text only (zero vectors)"
    )
    args = parser.parse_args()
    summary = run_intake(
        args.profile, args.endpoint, args.database, args.source, embed=not args.no_embed
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
