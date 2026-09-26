"""Gated @prod alias promotion for the claims-adjudication agent.

Promotion is a versioned, comparable, governed step — NOT a side effect of every
eval run. The eval run is the system of record: ``mlflow.genai.evaluate`` logs the
release-gate metrics to an MLflow run tagged with the candidate's registered-model
version (``candidate_version``). This module reads those logged metrics via
``mlflow.search_runs`` for both the candidate and the current ``@prod`` version,
applies the release gate, and moves the ``@prod`` alias to the candidate ONLY when
the candidate wins:

  1. every money-safety invariant passes its absolute release threshold, AND
  2. the verdict / disposition / amount metrics are not worse than current ``@prod``.

If the candidate does not win, the alias is left unchanged and the reason is
reported. Moving the alias is an explicit, gated, logged action: the CLI defaults
to a dry-run report and only mutates the registry when invoked with ``--promote``
against the ``fe-bar`` profile.

Grounded against MLflow 3.16.1:
  - ``mlflow.search_runs(experiment_ids=..., filter_string=..., order_by=..., max_results=...)``
    returns a pandas DataFrame with ``metrics.<name>/mean`` and ``tags.<key>`` columns.
  - ``MlflowClient.get_model_version_by_alias(name, alias) -> ModelVersion`` (``.version``).
  - ``MlflowClient.set_registered_model_alias(name, alias, version) -> None`` moves the alias.
"""

from __future__ import annotations

import argparse
import json
import math
import os

import mlflow
from mlflow.tracking import MlflowClient

from scorers import RELEASE_THRESHOLDS

MODEL_NAME = "fe-bar-ir.default.claims_adjudication_agent"
PROD_ALIAS = "prod"
# Float means of count ratios; a tiny tolerance absorbs representation noise.
EPS = 1e-9

# Money-safety invariants: the release-gate metrics whose threshold is an absolute
# 1.0. The candidate must satisfy every one of these on its own merits before it is
# eligible for promotion (a money-critical claim can never be paid incorrectly).
MONEY_SAFETY_METRICS = tuple(
    sorted(name for name, threshold in RELEASE_THRESHOLDS.items() if threshold >= 1.0)
)
# Quality metrics compared relatively: the candidate must be NOT WORSE than the
# current @prod on verdict / disposition / amount.
QUALITY_METRICS = ("verdict_exact_match", "disposition_exact_match", "amount_matches_gold")


def _first_row(runs):
    """Return the first run row from a pandas DataFrame or list, or None if empty."""
    if runs is None:
        return None
    if hasattr(runs, "empty"):  # pandas DataFrame
        return None if runs.empty else runs.iloc[0]
    if isinstance(runs, list):
        return runs[0] if runs else None
    return None


def _metric_from_row(row, name: str) -> float | None:
    """Pull a release-gate metric mean from a search_runs row (pandas or Run)."""
    for key in (f"metrics.{name}/mean", f"metrics.{name}", f"{name}/mean", name):
        value = row.get(key) if hasattr(row, "get") else getattr(row, key, None)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(number):
            continue
        return number
    return None


def release_gate_metrics(
    search_runs, experiment_id: str, version: str, tier: str = "exact"
) -> dict:
    """Read the release-gate metric means for a registered-model version's eval run.

    Finds the most recent ``tier`` run tagged with that version and returns
    ``{metric_name: mean_or_None}`` for every release-gate metric. Falls back to the
    legacy ``agent_model_version`` tag so eval runs predating the ``candidate_version``
    tag are still resolvable.
    """
    for tag_key in ("candidate_version", "agent_model_version"):
        runs = search_runs(
            experiment_ids=[experiment_id],
            filter_string=f"tags.{tag_key} = '{version}' and tags.tier = '{tier}'",
            order_by=["attributes.start_time DESC"],
            max_results=1,
        )
        row = _first_row(runs)
        if row is not None:
            return {name: _metric_from_row(row, name) for name in RELEASE_THRESHOLDS}
    raise LookupError(
        f"no {tier}-tier eval run found for model version {version} in experiment {experiment_id}; "
        "run the eval on that version first so its release-gate metrics are recorded in MLflow"
    )


