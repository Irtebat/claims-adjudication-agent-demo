# scripts — end-to-end bootstrap composer

`bootstrap.py` composes the one-time pipelines/Lakebase bring-up in dependency
order. It is the guarded end-to-end path referenced by `pipelines/README.md` and
`lakebase/README.md`.

## Execution model

Two mechanisms only (see the repo-root README). The composer applies the rule
directly — it does **not** shell through per-layer `run.py` shims:

1. **DABs bundle for compute, invoked directly.** The plain pipelines deploy is a
   direct `databricks bundle deploy --target prod --profile fe-bar` (run in
   `pipelines/`), because it carries no logic a script needs to add.
2. **Direct `uv run python` only for genuine orchestration/guards.** The remaining
   steps stay wrapped because a plain `bundle run` cannot express them:
   - `pipelines/run.py generate` — injects the authored warranty schedule
     (`lakebase/src/policy_source.json`) into the generator.
   - `lakebase/run.py setup-and-seed` — the reseed guard (refuses once native CDF
     exists) + `bundle run setup_and_seed` + the non-bundle `policy_intake.py` step.
   - `lakebase/run.py create-cdf` — the exists guard + provision + poll to
     `CDF_STATE_STREAMING`.
   - `pipelines/run.py refresh` — resolves the dynamic native-CDF table names, then
     deploys and runs `refresh_medallion`.

## Run

```bash
uv run --with pyyaml python scripts/bootstrap.py
```

Order (enforced by `bootstrap.py`): pipelines `bundle deploy` → `generate` →
Lakebase `setup-and-seed` → `create-cdf` → pipelines `refresh`. The CDF-exists
guard lives in the Lakebase steps, so it is checked only after the pipelines
`bundle deploy` and `generate` steps have already run. If a CDF config already
exists, `setup-and-seed` refuses before the Lakebase bundle deploy and before any
seed, and the bootstrap stops there (no `create-cdf` or `refresh`), because
replacing the fixture would emit artificial deletes/inserts and create spurious SCD2
versions. The earlier pipelines deploy and `generate` are not rolled back. Do not run
the bootstrap against live data.

## Checks

```bash
uv run --with ruff ruff check scripts
uv run --with ruff ruff format --check scripts
```
