# docs

Project documentation and committed execution evidence.

This repository is a reference implementation of a steel claims-adjudication agent.
Claims are born in a Lakebase (Postgres) operational plane; governed reference and
policy data moves between Lakebase and Unity Catalog; deterministic tools decide
money while retrieval finds citable evidence; and the claim/adjudication history is
maintained incrementally in the lakehouse via native Change Data Feed (CDF).

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
    gold["gold current + history views"]
  end
  agent["agent tools:<br/>resolution, authorities,<br/>duplicate, retrieval"]

  refsrc -- "serve-down" --> ref
  claims -- "native CDF" --> cdfland --> silverh --> gold
  policy --> agent
  ref --> agent
  claims --> agent
  agent -. planned .-> app["app: adjuster UI"]
  gold -. planned .-> dash["dashboards / Genie"]
  gold -. planned .-> evalx["eval: MLflow gate"]
```

## Layers

| Directory | What it does | Status |
| --- | --- | --- |
| `pipelines/` | Synthetic data, medallion pipeline, CDF SCD2 history, governance | Built |
| `lakebase/` | Operational Postgres plane, serve-down, native tables, CDF | Built |
| `agent/` | Policy intake, deterministic authorities, retrieval, duplicate, fraud graph | Built (tools; orchestrating agent pending) |
| `app/` | Adjuster review UI (Databricks App) | Planned |
| `dashboards/` | AI/BI dashboards + Genie over gold KPIs | Planned |
| `eval/` | MLflow evaluation harness + CI threshold gate | Planned |
| `services/` | Kafka worker, outbox relay, downstream consumers | Planned |

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
