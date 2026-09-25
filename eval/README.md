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

## Known limitations

- `retrieval_groundedness` is **not yet measurable**. The agent's RETRIEVER
  spans expose cited clause IDs as attributes but do not emit retrieved clause
  text as span outputs, so the judge has no content against which to assess
  grounding. The existing `0.0` is an instrumentation limitation, not evidence
  of a grounding regression. Agent-side follow-up: enrich RETRIEVER span outputs
  with clause text. `authority_guidelines` remains a valid diagnostic.
- The resolver oracle reuses the agent's frozen deterministic resolution, but
  currently reconstructs `oracle_clause_ids` from a hardcoded section vocabulary
  instead of the agent citation-key builder. It aligns today and is fragile if
  that key format changes.
- The unscheduled DAB job is an attended/local reproducibility template: it
  hardcodes `--profile fe-bar`, and `evaluate.py` enforces that CLI profile.
  Serverless job runtimes do not carry local CLI profiles, so the verified path
  is the local `uv run python src/evaluate.py --profile fe-bar` command above.

## Manual DAB job

The job is intentionally unscheduled and parameterized by dataset version, model
URI, judge endpoint, scorer tier, and experiment.

```bash
databricks bundle validate --strict -t prod --profile fe-bar
```
