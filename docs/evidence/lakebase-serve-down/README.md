# Evidence — Lakebase provisioning and serve-down

Live evidence that the Lakebase operational plane was provisioned and that Unity
Catalog reference data serves down into it. Captured against the `fe-bar` profile.

> Note: `oltp-verification.json` records native CDF as disabled at capture time;
> CDF was enabled later (see the `cdf-incremental-history` evidence), so that state
> is superseded.

| File | Demonstrates |
| --- | --- |
| `infrastructure.json` | Project, branch, endpoint, database, PostgreSQL version, sizing, catalog storage |
| `oltp-verification.json` | Setup/seed row counts, CDF-eligible table identities, extensions, then-disabled CDF |
| `synced-table-parity.json` | Five Triggered synced reference tables at source/target parity (plus two lingering legacy relations) |
| `governance-and-secrets.json` | Removed serve-down masks, retained analytical masks, secret-scope keys |
| `gates.txt` | Deployment, validation, syntax, parity, and governance checks |
