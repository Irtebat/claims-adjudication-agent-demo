"""Live, model-driven Genie tools — the agent's first NON-deterministic tools.

Two governed Genie Agents (formerly Genie Spaces) are exposed to the reasoning
LLM as natural-language query tools it MAY call while reasoning about a claim:

* ``query_claims_genie``    -> the operational per-claim Genie Agent (current
  claims, adjudications, persisted decision records, manufacturing context,
  advisory customer/heat risk, and prior-claim precedent).
* ``query_analytics_genie`` -> the gold KPI / portfolio-analytics Genie Agent
  (quality-claims metrics, failure-mode, supplier-recovery, fraud-cluster, and
  agent/human-alignment analytics).

Unlike the frozen echo tools in ``agent.ClaimsAdjudicationAgent._tools`` (which
replay already-computed deterministic results), these call Genie live with a
model-authored question, so the question and the answer are non-deterministic.

ADVISORY-ONLY INVARIANT. These tools live ONLY in the reasoning loop
(``agent._run_graph``); they are NOT part of the deterministic core and run
AFTER the authorities + duplicate gate have already decided the money and the
verdict eligibility. ``decision_record.enforce_invariants`` then corrects the
LLM's recommendation back to the deterministic outcome, so a Genie answer can
NEVER change the verdict, the eligibility, or the amount — it only informs the
LLM's rationale and narrative.

ROBUSTNESS. Each call is bound by a wall-clock timeout and every failure
(timeout, 403/permission, Genie/HTTP error, empty answer) is swallowed into a
short graceful string returned to the LLM. A Genie outage therefore never hangs
or fails an adjudication: the reasoning simply proceeds on the deterministic +
frozen evidence.

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

# The two governed Genie Agents. Centralised here (not in agent.py) so the live
# space wiring lives in one auditable place alongside the tool semantics.
OPERATIONAL_SPACE_ID = "01f1c269ca3c1adea7feb9f248ab3445"
ANALYTICS_SPACE_ID = "01f1c2698a5418298f81f9e79df576ca"

# Per-call wall-clock bound (seconds); override with GENIE_TIMEOUT_S on the endpoint.
DEFAULT_TIMEOUT_S = 45.0

# Cap the returned/persisted answer so a large Genie result table cannot bloat the
# decision record or the LLM context.
_ANSWER_MAX_CHARS = 4000


class _GenieTimeout(Exception):
    """Raised when a single Genie call exceeds its wall-clock bound."""


def _timeout_s() -> float:
    try:
        return float(os.environ.get("GENIE_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _ask_bounded(
    space_id: str,
    question: str,
    client_factory: Callable[[], Any],
    ask_fn: Callable[[str, str, Callable[[], Any]], dict],
    timeout_s: float,
) -> dict:
    """Run ``ask_fn`` in an ISOLATED, short-lived daemon thread, bounded by ``timeout_s``.

    Each Genie call gets its own daemon thread — there is NO shared worker pool to
    exhaust, so repeated hangs cannot starve other adjudications. A call that exceeds
    the bound is abandoned (``_GenieTimeout`` raised to the caller); the daemon thread
    holds no shared resource, is never joined at interpreter exit, and dies with the
    process, so Genie degradation stays fully contained while the adjudication proceeds
    on the deterministic + frozen evidence.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = ask_fn(space_id, question, client_factory)
        except Exception as exc:  # re-raised in the calling thread below
            box["error"] = exc

    worker = threading.Thread(target=target, name="genie-ask", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise _GenieTimeout()
    if "error" in box:
        raise box["error"]
    return box.get("result") or {}


def _ask(space_id: str, question: str, client_factory: Callable[[], Any]) -> dict:
    """Call one Genie Agent once via the databricks-ai-bridge client (SP-authed).

    Imported lazily so this module imports without ``databricks-ai-bridge`` present
    (local dev / unit tests); the dependency is pinned for the serving container in
    ``register_agent.PIP_REQUIREMENTS``. ``client_factory`` yields the dedicated
    serving-SP ``WorkspaceClient`` (same auth as the rest of the agent), so Genie's
    generated SQL runs as the SP against the space's warehouse and tables.
    """
    from databricks_ai_bridge.genie import Genie

    response = Genie(space_id, client=client_factory()).ask_question(question)
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
    """Ask one Genie Agent a NL question; audit it; return a string for the LLM.

    Never raises: a timeout, a 403/permission error, any Genie/HTTP error, or an
    empty answer all become a short graceful string so the adjudication proceeds on
    the deterministic + frozen evidence. Appends exactly one consultation row to
    ``collector`` and emits a ``genie_<label>`` span capturing the question and the
    outcome. ``ask_fn`` is injectable for unit testing.
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


# Tool descriptions the LLM reads to pick the right space. Explicit about scope and
# that the answers are ADVISORY context — never the money.
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
_ANALYTICS_DESCRIPTION = (
    "ADVISORY. Ask a natural-language question of the gold KPI / portfolio-analytics "
    "Genie Agent over quality-claims metrics, failure-mode, supplier-recovery, "
    "fraud-cluster, and agent/human-alignment analytics. Use it for aggregate or "
    "business context (for example, 'what is the recent approval rate for this failure "
    "mode?'). Input: a single question string. The answer is ADVISORY background for "
    "your rationale ONLY; it does not and must not change the deterministic verdict, "
    "eligibility, or amount, and must not be cited as a policy clause."
)

# (space_id, label, tool_name, description) for the two bound tools.
_TOOL_SPECS = (
    (OPERATIONAL_SPACE_ID, "operational", "query_claims_genie", _OPERATIONAL_DESCRIPTION),
    (ANALYTICS_SPACE_ID, "analytics", "query_analytics_genie", _ANALYTICS_DESCRIPTION),
)


def build_genie_tools(client_factory: Callable[[], Any], collector: list[dict]):
    """Return the two Genie ``StructuredTool``s bound to the SP client + audit collector.

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
