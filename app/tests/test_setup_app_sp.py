"""Execution tests for the app service-principal Lakebase grants."""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "setup_app_sp.py"
SPEC = importlib.util.spec_from_file_location("setup_app_sp", MODULE_PATH)
setup_app_sp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_app_sp)

PRINCIPAL = "rotated-app-service-principal"


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, query, params=None):
        self.statements.append(query.as_string(None))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _expected():
    role = f'"{PRINCIPAL}"'
    statements = [
        f'GRANT CONNECT ON DATABASE "databricks_postgres" TO {role}',
        f'GRANT USAGE ON SCHEMA "public" TO {role}',
        f'GRANT USAGE ON SCHEMA "reference" TO {role}',
    ]
    statements.extend(
        f'GRANT SELECT ON "public"."{table}" TO {role}'
        for table in setup_app_sp.PUBLIC_SELECT_TABLES
    )
    statements.extend(
        f'GRANT SELECT ON "reference"."{table}" TO {role}'
        for table in setup_app_sp.REFERENCE_SELECT_TABLES
    )
    statements.extend(
        [
            f'GRANT INSERT, UPDATE ON "public"."adjudications" TO {role}',
            f'GRANT INSERT, UPDATE ON "public"."adjudication_decision_records" TO {role}',
            f'GRANT INSERT ON "public"."outbox" TO {role}',
        ]
    )
    return statements


def test_grant_issues_exact_idempotent_grant_set():
    first = FakeCursor()
    setup_app_sp._grant(first, PRINCIPAL, "databricks_postgres")
    second = FakeCursor()
    setup_app_sp._grant(second, PRINCIPAL, "databricks_postgres")

    assert first.statements == _expected()
    assert second.statements == first.statements


def test_main_requires_principal_before_connecting(monkeypatch):
    connected = False

    def fake_connect(*args, **kwargs):
        nonlocal connected
        connected = True

    monkeypatch.setattr(setup_app_sp, "_connect", fake_connect)
    monkeypatch.setattr(sys, "argv", ["setup_app_sp", "--profile", "fe-bar-ir-2026"])

    with pytest.raises(SystemExit):
        setup_app_sp.main()
    assert connected is False


def test_main_passes_explicit_profile_principal_and_connection_options(monkeypatch):
    cursor = FakeCursor()
    calls = []

    def fake_connect(*args):
        calls.append(args)
        return FakeConn(cursor)

    monkeypatch.setattr(setup_app_sp, "_connect", fake_connect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "setup_app_sp",
            "--principal",
            PRINCIPAL,
            "--profile",
            "chosen-profile",
            "--endpoint",
            "projects/p/branches/b/endpoints/e",
            "--database",
            "claims_db",
        ],
    )

    setup_app_sp.main()

    assert calls == [("chosen-profile", "projects/p/branches/b/endpoints/e", "claims_db")]
    assert cursor.statements[0] == (
        f'GRANT CONNECT ON DATABASE "claims_db" TO "{PRINCIPAL}"'
    )
