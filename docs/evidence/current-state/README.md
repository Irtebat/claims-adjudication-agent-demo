# Current state evidence

Captured read-only on 2026-10-01 with Databricks CLI 1.17.0 and `--profile fe-bar`. No resource was created, updated, started, or deleted.

| File | Evidence |
| --- | --- |
| [UC inventory](uc-inventory.json) and [row counts](row-counts.json) | Actual UC types and counts |
| [Synced tables](synced-tables.json) and [Lakebase](lakebase.json) | Actual states, corpus DDL, grants, and count |
| [Serving and models](serving-models.json) | Actual endpoint version and aliases |
| [App, dashboard, Genie](app-dashboard-genie.json) | Actual platform responses |
| [Jobs and pipelines](jobs-pipelines.json) | Actual deployed inventories |
| [Pending](pending.md) | Evidence that still requires an authorized live run |

Lakebase index definitions and grants are corroborated by [the live cutover](../refresh-and-prior-claims/live-cutover.json): `reference.prior_claims_corpus` has 4,999 rows, `lakebase_ann` and `lakebase_bm25` indexes, and SELECT for both service principals.
