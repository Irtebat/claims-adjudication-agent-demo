# Steel Claims Cockpit — AppKit app (Wave 7, Stage A: BACKEND, headless)

The Databricks App (AppKit — Node/TS/React) that adjusters and business users use to
review, finalize, and analyze steel warranty/quality claims. **Stage A is the
backend contract + headless logic + tests only — no UI.** Stage B builds the UI on
this contract (Databricks brand tokens, Linear-style dense command layout).

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

**Role source:** an explicit **per-user allowlist** keyed on the authenticated
caller's email — env `ADJUSTER_USERS` / `BUSINESS_USERS` (comma-separated). This is
the **sole** role mechanism, and the config advertises nothing more. Group-based
mapping is intentionally not wired: the typed experimental workspace-client
`currentUser.me()` in this scaffold does not expose group membership, so a governed
group lookup isn't available here — wiring one would risk a deploy whose config
doesn't match enforcement. A caller matching no allowlist is **hard-denied**, and any
unmapped `/api/*` route is denied by default (fail-closed).

## Authentication

- **Lakebase (operational OLTP):** all reads/writes run as the **App service
  principal** via the platform-injected identity (`appkit.lakebase` pool; the
  platform mints the DB credential, `sslmode=require`). No `fe-bar` profile fallback.
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

## Gates (all green, offline)

```
npm run test           # vitest — 33 tests (authz matrix + finalize contract)
npx tsc -b tsconfig.server.json   # server typecheck
npm run lint           # eslint
npx appkit lint        # ast-grep (no-double-type-assertion, etc.)
npm run format         # prettier
databricks bundle validate --profile fe-bar   # Validation OK
```

## Deploy runbook (LIVE — Stage A stops before these; several need the app SP)

`databricks apps init` created the app SP is NOT done here. Deploying provisions the
app's own service principal, whose id is needed to grant Lakebase. Order:

1. `databricks bundle deploy -t default --profile fe-bar` — creates the app + its SP,
   injects `PGHOST`/`PGDATABASE`/`PGPORT`/`PGSSLMODE` + the resource envs.
2. Grant the **app SP** a Lakebase Postgres role + fine-grained grants (the app SP is
   distinct from the serving SP `claims-adjudication-serving`):
   - `CONNECT` on `databricks_postgres`; `USAGE` on `public` + `reference`.
   - `SELECT` on `public.claims`, `adjudications`, `adjudication_decision_records`,
     `prior_claims`, and `reference.*`.
   - `SELECT, UPDATE` on `public.adjudications` (finalize UPDATE).
   - `INSERT` on `public.adjudication_decision_records` (new record_version).
   - **`INSERT` on `public.outbox`** ← REQUIRED for finalize; not in the serving SP's
     documented grants (`docs/evidence/serving-endpoint/README.md`) — a NEW grant.
3. Enable **user authorization** with scopes `dashboards.genie` + `sql` (in
   `databricks.yml`, applied on deploy) so OBO works for Genie + the warehouse.
4. Set the role allowlists `ADJUSTER_USERS` / `BUSINESS_USERS` (comma-separated
   emails) — the sole role mechanism. Without them all guarded routes hard-deny.

Steps 1–4 need the deployed app SP id and may need account-admin — they are the
Stage-A STOP-AND-REPORT items (see `docs/evidence/copilot-app-backend/`).
