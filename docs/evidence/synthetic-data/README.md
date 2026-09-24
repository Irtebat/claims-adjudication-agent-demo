# Synthetic data evidence

Captured 2026-09-23 from catalog `fe-bar-ir` after clean bootstrap run
`556871833171983` and governance reapplication.

- `table-schemas.json`: columns and types for every current bronze, silver, and gold business table.
- `row-counts.json`: clean per-table counts.
- `outcome-distribution.json`: finalized adjudication verdict/disposition distribution.
- `integrity-checks.json`: the ten checks from `pipelines/src/checks.py`, all with zero violations.
- `persisted-table-inventory.json`: the current catalog inventory; retired policy and risk tables are absent.
- `catalog-grants.json`, `schema-grants.json`, `grants.json`, `show-grants-claims.json`, and `show-grants-heats.json`: live governance grants.
- `column-masks.json`: live masks. Claims have only the customer and filed-freight masks; adjudication amounts remain protected separately.
- `fraud-clusters.json`: finalized investigation annotations joined to claim/customer/heat facts.
- `sample-*.json`: live rows from every current silver and gold business table.

Claims are lean customer intake. Final adjudications carry outcome, duplicate,
investigation, citation, and amount ground truth.
