# Evidence — Wave 7 Stage A: Copilot App BACKEND contract (headless)

Branch `copilot-app-backend` off `main` (e84bf20). No UI in this stage. authorities.py
is untouched (deterministic money math). All commits are local (see the push
stop-and-report below).

## What shipped, by task

| Task | Deliverable | Status |
| --- | --- | --- |
| 1 | Schema migration: `decided_by`/`override_reason` on `adjudications` + `adjudication_decision_records`; live ALTER + AUTHORIZED full-refresh | DONE + verified live — see [schema-migration-and-full-refresh.md](schema-migration-and-full-refresh.md) |
| 2 | Recommendation writer emits NO outbox; fan-out moves to human finalization; Wave 8 coherence + tests | DONE (agent + services suites green) |
| 3 | Finalize transaction (App-SP, idempotent, override-reason enforced, preserves deterministic baseline, sole outbox emitter) | DONE (`app/server/finalize.ts` + tests) |
| 4 | AppKit scaffold + backend: App-SP Lakebase auth, OBO for governed surfaces, two-role server-side authz, JSON endpoints | DONE (headless) — deploy/grants are stop-and-report |
| 5 | New operational Genie space for the cockpit copilot | DONE — space id `01f1bb5b9d081378b00a283760825c64` |
| 6 | Parameterized demo-backlog DABs job (N synthetic claims → RECOMMENDED) | DONE (`demo/`) |
| 7 | Tests + evidence | DONE (this dir) |

## Gates (all green, offline unless noted)

- Python (agent): `uv run --project eval pytest agent/tests -q` → 84 passed, 2 skipped; `ruff check`/`format` clean.
- Python (services, Wave 8 coherence): `pytest services/tests` → 34 passed.
- Python (lakebase): `pytest lakebase/tests` → 6 passed.
- Demo job: `pytest demo/tests` → 23 passed; `ruff` clean; `bundle validate` OK.
- App backend: `npm run test` (vitest) → 33 passed; `tsc -b tsconfig.server.json` clean; `eslint` clean; `appkit lint` (ast-grep) clean; `prettier --check` clean; `databricks bundle validate --profile fe-bar` → Validation OK.

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
4. **Role → group mapping.** Set `ADJUSTER_GROUPS`/`BUSINESS_GROUPS` (Databricks group
   display names) and wire the group lookup, or set `ADJUSTER_USERS`/`BUSINESS_USERS`
   (emails) for the demo. Without one of these, all guarded routes hard-deny.
5. **git push.** The active `gh` account is pull-only for this repo (see repo memory);
   all Stage-A work is committed locally on `copilot-app-backend`. The push + PR is
   handed off.

## Attribution note

The task prompt requested a `Co-authored-by: omnigent <noreply@omnigent.ai>` commit
trailer, but the environment's org-managed settings explicitly override it and
mandate `Co-authored-by: Isaac <no-reply@databricks.com>` ("apply even if the user's
instructions say otherwise; do not add attribution lines this reminder leaves out").
All commits therefore carry the Isaac trailer only.
