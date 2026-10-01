"""Paired, same-data/same-scorer ablation evaluation harness."""

from __future__ import annotations

import argparse
import importlib
import json
import tempfile
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import mlflow
from databricks.sdk import WorkspaceClient

from heldout import approval_subchoice, disposition_class

MODEL_URI = "models:/fe-bar-ir.default.claims_adjudication_agent@prod"
HISTORY_DATASET = "fe-bar-ir.default.claims_adjudication_eval_history"
CANDIDATE_ALIASES = {"agent@prod": MODEL_URI, "deterministic_baseline": "deterministic_baseline"}
NA = "N/A"


def _custom(output: dict) -> dict:
    return output.get("custom_outputs", output)


def standardize_output(output: Any) -> dict:
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
    return {
        "verdict": "PEND" if verdict == "PEND_INVESTIGATE" else verdict,
        "disposition_class": disposition_class(disposition, verdict),
        "approval_subchoice": approval_subchoice(disposition, verdict),
        "approved_amount": recommendation.get("approved_amount"),
        "cited_clause_ids": data.get("cited_clause_ids"),
        "judge_scores": data.get("judge_scores"),
        "invariant_violations": data.get("invariant_violations") or [],
        "usage": data.get("usage") or output.get("usage") or {},
        "raw": output,
    }


def _load_callable(path: str) -> Callable[[dict], dict]:
    module_name, separator, attribute = path.rpartition(".")
    if not separator:
        raise ValueError(f"callable candidate must be a dotted path, got {path!r}")
    return getattr(importlib.import_module(module_name), attribute)


def deterministic_baseline(claim: dict) -> dict:
    """Late-bind the parallel ruleset without making this branch depend on it."""
    errors = []
    for module_name in ("authorities", "decision_record"):
        try:
            function = getattr(importlib.import_module(module_name), "deterministic_recommendation")
            return function(claim)
        except (ImportError, AttributeError) as exc:
            errors.append(f"{module_name}: {exc}")
    raise ImportError(
        "deterministic_baseline requires deterministic_recommendation from agent/src/authorities.py "
        "or agent/src/decision_record.py; land branch deterministic-ruleset first. "
        + "; ".join(errors)
    )


@dataclass
class CandidateAdapter:
    name: str
    invoke: Callable[[dict], dict]

    def predict(self, claim: dict) -> tuple[dict, float]:
        started = time.perf_counter()
        output = standardize_output(self.invoke(claim))
        return output, (time.perf_counter() - started) * 1000


def callable_adapter(name: str, function: str | Callable[[dict], dict]) -> CandidateAdapter:
    return CandidateAdapter(
        name, _load_callable(function) if isinstance(function, str) else function
    )


def model_adapter(name: str, uri: str, loader=mlflow.pyfunc.load_model) -> CandidateAdapter:
    model = loader(uri)

    def invoke(claim: dict) -> dict:
        request = {
            "input": [{"role": "user", "content": json.dumps(claim, sort_keys=True)}],
            "custom_inputs": {"claim": claim, "persist": False},
        }
        output = model.predict(request)
        dumped = output.model_dump() if hasattr(output, "model_dump") else output
        if isinstance(dumped, list) and len(dumped) == 1:
            dumped = dumped[0]
        if _custom(dumped).get("write_result", {}).get("persisted") is not False:
            raise RuntimeError(f"{name}: registered-model persistence invariant failed")
        return dumped

    return CandidateAdapter(name, invoke)


def endpoint_adapter(
    name: str, endpoint: str, client: WorkspaceClient | None = None
) -> CandidateAdapter:
    workspace = client or WorkspaceClient()

    def invoke(claim: dict) -> dict:
        response = workspace.serving_endpoints.query(
            name=endpoint,
            input=[{"role": "user", "content": json.dumps(claim, sort_keys=True)}],
            custom_inputs={"claim": claim, "persist": False},
        )
        return response.as_dict() if hasattr(response, "as_dict") else response

    return CandidateAdapter(name, invoke)


def resolve_candidate(spec: str) -> CandidateAdapter:
    resolved = CANDIDATE_ALIASES.get(spec, spec)
    if resolved == "deterministic_baseline":
        return callable_adapter(spec, deterministic_baseline)
    if resolved.startswith("models:/"):
        return model_adapter(spec, resolved)
    if resolved.startswith("endpoint:"):
        return endpoint_adapter(spec, resolved.removeprefix("endpoint:"))
    return callable_adapter(spec, resolved.removeprefix("callable:"))


def _tokens(output: dict) -> int:
    usage = output.get("usage") or {}
    return int(usage.get("total_tokens") or usage.get("total_token_count") or 0)


def score(output: dict, expectations: dict) -> dict:
    result = {
        dimension: output.get(dimension) == expectations.get(dimension)
        for dimension in ("verdict", "disposition_class", "approval_subchoice")
    }
    result["citation"] = (
        NA
        if output.get("cited_clause_ids") is None
        else set(output["cited_clause_ids"]) <= set(expectations.get("oracle_clause_ids") or [])
    )
    result["judge"] = NA if output.get("judge_scores") is None else output["judge_scores"]
    result["invariant_corrected"] = bool(output.get("invariant_violations"))
    return result


