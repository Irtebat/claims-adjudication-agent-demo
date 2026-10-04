"""Paired, same-data/same-scorer ablation evaluation harness."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import multiprocessing
import os
import sys
import tempfile
import time
import traceback
import uuid
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.config import Config

from build_dataset import _execute_sql, make_record, source_sql, stable_holdout
from heldout import approval_subchoice, disposition_class
from predict import PersistenceViolation, load_candidate, predict_claim
from resolver_oracle import AGENT_SRC, ResolverOracle

MODEL_URI = "models:/fe-bar-ir.default.claims_adjudication_agent@prod"
DEFAULT_EXPERIMENT = "/Shared/claims-adjudication-ablation"
CANDIDATE_ALIASES = {"agent@prod": MODEL_URI, "deterministic_baseline": "deterministic_baseline"}
NA = "N/A"
QUALITY_DIMENSIONS = (
    "verdict",
    "disposition_class",
    "approval_subchoice",
    "amount",
    "duplicate",
    "pend_routing",
    "citation",
    "narrative_escalation_rate",
    "false_escalation_rate",
    "deciding_clause_cited",
    "judge",
)
SUMMARY_DIMENSIONS = QUALITY_DIMENSIONS + (
    "tokens_per_claim",
    "latency_ms_per_claim",
    "invariant_correction_rate",
)
DEFAULT_CLAIM_TIMEOUT_SECONDS = 120
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
WORKER_SHUTDOWN_GRACE_SECONDS = 10
WORKER_KILL_GRACE_SECONDS = 2
EARLY_FAILURE_LIMIT = 5


class ClaimTimeoutError(TimeoutError):
    """One candidate exceeded the per-claim wall-clock budget."""


class CandidateBatchFailure(RuntimeError):
    """A candidate failed every row in the initial diagnostic window."""

    def __init__(self, candidate: str, observations: list[dict]):
        self.candidate = candidate
        self.observations = observations
        errors = [row["error"] for row in observations]
        super().__init__(f"{candidate} failed its first {len(observations)} rows: {errors}")


@dataclass(frozen=True)
class WorkerSpec:
    kind: str
    value: str | None = None
    profile: str | None = None
    run_id: str | None = None
    candidate_name: str | None = None
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS


def _private_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _local_baseline_functions():
    """Load money modules by file path, independent of global import pollution."""
    agent_path = str(AGENT_SRC)
    private_names = (
        "_ablation_disposition_rules",
        "_ablation_decision_record",
        "_ablation_authorities_runtime",
        "_ablation_duplicate",
        "_ablation_db",
    )
    original_path = list(sys.path)
    previous_modules = {name: sys.modules.get(name) for name in private_names}
    previous_disposition = sys.modules.get("disposition_rules")
    try:
        sys.path[:] = [item for item in sys.path if item != agent_path]
        sys.path.insert(0, agent_path)
        disposition = _private_module(
            "_ablation_disposition_rules", AGENT_SRC / "disposition_rules.py"
        )
        sys.modules["disposition_rules"] = disposition
        decision = _private_module("_ablation_decision_record", AGENT_SRC / "decision_record.py")
        authority_runtime = _private_module(
            "_ablation_authorities_runtime", AGENT_SRC / "authorities_runtime.py"
        )
        duplicate = _private_module("_ablation_duplicate", AGENT_SRC / "duplicate.py")
        db = _private_module("_ablation_db", AGENT_SRC / "db.py")
        return authority_runtime, decision, duplicate, db
    finally:
        sys.path[:] = original_path
        if previous_disposition is None:
            sys.modules.pop("disposition_rules", None)
        else:
            sys.modules["disposition_rules"] = previous_disposition
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def _invoke_endpoint(
    workspace: WorkspaceClient, endpoint: str, claim: dict, candidate_name: str
) -> dict:
    response = workspace.api_client.do(
        "POST",
        f"/api/2.0/serving-endpoints/{quote(endpoint, safe='')}/invocations",
        body={
            "input": [{"role": "user", "content": json.dumps(claim, sort_keys=True)}],
            "custom_inputs": {"claim": claim, "persist": False},
        },
    )
    if _custom(response).get("write_result", {}).get("persisted") is not False:
        raise PersistenceViolation(f"{candidate_name}: endpoint persistence invariant failed")
    return response


def _detach_active_run(run_id: str) -> None:
    """Remove a resumed child run without changing its parent-owned status."""
    from mlflow.tracking import fluent

    stack = fluent._active_run_stack.get()
    for index in range(len(stack) - 1, -1, -1):
        if stack[index].info.run_id == run_id:
            stack.pop(index)
            return


def _worker_main(spec: WorkerSpec, connection) -> None:
    close = None
    try:
        # Packaged models construct their own Databricks SDK clients. Config reads
        # this setting, giving every such client a bounded connect/read timeout.
        os.environ["DATABRICKS_HTTP_TIMEOUT_SECONDS"] = str(spec.request_timeout_seconds)
        if spec.kind == "model":
            model = load_candidate(spec.value)

            def invoke(claim):
                return predict_claim(claim, model=model)

            deterministic = False
        elif spec.kind == "endpoint":
            workspace = WorkspaceClient(
                config=Config(
                    profile=spec.profile,
                    http_timeout_seconds=spec.request_timeout_seconds,
                    retry_timeout_seconds=spec.request_timeout_seconds,
                )
            )

            def invoke(claim):
                return _invoke_endpoint(
                    workspace, spec.value, claim, spec.candidate_name or spec.value
                )

            deterministic = False
        elif spec.kind == "deterministic":
            runtime, decision, duplicate, db = _local_baseline_functions()
            manager = db.connect(profile=spec.profile or "fe-bar", autocommit=True)
            lakebase = manager.__enter__()

            def close():
                manager.__exit__(None, None, None)

            def invoke(claim):
                context = _deterministic_context(
                    lakebase,
                    claim,
                    authority_runtime=runtime.AuthorityRuntime,
                    deterministic_outcome_fn=decision.deterministic_outcome,
                    duplicate_fn=duplicate.check_duplicate_claim,
                )
                return decision.deterministic_recommendation(context)

            deterministic = True
        elif spec.kind == "callable":
            invoke = _load_callable(spec.value)
            deterministic = False
        else:
            raise ValueError(f"unknown worker kind {spec.kind}")
        connection.send({"ready": True})
        run_context = mlflow.start_run(run_id=spec.run_id) if spec.run_id else nullcontext()
        with run_context:
            try:
                while True:
                    request = connection.recv()
                    if request is None:
                        break
                    try:
                        output = standardize_output(invoke(request), deterministic=deterministic)
                        connection.send({"output": output})
                    except BaseException as exc:
                        connection.send(
                            {
                                "exception_module": type(exc).__module__,
                                "exception_type": type(exc).__name__,
                                "message": str(exc),
                                "traceback": traceback.format_exc(),
                            }
                        )
            finally:
                mlflow.flush_trace_async_logging()
                if spec.run_id:
                    _detach_active_run(spec.run_id)
    except BaseException as exc:
        try:
            connection.send(
                {
                    "exception_module": type(exc).__module__,
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception:
            pass
    finally:
        if close:
            close()
        connection.close()


def _custom(output: dict) -> dict:
    return output.get("custom_outputs", output)


def standardize_output(output: Any, deterministic: bool = False) -> dict:
    if hasattr(output, "model_dump"):
        output = output.model_dump()
    if isinstance(output, list) and len(output) == 1:
        output = output[0]
    if not isinstance(output, dict):
        raise TypeError("candidate must return a dict")
    data = _custom(output)
    recommendation = data.get("recommendation", data)
    disposition = recommendation.get("recommended_disposition") or recommendation.get("disposition")
    verdict = recommendation.get("recommended_verdict") or recommendation.get("verdict")
    judge = data.get("judge_scores")
    if isinstance(judge, dict):
        judge = judge.get("overall")
    if isinstance(judge, str) and judge.lower() in {"yes", "no"}:
        judge = judge.lower() == "yes"
    elif isinstance(judge, str):
        judge = None
    return {
        "verdict": "PEND" if verdict == "PEND_INVESTIGATE" else verdict,
        "disposition_class": disposition_class(disposition, verdict),
        "approval_subchoice": approval_subchoice(disposition, verdict),
        "disposition": disposition,
        "approved_amount": recommendation.get("approved_amount"),
        "cited_clause_ids": None if deterministic else data.get("cited_clause_ids"),
        "narrative_conflict": None if deterministic else recommendation.get("narrative_conflict"),
        "judge_score": judge,
        "invariant_violations": None if deterministic else data.get("invariant_violations"),
        "usage": data.get("usage") or output.get("usage"),
        "raw": output,
    }


def _load_callable(path: str) -> Callable[[dict], dict]:
    module_name, separator, attribute = path.rpartition(".")
    if not separator:
        raise ValueError(f"callable candidate must be a dotted path, got {path!r}")
    return getattr(importlib.import_module(module_name), attribute)


def _deterministic_context(
    connection,
    claim: dict,
    *,
    authority_runtime=None,
    deterministic_outcome_fn=None,
    duplicate_fn=None,
) -> dict:
    if str(AGENT_SRC) not in sys.path:
        sys.path.insert(0, str(AGENT_SRC))
    if authority_runtime is None or deterministic_outcome_fn is None or duplicate_fn is None:
        try:
            from authorities_runtime import AuthorityRuntime
            from decision_record import deterministic_outcome
            from duplicate import check_duplicate_claim
        except (ImportError, AttributeError) as exc:
            raise ImportError(
                "deterministic baseline requires the agent authority runtime"
            ) from exc
        authority_runtime = authority_runtime or AuthorityRuntime
        deterministic_outcome_fn = deterministic_outcome_fn or deterministic_outcome
        duplicate_fn = duplicate_fn or check_duplicate_claim
    frozen = authority_runtime(connection).freeze(claim["coil_id"])
    conformance = frozen.conformance()
    coverage = frozen.coverage(claim)
    settlement = frozen.settlement(
        {
            "coil_id": claim["coil_id"],
            "claim_type": claim.get("claim_type"),
            "claimed_tonnage": claim.get("claimed_tonnage") or 0,
            "claimed_freight": claim.get("claimed_freight") or 0,
            "proration_factor": coverage.get("proration_factor", 1.0),
        }
    )
    duplicate = duplicate_fn(connection, claim)
    deterministic = deterministic_outcome_fn(
        claim.get("claim_type"), conformance, coverage, settlement, duplicate
    )
    return {
        "claim_type": claim.get("claim_type"),
        "frozen": frozen,
        "resolved": frozen.resolved,
        "measured": frozen.measured,
        "conformance": conformance,
        "coverage": coverage,
        "settlement": settlement,
        "duplicate": duplicate,
        "deterministic": deterministic,
        "clauses": [],
        "citations": [],
        "precedent": [],
        "risk": {"risk_score": 0.0, "found": False, "cluster_id": None},
    }


def _recommend_from_context(context: dict) -> dict:
    try:
        from decision_record import deterministic_recommendation
    except (ImportError, AttributeError) as exc:
        raise ImportError(
            "deterministic_baseline requires decision_record.deterministic_recommendation(context) "
            "from branch deterministic-ruleset; land that branch first"
        ) from exc
    return deterministic_recommendation(context)


def deterministic_baseline(claim: dict) -> dict:
    """One-off baseline entry point; run adapters reuse one connection instead."""
    runtime, decision, duplicate, db = _local_baseline_functions()
    profile = os.environ.get("LAKEBASE_PROFILE") or "fe-bar"
    with db.connect(profile=profile, autocommit=True) as connection:
        context = _deterministic_context(
            connection,
            claim,
            authority_runtime=runtime.AuthorityRuntime,
            deterministic_outcome_fn=decision.deterministic_outcome,
            duplicate_fn=duplicate.check_duplicate_claim,
        )
        return decision.deterministic_recommendation(context)


@dataclass
class CandidateAdapter:
    name: str
    invoke: Callable[[dict], dict]
    deterministic: bool = False
    close: Callable[[], None] | None = None
    timeout_seconds: float = DEFAULT_CLAIM_TIMEOUT_SECONDS
    startup_timeout_seconds: float = DEFAULT_CLAIM_TIMEOUT_SECONDS
    worker_spec: WorkerSpec | None = None
    _process: Any = None
    _connection: Any = None

    def _stop_worker(self) -> None:
        if self._process is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=WORKER_KILL_GRACE_SECONDS)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=WORKER_KILL_GRACE_SECONDS)
            if self._process.is_alive():
                raise RuntimeError(f"candidate {self.name} worker survived SIGKILL")
        if self._connection is not None:
            self._connection.close()
        self._process = self._connection = None

    def _start_worker(self) -> None:
        self._stop_worker()
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_worker_main, args=(self.worker_spec, child), daemon=True)
        process.start()
        child.close()
        self._process, self._connection = process, parent
        if not parent.poll(self.startup_timeout_seconds):
            self._stop_worker()
            raise ClaimTimeoutError(f"candidate {self.name} worker startup timed out")
        try:
            response = parent.recv()
        except EOFError as exc:
            process.join(timeout=0.5)
            exitcode = process.exitcode
            self._stop_worker()
            raise RuntimeError(
                f"candidate {self.name} worker exited during startup (exitcode={exitcode})"
            ) from exc
        if not response.get("ready"):
            self._stop_worker()
            self._raise_worker_error(response)

    @staticmethod
    def _raise_worker_error(response: dict) -> None:
        detail = response.get("traceback") or response.get("message") or repr(response)
        if response.get("exception_type") == "PersistenceViolation":
            raise PersistenceViolation(detail)
        raise RuntimeError(f"candidate worker failed:\n{detail}")

    def predict(self, claim: dict) -> tuple[dict, float]:
        if self.worker_spec is None:
            started = time.perf_counter()
            output = standardize_output(self.invoke(claim), deterministic=self.deterministic)
            return output, (time.perf_counter() - started) * 1000
        if self._process is None or not self._process.is_alive():
            self._start_worker()
        started = time.perf_counter()
        self._connection.send(claim)
        if not self._connection.poll(self.timeout_seconds):
            self._stop_worker()
            raise ClaimTimeoutError(
                f"candidate {self.name} exceeded {self.timeout_seconds}s for claim "
                f"{claim.get('claim_id')}"
            )
        try:
            response = self._connection.recv()
        except EOFError as exc:
            self._process.join(timeout=0.5)
            exitcode = self._process.exitcode
            self._stop_worker()
            raise RuntimeError(
                f"candidate {self.name} worker exited while handling claim "
                f"{claim.get('claim_id')} (exitcode={exitcode})"
            ) from exc
        if "output" not in response:
            self._raise_worker_error(response)
        output = response["output"]
        return output, (time.perf_counter() - started) * 1000

    def shutdown(self) -> None:
        if self._connection is not None and self._process is not None and self._process.is_alive():
            try:
                self._connection.send(None)
                self._process.join(timeout=WORKER_SHUTDOWN_GRACE_SECONDS)
            except Exception:
                pass
        self._stop_worker()
        if self.close:
            self.close()


def callable_adapter(
    name: str, function: str | Callable[[dict], dict], deterministic: bool = False
) -> CandidateAdapter:
    return CandidateAdapter(
        name, _load_callable(function) if isinstance(function, str) else function, deterministic
    )


def deterministic_adapter(name: str, profile: str | None = None) -> CandidateAdapter:
    return CandidateAdapter(
        name,
        lambda claim: deterministic_baseline(claim),
        deterministic=True,
        worker_spec=WorkerSpec(
            "deterministic", profile=profile or os.environ.get("LAKEBASE_PROFILE") or "fe-bar"
        ),
    )


def model_adapter(name: str, uri: str, loader=mlflow.pyfunc.load_model) -> CandidateAdapter:
    if loader is not mlflow.pyfunc.load_model:
        model = load_candidate(uri, loader=loader)
        return CandidateAdapter(name, lambda claim: predict_claim(claim, model=model))
    return CandidateAdapter(name, lambda claim: {}, worker_spec=WorkerSpec("model", uri))


def endpoint_adapter(
    name: str,
    endpoint: str,
    client: WorkspaceClient | None = None,
    profile: str | None = None,
) -> CandidateAdapter:
    workspace = client

    def invoke(claim: dict) -> dict:
        nonlocal workspace
        if workspace is None:
            workspace = WorkspaceClient(profile=profile)
        return _invoke_endpoint(workspace, endpoint, claim, name)

    return CandidateAdapter(
        name,
        invoke,
        worker_spec=WorkerSpec("endpoint", endpoint, profile=profile, candidate_name=name),
    )


def resolve_candidate(spec: str, profile: str | None = None) -> CandidateAdapter:
    resolved = CANDIDATE_ALIASES.get(spec, spec)
    if resolved == "deterministic_baseline":
        return deterministic_adapter(spec, profile=profile)
    if resolved.startswith("models:/"):
        return model_adapter(spec, resolved)
    if resolved.startswith("endpoint:"):
        return endpoint_adapter(spec, resolved.removeprefix("endpoint:"), profile=profile)
    return callable_adapter(spec, resolved.removeprefix("callable:"))


def normalize_expectations(expectations: dict) -> dict:
    normalized = dict(expectations)
    disposition = normalized.get("disposition")
    if "disposition_class" not in normalized:
        normalized["disposition_class"] = disposition_class(disposition, normalized.get("verdict"))
    if "approval_subchoice" not in normalized:
        normalized["approval_subchoice"] = approval_subchoice(
            disposition, normalized.get("verdict")
        )
    return normalized


def _token_count(output: dict) -> int | str:
    usage = output.get("usage")
    if not isinstance(usage, dict):
        return NA
    value = usage.get("total_tokens") or usage.get("total_token_count")
    return int(value) if value is not None else NA


def score(output: dict, expectations: dict, claim: dict | None = None) -> dict:
    expected = normalize_expectations(expectations)
    result = {
        "verdict": output.get("verdict") == expected.get("verdict"),
        "disposition_class": output.get("disposition_class") == expected.get("disposition_class"),
        "approval_subchoice": (
            output.get("approval_subchoice") == expected.get("approval_subchoice")
            if expected.get("disposition_class") == "APPROVE"
            else NA
        ),
    }
    expected_amount = expected.get("approved_amount")
    if expected_amount is None or str(expected_amount).strip().lower() in {"", "none", "null"}:
        expected_amount = None
    try:
        result["amount"] = (
            NA
            if expected_amount is None
            else Decimal(str(output.get("approved_amount"))).quantize(Decimal("0.01"))
            == Decimal(str(expected_amount)).quantize(Decimal("0.01"))
        )
    except (InvalidOperation, TypeError):
        result["amount"] = False
    result["duplicate"] = (output.get("disposition") == "DUPLICATE") == (
        expected.get("disposition") == "DUPLICATE"
    )
    result["pend_routing"] = (output.get("verdict") == "PEND") == (
        expected.get("verdict") == "PEND"
    )
    oracle = expected.get("oracle_clause_ids")
    citations = output.get("cited_clause_ids")
    result["citation"] = (
        NA if not oracle or citations is None else bool(citations) and set(citations) <= set(oracle)
    )
    group = expected.get("group")
    conflict = output.get("narrative_conflict")
    deciding_clause = expected.get("deciding_clause_id") or expected.get("policy_clause_id")
    narrative = (claim or {}).get("defect_narrative")
    if group == "narrative":
        result["narrative_escalation_rate"] = bool(
            output.get("verdict") == "PEND"
            and isinstance(conflict, dict)
            and conflict.get("clause") == deciding_clause
            and isinstance(conflict.get("narrative_quote"), str)
            and conflict["narrative_quote"]
            and isinstance(narrative, str)
            and conflict["narrative_quote"] in narrative
        )
    else:
        result["narrative_escalation_rate"] = NA
    result["false_escalation_rate"] = output.get("verdict") == "PEND" if group == "normal" else NA
    result["deciding_clause_cited"] = (
        NA
        if citations is None or not deciding_clause or deciding_clause == "structured_authorities"
        else deciding_clause in citations
    )
    result["judge"] = output.get("judge_score") if output.get("judge_score") is not None else NA
    violations = output.get("invariant_violations")
    result["invariant_corrected"] = NA if violations is None else bool(violations)
    return result


def _observations(records: list[dict], candidate: CandidateAdapter) -> list[dict]:
    observations = []
    for record in records:
        claim_id = record["inputs"]["claim"]["claim_id"]
        try:
            output, latency_ms = candidate.predict(record["inputs"]["claim"])
            observations.append(
                {
                    **score(output, record["expectations"], record["inputs"]["claim"]),
                    "claim_id": claim_id,
                    "group": record["expectations"].get("group", "unspecified"),
                    "stratum": record["expectations"].get(
                        "stratum", record["expectations"].get("scenario_type", "unspecified")
                    ),
                    "expectations": normalize_expectations(record["expectations"]),
                    "tokens": _token_count(output),
                    "latency_ms": latency_ms,
                    "error": None,
                    "prediction": output,
                }
            )
        except PersistenceViolation:
            raise
        except Exception as exc:
            expected = normalize_expectations(record["expectations"])
            observations.append(
                {
                    "verdict": False,
                    "disposition_class": False,
                    "approval_subchoice": (
                        False if expected.get("disposition_class") == "APPROVE" else NA
                    ),
                    "amount": False if expected.get("approved_amount") is not None else NA,
                    "duplicate": False,
                    "pend_routing": False,
                    "citation": (
                        NA
                        if candidate.deterministic or not expected.get("oracle_clause_ids")
                        else False
                    ),
                    "narrative_escalation_rate": (
                        False if expected.get("group") == "narrative" else NA
                    ),
                    "false_escalation_rate": (False if expected.get("group") == "normal" else NA),
                    "deciding_clause_cited": (
                        NA
                        if candidate.deterministic
                        or not (
                            expected.get("deciding_clause_id") or expected.get("policy_clause_id")
                        )
                        or (expected.get("deciding_clause_id") or expected.get("policy_clause_id"))
                        == "structured_authorities"
                        else False
                    ),
                    "judge": NA if candidate.deterministic else False,
                    "invariant_corrected": NA,
                    "claim_id": claim_id,
                    "group": record["expectations"].get("group", "unspecified"),
                    "stratum": record["expectations"].get(
                        "stratum", record["expectations"].get("scenario_type", "unspecified")
                    ),
                    "expectations": expected,
                    "tokens": NA,
                    "latency_ms": NA,
                    "error": f"{type(exc).__name__}: {exc}",
                    "prediction": None,
                }
            )
        if len(observations) == EARLY_FAILURE_LIMIT and all(
            row["error"] is not None for row in observations
        ):
            if mlflow.active_run() is not None:
                mlflow.log_dict(
                    {"candidate": candidate.name, "per_claim": observations},
                    "ablation-errors.json",
                )
            raise CandidateBatchFailure(candidate.name, observations)
    return observations


def _recursive_claim_id(value: Any) -> str | None:
    if isinstance(value, dict):
        if "claim_id" in value:
            return str(value["claim_id"])
        return next(
            (found for item in value.values() if (found := _recursive_claim_id(item))), None
        )
    if isinstance(value, list):
        return next((found for item in value if (found := _recursive_claim_id(item))), None)
    if isinstance(value, str):
        try:
            return _recursive_claim_id(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _trace_token_usage(run_id: str) -> dict[str, int]:
    usage = {}
    for _, row in mlflow.search_traces(run_id=run_id).iterrows():
        claim_id = _recursive_claim_id(row.get("request"))
        if not claim_id:
            continue
        metadata = row.get("trace_metadata")
        if not isinstance(metadata, dict):
            continue
        value = metadata.get("mlflow.trace.tokenUsage")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict) and value.get("total_tokens") is not None:
            usage[claim_id] = int(value["total_tokens"])
    return usage


def _mean(values: list[Any]) -> float | str:
    measured = [value for value in values if value != NA]
    if not measured:
        return NA
    return sum(float(value) for value in measured) / len(measured)


def _summary(observations: list[dict]) -> dict:
    return {
        **{
            dimension: _mean([row[dimension] for row in observations])
            for dimension in QUALITY_DIMENSIONS
        },
        **{
            f"{dimension}_n": sum(row[dimension] != NA for row in observations)
            for dimension in QUALITY_DIMENSIONS
        },
        "tokens_per_claim": _mean([row["tokens"] for row in observations]),
        "latency_ms_per_claim": _mean([row["latency_ms"] for row in observations]),
        "invariant_correction_rate": _mean([row["invariant_corrected"] for row in observations]),
        "failure_count": sum(row["error"] is not None for row in observations),
    }


def _paired(baseline: list[dict], challenger: list[dict], summaries: tuple[dict, dict]) -> dict:
    result = {}
    for dimension in SUMMARY_DIMENSIONS:
        base_mean, candidate_mean = summaries[0][dimension], summaries[1][dimension]
        item = {"delta": NA if NA in (base_mean, candidate_mean) else candidate_mean - base_mean}
        if dimension in QUALITY_DIMENSIONS:
            pairs = [
                (base[dimension], other[dimension])
                for base, other in zip(baseline, challenger)
                if base[dimension] != NA and other[dimension] != NA
            ]
            boolean_pairs = [(bool(base), bool(other)) for base, other in pairs]
            item.update(
                comparable_count=len(pairs),
                both_correct=sum(base and other for base, other in boolean_pairs),
                baseline_only_correct=sum(base and not other for base, other in boolean_pairs),
                challenger_only_correct=sum(not base and other for base, other in boolean_pairs),
                both_wrong=sum(not base and not other for base, other in boolean_pairs),
            )
        result[dimension] = item
    return result


def _report(candidates: list[CandidateAdapter], aggregates: dict[str, list[dict]]) -> dict:
    rows = []
    for index, first in enumerate(aggregates[candidates[0].name]):
        row = {
            "claim_id": first["claim_id"],
            "group": first["group"],
            "stratum": first["stratum"],
            "expectations": first["expectations"],
            "candidates": {},
        }
        for candidate in candidates:
            observation = aggregates[candidate.name][index]
            if observation["claim_id"] != first["claim_id"]:
                raise ValueError("candidate observations are not claim-aligned")
            row["candidates"][candidate.name] = {
                key: value
                for key, value in observation.items()
                if key not in {"claim_id", "group", "stratum", "expectations"}
            }
        rows.append(row)
    summary = {name: _summary(observations) for name, observations in aggregates.items()}
    baseline = candidates[0].name
    paired = {
        candidate.name: _paired(
            aggregates[baseline],
            aggregates[candidate.name],
            (summary[baseline], summary[candidate.name]),
        )
        for candidate in candidates[1:]
    }
    groups = sorted({row["group"] for row in aggregates[baseline]})
    strata = sorted({row["stratum"] for row in aggregates[baseline]})

    def filtered_summary(field: str, value: str) -> dict:
        return {
            candidate.name: _summary(
                [row for row in aggregates[candidate.name] if row[field] == value]
            )
            for candidate in candidates
        }

    summary_by_group = {group: filtered_summary("group", group) for group in groups}
    summary_by_stratum = {stratum: filtered_summary("stratum", stratum) for stratum in strata}
    paired_by_group = {}
    for group in groups:
        base_rows = [row for row in aggregates[baseline] if row["group"] == group]
        paired_by_group[group] = {}
        for candidate in candidates[1:]:
            other_rows = [row for row in aggregates[candidate.name] if row["group"] == group]
            paired_by_group[group][candidate.name] = _paired(
                base_rows,
                other_rows,
                (summary_by_group[group][baseline], summary_by_group[group][candidate.name]),
            )
    return {
        "per_claim": rows,
        "summary": summary,
        "summary_by_group": summary_by_group,
        "summary_by_stratum": summary_by_stratum,
        "paired_vs_baseline": paired,
        "paired_by_group": paired_by_group,
    }


def compare(records: list[dict], candidates: list[CandidateAdapter]) -> dict:
    if len(candidates) < 2:
        raise ValueError("ablation requires at least two candidates")
    try:
        return _report(
            candidates,
            {candidate.name: _observations(records, candidate) for candidate in candidates},
        )
    finally:
        for candidate in candidates:
            candidate.shutdown()


def _numeric_metrics(prefix: str, values: dict) -> dict:
    return {
        f"{prefix}{name}": float(value)
        for name, value in values.items()
        if value != NA and isinstance(value, (bool, int, float))
    }


def _metric_component(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_." else "_" for character in value
    )


def run(
    records: list[dict], candidates: list[CandidateAdapter], ablation_id: str | None = None
) -> dict:
    if len(candidates) < 2:
        raise ValueError("ablation requires at least two candidates")
    ablation_id = ablation_id or str(uuid.uuid4())
    candidate_runs, aggregates = [], {}
    try:
        for candidate in candidates:
            with mlflow.start_run(run_name=f"ablation-{ablation_id}-{candidate.name}") as active:
                mlflow.set_tags({"ablation_id": ablation_id, "candidate": candidate.name})
                candidate_runs.append(active.info.run_id)
                if candidate.worker_spec is not None:
                    candidate.worker_spec = replace(
                        candidate.worker_spec, run_id=active.info.run_id
                    )
                try:
                    observations = _observations(records, candidate)
                except Exception:
                    mlflow.set_tag("aborted", "true")
                    raise
                candidate.shutdown()
                try:
                    trace_tokens = _trace_token_usage(active.info.run_id)
                except Exception:
                    trace_tokens = {}
                for observation in observations:
                    if observation["tokens"] == NA and observation["claim_id"] in trace_tokens:
                        observation["tokens"] = trace_tokens[observation["claim_id"]]
                aggregates[candidate.name] = observations
                mlflow.log_metrics(_numeric_metrics("", _summary(observations)))
        report = _report(candidates, aggregates)
        with mlflow.start_run(run_name=f"ablation-{ablation_id}-comparison") as active:
            mlflow.set_tags({"ablation_id": ablation_id, "run_type": "paired_comparison"})
            for candidate, dimensions in report["paired_vs_baseline"].items():
                for dimension, metrics in dimensions.items():
                    prefix = f"{_metric_component(candidate)}.{dimension}."
                    mlflow.log_metrics(_numeric_metrics(prefix, metrics))
            with tempfile.TemporaryDirectory() as directory:
                artifact = Path(directory) / "ablation-comparison.json"
                artifact.write_text(json.dumps(report, indent=2, default=str) + "\n")
                mlflow.log_artifact(str(artifact))
            comparison_run = active.info.run_id
        return {
            **report,
            "ablation_id": ablation_id,
            "candidate_runs": candidate_runs,
            "comparison_run": comparison_run,
        }
    finally:
        for candidate in candidates:
            candidate.shutdown()


def _stratum(record: dict) -> tuple:
    expected = normalize_expectations(record.get("expectations") or {})
    claim = (record.get("inputs") or {}).get("claim") or {}
    return (expected.get("verdict"), expected.get("disposition_class"), claim.get("claim_type"))


def stratified_sample(records: list[dict], n: int) -> list[dict]:
    if len(records) < n:
        raise ValueError(f"dataset has only {len(records)} rows; requested {n}")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        groups[_stratum(record)].append(record)
    ordered = [groups[key] for key in sorted(groups, key=lambda value: tuple(map(str, value)))]
    selected = []
    while len(selected) < n:
        for group in ordered:
            if group and len(selected) < n:
                selected.append(group.pop(0))
    return selected


def build_history_records(profile: str, warehouse_id: str, n: int) -> list[dict]:
    selected = stable_holdout(
        _execute_sql(profile, warehouse_id, source_sql()), size=n, minimum_rare=min(10, n // 10)
    )
    oracles = ResolverOracle(profile).resolve_rows(selected)
    return stratified_sample(
        [make_record(row, oracle) for row, oracle in zip(selected, oracles)], n
    )


def load_records(dataset: str, n: int, profile: str, warehouse_id: str) -> list[dict]:
    if dataset == "history":
        return build_history_records(profile, warehouse_id, n)
    names = {
        "heldout": "fe-bar-ir.eval.heldout_claims",
        "heldout_mixed": "fe-bar-ir.eval.heldout_claims_mixed",
    }
    name = names.get(dataset, dataset)
    records = mlflow.genai.datasets.get_dataset(name=name).to_df().to_dict("records")
    if dataset in names and n != 100:
        raise ValueError(f"{dataset} comparison must use its complete 100-row stratification")
    return stratified_sample(records, n)


def estimate_tokens(n: int, candidates: list[str], tokens_per_agent_claim: int = 3500) -> dict:
    llm_candidates = sum(
        CANDIDATE_ALIASES.get(item, item) != "deterministic_baseline" for item in candidates
    )
    return {
        "claims": n,
        "llm_candidates": llm_candidates,
        "estimated_tokens": n * llm_candidates * tokens_per_agent_claim,
        "assumption_tokens_per_agent_claim": tokens_per_agent_claim,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--profile", default="fe-bar")
    parser.add_argument("--warehouse-id")
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--estimate-only", action="store_true")
    args = parser.parse_args()
    specs = [item.strip() for item in args.candidates.split(",") if item.strip()]
    if args.estimate_only:
        print(json.dumps(estimate_tokens(args.n, specs), indent=2))
        return
    if args.dataset == "history" and not args.warehouse_id:
        parser.error("--dataset history requires --warehouse-id")
    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(args.experiment)
    records = load_records(args.dataset, args.n, args.profile, args.warehouse_id)
    candidates = []
    try:
        for spec in specs:
            candidates.append(resolve_candidate(spec, profile=args.profile))
        print(json.dumps(run(records, candidates), indent=2, default=str))
    finally:
        for candidate in candidates:
            candidate.shutdown()


if __name__ == "__main__":
    main()
