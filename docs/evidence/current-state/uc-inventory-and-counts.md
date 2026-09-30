# UC inventory and counts

Captured 2026-10-01. Exact read-only commands:

```bash
databricks experimental aitools tools query --warehouse 38e458a09de4a055 --profile fe-bar -o json 'SELECT table_schema, table_name, table_type FROM `fe-bar-ir`.information_schema.tables WHERE table_schema IN ("bronze","silver","gold","cdf","reference") ORDER BY 1,2'
databricks experimental aitools tools query --warehouse 38e458a09de4a055 --profile fe-bar -o json 'SELECT "gold.claims_current" object, count(*) rows FROM `fe-bar-ir`.gold.claims_current UNION ALL SELECT "gold.prior_claims_corpus",count(*) FROM `fe-bar-ir`.gold.prior_claims_corpus UNION ALL SELECT "silver.claims_history",count(*) FROM `fe-bar-ir`.silver.claims_history'
```

Observed: bronze has five materialized views; silver has seven streaming tables; gold has a streaming table, views, materialized views, managed outputs, and a metric view; cdf has eight managed history tables. `reference` is a Lakebase schema, not a UC schema. Historical `cdf.lb_claims_pending_history` remains visible but is not a current queue.

| Object | Rows |
| --- | ---: |
| `gold.claims_current` | 5,000 |
| `silver.claims_history` | 5,000 |
| `gold.prior_claims_corpus` | 4,999 |
