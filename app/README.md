# Steel Claims Cockpit — AppKit app

The Databricks App (AppKit — Node/TS/React) that adjusters and business users use to
review, finalize, and analyze steel warranty/quality claims. It ships four screens —
Work Queue, Claim Cockpit, Claims History, and the Business Leader Dashboard — over
the backend contract below, in a light Databricks theme with a dense command layout.

## Roles & server-side authorization (`server/authz.ts`)

Two roles, enforced on **every** `/api` route by a global guard registered in
`onPluginsReady` (so it precedes the deferred plugin-route mount and also guards the
auto-mounted Genie/analytics routes). Never enforced in the client.

| Surface                                                            | Adjuster | Business User |
| ------------------------------------------------------------------ | -------- | ------------- |
| `GET /api/queue` (pending queue)                                   | ✅       | ❌            |
| `GET /api/claims/:id` (cockpit detail)                             | ✅       | ❌            |
| `POST /api/claims/:id/finalize`                                    | ✅       | ❌            |
| `GET /api/history` (finalized claims)                              | ✅       | ✅            |
| `POST /api/genie/cockpit/*` (cockpit copilot)                      | ✅       | ❌            |
| `POST /api/genie/business/*` (business chat)                       | ❌       | ✅            |
| `/api/analytics/*`, `/api/business/dashboard` (business dashboard) | ❌       | ✅            |

Contract denials honored: Business Users are denied decision/finalize/queue;
Adjusters are denied the business-dashboard data endpoint.

**Role source (`server/identity.ts`):** governed **group membership** is the
**primary** mechanism. The signed-in user's OBO token (`x-forwarded-access-token`) is
presented to the Databricks **SCIM `/Me`** endpoint, and their **direct** group
memberships are mapped to a role by matching each group's display name **or** id
against `ADJUSTER_GROUPS` / `BUSINESS_GROUPS` (comma-separated). A **per-user
allowlist** (`ADJUSTER_USERS` / `BUSINESS_USERS`, keyed on `x-forwarded-email`) is an
**override** checked first — the escape hatch for individuals the group config can't
(yet) cover. Resolution order: **allowlist → (no OBO token ⇒ deny) → group lookup →
deny by default.**

- **Namespace:** `GROUP_SCOPE` selects `account` (→ `/api/2.0/account/scim/v2/Me`) or
  `workspace` (→ `/api/2.0/preview/scim/v2/Me`). **Default `account`.**
- **No extra grant / scope:** the `/Me` lookup rides the OBO token's default
  `iam.current-user:read` capability — **no** additional `user_api_scope` and **no**
  account-admin grant is required.
- **Fail-closed:** a caller matched by neither mechanism is hard-denied; any SCIM
  error/timeout hard-denies (never opens the door); any unmapped `/api/*` route is
  denied by default. Resolved roles are cached per user in a **bounded** LRU cache
  (≤ 5000 entries, ~120s TTL; expired entries purged on access, oldest evicted at
  capacity) — the OBO token is never cached, and failures are never cached.

### Role configuration (plain app env vars — no resource/grant needed)

| Env var           | Purpose                                                                |
| ----------------- | ---------------------------------------------------------------------- |
| `ADJUSTER_GROUPS` | **Primary.** Group display names / ids → `adjuster` (comma-sep).       |
| `BUSINESS_GROUPS` | **Primary.** Group display names / ids → `business_user` (comma-sep).  |
| `GROUP_SCOPE`     | `account` (default) or `workspace` — which SCIM `/Me` to call.         |
| `ADJUSTER_USERS`  | Override. Emails pinned to `adjuster` (checked first, comma-sep).      |
| `BUSINESS_USERS`  | Override. Emails pinned to `business_user` (checked first, comma-sep). |

`DATABRICKS_HOST` (SCIM host) is injected by the Apps runtime. `.env` is git-ignored;
set these locally in your own `.env` for `npm run dev`. With none set, every guarded
route hard-denies.

## Authentication

