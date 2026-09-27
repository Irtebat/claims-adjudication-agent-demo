# Serving endpoint deployment evidence

Status: **deployed and smoke-tested successfully**.

## Completed code changes

- Shared workspace client selects dedicated service-principal M2M credentials in
  served mode and the explicit `fe-bar` profile for local operation.
- Lakebase connections use `LAKEBASE_DB_USER` when supplied while retaining TLS and
  generated database credentials.
- Reasoning calls use the Unity Gateway OpenAI-compatible chat route; embeddings
  continue to use the governed embedding route.
- Model registration no longer declares endpoint-identity passthrough resources.
- Packaged evaluation omits optional `None` request fields for MLflow 3.16 schema
  compatibility.

## Governed model services

- Reasoning: `system.ai.gpt-5-2` (empirically returned HTTP 200 and a valid completion)
- Embeddings: `system.ai.gte-large-en`
- Routes: `/ai-gateway/mlflow/v1/chat/completions` and
  `/ai-gateway/mlflow/v1/embeddings`

## Registration and evaluation

- Registered model: `fe-bar-ir.default.claims_adjudication_agent`
- Fresh candidate/prod version: `1`
- Artifact: `models:/m-851a2f11d2b4406eb04bf70d39da5bad`
- Exact evaluation run: `c237ef5f4d7c4b6b95e03dcf7165b603`
- Money-safety bootstrap gate: passed; `@prod` resolves to version 1
- Exact diagnostics: disposition match `0.7333`, verdict match `0.9467`; all money,
  eligibility, duplicate, invariant, and citation safety gates passed.

## Dedicated service principal

- Display name: `claims-adjudication-serving`
- App/client ID: `47643eb1-dbd5-40a6-a51d-5da6b8e2da7a`
- Workspace SCIM ID: `71819096655994`
- Secret scope: `claims-agent`
- Keys: `app-sp-client-id`, `app-sp-client-secret`, `lakebase-db-user`
- No secret values are recorded here.
- The accidental duplicate `98283b7d-68cb-4978-8821-eb73c93d85ce`
  (`claims-adjudication-agent-serving`) was removed. The canonical SP above is the
  only remaining claims-adjudication application SP in the workspace.
- The service principal has only the `workspace-access` workspace entitlement,
  required to resolve the Lakebase endpoint and mint its database credential.

## Lakebase role and grants

Created resource ID `claims-agent-sp`, with `spec.postgres_role` equal to canonical
SP UUID `47643eb1-dbd5-40a6-a51d-5da6b8e2da7a` and OAuth auth. It has no superuser,
create-role, create-database, or bypass-RLS attributes.

Granted `CONNECT` on `databricks_postgres`; `USAGE` on `public` and `reference`;
`SELECT` on `public.claims`, `spec_params`, `warranty_terms`, `spec_clauses`,
`warranty_clauses`, and `prior_claims`; `SELECT` on `reference.heats_coils`,
`mill_test_certs`, and `customer_heat_risk`; `SELECT, INSERT, UPDATE` on
`public.adjudications`; and `SELECT, INSERT` on
`public.adjudication_decision_records`. `pg_get_serial_sequence` returned no
sequences for either writable table, so no sequence grant was applied.

## Governed service grants

The canonical SP has direct `USE CATALOG` on `system`, `USE SCHEMA` on
`system.ai`, and the user completed the required governed-function grants:

- `EXECUTE` on `system.ai.databricks-gpt-5-2`
- `EXECUTE` on `system.ai.gte_large_en_v1_5`

## Deploy and smoke test

The idempotent DAB job deployed endpoint
`agents_fe-bar-ir-default-claims_adjudication_agent`, version 1, with a Small
workload and scale-to-zero. Its final state was `READY`, `NOT_UPDATING`, and
`DEPLOYMENT_READY`.

The live `persist=true` smoke request returned HTTP 200 and adjudication
`ADJ-f3f80f6d541f69984498`. It proved all four runtime legs: Lakebase reads,
governed GTE hybrid retrieval, governed GPT-5.2 structured reasoning, and an atomic
write to both adjudication tables. The joined database verification returned one
new record with `record_version=1`.

- `smoke-request.json`: representative deployed-endpoint request
- `smoke-response.json`: redacted response and write result
- `smoke-verification.json`: endpoint, entitlement, four-leg, and database proof

## Minor review notes (not addressed)

- `parse_tool_calls` raises on malformed tool-call arguments instead of recording
  them in `invalid_tool_calls`.
- `bind()` keyword arguments are forwarded verbatim; verify that the gateway
  tolerates extras before using `with_structured_output`.
- The `model_service` Pydantic-namespace `UserWarning` is cosmetic.
