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
import gateway_chat

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


# --- audit accuracy: the recorded reasoning_endpoint is the service actually called ---


def test_reasoning_endpoint_label_is_single_sourced_from_gateway():
    """LLM_ENDPOINT (recorded as reasoning_endpoint) must be the real governed reasoning
    service the agent calls — derived from gateway_chat.MODEL_SERVICE so the audited
    label can never drift from the invoked service, and never the stale system.ai name."""
    assert agent_module.LLM_ENDPOINT == gateway_chat.MODEL_SERVICE
    assert agent_module.LLM_ENDPOINT == "fe-bar-ir.adjudication-agent.adjudication-reasoning"
    assert "system.ai" not in agent_module.LLM_ENDPOINT


def test_adjudication_records_the_real_reasoning_endpoint():
    """Every adjudication's decision record must label the reasoning model with the
    service actually invoked, not a stale constant."""
    outcome = _run_adjudication()
    assert outcome["record"]["reasoning_endpoint"] == gateway_chat.MODEL_SERVICE


def test_agent_wiring_is_lazy_no_client_at_construction():
    """Constructing the agent must not build any workspace/gateway client (all client
    wiring is lazy); only the reasoning-label constant is resolved."""
    instance = agent_module.ClaimsAdjudicationAgent()
    assert instance._llm is None
    assert instance._llm_with_tools is None


def test_importing_agent_module_builds_no_client_and_has_no_cycle():
    """Importing the agent module must not instantiate a workspace/gateway client at
    import time and must not hit a circular import — deriving LLM_ENDPOINT from
    gateway_chat only reads a string constant."""
    import importlib

    import databricks.sdk

    with patch.object(databricks.sdk, "WorkspaceClient") as constructor:
        importlib.reload(agent_module)
    constructor.assert_not_called()
    # The reasoning label is still single-sourced from gateway_chat after a clean reload.
    assert agent_module.LLM_ENDPOINT == gateway_chat.MODEL_SERVICE


def test_llm_endpoint_is_reproducibility_metadata_only():
    """Lock the negative: LLM_ENDPOINT is referenced ONLY as its definition and the
    decision-record/reproducibility `reasoning_endpoint` field — never on a live
    reasoning call path (bind/invoke/post/model=)."""
    import inspect

    uses = [
        ln.strip()
        for ln in inspect.getsource(agent_module).splitlines()
        if "LLM_ENDPOINT" in ln and not ln.strip().startswith("#")
    ]
    assert "LLM_ENDPOINT = REASONING_MODEL_SERVICE" in uses
    assert '"reasoning_endpoint": LLM_ENDPOINT,' in uses
    assert len(uses) == 2  # exactly the definition + the metadata field, nothing else
    for line in uses:
        for forbidden in ("invoke", "bind", "post_fn", "ask_question", "model=", "model_service"):
            assert forbidden not in line


def test_genie_client_factory_builds_a_timeout_bounded_client(monkeypatch):
    """End-to-end: the agent hands build_genie_tools a client_factory that constructs the
    Genie WorkspaceClient WITH the hard per-request http + retry timeouts (the real,
    supported mechanism), sourced from genie_tools.http_timeout_s()."""
    import genie_tools

    captured = {}

    def fake_build(client_factory, collector):
        captured["factory"] = client_factory
        return []

    built = []
    monkeypatch.setattr(genie_tools, "build_genie_tools", fake_build)
    monkeypatch.setattr(agent_module, "workspace_client", lambda *a, **k: built.append((a, k)))
    monkeypatch.setenv("GENIE_HTTP_TIMEOUT_S", "11")

    instance = agent_module.ClaimsAdjudicationAgent()
    instance._tools(dict(CORE))
    captured["factory"]()  # invoke the factory the agent handed to build_genie_tools

    assert built, "the Genie client_factory did not construct a workspace client"
    _, kwargs = built[0]
    assert kwargs["http_timeout_seconds"] == 11.0
    assert kwargs["retry_timeout_seconds"] == 11.0


# --- advisory-only: a Genie answer can never change the money decision -------------

