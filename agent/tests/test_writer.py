"""The recommendation writer: atomic (adjudication + decision record), idempotent,
correct verdict mapping, and — post Wave 7 — NO recommendation-time outbox row.

The ``claim.adjudicated`` fan-out now fires only when an adjuster finalizes the claim
in the App, so the recommendation transaction must write exactly two rows and never
touch ``public.outbox``. The event-payload shape is covered by
``services/tests/test_events.py`` (``build_adjudicated_payload``), which the App
finalizer mirrors."""

import os
import uuid
from contextlib import contextmanager

import pytest

import writer


class _Cursor:
    def __init__(self):
        self.calls = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.cur = _Cursor()
        self.transactions = 0

    def cursor(self):
        return self.cur

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield


def _record(verdict="APPROVE", disposition="CREDIT", approved=5000.0, dup=False):
    return {
        "adjudication_id": "ADJ-abc",
        "claim_id": "CLM-9",
        "record_version": 1,
        "idempotency_key": "abc",
        "recommended_verdict": verdict,
        "recommended_disposition": disposition,
        "claimed_amount": 9000.0,
        "approved_amount": approved,
        "settlement": {"covered_tonnage": 5.0, "freight_covered": False},
        "duplicate": {"duplicate_of_claim_id": "CLM-1" if dup else None},
        "rationale": "r",
        "cited_clause_ids": ["k1"],
        "confidence": 0.8,
        "flags": {"supplier_attributable": False, "fraud_risk": False, "over_claim": True},
        "advisory_risk": {"risk_score": 0.1, "cluster_id": None},
        "precedent": [],
        # decision-record columns consumed by _decision_record_row:
        "claim_type": "material_nonconformance",
        "claim_input": {"claim_id": "CLM-9"},
        "spec_provenance": {"grade": "G"},
        "warranty_provenance": {"product_line": "galvanized"},
        "spec_params": {},
        "warranty_terms": {},
        "freight_cap": 500.0,
        "coil": {},
        "mtc_measured": {},
        "conformance": {"conforms": False},
        "coverage": {"covered": True},
        "over_claim_flag": True,
        "duplicate_flag": dup,
        "deterministic_verdict": verdict,
        "deterministic_disposition": disposition,
        "invariant_violations": [],
        "citations": [{"citation_key": "k1"}],
        "authorities_git_sha": "sha",
        "authorities_source_sha256": "src",
        "agent_model_name": "m",
        "agent_model_version": "1",
        "reasoning_endpoint": "system.ai.gpt-5-4",
        "prompt_version": "p",
        "schema_version": "s",
        "mlflow_trace_id": "t",
    }


def _patch_jsonb(monkeypatch):
    monkeypatch.setattr(writer, "_jsonb", lambda value: {"__jsonb__": value})


