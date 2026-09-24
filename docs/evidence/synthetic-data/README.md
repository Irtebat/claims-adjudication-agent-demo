# Evidence — synthetic data and governance

Live evidence that the synthetic steel-claims dataset was generated, governed, and
landed correctly in Unity Catalog `fe-bar-ir`. Captured against the `fe-bar`
profile with real query text, UTC capture times, and result rows.

> Note: this set predates the native-CDF SCD2 cutover. The claims/adjudications
> object types in `persisted-table-inventory.json` and `table-schemas.json` reflect
> the earlier Parquet-fed materializations; the current silver histories are
> streaming tables and the gold objects are views (see the `cdf-incremental-history`
> evidence).

| File | Demonstrates |
| --- | --- |
| `row-counts.json` | Row counts across bronze/silver/gold |
| `outcome-distribution.json` | Verdict / disposition distribution of the injected label patterns |
| `integrity-checks.json` | Ten fixture integrity checks, zero violations |
| `fraud-clusters.json` | Injected 5-claim / 3-customer / single-heat fraud clusters |
| `table-schemas.json` | Column/type inventory of the business objects (pre-cutover) |
| `persisted-table-inventory.json` | Unity Catalog object inventory (pre-cutover) |
| `grants.json`, `schema-grants.json`, `catalog-grants.json` | Curated-table, schema, and catalog privileges for the two human roles |
| `column-masks.json` | Column-mask inventory at capture time |
| `show-grants-claims.json`, `show-grants-heats.json` | Direct `SHOW GRANTS` output for gold claims history and silver heats/coils |
| `sample-silver-*.json`, `sample-gold-*.json` | Sample rows from curated silver and gold tables |
