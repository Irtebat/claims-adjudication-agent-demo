"""Provision the claims-adjudication serving service principal end to end.

Idempotent prerequisite for ``agent/src/deploy_agent.py``. It establishes every
resource the deployed serving endpoint authenticates and reads with:

1. The account service principal ``claims-adjudication-serving`` and its
   assignment to the workspace (the ``workspace-access`` entitlement, i.e. the
   USER workspace permission).
2. An OAuth (M2M) secret for that service principal.
3. The Lakebase OAuth role ``claims-agent-sp`` whose ``postgres_role`` is the
   service principal's application UUID.
4. The service principal's Lakebase table grants: CONNECT, schema USAGE, the
   agent read set, SELECT/INSERT/UPDATE on ``public.adjudications``, and
   SELECT/INSERT on ``public.adjudication_decision_records``.
5. Unity Catalog ``EXECUTE`` on the governed model functions
   ``system.ai.databricks-gpt-5-4`` and ``system.ai.gte_large_en_v1_5``.
6. The ``claims-agent`` secret scope with ``app-sp-client-id``,
   ``app-sp-client-secret``, and ``lakebase-db-user`` — the references
   ``deploy_agent.py`` injects into the endpoint. ``lakebase-db-user`` is the
   Lakebase role, i.e. the service principal's application UUID.

The account steps use the account profile; the workspace, Lakebase, and Unity
Catalog steps use the workspace profile. Lakebase grants run over psycopg with a
databricks-sdk-minted database credential (``sslmode=require``); the Postgres
role name is the service principal's application UUID. Unity Catalog EXECUTE
grants run as SQL on a serverless warehouse. Each resource is detected first and
skipped when it already exists; GRANTs are idempotent. The resulting service
principal application id is printed at the end.

The ``reference.*`` synced-table SELECTs this script grants are also reapplied by
``lakebase/scripts/regrant_synced_table_selects.py`` whenever a synced table is
created or recreated; this script establishes the full initial grant and assumes
the ``reference`` synced tables already exist.

Usage:
    uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
        python agent/scripts/setup_serving_sp.py \
        --profile fe-bar --account-profile <account-profile>
"""

from __future__ import annotations

import argparse
import json

import psycopg
from psycopg import sql

PROJECT = "fe-bar-operational-plane"
BRANCH = f"projects/{PROJECT}/branches/production"
ENDPOINT = f"{BRANCH}/endpoints/primary"
DATABASE = "databricks_postgres"

DISPLAY_NAME = "claims-adjudication-serving"
ROLE_ID = "claims-agent-sp"
SECRET_SCOPE = "claims-agent"
SECRET_CLIENT_ID_KEY = "app-sp-client-id"
SECRET_CLIENT_SECRET_KEY = "app-sp-client-secret"
SECRET_DB_USER_KEY = "lakebase-db-user"

# Governed model functions the agent reasons and embeds with, granted EXECUTE in
# Unity Catalog. gte_large_en_v1_5 is a bare identifier; databricks-gpt-5-4 is
# backtick-quoted because of its hyphens, as is the service-principal grantee.
UC_REASONING_FUNCTION = "databricks-gpt-5-4"
UC_EMBEDDING_FUNCTION = "gte_large_en_v1_5"

# The Lakebase role's expected identity, matched when the role already exists so grants
# never land on a role backed by a different principal or auth method.
ROLE_IDENTITY_TYPE = "SERVICE_PRINCIPAL"
ROLE_AUTH_METHOD = "LAKEBASE_OAUTH_V1"

# The serving SP Lakebase grant set, following docs/evidence/serving-endpoint/README.md
# for the public reads and writes and the serving read set in
# lakebase/scripts/regrant_synced_table_selects.py (SERVING_TABLES) for the reference.*
# synced tables, which includes the precedent corpus prior_claims_corpus that
# find_similar_prior_claims reads. Each entry is (privileges, target) where target is
# one of ("database", <db>), ("schema", <schema>), or ("table", <schema>, <table>).
GRANTS: list[tuple[str, tuple]] = [
    ("CONNECT", ("database", DATABASE)),
    ("USAGE", ("schema", "public")),
    ("USAGE", ("schema", "reference")),
    ("SELECT", ("table", "public", "claims")),
    ("SELECT", ("table", "public", "spec_params")),
    ("SELECT", ("table", "public", "warranty_terms")),
    ("SELECT", ("table", "public", "spec_clauses")),
    ("SELECT", ("table", "public", "warranty_clauses")),
    ("SELECT", ("table", "public", "prior_claims")),
    ("SELECT", ("table", "reference", "heats_coils")),
    ("SELECT", ("table", "reference", "mill_test_certs")),
    ("SELECT", ("table", "reference", "customer_heat_risk")),
    ("SELECT", ("table", "reference", "prior_claims_corpus")),
    ("SELECT, INSERT, UPDATE", ("table", "public", "adjudications")),
    ("SELECT, INSERT", ("table", "public", "adjudication_decision_records")),
]


