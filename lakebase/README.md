# Lakebase

## Purpose

Operate the Postgres system of record, policy tables, Unity Catalog serve-down, and native CDF serve-up.

## Objects created

Project `fe-bar-operational-plane`, production branch, primary endpoint, database `databricks_postgres`, operational/policy tables, CDF config, and schema `reference`.

Desired and deployed synced-table inventory on 2026-10-01:

| Table | Source | Desired | Deployed |
| --- | --- | --- | --- |
| `heats_coils` | `silver.heats_coils` | Triggered | ONLINE |
| `mill_test_certs` | `silver.mill_test_certs` | Triggered | ONLINE |
| `customers` | `silver.customers` | Triggered | ONLINE |
| `suppliers` | `silver.suppliers` | Triggered | ONLINE |
| `defect_codes` | `silver.defect_codes` | Triggered | ONLINE |
| `customer_heat_risk` | `gold.customer_heat_risk` | Triggered | ONLINE |
| `prior_claims_corpus` | `gold.prior_claims_corpus` | Triggered | ONLINE, 4,999 rows |

## Resources configured

Bundle job `fe-bar-lakebase-setup-and-seed`; native CDF to `fe-bar-ir.cdf`; ANN and BM25 corpus indexes. Before create/recreate, confirm the app and serving principal IDs and overrides `APP_SP_PRINCIPAL` / `SERVING_SP_PRINCIPAL`; creation waits up to one hour for `SYNCED_TABLE_ONLINE*` before indexes and grants run.

## Data flow

```mermaid
flowchart LR
  UC[UC silver and gold] -->|triggered sync| R[reference schema]
  O[public operational tables] -->|native CDF| C[UC cdf]
  C --> G[Gold] --> UC
```

## Deploy

Working directory: `lakebase/`.

```bash
databricks bundle validate --strict -t prod --profile fe-bar
databricks bundle deploy -t prod --profile fe-bar
```

## Run

Working directory: repository root. First-time only:

```bash
uv run --with pyyaml python lakebase/run.py setup-and-seed
uv run --with pyyaml python lakebase/run.py create-cdf
uv run --with pyyaml python lakebase/run.py synced-tables
```

Routine refresh uses `resync-synced-tables`; recreate is only for incompatible schema changes. Both creation and recreation poll until ONLINE; failure or timeout stops before post-create grants.

## Verify

Working directory: repository root. Read-only:

```bash
databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.prior_claims_corpus --profile fe-bar -o json
databricks apps get steel-claims-cockpit --profile fe-bar -o json
databricks serving-endpoints get agents_fe-bar-ir-default-claims_adjudication_agent --profile fe-bar -o json
```

Repeat `get-synced-table` for all seven names above. Expected: `status.detailed_state` starts with `SYNCED_TABLE_ONLINE`; app and serving principal IDs match the grant preflight. Index and grant output is in [Lakebase evidence](../docs/evidence/current-state/lakebase.json).

## Status

2026-10-01: repo head desires seven synced tables and the deployed workspace has all seven ONLINE. `reference.prior_claims_corpus` has 4,999 rows, ANN/BM25 indexes, and SELECT grants for the app and serving principals.
