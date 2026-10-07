"""Grant the Steel Claims Cockpit App service principal its Lakebase permissions.

The app bundle auto-provisions the App service principal when it deploys
(``databricks bundle deploy`` creates the app and its SP), so this script does
NOT create the principal. It takes the app SP client id as ``--app-principal``
and applies the least-privilege Lakebase Postgres grants the app needs: schema
USAGE, the cockpit read set, the finalize-transaction writes
(UPDATE ``public.adjudications``, INSERT ``public.adjudication_decision_records``,
INSERT ``public.outbox``). The grant set mirrors
``docs/evidence/app-deploy/grants.sql``.

The ``reference.*`` synced-table SELECTs are also reapplied by
``lakebase/scripts/regrant_synced_table_selects.py`` whenever a synced table is
created or recreated; this script establishes the full initial grant and
assumes the ``reference`` synced tables already exist.

Grants are applied over psycopg with a databricks-sdk-minted database credential
(``sslmode=require``); the Postgres role name is the app SP client id. GRANTs are
idempotent, so re-running issues the same statements with no side effects.

Usage:
    uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
        python app/scripts/setup_app_sp.py --profile fe-bar-ir-2026 \
        --app-principal <app-sp-client-id>
"""

from __future__ import annotations

import argparse
import json
import os

import psycopg
from psycopg import sql

DEFAULT_ENDPOINT = (
    "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
)
DEFAULT_DATABASE = "databricks_postgres"

# The app SP grant set, in the exact order of docs/evidence/app-deploy/grants.sql.
# Each entry is (privileges, target) where target is one of:
#   ("schema", <schema>)            -> GRANT <priv> ON SCHEMA <schema>
#   ("table", <schema>, <table>)    -> GRANT <priv> ON <schema>.<table>
GRANTS: list[tuple[str, tuple]] = [
    # Schema usage (required for any table access under these schemas).
    ("USAGE", ("schema", "public")),
    ("USAGE", ("schema", "reference")),
    # Read grants (public).
    ("SELECT", ("table", "public", "claims")),
    ("SELECT", ("table", "public", "adjudications")),
    ("SELECT", ("table", "public", "adjudication_decision_records")),
    ("SELECT", ("table", "public", "spec_params")),
    ("SELECT", ("table", "public", "spec_clauses")),
    ("SELECT", ("table", "public", "warranty_terms")),
    ("SELECT", ("table", "public", "warranty_clauses")),
    # Read grants (reference synced tables the cockpit context query reads).
    ("SELECT", ("table", "reference", "heats_coils")),
    ("SELECT", ("table", "reference", "mill_test_certs")),
    ("SELECT", ("table", "reference", "customers")),
    ("SELECT", ("table", "reference", "customer_heat_risk")),
    ("SELECT", ("table", "reference", "prior_claims_corpus")),
    # Write grants for the finalize transaction.
    ("INSERT, UPDATE", ("table", "public", "adjudications")),
    ("INSERT, UPDATE", ("table", "public", "adjudication_decision_records")),
    ("INSERT", ("table", "public", "outbox")),
]


def _target(target: tuple) -> sql.Composable:
    kind = target[0]
    if kind == "schema":
        return sql.SQL("SCHEMA {}").format(sql.Identifier(target[1]))
    if kind == "table":
        return sql.SQL("{}").format(sql.Identifier(target[1], target[2]))
    raise ValueError(f"unknown grant target kind: {kind}")


def _grant(cur: psycopg.Cursor, role: str, privileges: str, target: tuple) -> None:
    # ``privileges`` is a fixed keyword literal from GRANTS, never user input, so it
    # is embedded as SQL text; identifiers (role, schema, table) are quoted via
    # psycopg.sql so a client id (a UUID) or object name can never break the statement.
    cur.execute(
        sql.SQL("GRANT {privileges} ON {target} TO {role}").format(
            privileges=sql.SQL(privileges),
            target=_target(target),
            role=sql.Identifier(role),
        )
    )


def apply_grants(cur: psycopg.Cursor, role: str) -> list[str]:
    """Issue every GRANT for the app SP and return the applied privilege labels."""
    applied = []
    for privileges, target in GRANTS:
        _grant(cur, role, privileges, target)
        applied.append(f"{privileges} on {'.'.join(target[1:])}")
    return applied


def _resolve_app_principal(value: str | None) -> str:
    """Return the app SP client id or raise if it was not supplied.

    The id is required and never defaulted: the app SP is auto-provisioned on
    deploy and its client id must be passed explicitly so the grants target the
    live principal.
    """
    if not value:
        raise ValueError(
            "App service-principal client id is required: pass --app-principal "
            "(or set APP_SP_PRINCIPAL). The app SP is auto-provisioned on deploy; "
            "read its client id from `databricks apps get <APP_NAME>` "
            "(service_principal_client_id)."
        )
    return value


def _connect(profile: str, endpoint: str, database: str) -> psycopg.Connection:
    # Imported here (not at module top) so the module — and thus its unit tests, which
    # mock _connect — imports without the Databricks SDK installed.
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fe-bar-ir-2026")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--app-principal",
        default=os.environ.get("APP_SP_PRINCIPAL"),
        help="App service-principal client id (= Postgres role) to grant",
    )
    args = parser.parse_args()

    role = _resolve_app_principal(args.app_principal)
    with (
        _connect(args.profile, args.endpoint, args.database) as conn,
        conn.cursor() as cur,
    ):
        applied = apply_grants(cur, role)
    print(
        json.dumps(
            {"granted": True, "principal": role, "grants": applied},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
