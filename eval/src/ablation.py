"""Paired, same-data/same-scorer ablation evaluation harness."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import mlflow
from databricks.sdk import WorkspaceClient

from build_dataset import _execute_sql, make_record, source_sql, stable_holdout
from heldout import approval_subchoice, disposition_class
from predict import PersistenceViolation, load_candidate, predict_claim
from resolver_oracle import AGENT_SRC, ResolverOracle

MODEL_URI = "models:/fe-bar-ir.default.claims_adjudication_agent@prod"
DEFAULT_EXPERIMENT = "/Shared/claims-adjudication-ablation"
CANDIDATE_ALIASES = {"agent@prod": MODEL_URI, "deterministic_baseline": "deterministic_baseline"}
NA = "N/A"
QUALITY_DIMENSIONS = ("verdict", "disposition_class", "approval_subchoice", "citation", "judge")
SUMMARY_DIMENSIONS = QUALITY_DIMENSIONS + (
    "tokens_per_claim",
    "latency_ms_per_claim",
    "invariant_correction_rate",
)


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
        "approved_amount": recommendation.get("approved_amount"),
        "cited_clause_ids": None if deterministic else data.get("cited_clause_ids"),
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


def _deterministic_context(connection, claim: dict) -> dict:
    if str(AGENT_SRC) not in sys.path:
        sys.path.insert(0, str(AGENT_SRC))
    try:
        from authorities_runtime import AuthorityRuntime
        from decision_record import deterministic_outcome
        from duplicate import check_duplicate_claim
    except (ImportError, AttributeError) as exc:
        raise ImportError("deterministic baseline requires the agent authority runtime") from exc
    frozen = AuthorityRuntime(connection).freeze(claim["coil_id"])
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
    duplicate = check_duplicate_claim(connection, claim)
    deterministic = deterministic_outcome(
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
    from db import connect

    profile = os.environ.get("LAKEBASE_PROFILE") or "fe-bar"
    with connect(profile=profile, autocommit=True) as connection:
        return _recommend_from_context(_deterministic_context(connection, claim))


@dataclass
class CandidateAdapter:
    name: str
    invoke: Callable[[dict], dict]
    deterministic: bool = False
    close: Callable[[], None] | None = None

    def predict(self, claim: dict) -> tuple[dict, float]:
        started = time.perf_counter()
        output = standardize_output(self.invoke(claim), deterministic=self.deterministic)
        return output, (time.perf_counter() - started) * 1000


def callable_adapter(
    name: str, function: str | Callable[[dict], dict], deterministic: bool = False
) -> CandidateAdapter:
    return CandidateAdapter(
        name, _load_callable(function) if isinstance(function, str) else function, deterministic
    )


def deterministic_adapter(name: str, profile: str | None = None) -> CandidateAdapter:
    """Open one Lakebase connection and reuse it for every claim in this candidate run."""
    from db import connect

    manager = connect(
        profile=profile or os.environ.get("LAKEBASE_PROFILE") or "fe-bar", autocommit=True
    )
    connection = manager.__enter__()

    def close() -> None:
        manager.__exit__(None, None, None)

    return CandidateAdapter(
        name,
        lambda claim: _recommend_from_context(_deterministic_context(connection, claim)),
        deterministic=True,
        close=close,
    )


def model_adapter(name: str, uri: str, loader=mlflow.pyfunc.load_model) -> CandidateAdapter:
    model = load_candidate(uri, loader=loader)
    return CandidateAdapter(name, lambda claim: predict_claim(claim, model=model))


def endpoint_adapter(
    name: str, endpoint: str, client: WorkspaceClient | None = None
) -> CandidateAdapter:
    workspace = client or WorkspaceClient()

    def invoke(claim: dict) -> dict:
        response = workspace.api_client.do(
            "POST",
            f"/api/2.0/serving-endpoints/{quote(endpoint, safe='')}/invocations",
            body={
                "input": [{"role": "user", "content": json.dumps(claim, sort_keys=True)}],
                "custom_inputs": {"claim": claim, "persist": False},
            },
        )
        if _custom(response).get("write_result", {}).get("persisted") is not False:
            raise PersistenceViolation(f"{name}: endpoint persistence invariant failed")
        return response

    return CandidateAdapter(name, invoke)


def resolve_candidate(spec: str) -> CandidateAdapter:
    resolved = CANDIDATE_ALIASES.get(spec, spec)
    if resolved == "deterministic_baseline":
        return deterministic_adapter(spec)
    if resolved.startswith("models:/"):
        return model_adapter(spec, resolved)
    if resolved.startswith("endpoint:"):
        return endpoint_adapter(spec, resolved.removeprefix("endpoint:"))
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


def score(output: dict, expectations: dict) -> dict:
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
    oracle = expected.get("oracle_clause_ids")
    citations = output.get("cited_clause_ids")
    result["citation"] = (
        NA if not oracle or citations is None else bool(citations) and set(citations) <= set(oracle)
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
                    **score(output, record["expectations"]),
                    "claim_id": claim_id,
                    "tokens": _token_count(output),
                    "latency_ms": latency_ms,
                    "error": None,
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
                    "citation": (
                        NA
                        if candidate.deterministic or not expected.get("oracle_clause_ids")
                        else False
                    ),
                    "judge": NA if candidate.deterministic else False,
                    "invariant_corrected": NA,
                    "claim_id": claim_id,
                    "tokens": NA,
                    "latency_ms": NA,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
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
        row = {"claim_id": first["claim_id"], "candidates": {}}
        for candidate in candidates:
            observation = aggregates[candidate.name][index]
            if observation["claim_id"] != first["claim_id"]:
                raise ValueError("candidate observations are not claim-aligned")
            row["candidates"][candidate.name] = {
                key: value for key, value in observation.items() if key != "claim_id"
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
    return {"per_claim": rows, "summary": summary, "paired_vs_baseline": paired}


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
            if candidate.close:
                candidate.close()


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
    for candidate in candidates:
        try:
            with mlflow.start_run(run_name=f"ablation-{ablation_id}-{candidate.name}") as active:
                mlflow.set_tags({"ablation_id": ablation_id, "candidate": candidate.name})
                observations = _observations(records, candidate)
                try:
                    trace_tokens = _trace_token_usage(active.info.run_id)
                except Exception:
                    trace_tokens = {}
                for observation in observations:
                    if observation["tokens"] == NA and observation["claim_id"] in trace_tokens:
                        observation["tokens"] = trace_tokens[observation["claim_id"]]
                aggregates[candidate.name] = observations
                mlflow.log_metrics(_numeric_metrics("", _summary(observations)))
                candidate_runs.append(active.info.run_id)
        finally:
            if candidate.close:
                candidate.close()
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
    name = "fe-bar-ir.eval.heldout_claims" if dataset == "heldout" else dataset
    records = mlflow.genai.datasets.get_dataset(name=name).to_df().to_dict("records")
    if dataset == "heldout" and n != 100:
        raise ValueError("heldout comparison must use its complete 100-row stratification")
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
    print(
        json.dumps(run(records, [resolve_candidate(spec) for spec in specs]), indent=2, default=str)
    )


if __name__ == "__main__":
    main()
