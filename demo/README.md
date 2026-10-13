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

## Prerequisites

- Lakebase seeded and the serving endpoint `READY` (`agent/README.md`) for the
  default `serving_endpoint` mode.

## Deploy

Working directory: `demo/`.

```bash
databricks bundle validate --strict -t prod --profile fe-bar-ir-2026
databricks bundle deploy -t prod --profile fe-bar-ir-2026
```

## Run

Preferred, from the repository root (deploys and runs this job with defaults, then
the routine refresh):

```bash
uv run --with pyyaml python scripts/refresh.py demo
```

Direct run, from `demo/` (job parameters map from the `--` flags):

```bash
databricks bundle run demo_backlog -t prod --profile fe-bar-ir-2026                  # 500 claims, seed 42
databricks bundle run demo_backlog -t prod --profile fe-bar-ir-2026 -- --count 1000 --seed 123 --mode serving_endpoint
```

After a direct run, run `scripts/refresh.py routine` (not `demo`, which would run
the job again) to carry the new rows into gold and the synced tables.

## Verify

Read-only:

```bash
databricks jobs list --name steel-claims-demo-backlog --profile fe-bar-ir-2026 -o json | jq '.[0].job_id'
databricks jobs list-runs --job-id <job-id> --limit 3 --profile fe-bar-ir-2026 -o json \
  | jq '.[] | {run_id, state: .status.state, result: .status.termination_details.code}'
```

Expected: the latest run is `TERMINATED` / `SUCCESS`. Its notebook output is the
JSON summary under **Example output**; the counts are also visible in the
adjuster queue (`GET /api/queue` in the app) and with the provenance queries below.

## Recommendation contract

The adjudications produced are **RECOMMENDED** (not yet FINAL):

- `decision_status = 'RECOMMENDED'`
- NO `public.outbox` row is written
- Claims with RECOMMENDED adjudications populate the adjuster queue
- Human adjuster finalizes in the Databricks App, which then:
  - Marks `decision_status = 'FINAL'`
  - Writes the `claim.adjudicated` outbox row
  - Triggers event fan-out to downstream systems

This is distinct from the seeded baseline (`synthetic_reference_baseline`) which has `decision_status = 'FINAL'` and was pre-adjudicated at setup time.

## Data Provenance

All inserted claims and adjudications carry `data_provenance = 'synthetic_demo_backlog'`, making them trivial to identify and clean up:

```sql
-- Find demo claims
SELECT COUNT(*) FROM public.claims WHERE data_provenance = 'synthetic_demo_backlog';

-- Find demo adjudications
SELECT COUNT(*) FROM public.adjudications WHERE data_provenance = 'agent_recommendation' AND claim_id IN (
  SELECT claim_id FROM public.claims WHERE data_provenance = 'synthetic_demo_backlog'
);
```

Cleanup is destructive: native CDF carries the deletes into the SCD2 history. Run it
as a Lakebase admin (decision records revoke `DELETE` from `PUBLIC`) over
`databricks psql --project fe-bar-operational-plane --profile fe-bar-ir-2026 -- -d databricks_postgres`:

```sql
BEGIN;
DELETE FROM public.adjudication_decision_records WHERE adjudication_id IN (
  SELECT adjudication_id FROM public.adjudications WHERE claim_id IN (
    SELECT claim_id FROM public.claims WHERE data_provenance = 'synthetic_demo_backlog'));
DELETE FROM public.adjudications WHERE claim_id IN (
  SELECT claim_id FROM public.claims WHERE data_provenance = 'synthetic_demo_backlog');
DELETE FROM public.claims WHERE data_provenance = 'synthetic_demo_backlog';
COMMIT;
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

## Example output

When deployed and run, the job produces a JSON summary of this **shape** (illustrative
— not a captured run result; the counts depend on `count`):

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
- **403 errors on outbox**: Expected — the recommendation write does NOT create outbox rows; the app finalization writes them.
