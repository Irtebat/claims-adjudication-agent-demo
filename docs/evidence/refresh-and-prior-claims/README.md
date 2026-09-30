# Evidence — refresh commands and lakehouse-built prior claims

Branch `refresh-commands-and-prior-claims`, off `main` 888ea2d.

| File | What it shows |
| --- | --- |
| `gates.txt` | Money-math gate (`agent/src/authorities.py` empty diff), `bundle validate --strict` for every touched bundle, unit tests, ruff, mypy, compileall |

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

## Live run: not performed (blocked)

The first live step, deploying the agent bundle so the new `prior_claims_corpus` job
exists, was denied by the auto-mode safety classifier:

```bash
cd agent && databricks bundle deploy -t prod --profile fe-bar
```

Because every later live step depends on that job, nothing was run live: no job run
IDs, no synced table created, no re-sync. To run the proof yourself:

```bash
cd agent && databricks bundle deploy -t prod --profile fe-bar
databricks bundle run prior_claims_corpus -t prod --profile fe-bar
cd .. && uv run --with pyyaml python lakebase/run.py synced-tables          # creates reference.prior_claims_corpus + indexes, grants SELECT
uv run --with pyyaml python lakebase/run.py resync-synced-tables            # triggered re-sync of customer_heat_risk + prior_claims_corpus
```

`synced-tables` and `resync-synced-tables` issue Lakebase writes (`CREATE INDEX`,
`GRANT SELECT`, `VACUUM (ANALYZE)`). One thing the first live run must confirm that
tests cannot: that `lakebase_ann` and `lakebase_bm25` accept the expression indexes
(`(embedding::vector(1024))`, `(to_tsvector('english', defect_narrative))`). If
either is rejected, the corpus needs a different storage shape for the embedding.

## Not changed live

- The legacy native `public.prior_claims` table is left in place. The deployed
  serving endpoint still reads it until the next `register -> evaluate -> promote ->
  deploy` cycle packages the new `retrieval.py`; drop it after that (see
  `docs/RUNBOOK.md`).
- No synced table was dropped or recreated.