# --- Lakebase grants (psycopg) ----------------------------------------------------


def _target(target: tuple) -> sql.Composable:
    kind = target[0]
    if kind == "database":
        return sql.SQL("DATABASE {}").format(sql.Identifier(target[1]))
    if kind == "schema":
        return sql.SQL("SCHEMA {}").format(sql.Identifier(target[1]))
    if kind == "table":
        return sql.SQL("{}").format(sql.Identifier(target[1], target[2]))
    raise ValueError(f"unknown grant target kind: {kind}")


def _grant(cur: psycopg.Cursor, role: str, privileges: str, target: tuple) -> None:
    # ``privileges`` is a fixed keyword literal from GRANTS, never user input, so it
    # is embedded as SQL text; identifiers (role, database, schema, table) are quoted
    # via psycopg.sql so a role id (a UUID) or object name can never break the statement.
    cur.execute(
        sql.SQL("GRANT {privileges} ON {target} TO {role}").format(
            privileges=sql.SQL(privileges),
            target=_target(target),
            role=sql.Identifier(role),
        )
    )


def apply_lakebase_grants(cur: psycopg.Cursor, role: str) -> list[str]:
    """Issue every Lakebase GRANT for the serving SP and return the applied labels."""
    applied = []
    for privileges, target in GRANTS:
        _grant(cur, role, privileges, target)
        applied.append(f"{privileges} on {'.'.join(target[1:])}")
    return applied


# --- Unity Catalog EXECUTE grants (SQL warehouse) ---------------------------------


def build_uc_grant_statements(principal: str) -> list[str]:
    """Return the USE/EXECUTE grants that let the SP call the governed functions."""
    grantee = f"`{principal}`"
    return [
        f"GRANT USE CATALOG ON CATALOG system TO {grantee}",
        f"GRANT USE SCHEMA ON SCHEMA system.ai TO {grantee}",
        f"GRANT EXECUTE ON FUNCTION system.ai.`{UC_REASONING_FUNCTION}` TO {grantee}",
        f"GRANT EXECUTE ON FUNCTION system.ai.`{UC_EMBEDDING_FUNCTION}` TO {grantee}",
    ]


def apply_uc_execute_grants(workspace, warehouse_id: str, principal: str) -> list[str]:
    """Run the Unity Catalog USE/EXECUTE grants for the SP on a SQL warehouse."""
    statements = build_uc_grant_statements(principal)
    for statement in statements:
        workspace.statement_execution.execute_statement(
            statement=statement, warehouse_id=warehouse_id, wait_timeout="30s"
        )
    return statements


def discover_warehouse(workspace, override: str | None) -> str:
    """Return the warehouse id to run EXECUTE grants on: the override or a serverless one."""
    if override:
        return override
    for warehouse in workspace.warehouses.list():
        if getattr(warehouse, "enable_serverless_compute", False):
            return warehouse.id
    raise ValueError(
        "No serverless SQL warehouse found; pass --warehouse <id> to apply the "
        "Unity Catalog EXECUTE grants."
    )


# --- Account service principal, workspace assignment, OAuth secret ----------------


def ensure_service_principal(account, display_name: str) -> dict:
    """Return the account SP for ``display_name``, creating it if absent."""
    for existing in account.service_principals.list():
        if existing.display_name == display_name:
            return {
                "application_id": existing.application_id,
                "id": str(existing.id),
                "created": False,
            }
    created = account.service_principals.create(display_name=display_name)
    return {
        "application_id": created.application_id,
        "id": str(created.id),
        "created": True,
    }


def ensure_workspace_assignment(account, workspace_id: int, sp_id: str) -> None:
    """Assign the SP to the workspace with USER permission (workspace-access)."""
    from databricks.sdk.service.iam import WorkspacePermission

    account.workspace_assignment.update(
        workspace_id=int(workspace_id),
        principal_id=int(sp_id),
        permissions=[WorkspacePermission.USER],
    )


# --- Lakebase OAuth role ----------------------------------------------------------


def _enum_value(value):
    # SDK enums carry the Postgres string in ``.value``; a plain string passes through.
    return getattr(value, "value", value)


