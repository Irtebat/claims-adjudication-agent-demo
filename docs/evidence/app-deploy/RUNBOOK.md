> **Superseded note (2026-10-01): the old single-role `/api/whoami` contract is replaced by the live role-set response documented in `app/README.md`.**

# Steel Claims Cockpit — App Deploy Runbook & Evidence

Deploy date: 2026-09-29 · Profile: `fe-bar` · Workspace: `https://fe-sandbox-fe-bar-ir.cloud.databricks.com`
Branch: `app-deploy` · Bundle: `app/` (`steel-claims-cockpit`)

## 1. App (deployed, RUNNING)

| Field | Value |
|-------|-------|
| App URL | https://steel-claims-cockpit-7474655183924919.aws.databricksapps.com |
| State | RUNNING / compute ACTIVE |
| App SP client_id (= Postgres role) | `d5309ee7-a8ea-499f-99d4-4ccbd8369d93` |
| App SP id | `78047036304508` |
| App SP name | `app-40xso0 steel-claims-cockpit` |
| Deploy command | `databricks apps deploy --target default --auto-approve --profile fe-bar` (AppKit pipeline: build → bundle deploy → run) |

**Why `apps deploy` not raw `bundle deploy`:** the app's `start` runs the prebuilt
`dist/server.js`, and `dist/` is `.gitignore`d — a raw `databricks bundle deploy` syncs
the app source honoring `.gitignore` and would ship without the server bundle. The AppKit
`apps deploy` pipeline builds and ships the build output correctly (the platform also
re-runs `npm install`/build server-side).

**Bundle fix required before deploy** (`app/databricks.yml`): the `postgres` app resource
needs FULL Lakebase resource paths; the target vars were short names. Fixed:
- `postgres_branch: projects/fe-bar-operational-plane/branches/production`
- `postgres_database: projects/fe-bar-operational-plane/branches/production/databases/databricks-postgres` (note hyphen)
- `postgres_project: projects/fe-bar-operational-plane`

Resolved app resources (confirmed via `databricks apps get`): sql-warehouse `38e458a09de4a055`
CAN_USE; genie-space `01f1bb5b9d081378b00a283760825c64` CAN_RUN; genie-space-business
`01f1bac20bf6119f84fa99c7ba438ba4` CAN_RUN; postgres (full paths) CAN_CONNECT_AND_CREATE.

## 2. Lakebase grants (least-privilege, App SP)

Grant target = Postgres role `d5309ee7-a8ea-499f-99d4-4ccbd8369d93` (auto-provisioned on
deploy as role_id `dbrx-apps-d5309ee7-…`; no role creation needed). Applied as
DATABRICKS_SUPERUSER `irtebat.shaukat@databricks.com` against `databricks_postgres`
(endpoint `ep-lively-waterfall-d8mn6aui…`). Full statements: `grants.sql` (this dir).

- USAGE on `public`, `reference`.
- SELECT on `public.{claims, adjudications, adjudication_decision_records, spec_params,
  spec_clauses, warranty_terms, warranty_clauses, prior_claims}`.
- SELECT on `reference.{heats_coils, mill_test_certs, customers, customer_heat_risk}`.
- INSERT, UPDATE on `public.adjudications`, `public.adjudication_decision_records`.
- INSERT on `public.outbox` (finalize event fan-out).

**Live verification (`has_table_privilege` / `has_schema_privilege` for the SP role):**
all 19 required privileges = `true`; negative checks (UPDATE `public.claims`, DELETE
`public.outbox`) = `false`. Least-privilege confirmed.

## 3. Role env (app.yaml, literal values)

`ADJUSTER_USERS=irtebat.shaukat@databricks.com` and
`BUSINESS_USERS=irtebat.shaukat@databricks.com` set in `app/app.yaml` before deploy.
Live: `GET /api/whoami` → `{"email":"irtebat.shaukat@databricks.com","role":"adjuster"}`.

> ⚠️ **Persona precedence (needs your decision).** The role resolver
> (`server/identity.ts`) returns a SINGLE role and checks `ADJUSTER_USERS` **before**
> `BUSINESS_USERS`. With the same email in both lists it always resolves to `adjuster`,
> so the **business surface is NOT reachable by this user as delivered** (there is no
> in-app persona switch). To demo the business persona, flip the config and redeploy:
> set `ADJUSTER_USERS=''` and keep `BUSINESS_USERS=…`, OR move to group-based
> (`BUSINESS_GROUPS`) with the user only in a business group.

## 4. OBO / Genie / warehouse

