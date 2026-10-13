"""Unit tests for the app-SP Lakebase grant script.

Mock psycopg and invoke the functions directly, asserting the EXACT grant
statements (matching docs/evidence/app-deploy/grants.sql), the idempotent
statement sequence, and that the app principal must be supplied explicitly. The
module is loaded by file path so it imports without the Databricks SDK installed
(the SDK is imported lazily inside _connect).
"""

import importlib.util
import json
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
    """Records rendered GRANT SQL, matching the psycopg rendering the script emits.

    Existence probes (``to_regclass`` / ``to_regnamespace``) are recorded separately in
    ``probes`` and answer "exists" unless the probed name is in ``missing``. A GRANT
    containing ``fail_on`` raises, like a Postgres error.
    """

    def __init__(self, fail_on=None, missing=()):
        self.statements = []
        self.probes = []
        self._fail_on = fail_on
        self._missing = set(missing)
        self._row = None

    def execute(self, query, params=None):
        rendered = query.as_string(None) if hasattr(query, "as_string") else str(query)
        if rendered.startswith("SELECT to_reg"):
            self.probes.append(params[0])
            self._row = (params[0] not in self._missing,)
            return
        self.statements.append(rendered)
        if self._fail_on and self._fail_on in rendered:
            raise RuntimeError(f"postgres error executing: {rendered}")

    def fetchone(self):
        return self._row

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
        f'GRANT SELECT ON "reference"."heats_coils" TO "{role}"',
        f'GRANT SELECT ON "reference"."mill_test_certs" TO "{role}"',
        f'GRANT SELECT ON "reference"."customers" TO "{role}"',
        f'GRANT SELECT ON "reference"."customer_heat_risk" TO "{role}"',
        f'GRANT SELECT ON "reference"."prior_claims_corpus" TO "{role}"',
        f'GRANT INSERT, UPDATE ON "public"."adjudications" TO "{role}"',
        f'GRANT INSERT, UPDATE ON "public"."adjudication_decision_records" TO "{role}"',
        # outbox needs SELECT as well as INSERT — the finalize outbox write uses
        # `ON CONFLICT (event_id) DO NOTHING`, which requires SELECT on the arbiter column.
        f'GRANT SELECT, INSERT ON "public"."outbox" TO "{role}"',
    ]


def test_apply_grants_issues_exact_statements():
    cur = FakeCursor()
    setup.apply_grants(cur, APP_SP)
    assert cur.statements == _expected(APP_SP)


def test_outbox_grant_includes_select_for_on_conflict():
    """Regression for the live finalize HTTP 500 ('permission denied for table outbox').

    The finalize transaction's outbox write is
    `INSERT INTO public.outbox (...) ON CONFLICT (event_id) DO NOTHING`. Postgres
    requires SELECT privilege on every column named in an ON CONFLICT arbiter target
    (here `event_id`), so an INSERT-only grant makes every finalize fail with
    `permission denied for table outbox`, roll back, and return 500. Assert the app-SP
    grant for outbox includes SELECT so the ON CONFLICT first-write-wins write can run.
    """
    cur = FakeCursor()
    setup.apply_grants(cur, APP_SP)
    outbox_grants = [s for s in cur.statements if '"public"."outbox"' in s]
    assert outbox_grants == [f'GRANT SELECT, INSERT ON "public"."outbox" TO "{APP_SP}"']
    assert "SELECT" in outbox_grants[0]


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
    out = capsys.readouterr()
    summary = json.loads(out.out)
    assert summary["complete"] is True
    assert summary["skipped"] == []
    assert len(summary["granted"]) == len(setup.GRANTS)
    assert APP_SP in out.err


# --- Fresh workspace: missing synced (reference.*) targets are skipped -------------


def test_missing_reference_table_is_skipped_and_warned_not_raised(monkeypatch, capsys):
    cur = FakeCursor(missing={'"reference"."customers"'})
    monkeypatch.setattr(setup, "_connect", lambda *a, **k: FakeConn(cur))
    monkeypatch.setattr(sys, "argv", ["setup_app_sp", "--app-principal", APP_SP])
    setup.main()  # exits normally (0), does not raise
    assert cur.statements == [
        s for s in _expected(APP_SP) if '"reference"."customers"' not in s
    ]
    out = capsys.readouterr()
    summary = json.loads(out.out)
    assert summary["skipped"] == ["SELECT on reference.customers"]
    assert summary["complete"] is False
    assert "reference.customers" in out.err
    assert "create synced tables, then re-run this script" in out.err.lower()


def test_missing_reference_schema_skips_all_synced_grants():
    synced = [t for _, t in setup.GRANTS if t[1] == "reference"]
    missing = {'"reference"'} | {
        f'"{t[1]}"."{t[2]}"' for t in synced if t[0] == "table"
    }
    cur = FakeCursor(missing=missing)
    result = setup.apply_grants(cur, APP_SP)
    assert len(result["skipped"]) == len(synced)
    assert all('"reference"' not in s for s in cur.statements)


def test_present_reference_tables_are_granted_and_probed():
    cur = FakeCursor()
    result = setup.apply_grants(cur, APP_SP)
    assert result["skipped"] == []
    assert cur.statements == _expected(APP_SP)
    assert cur.probes and all(p.startswith('"reference"') for p in cur.probes)


def test_non_reference_grant_failure_still_raises():
    cur = FakeCursor(fail_on='"public"."outbox"')
    with pytest.raises(RuntimeError, match="postgres error"):
        setup.apply_grants(cur, APP_SP)


def test_existing_reference_grant_failure_still_raises():
    cur = FakeCursor(fail_on='"reference"."heats_coils"')
    with pytest.raises(RuntimeError, match="postgres error"):
        setup.apply_grants(cur, APP_SP)


def test_main_prints_principal_before_any_grant(monkeypatch, capsys):
    def failing_connect(*args, **kwargs):
        raise RuntimeError("lakebase unreachable")

    monkeypatch.setattr(setup, "_connect", failing_connect)
    monkeypatch.setattr(sys, "argv", ["setup_app_sp", "--app-principal", APP_SP])
    with pytest.raises(RuntimeError, match="lakebase unreachable"):
        setup.main()
    assert APP_SP in capsys.readouterr().err


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