- **Lakebase (operational OLTP):** all reads/writes run as the **App service
  principal** via the platform-injected identity (`appkit.lakebase` pool; the
  platform mints the DB credential, `sslmode=require`). No local CLI profile fallback.
- **OBO (on-behalf-of the signed-in user):** used only for the **governed** surfaces
  — the Genie copilot/chat (`dashboards.genie` scope) and the business-dashboard
  warehouse queries (`sql` scope). The OBO user identity (`x-forwarded-email`) is
  captured as `decided_by` on a finalization.

## Backend endpoints

- **(a) `GET /api/queue`** — claims ⋈ adjudications where `decision_status='RECOMMENDED'`, with sort/filter.
- **(b) `GET /api/claims/:id`** — cockpit detail from ALREADY-PERSISTED data: the recommendation + full `adjudication_decision_records` trail (deterministic conformance/coverage/duplicate/settlement), citations, context (heats_coils, mill_test_certs, customers, customer_heat_risk), and prior-claim precedent. (Not Genie.)
- **(c) `POST /api/claims/:id/finalize`** — the human-finalization transaction (below).
- **(d) `GET /api/history`** — FINAL adjudications with `decided_by` / override metadata, sort/filter.
- **(e) cockpit copilot NL** — Genie plugin, alias `cockpit` → operational space (bundle variable `genie_space_id`).
- **(f) business dashboard** — analytics plugin (`config/queries/business_kpis.sql`, governed gold) + business chat via Genie alias `business` → gold-analytics space (bundle variable `business_genie_space_id`).

## Finalization transaction (`server/finalize.ts`, Task 3)

One Postgres transaction (App-SP pool), idempotent + first-write-wins:

1. `UPDATE public.adjudications` → `decision_status='FINAL'` with verdict, disposition,
   `approved_amount`, `override_flag`, `override_reason`, `decided_by`, `finalized_at`.
   Guarded `WHERE decision_status='RECOMMENDED'`.
2. `INSERT` a new immutable `record_version` into `public.adjudication_decision_records`
   capturing the human-final decision + `decided_by` + `override_reason`, **preserving
   the deterministic baseline** (`deterministic_verdict`/`disposition`, settlement,
   conformance, coverage, duplicate) copied from the prior version.
3. `INSERT` the `public.outbox` `claim.adjudicated` row (`event_id = adj-<id>`, payload
   reflecting the FINAL decision). This is the ONLY place the fan-out is emitted —
   the agent recommendation writes no outbox row.

Server-side rules: `override_reason` is MANDATORY whenever any of {verdict,
disposition, approved_amount} differs from the recommendation; the money math
(`authorities.py`) is never re-run (an override is a logged override, not a
recomputation); the final decision is validated internally money-consistent so it
cannot violate the silver/gold medallion invariants. Re-finalizing an already-FINAL
adjudication is a no-op (no double outbox, no new version).

## Gates

```
npm run test                      # vitest — authz matrix + finalize contract + identity/group resolver + RoleCache + UI
npx tsc -b tsconfig.server.json   # server typecheck — clean
npx tsc -b tsconfig.client.json   # client typecheck — clean
npx appkit lint                   # ast-grep (no-double-type-assertion, etc.) — clean
databricks bundle validate --profile fe-bar-ir-2026   # Validation OK
```

`server/**` and `client/**` are eslint- and prettier-clean.

## Prerequisites

- Lakebase seeded and the `reference.*` synced tables ONLINE (`lakebase/README.md`);
  the app's context query reads them.
- Both Genie spaces and the dashboard deployed (`dashboards/README.md`). Their IDs
  are bundle variables in `databricks.yml` (`genie_space_id`,
  `business_genie_space_id`, `warehouse_id`) and `DATABRICKS_DASHBOARD_ID` in
  `app.yaml` (rendered from `app.yaml.tmpl`).
- Node 18+ for local gates.

## Deploy

Working directory: `app/`. The app bundle uses target `default`.

