# Post-deploy notes

## Residual UI grant blocker

Lakebase role creation and least-privilege SQL grants are complete. The canonical
SP also has `USE CATALOG` on `system` and `USE SCHEMA` on `system.ai`.

The remaining governed-service grants must be applied in the UI because the UC
Grants API returns `MODEL is not enabled` and SQL does not expose the services as
grantable routines/models:

- grant `EXECUTE` on `system.ai.gpt-5-2` to
  `47643eb1-dbd5-40a6-a51d-5da6b8e2da7a`
- grant `EXECUTE` on `system.ai.gte-large-en` to
  `47643eb1-dbd5-40a6-a51d-5da6b8e2da7a`

The `claims-agent` scope key names are `app-sp-client-id`,
`app-sp-client-secret`, and `lakebase-db-user`. Before deployment, create a fresh
OAuth secret for the canonical SP and overwrite all three keys (ID/user keys with
the canonical UUID, secret key with the fresh secret). The account credential CLI
currently targets the workspace host with `--profile fe-bar` and returns HTTP 404.

After those two external prerequisites, deploy with the committed DAB job and run
one `persist=true` adjudication through the endpoint, retrying once for cold start;
then verify the corresponding `public.adjudication_decision_records` row.
