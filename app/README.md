# App

## Purpose

The live app is the human review and analytics interface. Adjusters review queued recommendations, drill through cited policy, source records, risk, and precedent, then finalize decisions. Business users see claims history, governed analytics, and Genie. Multi-role callers can switch persona; the switch changes presentation, never authorization.

Authentication deliberately splits by data plane. Operational Lakebase reads and finalization writes use the App-SP through the platform-injected pool. OBO (on-behalf-of-user) identity is used for SCIM role resolution and governed Genie and SQL warehouse surfaces. The OBO email is recorded as `decided_by`; the client persona never selects the authorization identity.

## Objects created

- App `steel-claims-cockpit`.
- Screens: Adjuster Queue, Claim Cockpit, Claims History, Business Dashboard, and Genie assistants.
- Read-only drill-through from Claim Cockpit to policy clauses, prior claims, and customer heat risk.

## Resources configured

The bundle attaches SQL warehouse `38e458a09de4a055`, two Genie spaces, and the production Lakebase database. Server-side authorization derives a role set from Databricks groups. `GET /api/whoami` returns identity, all roles, and the default role.

Role resolution is allowlist override → OBO group lookup → deny. SCIM failures deny access; the OBO token is never cached. Lakebase never falls back to a developer profile: App-SP ownership and grants govern operational access.

| Endpoint | Access |
| --- | --- |
| `GET /api/whoami` | Either role |
| `GET /api/queue`, `GET /api/claims/:id`, `GET /api/source/:source/:id` | Adjuster |
| `POST /api/claims/:id/finalize` | Adjuster |
| `GET /api/history`, `/api/genie/history/*` | Either role |
| `/api/genie/cockpit/*` | Adjuster |
| `/api/genie/business/*`, `/api/analytics/*`, `GET /api/business/dashboard` | Business user |

Unknown API routes fail closed.

## Data flow

```mermaid
flowchart LR
  U[Databricks user] --> A[Live app]
  A -->|identity| D[Databricks APIs]
  A -->|operations| L[Lakebase]
  A -->|analytics| W[SQL warehouse]
  A -->|questions| G[Genie]
```

## Deploy

Working directory: `app/`.

### First-time only

Before first deploy, grant the app service principal `d5309ee7-a8ea-499f-99d4-4ccbd8369d93` the Lakebase privileges required by finalization: `INSERT` on `public.outbox`; `SELECT, UPDATE` on `public.adjudications`; and `INSERT` on `public.adjudication_decision_records`. Use the reviewed SQL in [app deployment grants](../docs/evidence/app-deploy/grants.sql); the services `migrate` job also applies the schema/grants needed by that layer.

Configure `ADJUSTER_GROUPS`, `BUSINESS_GROUPS`, and `GROUP_SCOPE` (`account` by default). `ADJUSTER_USERS` and `BUSINESS_USERS` are per-user overrides, not the primary role mechanism. `app.yaml` currently pins one email for the demo account. With no matching group or override, guarded routes deny by default.

```bash
databricks bundle validate --strict -t default --profile fe-bar
databricks bundle deploy -t default --profile fe-bar
databricks bundle run app -t default --profile fe-bar
```

## Run

The deployed app starts automatically. Redeploy after an app change with the Deploy sequence.

## Verify

Working directory: `app/`. These commands are read-only.

```bash
databricks apps get steel-claims-cockpit --profile fe-bar -o json
APP_OAUTH_TOKEN="$(databricks auth token --profile fe-bar -o json | jq -r .access_token)"
curl -fsS -H "Authorization: Bearer $APP_OAUTH_TOKEN" \
  "https://steel-claims-cockpit-7474655183924919.aws.databricksapps.com/api/whoami"
```

The token command is confirmed on CLI 1.17 and requires U2M authentication. Verify the app is `RUNNING`, deployment is `SUCCEEDED`, and `/api/whoami` returns the expected role set. Never print or commit the bearer.

Development checks from `app/`: `npm test && npx tsc -b tsconfig.server.json && npx appkit lint`.

## Status

As of 2026-10-01, repo head describes the full UI and the app is live. Persona screenshots and a fresh authenticated whoami capture remain [pending](../docs/evidence/current-state/pending.md).
