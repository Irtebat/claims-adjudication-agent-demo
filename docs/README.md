# docs

Project documentation and committed execution evidence.

This repository is a reference implementation of a steel claims-adjudication agent.
Claims are born in a Lakebase (Postgres) operational plane; governed reference and
policy data moves between Lakebase and Unity Catalog; deterministic tools decide
money while retrieval finds citable evidence; the claim/adjudication history is
maintained incrementally in the lakehouse via native Change Data Feed (CDF); and
the agent is registered and deployed as a governed serving endpoint with an
offline evaluation gate over its traces.

**Operating the system:** see [`RUNBOOK.md`](RUNBOOK.md) for every pipeline, job,
app, dashboard, and synced table with its trigger, and for the run orders (fresh
bootstrap, routine refresh, demo refresh, schema change, event backbone) and when a
full refresh is required.

## Architecture

```mermaid
flowchart TB
  subgraph OLTP["Operational plane — Lakebase Postgres"]
    claims[("public.claims / adjudications")]
    policy[("policy tables")]
    ref[("reference.* (synced from UC,<br/>incl. prior_claims_corpus)")]
  end
  subgraph LH["Lakehouse — Unity Catalog: fe-bar-ir"]
    refsrc["bronze / silver reference"]
    cdfland["cdf.lb_*_history"]
    silverh["silver.*_history (SCD2)"]
    gold["gold current + history + KPI views"]
  end
  agent["agent tools:<br/>resolution, authorities,<br/>duplicate, retrieval"]
  endpoint["governed serving endpoint<br/>(Unity Gateway, scale-to-zero)"]

  refsrc -- "serve-down" --> ref
  claims -- "native CDF" --> cdfland --> silverh --> gold
  policy --> agent
  ref --> agent
  claims --> agent
  agent --> endpoint
  gold --> dash["AI/BI dashboards + Genie"]
  gold --> evalx["eval: MLflow gate + promotion"]
  agent --> app["live app:<br/>adjuster + business UI"]
  endpoint -. "built, not deployed" .-> svc["services: Kafka worker,<br/>outbox relay, consumers"]
```

## Layers

| Directory | What it does | Status |
| --- | --- | --- |
| `pipelines/` | Synthetic data, medallion pipeline, CDF SCD2 history, governance | Built |
| `lakebase/` | Operational Postgres plane, serve-down, native policy tables, CDF | Built |
| `agent/` | Policy intake, deterministic authorities, retrieval, duplicate, fraud graph; the orchestrating ResponsesAgent, registered and deployed as a governed serving endpoint | Built |
| `eval/` | MLflow evaluation harness + versioned candidate→prod promotion gate | Built |
| `dashboards/` | AI/BI dashboard + Genie space over gold KPIs | Built |
| `app/` | Adjuster and business UI (Databricks App) | Live |
| `services/` | Kafka event backbone, adjudication worker, outbox relay, downstream consumers | Built, not deployed |

## Design decisions

Intentional deviations from the initial plan, kept here so the repository reads accurately:

- **Deterministic authorities run in-process, not as UC Python functions.** The money math lives in a single source (`agent/src/authorities.py`), exercised by tests and imported directly by the agent runtime. UC function registration was removed to eliminate deployed-vs-tested drift and to keep the adjudication path off a SQL warehouse (sub-millisecond, no cold start).
- **Serve-down set: six reference tables plus the precedent corpus.** `spec_standards` and `coating_warranty_terms` were dropped from serve-down — policy data now lives in native Lakebase tables (`spec_params`, `spec_clauses`, `warranty_terms`, `warranty_clauses`) that the agent reads directly — and `customer_heat_risk` was added for the agent's advisory heat-risk tool.
- **The prior-claims precedent corpus is lakehouse-built and served down.** The agent `prior_claims_corpus` job builds `gold.prior_claims_corpus` from gold current claims and FINAL adjudications (governed GTE embeddings, change-only MERGE with Delta CDF); it is served down as the Triggered synced table `reference.prior_claims_corpus` with `lakebase_ann` / `lakebase_bm25` indexes, and refreshed by `scripts/refresh.py routine`. It replaced the native `public.prior_claims` table and its embed-once backfill.
- **`claims_pending` removed.** The retry-queue placeholder had no consumer; it is retired with the event-backbone (services) work.

## Evidence

Committed execution evidence lives under `docs/evidence/`, one folder per
workstream, each captured live against the `fe-bar` profile. Every folder has a
README indexing its files and what they demonstrate.

| Folder | Status | Covers |
| --- | --- | --- |
| `current-state/` | current-live | Read-only 2026-10-01 inventory, counts, resources, and pending evidence |
| `synthetic-data/` | historical-live | Dataset generation, governance grants/masks, integrity checks, samples |
| `lakebase-serve-down/` | historical-live | Lakebase provisioning and Triggered serve-down parity |
| `policy-intake-and-retrieval/` | historical-live | Policy intake, resolution, in-process authorities, retrieval |
| `cdf-incremental-history/` | historical-live | Native CDF SCD Type 2 cutover and incremental proof |
| `correctness-debt-fixes/` | historical-live | Fraud-graph heat resolution, transactional policy-intake upsert, `heats_coils` dedup |
| `pipelines-lakebase-cleanup/` | instructions | Resource rename, `run.py` separation, policy files relocated to `lakebase/` |
| `claims-adjudication-agent/` | historical-live | ResponsesAgent build, money-safety invariant enforcement, decision records |
| `mlflow-lifecycle-hygiene/` | historical-live | Independent eval runs, single `@prod` owner, packaged-artifact evaluation |
| `gold-analytics/` | historical-live | Gold KPI fact layer + governed metric view |
| `serving-endpoint/` | superseded | Historical v1 endpoint smoke; current-corpus smoke awaits promotion |
| `genie-dashboards/` | historical-live | AI/BI dashboard + Genie space over gold KPIs |
| `refresh-and-prior-claims/` | current-live | Live corpus, indexes, grants, refresh composer, and bootstrap ordering |
| `app-deploy/` | historical-live | App deployment runbook and grants |
| `copilot-app-backend/` | superseded | Headless backend replaced by the live UI |
| `execution-consistency/` | offline-only | Repository execution checks |
| `kafka-events/` | offline-only | Kafka simulation; live end-to-end is pending |

Pending live evidence is listed in [`current-state/pending.md`](evidence/current-state/pending.md).
