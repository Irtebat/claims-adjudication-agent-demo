# Serving endpoint deployment evidence

Status: **stopped during Phase 3 Lakebase role creation**. No serving endpoint was
created.

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

## Lakebase role and grants

Not applied. The requested role creation failed validation because the client ID
starts with a digit and cannot be used as the Lakebase API `role-id`. See
`POST-DEPLOY-NOTES.md`.

## Deploy and smoke test

Not run. There is no endpoint name or URL yet, and no smoke-test transaction was
written.
