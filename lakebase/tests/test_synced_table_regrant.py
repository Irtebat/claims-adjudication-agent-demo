"""Execution tests for the synced-table SELECT re-grant.

These mock a psycopg connection/cursor and actually invoke ``_regrant`` / ``main``,
asserting the EXACT ``GRANT`` statements issued (right role -> right table, no
over-grant), idempotent re-runs, that a missing required principal RAISES (the
fail-safe), and that a database error propagates instead of being swallowed.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
MODULE_PATH = SCRIPTS / "regrant_synced_table_selects.py"
SPEC = importlib.util.spec_from_file_location("regrant_synced", MODULE_PATH)
regrant = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(regrant)

APP_SP = "d5309ee7-a8ea-499f-99d4-4ccbd8369d93"
SERVING_SP = "47643eb1-dbd5-40a6-a51d-5da6b8e2da7a"


# Expected statements per principal, rendered as psycopg emits them (identifiers
# double-quoted). Derived directly from APP_TABLES / SERVING_TABLES so the test tracks
# the documented table sets and catches any over- or under-grant.
def _expected(role, tables):
    stmts = [f'GRANT USAGE ON SCHEMA "reference" TO "{role}"']
    stmts += [f'GRANT SELECT ON "reference"."{t}" TO "{role}"' for t in tables]
    return stmts


class FakeCursor:
    """Records rendered SQL; optionally raises when a statement matches ``fail_on``."""

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


def _run_main(monkeypatch, cursor, argv):
    connect_calls = []

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return FakeConn(cursor)

    monkeypatch.setattr(regrant, "_connect", fake_connect)
    monkeypatch.setattr(sys, "argv", argv)
    regrant.main()
    return connect_calls


# --- _regrant: exact statements, right mapping, no over-grant ---------------------


def test_regrant_issues_exact_grants_for_role():
    cur = FakeCursor()
    regrant._regrant(cur, "role-x", ["alpha", "beta"])
    assert cur.statements == [
        'GRANT USAGE ON SCHEMA "reference" TO "role-x"',
        'GRANT SELECT ON "reference"."alpha" TO "role-x"',
        'GRANT SELECT ON "reference"."beta" TO "role-x"',
    ]


def test_regrant_only_select_and_usage_never_write_or_ownership():
    cur = FakeCursor()
    regrant._regrant(cur, "role-x", ["alpha"])
    joined = " ".join(cur.statements)
    for forbidden in (
        "INSERT",
        "UPDATE",
        "DELETE",
        "OWNER",
        "ALL PRIVILEGES",
        "GRANT ALL",
    ):
        assert forbidden not in joined
    # Only USAGE (schema) and SELECT (tables) are ever emitted.
    assert all(
        ("GRANT USAGE ON SCHEMA" in s) or ("GRANT SELECT ON" in s)
        for s in cur.statements
    )


# --- main(): both principals, exact end-to-end mapping ----------------------------


def test_main_grants_both_principals_with_exact_table_mapping(monkeypatch):
    cur = FakeCursor()
    _run_main(monkeypatch, cur, ["regrant"])
    assert cur.statements == _expected(APP_SP, regrant.APP_TABLES) + _expected(
        SERVING_SP, regrant.SERVING_TABLES
    )
    # customer_heat_risk (the table that 500'd the cockpit) is covered for both SPs.
    assert (
        f'GRANT SELECT ON "reference"."customer_heat_risk" TO "{APP_SP}"'
        in cur.statements
    )
    assert (
        f'GRANT SELECT ON "reference"."customer_heat_risk" TO "{SERVING_SP}"'
        in cur.statements
    )


def test_main_is_idempotent_across_repeat_invocations(monkeypatch):
    # Postgres GRANTs are idempotent; the script is also deterministic, so a second
    # invocation issues the identical statement sequence (no accumulation, no skip).
    first = FakeCursor()
    _run_main(monkeypatch, first, ["regrant"])
    second = FakeCursor()
    _run_main(monkeypatch, second, ["regrant"])
    assert first.statements == second.statements


# --- fail-safe: a missing required principal RAISES (covers blocking 1) -----------


def test_resolve_consumers_missing_serving_raises_naming_it():
    with pytest.raises(ValueError, match="serving"):
        regrant._resolve_consumers(APP_SP, "")


def test_resolve_consumers_missing_app_raises_naming_it():
    with pytest.raises(ValueError, match="app"):
        regrant._resolve_consumers("", SERVING_SP)


def test_main_missing_required_principal_raises_and_grants_nothing(monkeypatch):
    cur = FakeCursor()
    connect_calls = []

    def fake_connect(*args, **kwargs):
        connect_calls.append((args, kwargs))
        return FakeConn(cur)

    monkeypatch.setattr(regrant, "_connect", fake_connect)
    monkeypatch.setattr(sys, "argv", ["regrant", "--serving-principal", ""])
    with pytest.raises(ValueError, match="serving"):
        regrant.main()
    # It must fail BEFORE connecting and must not have granted the app principal.
    assert connect_calls == []
    assert cur.statements == []


def test_allow_single_principal_is_explicit_opt_out():
    # Opt-out: only the supplied principal is granted; both-empty still errors.
    consumers = regrant._resolve_consumers(APP_SP, "", allow_single=True)
    assert consumers == [(APP_SP, regrant.APP_TABLES)]
    with pytest.raises(ValueError, match="No principals"):
        regrant._resolve_consumers("", "", allow_single=True)


# --- a database error propagates (fails loud, not swallowed) ----------------------


def test_db_error_propagates_and_no_success(monkeypatch, capsys):
    cur = FakeCursor(fail_on="customer_heat_risk")
    with pytest.raises(RuntimeError, match="postgres error"):
        _run_main(monkeypatch, cur, ["regrant"])
    # The success JSON is never printed when a grant fails.
    assert "regranted" not in capsys.readouterr().out


# --- consumer table lists cover the synced precedent corpus -----------------------


def test_both_consumers_read_the_synced_precedent_corpus():
    # The app cockpit and the served agent both read reference.prior_claims_corpus, so a
    # create/recreate must grant it to both. (Create/resync/recreate wiring is tested in
    # test_synced_tables.py.)
    assert "prior_claims_corpus" in regrant.APP_TABLES
    assert "prior_claims_corpus" in regrant.SERVING_TABLES
