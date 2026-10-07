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
- **(e) cockpit copilot NL** — Genie plugin, alias `cockpit` → operational space `01f1bb5b9d081378b00a283760825c64`.
- **(f) business dashboard** — analytics plugin (`config/queries/business_kpis.sql`, governed gold) + business chat via Genie alias `business` → gold-analytics space `01f1bac20bf6119f84fa99c7ba438ba4`.

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

## Deploy

Deploying provisions the app's own service principal, whose id the Lakebase grants
need. Order:

1. `databricks bundle deploy -t default --profile fe-bar-ir-2026` — creates the app + its SP,
   injects `PGHOST`/`PGDATABASE`/`PGPORT`/`PGSSLMODE` + the resource envs.
2. Grant the **app SP** its fine-grained Lakebase grants with
   `app/scripts/setup_app_sp.py` (the app SP is distinct from the serving SP
   `claims-adjudication-serving`; pass the app SP client id from step 1's
   `service_principal_client_id`):

   ```bash
   uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
     python app/scripts/setup_app_sp.py --profile fe-bar-ir-2026 \
     --app-principal <app-sp-client-id>
   ```

   The grant set mirrors `docs/evidence/app-deploy/grants.sql`:
   - `USAGE` on `public` + `reference`.
   - `SELECT` on `public.claims`, `adjudications`, `adjudication_decision_records`,
     `spec_params`, `spec_clauses`, `warranty_terms`, `warranty_clauses`, and
     `reference.heats_coils`, `mill_test_certs`, `customers`, `customer_heat_risk`,
     `prior_claims_corpus`. The `reference.*` SELECTs are also reapplied by
     `lakebase/scripts/regrant_synced_table_selects.py` (via `lakebase/run.py
     synced-tables`) when a synced table is created or recreated.
   - `INSERT, UPDATE` on `public.adjudications` and
     `public.adjudication_decision_records` (finalize UPDATE + new record_version).
   - **`INSERT` on `public.outbox`** ← REQUIRED for finalize; not in the serving SP's
     documented grants (`docs/evidence/serving-endpoint/README.md`) — a NEW grant.
3. Enable **user authorization** with scopes `dashboards.genie` + `sql` (in
   `databricks.yml`, applied on deploy) so OBO works for Genie + the warehouse.
4. Set the role config (plain app env vars — no resource/grant needed):
   `ADJUSTER_GROUPS` / `BUSINESS_GROUPS` (comma-separated group display names or ids)
   as the **primary** mechanism, and optionally `ADJUSTER_USERS` / `BUSINESS_USERS`
   (comma-separated emails) as the per-user **override**. Optionally set `GROUP_SCOPE`
   (`account` | `workspace`, default `account`). With none set, all guarded routes
   hard-deny. The SCIM `/Me` group lookup needs **no** extra `user_api_scope` and
   **no** admin grant (default `iam.current-user:read` covers `/Me`). See
   `.env.example`.
5. **Deploy-time verification (not a blocker, no grant):** on the deployed app, confirm
   the narrowly-scoped OBO token returns a populated `groups[]` under
   `iam.current-user:read` — the server logs the resolved group count once per real
   request. The `/Me` endpoint + body shape are confirmed with a full user token; the
   scoped-token case is the only bit that can only be verified live.

Steps 2–4 need the deployed app SP id (step 1 creates it); some sub-steps need
account-admin (see `docs/evidence/copilot-app-backend/`).
