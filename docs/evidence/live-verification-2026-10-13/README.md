# Evidence — live verification, 2026-10-13

> **Synthetic demo evidence**, captured live against the `fe-bar-ir-2026` workspace
> (catalog `fe-bar-ir`) on 2026-10-13 UTC — not production data or continuous
> monitoring. See [`../../CURRENT-STATE.md`](../../CURRENT-STATE.md).

Every file is raw captured output. Each `steps[]` entry records the exact command
(CLI call, SQL statement, Lakebase `SELECT`, or HTTP request), a UTC timestamp, the
exit code / HTTP status, and the output. Large payloads are trimmed (marked
`trimmed`); personal email addresses are redacted as `<user>`; no tokens are
recorded. All captures are read-only. The one serving call used
`custom_inputs.persist=false`, so it wrote nothing to Lakebase.

`genie.json`, `dashboard.json`, `lakebase-and-pipeline.json` and `e2e-trace.json`
were captured after the incremental routine refresh in `routine-refresh.log`.
`app.json` and `serving.json` were captured earlier the same day.

| Stage | File | What it proves |
| --- | --- | --- |
| Lakeflow | [`routine-refresh.log`](routine-refresh.log) | `scripts/refresh.py routine` completed with no full refresh, across one run plus a manual resumption: the local runner was interrupted during `prior_claims_corpus` (whose job run had already succeeded), and the final two steps were resumed by hand (marked `(resumed)` in the log). Two medallion updates completed (`f0857233…`, `002f10ae…`, both `full_refresh: false`), along with `fraud_graph` (5,100 claims scored), `prior_claims_corpus`, and the re-sync of the two synced tables. |
| Genie | [`genie.json`](genie.json) | Both Genie spaces exist: operational `01f1c269ca3c1adea7feb9f248ab3445` and analytics `01f1c2698a5418298f81f9e79df576ca`. Six live Conversation API questions (three per space) all reached `COMPLETED`, each with its generated SQL and result rows. One of them returns `CLM-DEMO-000006` as FINAL / APPROVE / CREDIT / 8360.00. |
| Dashboard | [`dashboard.json`](dashboard.json) | AI/BI dashboard `01f1c269ddc4143aa9bf584f267bd10b` is `ACTIVE` and published. All 9 dataset queries returned `SUCCEEDED` on warehouse `a323b5700ae25d85`, including `citations` (101 decision records). |
| App | [`app.json`](app.json) | App `steel-claims-cockpit` is `RUNNING`; its active deployment `SUCCEEDED`. Authenticated HTTP calls all returned 200: `/api/whoami` (roles), `/api/queue` (a default page of 50; Lakebase holds 98 `RECOMMENDED`), the cockpit detail for a queued claim (decision record, 3 citations, conformance/coverage/settlement, context, 5 prior claims), and `/api/history`. |
| ML / GenAI serving | [`serving.json`](serving.json) | Endpoint `agents_fe-bar-ir-default-claims_adjudication_agent` is `READY`, with 100% of traffic on v6 (the `@prod` alias). One live invocation returned APPROVE / CREDIT with $9,113.59 approved, equal to the deterministic authority. It reported `invariant_violations: []`, three cited clauses, `llm_used: true`, and `write_result.persisted: false`. |
| Lakebase + Lakeflow | [`lakebase-and-pipeline.json`](lakebase-and-pipeline.json) | Live row counts for the Lakebase `public` and `reference` tables. All 7 synced tables are `SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE`; `customer_heat_risk` and `prior_claims_corpus` were synced on 2026-10-13. Also records the latest `steel-claims` update (`002f10ae…`, `COMPLETED`) and row counts for UC `cdf`, `silver`, and `gold`. |
| End to end | [`e2e-trace.json`](e2e-trace.json) | Follows `CLM-DEMO-000006` through every stage: claim → FINAL adjudication + 2 decision records → outbox event `published_at` → settlement `STL-CLM-DEMO-000006` ($8,360.00, `PENDING_PAYMENT`) → native CDF `cdf.lb_*_history` → `silver.adjudications_history` (RECOMMENDED → FINAL SCD2 versions) → gold `claims_current` / `adjudications_current` / `gold_claim_adjudication_fact` → operational Genie (1 row, FINAL / APPROVE / 8360.00) → app history + cockpit. |

## Known limitations recorded by these captures

- **Reasoning endpoint label.** The live `@prod` v6 records `reasoning_endpoint` as
  `system.ai.gpt-5-4` on all 104 persisted decision records, which is the label from
  before the label fix. The fix is merged in `main` (PR #58) and goes live on the
  next register → evaluate → promote → deploy cycle. Per source, the call path
  targets the Unity Gateway model service
  `fe-bar-ir.adjudication-agent.adjudication-reasoning` (`agent/src/gateway_chat.py`).
- **Settlements have no silver/gold table.** The medallion does not model
  settlements, so the settlement row shows up in Lakebase and in
  `cdf.lb_settlements_history` only.
- The app's claims-history route is `/api/history`. It filters by `claim_type`,
  `customer_id`, and `verdict`, not by `claim_id`, so the trace filters the
  returned page on the client side.
