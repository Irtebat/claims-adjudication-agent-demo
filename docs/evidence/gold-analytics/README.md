# Gold analytics evidence

This folder records the deployed gold analytics validation. JSON files are direct
SQL results captured after the pipeline and metric-view job complete.

- `object-row-counts.json`: row counts for the wide fact and six aggregates.
- `quality-kpi-sample.json`: representative daily KPI rows.
- `reconciliation.json`: fact grain and approved-amount source/KPI reconciliation.
- `metric-view-query.json`: a governed `MEASURE()` query result.
- `dq-checks.json`: zero-violation fact and reconciliation checks.
- `gates.txt`: exact local gate commands and results.

The silver customer, supplier, defect-code, and certificate duplication is handled
by primary-key deduplication inside gold joins. Root-cause correction in the silver
flows is intentionally deferred to avoid a resnapshot or full refresh. Workflow
backlog KPIs are also deferred until settlement, supplier-recovery, and
investigation events are seeded.
