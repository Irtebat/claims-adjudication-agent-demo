# Claims-adjudication offline evaluation

This package builds a leakage-free held-out evaluation dataset from the SCD2 gold
claim and finalized adjudication histories, loads and runs the packaged `ResponsesAgent`
with persistence disabled, and records deterministic release metrics plus optional
diagnostic LLM-judge metrics in MLflow.

## System of record

**The MLflow run and the metrics logged to it are the authoritative record of an
evaluation.** `mlflow.genai.evaluate()` logs the release-gate metric means
(`<scorer>/mean`) to the run, and the run carries the `candidate_version`,
`git_sha`, and `release_gate_passed` tags. Everything written under
`eval/evidence/` is a **regenerated convenience snapshot** for local review and PR
evidence — it is overwritten on each run and is not canonical. See
`eval/evidence/README.md`. Trust the MLflow run when the two disagree.

## Ordered register → evaluate → promote lifecycle

Run these steps in order, carrying the newly registered version number (`N`) into
evaluation and promotion:

```bash
# 1. Log, isolated-validate, register version N, and set @candidate (never @prod).
cd agent
DATABRICKS_CONFIG_PROFILE=fe-bar uv run python src/register_agent.py --profile fe-bar

# 2. Independently score the packaged version N in version-named MLflow runs.
cd ../eval
DATABRICKS_CONFIG_PROFILE=fe-bar LAKEBASE_PROFILE=fe-bar \
  MLFLOW_GENAI_EVAL_MAX_WORKERS=5 uv run python src/evaluate.py \
  --profile fe-bar --experiment /Shared/claims-adjudication-offline-evaluation \
  --candidate-version N

# 3a. Gate-only dry run: bootstrap if @prod is absent, otherwise compare against @prod.
uv run python src/promote.py --profile fe-bar --candidate-version N

# 3b. After approval, make promote.py—the sole @prod owner—apply the passing decision.
uv run python src/promote.py --profile fe-bar --candidate-version N --promote
```

Each invocation of `evaluate.py` is independent: skipped tiers are absent, and no
prior convenience JSON is read or backfilled. `--candidate-version` is required.
The prediction adapter loads `models:/fe-bar-ir.default.claims_adjudication_agent/N`
with `mlflow.pyfunc.load_model` and invokes the packaged artifact with
`custom_inputs.persist=false`; it fails closed unless the artifact reports
`write_result.persisted=false`.

## Versioned candidate → compare-vs-@prod → alias promotion

Evaluation is a versioned, comparable, governed process, not a local-JSON record:

- **Version linkage.** Each eval run is tagged with `candidate_version` — the UC
  registered-model version of `fe-bar-ir.default.claims_adjudication_agent` under
  test. It is passed with the required `--candidate-version`, stamped on every run tag, and also
  exported as `AGENT_MODEL_VERSION` so the agent records the same version on its
  decision records and trace attributes. The run — and the traces it produces —
  therefore reference the exact version being scored. (MLflow 3 also offers
  `mlflow.set_active_model(name=..., model_id=...)` to bind traces to a *logged*
  model id; the `candidate_version` run tag is used here because it names the UC
  registry version directly and needs no logged-model id.)
- **Promotion is separate, explicit, and gated.** Running an eval never moves the
  `@prod` alias. Promotion is a deliberate second step (`src/promote.py`):

  ```bash
  # Dry-run report (default): compare candidate vs current @prod, move nothing.
  uv run python src/promote.py --profile fe-bar --candidate-version 3

  # Actually move @prod to the candidate — only if it wins the gate.
  uv run python src/promote.py --profile fe-bar --candidate-version 3 --promote
  ```

  `promote_if_beats_prod` resolves the current `@prod` version when present
  (`get_model_version_by_alias`), pulls both versions' release-gate metrics from
  their eval runs via `mlflow.search_runs`, and moves the alias **only if the
  candidate wins the gate**. If no `@prod` exists, bootstrap requires the five
  money-safety metrics (`no_payable_duplicate`, `amount_matches_authority`,
  `amount_matches_gold`, `verdict_matches_eligibility`, `invariant_clean`) to be
  finite, in range, and at least `1.0` with no epsilon slack:

  1. every **money-safety invariant** passes its absolute release threshold
     (`no_payable_duplicate`, `amount_matches_authority`,
     `verdict_matches_eligibility`, `citations_in_resolved_policy` — all `1.0`), **and**
  2. the **verdict / disposition / amount** metrics (`verdict_exact_match`,
     `disposition_exact_match`, `amount_matches_gold`) are **not worse** than
     current `@prod`.

  If it does not win, the alias is left unchanged and the reason is reported.
  Both the candidate and the current `@prod` version must each have an `exact`-tier
  eval run tagged with their version (the tool falls back to the legacy
  `agent_model_version` tag for runs predating `candidate_version`).

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
  --experiment /Shared/claims-adjudication-offline-evaluation \
  --candidate-version 3
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

The job is intentionally unscheduled and parameterized by dataset version,
candidate version, judge endpoint, scorer tier, and experiment. It passes
`--candidate-version` (not `--model-uri`), so `evaluate.py` derives the pinned
`models:/<name>/<candidate_version>` URI and the run provably scores that exact
version.

```bash
databricks bundle validate --strict -t prod --profile fe-bar
```
