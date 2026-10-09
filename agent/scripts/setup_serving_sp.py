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
5. Unity Catalog ``USE CATALOG``/``USE SCHEMA``/``EXECUTE`` on the two governed
   Unity Gateway model services ``fe-bar-ir.adjudication-agent.adjudication-reasoning``
   (reasoning) and ``fe-bar-ir.adjudication-agent.embedding`` (embedding).
6. The ``claims-agent`` secret scope with ``app-sp-client-id``,
   ``app-sp-client-secret``, and ``lakebase-db-user`` — the references
   ``deploy_agent.py`` injects into the endpoint. ``lakebase-db-user`` is the
   Lakebase role, i.e. the service principal's application UUID.
7. Genie Agent access for the advisory Genie tool (Phase 2): CAN_RUN on the
   operational Genie space and CAN USE on its SQL warehouse (workspace
   permissions API, additive), plus USE SCHEMA on ``fe-bar-ir.gold`` /
   ``fe-bar-ir.silver`` and SELECT on the tables it reads (SQL GRANT), so
   Genie's generated SQL runs as the serving SP.

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
        --profile fe-bar-ir-2026 --account-profile <account-profile>
"""

from __future__ import annotations

import argparse
import json
import re

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

# Governed Unity Gateway model services the agent reasons and embeds with. These are
# UC securables of type MODEL SERVICE (invoked via the AI Gateway route), NOT UC
# functions. The serving SP needs USE CATALOG on the parent catalog, USE SCHEMA on the
# parent schema, and EXECUTE on each model service. Every identifier is hyphenated, so
# each name part is backtick-quoted, as is the service-principal grantee.
MODEL_SERVICE_CATALOG = "fe-bar-ir"
MODEL_SERVICE_SCHEMA = "adjudication-agent"
REASONING_MODEL_SERVICE = "adjudication-reasoning"
EMBEDDING_MODEL_SERVICE = "embedding"

# --- Genie Agent (Phase 2) --------------------------------------------------------
# The governed operational Genie Agent the agent consults as a live, advisory tool, the
# SQL warehouse it runs on, and the Unity Catalog tables Genie reads. The serving SP
# needs, via the workspace permissions API, CAN_RUN on the space and CAN USE on the
# warehouse; and, via SQL GRANT, USE SCHEMA on each parent schema plus SELECT on each
# table so Genie's generated SQL executes as the SP. USE CATALOG on `fe-bar-ir` is
# already granted for the model services above and is reused here.
GENIE_SPACES = {
    "operational": "01f1c269ca3c1adea7feb9f248ab3445",
}
GENIE_WAREHOUSE_ID = "a323b5700ae25d85"  # the operational space is configured on this warehouse
GENIE_SPACE_PERMISSION = "CAN_RUN"
GENIE_WAREHOUSE_PERMISSION = "CAN_USE"

# UC schemas (within MODEL_SERVICE_CATALOG) and the tables the operational Genie space
# reads. Every object is a TABLE-class securable (table, view, streaming table, or
# materialized view), so a uniform GRANT SELECT ON TABLE covers all of them.
GENIE_UC_SCHEMAS = ("gold", "silver")
GENIE_UC_TABLES = (
    ("gold", "adjudication_decision_records"),
    ("gold", "adjudications_current"),
    ("gold", "claims_current"),
    ("gold", "customer_heat_risk"),
    ("gold", "prior_claims_corpus"),
    ("silver", "customers"),
    ("silver", "defect_codes"),
    ("silver", "heats_coils"),
    ("silver", "mill_test_certs"),
    ("silver", "suppliers"),
)

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


# A service-principal application id is a canonical UUID. The grantee is interpolated
# into GRANT SQL as a backtick-quoted identifier (Unity Catalog grants are not run
# through psycopg's identifier quoting, unlike the Lakebase grants above), so its shape
# is validated up front: a non-UUID or backtick-bearing principal here is always a bug,
# so we fail loud rather than silently escape it.
_PRINCIPAL_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _validate_principal(principal: str) -> str:
    """Return ``principal`` if it is a canonical UUID; raise ``ValueError`` otherwise."""
    if not _PRINCIPAL_RE.fullmatch(principal):
        raise ValueError(
            f"principal {principal!r} is not a valid service-principal application id "
            "(expected a UUID); refusing to interpolate it into a GRANT statement"
        )
    return principal


def build_uc_grant_statements(principal: str) -> list[str]:
    """Return the USE/EXECUTE grants that let the SP call the governed model services."""
    grantee = f"`{_validate_principal(principal)}`"
    catalog = f"`{MODEL_SERVICE_CATALOG}`"
    schema = f"`{MODEL_SERVICE_CATALOG}`.`{MODEL_SERVICE_SCHEMA}`"
    reasoning = f"`{MODEL_SERVICE_CATALOG}`.`{MODEL_SERVICE_SCHEMA}`.`{REASONING_MODEL_SERVICE}`"
    embedding = f"`{MODEL_SERVICE_CATALOG}`.`{MODEL_SERVICE_SCHEMA}`.`{EMBEDDING_MODEL_SERVICE}`"
    return [
        f"GRANT USE CATALOG ON CATALOG {catalog} TO {grantee}",
        f"GRANT USE SCHEMA ON SCHEMA {schema} TO {grantee}",
        f"GRANT EXECUTE ON MODEL SERVICE {reasoning} TO {grantee}",
        f"GRANT EXECUTE ON MODEL SERVICE {embedding} TO {grantee}",
    ]


def apply_uc_execute_grants(workspace, warehouse_id: str, principal: str) -> list[str]:
    """Run the Unity Catalog USE/EXECUTE grants for the SP on a SQL warehouse."""
    statements = build_uc_grant_statements(principal)
    for statement in statements:
        workspace.statement_execution.execute_statement(
            statement=statement, warehouse_id=warehouse_id, wait_timeout="30s"
        )
    return statements


# --- Genie Agent access: UC table grants + workspace permissions ------------------


def build_genie_uc_grant_statements(principal: str) -> list[str]:
    """USE SCHEMA on each Genie schema + SELECT on each underlying table, for the SP.

    Reuses ``_validate_principal`` so a non-UUID or backtick-bearing grantee can never
    be interpolated into the backtick-quoted GRANT SQL. Every target is a TABLE-class
    securable, so ``GRANT SELECT ON TABLE`` is uniform across tables, views,
    materialized views, streaming tables, and the metric view.
    """
    grantee = f"`{_validate_principal(principal)}`"
    catalog = MODEL_SERVICE_CATALOG
    statements = [
        f"GRANT USE SCHEMA ON SCHEMA `{catalog}`.`{schema}` TO {grantee}"
        for schema in GENIE_UC_SCHEMAS
    ]
    statements += [
        f"GRANT SELECT ON TABLE `{catalog}`.`{schema}`.`{table}` TO {grantee}"
        for schema, table in GENIE_UC_TABLES
    ]
    return statements


def apply_genie_uc_grants(workspace, warehouse_id: str, principal: str) -> list[str]:
    """Run the Genie USE SCHEMA + SELECT grants for the SP on a SQL warehouse."""
    statements = build_genie_uc_grant_statements(principal)
    for statement in statements:
        workspace.statement_execution.execute_statement(
            statement=statement, warehouse_id=warehouse_id, wait_timeout="30s"
        )
    return statements


def apply_genie_permissions(workspace, principal: str) -> list[dict]:
    """Grant the SP CAN_RUN on each Genie space + CAN USE on the shared warehouse.

    Uses the workspace permissions API (``update`` = additive merge, so other
    principals' access is preserved) because a Genie space and a SQL warehouse are
    workspace objects, not Unity Catalog securables. ``_validate_principal`` guards the
    SP id even though the API binds it as a field rather than string-interpolating it.
    """
    _validate_principal(principal)
    from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

    applied: list[dict] = []
    for label, space_id in GENIE_SPACES.items():
        workspace.permissions.update(
            request_object_type="genie",
            request_object_id=space_id,
            access_control_list=[
                AccessControlRequest(
                    service_principal_name=principal,
                    permission_level=PermissionLevel.CAN_RUN,
                )
            ],
        )
        applied.append(
            {
                "object_type": "genie",
                "object_id": space_id,
                "space": label,
                "permission": GENIE_SPACE_PERMISSION,
            }
        )
    workspace.permissions.update(
        request_object_type="warehouses",
        request_object_id=GENIE_WAREHOUSE_ID,
        access_control_list=[
            AccessControlRequest(
                service_principal_name=principal,
                permission_level=PermissionLevel.CAN_USE,
            )
        ],
    )
    applied.append(
        {
            "object_type": "warehouses",
            "object_id": GENIE_WAREHOUSE_ID,
            "permission": GENIE_WAREHOUSE_PERMISSION,
        }
    )
    return applied


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
    genie_uc_grants = apply_genie_uc_grants(workspace, warehouse_id, app_id)
    genie_permissions = apply_genie_permissions(workspace, app_id)

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
                "genie_uc_grants": genie_uc_grants,
                "genie_permissions": genie_permissions,
                "secrets": secrets,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
