# Databricks notebook source
# ruff: noqa: F821
"""Wave 8 operational-plane migration: drop the deprecated pending queue and grant
the Wave 6 application service principal the privileges the event layer needs.

- DROP ``public.claims_pending`` (user-confirmed removal; empty and out of the DDL).
- GRANT the SP role the minimum privileges so the endpoint's writer can INSERT the
  outbox row, the relay can read/mark it published, and the consumers can upsert
  their tables. Grants are idempotent; the DROP is ``IF EXISTS``.

Runs as the deploying admin (mints a short-lived OAuth credential like
``lakebase/src/setup_and_seed.py``); no password/token is stored. The SP role name is
the SP's application UUID (from the Wave 6 serving-endpoint evidence, not a secret).
"""

import json

import psycopg
from databricks.sdk import WorkspaceClient

dbutils.widgets.text("endpoint", "projects/fe-bar-operational-plane/branches/production/endpoints/primary")
dbutils.widgets.text("postgres_database", "databricks_postgres")
dbutils.widgets.text("sp_role", "47643eb1-dbd5-40a6-a51d-5da6b8e2da7a")

endpoint = dbutils.widgets.get("endpoint")
postgres_database = dbutils.widgets.get("postgres_database")
sp_role = dbutils.widgets.get("sp_role")

w = WorkspaceClient()
endpoint_details = w.postgres.get_endpoint(name=endpoint)
credential = w.postgres.generate_database_credential(endpoint=endpoint)
username = w.current_user.me().user_name
host = endpoint_details.status.hosts.host

# Role name is a UUID with hyphens -> must be double-quoted as an SQL identifier.
role = '"' + sp_role.replace('"', '""') + '"'

DROP_SQL = "DROP TABLE IF EXISTS public.claims_pending;"

GRANT_SQL = f"""
GRANT INSERT ON public.outbox TO {role};
GRANT SELECT, UPDATE ON public.outbox TO {role};
GRANT SELECT, INSERT, UPDATE ON public.settlements TO {role};
GRANT SELECT, INSERT, UPDATE ON public.investigation_cases TO {role};
GRANT SELECT, INSERT, UPDATE ON public.supplier_recovery_cases TO {role};
"""

with psycopg.connect(
    host=host,
    dbname=postgres_database,
    user=username,
    password=credential.token,
    sslmode="require",
) as connection:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.claims_pending') IS NOT NULL")
        claims_pending_existed = cursor.fetchone()[0]
        cursor.execute(DROP_SQL)
        cursor.execute(GRANT_SQL)
        # Confirm the SP now holds the outbox INSERT the endpoint's writer needs.
        cursor.execute(
            "SELECT has_table_privilege(%s, 'public.outbox', 'INSERT')",
            (sp_role,),
        )
        sp_can_insert_outbox = cursor.fetchone()[0]
        cursor.execute("SELECT to_regclass('public.claims_pending') IS NOT NULL")
        claims_pending_exists_after = cursor.fetchone()[0]

result = {
    "claims_pending_existed": claims_pending_existed,
    "claims_pending_exists_after": claims_pending_exists_after,
    "sp_role": sp_role,
    "sp_can_insert_outbox": sp_can_insert_outbox,
    "granted": [
        "outbox:INSERT",
        "outbox:SELECT,UPDATE",
        "settlements:SELECT,INSERT,UPDATE",
        "investigation_cases:SELECT,INSERT,UPDATE",
        "supplier_recovery_cases:SELECT,INSERT,UPDATE",
    ],
}
dbutils.notebook.exit(json.dumps(result, sort_keys=True))
