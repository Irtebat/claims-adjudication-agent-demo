# Current state evidence

Captured read-only on 2026-10-01 with Databricks CLI 1.17.0 and `--profile fe-bar`. No resource was created, updated, started, or deleted.

| File | Evidence |
| --- | --- |
| [UC inventory and counts](uc-inventory-and-counts.md) | UC object types and key row counts |
| [Runtime resources](runtime-resources.md) | Synced pipelines, endpoint/version, aliases, app, jobs, pipeline, dashboard, and Genie state |
| [Pending](pending.md) | Evidence that still requires an authorized live run |

Lakebase index definitions and grants are corroborated by [the live cutover](../refresh-and-prior-claims/live-cutover.json): `reference.prior_claims_corpus` has 4,999 rows, `lakebase_ann` and `lakebase_bm25` indexes, and SELECT for both service principals.
