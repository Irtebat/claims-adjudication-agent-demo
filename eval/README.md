# Claims-adjudication offline evaluation

This package builds a leakage-free held-out evaluation dataset from the SCD2 gold
claim and finalized adjudication histories, runs the in-process `ResponsesAgent`
with persistence disabled, and records deterministic release metrics plus optional
diagnostic LLM-judge metrics in MLflow.

## Local gates

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
```

Tests use synthetic fixtures and make no live calls.

## Live evaluation

```bash
export DATABRICKS_CONFIG_PROFILE=fe-bar LAKEBASE_PROFILE=fe-bar
uv run python src/evaluate.py --profile fe-bar \
  --experiment /Shared/claims-adjudication-offline-evaluation
```

The run starts with a 10-record pilot and only runs the 30-record judge tier when
projected total LLM spend is at most USD 25. The 75-record exact tier always runs.
The dataset is experiment-scoped and MLflow-managed. Databricks gives that
managed dataset a UC backing identifier; the harness does not create or manage a
separate duplicate table. Evidence is aggregate/redacted and contains no claim
content or PII.

## Manual DAB job

The job is intentionally unscheduled and parameterized by dataset version, model
URI, judge endpoint, scorer tier, and experiment.

```bash
databricks bundle validate --strict -t prod --profile fe-bar
```
