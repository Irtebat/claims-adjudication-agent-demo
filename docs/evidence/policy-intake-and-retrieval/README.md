# Policy intake, resolution, and retrieval evidence

Captured 2026-09-23 from Lakebase project `fe-bar-operational-plane`, database
`databricks_postgres`, using profile `fe-bar`.

- `intake-and-indexes.json`: live policy/prior-claim counts and index methods.
- `resolution-samples.json`: live atomic coil-to-spec and ship-date-to-warranty bindings.
- `retrieval-sanity.json`: clause BM25 and prior-claim ANN+BM25 index plans plus hybrid rankings.
- `authorities-in-process.json`: live psycopg resolution and deterministic authority outputs.
- `generator-validation.json`: successful no-landing generator validation run and row metrics.
- `gates.txt`: final local test and lint results.

Clause tables contain metadata, text, and lexical search state only. Dense
embeddings and cosine ANN are confined to the separate prior-claims corpus.
