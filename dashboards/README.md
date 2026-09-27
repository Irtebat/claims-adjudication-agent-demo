# dashboards

Planned layer — not yet implemented. This directory will hold AI/BI (Lakeview)
dashboard definitions and Genie space definitions built over the gold analytical
tables.

## Intended purpose

- Operational and quality KPIs over `gold.claims_current` /
  `gold.adjudications_current` and the full `gold.*_history` timelines — for
  example approval/denial rates, settlement amounts, supplier-attributable
  trends, and fraud-cluster counts.
- A Genie space for natural-language questions over the same gold data.

## Available inputs

- `gold.gold_claim_adjudication_fact` for cross-filterable detail.
- `gold.gold_quality_kpis`, `gold.gold_failure_mode_analytics`,
  `gold.gold_supplier_recovery_analytics`, `gold.gold_fraud_cluster_analytics`,
  `gold.gold_agent_human_alignment`, and `gold.gold_retrieval_citation_kpis` for
  dashboard-ready aggregates.
- `gold.quality_claims_metrics` for governed reusable measures in Genie and AI/BI.

The KPI sources are ready for the next Genie/dashboard layer. Dashboard JSON and
Genie configuration are not yet built here. Workflow-backlog visuals remain
deferred until settlement, recovery, and investigation events exist.
