# Evidence — policy intake, in-process authorities, Lakebase Search retrieval, fraud graph

Workstream A. Captured 2026-09-23 (UTC), profile `fe-bar`, catalog `fe-bar-ir`,
Lakebase project `fe-bar-operational-plane` / `production` / `primary`. **Updated for
the warehouse-removal cleanup** (see the section below): the deterministic authorities
now run in-process over the Lakebase psycopg path — no serverless SQL warehouse on the
adjudication path.

## Files

| File | What it shows |
| --- | --- |
| `gates.txt` | `ruff` + `ruff format --check` + **`pytest` 55 collected, 55 passed** (offline correctness fixtures + adapter tests + 2 live Lakebase-5432 runtime smoke) + `compileall` + all three `bundle validate` OK + warehouse-gone grep proof. |
| `intake-summary.json` | Idempotent intake: 6 `spec_params`, 12 `warranty_terms`, 18 `spec_clauses`, 36 `warranty_clauses`, all embedded; extensions verified loaded; index methods verified; reconciled-delete counts. |
| `lakebase-objects.json` | Verified extensions (`lakebase_vector` 1.1.1, `lakebase_text` 0.1.2), **verified index access methods** (`lakebase_ann` / `lakebase_bm25` from `pg_am`), `embedding_dim=1024`, provenance, row counts. |
| `authorities-in-process.json` | The authorities are now the SINGLE source of the money math, called **in-process** by the runtime adapter (no UC functions, no warehouse); the adapter fetches all four inputs over psycopg; in-process results equal the retained fixtures (incl. zero-proration `approved_amount=0.0`); live Lakebase fetches succeed. |
| `grant-activation.json` | `render_governance` proof + the explicit agent-principal object→grant audit. The chain now matches the psycopg path: 7 grants on `fe_bar_operational` (public `spec_params`/`warranty_terms` + reference `heats_coils`/`mill_test_certs`); the 3 UC-function `EXECUTE` grants and the UC-silver grants are removed. With **no** principal 10 SP grants are `skipped`; with principals supplied 0 remain. |
| `retrieval-sanity.json` | `lakebase_ann` vector-only, `lakebase_bm25` BM25-only, and hybrid (RRF) all rank the `marine` exclusion #1; `bm25_index_used=true` (EXPLAIN shows the `lakebase_bm25` index scan — the dead-index gap is closed). |
| `fraud-risk-by-label.json` | `gold.customer_heat_risk` vs ground truth: high risk concentrates on `duplicate` (0.75) and `fraud_cluster` (0.60); in-spec / over-claim / out-of-warranty / supplier score 0.0. |
| `generator-validation.txt` | Live `check-generator`: `generate.py` sources the warranty schedule from the authored policy (no hardcoded durations); 11 integrity checks pass; no policy tables produced by the pipeline. |

## Warehouse-removal cleanup (this update)

A latency investigation showed each adjudication was ~5-6 warehouse round-trips
(~7s warm, tens of seconds cold) for math that runs in-process in <1µs. The
serverless SQL warehouse is now removed from the operational adjudication path.
The conformance/coverage/settlement math in `authorities.py` is unchanged.

1. **UC-function registration removed.** `agent/src/deploy_authorities.py` (which
   registered `compute_conformance`/`coverage`/`settlement` as UC Python functions)
   and the live UC-parity test `test_uc_authorities_parity.py` are deleted.
   `authorities.py` is now the single source of the math, exercised by the offline
   fixtures (which are all retained — incl. zero-proration / exact warranty-duration
   boundary and over-claim / marine-denial).
2. **Runtime adapter rewritten (`authorities_runtime.py`).** It imports `authorities`
   and calls the functions **in-process** on the fetched inputs, and moves ALL FOUR
   `fetch_*` reads onto the SAME psycopg 5432 path as retrieval/duplicate (`db.py`):
   `public.spec_params` / `public.warranty_terms` (native Lakebase) and
   `reference.heats_coils` / `reference.mill_test_certs` (Lakebase reference), all
   with bound/parameterized queries. The SQL-literal construction is gone, so the
   `lit()` / `struct_literal()` helpers (and the two defense-in-depth notes about
   `bool()` truthy strings and `ARRAY<STRING>` accepting any iterable) are retired,
   along with `test_runtime_sql_safety.py`.
