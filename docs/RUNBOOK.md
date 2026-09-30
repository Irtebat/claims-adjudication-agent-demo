# Runbook

What runs in this repository, what triggers it, and the order to run things in. All
commands use `--profile fe-bar`; bundles use `--target prod` (`-t prod`), except the
app bundle, which uses `-t default`. Run commands from the repository root unless a
directory is named.

Execution model (see the root README): plain `databricks bundle deploy|run` for
anything on Databricks compute; `uv run python` only for the thin wrappers that add
orchestration a plain `bundle run` cannot express.

## What exists and when to trigger it

| Bundle / area | Resource | What it does | Trigger | When to run |
| --- | --- | --- | --- | --- |
| `pipelines/` | Pipeline `steel-claims` (key `medallion`) | Bronze/silver reference data, native-CDF AUTO CDC SCD2 claim/adjudication history, gold decision records, current/history views, gold fact and KPI views | Triggered (no schedule) | Through `pipelines/run.py refresh` only: it resolves the hash-suffixed CDF table names the pipeline needs |
| `pipelines/` | Job `steel-claims-refresh-medallion` | Runs the pipeline once, incrementally | Manual | Same as above (routine and demo refresh run it twice) |
| `pipelines/` | Job `steel-claims-generate-raw` | Writes the synthetic raw Parquet landing data | Manual, via `pipelines/run.py generate` | Fresh bootstrap only |
| `pipelines/` | Job `steel-claims-validate-generator` | Runs the generator against temp views; lands nothing | Manual, via `pipelines/run.py check-generator` | Diagnostic, any time |
| `pipelines/` | Job `steel-claims-deploy-metric-views` | Creates the `gold.quality_claims_metrics` metric view | Manual | After bootstrap and after a metric-view SQL change |
| `pipelines/` | `pipelines/run.py govern` | Account groups, grants, column masks | Manual | After bootstrap and after a `governance.sql` change |
| `lakebase/` | Job `fe-bar-lakebase-setup-and-seed` | Postgres DDL and the one-time synthetic seed; then the policy intake | Manual, via `lakebase/run.py setup-and-seed` (refuses once CDF exists) | Fresh bootstrap only |
| `lakebase/` | Native CDF config (`public` -> `fe-bar-ir.cdf`) | Streams every insert/update/delete of the operational tables into `cdf.lb_*_history` | Always on (native Lakebase) | Created once by `lakebase/run.py create-cdf` |
| `lakebase/` | Synced tables `reference.{heats_coils, mill_test_certs, customers, suppliers, defect_codes, customer_heat_risk, prior_claims_corpus}` | Serve UC tables down to Postgres for the agent and app | Triggered (Delta CDF on each source) | Create: `lakebase/run.py synced-tables`. Re-sync: `lakebase/run.py resync-synced-tables` (routine refresh does `customer_heat_risk` and `prior_claims_corpus`). Recreate: schema change only |
| `lakebase/` | `lakebase/run.py policy-intake` | Loads `policy_source.json` into the four policy tables | Manual | After a policy edit |
| `agent/` | Job `fe-bar-fraud-graph` | Scores `gold.customer_heat_risk` from current claims | Manual | Routine refresh; twice in bootstrap (first run publishes an empty table) |
| `agent/` | Job `fe-bar-prior-claims-corpus` | Builds `gold.prior_claims_corpus` from FINAL adjudications, with governed embeddings | Manual | Routine refresh, after the first medallion run |
| `agent/` | Job `fe-bar-deploy-claims-adjudication-agent` | Creates/updates the governed serving endpoint | Manual | After `register -> evaluate -> promote` |
| `agent/`, `eval/` | `register_agent.py`, `evaluate.py`, `promote.py` | Human-gated model lifecycle | Manual `uv run python` | On an agent code change (including `retrieval.py`) |
| `demo/` | Job `steel-claims-demo-backlog` | New synthetic claims plus RECOMMENDED agent adjudications | Manual | Demo refresh |
| `services/` | Job `fe-bar-services-migrate` | Drops the retired pending queue; grants the app SP | Manual | Once, before the other services jobs |
| `services/` | Jobs `fe-bar-services-{producer, worker, relay, consumers}` | Kafka event backbone: CDF inserts -> `claim.submitted` -> RECOMMENDED adjudication; finalized outbox -> `claim.adjudicated` -> four consumers | Hourly periodic schedule (continuous toggle documented per job) | Automatic once deployed. They fail on every run until the Kafka secrets exist in scope `fe-bar-aiven-kafka` |
| `app/` | App `steel-claims-cockpit` | Adjuster cockpit; the only writer of FINAL adjudications and `public.outbox` rows | Always running | Deploy (`-t default`) on an app change |
| `dashboards/` | Dashboard `Steel Quality Claims Analytics` and the Genie space | Read gold views | On read | Deploy on a dashboard change; data freshness follows the medallion |

## Run orders

### Fresh bootstrap (empty workspace only)

```bash
uv run --with pyyaml python scripts/bootstrap.py
```

It runs: pipelines deploy -> `generate` -> `setup-and-seed` -> `create-cdf` -> agent
deploy -> `fraud_graph` -> `pipelines/run.py refresh` -> `fraud_graph` ->
`prior_claims_corpus` -> `pipelines/run.py refresh`.

