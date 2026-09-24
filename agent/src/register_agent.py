"""Log, register, validate, and alias the claims-adjudication agent in Unity Catalog.

Runs as local runtime Python against the ``fe-bar`` workspace (set
``DATABRICKS_CONFIG_PROFILE=fe-bar``). Logs the ResponsesAgent via
``mlflow.pyfunc.log_model`` (Models-from-code: ``python_model="agent.py"`` plus the
sibling modules as ``code_paths``), with pinned deps and the passthrough-auth
resources (the reasoning endpoint + Lakebase). It registers to
``fe-bar-ir.default.claims_adjudication_agent``, validates the isolated artifact
with ``mlflow.models.predict(env_manager="uv")`` on a real sample claim
(``persist=false`` — validation never writes), and sets the ``@prod`` alias. It
does NOT create a serving endpoint (that is a later workstream).
"""

from __future__ import annotations

import argparse
import json
import os

import mlflow
from mlflow.models.resources import DatabricksLakebase, DatabricksServingEndpoint
from mlflow.tracking import MlflowClient

# Mirror the agent constants without importing agent.py (which calls set_model).
MODEL_NAME = "fe-bar-ir.default.claims_adjudication_agent"
LLM_ENDPOINT = "databricks-gpt-5-2"
LAKEBASE_INSTANCE = "fe-bar-operational-plane"
_HERE = os.path.dirname(os.path.abspath(__file__))

CODE_MODULES = [
    "agent_tools.py",
    "decision_record.py",
    "writer.py",
    "authorities.py",
    "authorities_runtime.py",
    "resolution.py",
    "duplicate.py",
    "retrieval.py",
    "heat_risk.py",
    "db.py",
    "gateway_embed.py",
]
PIP_REQUIREMENTS = [
    "mlflow>=3.1.3",
    "databricks-agents>=1.1.0",
    "databricks-langchain",
    "langgraph",
    "psycopg[binary]",
    "pydantic>=2",
    "databricks-sdk>=0.81.0",
]


def _sample_claim(profile: str) -> dict:
    """A real claim from Lakebase for the input example / isolated validation."""
    import sys

    sys.path.insert(0, _HERE)
    from db import connect

    with connect(profile=profile, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT claim_id, coil_id, customer_id, claim_type, claim_date, install_date, "
                "environment, installation, coast_distance_km, defect_code, defect_narrative, "
                "claimed_tonnage, claimed_freight FROM claims "
                "WHERE coil_id IS NOT NULL ORDER BY claim_id LIMIT 1"
            )
            columns = [c.name for c in cur.description]
            row = cur.fetchone()
    claim = dict(zip(columns, row))
    return {k: (str(v) if hasattr(v, "isoformat") else v) for k, v in claim.items()}


def run(profile: str, experiment: str, validate: bool = True) -> dict:
    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")
    client = MlflowClient(registry_uri="databricks-uc")

    # A named NON-Git experiment (standard MLflow traces), parent dir pre-created.
    mlflow.set_experiment(experiment)

    claim = _sample_claim(profile)
    input_example = {
        "input": [{"role": "user", "content": json.dumps(claim)}],
        "custom_inputs": {"persist": False, "claim": claim},
    }
    resources = [
        DatabricksServingEndpoint(endpoint_name=LLM_ENDPOINT),
        DatabricksLakebase(database_instance_name=LAKEBASE_INSTANCE),
    ]
    with mlflow.start_run(run_name="claims-adjudication-agent") as run_ctx:
        info = mlflow.pyfunc.log_model(
            name="agent",
            python_model=os.path.join(_HERE, "agent.py"),
            code_paths=[os.path.join(_HERE, module) for module in CODE_MODULES],
            resources=resources,
            input_example=input_example,
            pip_requirements=PIP_REQUIREMENTS,
            registered_model_name=MODEL_NAME,
        )
        run_id = run_ctx.info.run_id

    result = {
        "run_id": run_id,
        "model_uri": info.model_uri,
        "model_version": info.registered_model_version,
    }

    if validate:
        # Rebuild the env in isolation and run one request (persist=False -> no writes).
        env = dict(os.environ, LAKEBASE_PROFILE=profile)
        os.environ.update(env)
        mlflow.models.predict(
            model_uri=info.model_uri,
            input_data=input_example,
            env_manager="uv",
        )
        result["validated"] = True

    client.set_registered_model_alias(MODEL_NAME, "prod", info.registered_model_version)
    result["alias"] = "prod"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.environ.get("DATABRICKS_CONFIG_PROFILE", "fe-bar"))
    parser.add_argument(
        "--experiment",
        default="/Users/irtebat.shaukat@databricks.com/claims_adjudication_agent",
    )
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args()
    summary = run(args.profile, args.experiment, validate=not args.no_validate)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
