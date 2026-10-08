"""Unit tests for the serving-SP provisioning script.

Mock psycopg and the Databricks SDK clients and invoke the functions directly,
asserting the EXACT Lakebase and Unity Catalog grant statements, the
detect-and-skip idempotency of each created resource, and that a missing account
profile is rejected. The module is loaded by file path so it imports without the
SDK installed (the SDK is imported lazily on the paths that create resources).
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS / "setup_serving_sp.py"
SPEC = importlib.util.spec_from_file_location("setup_serving_sp", MODULE_PATH)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)

APP_ID = "11111111-2222-3333-4444-555555555555"


class FakeCursor:
    """Records rendered SQL, matching the psycopg rendering the script emits."""

    def __init__(self):
        self.statements = []

    def execute(self, query, params=None):
        rendered = query.as_string(None) if hasattr(query, "as_string") else str(query)
        self.statements.append(rendered)


# --- Lakebase grants: exact statements, right order, no over-grant ----------------


def _expected_lakebase(role):
    return [
        f'GRANT CONNECT ON DATABASE "databricks_postgres" TO "{role}"',
        f'GRANT USAGE ON SCHEMA "public" TO "{role}"',
        f'GRANT USAGE ON SCHEMA "reference" TO "{role}"',
        f'GRANT SELECT ON "public"."claims" TO "{role}"',
        f'GRANT SELECT ON "public"."spec_params" TO "{role}"',
        f'GRANT SELECT ON "public"."warranty_terms" TO "{role}"',
        f'GRANT SELECT ON "public"."spec_clauses" TO "{role}"',
        f'GRANT SELECT ON "public"."warranty_clauses" TO "{role}"',
        f'GRANT SELECT ON "public"."prior_claims" TO "{role}"',
        f'GRANT SELECT ON "reference"."heats_coils" TO "{role}"',
        f'GRANT SELECT ON "reference"."mill_test_certs" TO "{role}"',
        f'GRANT SELECT ON "reference"."customer_heat_risk" TO "{role}"',
        f'GRANT SELECT ON "reference"."prior_claims_corpus" TO "{role}"',
        f'GRANT SELECT, INSERT, UPDATE ON "public"."adjudications" TO "{role}"',
        f'GRANT SELECT, INSERT ON "public"."adjudication_decision_records" TO "{role}"',
    ]


def test_apply_lakebase_grants_issues_exact_statements():
    cur = FakeCursor()
    setup.apply_lakebase_grants(cur, APP_ID)
    assert cur.statements == _expected_lakebase(APP_ID)


def test_lakebase_grants_never_touch_delete_or_ownership():
    cur = FakeCursor()
    setup.apply_lakebase_grants(cur, APP_ID)
    joined = " ".join(cur.statements)
    for forbidden in ("DELETE", "OWNER", "ALL PRIVILEGES", "GRANT ALL", "SUPERUSER"):
        assert forbidden not in joined


def test_apply_lakebase_grants_is_idempotent_statement_sequence():
    first, second = FakeCursor(), FakeCursor()
    setup.apply_lakebase_grants(first, APP_ID)
    setup.apply_lakebase_grants(second, APP_ID)
    assert first.statements == second.statements


def test_lakebase_grants_cover_the_synced_precedent_corpus():
    # find_similar_prior_claims reads reference.prior_claims_corpus, so the serving SP
    # must be granted SELECT on it (same read set as regrant SERVING_TABLES).
    cur = FakeCursor()
    setup.apply_lakebase_grants(cur, APP_ID)
    assert f'GRANT SELECT ON "reference"."prior_claims_corpus" TO "{APP_ID}"' in cur.statements


# --- Unity Catalog EXECUTE grants: exact statements, backtick-quoted ---------------


def test_build_uc_grant_statements_exact():
    assert setup.build_uc_grant_statements(APP_ID) == [
        f"GRANT USE CATALOG ON CATALOG `fe-bar-ir` TO `{APP_ID}`",
        f"GRANT USE SCHEMA ON SCHEMA `fe-bar-ir`.`adjudication-agent` TO `{APP_ID}`",
        "GRANT EXECUTE ON MODEL SERVICE "
        f"`fe-bar-ir`.`adjudication-agent`.`adjudication-reasoning` TO `{APP_ID}`",
        f"GRANT EXECUTE ON MODEL SERVICE `fe-bar-ir`.`adjudication-agent`.`embedding` TO `{APP_ID}`",
    ]


def test_apply_uc_execute_grants_runs_each_on_the_warehouse():
    calls = []

    workspace = SimpleNamespace(
        statement_execution=SimpleNamespace(execute_statement=lambda **kw: calls.append(kw))
    )
    statements = setup.apply_uc_execute_grants(workspace, "wh-123", APP_ID)
    assert [c["statement"] for c in calls] == statements
    assert all(c["warehouse_id"] == "wh-123" for c in calls)


# --- Warehouse discovery ----------------------------------------------------------


def test_discover_warehouse_prefers_override():
    assert setup.discover_warehouse(object(), "wh-override") == "wh-override"


def test_discover_warehouse_picks_serverless():
    workspace = SimpleNamespace(
        warehouses=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(id="classic", enable_serverless_compute=False),
                SimpleNamespace(id="serverless", enable_serverless_compute=True),
            ]
        )
    )
    assert setup.discover_warehouse(workspace, None) == "serverless"


def test_discover_warehouse_raises_when_none_found():
    workspace = SimpleNamespace(warehouses=SimpleNamespace(list=lambda: []))
    with pytest.raises(ValueError, match="--warehouse"):
        setup.discover_warehouse(workspace, None)


# --- Service principal: detect-and-skip -------------------------------------------


def test_ensure_service_principal_reuses_existing_by_display_name():
    created = []
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(display_name="other", application_id="x", id=1),
                SimpleNamespace(display_name=setup.DISPLAY_NAME, application_id=APP_ID, id=99),
            ],
            create=lambda **kw: created.append(kw),
        )
    )
    result = setup.ensure_service_principal(account, setup.DISPLAY_NAME)
    assert result == {"application_id": APP_ID, "id": "99", "created": False}
    assert created == []


def test_ensure_service_principal_creates_when_absent():
    account = SimpleNamespace(
        service_principals=SimpleNamespace(
            list=lambda: [],
            create=lambda **kw: SimpleNamespace(application_id=APP_ID, id=7),
        )
    )
    result = setup.ensure_service_principal(account, setup.DISPLAY_NAME)
    assert result == {"application_id": APP_ID, "id": "7", "created": True}


# --- Lakebase OAuth role: detect-and-skip -----------------------------------------


def _existing_role(
    role_id, postgres_role, identity=setup.ROLE_IDENTITY_TYPE, auth=setup.ROLE_AUTH_METHOD
):
    return SimpleNamespace(
        role_id=role_id,
        spec=SimpleNamespace(postgres_role=postgres_role, identity_type=identity, auth_method=auth),
    )


def _role_workspace(roles, created):
    return SimpleNamespace(
        postgres=SimpleNamespace(
            list_roles=lambda parent: roles,
            create_role=lambda **kw: created.append(kw),
        )
    )


def test_ensure_lakebase_role_skips_when_present_and_matching():
    created = []
    workspace = _role_workspace([_existing_role(setup.ROLE_ID, APP_ID)], created)
    assert setup.ensure_lakebase_role(workspace, setup.BRANCH, setup.ROLE_ID, APP_ID) is False
    assert created == []


def test_ensure_lakebase_role_fails_loud_on_mismatch():
    # A role with the right id but a different postgres_role must NOT be reused: grants
    # would land on the wrong/unprovisioned principal.
    created = []
    workspace = _role_workspace(
        [_existing_role(setup.ROLE_ID, "99999999-0000-0000-0000-000000000000")], created
    )
    with pytest.raises(ValueError, match="does not match the serving"):
        setup.ensure_lakebase_role(workspace, setup.BRANCH, setup.ROLE_ID, APP_ID)
    assert created == []  # never creates and never falls through to grants


def test_ensure_lakebase_role_fails_loud_on_wrong_auth_method():
    created = []
    workspace = _role_workspace(
        [_existing_role(setup.ROLE_ID, APP_ID, auth="PG_PASSWORD_SCRAM_SHA_256")], created
    )
    with pytest.raises(ValueError, match="auth_method"):
        setup.ensure_lakebase_role(workspace, setup.BRANCH, setup.ROLE_ID, APP_ID)
    assert created == []


def test_ensure_lakebase_role_creates_when_absent():
    pytest.importorskip("databricks.sdk.service.postgres")
    created = []
    workspace = SimpleNamespace(
        postgres=SimpleNamespace(
            list_roles=lambda parent: [],
            create_role=lambda **kw: created.append(kw),
        )
    )
    assert setup.ensure_lakebase_role(workspace, setup.BRANCH, setup.ROLE_ID, APP_ID) is True
    assert created and created[0]["role_id"] == setup.ROLE_ID
    assert created[0]["parent"] == setup.BRANCH
    assert created[0]["role"].spec.postgres_role == APP_ID


# --- Secret scope: detect-and-skip, mint only when absent -------------------------


def _secrets_workspace(existing_scopes, existing_keys, puts, scope_creates):
    return SimpleNamespace(
        secrets=SimpleNamespace(
            list_scopes=lambda: [SimpleNamespace(name=n) for n in existing_scopes],
            list_secrets=lambda scope: [SimpleNamespace(key=k) for k in existing_keys],
            create_scope=lambda scope: scope_creates.append(scope),
            put_secret=lambda scope, key, string_value: puts.append((key, string_value)),
        )
    )


def test_ensure_secrets_creates_scope_and_mints_secret_when_absent():
    puts, scope_creates, minted = [], [], []
    workspace = _secrets_workspace([], [], puts, scope_creates)
    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            create=lambda service_principal_id: (
                minted.append(service_principal_id) or SimpleNamespace(secret="minted-secret")
            )
        )
    )
    result = setup.ensure_secrets(account, workspace, setup.SECRET_SCOPE, "99", APP_ID)
    assert scope_creates == [setup.SECRET_SCOPE]
    assert minted == ["99"]
    assert result["client_secret_minted"] is True
    assert (setup.SECRET_CLIENT_ID_KEY, APP_ID) in puts
    assert (setup.SECRET_DB_USER_KEY, APP_ID) in puts
    assert (setup.SECRET_CLIENT_SECRET_KEY, "minted-secret") in puts


def test_ensure_secrets_skips_minting_when_secret_present():
    puts, scope_creates, minted = [], [], []
    workspace = _secrets_workspace(
        [setup.SECRET_SCOPE], [setup.SECRET_CLIENT_SECRET_KEY], puts, scope_creates
    )
    account = SimpleNamespace(
        service_principal_secrets=SimpleNamespace(
            create=lambda service_principal_id: minted.append(service_principal_id)
        )
    )
    result = setup.ensure_secrets(account, workspace, setup.SECRET_SCOPE, "99", APP_ID)
    assert scope_creates == []  # scope already exists
    assert minted == []  # write-once secret not re-minted
    assert result["client_secret_minted"] is False
    assert all(key != setup.SECRET_CLIENT_SECRET_KEY for key, _ in puts)


# --- Required-arg validation ------------------------------------------------------


def test_main_requires_account_profile(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["setup_serving_sp", "--profile", "fe-bar"])
    with pytest.raises(SystemExit):
        setup.main()