The gold fact joins `gold.customer_heat_risk`, which the fraud-graph job builds from
the medallion's own `gold.claims_current`. The first `fraud_graph` run therefore comes
before the first medallion run: with no `gold.claims_current` it publishes an empty,
typed risk table and exits, so the first medallion run succeeds. The second run
scores for real, and the last medallion run picks up the scores.

Then, once the app and serving service principals exist:

1. `uv run --with pyyaml python lakebase/run.py synced-tables` (creates all seven
   synced tables, builds the corpus indexes, grants SELECT to both principals).
2. `cd pipelines && databricks bundle run deploy_metric_views -t prod --profile fe-bar`
3. `uv run --with pyyaml python pipelines/run.py govern`
4. Deploy the agent endpoint, app, dashboards, and services as their READMEs describe.

Known gap: the gold fact also reads `gold.adjudication_decision_records`, which the
pipeline defines only after the first decision record has materialized
`cdf.lb_adjudication_decision_records_history`. On a truly empty workspace that table
does not exist yet at the first medallion run. This has not been resolved here.

### Routine refresh

```bash
uv run --with pyyaml python scripts/refresh.py routine
```

1. `pipelines/run.py refresh`: incremental medallion run, fed every CDF table name
   (claims, adjudications, and decision records once materialized).
2. Agent `bundle deploy`, then `bundle run fraud_graph`.
3. `bundle run prior_claims_corpus` (embeds only new or edited narratives).
4. `lakebase/run.py resync-synced-tables`: triggered re-sync of
   `reference.customer_heat_risk` and `reference.prior_claims_corpus`, then corpus
   index check and `VACUUM (ANALYZE)` so BM25 statistics include the new rows.
5. `pipelines/run.py refresh` again, so the gold fact carries the new risk scores.

A re-sync is a pipeline update on the existing synced table. It keeps the Postgres
table, its owner, its indexes, and its grants, so it never re-grants. Nothing in the
routine refresh reseeds, recreates, or runs a full refresh.

### Demo refresh

```bash
uv run --with pyyaml python scripts/refresh.py demo
```

Demo `bundle deploy`, `bundle run demo_backlog` (new claims and RECOMMENDED
adjudications in the adjuster queue), then the routine refresh. Precedent and outbox
rows appear only after an adjuster finalizes claims in the app; the next routine
refresh carries those FINAL decisions into gold and the corpus.

### Schema change

Lakebase operational table (for example a new column on `public.adjudications`):

1. Add the column to `lakebase/src/setup_and_seed.py` (the `CREATE TABLE` for fresh
   setups, plus an `ADD COLUMN IF NOT EXISTS` if existing workspaces need it). Do not
   rerun `setup-and-seed` on a live workspace; apply the `ALTER` once over psql
   (`databricks psql --project fe-bar-operational-plane --profile fe-bar`).
2. Native CDF re-snapshots the table. The next medallion run fails by design (see
   below). Run one full refresh of the medallion:
   `cd pipelines && databricks bundle run medallion --full-refresh-all -t prod --profile fe-bar`
3. Run the routine refresh.

UC source of a synced table:

- Additive change (new column): the next re-sync applies it. No recreate.
- Any other change (type change, rename, removed column, new primary key):
  `uv run --with pyyaml python lakebase/run.py recreate-synced-table --table <name>`.
  This deletes the synced table, drops the Postgres table, creates it again, rebuilds
  the corpus indexes if it is the corpus, and re-grants SELECT. Re-grant runs only on
  this path and on first creation.

### Event backbone

1. Put the four Kafka secrets into scope `fe-bar-aiven-kafka` (`services/README.md`).
2. `cd services && databricks bundle deploy -t prod --profile fe-bar`
3. `databricks bundle run migrate -t prod --profile fe-bar` (once).
4. The hourly schedules then run producer -> worker -> relay -> consumers. To drive it
   by hand, `bundle run` them in that order.

The worker writes RECOMMENDED adjudications only. The `claim.adjudicated` outbox row
is written only by the app when an adjuster finalizes, so the relay and consumers run
and exit idle until then. Before step 1, every scheduled services run fails at Kafka
setup; pause the triggers if that noise is unwanted.

## Full refresh vs routine refresh

Routine refresh is incremental. Inserts, updates, and deletes in Lakebase flow
through native CDF into `cdf.lb_*_history`, and AUTO CDC applies them to the SCD2
silver histories as new versions. Nothing is re-snapshotted and existing history is
kept. This is the normal path and it is always safe to run.

A full refresh is required only after a schema change on a Lakebase source table.
Native CDF re-snapshots the changed table, which rewrites its CDF landing table. The
medallion reads that table as a streaming source, and a streaming source must be
append-only, so the next normal update fails. That failure is by design: it stops the
pipeline from silently mixing two snapshots. The operator then runs one full refresh
of the medallion, which resets its streaming tables and rebuilds them from the
current CDF snapshot. The SCD2 history is rebuilt from that snapshot, so earlier
versions of the rows are not preserved. Run the routine refresh afterwards.

Never use a full refresh to fix an ordinary failure. Check the pipeline event log
first.

## Precedent corpus cutover

The precedent corpus used to be the native Lakebase table `public.prior_claims`. It is
now `gold.prior_claims_corpus`, served down as `reference.prior_claims_corpus`. The
agent code and the app read the new table. The live serving endpoint keeps reading
`public.prior_claims` until the next `register -> evaluate -> promote -> deploy` cycle
packages the new `retrieval.py`. After that cycle, drop the legacy table:
`DROP TABLE public.prior_claims;` (fresh setups no longer create it).