# A deterministic DENY (in-spec material): not eligible, authority amount 1234, paid 0.
DENY_DETERMINISTIC = {
    "verdict": "DENY",
    "disposition": "DENY",
    "approved_amount": 0.0,
    "settlement_authority_amount": 1234.0,
    "eligible": False,
    "reason": "in_spec_per_mtc",
    "duplicate_of_claim_id": None,
}

DENY_CORE = {
    "claim_type": "material_nonconformance",
    "frozen": None,
    "resolved": {},
    "measured": {},
    "heat_no": "H1",
    "conformance": {"conforms": True},
    "coverage": {"covered": False},
    "settlement": {
        "approved_amount": 1234.0,
        "claimed_amount": 5000.0,
        "over_claim_detected": False,
    },
    "duplicate": {"is_duplicate": False, "duplicate_of_claim_id": None},
    "deterministic": DENY_DETERMINISTIC,
    "clauses": [],
    "citations": [{"citation_key": "spec/mech/1"}],
    "precedent": [],
    "risk": {"risk_score": 0.0, "found": False, "cluster_id": None},
}

GENIE_CONSULTATION = {
    "space": "operational",
    "space_id": "01f1c269ca3c1adea7feb9f248ab3445",
    "question": "how were prior similar claims adjudicated?",
    "status": "ok",
    "answer": "3 similar prior claims were APPROVED with credits.",
    "generated_sql": "SELECT ...",
    "latency_ms": 150,
}

# What the LLM returns AFTER a Genie consultation 'influences' it to the wrong, payable
# outcome — an APPROVE for 5000 that contradicts the deterministic DENY.
GENIE_INFLUENCED_WRONG_REC = {
    "recommended_verdict": "APPROVE",
    "recommended_disposition": "CREDIT",
    "settlement_estimate": 5000.0,
    "approved_amount": 5000.0,
    "cited_clause_ids": [],
    "precedent": [],
    "rationale": "Genie precedent shows similar claims were approved, so approve this one.",
    "flags": {},
    "confidence": 0.95,
}


def test_genie_influenced_recommendation_cannot_change_money():
    """End-to-end advisory-only proof: a Genie-influenced APPROVE/5000 on a
    deterministic DENY is clamped back by enforce_invariants to DENY / amount 0 /
    authority settlement, and the Genie consultation is still recorded for audit."""
    instance = agent_module.ClaimsAdjudicationAgent()

    def fake_llm(core):
        # Simulate the reasoning loop consulting Genie, then returning the wrong rec.
        core["genie_consultations"].append(dict(GENIE_CONSULTATION))
        return dict(GENIE_INFLUENCED_WRONG_REC), True

    with (
        patch("db.connect", _fake_conn),
        patch.object(instance, "_deterministic_core", return_value=DENY_CORE),
        patch.object(instance, "_llm_recommendation", side_effect=fake_llm),
    ):
        outcome = instance.adjudicate(CLAIM, persist=False)

    record = outcome["record"]
    # Money and verdict are the DETERMINISTIC result, not the Genie-influenced APPROVE.
    assert record["recommended_verdict"] == "DENY"
    assert record["approved_amount"] == 0.0
    assert outcome["recommendation"]["settlement_estimate"] == 1234.0  # authority, not 5000
    assert record["deterministic_verdict"] == "DENY"
    assert "cannot_approve_ineligible_claim" in outcome["invariant_violations"]
    # The consultation that 'influenced' the rec is still audited on the record.
    assert record["flags"]["genie_consultations"] == [GENIE_CONSULTATION]


def test_genie_tools_are_bound_in_the_reasoning_loop():
    """The live operational Genie tool is part of the bound reasoning-loop tool list
    (with the 7 frozen echo tools) — the only place model-driven tools live."""
    instance = agent_module.ClaimsAdjudicationAgent()
    names = [tool.name for tool in instance._tools(dict(CORE))]
    assert "query_claims_genie" in names
    assert "query_analytics_genie" not in names  # analytics tool dropped
    assert len(names) == 8  # 7 frozen deterministic echoes + 1 live Genie tool
