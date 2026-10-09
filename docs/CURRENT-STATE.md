# Current state and verification

The single source of truth for *what exists now* and *what has been verified*. It
draws the line between what is implemented in code, what is proven by committed
(synthetic) evidence, and what is code-complete but not live-re-verified in the
current workspace. Read this before trusting any claim in a layer README or an
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

### Proven in code and unit/integration tests (not a committed live capture)
- The finalize transaction (FINAL adjudication + decision record + outbox row,
  scoped to `claim_id` + `adjudication_id`), server-side authorization, and the
  consumer dedup/skip logic.
- The producer→worker Kafka path has been exercised against the live Aiven broker.

### Code-complete but NOT live-re-verified in the current workspace
These are implemented and consistent in source, but this submission does **not**
include a fresh live capture for them in `fe-bar-ir-2026`:
- The reasoning/embedding **model-service switch** (the agent code targets the
  `fe-bar-ir.adjudication-agent.*` Unity Gateway services). Committed deployment
  evidence from the retired workspace / earlier reasoning models was removed rather
  than re-captured; treat the live endpoint's exact served version as unverified here.
- The AI/BI dashboard and Genie-space deployments (the definitions are in
  `dashboards/`; the specific deployed IDs are workspace-specific and parameterized).
- The full Kafka fan-out (relay → the four downstream consumer stubs) end to end,
  which additionally requires user-provided Aiven secrets and a human-finalized claim.

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
