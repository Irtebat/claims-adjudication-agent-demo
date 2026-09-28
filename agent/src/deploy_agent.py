"""Idempotently deploy the validated claims-adjudication agent version."""

from __future__ import annotations

import json

from databricks import agents
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from mlflow.tracking import MlflowClient

MODEL_NAME = "fe-bar-ir.default.claims_adjudication_agent"
MODEL_ALIAS = "prod"
ENDPOINT_NAME = "agents_fe-bar-ir-default-claims_adjudication_agent"


def main() -> None:
    workspace = WorkspaceClient()
    model_version = int(
        MlflowClient(registry_uri="databricks-uc")
        .get_model_version_by_alias(MODEL_NAME, MODEL_ALIAS)
        .version
    )
    try:
        workspace.serving_endpoints.get(ENDPOINT_NAME)
        action = "redeploy"
    except NotFound:
        action = "create"

    deployment = agents.deploy(
        MODEL_NAME,
        model_version,
        endpoint_name=ENDPOINT_NAME,
        scale_to_zero=True,
        workload_size="Small",
        environment_vars={
            "DATABRICKS_HOST": workspace.config.host,
            "APP_SP_CLIENT_ID": "{{secrets/claims-agent/app-sp-client-id}}",
            "APP_SP_CLIENT_SECRET": "{{secrets/claims-agent/app-sp-client-secret}}",
            "LAKEBASE_DB_USER": "{{secrets/claims-agent/lakebase-db-user}}",
        },
    )
    print(
        json.dumps(
            {
                "action": action,
                "endpoint_name": ENDPOINT_NAME,
                "endpoint_url": f"{workspace.config.host}/ml/endpoints/{ENDPOINT_NAME}",
                "model_name": MODEL_NAME,
                "model_version": model_version,
                "deployment": str(deployment),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
