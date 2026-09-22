# Synthetic claims data execution evidence

Live evidence for bundle `steel-claims`, target `prod`, profile `fe-bar`, catalog `fe-bar-ir`.
Workspace root: `/Workspace/Users/irtebat.shaukat@databricks.com/.bundle/steel-claims/prod`.

The pipeline consumes the existing immutable Parquet snapshot in the bronze landing volume.
Query artifacts record capture time, SQL, and live results. Governance verification uses
the owner session and checks metadata; it does not impersonate role members.

Pipeline `495e25a1-d984-4993-8a07-7b9ba7c33467` completed update
`4d8e0f86-ca16-4dfd-a70e-5d6f0b4ffa95`. Exactly one update was started.
The source-data bootstrap was not rerun.

`serve-down-table-status.json` records live table types, CDF settings, columns,
and ownership by this pipeline. The seven silver serve-down tables are
STREAMING_TABLE with CDF enabled; both gold history tables are MATERIALIZED_VIEW.
The silver columns match the previous schema, including the separate
`structured_params` and `clause_text` policy representations.

`row-counts.json` covers all 20 published datasets. `label-distribution.json`
records the seven injected patterns. `integrity-checks.json` contains 14 checks.
`grants.json` and `column-masks.json` record the two human roles' SELECT grants
and sensitive-column masks; catalog/schema grants are recorded separately.

Local verification (repository root):

```bash
uv run --with pytest pytest --collect-only -q
uv run --with pytest pytest -q
uv run --with ruff ruff check pipelines
uv run --with ruff ruff format --check pipelines
uv run --with mypy --with types-PyYAML mypy --config-file pipelines/pyproject.toml pipelines/run.py pipelines/evidence.py pipelines/src/policies.py pipelines/src/checks.py
uv run --no-project python -m compileall -q pipelines
```

Three test cases from three test functions passed. Ruff checked all Python files
under `pipelines/`; 27 files were already formatted. Mypy checked the four
explicit files above. Compilation completed successfully for `pipelines/`.