```bash
databricks bundle validate --strict -t default --profile fe-bar-ir-2026
databricks bundle deploy -t default --profile fe-bar-ir-2026
databricks bundle run app -t default --profile fe-bar-ir-2026
```

`bundle deploy` creates the app and its service principal, applies the
`user_api_scopes` (`dashboards.genie`, `sql`) and resources, and injects
`PGHOST`/`PGDATABASE`/`PGPORT`/`PGSSLMODE`. `bundle run app` builds and restarts the
app from the uploaded source; run it after every code change.

### First-time only

1. Read the app SP client id from the deployed app:

   ```bash
   databricks apps get steel-claims-cockpit --profile fe-bar-ir-2026 -o json | jq -r .service_principal_client_id
   ```

2. Grant it the Lakebase privileges with `app/scripts/setup_app_sp.py` (idempotent;
   `--app-principal` or env `APP_SP_PRINCIPAL`). The app SP is distinct from the
   serving SP `claims-adjudication-serving`.

   ```bash
   uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
     python app/scripts/setup_app_sp.py --profile fe-bar-ir-2026 \
     --app-principal <app-sp-id>
   ```

   The grant set (`GRANTS` in the script):
   - `USAGE` on `public` + `reference`.
   - `SELECT` on `public.claims`, `adjudications`, `adjudication_decision_records`,
     `spec_params`, `spec_clauses`, `warranty_terms`, `warranty_clauses`, and
     `reference.heats_coils`, `mill_test_certs`, `customers`, `customer_heat_risk`,
     `prior_claims_corpus`.
   - `INSERT, UPDATE` on `public.adjudications` and
     `public.adjudication_decision_records` (finalize UPDATE + new record_version).
   - **`SELECT, INSERT` on `public.outbox`** — required by finalize; the outbox
     write uses `ON CONFLICT (event_id) DO NOTHING`, which needs `SELECT`. Without
     it finalize fails with `permission denied for table outbox`.

   The `reference.*` SELECTs are reapplied by
   `lakebase/scripts/regrant_synced_table_selects.py` whenever a synced table is
   created or recreated; export `APP_SP_PRINCIPAL=<app-sp-id>` for those runs.
3. Set the role config in `app.yaml` `env` (plain env vars, see `server/identity.ts`):
   `ADJUSTER_GROUPS` / `BUSINESS_GROUPS` (comma-separated group display names or
   ids) are the primary mechanism; `ADJUSTER_USERS` / `BUSINESS_USERS`
   (comma-separated emails, e.g. `<user>`) are per-user overrides checked first.
   Optionally set `GROUP_SCOPE` (`account` | `workspace`, default `account`). With
   none set, every guarded route hard-denies. The SCIM `/Me` lookup needs no extra
   `user_api_scope` and no admin grant. Redeploy (`bundle deploy` + `bundle run
   app`) after changing them.
4. For the embedded dashboard, add the app domain to the workspace AI/BI embedding
   approved-domains list; until then the app shows an open-in-Databricks link.

## Run

The app runs continuously once deployed (`lifecycle.started: true`). Locally, set
the role env vars in your own git-ignored `.env` and run `npm run dev`.

## Verify

Read-only:

```bash
databricks apps get steel-claims-cockpit --profile fe-bar-ir-2026 -o json \
  | jq '{app: .app_status.state, compute: .compute_status.state, deployment: .active_deployment.status.state, url}'
TOKEN="$(databricks auth token --profile fe-bar-ir-2026 -o json | jq -r .access_token)"
curl -fsS -H "Authorization: Bearer $TOKEN" "<app-url>/api/whoami"
```

Expected: app `RUNNING`, compute `ACTIVE`, deployment `SUCCEEDED`, and
`/api/whoami` returns the caller's resolved role set. After a redeploy, confirm
`active_deployment.deployment_id` changed. The token command needs U2M
authentication; never print or commit the bearer. On the deployed app, the server
logs the resolved SCIM group count once per request — use it to confirm the scoped
OBO token returns `groups[]`.