def ensure_lakebase_role(workspace, branch: str, role_id: str, postgres_role: str) -> bool:
    """Create the SP's Lakebase OAuth role if absent. Return True when created.

    When a role with ``role_id`` already exists, its spec must match the serving SP:
    ``postgres_role`` equals the SP application UUID, SERVICE_PRINCIPAL identity, and
    LAKEBASE_OAUTH_V1 auth. A match is skipped; a mismatch raises, so grants are never
    applied to a role backed by a different or unprovisioned principal.
    """
    for role in workspace.postgres.list_roles(parent=branch):
        if role.role_id != role_id:
            continue
        spec = role.spec
        mismatches = []
        if spec.postgres_role != postgres_role:
            mismatches.append(f"postgres_role={spec.postgres_role!r} (expected {postgres_role!r})")
        if _enum_value(spec.identity_type) != ROLE_IDENTITY_TYPE:
            mismatches.append(
                f"identity_type={_enum_value(spec.identity_type)!r} "
                f"(expected {ROLE_IDENTITY_TYPE!r})"
            )
        if _enum_value(spec.auth_method) != ROLE_AUTH_METHOD:
            mismatches.append(
                f"auth_method={_enum_value(spec.auth_method)!r} (expected {ROLE_AUTH_METHOD!r})"
            )
        if mismatches:
            raise ValueError(
                f"Lakebase role {role_id!r} already exists but does not match the serving "
                f"service principal: {'; '.join(mismatches)}. Refusing to apply grants to "
                "a mismatched role."
            )
        return False
    from databricks.sdk.service.postgres import (
        Role,
        RoleAuthMethod,
        RoleIdentityType,
        RoleRoleSpec,
    )

    workspace.postgres.create_role(
        parent=branch,
        role_id=role_id,
        role=Role(
            spec=RoleRoleSpec(
                identity_type=RoleIdentityType.SERVICE_PRINCIPAL,
                postgres_role=postgres_role,
                auth_method=RoleAuthMethod.LAKEBASE_OAUTH_V1,
            )
        ),
    )
    return True


# --- Secret scope -----------------------------------------------------------------


def ensure_secrets(account, workspace, scope: str, sp_id: str, app_id: str) -> dict:
    """Create the secret scope and write the three endpoint secrets, idempotently.

    ``app-sp-client-id`` and ``lakebase-db-user`` are the SP application UUID and
    are always written. ``app-sp-client-secret`` is write-once and cannot be read
    back, so a fresh OAuth secret is minted only when the key is absent.
    """
    scopes = {existing.name for existing in workspace.secrets.list_scopes()}
    if scope not in scopes:
        workspace.secrets.create_scope(scope=scope)

    keys = {secret.key for secret in workspace.secrets.list_secrets(scope=scope)}
    workspace.secrets.put_secret(scope=scope, key=SECRET_CLIENT_ID_KEY, string_value=app_id)
    workspace.secrets.put_secret(scope=scope, key=SECRET_DB_USER_KEY, string_value=app_id)
    minted = False
    if SECRET_CLIENT_SECRET_KEY not in keys:
        secret = account.service_principal_secrets.create(service_principal_id=sp_id)
        workspace.secrets.put_secret(
            scope=scope, key=SECRET_CLIENT_SECRET_KEY, string_value=secret.secret
        )
        minted = True
    return {"scope": scope, "client_secret_minted": minted}


# --- Lakebase connection (psycopg over an SDK-minted credential) -------------------


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
    parser.add_argument("--profile", default="fe-bar", help="Workspace profile")
    parser.add_argument(
        "--account-profile",
        required=True,
        help="Account profile for the SP, workspace assignment, and OAuth secret",
    )
    parser.add_argument("--endpoint", default=ENDPOINT)
    parser.add_argument("--database", default=DATABASE)
    parser.add_argument("--display-name", default=DISPLAY_NAME)
    parser.add_argument("--role-id", default=ROLE_ID)
    parser.add_argument("--scope", default=SECRET_SCOPE)
    parser.add_argument(
        "--warehouse",
        help="SQL warehouse id for the Unity Catalog EXECUTE grants (else a "
        "serverless warehouse is discovered)",
    )
    args = parser.parse_args()

    from databricks.sdk import AccountClient, WorkspaceClient

    account = AccountClient(profile=args.account_profile)
    workspace = WorkspaceClient(profile=args.profile)

    sp = ensure_service_principal(account, args.display_name)
    app_id = sp["application_id"]

    ensure_workspace_assignment(account, workspace.get_workspace_id(), sp["id"])
    role_created = ensure_lakebase_role(workspace, BRANCH, args.role_id, postgres_role=app_id)

    with (
        _connect(args.profile, args.endpoint, args.database) as conn,
        conn.cursor() as cur,
    ):
        lakebase_grants = apply_lakebase_grants(cur, app_id)

    warehouse_id = discover_warehouse(workspace, args.warehouse)
    uc_grants = apply_uc_execute_grants(workspace, warehouse_id, app_id)

    secrets = ensure_secrets(account, workspace, args.scope, sp["id"], app_id)

    print(
        json.dumps(
            {
                "application_id": app_id,
                "service_principal_created": sp["created"],
                "role_id": args.role_id,
                "role_created": role_created,
                "lakebase_grants": lakebase_grants,
                "uc_grants": uc_grants,
                "secrets": secrets,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
