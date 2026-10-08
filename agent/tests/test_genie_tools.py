"""Unit tests for the live, model-driven Genie advisory tools.

These are the agent's first NON-deterministic tools, so the tests pin the safety
contract: every outcome (ok / empty / timeout / 403 / error) returns a graceful
STRING (never raises), and every call is captured in the per-adjudication
collector that feeds the decision record. The Genie client is injected via
``ask_fn``, so the tests need no network and no databricks-ai-bridge install.
"""

from __future__ import annotations

import threading
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
        label="operational",
        question="how many claims last month?",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
    )
    assert out == "42 claims last month"
    assert len(collector) == 1
    row = collector[0]
    assert row["status"] == "ok"
    assert row["space"] == "operational"
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
        label="operational",
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
        label="operational",
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
    # Only the operational Genie tool remains; the analytics tool was dropped.
    assert [t.name for t in tools] == ["query_claims_genie"]
    # Each description tells the LLM the tool is advisory and must not change money.
    assert all("ADVISORY" in t.description for t in tools)
    assert all("must not change" in t.description.lower() for t in tools)


def test_only_the_operational_agent_is_configured():
    assert genie_tools.OPERATIONAL_SPACE_ID == "01f1c269ca3c1adea7feb9f248ab3445"
    # The analytics Genie space was removed, so its id must not linger as a constant.
    assert not hasattr(genie_tools, "ANALYTICS_SPACE_ID")


# --- pool hardening: isolated per-call thread, no shared pool to exhaust ----------


def test_no_shared_module_level_executor():
    # Regression guard: a hung Genie call must not occupy a shared bounded pool that
    # repeated hangs could exhaust. Each call uses its own isolated daemon thread.
    assert not hasattr(genie_tools, "_EXECUTOR")


def test_repeated_timeouts_stay_graceful_and_contained():
    # Many hung calls back to back each return a graceful timeout promptly, with no
    # shared pool to starve — each runs on its own isolated daemon thread.
    def slow(s, q, cf):
        time.sleep(2)  # far exceeds the 0.05s bound; the thread is abandoned
        return {"result": "late", "query": None, "description": None}

    collector: list[dict] = []
    outs = [
        genie_tools.consult_genie(
            space_id="S",
            label="operational",
            question=f"q{i}",
            client_factory=_client_factory,
            collector=collector,
            ask_fn=slow,
            timeout_s=0.05,
        )
        for i in range(12)
    ]
    assert all("did not answer within" in out for out in outs)
    assert [row["status"] for row in collector] == ["timeout"] * 12


# --- hardening: the Genie timeout value genie_tools asks the factory to build with ----


def test_http_timeout_s_defaults_to_wall_clock_bound(monkeypatch):
    # agent._tools reads this and constructs the Genie WorkspaceClient with it; here we
    # only pin that the default tracks the wall-clock bound and the env override wins.
    monkeypatch.delenv("GENIE_HTTP_TIMEOUT_S", raising=False)
    monkeypatch.setenv("GENIE_TIMEOUT_S", "20")
    assert genie_tools.http_timeout_s() == 20.0
    monkeypatch.setenv("GENIE_HTTP_TIMEOUT_S", "7.5")
    assert genie_tools.http_timeout_s() == 7.5
    monkeypatch.setenv("GENIE_HTTP_TIMEOUT_S", "bogus")
    assert genie_tools.http_timeout_s() == genie_tools._timeout_s()


# --- hardening backstop: in-flight concurrency cap bounds thread accumulation ------


def test_concurrent_hangs_are_capped_not_leaked(monkeypatch):
    # Prove repeated/concurrent hangs are BOUNDED: with every in-flight slot taken, a new
    # call sheds load with a graceful 'capacity' string and NEVER spawns another thread,
    # so daemon threads/sockets cannot accumulate unbounded.
    sem = threading.BoundedSemaphore(2)
    monkeypatch.setattr(genie_tools, "_INFLIGHT", sem)
    monkeypatch.setenv("GENIE_MAX_INFLIGHT", "2")
    assert sem.acquire(blocking=False)  # simulate two already in-flight (hung) calls
    assert sem.acquire(blocking=False)

    called = {"ask": False}

    def ask_fn(s, q, cf):
        called["ask"] = True  # must never run while the cap is saturated
        return {"result": "x", "query": None, "description": None}

    collector: list[dict] = []
    out = genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
        timeout_s=5,
    )
    assert "at capacity" in out
    assert collector[0]["status"] == "busy"
    assert called["ask"] is False  # no new worker thread was spawned


def test_inflight_slot_released_after_successful_call(monkeypatch):
    # A completed call must return its slot; otherwise the cap would leak slots over time.
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(genie_tools, "_INFLIGHT", sem)

    def ask_fn(s, q, cf):
        return {"result": "ok", "query": None, "description": None}

    genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=[],
        ask_fn=ask_fn,
        timeout_s=5,
    )
    assert sem.acquire(blocking=False)  # the single slot is free again
    sem.release()


def test_timed_out_thread_releases_its_slot_when_bounded_call_finishes(monkeypatch):
    # A call abandoned on the wall-clock timeout holds its slot only until the (now
    # time-bounded) underlying call finishes; then the worker thread releases it and
    # dies, so the slot is reclaimed rather than leaked.
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(genie_tools, "_INFLIGHT", sem)
    done = threading.Event()

    def ask_fn(s, q, cf):
        time.sleep(0.2)  # exceeds the 0.05s wall-clock bound, but is itself bounded
        done.set()
        return {"result": "late", "query": None, "description": None}

    collector: list[dict] = []
    out = genie_tools.consult_genie(
        space_id="S",
        label="operational",
        question="q",
        client_factory=_client_factory,
        collector=collector,
        ask_fn=ask_fn,
        timeout_s=0.05,
    )
    assert collector[0]["status"] == "timeout"
    assert "did not answer within" in out
    # While the underlying call is still running, the slot is held.
    assert sem.acquire(blocking=False) is False
    # Once the bounded underlying call finishes, the worker releases the slot.
    assert done.wait(2.0)
    deadline = time.time() + 1.0
    while time.time() < deadline:
        if sem.acquire(blocking=False):
            sem.release()
            break
        time.sleep(0.01)
    else:
        raise AssertionError("timed-out worker never released its in-flight slot")
