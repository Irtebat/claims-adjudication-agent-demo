"""Grant the Steel Claims Cockpit App service principal its Lakebase permissions.

The app bundle auto-provisions the App service principal when it deploys
(``databricks bundle deploy`` creates the app and its SP), so this script does
NOT create the principal. It takes the app SP client id as ``--app-principal``
and applies the least-privilege Lakebase Postgres grants the app needs: schema
USAGE, the cockpit read set, the finalize-transaction writes
(UPDATE ``public.adjudications``, INSERT ``public.adjudication_decision_records``,
SELECT+INSERT ``public.outbox`` — SELECT is required because the outbox write uses
``ON CONFLICT (event_id) DO NOTHING``). The grant set mirrors
``docs/evidence/app-deploy/grants.sql``.

The ``reference`` schema and its synced tables only exist once synced tables are
created, which itself needs this SP's id. On a fresh workspace the ``reference.*``
grants whose target is missing are therefore skipped with a warning (the run still
exits 0); create the synced tables, then re-run this script. Every other grant
fails loud. The ``reference.*`` SELECTs are also reapplied by
``lakebase/scripts/regrant_synced_table_selects.py`` whenever a synced table is
created or recreated.

Grants are applied over psycopg with a databricks-sdk-minted database credential
(``sslmode=require``); the Postgres role name is the app SP client id. GRANTs are
idempotent, so re-running issues the same statements with no side effects. The
principal is printed to stderr before any grant, and a JSON summary of granted vs
skipped grants is printed at the end.

Usage:
    uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
        python app/scripts/setup_app_sp.py --profile fe-bar-ir-2026 \
        --app-principal <app-sp-client-id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg
from psycopg import sql

DEFAULT_ENDPOINT = (
    "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
)
DEFAULT_DATABASE = "databricks_postgres"

# The Lakebase schema holding the synced tables; grants on it are existence-checked.
SYNCED_SCHEMA = "reference"

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
    # outbox needs SELECT in ADDITION to INSERT. The finalize outbox write is
    # `INSERT ... ON CONFLICT (event_id) DO NOTHING` (first-write-wins idempotency),
    # and Postgres requires SELECT privilege on every column named in an ON CONFLICT
    # arbiter target — here `event_id`. With INSERT-only, every finalize failed at the
    # outbox write with `permission denied for table outbox`, rolled back, and returned
    # HTTP 500 (adjudications/adjudication_decision_records escaped this only because
    # they are already in the read-grant set above). SELECT is the minimum the existing
    # ON CONFLICT semantics require; no UPDATE/DELETE is granted.
    ("SELECT, INSERT", ("table", "public", "outbox")),
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


def _is_synced(target: tuple) -> bool:
    # The reference schema and its tables are created by synced-table creation, which
    # itself needs this SP's id, so on a fresh workspace they may not exist yet.
    return target[0] in ("schema", "table") and target[1] == SYNCED_SCHEMA


def _exists(cur: psycopg.Cursor, target: tuple) -> bool:
    # to_regnamespace / to_regclass return NULL (never raise) for a missing object,
    # independent of the connecting user's privileges on it.
    if target[0] == "schema":
        cur.execute("SELECT to_regnamespace(%s) IS NOT NULL", (f'"{target[1]}"',))
    else:
        cur.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f'"{target[1]}"."{target[2]}"',)
        )
    return bool(cur.fetchone()[0])


def apply_grants(cur: psycopg.Cursor, role: str) -> dict:
    """Issue every GRANT for the app SP; return the granted and skipped labels.

    A ``reference.*`` (synced-table) target that does not exist yet is skipped rather
    than aborting the run; every other grant, and any grant on an existing synced
    table, fails loud.
    """
    granted, skipped = [], []
    for privileges, target in GRANTS:
        label = f"{privileges} on {'.'.join(target[1:])}"
        if _is_synced(target) and not _exists(cur, target):
            skipped.append(label)
            continue
        _grant(cur, role, privileges, target)
        granted.append(label)
    return {"granted": granted, "skipped": skipped}


def warn_skipped(skipped: list[str]) -> None:
    """Warn on stderr about synced-table grants skipped because the target is missing."""
    if not skipped:
        return
    print(
        f"WARNING: skipped {len(skipped)} grant(s) on missing synced-table targets: "
        f"{', '.join(skipped)}. Create synced tables, then re-run this script.",
        file=sys.stderr,
        flush=True,
    )


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
    print(f"app SP principal: {role}", file=sys.stderr, flush=True)
    with (
        _connect(args.profile, args.endpoint, args.database) as conn,
        conn.cursor() as cur,
    ):
        result = apply_grants(cur, role)
    warn_skipped(result["skipped"])
    print(
        json.dumps(
            {
                "principal": role,
                "granted": result["granted"],
                "skipped": result["skipped"],
                "complete": not result["skipped"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
