"""Live, model-driven Genie tool — the agent's first NON-deterministic tool.

One governed Genie Agent (formerly Genie Space) is exposed to the reasoning LLM
as a natural-language query tool it MAY call while reasoning about a claim:

* ``query_claims_genie`` -> the operational per-claim Genie Agent (current
  claims, adjudications, persisted decision records, manufacturing context,
  advisory customer/heat risk, and prior-claim precedent).

Unlike the frozen echo tools in ``agent.ClaimsAdjudicationAgent._tools`` (which
replay already-computed deterministic results), this calls Genie live with a
model-authored question, so the question and the answer are non-deterministic.

ADVISORY-ONLY INVARIANT. This tool lives ONLY in the reasoning loop
(``agent._run_graph``); it is NOT part of the deterministic core and runs AFTER
the authorities + duplicate gate have already decided the money and the verdict
eligibility. ``decision_record.enforce_invariants`` then corrects the LLM's
recommendation back to the deterministic outcome, so a Genie answer can NEVER
change the verdict, the eligibility, or the amount — it only informs the LLM's
rationale and narrative.

ROBUSTNESS. Each call runs in its own short-lived daemon thread bounded by a
wall-clock timeout. Because a daemon thread cannot be force-killed, the worker is
kept from hanging forever by giving the underlying Genie client a HARD
per-request HTTP/socket timeout AND a capped retry window (``_bound_client_http_timeout``):
a stalled request raises instead of blocking, so the worker thread actually
TERMINATES (returns or errors) within bounds rather than leaking a thread stuck
on a dead socket. A module-level concurrency cap (``GENIE_MAX_INFLIGHT``) is the
backstop: it bounds the number of simultaneously in-flight Genie threads, so even
under repeated hangs daemon threads (and their sockets) cannot accumulate
unbounded — once the cap is reached, a new call sheds load with a graceful
"capacity" string instead of spawning yet another thread. Every failure (timeout,
capacity, 403/permission, Genie/HTTP error, empty answer) is swallowed into a
short graceful string returned to the LLM, so a Genie outage never hangs or fails
an adjudication: the reasoning simply proceeds on the deterministic + frozen
evidence.

AUDITABILITY. Every call is captured twice: as a ``genie_<label>`` MLflow span
(the question in; the status/answer/SQL/latency out) AND as a structured
consultation row appended to the per-adjudication collector, which
``agent.adjudicate`` folds into the persisted decision record
(``flags.genie_consultations``). Any adjudication that consulted Genie is
therefore fully auditable from the trace and from the immutable record.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

import mlflow

# The governed Genie Agent. Centralised here (not in agent.py) so the live space
# wiring lives in one auditable place alongside the tool semantics.
OPERATIONAL_SPACE_ID = "01f1c269ca3c1adea7feb9f248ab3445"

# Per-call wall-clock bound (seconds); override with GENIE_TIMEOUT_S on the endpoint.
DEFAULT_TIMEOUT_S = 45.0

# Hard per-request HTTP/socket timeout and retry-window cap imposed on the underlying
# Genie client so a stalled call errors out and the worker thread terminates within
# bounds (see _bound_client_http_timeout). Overridable with GENIE_HTTP_TIMEOUT_S; it
# defaults to the wall-clock bound so the socket gives up around the same time.
DEFAULT_HTTP_TIMEOUT_S = DEFAULT_TIMEOUT_S

# Backstop cap on concurrently in-flight Genie worker threads so repeated hangs cannot
# let daemon threads (and their sockets) accumulate unbounded. Overridable with
# GENIE_MAX_INFLIGHT. Sized well above the realistic per-endpoint concurrency; a call
# that cannot acquire a slot sheds load gracefully instead of spawning a thread.
DEFAULT_MAX_INFLIGHT = 32

# Cap the returned/persisted answer so a large Genie result table cannot bloat the
# decision record or the LLM context.
_ANSWER_MAX_CHARS = 4000


class _GenieTimeout(Exception):
    """Raised when a single Genie call exceeds its wall-clock bound."""


class _GenieBusy(Exception):
    """Raised when the in-flight concurrency cap is saturated (backpressure)."""


def _timeout_s() -> float:
    try:
        return float(os.environ.get("GENIE_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _http_timeout_s() -> float:
    """Hard per-request HTTP/socket timeout for the underlying Genie client.

    Defaults to the wall-clock bound so a single stalled request raises around the
    same time the wall-clock join would abandon the thread; override independently
    with ``GENIE_HTTP_TIMEOUT_S``.
    """
    raw = os.environ.get("GENIE_HTTP_TIMEOUT_S")
    if raw is None:
        return _timeout_s()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _timeout_s()


def _max_inflight() -> int:
    try:
        value = int(os.environ.get("GENIE_MAX_INFLIGHT", DEFAULT_MAX_INFLIGHT))
    except (TypeError, ValueError):
        return DEFAULT_MAX_INFLIGHT
    return value if value > 0 else DEFAULT_MAX_INFLIGHT


# Module-level backstop: a bounded semaphore capping simultaneously in-flight Genie
# worker threads. This is NOT a worker pool (each call still gets its own isolated
# daemon thread); it only bounds how many may run at once so repeated hangs cannot
# accumulate threads/sockets without limit. Tests may monkeypatch this.
_INFLIGHT = threading.BoundedSemaphore(_max_inflight())


def _bound_client_http_timeout(client: Any, timeout_s: float | None = None) -> Any:
    """Impose a HARD per-request HTTP/socket timeout (and a matching retry-window cap)
    on the SDK ``WorkspaceClient`` so a stalled Genie request raises instead of hanging
    the worker thread forever.

    The databricks-sdk captures ``http_timeout_seconds`` (default 60) and
    ``retry_timeout_seconds`` (default 300) when the low-level client is BUILT, so
    mutating only ``config`` on an already-constructed client would not take effect for
    the live request path. We therefore set the bound on ``config`` AND, best-effort,
    on the live low-level client (``client.api_client`` -> ``_api_client``). Every write
    is guarded so an unexpected client shape degrades to the SDK default rather than
    raising — the concurrency cap remains the hard backstop regardless.
    """
    bound = _http_timeout_s() if timeout_s is None else timeout_s
    config = getattr(client, "config", None)
    if config is not None:
        try:
            config.http_timeout_seconds = bound
            config.retry_timeout_seconds = bound
        except Exception:  # noqa: BLE001 - config shape differences must not break the call
            pass
    api_client = getattr(client, "api_client", None)
    base = getattr(api_client, "_api_client", None)
    if base is not None:
        for attr in ("_http_timeout_seconds", "_retry_timeout_seconds"):
            if hasattr(base, attr):
                try:
                    setattr(base, attr, bound)
                except Exception:  # noqa: BLE001 - internal shape differences are non-fatal
                    pass
    return client


def _ask_bounded(
    space_id: str,
    question: str,
    client_factory: Callable[[], Any],
    ask_fn: Callable[[str, str, Callable[[], Any]], dict],
    timeout_s: float,
) -> dict:
    """Run ``ask_fn`` in an ISOLATED, short-lived daemon thread, bounded by ``timeout_s``.

    Each Genie call gets its own daemon thread — there is NO shared worker pool to
    exhaust, so repeated hangs cannot starve other adjudications. Two things keep an
    abandoned worker from leaking forever: the underlying client carries a hard
    HTTP/socket timeout (see ``_bound_client_http_timeout`` on the ``_ask`` path) so a
    stalled call raises and the thread terminates within bounds, and a module-level
    ``_INFLIGHT`` semaphore caps how many worker threads may be alive at once. A call
    that cannot acquire a slot raises ``_GenieBusy`` (no thread is spawned); a call that
    exceeds the wall-clock bound raises ``_GenieTimeout`` and is abandoned, but it holds
    its slot only until the (now time-bounded) underlying call returns or errors, at
    which point the thread releases the slot and dies.
    """
    if not _INFLIGHT.acquire(blocking=False):
        raise _GenieBusy()
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = ask_fn(space_id, question, client_factory)
        except Exception as exc:  # re-raised in the calling thread below
            box["error"] = exc
        finally:
            _INFLIGHT.release()

    worker = threading.Thread(target=target, name="genie-ask", daemon=True)
    try:
        worker.start()
    except Exception:
        _INFLIGHT.release()  # thread never started; return the slot immediately
        raise
    worker.join(timeout_s)
    if worker.is_alive():
        raise _GenieTimeout()
    if "error" in box:
        raise box["error"]
    return box.get("result") or {}


def _ask(space_id: str, question: str, client_factory: Callable[[], Any]) -> dict:
    """Call the Genie Agent once via the databricks-ai-bridge client (SP-authed).

    Imported lazily so this module imports without ``databricks-ai-bridge`` present
    (local dev / unit tests); the dependency is pinned for the serving container in
    ``register_agent.PIP_REQUIREMENTS``. ``client_factory`` yields the dedicated
    serving-SP ``WorkspaceClient`` (same auth as the rest of the agent), so Genie's
    generated SQL runs as the SP against the space's warehouse and tables. The client
    is given a hard HTTP/socket timeout first so this call cannot hang the worker
    thread on a dead socket.
    """
    from databricks_ai_bridge.genie import Genie

    client = _bound_client_http_timeout(client_factory())
    response = Genie(space_id, client=client).ask_question(question)
    return {
        "result": getattr(response, "result", None),
        "query": getattr(response, "query", None),
        "description": getattr(response, "description", None),
    }


def _classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "403" in message or "forbidden" in message or "permission" in message:
        return "forbidden"
    return "error"


def consult_genie(
    *,
    space_id: str,
    label: str,
    question: str,
    client_factory: Callable[[], Any],
    collector: list[dict],
    timeout_s: float | None = None,
    ask_fn: Callable[[str, str, Callable[[], Any]], dict] = _ask,
) -> str:
    """Ask the Genie Agent a NL question; audit it; return a string for the LLM.

    Never raises: a timeout, a capacity rejection, a 403/permission error, any
    Genie/HTTP error, or an empty answer all become a short graceful string so the
    adjudication proceeds on the deterministic + frozen evidence. Appends exactly one
    consultation row to ``collector`` and emits a ``genie_<label>`` span capturing the
    question and the outcome. ``ask_fn`` is injectable for unit testing.
    """
    bound = _timeout_s() if timeout_s is None else timeout_s
    started = time.time()
    status = "ok"
    answer = ""
    generated_sql = None
    with mlflow.start_span(name=f"genie_{label}", span_type="TOOL") as span:
        span.set_inputs({"space": label, "space_id": space_id, "question": question})
        try:
            payload = _ask_bounded(space_id, question, client_factory, ask_fn, bound)
            answer = (payload.get("result") or payload.get("description") or "").strip()
            generated_sql = payload.get("query")
            if not answer:
                status = "empty"
                answer = f"Genie ({label}) returned no answer for this question."
        except _GenieBusy:
            status = "busy"
            answer = (
                f"Genie ({label}) is at capacity ({_max_inflight()} concurrent calls); "
                "proceeding without it."
            )
        except _GenieTimeout:
            status = "timeout"
            answer = f"Genie ({label}) did not answer within {bound:.0f}s; proceeding without it."
        except Exception as exc:  # noqa: BLE001 - advisory tool must never break adjudication
            status = _classify_error(exc)
            answer = f"Genie ({label}) unavailable ({status}): {str(exc)[:200]}"
        answer = answer[:_ANSWER_MAX_CHARS]
        latency_ms = int((time.time() - started) * 1000)
        span.set_outputs(
            {
                "status": status,
                "answer": answer[:1000],
                "generated_sql": generated_sql,
                "latency_ms": latency_ms,
            }
        )
    collector.append(
        {
            "space": label,
            "space_id": space_id,
            "question": question,
            "status": status,
            "answer": answer,
            "generated_sql": generated_sql,
            "latency_ms": latency_ms,
        }
    )
    return answer


# Tool description the LLM reads. Explicit about scope and that the answers are
# ADVISORY context — never the money.
_OPERATIONAL_DESCRIPTION = (
    "ADVISORY. Ask a natural-language question of the operational claims Genie Agent "
    "over current claims, adjudications, persisted decision records, manufacturing "
    "context (coils/heats/mill-test-certs), advisory customer/heat risk, and "
    "prior-claim precedent. Use it for per-claim history and context (for example, "
    "'how were prior claims on this coil's heat adjudicated?'). Input: a single "
    "question string. The answer is ADVISORY background for your rationale ONLY; it "
    "does not and must not change the deterministic verdict, eligibility, or amount, "
    "and must not be cited as a policy clause."
)

# (space_id, label, tool_name, description) for the single bound tool.
_TOOL_SPECS = (
    (OPERATIONAL_SPACE_ID, "operational", "query_claims_genie", _OPERATIONAL_DESCRIPTION),
)


def build_genie_tools(client_factory: Callable[[], Any], collector: list[dict]):
    """Return the operational Genie ``StructuredTool`` bound to the SP client + audit collector.

    ``client_factory`` yields a fresh serving-SP ``WorkspaceClient`` per call (auth is
    refreshed the same way the rest of the agent refreshes it). ``collector`` is the
    per-adjudication list every call appends its audit row to.
    """
    from langchain_core.tools import StructuredTool

    def _make(space_id: str, label: str, name: str, description: str):
        def _tool(question: str) -> str:
            return consult_genie(
                space_id=space_id,
                label=label,
                question=question,
                client_factory=client_factory,
                collector=collector,
            )

        return StructuredTool.from_function(func=_tool, name=name, description=description)

    return [_make(space_id, label, name, desc) for space_id, label, name, desc in _TOOL_SPECS]