def evaluate_gate(candidate_metrics: dict, prod_metrics: dict) -> dict:
    """Decide whether the candidate wins the release gate against current @prod.

    Pure comparison over already-fetched metric dicts. The candidate wins iff every
    money-safety invariant meets its absolute threshold AND every verdict/disposition/
    amount metric is not worse than @prod. Returns a fully itemised decision.
    """
    money_safety = {}
    for name in MONEY_SAFETY_METRICS:
        value = candidate_metrics.get(name)
        threshold = RELEASE_THRESHOLDS[name]
        money_safety[name] = {
            "value": value,
            "threshold": threshold,
            "passed": value is not None and value >= threshold - EPS,
        }
    money_safety_passed = all(item["passed"] for item in money_safety.values())

    quality = {}
    for name in QUALITY_METRICS:
        candidate_value = candidate_metrics.get(name)
        prod_value = prod_metrics.get(name)
        comparable = candidate_value is not None and prod_value is not None
        quality[name] = {
            "candidate": candidate_value,
            "prod": prod_value,
            "delta": (candidate_value - prod_value) if comparable else None,
            # "not worse than @prod": candidate >= prod. A missing value is not comparable.
            "not_worse": comparable and candidate_value >= prod_value - EPS,
        }
    quality_not_worse = all(item["not_worse"] for item in quality.values())

    wins = money_safety_passed and quality_not_worse
    reasons: list[str] = []
    if not money_safety_passed:
        failed = [name for name, item in money_safety.items() if not item["passed"]]
        reasons.append(f"money-safety invariant(s) below threshold: {', '.join(failed)}")
    if not quality_not_worse:
        worse = [name for name, item in quality.items() if not item["not_worse"]]
        reasons.append(
            f"verdict/disposition/amount worse than @prod (or missing): {', '.join(worse)}"
        )
    if wins:
        reasons.append(
            "all money-safety invariants pass and verdict/disposition/amount are not worse than @prod"
        )
    return {
        "wins": wins,
        "money_safety_passed": money_safety_passed,
        "money_safety": money_safety,
        "quality_not_worse": quality_not_worse,
        "quality_comparison": quality,
        "reasons": reasons,
    }


def promote_if_beats_prod(
    candidate_version: str,
    experiment_id: str,
    *,
    client: MlflowClient,
    search_runs=mlflow.search_runs,
    dry_run: bool = True,
    model_name: str = MODEL_NAME,
) -> dict:
    """Compare the candidate against current @prod and, if it wins, move the alias.

    The alias is moved ONLY when the candidate wins the gate AND ``dry_run`` is False.
    ``dry_run`` defaults to True so callers (and tests) never mutate the registry by
    accident; the CLI flips it to False only under ``--promote``.
    """
    prod_version = str(client.get_model_version_by_alias(model_name, PROD_ALIAS).version)
    candidate_version = str(candidate_version)
    decision = {
        "model_name": model_name,
        "candidate_version": candidate_version,
        "prod_version": prod_version,
        "dry_run": dry_run,
        "promoted": False,
        "alias_from_version": prod_version,
        "alias_to_version": None,
    }
    if candidate_version == prod_version:
        decision["reason"] = "candidate is already @prod; nothing to promote"
        return decision

    candidate_metrics = release_gate_metrics(search_runs, experiment_id, candidate_version)
    prod_metrics = release_gate_metrics(search_runs, experiment_id, prod_version)
    gate = evaluate_gate(candidate_metrics, prod_metrics)
    decision["gate"] = gate

    if not gate["wins"]:
        decision["reason"] = "candidate did not win the gate; @prod unchanged: " + "; ".join(
            gate["reasons"]
        )
        return decision
    if dry_run:
        decision["reason"] = (
            "candidate wins the gate; dry-run so @prod is unchanged. "
            "Re-run with --promote (profile fe-bar) to move the alias."
        )
        return decision

    # Explicit, gated, logged mutation — the only path that moves the live alias.
    client.set_registered_model_alias(model_name, PROD_ALIAS, candidate_version)
    decision["promoted"] = True
    decision["alias_to_version"] = candidate_version
    decision["reason"] = (
        f"candidate wins the gate; @prod moved from version {prod_version} to {candidate_version}"
    )
    return decision


def _experiment_id(experiment: str) -> str:
    exp = mlflow.get_experiment_by_name(experiment)
    if exp is None:
        raise LookupError(f"experiment not found: {experiment}")
    return exp.experiment_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", ""))
    parser.add_argument("--candidate-version", required=True, help="UC model version to consider")
    parser.add_argument("--experiment", default="/Shared/claims-adjudication-offline-evaluation")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Actually move the @prod alias if the candidate wins (default: dry-run report only).",
    )
    args = parser.parse_args()
    # Moving the LIVE alias is only ever approved for the explicit fe-bar profile.
    if args.promote and args.profile != "fe-bar":
        raise ValueError(
            "moving the live @prod alias is approved only for the explicit fe-bar profile"
        )
    if args.profile:
        os.environ["DATABRICKS_CONFIG_PROFILE"] = args.profile
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    decision = promote_if_beats_prod(
        args.candidate_version,
        _experiment_id(args.experiment),
        client=MlflowClient(registry_uri="databricks-uc"),
        dry_run=not args.promote,
    )
    print(json.dumps(decision, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
