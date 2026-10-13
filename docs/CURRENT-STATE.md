# Current state and verification

The single source of truth for *what exists now* and *what has been verified*. It
draws the line between what is implemented in code, what is proven by committed
(synthetic) evidence, what is proven by live capture in the current workspace, and
which gaps that capture recorded. Read this before trusting any claim in a layer README or an
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
- **Genie:** both spaces answer live Conversation API questions with generated SQL
  and result rows (`genie.json`).
- **AI/BI dashboard:** deployed and published; all 9 dataset queries succeed on the
  warehouse (`dashboard.json`).
- **App:** `steel-claims-cockpit` running; authenticated `/api/whoami`, `/api/queue`,
  cockpit detail (decision record + citations), `/api/history` all return 200 (`app.json`).
- **Serving:** the agent endpoint is `READY` on v6 (`@prod`, 100% traffic). A
  `persist=false` invocation returns a verdict and amount that match the
  deterministic authority, with no invariant violations (`serving.json`).
- **Lakebase + Lakeflow:** live OLTP counts, 7 synced tables online, pipeline update
  history, cdf/silver/gold counts (`lakebase-and-pipeline.json`).
- **End to end** for `CLM-DEMO-000006` (`e2e-trace.json`): the claim's human
  finalize produced a FINAL adjudication, decision record v2, and outbox row. The
  relay then set `published_at`, and the settlement consumer wrote
  `STL-CLM-DEMO-000006`. Native CDF captured all of these rows. The app's history
  and cockpit return the claim.

### Live gaps recorded by that capture
- **Silver/gold lag:** the `steel-claims` pipeline last completed on 2026-10-07,
  before the demo claims were finalized. Those claims are in `cdf.lb_*` but not
  yet in silver/gold, so the operational Genie cannot see them yet. For the same
  reason `gold.adjudication_decision_records` and the citation KPIs are empty.
  The next pipeline update brings them in.
- **Reasoning endpoint label:** all persisted decision records (latest one
  2026-10-09) record `system.ai.gpt-5-4`. The source targets the
  `fe-bar-ir.adjudication-agent.*` Unity Gateway services. The `persist=false`
  response does not expose which endpoint the served v6 called.

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
- **Committed evidence is point-in-time synthetic capture**, tied to the commit noted
  in each set — not continuous production monitoring.

## How to re-verify

Run the offline evaluation (`eval/`, `uv run`) for the deterministic-vs-agent and
money-safety scorers on committed synthetic data — no deployment required. For a live
bring-up, follow `RUNBOOK.md` (core data bootstrap, then the manual provisioning
steps), supply the Aiven secrets, and the services + app complete the event loop.