- App `user_api_scopes`: `dashboards.genie`, `sql` (confirmed on the deployed app; survived redeploy).
- User `irtebat.shaukat@databricks.com` on Genie spaces: **CAN_MANAGE** (≥ CAN_RUN) on
  operational `01f1bb5b9d081378b00a283760825c64` AND gold `01f1bac20bf6119f84fa99c7ba438ba4`.
- User on warehouse `38e458a09de4a055`: **CAN_USE** (via `users`) + CAN_MANAGE (via `admins`).
- No grants were missing — nothing to add.

## 5. Work queue (RECOMMENDED)

- RECOMMENDED = **10** (also FINAL=4999, REVIEWED=1; claims=5000). Queue endpoint returns them.
- Demo-backlog job **NOT run**: task gates it on "if Lakebase has none" (it has 10) and
  flags LLM cost — so it was intentionally skipped.

> ⚠️ **Queue demoability (needs your decision).** All 10 RECOMMENDED adjudications have
> `recommended_at = NULL` and sit on claims that also carry an older non-RECOMMENDED
> adjudication (9 FINAL + 1 REVIEWED). The cockpit selects the current adjudication via
> `ORDER BY recommended_at DESC NULLS LAST`, so it resolves the colliding FINAL/REVIEWED
> row (no decision records / citations) instead of the RECOMMENDED one — even though each
> RECOMMENDED adjudication IS evidence-complete (1 decision record, 3 citations, 3 clause
> ids). Net: the cockpit renders correctly but shows the wrong (empty) adjudication for
> every current queue item.
>
> A zero-cost backfill (`UPDATE public.adjudications SET recommended_at = now() WHERE
> decision_status='RECOMMENDED' AND recommended_at IS NULL`) was **blocked by the session's
> safety classifier** (broad production write). Pick one, all authorized paths:
> - **(A)** Authorize that scoped `recommended_at` backfill (free; reuses existing evidence).
> - **(B)** Run the demo-backlog job for ~25 FRESH claims (`demo/`); fresh claims have a
>   single RECOMMENDED adjudication so the cockpit resolves it correctly (LLM cost, ~25).
> - **(C)** App-logic change: have the cockpit prefer `decision_status='RECOMMENDED'` over
>   FINAL/REVIEWED (free, behavior change to `server/sql.ts::cockpitAdjudicationSql`).

## 6. Cockpit SQL bugfix (`server/sql.ts`) — applied

The cockpit `cockpitContextSql` `mill_test_cert` scalar subquery lacked a `LIMIT 1`; the
reference data has multiple MTCs per heat (900 heats), so the subquery returned >1 row →
**every** cockpit request 500'd (`more than one row returned by a subquery used as an
expression`). Added `ORDER BY m.cert_date DESC NULLS LAST, m.cert_id DESC LIMIT 1`
(mirrors the sibling `customer_heat_risk` subquery's existing `LIMIT 1`). After fix +
redeploy the cockpit returns 200 with full context (heats_coils, mill_test_cert, customer,
customer_heat_risk). 57/57 unit tests pass. **Please review this app-logic change.**

## 7. Smoke test (against the deployed app, user OAuth bearer)

| Check | Result |
|-------|--------|
| `GET /api/whoami` | 200 → role `adjuster` (role env live) ✅ |
| `GET /api/queue` | 200 → returns RECOMMENDED claims (App-SP Lakebase reads work) ✅ |
| `GET /api/history` | 200 (both-roles claims history) ✅ |
| `GET /api/claims/:id` (cockpit) | 200; context populates; **evidence blocked** by the queue-data collision (§5) ⚠️ |
| `POST /api/claims/:id/finalize` | 400 `invalid_body` on bad input — route wired + validates, **NON-mutating** (no real finalize performed) ✅ |
| Genie history OBO (`POST /api/genie/history/messages`) | Full SSE round-trip: generated SQL + answer "5,000 claims" + query_result ✅ |
| Business dashboard route (`GET /api/business/dashboard`) | 403 `role_not_permitted` as adjuster — route wired + authz-enforced; not reachable by this user by design (§3) ⚠️ |

No real claim was finalized (no disposable staged claim available; per instruction).

## 8. Items needing a human hand

1. **git push** — commit is local on `app-deploy`; push/PR is handed off (known 403).
2. **Queue demoability** — choose (A) backfill / (B) demo-backlog job / (C) cockpit ordering (§5).
3. **Business persona** — flip config to demo the business surface (§3).
4. **Review** the `server/sql.ts` cockpit bugfix (§6).
