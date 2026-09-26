"""Cost-gated live MLflow GenAI evaluation runner and evidence writer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from build_dataset import build
from predict import predict_fn
from resolver_oracle import ResolverOracle
from scorers import EXACT_SCORERS, PRIMARY_JUDGE_SCORERS, RELEASE_THRESHOLDS

EVIDENCE = Path(__file__).resolve().parents[1] / "evidence"
MODEL_NAME = "fe-bar-ir.default.claims_adjudication_agent"
MODEL_VERSION = "2"
COST_LIMIT_USD = 25.0


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _model_uri_version(model_uri: str, model_name: str, client_factory) -> str:
    """Resolve the registered-model VERSION a ``models:/`` URI points at.

    ``models:/<name>/<version>`` -> that version (no registry call);
    ``models:/<name>@<alias>`` -> the version the alias resolves to (one registry call).
    Rejects any other shape or a name that is not ``model_name``.
    """
    prefix = "models:/"
    if not model_uri.startswith(prefix):
        raise ValueError(
            f"--model-uri must be a UC model URI (models:/<name>/<version> or "
            f"models:/<name>@<alias>): {model_uri}"
        )
    rest = model_uri[len(prefix) :]
    if "@" in rest:
        name, alias = rest.split("@", 1)
        if name != model_name:
            raise ValueError(f"--model-uri names {name!r}, not the agent model {model_name!r}")
        return str(client_factory().get_model_version_by_alias(model_name, alias).version)
    if "/" in rest:
        name, version = rest.rsplit("/", 1)
        if name != model_name:
            raise ValueError(f"--model-uri names {name!r}, not the agent model {model_name!r}")
        return str(version)
    raise ValueError(f"--model-uri must pin a version or alias: {model_uri}")


def pinned_candidate_model_uri(
    model_uri: str | None, candidate_version: str, model_name: str, client_factory=None
) -> str:
    """Return a model URI provably pinned to ``candidate_version``, or raise.

    The eval must score the candidate version whose metrics promote.py later reads, so
    the recorded model URI cannot silently point elsewhere (e.g. ``@prod``). When
    ``--model-uri`` is omitted the URI is derived as ``models:/<name>/<candidate>``;
    when it is passed explicitly it must resolve to exactly the candidate version, else
    this refuses with a clear error.
    """
    candidate_version = str(candidate_version)
    if not model_uri:
        return f"models:/{model_name}/{candidate_version}"
    if client_factory is None:
        client_factory = lambda: MlflowClient(registry_uri="databricks-uc")  # noqa: E731
    resolved = _model_uri_version(model_uri, model_name, client_factory)
    if resolved != candidate_version:
        raise ValueError(
            f"--model-uri {model_uri} resolves to version {resolved}, but --candidate-version is "
            f"{candidate_version}; the eval must run the candidate version. Pass "
            f"--model-uri models:/{model_name}/{candidate_version} or omit --model-uri."
        )
    return model_uri


def _records(dataset) -> list[dict]:
    if hasattr(dataset, "records"):
        return list(dataset.records)
    if hasattr(dataset, "to_df"):
        return dataset.to_df().to_dict("records")
    raise TypeError("MLflow managed dataset does not expose records")


def _usage(run_id: str) -> dict:
    tokens, cost = 0, 0.0
    for _, row in mlflow.search_traces(run_id=run_id).iterrows():
        for key in ("token_usage", "usage", "trace_metadata"):
            value = row.get(key)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            if isinstance(value, dict):
                tokens += int(value.get("total_tokens") or value.get("total_token_count") or 0)
                cost += float(value.get("total_cost") or value.get("cost_usd") or 0)
    return {"tokens": tokens, "cost_usd": cost}


def _metric_summary(metrics: dict) -> dict:
    summary = {}
    for name, threshold in RELEASE_THRESHOLDS.items():
        value = next((metrics[key] for key in (f"{name}/mean", name) if key in metrics), None)
        summary[name] = {
            "value": value,
            "threshold": threshold,
            "passed": value is not None and float(value) >= threshold,
        }
    return summary


def _redacted_failures(run_id: str, maximum: int = 10) -> list[dict]:
    samples = []
    for _, row in mlflow.search_traces(run_id=run_id).iterrows():
        failed = [
            item.get("assessment_name")
            for item in (row.get("assessments") or [])
            if item.get("feedback", {}).get("value") in (False, 0, "no")
        ]
        if failed:
            samples.append(
                {
                    "request_hash": hashlib.sha256(
                        str(row.get("request", "")).encode()
                    ).hexdigest()[:16],
                    "failed_scorers": failed,
                }
            )
        if len(samples) >= maximum:
            break
    return samples


def _write(name: str, payload) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")


def _read_evidence(name: str, default):
    path = EVIDENCE / name
    return json.loads(path.read_text()) if path.exists() else default


def run(args) -> dict:
    if args.profile != "fe-bar":
        raise ValueError("live evaluation is approved only for the explicit fe-bar profile")
    # The candidate registered-model VERSION under evaluation. Passed via
    # --candidate-version (default: AGENT_MODEL_VERSION env, else MODEL_VERSION). It is
    # stamped as the ``candidate_version`` run tag on every eval run so the run — and the
    # traces it produces — reference the exact UC version of
    # fe-bar-ir.default.claims_adjudication_agent being scored, and so promote.py can pull
    # this version's release-gate metrics later. It is also exported as AGENT_MODEL_VERSION
    # so the agent stamps the same version on its decision records and trace attributes.
    candidate_version = str(args.candidate_version or MODEL_VERSION)
    os.environ.update(
        DATABRICKS_CONFIG_PROFILE=args.profile,
        LAKEBASE_PROFILE=args.profile,
        AGENT_MODEL_VERSION=candidate_version,
    )
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    # Fail fast (before the expensive build/eval) if the requested model URI does not
    # pin the candidate version. The metrics this run logs are promoted later against
    # candidate_version, so they must provably come from evaluating that exact version.
    model_uri = pinned_candidate_model_uri(args.model_uri, candidate_version, MODEL_NAME)
    experiment = mlflow.set_experiment(args.experiment)
    dataset, metadata, records = build(
        args.profile, args.warehouse_id, experiment.experiment_id, ResolverOracle(args.profile)
    )
    tags = {
        "agent_model": MODEL_NAME,
        "agent_model_version": candidate_version,
        "candidate_version": candidate_version,
        "git_sha": _git_sha(),
        "dataset_split": metadata["source_fingerprint"],
        "persist": "false",
    }
    previous_manifest = _read_evidence("run-manifest.json", {})
    previous_metrics = _read_evidence("aggregate-metrics.json", {})
    previous_cost = _read_evidence("pilot-cost.json", {})
    pilot = None
    if args.scorer_tier != "exact":
        with mlflow.start_run(run_name="claims-eval-pilot", tags={**tags, "tier": "pilot"}):
            pilot = mlflow.genai.evaluate(
                data=records[:10],
                predict_fn=predict_fn,
                scorers=EXACT_SCORERS + PRIMARY_JUDGE_SCORERS,
            )
        usage = _usage(pilot.run_id)
        telemetry_available = bool(usage["cost_usd"] or usage["tokens"])
        # Missing trace usage must not be interpreted as a zero-cost run.
        pilot_cost = usage["cost_usd"] or (
            usage["tokens"] / 1000 * 0.002 if usage["tokens"] else 1.0
        )
        # Pilot: 10 agent + 20 judge calls. Full: 105 agent + 60 judge calls.
        projected = pilot_cost * (165 / 30)
        run_judges = projected <= COST_LIMIT_USD
        cost = {
            "pilot_records": 10,
            "pilot_tokens": usage["tokens"],
            "pilot_cost_usd": pilot_cost,
            "trace_token_telemetry_available": telemetry_available,
            "cost_method": (
                "trace_reported" if telemetry_available else "conservative_$1_pilot_ceiling"
            ),
            "pilot_llm_invocations": 30,
            "projected_llm_invocations": 165,
            "projected_total_usd": projected,
            "threshold_usd": COST_LIMIT_USD,
            "judge_tier_ran": run_judges,
            "judge_tier_deferred": not run_judges,
        }
    else:
        run_judges = False
        cost = previous_cost
    with mlflow.start_run(run_name="claims-eval-exact", tags={**tags, "tier": "exact"}):
        exact = mlflow.genai.evaluate(data=records, predict_fn=predict_fn, scorers=EXACT_SCORERS)
    judge = None
    if run_judges:
        judge_records = [row for row in records if row["expectations"].get("judge_subset")][:30]
        with mlflow.start_run(run_name="claims-eval-judges", tags={**tags, "tier": "judges"}):
            judge = mlflow.genai.evaluate(
                data=judge_records, predict_fn=predict_fn, scorers=PRIMARY_JUDGE_SCORERS
            )
    link = f"{args.workspace_host}/ml/experiments/{experiment.experiment_id}/runs/{exact.run_id}"
    manifest = {
        "built_at_utc": datetime.now(UTC).isoformat(),
        "experiment": args.experiment,
        "model_name": MODEL_NAME,
        "model_version": candidate_version,
        "candidate_version": candidate_version,
        "model_uri": model_uri,
        "git_sha": tags["git_sha"],
        "dataset": metadata,
        "pilot_run_id": pilot.run_id if pilot else previous_manifest.get("pilot_run_id"),
        "exact_run_id": exact.run_id,
        "judge_run_id": (judge.run_id if judge else previous_manifest.get("judge_run_id")),
        "mlflow_run_link": link,
        "persist": False,
        # The MLflow run/metrics (link above) are the authoritative system of record.
        # Everything written under eval/evidence/ is a regenerated convenience snapshot.
        "record_of_truth": "mlflow",
        "convenience_artifact": True,
    }
    metrics = {
        "release_gate": _metric_summary(exact.metrics),
        "raw_exact_metrics": exact.metrics,
        "diagnostic_judges": {
            "authority_guidelines": {
                "value": (
                    judge.metrics.get("authority_guidelines/mean")
                    if judge
                    else previous_metrics.get("diagnostic_judges", {})
                    .get("authority_guidelines", {})
                    .get(
                        "value",
                        previous_metrics.get("raw_judge_metrics", {}).get(
                            "authority_guidelines/mean"
                        ),
                    )
                ),
                "status": "measured",
            },
            "retrieval_groundedness": {
                "value": (
                    judge.metrics.get("retrieval_groundedness/mean")
                    if judge
                    else previous_metrics.get("diagnostic_judges", {})
                    .get("retrieval_groundedness", {})
                    .get(
                        "value",
                        previous_metrics.get("raw_judge_metrics", {}).get(
                            "retrieval_groundedness/mean"
                        ),
                    )
                ),
                "status": "not_yet_measurable",
                "limitation": (
                    "RETRIEVER spans expose cited IDs but not retrieved clause text as outputs; "
                    "0.0 is not a grounding-regression signal."
                ),
                "follow_up": "Enrich agent RETRIEVER span outputs with clause text.",
            },
        },
    }
    _write("run-manifest.json", manifest)
    _write("aggregate-metrics.json", metrics)
    _write("pilot-cost.json", cost)
    _write("failure-samples.json", _redacted_failures(exact.run_id))
    _write(
        "commands.json",
        {
            "local_gates": [
                "uv run ruff check src tests",
                "uv run ruff format --check src tests",
                "uv run pytest -q",
            ],
            "live": (
                "DATABRICKS_CONFIG_PROFILE=fe-bar LAKEBASE_PROFILE=fe-bar "
                "MLFLOW_GENAI_EVAL_MAX_WORKERS=5 uv run python src/evaluate.py "
                f"--profile fe-bar --scorer-tier {args.scorer_tier}"
            ),
        },
    )
    MlflowClient().set_tag(
        exact.run_id,
        "release_gate_passed",
        str(all(value["passed"] for value in metrics["release_gate"].values())).lower(),
    )
    return {"manifest": manifest, "cost": cost, "metrics": metrics}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", ""))
    parser.add_argument("--warehouse-id", default="38e458a09de4a055")
    parser.add_argument("--experiment", default="/Shared/claims-adjudication-offline-evaluation")
    parser.add_argument(
        "--model-uri",
        default=None,
        help=(
            "UC model URI to evaluate; must pin --candidate-version. Omit to derive "
            "models:/<name>/<candidate-version>. An explicit URI resolving to a different "
            "version is rejected."
        ),
    )
    parser.add_argument("--judge-endpoint", default="databricks-meta-llama-3-3-70b-instruct")
    parser.add_argument("--scorer-tier", choices=("auto", "exact", "judges"), default="auto")
    parser.add_argument(
        "--candidate-version",
        default=os.environ.get("AGENT_MODEL_VERSION") or MODEL_VERSION,
        help="UC registered-model version under evaluation; stamped as the candidate_version run tag.",
    )
    parser.add_argument("--dataset-version", default="latest")
    parser.add_argument(
        "--workspace-host", default="https://fe-sandbox-fe-bar-ir.cloud.databricks.com"
    )
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
