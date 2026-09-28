"""Apply the Wave 7 human-finalization columns to live Lakebase, idempotently.

Runs ONLY the two additive ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` statements
that ``lakebase/src/setup_and_seed.py`` declares, against the live Lakebase Postgres
endpoint over psycopg (port 5432, SDK OAuth credential, sslmode=require). This is the
minimal, non-destructive way to apply the migration after native CDF is active — it
does NOT rerun ``setup_and_seed`` (which also re-seeds the synthetic baseline), per
``docs/evidence/pipelines-lakebase-cleanup/POST-MERGE-LIVE-STEPS.md``.

Both columns are captured by native Lakebase CDF, so adding them triggers a one-time
re-snapshot; follow with a pipeline full-refresh so silver/gold re-materialize.

Usage:
    uv run --project eval python lakebase/scripts/apply_finalization_columns.py \
        --profile fe-bar
"""

from __future__ import annotations

import argparse
import json

import psycopg
from databricks.sdk import WorkspaceClient

DEFAULT_ENDPOINT = "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
DEFAULT_DATABASE = "databricks_postgres"

MIGRATION_SQL = """
ALTER TABLE public.adjudications
  ADD COLUMN IF NOT EXISTS decided_by text,
  ADD COLUMN IF NOT EXISTS override_reason text;
ALTER TABLE public.adjudication_decision_records
  ADD COLUMN IF NOT EXISTS decided_by text,
  ADD COLUMN IF NOT EXISTS override_reason text;
"""

VERIFY_SQL = """
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('adjudications', 'adjudication_decision_records')
  AND column_name IN ('decided_by', 'override_reason')
ORDER BY table_name, column_name
"""


def _connect(profile: str, endpoint: str, database: str) -> psycopg.Connection:
    client = WorkspaceClient(profile=profile)
    details = client.postgres.get_endpoint(name=endpoint)
    credential = client.postgres.generate_database_credential(endpoint=endpoint)
    return psycopg.connect(
        host=details.status.hosts.host,
        dbname=database,
        user=client.current_user.me().user_name,
        password=credential.token,
        sslmode="require",
        autocommit=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fe-bar")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    args = parser.parse_args()

    with _connect(args.profile, args.endpoint, args.database) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION_SQL)
        cur.execute(VERIFY_SQL)
        verified = [{"table": t, "column": c, "data_type": d} for t, c, d in cur.fetchall()]
    print(json.dumps({"applied": True, "columns": verified}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
