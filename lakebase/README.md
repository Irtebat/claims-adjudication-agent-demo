# Lakebase operational plane

This directory defines the Wave 2 Lakebase Autoscaling operational plane.
The project uses PostgreSQL 17, a 0.5 CU endpoint, and the
`databricks_postgres` database so a later wave can enable native Lakehouse
Sync. Native CDF is not enabled here.

`databricks.yml` deploys the one-time, idempotent setup and seed job.
`src/setup_and_seed.py` creates the seven operational tables, enables
`pg_trgm` and `vector`, and upserts the synthetic claims and adjudications
history. Every seeded row has `data_provenance = 'synthetic_wave_2_baseline'`.

Synced tables are created with `scripts/create_synced_tables.sh`. The command
uses Triggered mode and the existing CDF-enabled silver tables. Current
Lakebase bundle support does not include Autoscaling synced tables, so this
script uses the supported `databricks postgres create-synced-table` surface.

Run commands from this directory:

```text
databricks bundle validate --strict -t prod --profile fe-bar
databricks bundle deploy -t prod --profile fe-bar
databricks bundle run setup_and_seed -t prod --profile fe-bar
./scripts/create_synced_tables.sh
```

The job generates a short-lived OAuth database credential at runtime. No
database password or token is stored in this repository.
