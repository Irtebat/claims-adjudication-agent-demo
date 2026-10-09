# Steel Quality and Warranty Claims Adjudication Agent

A reference implementation of a **human-in-the-loop** system that adjudicates steel
quality and warranty claims for a coated-flat-steel producer, built on Databricks.
For each claim, deterministic authorities decide the outcome and the money, a
language model reads the supporting documents and drafts a cited rationale, and a
**human adjuster reviews and finalizes** the decision — APPROVE, DENY, or
PEND-INVESTIGATE, with a disposition, an approved amount, and cited clauses.

> **This is a proof of concept on synthetic data.** It demonstrates the end-to-end
> *pattern*; it is **not** a production system, **not** an integration with any real
> customer's systems, and **not** a conclusive final architecture. All data is
> synthetic — no real claims or customer data are used. Dollar figures and counts in
> the docs and `docs/evidence/` are results of synthetic demo runs, not production
> outcomes.

## Design principle

Decisions that move money are made by **deterministic, auditable code** —
conformance against the Mill Test Certificate (MTC), warranty coverage, settlement
math, and duplicate detection. The **language model only reads documents, retrieves
and cites supporting clauses, and drafts a recommendation**; it cannot change a
verdict or an amount. A code-level invariant layer overwrites any model output that
deviates from the deterministic authority, and a model version cannot be promoted
unless it passes money-safety checks. A human adjuster makes the final call.

## Architecture

The implementation connects these Databricks capabilities as one flow:

| Capability | Role |
| --- | --- |
| Lakeflow Spark Declarative Pipelines | Generate synthetic data; medallion bronze→silver→gold; native CDF → SCD2 history |
| Unity Catalog | Governs data, grants, masks, lineage; hosts the reasoning + embedding model services via Unity Gateway |
| Lakebase (Postgres, OLTP) | Store of record for live claims and the adjuster app; policy tables + synced reference tables |
| In-process deterministic authorities | Conformance (MTC vs spec), coverage, settlement, duplicate — decide the verdict and the money (plain Python, **not** an LLM) |
| Reasoning model service (Unity Gateway) | Reads documents, retrieves + cites clauses, drafts the recommendation — **advisory only** |
| Mosaic AI `ResponsesAgent` | Orchestrates the above into one recommendation, then the invariant layer enforces money-safety |
| MLflow | Offline evaluation + a human-gated `register → evaluate → promote` lifecycle |
| AI/BI dashboards + Genie (two spaces) | Live COPQ / leakage analytics + natural-language Q&A (operational + gold) |
| Databricks App | Human adjuster review-and-finalize UI |

A Kafka event backbone (Aiven; topics `claim.submitted` and `claim.adjudicated`)
links the operational store to downstream services: on human finalization an outbox
row is written, an outbox relay publishes it, and downstream consumer stubs
(settlement, notification, investigation, supplier-recovery) react.

## Repository layout

| Path | Contents |
| --- | --- |
| config/ | Environment config templates (placeholders only, no secrets) |
| pipelines/ | Synthetic data generation and Lakeflow pipelines (medallion + CDF history) |
| lakebase/ | Postgres schema, extensions, policy intake, and synced-table definitions |
| agent/ | Mosaic AI agent, its deterministic authorities, retrieval, and tools |
| eval/ | MLflow evaluation harness, scorers, and the promotion gate |
| services/ | Kafka producer, worker, outbox relay, and downstream consumers |
| app/ | Databricks App (adjuster UI) |
| dashboards/ | AI/BI dashboards and Genie definitions |
| demo/ | Demo-backlog job that generates fresh claims and populates the queue |
| scripts/ | `bootstrap.py` (end-to-end bring-up) and `refresh.py` (routine/demo refresh) |
| docs/ | Documentation, the current-state/verification page, and committed execution evidence |

## Environment and naming

Two similarly-named things are intentionally different — do not conflate them:

