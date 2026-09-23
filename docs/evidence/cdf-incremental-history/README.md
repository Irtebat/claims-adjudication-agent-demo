# Native-CDF SCD2 cutover evidence

Captured live from the `fe-bar` profile on 2026-09-23 UTC after a normal
`process_cdf` run. No full refresh was used.

| File | Evidence |
| --- | --- |
| `scd2-counts.json` | 5,000 current rows for both history targets; the updated adjudication adds one historical version |
| `scd2-invariants.json` | Zero duplicate-current, invalid-interval, or overlap violations |
| `table-types.json` | Silver history targets are `STREAMING_TABLE`; gold current/history objects are views |
| `gold-current-counts.json` | Both gold current views expose 5,000 current rows |
| `type-reconciliation.json` | `cited_clause_ids` remains `ARRAY<STRING>` and `finalized_at` remains `TIMESTAMP` |
| `incremental-proof.json` | Before/after SCD2 rows for the single Lakebase update |
| `incremental-flow-event.json` | Lakeflow update `5423ccb3-ddd4-446c-a877-e4f5d68c7553` completed a streaming update, with no `COMPLETE_RECOMPUTE` event |
| `gates.txt` | Exact bundle validation, pytest collection/execution, Ruff, and evidence-validation commands |

The incremental mutation was exactly one Lakebase row:
`ADJ-CLM-0000000`, `decision_status` from `FINAL` to `REVIEWED`.
