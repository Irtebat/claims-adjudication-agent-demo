# Evidence — Wave 7 finalization-columns migration + full-refresh (Task 1)

Live against the `fe-bar` profile. Adds the human-finalization metadata columns and
re-materializes the medallion cleanly through native Lakebase CDF.

> Superseded (refresh-and-prior-claims): the one-off migration script referenced below
> was deleted after it had been applied here. It only ran the additive `ADD COLUMN IF
> NOT EXISTS` statements; the columns are now part of the day-1 `CREATE TABLE` DDL in
> `lakebase/src/setup_and_seed.py`. See `docs/RUNBOOK.md` for the schema-change run
> order.

## Schema change (committed code)

`lakebase/src/setup_and_seed.py` (idempotent, `ADD COLUMN IF NOT EXISTS`):

- `public.adjudications`: `decided_by text`, `override_reason text`
- `public.adjudication_decision_records`: `decided_by text`, `override_reason text`

## Live application (not a `setup_and_seed` rerun)

Per `docs/evidence/pipelines-lakebase-cleanup/POST-MERGE-LIVE-STEPS.md` ("Do not
rerun `setup_and_seed` after CDF is active"), the ALTERs were applied to live Lakebase
over psycopg (port 5432, SDK OAuth, `sslmode=require`) by the targeted, idempotent
script `lakebase/scripts/apply_finalization_columns.py`:

```
uv run --project eval python lakebase/scripts/apply_finalization_columns.py --profile fe-bar
```

Result — all four columns verified present as `text` on both tables:

| table | column |
| --- | --- |
| public.adjudications | decided_by |
| public.adjudications | override_reason |
| public.adjudication_decision_records | decided_by |
| public.adjudication_decision_records | override_reason |

## CDF re-snapshot + pipeline full-refresh (AUTHORIZED, synthetic data)

Both columns are captured by native Lakebase CDF, so the ALTER triggered a one-time
re-snapshot. Full-refresh of the `steel-claims` medallion pipeline
(`495e25a1-d984-4993-8a07-7b9ba7c33467`):

```
databricks pipelines start-update 495e25a1-d984-4993-8a07-7b9ba7c33467 --full-refresh --profile fe-bar
# update_id c874aab5-15a3-4534-85b4-fb7019716528 -> state COMPLETED
```

The pipeline's `expect_all_or_fail` money/business invariants held (the update would
have FAILED otherwise), so CDF streaming returned to normal.

## New columns propagated to UC (verified)

`decided_by` and `override_reason` present in:

- `fe-bar-ir.silver.adjudications_history`
- `fe-bar-ir.gold.adjudications_history` (current view)
- `fe-bar-ir.gold.adjudication_decision_records`

The AUTO CDC flows pass new scalar columns through via `except_column_list`
(no explicit column-list edit was needed in the transformations).

## Re-materialized counts (post full-refresh)

| table | rows |
| --- | --- |
| `fe-bar-ir.silver.claims_history` | 5000 |
| `fe-bar-ir.silver.adjudications_history` | 5010 |
| `fe-bar-ir.gold.adjudication_decision_records` | 10 |

`gold.adjudication_decision_records` holds only agent-produced records (the seeded
baseline creates none), consistent with prior evidence. The full-refresh completed
cleanly — this is the normal case, not a benign-re-snapshot failure.
