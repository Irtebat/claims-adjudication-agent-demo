"""Apply the Steel Claims Cockpit Lakebase grants to an app service principal.

The app service-principal client ID is also its Lakebase Postgres role name. App
service principals change when the app is provisioned in another workspace, so the
principal and Databricks CLI profile are required arguments.

Usage:
    uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
        python app/scripts/setup_app_sp.py \
        --principal <app-service-principal-client-id> --profile <profile>
"""

from __future__ import annotations

import argparse
import json

import psycopg
from psycopg import sql

DEFAULT_ENDPOINT = (
    "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
)
DEFAULT_DATABASE = "databricks_postgres"

PUBLIC_SELECT_TABLES = [
    "claims",
    "adjudications",
    "adjudication_decision_records",
    "spec_params",
    "spec_clauses",
    "warranty_terms",
    "warranty_clauses",
]
REFERENCE_SELECT_TABLES = [
    "heats_coils",
    "mill_test_certs",
    "customers",
    "customer_heat_risk",
    "prior_claims_corpus",
]
WRITE_GRANTS = [
    ("adjudications", ["INSERT", "UPDATE"]),
    ("adjudication_decision_records", ["INSERT", "UPDATE"]),
    ("outbox", ["INSERT"]),
]


def _connect(profile: str, endpoint: str, database: str) -> psycopg.Connection:
    # Keep the SDK import local so unit tests can import this module without the SDK.
    from databricks.sdk import WorkspaceClient

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


def _grant(cur: psycopg.Cursor, principal: str, database: str) -> None:
    role = sql.Identifier(principal)
    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), role
        )
    )
    for schema in ("public", "reference"):
        cur.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), role
            )
        )
    for schema, tables in (
        ("public", PUBLIC_SELECT_TABLES),
        ("reference", REFERENCE_SELECT_TABLES),
    ):
        for table in tables:
            cur.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(
                    sql.Identifier(schema, table), role
                )
            )
    for table, privileges in WRITE_GRANTS:
        cur.execute(
            sql.SQL("GRANT {} ON {} TO {}").format(
                sql.SQL(", ").join(map(sql.SQL, privileges)),
                sql.Identifier("public", table),
                role,
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--principal",
        required=True,
        help="App service-principal client ID (= Postgres role name)",
    )
    parser.add_argument("--profile", required=True, help="Databricks CLI profile")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    args = parser.parse_args()

    with (
        _connect(args.profile, args.endpoint, args.database) as conn,
        conn.cursor() as cur,
    ):
        _grant(cur, args.principal, args.database)

    print(
        json.dumps(
            {
                "granted": True,
                "principal": args.principal,
                "database": args.database,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
