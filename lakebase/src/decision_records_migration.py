"""Create the append-only decision-record table and widen adjudications.

Idempotent Lakebase migration, run as local runtime Python (psycopg 5432 + SDK
OAuth), always with an explicit profile. It:

* creates ``public.adjudication_decision_records`` — the append-only canonical
  record per adjudication, PK ``(adjudication_id, record_version)``, nested
  structs/arrays as JSONB. It carries NO vector/tsvector columns and is given
  ``REPLICA IDENTITY FULL`` so the EXISTING schema-scoped native CDF config over
  ``public`` streams it (without it, native CDF SKIPS the table — the same reason
  the reference/precedent tables show "Error"/skipped);
* applies the minimal ``adjudications`` widening to carry the recommendation
  fields the agent writes; and
* applies immutability-by-access grants: a writer gets INSERT/SELECT only, and
  UPDATE/DELETE/TRUNCATE are revoked, so a persisted record cannot be mutated.

This migration does NOT reseed and does NOT touch the CDF config — it is additive
and safe to run after native CDF is active (unlike the baseline seed).
"""

from __future__ import annotations

import argparse
import json

import psycopg
from databricks.sdk import WorkspaceClient

DEFAULT_ENDPOINT = (
    "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
)
DEFAULT_DATABASE = "databricks_postgres"

DECISION_RECORD_DDL = """
CREATE TABLE IF NOT EXISTS adjudication_decision_records (
  adjudication_id text NOT NULL,
  claim_id text NOT NULL,
  record_version integer NOT NULL,
  idempotency_key text NOT NULL,
  claim_type text,
  claim_input jsonb NOT NULL,
  spec_provenance jsonb,
  warranty_provenance jsonb,
  spec_params jsonb,
  warranty_terms jsonb,
  freight_cap numeric,
  coil jsonb,
  mtc_measured jsonb,
  conformance jsonb NOT NULL,
  coverage jsonb,
  settlement jsonb NOT NULL,
  duplicate jsonb NOT NULL,
  claimed_amount numeric,
  approved_amount numeric NOT NULL,
  over_claim_flag boolean NOT NULL,
  duplicate_flag boolean NOT NULL,
  deterministic_verdict text NOT NULL,
  deterministic_disposition text NOT NULL,
  recommended_verdict text NOT NULL,
  recommended_disposition text NOT NULL,
  rationale text,
  confidence numeric,
  flags jsonb NOT NULL,
  advisory_risk jsonb,
  precedent jsonb NOT NULL,
  invariant_violations jsonb NOT NULL,
  citations jsonb NOT NULL,
  cited_clause_ids text[] NOT NULL,
  authorities_git_sha text,
  authorities_source_sha256 text NOT NULL,
  agent_model_name text,
  agent_model_version text,
  reasoning_endpoint text,
  prompt_version text NOT NULL,
  schema_version text NOT NULL,
  mlflow_trace_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (adjudication_id, record_version)
);
ALTER TABLE adjudication_decision_records REPLICA IDENTITY FULL;
"""

# Minimal adjudications widening for the recommendation fields the agent writes.
ADJUDICATIONS_MIGRATION = """
ALTER TABLE adjudications
  ADD COLUMN IF NOT EXISTS recommended_disposition text,
  ADD COLUMN IF NOT EXISTS confidence numeric,
  ADD COLUMN IF NOT EXISTS flags jsonb,
  ADD COLUMN IF NOT EXISTS advisory_risk jsonb,
  ADD COLUMN IF NOT EXISTS precedent jsonb,
  ADD COLUMN IF NOT EXISTS idempotency_key text,
  ADD COLUMN IF NOT EXISTS decision_record_version integer,
  ADD COLUMN IF NOT EXISTS recommended_at timestamptz DEFAULT now();
"""


def _grants_sql(writer_role: str | None) -> list[str]:
    """Immutability by access: revoke mutation, grant append+read to the writer.

    UPDATE/DELETE/TRUNCATE are revoked from PUBLIC so no role can mutate a record.
    When a non-owner runtime role is supplied it is granted INSERT/SELECT only and
    explicitly denied UPDATE/DELETE. (A table owner always retains privileges on
    its own table; in a real deployment the agent principal is a non-owner role.)
    """
    statements = [
        "REVOKE UPDATE, DELETE, TRUNCATE ON adjudication_decision_records FROM PUBLIC",
    ]
    if writer_role:
        statements += [
            f'GRANT INSERT, SELECT ON adjudication_decision_records TO "{writer_role}"',
            f'REVOKE UPDATE, DELETE, TRUNCATE ON adjudication_decision_records FROM "{writer_role}"',
        ]
    return statements


def _connection_params(profile: str, endpoint: str, database: str) -> dict:
    client = WorkspaceClient(profile=profile)
    endpoint_details = client.postgres.get_endpoint(name=endpoint)
    credential = client.postgres.generate_database_credential(endpoint=endpoint)
    return {
        "host": endpoint_details.status.hosts.host,
        "dbname": database,
        "user": client.current_user.me().user_name,
        "password": credential.token,
        "sslmode": "require",
    }


_REPLICA_IDENTITY_SQL = """
SELECT CASE c.relreplident
         WHEN 'f' THEN 'full' WHEN 'd' THEN 'default'
         WHEN 'n' THEN 'nothing' WHEN 'i' THEN 'index' END
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = %s
"""
_UNCDCABLE_SQL = """
SELECT count(*) FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_type t ON t.oid = a.atttypid
WHERE n.nspname = 'public' AND c.relname = 'adjudication_decision_records'
  AND t.typname IN ('vector', 'tsvector')
"""


def run(
    profile: str = "fe-bar",
    endpoint: str = DEFAULT_ENDPOINT,
    database: str = DEFAULT_DATABASE,
    writer_role: str | None = None,
) -> dict:
    params = _connection_params(profile, endpoint, database)
    summary: dict = {"grants_applied": []}
    # psycopg3 Connection.execute runs one statement and returns a cursor to read.
    with psycopg.connect(autocommit=True, **params) as conn:
        conn.execute(DECISION_RECORD_DDL)
        conn.execute(ADJUDICATIONS_MIGRATION)
        for statement in _grants_sql(writer_role):
            conn.execute(statement)
            summary["grants_applied"].append(statement)
        row = conn.execute(
            _REPLICA_IDENTITY_SQL, ("adjudication_decision_records",)
        ).fetchone()
        summary["decision_record_replica_identity"] = row[0] if row else None
        summary["decision_record_column_count"] = conn.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'adjudication_decision_records'"
        ).fetchone()[0]
        summary["decision_record_rows"] = conn.execute(
            "SELECT count(*) FROM adjudication_decision_records"
        ).fetchone()[0]
        # Confirm the table carries no type CDF cannot serialize (vector/tsvector).
        summary["uncdcable_columns"] = conn.execute(_UNCDCABLE_SQL).fetchone()[0]
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create decision-record infra in Lakebase"
    )
    parser.add_argument("--profile", default="fe-bar")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--writer-role",
        default=None,
        help="Non-owner Postgres role granted INSERT/SELECT only (immutability by access)",
    )
    args = parser.parse_args()
    summary = run(args.profile, args.endpoint, args.database, args.writer_role)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
