"""Claims-adjudication agent — MLflow 3 ResponsesAgent over a LangGraph tool loop.

Decision flow per adjudication (the money is decided by deterministic tools; the
LLM only reasons, cites, and recommends):

1. RESOLVE-ONCE — bind the claim's coil to a frozen policy snapshot.
2. Deterministic authorities + duplicate gate — these decide verdict eligibility
   and the amount. Run unconditionally, before the LLM sees anything.
3. Advisory context — retrieve citable clauses, similar prior claims, and
   customer/heat risk.
4. LLM — a LangGraph tool-calling loop reasons over the evidence, then a
   NON-STREAMING ``response_format`` JSON-Schema call emits a structured
   recommendation, validated by Pydantic.
5. CODE-LEVEL INVARIANTS — enforce that a duplicate is never payable, that the
   approved amount equals the settlement authority output exactly, and that the
   verdict is consistent with eligibility. On any violation the recommendation is
   corrected to the deterministic outcome (the LLM never wins) and the violation
   is recorded.
6. PERSIST — one transaction writes the recommendation into ``adjudications`` and
   the canonical row into ``adjudication_decision_records`` (atomic, idempotent).

Every step is a child span under a root AGENT trace with searchable attributes.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Generator

import mlflow
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

import agent_tools
from decision_record import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    Recommendation,
    build_decision_record,
    enforce_invariants,
    recommendation_json_schema,
    source_sha256,
)
from writer import write_adjudication

LLM_ENDPOINT = "databricks-gpt-5-2"
MODEL_NAME = "fe-bar-ir.default.claims_adjudication_agent"
_HERE = os.path.dirname(os.path.abspath(__file__))


def _authorities_path() -> str:
    """Resolve the imported authority module in source and MLflow artifact layouts.

    Models-from-code keeps ``agent.py`` at the model root and adds each
    ``code_paths`` entry under ``model/code`` to ``sys.path``. Consequently an
    imported sibling's ``__file__`` is authoritative; joining against the model
    file's directory is not.
    """
    import authorities

    return authorities.__file__


SYSTEM_PROMPT = (
    "You are a steel quality/warranty claims adjudication assistant. Deterministic "
    "tools have ALREADY decided the money and the verdict eligibility for this claim: "
    "the conformance, coverage, and settlement authorities and the duplicate gate are "
    "AUTHORITATIVE. You must NOT change any authority's number or verdict. Your job is "
    "to reason over the evidence, cite the applicable clauses by their natural clause "
    "keys, note advisory precedent and risk, and produce a recommendation that AGREES "
    "with the deterministic outcome. Rules you must follow exactly: a duplicate is "
    "DENY/DUPLICATE and never payable; an in-spec material claim is DENY; an "
    "out-of-coverage warranty claim is DENY; the settlement_estimate MUST equal the "
    "settlement authority's approved amount; you may only escalate to PEND_INVESTIGATE "
    "as a safe hold, never convert a denial into a payment."
)


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=_HERE, capture_output=True, text=True, check=False
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _evidence_block(core: dict) -> str:
    """A compact, PII-light JSON summary of the deterministic + advisory evidence."""
    det = core["deterministic"]
    return json.dumps(
        {
            "claim_type": core.get("claim_type"),
            "conformance": core["conformance"],
            "coverage": core["coverage"],
            "settlement": core["settlement"],
            "duplicate": {
                "is_duplicate": core["duplicate"].get("is_duplicate"),
                "duplicate_of_claim_id": core["duplicate"].get("duplicate_of_claim_id"),
            },
            "deterministic_outcome": det,
            "cited_clause_ids": [c["citation_key"] for c in core.get("citations", [])],
            "advisory_precedent": core.get("precedent", []),
            "advisory_risk_score": (core.get("risk") or {}).get("risk_score"),
        },
        default=str,
        sort_keys=True,
    )


class ClaimsAdjudicationAgent(ResponsesAgent):
    def __init__(self):
        self.profile = os.environ.get("LAKEBASE_PROFILE") or None
        self.model_version = os.environ.get("AGENT_MODEL_VERSION")
        self._llm = None
        self._llm_with_tools = None

    # --- LLM wiring (lazy so the class imports without a live endpoint) ---------
    def _llm_client(self):
        if self._llm is None:
            from databricks_langchain import ChatDatabricks

            self._llm = ChatDatabricks(endpoint=LLM_ENDPOINT, temperature=0.0)
        return self._llm

    def _embed_fn(self):
        from gateway_embed import embed_texts

        return lambda texts: embed_texts(texts, profile=self.profile or "fe-bar")

    # --- Deterministic core with per-step tracing -------------------------------
    def _deterministic_core(self, conn: Any, claim: dict) -> dict:
        with mlflow.start_span(name="resolve_once", span_type="TOOL") as span:
            runtime = agent_tools.AuthorityRuntime(conn)
            frozen = runtime.freeze(claim["coil_id"])
            heat_no = agent_tools.resolve_heat_no(conn, claim["coil_id"])
            span.set_attributes({"coil_id": claim["coil_id"], "heat_no": heat_no})

        with mlflow.start_span(name="compute_conformance", span_type="TOOL") as span:
            conformance = frozen.conformance()
            span.set_attribute("conforms", conformance["conforms"])
        with mlflow.start_span(name="compute_coverage", span_type="TOOL") as span:
            coverage = frozen.coverage(claim)
            span.set_attribute("covered", coverage["covered"])
        with mlflow.start_span(name="compute_settlement", span_type="TOOL") as span:
            settlement = frozen.settlement(agent_tools.settlement_inputs(claim, coverage))
            span.set_attribute("approved_amount", settlement["approved_amount"])
        with mlflow.start_span(name="check_duplicate_claim", span_type="TOOL") as span:
            duplicate = agent_tools.check_duplicate_claim(conn, claim)
            span.set_attribute("is_duplicate", bool(duplicate.get("is_duplicate")))

        deterministic = agent_tools.deterministic_outcome(
            claim.get("claim_type"), conformance, coverage, settlement, duplicate
        )

        with mlflow.start_span(name="retrieve_policy_clauses", span_type="RETRIEVER") as span:
            clauses = agent_tools.retrieve_clauses(conn, frozen, claim)
            citations = agent_tools.build_citations(clauses)
            span.set_attribute("cited_clause_ids", [c["citation_key"] for c in citations])
        precedent: list[dict] = []
        with mlflow.start_span(name="find_similar_prior_claims", span_type="RETRIEVER"):
            try:
                precedent = agent_tools.find_precedent(conn, frozen, claim, self._embed_fn())
            except Exception:
                precedent = []
        with mlflow.start_span(name="get_customer_heat_risk", span_type="TOOL") as span:
            try:
                risk = agent_tools.get_customer_heat_risk(conn, claim.get("customer_id"), heat_no)
            except Exception:
                risk = {"risk_score": 0.0, "found": False, "cluster_id": None}
            span.set_attribute("risk_score", float(risk.get("risk_score") or 0.0))

        return {
            "claim_type": claim.get("claim_type"),
            "frozen": frozen,
            "resolved": frozen.resolved,
            "measured": frozen.measured,
            "heat_no": heat_no,
            "conformance": conformance,
            "coverage": coverage,
            "settlement": settlement,
            "duplicate": duplicate,
            "deterministic": deterministic,
            "clauses": clauses,
            "citations": citations,
            "precedent": precedent,
            "risk": risk,
        }

    # --- LLM reasoning + structured recommendation ------------------------------
    def _llm_recommendation(self, core: dict) -> tuple[dict, bool]:
        """Run the tool-calling loop, then a structured-output call. Falls back to the
        deterministic recommendation if the endpoint is unreachable."""
        evidence = _evidence_block(core)
        try:
            with mlflow.start_span(name="agent_reasoning", span_type="CHAT_MODEL"):
                reasoning = self._run_graph(core, evidence)
            with mlflow.start_span(name="structured_recommendation", span_type="CHAT_MODEL"):
                rec = self._structured_call(evidence, reasoning)
            return rec, True
        except Exception as exc:  # endpoint unreachable / invalid output -> safe fallback
            mlflow.get_current_active_span() and mlflow.get_current_active_span().set_attribute(
                "llm_fallback_reason", str(exc)[:200]
            )
            return agent_tools.default_recommendation(core), False

    def _tools(self, core: dict):
        """Expose the required tools as read-only views of the frozen tool results.

        The deterministic core has already resolved the policy snapshot and run every
        tool exactly once. These wrappers let LangGraph reason with the named tools
        without re-resolving policy or giving the LLM a path to rerun/change money.
        """
        from langchain_core.tools import StructuredTool

        def frozen_result(key: str):
            return lambda: json.dumps(core[key], default=str, sort_keys=True)

        return [
            StructuredTool.from_function(
                func=frozen_result(key),
                name=name,
                description=description,
            )
            for name, description in (
                ("compute_conformance", "Return the frozen deterministic conformance result."),
                ("compute_coverage", "Return the frozen deterministic warranty coverage result."),
                ("compute_settlement", "Return the frozen deterministic settlement amount."),
                ("check_duplicate_claim", "Return the frozen deterministic duplicate-gate result."),
                (
                    "retrieve_policy_clauses",
                    "Return clauses resolved from the frozen policy snapshot.",
                ),
                ("find_similar_prior_claims", "Return advisory similar prior claims."),
                ("get_customer_heat_risk", "Return advisory customer/heat risk."),
            )
            for key in [
                {
                    "compute_conformance": "conformance",
                    "compute_coverage": "coverage",
                    "compute_settlement": "settlement",
                    "check_duplicate_claim": "duplicate",
                    "retrieve_policy_clauses": "citations",
                    "find_similar_prior_claims": "precedent",
                    "get_customer_heat_risk": "risk",
                }[name]
            ]
        ]

    def _run_graph(self, core: dict, evidence: str) -> str:
        from typing import Annotated, Sequence, TypedDict

        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        from langgraph.graph import END, StateGraph
        from langgraph.graph.message import add_messages
        from langgraph.prebuilt.tool_node import ToolNode

        tools = self._tools(core)
        llm_with_tools = self._llm_client().bind_tools(tools)

        class State(TypedDict):
            messages: Annotated[Sequence, add_messages]

        def call_model(state):
            return {"messages": [llm_with_tools.invoke(state["messages"])]}

        def should_continue(state):
            last = state["messages"][-1]
            return "tools" if isinstance(last, AIMessage) and last.tool_calls else "end"

        graph = StateGraph(State)
        graph.add_node("agent", call_model)
        graph.add_node("tools", ToolNode(tools))
        graph.set_entry_point("agent")
        graph.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
        graph.add_edge("tools", "agent")
        compiled = graph.compile()
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Adjudicate this claim. Deterministic evidence (authoritative):\n"
                    + evidence
                    + "\nReason briefly about the verdict, disposition, and which clauses to cite."
                )
            ),
        ]
        result = compiled.invoke({"messages": messages}, {"recursion_limit": 8})
        final = result["messages"][-1]
        return getattr(final, "content", "") or ""

    def _structured_call(self, evidence: str, reasoning: str) -> dict:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self._llm_client().bind(response_format=recommendation_json_schema())
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Deterministic evidence (authoritative):\n"
                    + evidence
                    + "\n\nYour reasoning:\n"
                    + reasoning
                    + "\n\nEmit ONLY the structured recommendation JSON. settlement_estimate "
                    "MUST equal the settlement authority's approved amount; cite natural "
                    "clause keys in cited_clause_ids."
                )
            ),
        ]
        response = llm.invoke(messages)
        content = (
            response.content if isinstance(response.content, str) else json.dumps(response.content)
        )
        recommendation = Recommendation.model_validate_json(content)
        return recommendation.model_dump()

    # --- Orchestration ----------------------------------------------------------
    def adjudicate(self, claim: dict, persist: bool = True) -> dict:
        reproducibility = {
            "authorities_git_sha": _git_sha(),
            "authorities_source_sha256": source_sha256(_authorities_path()),
            "agent_model_name": MODEL_NAME,
            "agent_model_version": self.model_version,
            "reasoning_endpoint": LLM_ENDPOINT,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "mlflow_trace_id": None,
        }
        from db import connect

        with mlflow.start_span(name="adjudicate", span_type="AGENT") as root:
            reproducibility["mlflow_trace_id"] = getattr(root, "trace_id", None) or getattr(
                root, "request_id", None
            )
            with connect(profile=self.profile, autocommit=False) as conn:
                core = self._deterministic_core(conn, claim)
                raw_recommendation, llm_used = self._llm_recommendation(core)
                with mlflow.start_span(name="enforce_invariants", span_type="TOOL") as span:
                    corrected, violations = enforce_invariants(
                        raw_recommendation, core["deterministic"]
                    )
                    span.set_attribute("invariant_violations", violations)
                record = build_decision_record(
                    claim=claim,
                    resolved=core["resolved"],
                    measured=core["measured"],
                    conformance=core["conformance"],
                    coverage=core["coverage"],
                    settlement=core["settlement"],
                    duplicate=core["duplicate"],
                    deterministic=core["deterministic"],
                    recommendation=corrected,
                    invariant_violations=violations,
                    citations=core["citations"],
                    advisory_risk=core.get("risk"),
                    reproducibility=reproducibility,
                )
                write_result = {"persisted": False}
                if persist:
                    with mlflow.start_span(name="persist", span_type="TOOL") as span:
                        write_result = write_adjudication(conn, record)
                        write_result["persisted"] = True
                        span.set_attributes(
                            {
                                "adjudication_id": record["adjudication_id"],
                                "decision_record_inserted": write_result.get(
                                    "decision_record_inserted"
                                ),
                            }
                        )
            self._tag_trace(claim, record, violations, llm_used)
        return {
            "record": record,
            "recommendation": corrected,
            "deterministic": core["deterministic"],
            "invariant_violations": violations,
            "llm_used": llm_used,
            "write_result": write_result,
        }

    def _tag_trace(self, claim: dict, record: dict, violations: list[str], llm_used: bool) -> None:
        # Searchable root attributes — NO credentials/PII.
        try:
            mlflow.update_current_trace(
                tags={
                    "claim_id": str(claim.get("claim_id")),
                    "adjudication_id": record["adjudication_id"],
                    "claim_type": str(claim.get("claim_type")),
                    "deterministic_verdict": record["deterministic_verdict"],
                    "duplicate_flag": str(record["duplicate_flag"]),
                    "recommended_verdict": record["recommended_verdict"],
                    "recommended_disposition": record["recommended_disposition"],
                    "agent_model_name": MODEL_NAME,
                    "agent_model_version": str(self.model_version),
                    "authorities_git_sha": str(record.get("authorities_git_sha")),
                    "cited_clause_ids": ",".join(record.get("cited_clause_ids") or []),
                    "invariant_violations": ",".join(violations),
                    "llm_used": str(llm_used),
                }
            )
        except Exception:
            pass

    # --- ResponsesAgent interface -----------------------------------------------
    def _extract_claim(self, request: ResponsesAgentRequest) -> dict:
        custom = getattr(request, "custom_inputs", None) or {}
        if isinstance(custom, dict) and isinstance(custom.get("claim"), dict):
            return custom["claim"]
        for message in reversed([m.model_dump() for m in request.input]):
            if message.get("role") == "user":
                content = message.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        part.get("text", "") for part in content if isinstance(part, dict)
                    )
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and parsed.get("claim_id"):
                        return parsed
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
        raise ValueError("No claim JSON found in request input or custom_inputs.claim")

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        claim = self._extract_claim(request)
        custom = getattr(request, "custom_inputs", None) or {}
        persist = bool(custom.get("persist", True)) if isinstance(custom, dict) else True
        outcome = self.adjudicate(claim, persist=persist)
        record = outcome["record"]
        summary = (
            f"Recommendation for claim {claim.get('claim_id')}: "
            f"{record['recommended_verdict']} / {record['recommended_disposition']} "
            f"(approved {record['approved_amount']}). "
            f"Deterministic verdict {record['deterministic_verdict']}; "
            f"invariant violations: {outcome['invariant_violations'] or 'none'}."
        )
        return ResponsesAgentResponse(
            output=[self.create_text_output_item(text=summary, id="msg_recommendation")],
            custom_outputs={
                "recommendation": outcome["recommendation"],
                "deterministic": outcome["deterministic"],
                "invariant_violations": outcome["invariant_violations"],
                "adjudication_id": record["adjudication_id"],
                "cited_clause_ids": record["cited_clause_ids"],
                "llm_used": outcome["llm_used"],
                "write_result": outcome["write_result"],
            },
        )

    def predict_stream(
        self, request: ResponsesAgentRequest
    ) -> Generator[ResponsesAgentStreamEvent, None, None]:
        response = self.predict(request)
        for item in response.output:
            yield ResponsesAgentStreamEvent(type="response.output_item.done", item=item)


mlflow.langchain.autolog()
AGENT = ClaimsAdjudicationAgent()
mlflow.models.set_model(AGENT)