def test_writes_both_in_one_transaction(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    result = writer.write_adjudication(conn, _record())
    assert conn.transactions == 1
    # adjudications upsert + decision-record insert, one transaction. NO outbox.
    assert len(conn.cur.calls) == 2
    adjudication_sql, adjudication_params = conn.cur.calls[0]
    record_sql, _ = conn.cur.calls[1]
    assert "INSERT INTO adjudications" in adjudication_sql
    # first-write-wins on adjudication_id, matching the decision record, so a same-id
    # retry can never mutate the adjudication out of step with the immutable record.
    assert "ON CONFLICT (adjudication_id) DO NOTHING" in adjudication_sql
    assert "DO UPDATE" not in adjudication_sql
    assert "INSERT INTO adjudication_decision_records" in record_sql
    assert "ON CONFLICT (adjudication_id, record_version) DO NOTHING" in record_sql
    assert adjudication_params["decision_status"] == "RECOMMENDED"
    assert result["adjudication_inserted"] is True
    assert result["decision_record_inserted"] is True


def test_no_outbox_row_written_at_recommendation_time(monkeypatch):
    """The fan-out moved to human finalization: recommendation writes NO outbox row."""
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    result = writer.write_adjudication(conn, _record())
    assert all("INSERT INTO outbox" not in sql for sql, _ in conn.cur.calls)
    # No outbox metadata leaks into the writer's return contract anymore.
    assert "outbox_event_id" not in result
    assert "outbox_event_inserted" not in result


def test_recommended_at_is_omitted_so_ddl_default_now_applies(monkeypatch):
    """recommended_at must NOT be bound: omitting the column lets the DDL

    `recommended_at timestamptz DEFAULT now()` fill it. Binding it (even as an explicit
    NULL, the previous bug) overrides the default and leaves the recommendation timestamp
    NULL — which then sorts unpredictably in the queue/cockpit. The written RECOMMENDED
    row therefore gets a non-null recommended_at from the database default.
    """
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    writer.write_adjudication(conn, _record())
    adjudication_sql, adjudication_params = conn.cur.calls[0]
    # The column is absent from the INSERT column list and from the bound params, so the
    # DB default now() applies rather than an explicit value.
    assert "recommended_at" not in adjudication_sql
    assert "recommended_at" not in adjudication_params
    # finalized_at (no default) is still bound NULL — set only at human finalization.
    assert "finalized_at" in adjudication_params
    assert adjudication_params["finalized_at"] is None


def test_pend_verdict_maps_to_operational_pend(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    writer.write_adjudication(
        conn, _record(verdict="PEND_INVESTIGATE", disposition="PEND_INVESTIGATE", approved=0.0)
    )
    _, params = conn.cur.calls[0]
    assert params["verdict"] == "PEND"
    assert params["disposition"] == "PEND_INVESTIGATE"
    assert params["approved_amount"] == 0.0


def test_idempotent_retry_reports_not_inserted(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    conn.cur.rowcount = 0  # ON CONFLICT DO NOTHING matched both existing rows
    result = writer.write_adjudication(conn, _record())
    assert result["adjudication_inserted"] is False
    assert result["decision_record_inserted"] is False


# --------------------------------------------------------------------------- #
# Regression: retry consistency (money-path). Both rows are first-write-wins, so a
# same-adjudication_id retry — even with different decision data — leaves the
# adjudication and the immutable decision record mutually consistent, and no
# partial/inconsistent state is reachable.
# --------------------------------------------------------------------------- #
import re  # noqa: E402


class _StatefulCursor:
    """Applies writer's ON CONFLICT DO NOTHING / DO UPDATE against in-memory tables."""

    _TABLE = re.compile(r"INSERT INTO (\w+)")
    _PK = re.compile(r"ON CONFLICT \(([^)]+)\)")

    def __init__(self, store):
        self.store = store  # table -> {pk_tuple: row}
        self.rowcount = 0

    def execute(self, sql, params=None):
        table = self._TABLE.search(sql).group(1)
        pk_cols = [c.strip() for c in self._PK.search(sql).group(1).split(",")]
        do_update = "DO UPDATE" in sql
        key = tuple(params[c] for c in pk_cols)
        rows = self.store.setdefault(table, {})
        if key in rows:
            if do_update:
                rows[key] = dict(params)
                self.rowcount = 1
            else:  # DO NOTHING
                self.rowcount = 0
        else:
            rows[key] = dict(params)
            self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StatefulConn:
    def __init__(self):
        self.store = {}
        self.cur = _StatefulCursor(self.store)

    def cursor(self):
        return self.cur

    @contextmanager
    def transaction(self):
        yield


def _amounts(store):
    """The approved_amount as stored in each of the two rows (for divergence checks)."""
    adj = next(iter(store["adjudications"].values()))
    rec = next(iter(store["adjudication_decision_records"].values()))
    return {
        "adjudication": adj["approved_amount"],
        "decision_record": rec["approved_amount"],
    }


def test_same_id_retry_keeps_both_consistent(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _StatefulConn()

    first = writer.write_adjudication(conn, _record(approved=5000.0))
    assert first["adjudication_inserted"] is True
    assert first["decision_record_inserted"] is True

    # Retry with the SAME adjudication_id but DIFFERENT decision data (a collision).
    retry_record = _record(approved=9999.0, verdict="DENY", disposition="DUPLICATE")
    second = writer.write_adjudication(conn, retry_record)

    # (a) first-write-wins: nothing re-inserted on retry.
    assert second["adjudication_inserted"] is False
    assert second["decision_record_inserted"] is False

    # (b) no divergence: exactly one row per table, and the amount is the FIRST write's
    # 5000.0 across the adjudication AND the immutable decision record. No outbox row
    # exists at recommendation time.
    assert len(conn.store["adjudications"]) == 1
    assert len(conn.store["adjudication_decision_records"]) == 1
    assert "outbox" not in conn.store
    amounts = _amounts(conn.store)
    assert amounts["adjudication"] == 5000.0
    assert amounts["decision_record"] == 5000.0
    assert len(set(amounts.values())) == 1  # both agree — no divergence


def test_no_do_update_path_exists_for_either_write(monkeypatch):
    """A mismatched-id collision cannot partially mutate: every write is DO NOTHING."""
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    writer.write_adjudication(conn, _record())
    for sql, _params in conn.cur.calls:
        assert "DO UPDATE" not in sql
        assert "DO NOTHING" in sql


def test_duplicate_carries_reference(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    writer.write_adjudication(
        conn, _record(verdict="DENY", disposition="DUPLICATE", approved=0.0, dup=True)
    )
    _, params = conn.cur.calls[0]
    assert params["duplicate_of_claim_id"] == "CLM-1"
    assert params["verdict"] == "DENY"


# --------------------------------------------------------------------------- #
# Integration: the recommended_at DDL DEFAULT actually fires on a real write.
# test_recommended_at_is_omitted_... proves the column is left out of the INSERT;
# this proves the *consequence* — a RECOMMENDED row read back from Lakebase has a
# NON-NULL recommended_at (the default now() populated it). Gated on a live
# connection and skipped without one, mirroring test_authorities_runtime's live
# smoke. The write is done inside an outer transaction that is rolled back after the
# read-back, so it exercises the real default without persisting a test row (and
# without needing DELETE on the immutable decision-record table).
# --------------------------------------------------------------------------- #


class _RollbackSentinel(Exception):
    """Raised to abort the integration transaction after the read-back assertions."""


def test_live_written_recommendation_has_non_null_recommended_at():
    if os.getenv("RUN_LIVE_LAKEBASE_TESTS") != "1":
        pytest.skip("set RUN_LIVE_LAKEBASE_TESTS=1 for the live Lakebase write/read-back test")
    pytest.importorskip("psycopg")
    try:
        from db import connect
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"db module unavailable: {exc}")

    adjudication_id = f"ADJ-ITEST-{uuid.uuid4().hex[:12]}"
    record = _record()
    record["adjudication_id"] = adjudication_id
    record["claim_id"] = f"CLM-ITEST-{uuid.uuid4().hex[:8]}"
    record["idempotency_key"] = adjudication_id

    try:
        cm = connect(profile="fe-bar")
        conn = cm.__enter__()
    except Exception as exc:  # no creds / Lakebase unreachable -> skip
        pytest.skip(f"fe-bar Lakebase not reachable: {exc}")
    try:
        # Outer transaction so write_adjudication's own transaction() nests as a
        # savepoint (visible to our read-back) rather than committing immediately.
        try:
            with conn.transaction():
                result = writer.write_adjudication(conn, record)
                assert result["adjudication_inserted"] is True
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT recommended_at, decision_status FROM adjudications "
                        "WHERE adjudication_id = %(id)s",
                        {"id": adjudication_id},
                    )
                    row = cur.fetchone()
                assert row is not None, "written RECOMMENDED row must be read back"
                recommended_at, decision_status = row
                assert decision_status == "RECOMMENDED"
                # The core assertion: the DDL DEFAULT now() populated recommended_at.
                assert recommended_at is not None
                raise _RollbackSentinel  # abort so no test row persists
        except _RollbackSentinel:
            pass
    finally:
        cm.__exit__(None, None, None)
