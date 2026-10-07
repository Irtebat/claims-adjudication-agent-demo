"""Unit tests for the app-SP Lakebase grant script.

Mock psycopg and invoke the functions directly, asserting the EXACT grant
statements (matching docs/evidence/app-deploy/grants.sql), the idempotent
statement sequence, and that the app principal must be supplied explicitly. The
module is loaded by file path so it imports without the Databricks SDK installed
(the SDK is imported lazily inside _connect).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS / "setup_app_sp.py"
SPEC = importlib.util.spec_from_file_location("setup_app_sp", MODULE_PATH)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)

APP_SP = "d5309ee7-a8ea-499f-99d4-4ccbd8369d93"


class FakeCursor:
    """Records rendered SQL, matching the psycopg rendering the script emits."""

    def __init__(self, fail_on=None):
        self.statements = []
        self._fail_on = fail_on

    def execute(self, query, params=None):
        rendered = query.as_string(None) if hasattr(query, "as_string") else str(query)
        self.statements.append(rendered)
        if self._fail_on and self._fail_on in rendered:
            raise RuntimeError(f"postgres error executing: {rendered}")

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


def _expected(role):
    # The exact grant set of docs/evidence/app-deploy/grants.sql, in file order,
    # rendered as psycopg emits it (identifiers double-quoted).
    return [
        f'GRANT USAGE ON SCHEMA "public" TO "{role}"',
        f'GRANT USAGE ON SCHEMA "reference" TO "{role}"',
        f'GRANT SELECT ON "public"."claims" TO "{role}"',
        f'GRANT SELECT ON "public"."adjudications" TO "{role}"',
        f'GRANT SELECT ON "public"."adjudication_decision_records" TO "{role}"',
        f'GRANT SELECT ON "public"."spec_params" TO "{role}"',
        f'GRANT SELECT ON "public"."spec_clauses" TO "{role}"',
        f'GRANT SELECT ON "public"."warranty_terms" TO "{role}"',
        f'GRANT SELECT ON "public"."warranty_clauses" TO "{role}"',
        f'GRANT SELECT ON "public"."prior_claims" TO "{role}"',
        f'GRANT SELECT ON "reference"."heats_coils" TO "{role}"',
        f'GRANT SELECT ON "reference"."mill_test_certs" TO "{role}"',
        f'GRANT SELECT ON "reference"."customers" TO "{role}"',
        f'GRANT SELECT ON "reference"."customer_heat_risk" TO "{role}"',
        f'GRANT INSERT, UPDATE ON "public"."adjudications" TO "{role}"',
        f'GRANT INSERT, UPDATE ON "public"."adjudication_decision_records" TO "{role}"',
        f'GRANT INSERT ON "public"."outbox" TO "{role}"',
    ]


def test_apply_grants_issues_exact_statements():
    cur = FakeCursor()
    setup.apply_grants(cur, APP_SP)
    assert cur.statements == _expected(APP_SP)


def test_grants_never_touch_delete_select_only_reads_or_ownership():
    cur = FakeCursor()
    setup.apply_grants(cur, APP_SP)
    joined = " ".join(cur.statements)
    for forbidden in ("DELETE", "OWNER", "ALL PRIVILEGES", "GRANT ALL"):
        assert forbidden not in joined


def test_apply_grants_is_idempotent_statement_sequence():
    first, second = FakeCursor(), FakeCursor()
    setup.apply_grants(first, APP_SP)
    setup.apply_grants(second, APP_SP)
    assert first.statements == second.statements


def test_resolve_app_principal_requires_a_value():
    with pytest.raises(ValueError, match="App service-principal client id"):
        setup._resolve_app_principal(None)
    with pytest.raises(ValueError, match="App service-principal client id"):
        setup._resolve_app_principal("")
    assert setup._resolve_app_principal(APP_SP) == APP_SP


def test_main_grants_when_principal_supplied(monkeypatch, capsys):
    cur = FakeCursor()
    connect_calls = []

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return FakeConn(cur)

    monkeypatch.setattr(setup, "_connect", fake_connect)
    monkeypatch.setattr(sys, "argv", ["setup_app_sp", "--app-principal", APP_SP])
    setup.main()
    assert cur.statements == _expected(APP_SP)
    assert '"granted": true' in capsys.readouterr().out


def test_main_requires_app_principal_and_grants_nothing(monkeypatch):
    monkeypatch.delenv("APP_SP_PRINCIPAL", raising=False)
    connect_calls = []

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return FakeConn(FakeCursor())

    monkeypatch.setattr(setup, "_connect", fake_connect)
    monkeypatch.setattr(sys, "argv", ["setup_app_sp"])
    with pytest.raises(ValueError, match="App service-principal client id"):
        setup.main()
    # It must fail BEFORE connecting.
    assert connect_calls == []
