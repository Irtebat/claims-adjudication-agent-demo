> **Superseded 2026-10-01:** replaced by the live full UI and [current-state app evidence](../current-state/runtime-resources.md). Retained as history.

# Evidence — Wave 7 Stage A: Copilot App BACKEND contract (headless)

Branch `copilot-app-backend` off `main` (e84bf20). No UI in this stage. authorities.py
is untouched (deterministic money math). All commits are local (see the push
stop-and-report below).

## What shipped, by task

| Task | Deliverable                                                                                                                                 | Status                                                                                                  |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| 1    | Schema migration: `decided_by`/`override_reason` on `adjudications` + `adjudication_decision_records`; live ALTER + AUTHORIZED full-refresh | DONE + verified live — see [schema-migration-and-full-refresh.md](schema-migration-and-full-refresh.md) |
| 2    | Recommendation writer emits NO outbox; fan-out moves to human finalization; Wave 8 coherence + tests                                        | DONE (agent + services suites green)                                                                    |
| 3    | Finalize transaction (App-SP, idempotent, override-reason enforced, preserves deterministic baseline, sole outbox emitter)                  | DONE (`app/server/finalize.ts` + tests)                                                                 |
| 4    | AppKit scaffold + backend: App-SP Lakebase auth, OBO for governed surfaces, two-role server-side authz, JSON endpoints                      | DONE (headless) — deploy/grants are stop-and-report                                                     |
| 5    | New operational Genie space for the cockpit copilot                                                                                         | DONE — space id `01f1bb5b9d081378b00a283760825c64`                                                      |
| 6    | Parameterized demo-backlog DABs job (N synthetic claims → RECOMMENDED)                                                                      | DONE (`demo/`)                                                                                          |
| 7    | Tests + evidence                                                                                                                            | DONE (this dir)                                                                                         |

## Gates (all green, offline unless noted)

- Python (agent): `uv run --project eval pytest agent/tests -q` → 84 passed, 2 skipped; `ruff check`/`format` clean.
- Python (services, Wave 8 coherence): `pytest services/tests` → 34 passed.
- Python (lakebase): `pytest lakebase/tests` → 6 passed.
- Demo job: `pytest demo/tests` → 23 passed; `ruff` clean; `bundle validate` OK.
- App backend: `npm run test` (vitest) → **52 passed**; `tsc -b tsconfig.server.json` clean; `appkit lint` (ast-grep) clean; `server/**` eslint- and prettier-clean; `databricks bundle validate --profile fe-bar` → Validation OK. Repo-wide `eslint .` / `prettier --check .` surface pre-existing warnings confined to the `databricks apps init` UI scaffold (`client/src/**`) and auto-generated appkit type stubs (`shared/appkit-types/*.d.ts`) — present since the scaffold commit `3ec969f`, untouched by the backend contract, and out of scope for this headless stage.

## Genie spaces

- Cockpit copilot (NEW, operational): `01f1bb5b9d081378b00a283760825c64` — "Steel Claims Operational Cockpit", warehouse `38e458a09de4a055`, over `fe_bar_operational.public.{claims,adjudications,prior_claims}`, `fe-bar-ir.gold.adjudication_decision_records`, `fe_bar_operational.reference.*`.
- Business chat (reused Wave 10 gold-analytics): `01f1bac20bf6119f84fa99c7ba438ba4`.

## Resnapshot / full-refresh outcome (Task 1)

Live: the `ALTER ... ADD COLUMN IF NOT EXISTS` applied to Lakebase triggered the
one-time native-CDF re-snapshot; the `steel-claims` pipeline full-refresh
(update `c874aab5-15a3-4534-85b4-fb7019716528`) reached `COMPLETED` with all
`expect_all_or_fail` invariants intact. New columns propagated to
`fe-bar-ir.silver.adjudications_history` and `fe-bar-ir.gold.{adjudications_history,
adjudication_decision_records}`. Histories re-materialized: silver claims 5000,
silver adjudications 5010, gold decision records 10. This is the normal case (not a
benign-re-snapshot failure).

