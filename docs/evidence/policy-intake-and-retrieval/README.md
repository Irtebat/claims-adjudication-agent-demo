# Evidence — policy intake and retrieval

Live evidence that the authored policy JSON was parsed into Lakebase params and
clause tables, that resolution and the in-process deterministic authorities produce
correct outputs, and that clause and prior-claim retrieval work. Captured against
the `fe-bar` profile.

| File | Demonstrates |
| --- | --- |
| `intake-and-indexes.json` | Policy / prior-claim counts, embedding details, actual BM25/ANN index methods |
| `resolution-samples.json` | Atomic coil -> specification and ship-date -> warranty-version resolution |
| `authorities-in-process.json` | Lakebase psycopg param resolution followed by in-process authority outputs |
| `retrieval-sanity.json` | BM25 and ANN index plans and hybrid prior-claim rankings |
| `generator-validation.json` | Generator validation run and integrity metrics (no data landed) |
| `gates.txt` | Tests, lint, bundle validation, Lakebase run, and the known pre-existing mypy note |
