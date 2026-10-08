"""Unit tests for the live, model-driven Genie advisory tools.

These are the agent's first NON-deterministic tools, so the tests pin the safety
contract: every outcome (ok / empty / timeout / 403 / error) returns a graceful
STRING (never raises), and every call is captured in the per-adjudication
collector that feeds the decision record. The Genie client is injected via
``ask_fn``, so the tests need no network and no databricks-ai-bridge install.
"""

from __future__ import annotations

import time

import genie_tools


def _client_factory():
    return object()  # never used — ask_fn is injected in these tests


def test_ok_answer_returned_and_recorded():
    collector: list[dict] = []

    def ask_fn(space_id, question, cf):
        return {"result": "42 claims last month", "query": "SELECT count(*)", "description": "d"}

    out = genie_tools.consult_genie(
        space_id="S1",
        label="analytics",
        question="how many claims last month?",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
    )
    assert out == "42 claims last month"
    assert len(collector) == 1
    row = collector[0]
    assert row["status"] == "ok"
    assert row["space"] == "analytics"
    assert row["space_id"] == "S1"
    assert row["question"] == "how many claims last month?"
    assert row["answer"] == "42 claims last month"
    assert row["generated_sql"] == "SELECT count(*)"
    assert isinstance(row["latency_ms"], int)


def test_falls_back_to_description_when_result_empty():
    collector: list[dict] = []

    def ask_fn(s, q, cf):
        return {"result": "", "query": None, "description": "see the table"}

    out = genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
    )
    assert out == "see the table"
    assert collector[0]["status"] == "ok"


def test_empty_answer_is_graceful():
    collector: list[dict] = []

    def ask_fn(s, q, cf):
        return {"result": "", "query": None, "description": ""}

    out = genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
    )
    assert "returned no answer" in out
    assert collector[0]["status"] == "empty"


def test_timeout_is_graceful_and_recorded():
    collector: list[dict] = []

    def ask_fn(s, q, cf):
        time.sleep(5)  # exceeds the 0.1s bound below
        return {"result": "late", "query": None, "description": None}

    out = genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
        timeout_s=0.1,
    )
    assert "did not answer within" in out
    assert collector[0]["status"] == "timeout"


def test_403_is_classified_forbidden():
    collector: list[dict] = []

    def ask_fn(s, q, cf):
        raise RuntimeError("Error 403: PERMISSION_DENIED on space")

    out = genie_tools.consult_genie(
        space_id="S",
        label="analytics",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
        timeout_s=5,
    )
    assert "unavailable (forbidden)" in out
    assert collector[0]["status"] == "forbidden"


def test_generic_error_is_classified_error():
    collector: list[dict] = []

    def ask_fn(s, q, cf):
        raise RuntimeError("connection reset by peer")

    out = genie_tools.consult_genie(
        space_id="S",
        label="analytics",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
        timeout_s=5,
    )
    assert "unavailable (error)" in out
    assert collector[0]["status"] == "error"


def test_answer_and_recorded_answer_are_truncated():
    collector: list[dict] = []
    big = "x" * (genie_tools._ANSWER_MAX_CHARS + 500)

    def ask_fn(s, q, cf):
        return {"result": big, "query": None, "description": None}

    out = genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
        timeout_s=5,
    )
    assert len(out) == genie_tools._ANSWER_MAX_CHARS
    assert len(collector[0]["answer"]) == genie_tools._ANSWER_MAX_CHARS


def test_timeout_env_override(monkeypatch):
    monkeypatch.setenv("GENIE_TIMEOUT_S", "0.05")
    assert genie_tools._timeout_s() == 0.05
    monkeypatch.setenv("GENIE_TIMEOUT_S", "bogus")
    assert genie_tools._timeout_s() == genie_tools.DEFAULT_TIMEOUT_S


def test_build_genie_tools_names_count_and_advisory_descriptions():
    tools = genie_tools.build_genie_tools(client_factory=_client_factory, collector=[])
    assert [t.name for t in tools] == ["query_claims_genie", "query_analytics_genie"]
    # Each description tells the LLM the tool is advisory and must not change money.
    assert all("ADVISORY" in t.description for t in tools)
    assert all("must not change" in t.description.lower() for t in tools)


def test_space_ids_are_the_two_configured_agents():
    assert genie_tools.OPERATIONAL_SPACE_ID == "01f1c269ca3c1adea7feb9f248ab3445"
    assert genie_tools.ANALYTICS_SPACE_ID == "01f1c2698a5418298f81f9e79df576ca"