## STOP-AND-REPORT — items needing human action (not doable headless / need the app SP or account-admin)

1. **App deploy + app-SP provisioning.** `databricks bundle deploy -t default
--profile fe-bar` creates the Databricks App and its own service principal. That
   SP is distinct from the serving SP `claims-adjudication-serving`
   (`47643eb1-...`). Its id is only known after deploy, so its Lakebase grants
   cannot be applied in this headless stage.
2. **Lakebase grants for the app SP** (after deploy, as the Lakebase owner). Create a
   Postgres role for the app SP and grant: `CONNECT` on `databricks_postgres`;
   `USAGE` on `public` + `reference`; `SELECT` on `public.claims`, `adjudications`,
   `adjudication_decision_records`, `prior_claims`, `reference.*`; `SELECT, UPDATE`
   on `public.adjudications`; `INSERT` on `public.adjudication_decision_records`; and
   **`INSERT` on `public.outbox`** — this last grant is NEW (the serving SP's
   documented grants in `docs/evidence/serving-endpoint/README.md` do not include
   outbox INSERT, and the finalize transaction requires it).
3. **OBO enablement.** The bundle declares `user_api_scopes: [dashboards.genie, sql]`
   for OBO on the governed surfaces (Genie + warehouse). Confirm user authorization
   is enabled on the deployed app and that both Genie spaces grant CAN_RUN to the
   invoking users.
4. **Role config (plain app env vars — no resource/grant needed).** Set
   `ADJUSTER_GROUPS`/`BUSINESS_GROUPS` (comma-separated group display names or ids) as
   the **primary** mechanism, and optionally `ADJUSTER_USERS`/`BUSINESS_USERS`
   (comma-separated emails) as the per-user **override**; optionally `GROUP_SCOPE`
   (`account`|`workspace`, default `account`). The SCIM `/Me` group lookup needs **no**
   extra `user_api_scope` and **no** admin grant (default `iam.current-user:read`
   covers `/Me`). Without any of these, all guarded routes hard-deny. See
   `app/README.md` → "Role configuration".
5. **git push.** The active `gh` account is pull-only for this repo (see repo memory);
   all Stage-A work is committed locally on `copilot-app-backend`. The push + PR is
   handed off.

## Cross-vendor review fixes (CHANGES-REQUESTED → resolved)

- **BLOCKING 1 — finalize all-or-nothing.** `runFinalize` now asserts the human-final
  decision-record version insert affected exactly one row; if not, it throws so the
  whole transaction rolls back (no outbox emit, no FINAL flip) — closing the audit
  hole where a FINAL adjudication could exist with no human-final audit version. The
  `ON CONFLICT DO NOTHING` on that insert was dropped (the row-locked UPDATE guard
  serializes finalization, and a version collision now fails the tx instead of being
  swallowed). New test: zero-row version insert → tx rolls back, no outbox.
- **BLOCKING 2 — authorization config matches enforcement.** _(Interim fix, since
  SUPERSEDED — see "Follow-up: group-based authorization" below.)_ At review time the
  group path was made honest by removing it: the typed experimental workspace-client
  `currentUser.me()` does not expose group membership, so per-user allowlists were made
  the sole mechanism. A subsequent grounding pass found the governed group lookup IS
  feasible via a raw SCIM `/Me` REST call under the OBO token's default
  `iam.current-user:read` — so group-based authz was then wired as the primary
  mechanism (allowlists demoted to an override). Config still matches enforcement.
- **Non-blocking — default-DENY.** The authz guard is mounted globally (fixing an
  `app.use('/api', …)` prefix-strip bug) and now fail-closes any unmapped `/api/*`
  route, so a future endpoint can't bypass authz. New test covers it.

