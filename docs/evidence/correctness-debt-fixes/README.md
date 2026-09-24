# Evidence — fraud graph and policy intake correctness fixes

Live evidence captured on 2026-09-24 against the explicit `fe-bar` profile.

| File | Demonstrates |
| --- | --- |
| `schema-diagnosis.json` | Claims expose `coil_id`, not `heat_no`; the coil master owns the heat mapping |
| `fraud-graph-build.json` | Successful serverless build plus output counts and risk-score distribution |
| `policy-dependencies.json` | Pre-fix `CASCADE` blast radius and absence of external FKs/views at diagnosis time |
| `policy-intake.json` | Idempotent intake rerun preserved BM25 indexes and reconciled no current rows away |
| `gates.txt` | Test collection/execution, Ruff, compilation, bundle, and guardrail verification |

The first corrected fraud run revealed duplicate physical rows in
`silver.heats_coils`: 5,000 current claims expanded to 10,000 joined rows. The
final job deduplicates identical `coil_id -> heat_no` pairs and rejects ambiguous
mappings before scoring. Run `355068363829515` is the final successful evidence
run; run `310634335625264` is retained in the narrative only as the diagnostic run
that exposed the duplicate-row issue.

The policy dependency query found only table-owned primary-key/storage objects and
the two clause BM25 indexes. No foreign keys or views referenced the four policy
tables at capture time. The fix nevertheless avoids replacing table identity or
destroying dependents: it creates missing tables, upserts natural keys, and deletes
only rows carrying an older full-source hash within the same transaction.
