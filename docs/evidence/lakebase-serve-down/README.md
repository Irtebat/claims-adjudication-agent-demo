# Lakebase serve-down evidence

Wave 2 provisioned the `fe-bar-operational-plane` Autoscaling project,
`production` branch, `primary` compute endpoint, and `databricks_postgres`
database with PostgreSQL 17. The endpoint is fixed at the smallest supported
size, 0.5 CU, and suspends after 300 seconds of inactivity. The CLI did not
report a currency cost; Autoscaling bills for compute activity and can scale
to zero. Rerunnable setup is idempotent: schema objects use `IF NOT EXISTS`,
seed rows use primary-key upserts, and synced-table creation skips resources
that already exist instead of duplicating them.

The setup job created seven operational tables with stable primary keys and
`REPLICA IDENTITY FULL`. It enabled `pg_trgm` and `vector`, then loaded 5,000
claims and 5,000 adjudications. A second run returned the same counts as the
Unity Catalog sources, demonstrating idempotent upserts. Seeded rows use the
`synthetic_wave_2_baseline` provenance marker.

All seven reference tables use Triggered sync and are online at source/target
row-count parity. The masks on `silver.customers` and
`silver.heats_coils` were intentionally removed because the operational plane
requires real values. Masks remain on the gold analytical layer and the silver
history materialized views.

Connection metadata is stored in secret scope `fe-bar-lakebase`; all five keys
resolve. OAuth database credentials remain short-lived and are not stored.
Native Lakehouse Sync/CDF was not enabled, confirmed by an empty CDF config
list and the absence of the `wal2delta` schema.

## Build activities

- Provisioned the Autoscaling project, branch, endpoint, and database.
- Corrected the serverless job dependencies to use a job environment.
- Deployed and ran the schema/extension/idempotent-seed bundle job.
- Registered `databricks_postgres` as `fe_bar_operational` in Unity Catalog.
- Removed five approved silver serve-down masks and retained analytical masks.
- Created seven Triggered synced tables and verified row-count parity.
- Created the secret scope and verified its connection metadata keys.
- Validated the bundle and repository checks recorded in `gates.txt`.

Lakebase Autoscaling is GA on AWS and Azure. The `databricks postgres` CLI is
Beta. Synced tables are supported for Autoscaling projects. Native Lakehouse
Sync/CDF is also supported for Autoscaling PostgreSQL 17 but remains Beta and
is intentionally disabled in this wave.
