# Current state and verification

The single source of truth for *what exists now* and *what has been verified*. It
draws the line between what is implemented in code, what is proven by committed
(synthetic) evidence, what is proven by live capture in the current workspace, and
the remaining known limitations. Read this before trusting any claim in a layer README or an
evidence folder.

> **This is a proof of concept on synthetic data.** Not a production system, not an
> integration with any real customer's systems, and not a conclusive final
> architecture. Every number is a synthetic-demo result.

## Repository state

- Branch: `main`, plus the submission-readiness source and docs changes.
- All nine layers (`pipelines`, `lakebase`, `agent`, `eval`, `services`, `app`,
  `dashboards`, `demo`, `scripts`) are **implemented (code-complete)**.

## Environment and naming

| Thing | Value |
| --- | --- |
| Unity Catalog (data + model services prefix) | `fe-bar-ir` |
| CLI profile / workspace | `fe-bar-ir-2026` |
| Workspace host | `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com` |
| Lakebase project | `fe-bar-operational-plane` |
| Reasoning model service (Unity Gateway) | `fe-bar-ir.adjudication-agent.adjudication-reasoning` |
| Embedding model service (Unity Gateway) | `fe-bar-ir.adjudication-agent.embedding` |
| Registered agent model | `fe-bar-ir.default.claims_adjudication_agent` |

`fe-bar-ir` is the catalog / model-service prefix; `fe-bar-ir-2026` is the
workspace/profile. A **retired** workspace `fe-bar-ir` (host
`fe-sandbox-fe-bar-ir.cloud.databricks.com`) was used in earlier iterations; it no
longer exists, and its deployment-event evidence has been removed from this repo.

## Verification tiers

### Proven by committed synthetic evidence (`docs/evidence/`)
Point-in-time captures from synthetic demo runs — see each folder's README for the
commit and commands:
- Synthetic data generation, governance grants/masks, integrity checks (`synthetic-data/`).
- Lakebase provisioning + Triggered serve-down parity (`lakebase-serve-down/`).
- Policy intake, resolution, **in-process deterministic authorities**, retrieval
  (`policy-intake-and-retrieval/`).
- Native CDF → SCD Type 2 history, incremental proof (`cdf-incremental-history/`).
- ResponsesAgent build, **money-safety invariant enforcement**, decision records
  (`claims-adjudication-agent/`).
- Gold KPI fact layer + governed metric view (`gold-analytics/`).
- **Deterministic-rules vs. agent ablation** — the measured evidence for where
  reasoning adds value over rules (`ablation-eval/`).

### Proven by live capture in `fe-bar-ir-2026` (`docs/evidence/live-verification-2026-10-13/`)
Read-only captures from 2026-10-13 (see that folder's README):
- **Lakeflow:** the incremental routine refresh (`scripts/refresh.py routine`, no full
  refresh) completed across one run plus a manual resumption of its final two steps
  after the local runner was interrupted. Medallion updates `f0857233…` and `002f10ae…` both reached
  `COMPLETED` (`routine-refresh.log`).
- **Genie:** both spaces answer live Conversation API questions with generated SQL
  and result rows. The operational space returns `CLM-DEMO-000006` as FINAL /
  APPROVE / 8360.00 (`genie.json`).
- **AI/BI dashboard:** deployed and published; all 9 dataset queries succeed on the
  warehouse, including citation coverage (`dashboard.json`).
- **App:** `steel-claims-cockpit` running; authenticated `/api/whoami`, `/api/queue`,
  cockpit detail (decision record + citations), `/api/history` all return 200 (`app.json`).
- **Serving:** the agent endpoint is `READY` on v6 (`@prod`, 100% traffic). A
  `persist=false` invocation returns a verdict and amount that match the
  deterministic authority, with no invariant violations (`serving.json`).
- **Lakebase:** live OLTP counts and 7 synced tables online. Counts for
  cdf/silver/gold are in step with Lakebase: 5,100 claims, and 5,101 current
  adjudications (5,003 FINAL + 98 RECOMMENDED) (`lakebase-and-pipeline.json`).
- **End to end** for `CLM-DEMO-000006` (`e2e-trace.json`): the claim's human
  finalize produced a FINAL adjudication, decision record v2, and outbox row. The
  relay set `published_at`, and the settlement consumer wrote `STL-CLM-DEMO-000006`.
  Native CDF captured all of these rows. The claim then appears as RECOMMENDED →
  FINAL SCD2 versions in silver and as FINAL in the gold current views and fact.
  The operational Genie and the app's history and cockpit all return it.

### Proven in code and unit/integration tests (not a committed live capture)
- Server-side authorization and the consumer dedup/skip logic.
- The investigation and supplier-recovery consumers: both of their Lakebase tables
  have 0 rows, because no finalized claim has routed to either one yet.
- The producer→worker Kafka path has been exercised against the live Aiven broker.

## Known limitations

- **Synthetic data only.** No production or customer data.
- **Live Kafka broker end-to-end needs user-provided Aiven secrets** in scope
  `fe-bar-aiven-kafka`; without them the services jobs cannot reach the broker.
- **Supplier recovery:** supplier-attributable claims derive a `recovery_supplier_id`
  so the recovery consumer creates a case (see the supplier-recovery change in the
  source). The downstream consumers are **stubs** — they record/log the action; they
  do not integrate with a real settlement or ERP system.
- **Reasoning-endpoint label:** the live `@prod` v6 records `reasoning_endpoint` as
  `system.ai.gpt-5-4` on its decision records, which is the label from before the
  label fix. The fix is merged in `main` (PR #58) and goes live on the next
  register → evaluate → promote → deploy cycle. Per source, the call path targets the
  Unity Gateway model service `fe-bar-ir.adjudication-agent.adjudication-reasoning`.
- **Settlements stop at CDF:** the medallion has no settlement table, so a
  settlement is visible in Lakebase and `cdf.lb_settlements_history` only.
- **Committed evidence is point-in-time synthetic capture**, tied to the commit noted
  in each set — not continuous production monitoring.

## How to re-verify

Run the offline evaluation (`eval/`, `uv run`) for the deterministic-vs-agent and
money-safety scorers on committed synthetic data — no deployment required. For a live
bring-up, follow `RUNBOOK.md` (core data bootstrap, then the manual provisioning
steps), supply the Aiven secrets, and the services + app complete the event loop.
