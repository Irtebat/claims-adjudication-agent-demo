# Synthetic claims data, pipeline, and governance

Builds a reproducible steel claims dataset in the catalog configured in
`settings.yaml`. This uses the `databricks` mapping from
`../config/config.example.yaml`; credentials stay in CLI authentication.
The CLI wrapper always supplies the configured profile explicitly.

The serverless generator writes Parquet to
`/Volumes/<catalog>/bronze/raw_landing`. The one-time baseline seed reads claims
and adjudications directly from those files, then native Lakebase CDF snapshots
the operational tables into `cdf.lb_*_history`. AUTO CDC incrementally maintains
the two silver SCD Type 2 histories; gold current views filter on
`__END_AT IS NULL`, while gold history views retain the full timeline for
evaluation. The other serve-down tables continue to stream immutable Parquet
files from bronze's landing volume using Auto Loader. Policy standards and coating-warranty terms are **not**
produced here: they are authored in `agent/src/policy_source.json` and loaded
directly into Lakebase by the policy intake (`agent/src/policy_intake.py`).

The default bundle target is `prod` (production mode), profile `fe-bar`, catalog
`fe-bar-ir`. Its workspace root is `/Workspace/Users/irtebat.shaukat@databricks.com/.bundle/steel-claims/prod`.
Run only `databricks bundle run medallion --profile fe-bar` from `pipelines/` to
consume already-landed files. A bootstrap rerun replaces the raw synthetic snapshot;
follow it with a full pipeline refresh to reset streaming checkpoints. Do not use
this bootstrap job against live data.

| Curated dataset | Meaning |
| --- | --- |
| silver.heats_coils | Coil/heat, ordered and measured dimensions, coating, dates, supplier lots, customer and price |
| silver.mill_test_certs | Chemistry, mechanical measurements and coating adhesion result |
| silver.customers / suppliers / defect_codes | Synthetic entities and defect taxonomy |
| silver.claims_history / adjudications_history | Validated historical facts |
| gold.claims_current / adjudications_current | Current operational claims and decisions |
| gold.claims_history / adjudications_history | Full SCD2 timelines for evaluation/audit |

Policy standards and coating-warranty terms are authored in
`agent/src/policy_source.json` and loaded into Lakebase by the policy intake, as
both the structured params the deterministic authorities read and the citable
text clauses (`agent/README.md`). This generator only synthesizes the reference,
master and history fact data above. The synthetic historical adjudications apply
the warranty version schedule (effective windows + durations) **sourced from the
authored policy** — `run.py` reads `agent/src/policy_source.json` and passes it to
`generate.py` as a job parameter, so an edit to the policy propagates into the
generated history and no policy numbers are hardcoded here. The authoritative live
coverage/settlement math lives in the `compute_*` authorities, never in this
generator. Money is stored as decimal values; a partial approval has a smaller
approved amount; denials and investigations have zero approved amount and no
disposition.

Finalized adjudications record the injected pattern; claims carry no label. Each complete
100-claim block contains 20 clean claims, 20 in-spec denials, 15 warranty or
exclusion denials, 10 duplicates, 15 over-claims, 15 supplier-attributable
claims and a five-claim fraud cluster. Clean claims include covered corrosion
and actual tensile deviations. Duplicates reuse a prior claim's coil, customer,
defect, tonnage, value and narrative within one day. Supplier cases share a
coating lot and failed adhesion; fraud clusters share a heat and supplier lot
across three customer identities. These labels are evaluation data, not inputs
for a later production decision tool.

## Run

Requires authenticated Databricks CLI >=1.0, `uv`, an accessible UC managed
storage root, and permission to create schemas, volumes and account groups.
All compute is serverless. There is no schedule.

From the repository root:

```bash
uv run --with pyyaml python pipelines/run.py validate
uv run --with pyyaml python pipelines/run.py deploy
uv run --with pyyaml python pipelines/run.py run
uv run --with pyyaml python pipelines/run.py govern
uv run --with pyyaml python pipelines/run.py evidence
```

The wrapper runs `databricks bundle validate --strict`, deploys the bundles, and
orchestrates raw generation, one-time Lakebase seeding, CDF creation/readiness,
then the triggered AUTO CDC pipeline, always passing the configured profile.
It refuses to reseed after a CDF config exists, because fixture replacement would
emit artificial deletes/inserts and create spurious SCD2 versions.
Set `synthetic.claim_count` once in `settings.yaml` to scale to 20,000–50,000,
or deploy with `--claim-count 20000`. The minimum is 100 so every pattern is
present. Seed controls Faker identities; the fact pattern allocation is fixed.
`--config path/to/config.yaml` selects a different file with the same mapping.
Run `summary` for deployed resource links.

After any code change, deploy before running. `check-generator` runs the same
Spark/Faker generator on serverless against temporary views, checking all
column expressions without writing raw files. It is a diagnostic check, not a
successful data load. The bootstrap run must still succeed.

`governance.sql` is rendered with the configured catalog by `govern`. It creates
account groups only when absent and never enrolls users. Both human roles get
SELECT on curated tables. Adjusters and workspace admins can see identifiers
and money; other readers get stable hashed identifiers and NULL prices/amounts.
Bronze receives the same masks, but no role receives bronze or volume access.
App/agent SP grants are commented placeholders for future components. Reapply
`govern` after a rebuild and verify the masks before granting any new access.
End-to-end verification with a real nonprivileged user requires that user's
membership and authenticated session; owner-level metadata inspection alone
does not prove their effective access.

Evidence capture writes SQL, UTC capture time, real result rows, role grants,
masks, samples and relationship/pattern assertions to
`docs/evidence/synthetic-data/`. It fails if any integrity check finds a violation.
Do not treat planned row counts or temporary-view checks as persisted data.

## Development checks

```bash
uv run --with ruff ruff check pipelines
uv run --with ruff ruff format --check pipelines
uv run --with mypy --with types-PyYAML mypy --config-file pipelines/pyproject.toml pipelines/run.py pipelines/evidence.py pipelines/src/checks.py
python3 -m compileall -q pipelines
```

Mypy covers local orchestration. Spark expressions are analyzed and executed by
the serverless generator and Lakeflow; pipeline expectations fail on invalid
keys or economic invariants. The policy intake, retrieval, deterministic
authorities and fraud-graph risk job live under `agent/` and Lakebase.
