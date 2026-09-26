# Post-merge live steps

Run these only after the PR is merged and reviewed.

```bash
cd pipelines
databricks bundle deploy -t prod --profile fe-bar
OLD_JOB_ID="$(databricks jobs list --name steel-claims-process-cdf --profile fe-bar -o json | jq -r '.[0].job_id // empty')"; test -z "$OLD_JOB_ID" || databricks jobs delete "$OLD_JOB_ID" --profile fe-bar

cd ../lakebase
databricks bundle deploy -t prod --profile fe-bar
databricks experimental aitools tools query 'DROP TABLE IF EXISTS public.claims_pending' --profile fe-bar
```

The decision-record table and adjudications widening from the former standalone
migration are already applied live. Their merged idempotent DDL is therefore a no-op.
Do not rerun `setup_and_seed` after CDF is active.
