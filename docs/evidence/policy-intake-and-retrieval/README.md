# Policy intake, resolution, and retrieval evidence

Captured 2026-09-23 from Lakebase project `fe-bar-operational-plane` / database `databricks_postgres`.

- Policy tables use composite natural keys.
- Clause tables contain metadata, text, and `tsvector` only; both use `lakebase_bm25` and have no VECTOR column or ANN index.
- `prior_claims` contains 5,000 governed GTE embeddings (`system.ai.gte-large-en`, 1,024 dimensions, L2 normalized) and supports cosine ANN plus BM25 hybrid retrieval.
- Resolution joins coil atomic fields to spec parameters and the warranty version effective on ship date, including `freight_cap`.
