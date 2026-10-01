# Evidence — refresh commands and lakehouse-built prior claims

Branch `refresh-commands-and-prior-claims`, off `main` 888ea2d.

| File | What it shows |
| --- | --- |
| `gates.txt` | Money-math gate (`agent/src/authorities.py` empty diff), `bundle validate --strict` for every touched bundle, unit tests, ruff, mypy, compileall |
| `live-cutover.json` | Operator live run: corpus job run, synced table ONLINE with 4999 rows, expression-index definitions, EXPLAIN index use, re-sync update IDs, regrant |

## What was verified

- `agent/src/authorities.py` has an empty diff against `main`.
- `databricks bundle validate --strict` passes for `pipelines`, `lakebase`, `agent`,
  `services`, `demo` (`-t prod`) and `app` (`-t default`). Read-only.
- Unit tests (counts are collected test cases, parametrized cases counted
  individually): pipelines 34, lakebase 31, agent 98 passed / 3 skipped, services 34,
  app server 84 (vitest), app server typecheck clean.
- Behavioral tests for the new commands, all with the CLI mocked at `subprocess.run`:
  - `pipelines/tests/test_refresh_commands.py`: routine order (medallion -> agent
    deploy -> fraud_graph -> prior_claims_corpus -> resync -> medallion); demo =
    demo_backlog then routine; neither path calls `synced-tables`, `recreate`,
    `regrant`, `--full-refresh`, `setup-and-seed`, or `create-cdf`; stops at the first
    failing step.
  - `pipelines/tests/test_refresh.py`: `run.py refresh` passes the claims,
    adjudications, and decision-record CDF table names to both the deploy and the
    `refresh_medallion` run, and tolerates a not-yet-materialized decision-record table.
  - `pipelines/tests/test_bootstrap_idempotency.py`: new bootstrap order, and that the
    fraud-graph job publishes the empty typed risk table before any claims read.
  - `lakebase/tests/test_synced_tables.py`: re-sync starts an update of each existing
    table's sync pipeline, polls it to COMPLETED, runs no full refresh / delete /
    create / DROP / GRANT and never calls the re-grant script, then ensures the corpus
    indexes and runs `VACUUM (ANALYZE)`; create re-grants only when it created a
    table; recreate runs delete -> DROP -> create -> indexes -> re-grant in that order.
- The re-sync interface was confirmed read-only on the live workspace, not guessed:
  `databricks postgres get-synced-table synced_tables/fe_bar_operational.reference.customer_heat_risk`
  returns `status.pipeline_id` (`1e8fddb2-f544-40e5-85b4-ee5d5c5c5501`); that pipeline
  is the managed sync pipeline (`Synced table: fe_bar_operational.reference.customer_heat_risk ...`);
  `databricks pipelines get-update <pipeline_id> <update_id>` returns `update.state`
  (`COMPLETED`). The CLI has no dedicated synced-table refresh command
  (`databricks postgres -h` lists only create/get/delete), and the docs describe the
  "Sync now" button and the Jobs "Database Table Sync pipeline" task, both of which run
  that pipeline. `databricks pipelines start-update -h` confirms the default update is
  not a full refresh.
- Current live data the corpus will build from (read-only query):
  `gold.adjudications_current` has 4999 `FINAL` rows and 1 `REVIEWED`;
  `gold.claims_current` has 5000 rows.

## Follow-up fixes (code and tests only, no workspace commands)

- Empty-workspace bootstrap: the gold fact now joins the always-defined pipeline view
  `decision_records_for_fact` (empty and typed while the decision-record CDF source is
  absent). `pipelines/tests/test_decision_records_bootstrap.py` executes the real
  transformation on local Spark (pyspark 4.0.1, Java 17) with the source absent: no
  streaming table or flow is registered, the view is empty with the declared schema,
  and the fact's own decision-record SQL, extracted from `gold_analytics.sql`, runs
  against it and returns one row per adjudication with NULL agent columns. With the
  source present the view exposes the identical schema. Without pyspark that module
  is skipped (reported as 1 skipped).
- `pipelines/run.py refresh` fails loudly when the decision-record CDF table is not
  found, unless `--allow-missing-decision-records` is passed (the bootstrap passes it).
- `prior_claims_corpus` job (failed live in run 124128428225115 with
  `NOT_SUPPORTED_WITH_SERVERLESS` on PERSIST TABLE): no caching; the candidate set is
  staged once to the Delta table `gold.prior_claims_corpus_staging`. A test scans the
  job and its local imports for cache/persist calls.
- Retrieval: each arm queries `reference.prior_claims_corpus` directly and orders by
  the indexed expression; tests assert character identity with the index DDL (built
  from shared constants in `agent/src/retrieval.py`) and the absence of any CTE.

## Live cutover (operator run)

Recorded in `live-cutover.json`. The operator ran these steps against the `fe-bar`
profile and reported the results; this agent ran no workspace commands for them.

- **Corpus job.** Run `776035986368373` of `fe-bar-prior-claims-corpus` finished
  TERMINATED / SUCCESS. The earlier run `124128428225115` failed with
  `[NOT_SUPPORTED_WITH_SERVERLESS] PERSIST TABLE`; commit `2846243` removed the
  caching.
- **Synced table.** `reference.prior_claims_corpus` is ONLINE
  (`SYNCED_TABLE_ONLINE_TRIGGERED_UPDATE`) with 4999 rows, one per FINAL
  adjudication. That matches the 4999 FINAL rows in `gold.adjudications_current`
  noted above.
- **Expression indexes are accepted.** This was the open question from the offline
  work. Postgres reports:
  - `prior_claims_corpus_lb_ann USING lakebase_ann (((embedding)::vector(1024)) vector_cosine_ops)`
  - `prior_claims_corpus_lb_bm25 USING lakebase_bm25 (to_tsvector('english'::regconfig, defect_narrative))`
- **Both arms use their index.** `EXPLAIN` shows the dense arm as an Index Scan on
  the partition embedding index, ordered by `(embedding)::vector(1024) <=> ...`. The
  FTS arm is an Index Scan on the partition `to_tsvector` index, ordered by
  `<@> bm25query`. The hybrid query returned 20 arm rows, each with `verdict` and
  `approved_amount`.
- **Re-sync.** `lakebase/run.py resync-synced-tables` completed both sync pipeline
  updates: `customer_heat_risk` update `65439518-951c-4c73-b264-f49b489266c6` and
  `prior_claims_corpus` update `546e096e-f823-4814-b2ff-62eab923ce6c`. It reported
  `regranted: false`, as designed.
- **Create-path bug, now fixed.** `lakebase/run.py synced-tables` ran `CREATE INDEX`
  before the initial sync had created the Postgres table. It failed with
  `UndefinedTable: relation "reference.prior_claims_corpus" does not exist`, and the
  regrant step never ran. Commit `54dba43` makes create and recreate wait for
  `SYNCED_TABLE_ONLINE*` first.
- **Manual regrant.** Because of that bug, the regrant was applied by hand. It gave
  `SELECT` on `reference.prior_claims_corpus` to the app SP
  `d5309ee7-a8ea-499f-99d4-4ccbd8369d93` and the serving SP
  `47643eb1-dbd5-40a6-a51d-5da6b8e2da7a`. With the fix, a future create or recreate
  runs this regrant itself.

## Not changed live

- The legacy native `public.prior_claims` table is left in place. The deployed
  serving endpoint still reads it until the next `register -> evaluate -> promote ->
  deploy` cycle packages the new `retrieval.py`; drop it after that (see
  `docs/RUNBOOK.md`).
- No synced table was dropped or recreated.
