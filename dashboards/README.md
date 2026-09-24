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

## Intended inputs

- The gold views published by `pipelines/`.

Nothing is built here yet: there is no dashboard JSON, bundle resource, or Genie
configuration.