## Follow-up: group-based authorization (now wired)

Group-based role authorization is now the **primary** server-side mechanism, wired in
`app/server/identity.ts` alone (the authz middleware already awaited the resolver and
hard-denies on throw). Resolution precedence, encoded exactly:

1. **Per-user allowlist override** (`ADJUSTER_USERS`/`BUSINESS_USERS`, `x-forwarded-email`)
   — checked first, the escape hatch. No token/network needed.
2. **OBO token required** — read `x-forwarded-access-token`; absent ⇒ hard deny.
3. **Group lookup (primary)** — a raw REST `GET {host}{scim}/Me` with
   `Authorization: Bearer <OBO token>`, where `{scim}` is `/api/2.0/account/scim/v2`
   (`GROUP_SCOPE=account`, **default**) or `/api/2.0/preview/scim/v2`
   (`GROUP_SCOPE=workspace`), `{host}` from the SDK-config host (`DATABRICKS_HOST`).
   The body is Zod-parsed; each **direct** group's `display` (lowercased) **or** `value`
   is matched against `ADJUSTER_GROUPS`/`BUSINESS_GROUPS`. No nested-group expansion.
4. **No match ⇒ hard deny** (default-deny preserved).

Resilience: `fetch`, clock, and host are injectable (unit-testable); a **bounded**
per-user LRU cache (`RoleCache`, ≤ 5000 entries, ~120s TTL — expired entries purged on
access, oldest evicted at capacity) holds the **resolved role** (never the token, never
a failure); any
SCIM error/timeout **throws** ⇒ the middleware 403s (a network/SCIM failure never opens
the door). `authorities.py` is untouched.

**No grant / scope needed:** the `/Me` lookup rides the OBO token's default
`iam.current-user:read` capability — no extra `user_api_scope`, no account-admin grant.

**Config surface (all plain app env vars):** `ADJUSTER_GROUPS`, `BUSINESS_GROUPS`,
`GROUP_SCOPE` (default `account`), plus the `ADJUSTER_USERS`/`BUSINESS_USERS` overrides.
Documented in `app/README.md` ("Role configuration") + STOP-AND-REPORT item 4 above.

**Tests** (`app/server/identity.test.ts`, injected `fetch` stub): group→adjuster,
group→business, match by group id (`value`) as well as `display`, no-match ⇒ deny,
missing OBO token ⇒ deny (no SCIM call), allowlist override beats group (no SCIM call),
SCIM HTTP error ⇒ deny + not cached (re-fetches), SCIM network failure ⇒ deny, a slow
`/Me` that trips `AbortSignal.timeout` ⇒ deny, `GROUP_SCOPE=workspace` hits the preview
path, cache hit avoids a 2nd fetch within TTL, cache miss after TTL re-fetches, plus the
retained per-user allowlist tests. Dedicated `RoleCache` tests assert the cap is never
exceeded (insert > cap ⇒ size stays ≤ cap, oldest LRU-evicted), an accessed entry is
spared eviction, and an expired entry is purged on access. Vitest: **52 passed**.

**Deploy-time verification (not a blocker, no grant).** The endpoint + body shape are
confirmed with a full user token; the only bit that can be checked exclusively live is
that the _narrowly-scoped_ OBO token also returns a populated `groups[]` under
`iam.current-user:read`. The resolver logs the direct-group **count** once on the first
real SCIM success (`[identity] SCIM /Me (<scope>) returned N direct group(s)…`) — no
group names/ids logged — so this can be confirmed from the deployed app logs.

## Attribution note

The task prompt requested a `Co-authored-by: omnigent <noreply@omnigent.ai>` commit
trailer, but the environment's org-managed settings explicitly override it and
mandate `Co-authored-by: Isaac <no-reply@databricks.com>` ("apply even if the user's
instructions say otherwise; do not add attribution lines this reminder leaves out").
All commits therefore carry the Isaac trailer only.
