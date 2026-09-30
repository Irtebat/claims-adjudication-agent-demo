"""Re-grant SELECT on the reference.* synced tables to their documented consumers.

Creating or recreating a Lakebase synced table (``scripts/synced_tables.py create`` /
``recreate``) makes the table owned by the creating role with no consumer grants — a
recreate DROPS every prior grant. On the go-live run the app service principal lost
SELECT on ``reference.customer_heat_risk`` and the cockpit 500'd until a manual
``GRANT SELECT`` was restored. This script re-applies those grants idempotently so a
create/recreate is reproducible without a manual step. A routine triggered re-sync
(``synced_tables.py resync``) keeps the existing table and its grants and does NOT
call this script. It grants ONLY SELECT (plus the
prerequisite schema USAGE) — never ownership or write — mirroring
``docs/evidence/app-deploy/grants.sql`` (app SP) and
``docs/evidence/serving-endpoint/README.md`` (serving SP).

Runs against the live Lakebase Postgres endpoint over psycopg as the invoking
superuser (SDK OAuth credential, sslmode=require) — the same connection pattern as
``synced_tables.py``.

Both the app SP and the serving SP are contractually required consumers, so by
default both principals must be resolved or the script errors before touching the
database — it never reports success for a principal it skipped. Pass the explicit
``--allow-single-principal`` opt-out to grant only the principal(s) supplied.

Usage:
    uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
        python lakebase/scripts/regrant_synced_table_selects.py --profile fe-bar
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
REFERENCE_SCHEMA = "reference"

# Documented service-principal ids (= Postgres role names). Overridable via flag/env so
# the flow stays reproducible if a principal is rotated.
#   app SP     -> docs/evidence/app-deploy/grants.sql
#   serving SP -> docs/evidence/serving-endpoint/README.md
DEFAULT_APP_PRINCIPAL = "d5309ee7-a8ea-499f-99d4-4ccbd8369d93"
DEFAULT_SERVING_PRINCIPAL = "47643eb1-dbd5-40a6-a51d-5da6b8e2da7a"

# reference.* synced tables each consumer reads, per the two evidence docs above.
# prior_claims_corpus is the precedent corpus the agent's find_similar_prior_claims and
# the app cockpit read (it replaced the native public.prior_claims table).
APP_TABLES = [
    "heats_coils",
    "mill_test_certs",
    "customers",
    "customer_heat_risk",
    "prior_claims_corpus",
]
SERVING_TABLES = [
    "heats_coils",
    "mill_test_certs",
    "customer_heat_risk",
    "prior_claims_corpus",
]


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


def _regrant(cur: psycopg.Cursor, role: str, tables: list[str]) -> None:
    # USAGE on the schema is a documented prerequisite and idempotent; the table-level
    # SELECT is what a recreate drops. Identifiers are quoted via psycopg.sql so a role
    # id (a UUID) or table name can never break out of the statement.
    cur.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(REFERENCE_SCHEMA), sql.Identifier(role)
        )
    )
    for table in tables:
        cur.execute(
            sql.SQL("GRANT SELECT ON {} TO {}").format(
                sql.Identifier(REFERENCE_SCHEMA, table), sql.Identifier(role)
            )
        )


def _resolve_consumers(app_principal, serving_principal, allow_single=False):
    """Resolve the [(role, tables), ...] to grant, failing safe on a missing principal.

    Both the app SP and the serving SP are contractually required consumers. If either
    is empty/unset this raises ValueError NAMING the missing principal(s) and grants
    nothing — the script must never silently skip a required consumer yet report
    success. ``allow_single=True`` is the explicit opt-out: grant only whichever
    principals are supplied (still erroring if none are).
    """
    candidates = [
        ("app", app_principal, APP_TABLES),
        ("serving", serving_principal, SERVING_TABLES),
    ]
    if not allow_single:
        missing = [name for name, principal, _ in candidates if not principal]
        if missing:
            raise ValueError(
                "Required principal(s) not set: "
                + ", ".join(missing)
                + ". Both the app and serving service principals are required consumers of "
                "the reference.* synced tables; set --app-principal / --serving-principal "
                "(or APP_SP_PRINCIPAL / SERVING_SP_PRINCIPAL), or pass "
                "--allow-single-principal to deliberately grant only the ones supplied."
            )
    consumers = [
        (principal, tables) for _, principal, tables in candidates if principal
    ]
    if not consumers:
        raise ValueError(
            "No principals supplied; set --app-principal and/or --serving-principal "
            "(or their APP_SP_PRINCIPAL / SERVING_SP_PRINCIPAL env vars)."
        )
    return consumers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fe-bar")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument(
        "--app-principal",
        default=os.environ.get("APP_SP_PRINCIPAL", DEFAULT_APP_PRINCIPAL),
        help="App service-principal id (Postgres role) for the cockpit reads",
    )
    parser.add_argument(
        "--serving-principal",
        default=os.environ.get("SERVING_SP_PRINCIPAL", DEFAULT_SERVING_PRINCIPAL),
        help="Serving service-principal id (Postgres role)",
    )
    parser.add_argument(
        "--allow-single-principal",
        action="store_true",
        help=(
            "Explicit opt-out of the both-principals requirement: grant only the "
            "principal(s) supplied. Off by default — a missing app or serving SP is an "
            "error, never a silent skip."
        ),
    )
    args = parser.parse_args()

    consumers = _resolve_consumers(
        args.app_principal, args.serving_principal, args.allow_single_principal
    )

    applied = []
    with (
        _connect(args.profile, args.endpoint, args.database) as conn,
        conn.cursor() as cur,
    ):
        for role, tables in consumers:
            _regrant(cur, role, tables)
            applied.append({"principal": role, "tables": tables})
    print(
        json.dumps(
            {"regranted": True, "schema": REFERENCE_SCHEMA, "grants": applied},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
