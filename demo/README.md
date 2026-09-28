# Steel Claims Demo Backlog Job

A Databricks Asset Bundle (DABs) job that generates N fresh synthetic claims and runs them through the adjudication agent to produce RECOMMENDED adjudications.

## What It Does

1. **Generates N synthetic claims** consistent with the label-pattern distribution used by `pipelines/src/generate.py`
2. **Inserts claims into Lakebase** `public.claims` with `data_provenance='synthetic_demo_backlog'` for easy cleanup
3. **Runs adjudication** via the governed serving endpoint or in-process to produce RECOMMENDED adjudications
4. **Populates the adjuster queue** with claims that have RECOMMENDED adjudications ready for human review

## Parameters

The job accepts the following parameters (all have sensible defaults):

- **count** (default: 500): Number of synthetic claims to generate (minimum 100)
- **seed** (default: 42): Random seed for deterministic generation
- **mode** (default: serving_endpoint): Adjudication mode
  - `serving_endpoint`: Use the governed serving endpoint (preferred for isolation)
  - `in_process`: Import and run the agent in-process (requires agent module)
- **catalog** (default: fe-bar-ir): Unity Catalog
- **endpoint** (default: projects/.../endpoints/primary): Lakebase endpoint
- **postgres_database** (default: databricks_postgres): Postgres database name

## Distribution Consistency

The generator strictly follows the label-pattern distribution from `pipelines/src/generate.py`:

Per 100 claims:
- **20%** (0–19): clean
- **20%** (20–39): in_spec_should_deny
- **15%** (40–54): out_of_warranty_or_environment_excluded
- **10%** (55–64): duplicate
- **15%** (65–79): over_claim
- **15%** (80–94): supplier_attributable
- **5%** (95–99): fraud_cluster

This ensures the demo backlog maintains the same realistic claim distribution as the main synthetic baseline.

## Deployment

Deploy to Databricks:

```bash
cd demo/
databricks bundle deploy -t prod --profile fe-bar
```

Run the job with custom parameters:

```bash
databricks bundle run demo_backlog -t prod --profile fe-bar -- --count 1000 --seed 123 --mode serving_endpoint
```

Or run with all defaults (500 claims, seed 42, serving endpoint):

```bash
databricks bundle run demo_backlog -t prod --profile fe-bar
```

## Recommendation Contract (Wave 7)

The adjudications produced are **RECOMMENDED** (not yet FINAL):

- `decision_status = 'RECOMMENDED'`
- NO `public.outbox` row is written
- Claims with RECOMMENDED adjudications populate the adjuster queue
- Human adjuster finalizes in the Databricks App, which then:
  - Marks `decision_status = 'FINAL'`
  - Writes the `claim.adjudicated` outbox row
  - Triggers event fan-out to downstream systems

This is distinct from the seeded baseline (`synthetic_wave_2_baseline`) which has `decision_status = 'FINAL'` and was pre-adjudicated at setup time.

## Data Provenance

All inserted claims and adjudications carry `data_provenance = 'synthetic_demo_backlog'`, making them trivial to identify and clean up:

```sql
-- Find demo claims
SELECT COUNT(*) FROM claims WHERE data_provenance = 'synthetic_demo_backlog';

-- Find demo adjudications
SELECT COUNT(*) FROM adjudications WHERE data_provenance = 'agent_recommendation' AND claim_id IN (
  SELECT claim_id FROM claims WHERE data_provenance = 'synthetic_demo_backlog'
);

-- Clean up (if needed)
DELETE FROM adjudications WHERE claim_id IN (
  SELECT claim_id FROM claims WHERE data_provenance = 'synthetic_demo_backlog'
);
DELETE FROM claims WHERE data_provenance = 'synthetic_demo_backlog';
```

## Files

- **databricks.yml**: DABs bundle configuration
- **src/generator_core.py**: Pure, importable claims generator (no Spark/Lakebase required)
- **src/runner.py**: Notebook entry point (Databricks job)
- **tests/test_generator_core.py**: Comprehensive unit tests for the generator
- **pyproject.toml**: Ruff linting configuration

## Testing Locally

Run unit tests (no Spark/Databricks account needed):

```bash
uv run --project eval pytest demo/tests -q
```

Check formatting and linting:

```bash
uv run --with ruff ruff check demo
uv run --with ruff ruff format --check demo
```

Apply formatting fixes:

```bash
uv run --with ruff ruff format demo
```

## Example Output

When deployed and run, the job produces a JSON summary:

```json
{
  "catalog": "fe-bar-ir",
  "count": 500,
  "seed": 42,
  "mode": "serving_endpoint",
  "data_provenance": "synthetic_demo_backlog",
  "inserted_claims": 500,
  "adjudicated": 500,
  "failed": 0,
  "queue_count": 500,
  "timestamp": "2026-09-28T15:42:30.123456"
}
```

## Troubleshooting

- **"IP-ACL error" from serving endpoint**: The job runs serverless and may hit IP-ACL restrictions. Contact your workspace admin.
- **"In-process mode: cannot import agent"**: The agent module may not be installed or on the path. Use serving_endpoint mode instead.
- **Blank adjudications**: Verify the serving endpoint is deployed and accepts `custom_inputs.claim`.
- **403 errors on outbox**: This is expected — the recommendation write does NOT create outbox rows per Wave 7 contract.
