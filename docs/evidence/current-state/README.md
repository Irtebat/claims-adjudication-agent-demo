# Current state evidence

Captured read-only on 2026-10-01 with Databricks CLI 1.17.0 and `--profile fe-bar`. No resource was created, updated, started, or deleted.

| File | Evidence |
| --- | --- |
| [`uc-inventory.json`](uc-inventory.json) | Captured UC object/type projection |
| [`row-counts.json`](row-counts.json) | Captured UC and Lakebase counts |
| [`uc-inventory-and-counts.md`](uc-inventory-and-counts.md) | Exact inventory/count commands and readable results |
| [`synced-tables.json`](synced-tables.json) | All seven captured synced-table states |
| [`capture_lakebase.py`](capture_lakebase.py) | Read-only credential/query capture program |
| [`lakebase.json`](lakebase.json) | Projection of captured corpus DDL, grants, and count |
| [`serving-models.json`](serving-models.json) | Captured endpoint version and aliases |
| [`app-dashboard-genie.json`](app-dashboard-genie.json) | Captured app, dashboard, and two Genie-space responses |
| [`jobs-pipelines.json`](jobs-pipelines.json) | Projection of captured job and pipeline lists |
| [`runtime-resources.md`](runtime-resources.md) | Exact runtime commands and interpretation |
| [`pending.md`](pending.md) | Evidence still requiring an authorized live run |

Lakebase index definitions and grants are corroborated by [the live cutover](../refresh-and-prior-claims/live-cutover.json): `reference.prior_claims_corpus` has 4,999 rows, `lakebase_ann` and `lakebase_bm25` indexes, and SELECT for both service principals.
