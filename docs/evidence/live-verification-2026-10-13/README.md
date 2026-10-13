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

| Stage | File | What it proves |
| --- | --- | --- |
| Genie | [`genie.json`](genie.json) | Both Genie spaces exist: operational `01f1c269ca3c1adea7feb9f248ab3445` and analytics `01f1c2698a5418298f81f9e79df576ca`. Six live Conversation API questions (three per space) all reached `COMPLETED`, and each one records the question, the SQL Genie generated, and the result rows. |
| Dashboard | [`dashboard.json`](dashboard.json) | AI/BI dashboard `01f1c269ddc4143aa9bf584f267bd10b` is `ACTIVE` and published. All 9 dataset queries were run on warehouse `a323b5700ae25d85`, and all returned `SUCCEEDED` with row counts and sample rows. |
| App | [`app.json`](app.json) | App `steel-claims-cockpit` is `RUNNING`; its active deployment `SUCCEEDED`. Authenticated HTTP calls all returned 200: `/api/whoami` (roles), `/api/queue` (a default page of 50; Lakebase holds 98 `RECOMMENDED`), the cockpit detail for a queued claim (decision record, 3 citations, conformance/coverage/settlement, context, 5 prior claims), and `/api/history`. |
| ML / GenAI serving | [`serving.json`](serving.json) | Endpoint `agents_fe-bar-ir-default-claims_adjudication_agent` is `READY`, with 100% of traffic on v6 (the `@prod` alias). One live invocation returned APPROVE / CREDIT with $9,113.59 approved, equal to the deterministic authority. It reported `invariant_violations: []`, three cited clauses, `llm_used: true`, and `write_result.persisted: false`. |
| Lakebase + Lakeflow | [`lakebase-and-pipeline.json`](lakebase-and-pipeline.json) | Live row counts for the Lakebase `public` and `reference` tables. All 7 synced tables are `SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE`. Also records the `steel-claims` pipeline's latest updates and row counts for UC `cdf`, `silver`, and `gold`. |
| End to end | [`e2e-trace.json`](e2e-trace.json) | Follows `CLM-DEMO-000006` through every stage: claim → FINAL adjudication + 2 decision records → outbox event `published_at` → settlement `STL-CLM-DEMO-000006` ($8,360.00, `PENDING_PAYMENT`) → native CDF `cdf.lb_*_history` → silver/gold (not yet present, see below) → operational Genie (0 rows, see below) → app history + cockpit. |

## Observations recorded by these captures

- **Silver/gold lag behind Lakebase.** The `steel-claims` pipeline last completed
  an update on 2026-10-07 at 11:27 UTC. The three finalized demo claims (`CLM-DEMO-000005`/`6`/`7`)
  were finalized after that, on 2026-10-08 and 2026-10-09. Native CDF already holds them:
  `cdf.lb_claims_history` has 5,100 rows and `cdf.lb_adjudications_history` has
  5,107. The silver/gold histories still hold the 5,000 baseline rows.
  As a result:
  - `CLM-DEMO-000006` is not yet in silver/gold.
  - The operational Genie returns 0 rows when asked about it by its exact ID.
  - `gold.adjudication_decision_records` and `gold_retrieval_citation_kpis` are
    empty, so the dashboard's `citations` dataset returns nulls.
  - In `genie.json`, the operational Genie matched the first question with
    `LIKE 'CLM%000006'` and answered for baseline claim `CLM-0000006`.

  The next pipeline update will bring these rows into silver/gold. That update
  was not triggered here, because these captures are read-only.
- **Reasoning endpoint label.** The `persist=false` response does not expose
  `reasoning_endpoint`. All 104 persisted decision records (latest one
  2026-10-09) record `system.ai.gpt-5-4`. Current source labels the endpoint
  `fe-bar-ir.adjudication-agent.adjudication-reasoning` (`agent/src/gateway_chat.py`).
  These captures do not show which reasoning endpoint the served v6 used.
- The app's claims-history route is `/api/history`. It filters by `claim_type`,
  `customer_id`, and `verdict`, not by `claim_id`, so the trace filters the
  returned page on the client side.
