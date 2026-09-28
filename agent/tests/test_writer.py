"""The transactional writer: atomic (adjudication + decision record + outbox),
idempotent, correct verdict mapping, and a well-formed claim.adjudicated outbox event."""

from contextlib import contextmanager

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
        "reasoning_endpoint": "databricks-gpt-5-2",
        "prompt_version": "p",
        "schema_version": "s",
        "mlflow_trace_id": "t",
    }


def _patch_jsonb(monkeypatch):
    monkeypatch.setattr(writer, "_jsonb", lambda value: {"__jsonb__": value})


def test_writes_all_three_in_one_transaction(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    result = writer.write_adjudication(conn, _record())
    assert conn.transactions == 1
    # adjudications upsert + decision-record insert + outbox insert, one transaction.
    assert len(conn.cur.calls) == 3
    adjudication_sql, adjudication_params = conn.cur.calls[0]
    record_sql, _ = conn.cur.calls[1]
    outbox_sql, outbox_params = conn.cur.calls[2]
    assert "INSERT INTO adjudications" in adjudication_sql
    assert "ON CONFLICT (adjudication_id) DO UPDATE" in adjudication_sql
    assert "INSERT INTO adjudication_decision_records" in record_sql
    assert "ON CONFLICT (adjudication_id, record_version) DO NOTHING" in record_sql
    assert "INSERT INTO outbox" in outbox_sql
    assert "ON CONFLICT (event_id) DO NOTHING" in outbox_sql
    assert adjudication_params["decision_status"] == "RECOMMENDED"
    assert outbox_params["event_id"] == "adj-ADJ-abc"
    assert outbox_params["aggregate_id"] == "CLM-9"
    assert outbox_params["event_type"] == "claim.adjudicated"
    assert result["decision_record_inserted"] is True
    assert result["outbox_event_inserted"] is True
    assert result["outbox_event_id"] == "adj-ADJ-abc"


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
    conn.cur.rowcount = 0  # ON CONFLICT DO NOTHING matched existing record + outbox event
    result = writer.write_adjudication(conn, _record())
    assert result["decision_record_inserted"] is False
    assert result["outbox_event_inserted"] is False


def test_outbox_event_payload_shape(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    writer.write_adjudication(
        conn, _record(verdict="PEND_INVESTIGATE", disposition="PEND_INVESTIGATE", approved=0.0)
    )
    _, outbox_params = conn.cur.calls[2]
    payload = outbox_params["payload"]["__jsonb__"]  # _jsonb is patched to wrap the dict
    assert payload["event_id"] == "adj-ADJ-abc"
    assert payload["event_type"] == "claim.adjudicated"
    assert payload["schema_version"] == "claim-event/v1"
    assert payload["claim_id"] == "CLM-9"
    assert payload["verdict"] == "PEND"  # operational mapping of PEND_INVESTIGATE
    assert payload["recommended_verdict"] == "PEND_INVESTIGATE"
    assert payload["approved_amount"] == 0.0
    assert payload["idempotency_key"] == "abc"


def test_outbox_event_carries_duplicate_and_supplier_context(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    record = _record(verdict="DENY", disposition="DUPLICATE", approved=0.0, dup=True)
    record["flags"]["supplier_attributable"] = True
    record["recovery_supplier_id"] = "SUP-7"
    writer.write_adjudication(conn, record)
    payload = conn.cur.calls[2][1]["payload"]["__jsonb__"]
    assert payload["verdict"] == "DENY"
    assert payload["duplicate_of_claim_id"] == "CLM-1"
    assert payload["supplier_attributable"] is True
    assert payload["recovery_supplier_id"] == "SUP-7"


def test_duplicate_carries_reference(monkeypatch):
    _patch_jsonb(monkeypatch)
    conn = _Conn()
    writer.write_adjudication(
        conn, _record(verdict="DENY", disposition="DUPLICATE", approved=0.0, dup=True)
    )
    _, params = conn.cur.calls[0]
    assert params["duplicate_of_claim_id"] == "CLM-1"
    assert params["verdict"] == "DENY"