3. **Governance reconciled (`governance.sql`).** The three UC-function `EXECUTE`
   grants and the UC-silver grants are removed; the agent-principal chain now grants
   `SELECT` on exactly the four tables the runtime reads over psycopg. The synced
   reference-table count is corrected from seven to **five** in the lakebase
   serve-down evidence/docs (`spec_standards`/`warranty_terms` moved to native
   `public`). The two stale relations `reference.spec_standards` /
   `reference.coating_warranty_terms` are verified unreferenced; see the report note
   for why they were left in place.
4. **Wave-4 note.** Removing UC-function invocation logging is intentionally
   replaced by the Wave-4 per-adjudication Delta decision-record + MLflow tracing
   audit trail, built at adjudication time by the agent (out of scope here).

> The historical "PR #3 review fixes" / "Round-N" notes below are kept for record.
> Where they describe the authorities as **deployed UC Python functions** invoked on
> the warehouse, or the `lit()` SQL-safety audit, they are **superseded** by this
> cleanup: the authorities run in-process and no warehouse/UC-function call remains.

## PR #3 review fixes — verification

1. **generate.py hardcoded warranty durations removed.** `run.py` reads
   `agent/src/policy_source.json` and passes the version schedule as a bundle
   var → job param; `generate.py` selects version/duration/full-coverage by the
   ship-date effective window. Verified live in `generator-validation.txt`.
2. **Authorities are UC `Python` functions at full parity, incl. gauge + width.**
   The UC function bodies are the exact `agent/src/authorities.py` source (embedded
   via `inspect.getsource` by `deploy_authorities.py`), so deployed == tested.
   `compute_conformance` now includes the gauge and width dimensional checks. The
   live parity test (`test_uc_authorities_parity.py`, 17 cases incl. dimensional +
   boundary dates) asserts the deployed functions match the reference. The old SQL
   authorities were dropped and `agent/sql/compute_*.sql` deleted.
3. **Distinct index names + verified reporting.** Indexes are named
   `<table>_lb_ann` / `<table>_lb_bm25`; the intake reads the ACTUAL access method
   from `pg_am` and reports the verified state (and raises if it is not the
   Lakebase Search method). See `lakebase-objects.json`.
4. **Real Lakebase Search, no silent fallback.** `CREATE EXTENSION lakebase_vector
   / lakebase_text` now succeeds (verified via `pg_extension`); `lakebase_ann`
   (`vector_cosine_ops`) and `lakebase_bm25` (`tsvector_bm25_ops`) indexes are built
   after backfill. The intake raises if an extension fails to load.
5. **Retrieval uses the BM25 index.** The lexical arm scores with
   `clause_tsv <@> to_bm25query(to_tsvector('english', q), '<index>'::regclass)`
   (ASC), driven by `lakebase_bm25`; EXPLAIN confirms the index is used.
6. **Intake is the sole creator + populator of all four policy tables**, from
   `policy_source.json`; verified from an empty schema this run (all four tables
   dropped, then intake alone recreated + populated + indexed them). No spec/warranty
   DDL or seed exists elsewhere.
7. **Intake reconciliation.** Rows removed from the source are deleted (reconcile by
   id, transactional). Verified live by injecting two stale rows and confirming the
   next run reported `reconciled_deletes` {spec_params:1, warranty_clauses:1} and
   removed them.
8. **Fraud-graph doc/code parity.** Clusters by `heat_no` only (docstring matches);
   the unused supplier-lot tracking was removed.

## Round-3 review fixes

- **Zero-proration money bug fixed** (`authorities.py`): `compute_settlement` now
  uses an explicit None check (`_num`) so a legitimate `proration_factor=0.0` (and a
  zero `freight_cap`) is respected, never coerced by a falsy `or`. Regression tests
  added offline and to the live parity suite; the DEPLOYED function pays `0.0`.