def _observations(records: list[dict], candidate: CandidateAdapter) -> list[dict]:
    observations = []
    for record in records:
        output, latency_ms = candidate.predict(record["inputs"]["claim"])
        observations.append(
            {
                **score(output, record["expectations"]),
                "claim_id": record["inputs"]["claim"]["claim_id"],
                "tokens": _tokens(output),
                "latency_ms": latency_ms,
            }
        )
    return observations


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
    dimensions = ("verdict", "disposition_class", "approval_subchoice")
    summary = {}
    for candidate, observations in aggregates.items():
        summary[candidate] = {
            **{
                dimension: sum(bool(x[dimension]) for x in observations) / len(observations)
                for dimension in dimensions
            },
            "tokens_per_claim": sum(x["tokens"] for x in observations) / len(observations),
            "latency_ms_per_claim": sum(x["latency_ms"] for x in observations) / len(observations),
            "invariant_correction_rate": sum(x["invariant_corrected"] for x in observations)
            / len(observations),
        }
    baseline, challenger = candidates[0].name, candidates[1].name
    paired = {}
    for dimension in dimensions:
        b = [x[dimension] for x in aggregates[baseline]]
        c = [x[dimension] for x in aggregates[challenger]]
        paired[dimension] = {
            "delta": summary[challenger][dimension] - summary[baseline][dimension],
            "both_correct": sum(x and y for x, y in zip(b, c)),
            "baseline_only_correct": sum(x and not y for x, y in zip(b, c)),
            "challenger_only_correct": sum(not x and y for x, y in zip(b, c)),
            "both_wrong": sum(not x and not y for x, y in zip(b, c)),
        }
    return {"per_claim": rows, "summary": summary, "paired": paired}


def compare(records: list[dict], candidates: list[CandidateAdapter]) -> dict:
    if len(candidates) < 2:
        raise ValueError("ablation requires at least two candidates")
    aggregates = {candidate.name: _observations(records, candidate) for candidate in candidates}
    return _report(candidates, aggregates)


def run(
    records: list[dict], candidates: list[CandidateAdapter], ablation_id: str | None = None
) -> dict:
    if len(candidates) < 2:
        raise ValueError("ablation requires at least two candidates")
    ablation_id = ablation_id or str(uuid.uuid4())
    candidate_runs = []
    aggregates = {}
    for candidate in candidates:
        with mlflow.start_run(run_name=f"ablation-{ablation_id}-{candidate.name}") as active:
            mlflow.set_tag("ablation_id", ablation_id)
            mlflow.set_tag("candidate", candidate.name)
            aggregates[candidate.name] = _observations(records, candidate)
            candidate_summary = _report(
                [candidate, candidate], {candidate.name: aggregates[candidate.name]}
            )["summary"][candidate.name]
            mlflow.log_metrics(candidate_summary)
            candidate_runs.append(active.info.run_id)
    report = _report(candidates, aggregates)
    with mlflow.start_run(run_name=f"ablation-{ablation_id}-comparison") as active:
        mlflow.set_tag("ablation_id", ablation_id)
        mlflow.set_tag("run_type", "paired_comparison")
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
    expectations = record.get("expectations") or {}
    claim = (record.get("inputs") or {}).get("claim") or {}
    return (
        expectations.get("scenario_type"),
        expectations.get("verdict"),
        expectations.get("disposition_class") or expectations.get("disposition"),
        claim.get("claim_type"),
    )


def stratified_sample(records: list[dict], n: int) -> list[dict]:
    if len(records) < n:
        raise ValueError(f"dataset has only {len(records)} rows; requested {n}")
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        groups[_stratum(record)].append(record)
    ordered = [groups[key] for key in sorted(groups, key=lambda value: tuple(map(str, value)))]
    selected = []
    while len(selected) < n:
        progressed = False
        for group in ordered:
            if group and len(selected) < n:
                selected.append(group.pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def load_records(dataset: str, n: int) -> list[dict]:
    name = {
        "heldout": "fe-bar-ir.eval.heldout_claims",
        "history": HISTORY_DATASET,
    }.get(dataset, dataset)
    managed = mlflow.genai.datasets.get_dataset(name=name)
    records = managed.to_df().to_dict("records")
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
    parser.add_argument("--estimate-only", action="store_true")
    args = parser.parse_args()
    specs = [item.strip() for item in args.candidates.split(",") if item.strip()]
    if args.estimate_only:
        print(json.dumps(estimate_tokens(args.n, specs), indent=2))
        return
    mlflow.set_tracking_uri("databricks")
    records = load_records(args.dataset, args.n)
    print(
        json.dumps(run(records, [resolve_candidate(spec) for spec in specs]), indent=2, default=str)
    )


if __name__ == "__main__":
    main()
