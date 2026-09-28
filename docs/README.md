# docs

Project documentation and committed execution evidence.

This repository is a reference implementation of a steel claims-adjudication agent.
Claims are born in a Lakebase (Postgres) operational plane; governed reference and
policy data moves between Lakebase and Unity Catalog; deterministic tools decide
money while retrieval finds citable evidence; the claim/adjudication history is
maintained incrementally in the lakehouse via native Change Data Feed (CDF); and
the agent is registered and deployed as a governed serving endpoint with an
offline evaluation gate over its traces.

## Architecture

```mermaid
flowchart TB
  subgraph OLTP["Operational plane — Lakebase Postgres"]
    claims[("public.claims / adjudications")]
    policy[("policy tables + prior_claims")]
    ref[("reference.* (synced from UC)")]
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
  agent -. planned .-> app["app: adjuster UI"]
  endpoint -. planned .-> svc["services: Kafka worker,<br/>outbox relay, consumers"]
```

## Layers

| Directory | What it does | Status |
| --- | --- | --- |
| `pipelines/` | Synthetic data, medallion pipeline, CDF SCD2 history, governance | Built |
| `lakebase/` | Operational Postgres plane, serve-down, native policy tables, CDF | Built |
| `agent/` | Policy intake, deterministic authorities, retrieval, duplicate, fraud graph; the orchestrating ResponsesAgent, registered and deployed as a governed serving endpoint | Built |
| `eval/` | MLflow evaluation harness + versioned candidate→prod promotion gate | Built |
| `dashboards/` | AI/BI dashboard + Genie space over gold KPIs | Built |
| `app/` | Adjuster review UI (Databricks App) | Planned |
| `services/` | Kafka event backbone, adjudication worker, outbox relay, downstream consumers | Planned |

## Design decisions

Intentional deviations from the initial plan, kept here so the repository reads accurately:

- **Deterministic authorities run in-process, not as UC Python functions.** The money math lives in a single source (`agent/src/authorities.py`), exercised by tests and imported directly by the agent runtime. UC function registration was removed to eliminate deployed-vs-tested drift and to keep the adjudication path off a SQL warehouse (sub-millisecond, no cold start).
- **Serve-down set is six reference tables, not seven.** `spec_standards` and `coating_warranty_terms` were dropped from serve-down — policy data now lives in native Lakebase tables (`spec_params`, `spec_clauses`, `warranty_terms`, `warranty_clauses`) that the agent reads directly — and `customer_heat_risk` was added for the agent's advisory heat-risk tool.
- **`prior_claims` is a native Lakebase table.** Correct for the seeded demo (embed-once backfill). The production pattern is a lakehouse-built precedent corpus served down to Lakebase, adopted once continuous CDF-driven refresh is wired.
- **`claims_pending` removed.** The retry-queue placeholder had no consumer; it is retired with the event-backbone (services) work.

## Evidence

Committed execution evidence lives under `docs/evidence/`, one folder per
workstream, each captured live against the `fe-bar` profile. Every folder has a
README indexing its files and what they demonstrate.

| Folder | Covers |
| --- | --- |
| `synthetic-data/` | Dataset generation, governance grants/masks, integrity checks, samples |
| `lakebase-serve-down/` | Lakebase provisioning and Triggered serve-down parity |
| `policy-intake-and-retrieval/` | Policy intake, resolution, in-process authorities, retrieval |
| `cdf-incremental-history/` | Native CDF SCD Type 2 cutover and incremental proof |
| `correctness-debt-fixes/` | Fraud-graph heat resolution, transactional policy-intake upsert, `heats_coils` dedup |
| `pipelines-lakebase-cleanup/` | Resource rename, `run.py` separation, policy files relocated to `lakebase/` |
| `claims-adjudication-agent/` | ResponsesAgent build, money-safety invariant enforcement, decision records |
| `mlflow-lifecycle-hygiene/` | Independent eval runs, single `@prod` owner, packaged-artifact evaluation |
| `gold-analytics/` | Gold KPI fact layer + governed metric view |
| `serving-endpoint/` | Governed agent deployment + live end-to-end smoke test |
| `genie-dashboards/` | AI/BI dashboard + Genie space over gold KPIs |