- **Real runtime path invokes the UC authorities** (`authorities_runtime.py`): reads
  params from the `fe_bar_operational` mirror + the coil MTC from UC silver, builds
  the typed inputs, and calls the deployed UC Python authorities.
  `test_authorities_runtime.py` exercises it end-to-end (2 live tests). The
  `governance.sql` comment is replaced with real GRANT statements (mirror SELECT +
  function EXECUTE + catalog/schema USE) for the app/agent principal.
- **UC authorities declared `DETERMINISTIC`** (`is_deterministic=true`, verified).
- **Fraud-graph docstrings** now describe heat-only clustering, matching the code.

## Round-4 review fixes

- **Govern grants can activate** (`run.py`): `render_governance` now SUBSTITUTES
  `${app_principal}` / `${agent_principal}` from CLI (`--app-principal` /
  `--agent-principal`), env (`APP_PRINCIPAL` / `AGENT_PRINCIPAL`), or a
  `governance:` config block, before splitting. A grant is skipped ONLY when its
  principal is genuinely unset. `grant-activation.json` shows the 12 SP grants move
  from skipped → executable when principals are supplied; `test_governance_render.py`
  asserts both states. (A live test-grant against a specific principal was not
  auto-run — it needs an explicitly named principal/object.)
- **Full CALL privilege chain** (`governance.sql`): added the missing
  `USE SCHEMA ON SCHEMA <catalog>.silver` grant for the agent principal, so the chain
  is USE CATALOG + USE SCHEMA + EXECUTE on the functions, plus USE CATALOG/USE SCHEMA
  + SELECT on the `fe_bar_operational` mirror objects the runtime reads.
- **No SQL injection in the decision path** (`authorities_runtime.py`): claim-derived
  `coil_id` / `spec_id` / `warranty_id` are bound query parameters (`:name`), not
  string-interpolated; the typed inputs use the escaped-literal struct helper. Verified
  live (the runtime-adapter tests pass through the parameterized fetches).
- **Settlement via the adapter is tested end-to-end**, including a zero-proration case
  asserting `approved_amount == 0.0` through the deployed function.

## Round-5 review fixes

- **SQL-safety audit completed** (`authorities_runtime.py`). `lit()` now validates every
  type: dates are parsed as ISO dates (a malformed/injecting value like
  `2020-01-01' OR '1'='1` **raises**, never string-interpolated into `DATE'...'`);
  strings/arrays double single quotes; INT/DOUBLE coerce (non-numeric raises); an
  unknown type raises. Regression test `test_runtime_sql_safety.py` covers the
  malformed date, injection in a free-text field, and non-numeric rejection. Grep of
  the runtime decision path confirms the only interpolated values are the trusted
  config identifiers `self.catalog` / `self.mirror` (inside backticks); all
  claim-derived lookup ids are bound parameters (`:name`) and all struct values go
  through the validated escaped-literal helper.
- **Agent-principal grant chain completed** (`governance.sql`). Added SELECT on
  `silver.mill_test_certs` and `silver.heats_coils` (both read by
  `fetch_measured`). The full object→grant list is enumerated in a comment and audited
  in `grant-activation.json`: USE CATALOG + USE SCHEMA on silver and
  `fe_bar_operational.public`; SELECT on `silver.mill_test_certs`,
  `silver.heats_coils`, `spec_params`, `warranty_terms`; EXECUTE on the three
  authorities — no gaps.
- **Principal identifiers validated** in `render_governance` against
  `^[A-Za-z0-9][A-Za-z0-9._@-]*$` (rejects backticks/quotes/semicolons/whitespace)
  before being placed inside backticks; unit-tested.
- Updated the stale governance comment to describe the substitute-then-execute behavior.

## Kept (not regressed)
Governed GTE embeddings via `system.ai.gte-large-en` (Unity Gateway), 1024-dim
cosine, provenance stored and reused at query time; runtime retrieval + `pg_trgm`
duplicate checks (not UC Python UDFs); OAuth creds with no secrets committed;
`setup_and_seed.py` untouched; unrelated reference/master/history datasets intact.
