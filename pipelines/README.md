# Synthetic claims data, pipeline, and governance

Builds a reproducible steel claims dataset in the catalog configured in
`settings.yaml`. This uses the `databricks` mapping from
`../config/config.example.yaml`; credentials stay in CLI authentication.
The CLI wrapper always supplies the configured profile explicitly.

The serverless bootstrap job writes Parquet to
`/Volumes/<catalog>/bronze/raw_landing`. A triggered Lakeflow declarative
pipeline creates nine bronze materialized views, seven silver streaming tables
with `delta.enableChangeDataFeed=true`, two silver history materialized views,
and two gold history materialized views. The serve-down tables stream immutable
Parquet files from bronze's landing volume using Auto Loader, preserving the
structured parameters and citable clause text. No joins or aggregations are
needed. Gold history is for evaluation/analytics; live claims originate in Lakebase.

The default bundle target is `prod` (production mode), profile `fe-bar`, catalog
`fe-bar-ir`. Its workspace root is `/Workspace/Users/irtebat.shaukat@databricks.com/.bundle/steel-claims/prod`.
Run only `databricks bundle run medallion --profile fe-bar` from `pipelines/` to
consume already-landed files. A bootstrap rerun replaces the raw synthetic snapshot;
follow it with a full pipeline refresh to reset streaming checkpoints. Do not use
this bootstrap job against live data.

| Curated dataset | Meaning |
| --- | --- |
| silver.spec_standards | Chemistry/mechanical ranges, dimensional tolerances and coating thresholds, plus citable clauses |
| silver.coating_warranty_terms | Versioned duration, thresholds, exclusions and proration, plus citable clauses |
| silver.heats_coils | Coil/heat, ordered and measured dimensions, coating, dates, supplier lots, customer and price |
| silver.mill_test_certs | Chemistry, mechanical measurements and coating adhesion result |
| silver.customers / suppliers / defect_codes | Synthetic entities and defect taxonomy |
| silver.claims_history / adjudications_history | Validated historical facts |
| gold.claims_history / adjudications_history | Published claims and final labeled decisions |

`src/policy_source.json` is the sole authored policy source. `policies.py`
parses it once into a typed `structured_params` struct and clause text. Every
row has a stable `clause_id`, `parent_clause_id`, section, source hash and
corpus-specific metadata. Parameters repeat identically across a parent's
clauses; use `section_ref = 'coverage'` for one warranty row or
`section_ref = 'mechanical'` for one standard row when joining decision tools.
A vector column can be added to these clause rows later. None exists now.
These are illustrative policies, not licensed or authoritative ASTM/EN limits.

Warranty windows are **[effective_from, effective_to)** and selected using
shipment date, not claim date. Duration starts at shipment. Proration uses
completed months: full coverage through `full_coverage_months`, then
`max(0, (duration_months - completed_months) /
(duration_months - full_coverage_months))`. The final month has zero coverage.
Warranty freight is excluded. Material claims cap freight at `freight_cap`.
Money is stored as decimal values. A partial approval has a smaller approved
amount; denials and investigations have zero approved amount and no disposition.

The `ground_truth_label` column records the injected pattern. Each complete
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

The wrapper runs `databricks bundle validate --strict`, `bundle deploy`, and
`bundle run bootstrap`, passing `--profile` and bundle variables from config.
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

Evidence capture writes SQL, UTC capture time, real result rows, policy schemas,
role grants, masks, samples and relationship/pattern assertions to
`docs/evidence/synthetic-data/`. It fails if any integrity check finds a violation.
Do not treat planned row counts or temporary-view checks as persisted data.

## Development checks

```bash
uv run --with pytest pytest pipelines/tests/test_policies.py -q
uv run --with ruff ruff check pipelines
uv run --with ruff ruff format --check pipelines
uv run --with mypy --with types-PyYAML mypy --config-file pipelines/pyproject.toml pipelines/run.py pipelines/evidence.py pipelines/src/policies.py pipelines/src/checks.py
python3 -m compileall -q pipelines
```

Mypy covers local orchestration and policy parsing. Spark expressions are
analyzed and executed by the serverless generator and Lakeflow; pipeline
expectations fail on invalid keys or economic invariants. Optional process
telemetry and downstream risk, KPI, embedding and Lakebase jobs are deferred.
