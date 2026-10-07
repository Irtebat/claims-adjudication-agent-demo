"""Offline validation must never write adjudications to Lakebase.

Persisting during validation seeds stray ``RECOMMENDED`` rows against the seeded FINAL
sample claims, which then pollute the adjuster queue. These tests pin the contract:
``run()`` invokes the agent with ``persist=False`` for every sampled claim, opens Lakebase
only once (to select the sample — never for a readback), and the evidence summary records
``persisted: False`` with no decision-record readback.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock

import offline_validation as ov


def _outcome(claim: dict) -> dict:
    return {
        "record": {
            "adjudication_id": f"ADJ-{claim['claim_id']}",
            "recommended_verdict": "APPROVE",
            "recommended_disposition": "CREDIT",
            "approved_amount": 100.0,
            "duplicate_flag": False,
            "cited_clause_ids": ["policy/coating/1"],
        },
        "deterministic": {
            "verdict": "APPROVE",
            "disposition": "CREDIT",
            "settlement_authority_amount": 100.0,
        },
        "invariant_violations": [],
        "llm_used": True,
    }


def test_run_uses_persist_false_and_never_reads_back(monkeypatch, tmp_path):
    import agent as agent_module
    import db as db_module

    persist_calls: list[bool] = []

    def fake_adjudicate(claim, persist=True):
        persist_calls.append(persist)
        return _outcome(claim)

    fake_agent = MagicMock()
    fake_agent.adjudicate.side_effect = fake_adjudicate

    connect_count = {"n": 0}

    @contextlib.contextmanager
    def fake_connect(*args, **kwargs):
        connect_count["n"] += 1
        yield object()

    # One representative claim per required label pattern (satisfies run()'s completeness
    # guard) — the actual selection query is stubbed out.
    sample = {
        pattern: {"claim_id": f"CLM-{i}", "claim_type": "coating_warranty"}
        for i, pattern in enumerate(ov.PATTERN_SQL)
    }

    monkeypatch.setattr(ov.mlflow, "set_tracking_uri", lambda *a, **k: None)
    monkeypatch.setattr(ov.mlflow, "set_experiment", lambda *a, **k: None)
    monkeypatch.setattr(agent_module, "AGENT", fake_agent)
    monkeypatch.setattr(db_module, "connect", fake_connect)
    monkeypatch.setattr(ov, "_select_sample", lambda conn: sample)

    summary = ov.run("fe-bar-ir-2026", "/exp/offline", tmp_path)

    # Every adjudication ran with persist=False — validation writes nothing to Lakebase.
    assert persist_calls, "adjudicate was never called"
    assert all(p is False for p in persist_calls)
    assert fake_agent.adjudicate.call_count == len(sample)

    # Lakebase is opened exactly once (the sample select). There is no second connection
    # for a decision-record readback anymore.
    assert connect_count["n"] == 1

    # The summary reflects the no-persist contract and carries no readback fields.
    assert summary["persisted"] is False
    assert summary["sample_size"] == len(sample)
    assert "decision_records_readback" not in summary
    assert "decision_records_written" not in summary

    # Evidence artifact is still written.
    assert (tmp_path / "offline-validation.json").exists()
