# Evidence — native CDF SCD2 cutover

Live evidence that claims and adjudications history is maintained incrementally from
Lakebase via native Change Data Feed and AUTO CDC (SCD Type 2). Captured against the
`fe-bar` profile after a normal `process_cdf` run with no full refresh. The
incremental mutation was a single Lakebase row: `ADJ-CLM-0000000`, `decision_status`
from `FINAL` to `REVIEWED`.

| File | Demonstrates |
| --- | --- |
| `scd2-counts.json` | 5,000 current rows per history; the update adds one historical version |
| `scd2-invariants.json` | Zero duplicate-current, invalid-interval, or overlap violations (18 checks) |
| `table-types.json` | Silver histories are `STREAMING_TABLE`; gold current/history objects are views |
| `gold-current-counts.json` | Both gold current views expose 5,000 rows |
| `type-reconciliation.json` | `cited_clause_ids` remains `ARRAY<STRING>`, `finalized_at` remains `TIMESTAMP` |
| `incremental-proof.json` | Before/after SCD2 rows for the single update |
| `incremental-flow-event.json` | Lakeflow streaming update with no `COMPLETE_RECOMPUTE` event |
| `gates.txt` | Bundle validation, pytest collection/execution, Ruff, and evidence-validation commands |
