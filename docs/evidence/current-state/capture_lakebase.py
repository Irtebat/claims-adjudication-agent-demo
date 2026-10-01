"""Read-only Lakebase evidence capture used on 2026-10-01."""

import json

import psycopg
from databricks.sdk import WorkspaceClient

PROFILE = "fe-bar"
ENDPOINT = "projects/fe-bar-operational-plane/branches/production/endpoints/primary"

w = WorkspaceClient(profile=PROFILE)
host = w.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
token = w.postgres.generate_database_credential(endpoint=ENDPOINT).token
queries = {
    "count": "SELECT count(*) FROM reference.prior_claims_corpus",
    "indexes": (
        "SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname='reference' AND tablename='prior_claims_corpus' "
        "ORDER BY indexname"
    ),
    "grants": (
        "SELECT grantee, table_schema, table_name, privilege_type "
        "FROM information_schema.role_table_grants "
        "WHERE table_schema='reference' AND privilege_type='SELECT' "
        "ORDER BY grantee, table_name"
    ),
}
output = {}
with psycopg.connect(
    host=host,
    dbname="databricks_postgres",
    user=w.current_user.me().user_name,
    password=token,
    sslmode="require",
) as connection:
    with connection.cursor() as cursor:
        for name, query in queries.items():
            cursor.execute(query)
            output[name] = {
                "columns": [column.name for column in cursor.description],
                "rows": cursor.fetchall(),
            }
print(json.dumps(output, indent=2, default=str))