| Thing | Value |
| --- | --- |
| Unity Catalog (data + model services) | `fe-bar-ir` |
| CLI profile / workspace | `fe-bar-ir-2026` (always pass `--profile fe-bar-ir-2026`) |
| Workspace host | `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com` |
| Lakebase project | `fe-bar-operational-plane` |
| Reasoning model service | `fe-bar-ir.adjudication-agent.adjudication-reasoning` |
| Embedding model service | `fe-bar-ir.adjudication-agent.embedding` |
| Registered agent model | `fe-bar-ir.default.claims_adjudication_agent` |

`fe-bar-ir` is the catalog / model-service prefix; `fe-bar-ir-2026` is the
workspace/profile. Secrets live in Databricks secret scopes, referenced by name —
never in source.

## Prerequisites

- Databricks CLI ≥ 1.17 and a workspace on Unity Catalog with create rights.
- A Lakebase-capable workspace and a serverless SQL warehouse.
- Python 3.12 + [`uv`](https://docs.astral.sh/uv/); Node 18+ (for the AppKit app).
- An Aiven Kafka service (for the event backbone) and its SASL credentials.
- Account-admin rights to create the service principals, OAuth secret, and secret
  scopes the agent/app/services use (see the layer READMEs and `docs/RUNBOOK.md`).

## Quick start

Three entry points, smallest first. See `docs/RUNBOOK.md` for the full command list,
trigger table, and run orders.

1. **Evaluate locally (no deployment).** Run the offline evaluation harness against
   committed synthetic data to see the deterministic-vs-agent comparison and the
   money-safety scorers — `eval/` (`uv run`). Scope: read-only analysis.
2. **Deploy the core data layer.** `scripts/bootstrap.py` brings up the medallion +
   CDF history + Lakebase seed + policy intake. Scope: the data and OLTP foundation.
3. **Full environment.** After the core bootstrap, the manual steps in
   `docs/RUNBOOK.md` provision the service principals, serving endpoint, synced
   tables, app, dashboards, Genie spaces, and services. Scope: the complete demo.

## Execution model

Every layer uses the **same two execution mechanisms — and only these two**:

1. **DABs bundle — `databricks bundle deploy` + `databricks bundle run <job>` — for
   anything that runs on Databricks compute** (jobs, pipelines, apps). This is the
   canonical mechanism; always pass `--target prod --profile fe-bar-ir-2026`. A plain
   `bundle` operation is invoked directly, never wrapped in a script.
2. **Direct `uv run python` only for two cases:**
   - **(a) the human-gated MLflow model lifecycle** — `agent`'s
     `register → evaluate → promote`, an interactive operator loop with a human
     decision at promotion, not a scheduled job; and
   - **(b) thin `uv run python` wrappers that add orchestration around `bundle run`** —
     sourcing the authored warranty schedule into the generator, the Lakebase
     reseed / CDF-exists guards, and resolving the dynamic native-CDF table names
     before a medallion run.

Each layer README states this rule and lists the exact commands for that layer.
`scripts/bootstrap.py` composes the end-to-end path from these mechanisms directly,
and `scripts/refresh.py routine|demo` composes the refreshes (see `scripts/README.md`).

## Current state and verification

See **`docs/CURRENT-STATE.md`** for what is implemented, what is verified by committed
(synthetic) evidence, and what is code-complete but not live-verified in the current
workspace. In short: the system is implemented end-to-end **in code**; committed
evidence is point-in-time synthetic-demo capture, not continuous production
monitoring.

## Known limitations

- **Synthetic data only** — a POC, not production or customer data.
- **Live Kafka broker end-to-end requires user-provided Aiven secrets.** Without them
  the services jobs cannot run against the broker; the flow is proven in code and
  unit/integration tests, and the producer→worker path has been exercised against a
  live broker (see `docs/CURRENT-STATE.md`).
- **Committed deployment/eval evidence is a point-in-time synthetic capture**, tied to
  the commit noted in each evidence set — not a live production guarantee.

## Development

Changes are proposed as pull requests and reviewed before merge; see
`docs/RUNBOOK.md` for how the pieces are built, run, and verified.
