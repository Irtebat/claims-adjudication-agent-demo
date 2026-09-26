"""Root-span trace I/O contract for the adjudication flow.

The agent opens a root ``adjudicate`` AGENT span. MLflow 3 derives the trace's
request/response from that root span's inputs/outputs, so if they are never set
the trace renders null request/response in the UI. These tests pin the contract:
after an adjudication the root span carries non-null inputs (the incoming claim)
AND non-null outputs (the final, invariant-corrected recommendation).

No live endpoint or Lakebase: the deterministic core and the LLM step are
stubbed and persistence is disabled, so the test only exercises span I/O.
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

import mlflow

import agent as agent_module

CLAIM = {"claim_id": "CLAIM-1", "claim_type": "coating_warranty", "coil_id": "COIL-1"}

DETERMINISTIC = {
    "verdict": "APPROVE",
    "disposition": "CREDIT",
    "approved_amount": 100.0,
    "settlement_authority_amount": 100.0,
    "eligible": True,
    "reason": "covered",
    "duplicate_of_claim_id": None,
}

CORE = {
    "claim_type": "coating_warranty",
    "frozen": None,
    "resolved": {},
    "measured": {},
    "heat_no": "H1",
    "conformance": {"conforms": False},
    "coverage": {"covered": True},
    "settlement": {"approved_amount": 100.0, "claimed_amount": 100.0, "over_claim_detected": False},
    "duplicate": {"is_duplicate": False, "duplicate_of_claim_id": None},
    "deterministic": DETERMINISTIC,
    "clauses": [],
    "citations": [{"citation_key": "policy/coating/1"}],
    "precedent": [],
    "risk": {"risk_score": 0.0, "found": False, "cluster_id": None},
}

RAW_RECOMMENDATION = {
    "recommended_verdict": "APPROVE",
    "recommended_disposition": "CREDIT",
    "settlement_estimate": 100.0,
    "approved_amount": 100.0,
    "cited_clause_ids": ["policy/coating/1"],
    "precedent": [],
    "rationale": "Covered warranty claim; settlement equals the authority amount.",
    "flags": {},
    "confidence": 0.9,
}


@contextlib.contextmanager
def _fake_conn(*args, **kwargs):
    yield object()


def _run_adjudication() -> dict:
    """Adjudicate one claim with the DB, deterministic core, and LLM step stubbed."""
    instance = agent_module.ClaimsAdjudicationAgent()
    # adjudicate() does `from db import connect` at call time, so patch db.connect.
    with (
        patch("db.connect", _fake_conn),
        patch.object(instance, "_deterministic_core", return_value=CORE),
        patch.object(instance, "_llm_recommendation", return_value=(RAW_RECOMMENDATION, True)),
    ):
        return instance.adjudicate(CLAIM, persist=False)


def _root_span(trace_id: str):
    mlflow.flush_trace_async_logging()
    trace = mlflow.get_trace(trace_id)
    assert trace is not None, "adjudicate did not produce a retrievable trace"
    # The root AGENT span is the first span; its I/O becomes the trace request/response.
    return trace, trace.data.spans[0]


def test_root_span_carries_non_null_inputs_and_outputs():
    outcome = _run_adjudication()
    trace_id = outcome["record"]["mlflow_trace_id"]
    assert trace_id, "root span did not expose a trace id"

    trace, root = _root_span(trace_id)
    assert root.name == "adjudicate"
    assert str(root.span_type) == "AGENT" or root.span_type == "AGENT"

    # Non-null inputs AND outputs — the whole point of the change.
    assert root.inputs is not None
    assert root.outputs is not None
    # The trace request/response (derived from the root span) are non-null too.
    assert trace.data.request is not None
    assert trace.data.response is not None


def test_root_inputs_are_the_claim_and_outputs_are_the_final_recommendation():
    outcome = _run_adjudication()
    trace, root = _root_span(outcome["record"]["mlflow_trace_id"])

    assert root.inputs["claim"]["claim_id"] == "CLAIM-1"
    # Outputs carry the final, invariant-corrected recommendation, not a null/draft.
    assert root.outputs["recommended_verdict"] == "APPROVE"
    assert root.outputs["recommendation"]["approved_amount"] == 100.0
    assert root.outputs["invariant_violations"] == []
