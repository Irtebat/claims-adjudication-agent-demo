# scripts — cross-layer composers

`bootstrap.py` composes the one-time fresh-workspace bring-up; `refresh.py` composes
the routine and demo refreshes. Both apply the repo's execution model directly and
are the paths referenced by `docs/RUNBOOK.md`.

## Execution model

Two mechanisms only (see the repo-root README). The composers apply the rule
directly — they do **not** shell through per-layer `run.py` shims for plain bundle
operations:

1. **DABs bundle for compute, invoked directly.** Plain deploys and job runs are
   `databricks bundle deploy|run --target prod --profile fe-bar` in the owning layer
   directory (`pipelines/`, `agent/`, `demo/`).
2. **Direct `uv run python` only for genuine orchestration/guards.**
   - `pipelines/run.py generate` — injects the authored warranty schedule
     (`lakebase/src/policy_source.json`) into the generator.
   - `lakebase/run.py setup-and-seed` — the reseed guard (refuses once native CDF
     exists) + `bundle run setup_and_seed` + the non-bundle `policy_intake.py` step.
   - `lakebase/run.py create-cdf` — the exists guard + provision + poll to
     `CDF_STATE_STREAMING`.
   - `pipelines/run.py refresh` — resolves every dynamic native-CDF table name
     (claims, adjudications, decision records), then deploys and runs
     `refresh_medallion`.
   - `lakebase/run.py resync-synced-tables` — resolves each synced table's managed
     sync pipeline and runs an incremental update of it (never a re-grant).

## Bootstrap (fresh workspace only)

```bash
uv run --with pyyaml python scripts/bootstrap.py
```

Order (enforced by `bootstrap.py`): pipelines `bundle deploy` -> `generate` ->
Lakebase `setup-and-seed` -> `create-cdf` -> agent `bundle deploy` -> `fraud_graph`
(publishes an empty typed `gold.customer_heat_risk`, because no
`gold.claims_current` exists yet) -> `pipelines/run.py refresh` -> `fraud_graph`
(real scores) -> `prior_claims_corpus` -> `pipelines/run.py refresh` (gold fact picks
up the risk scores). Synced tables are created afterwards with
`lakebase/run.py synced-tables`, once the app and serving service principals exist
(the create path re-grants to them).

The CDF-exists guard lives in the Lakebase steps, so it is checked only after the
pipelines `bundle deploy` and `generate` steps have already run. If a CDF config
already exists, `setup-and-seed` refuses before the Lakebase bundle deploy and before
any seed, and the bootstrap stops there, because replacing the fixture would emit
artificial deletes/inserts and create spurious SCD2 versions. The earlier pipelines
deploy and `generate` are not rolled back. Do not run the bootstrap against live
data.

## Refresh (routine and demo)

```bash
uv run --with pyyaml python scripts/refresh.py routine
uv run --with pyyaml python scripts/refresh.py demo
```

`routine`: `pipelines/run.py refresh` (incremental, all CDF tables) -> agent
`bundle deploy` -> `fraud_graph` -> `prior_claims_corpus` ->
`lakebase/run.py resync-synced-tables` (`customer_heat_risk`, `prior_claims_corpus`)
-> `pipelines/run.py refresh` (so gold picks up the new risk scores). No full
refresh, no reseed, no synced-table recreate, no re-grant.

`demo`: demo `bundle deploy` -> `demo_backlog` (new synthetic claims + RECOMMENDED
adjudications) -> the routine steps.

Each step runs with `check=True`; the first failure stops the composer.

## Checks

```bash
uv run --with ruff ruff check scripts
uv run --with ruff ruff format --check scripts
uv run --with pytest --with pyyaml pytest -q pipelines/tests   # bootstrap + refresh composer tests
```
