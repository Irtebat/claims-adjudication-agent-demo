# Evidence — fraud graph and policy intake correctness fixes

Live evidence captured on 2026-09-24 against the explicit `fe-bar` profile.

| File | Demonstrates |
| --- | --- |
| `schema-diagnosis.json` | Claims expose `coil_id`, not `heat_no`; the coil master owns the heat mapping |
| `fraud-graph-build.json` | Successful serverless build plus output counts and risk-score distribution |
| `policy-dependencies.json` | Pre-fix `CASCADE` blast radius and absence of external FKs/views at diagnosis time |
| `policy-intake.json` | Idempotent intake rerun preserved BM25 indexes and reconciled no current rows away |
| `heats-coils-diagnosis.json` | Generator/bronze uniqueness, exact silver duplication, and append/replay Delta history |
| `heats-coils-verification.json` | Clean silver/Lakebase counts, 1:1 claims join, and unambiguous spec/warranty resolution |
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

The coil-master diagnosis found 4,500 unique rows in both the generator output and
`bronze.heats_coils`, but two append-only 4,500-row commits in silver. Every one of
the 4,500 keys had two exact copies; no key mapped to conflicting attributes or
multiple heats. `silver.heats_coils` now applies regenerated files through keyed
SCD1 Auto CDC using `coil_id`, with file modification time/path as sequencing. The
shared integrity checks fail on either duplicate keys or multiple heats per coil.
Selective full refresh `6bb8b62d-6a17-49e9-b80e-ed6f678a4205` rebuilt only that
table, and synced-table full refresh `6c5f1d3a-aad8-4ff8-b33f-744a570966d3`
re-established the Lakebase copy after the intentional source-CDF reset.
