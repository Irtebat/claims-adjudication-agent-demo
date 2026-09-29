# Execution consistency — DABs for compute, direct-python only for the MLflow lifecycle

Collapsed four inconsistent per-layer execution patterns into **two**, and stated the
rule in every touched README. No live workspace changes were made (validate is
read-only; no `deploy`/`run`). `agent/src/authorities.py` (deterministic money math)
is untouched — its diff vs `main` is empty.

## The two-mechanism rule (now stated in each touched README)

1. **DABs bundle — `databricks bundle deploy` + `bundle run <job>` — for anything
   that runs on Databricks compute.** Plain bundle ops are invoked directly, never
   wrapped.
2. **Direct `uv run python` only for (a) the human-gated MLflow lifecycle**
   (`agent` `register → evaluate → promote`, an attended operator loop) **and (b)
   thin wrappers adding real orchestration/guards a `bundle run` cannot express.**

## Before → after per layer

| Layer | Before | After |
| --- | --- | --- |
| `services`, `demo`, `app` | Pure bundle (`bundle deploy` + `bundle run`) | Unchanged — this was the model to standardize toward |
| `pipelines` | `run.py` with 10 actions, incl. pure `bundle` passthroughs (`validate`/`deploy`/`summary`) mixed with genuine orchestration | Passthroughs removed from `run.py` and documented as direct `databricks bundle …`; `run.py` keeps only genuine orchestration (`generate`, `check-generator`, `refresh`, `decision-records`, `preview-status`, `govern`, `evidence`), each justified in the README |
| `lakebase` | `run.py` guarded wrapper (kept) | Kept; README now states *why* it is a guarded wrapper (reseed guard, CDF-exists guard + poll-to-STREAMING, non-bundle `policy_intake.py` / synced-table re-grant) rather than a plain bundle run |
| `agent` | `register`/`evaluate`/`promote` direct-python + `fraud_graph`/`deploy_claims_agent` via bundle; rule/why unstated | Same commands; README now states the two-mechanism rule and *why* the lifecycle is the one direct-python exception (attended human-gated MLflow loop) |
| `eval` | Declared `claims_adjudication_eval` bundle job; README documented only direct `uv run python` | Bundle job **kept and documented** against the rule with an honest interactive-vs-bundle boundary |
| `scripts/bootstrap.py` | Composed via per-layer `run.py` shims incl. `pipelines/run.py deploy` | Composes by calling `databricks bundle deploy` **directly** for the plain deploy; keeps `run.py`/`lakebase/run.py` only for the genuine-orchestration steps; new `scripts/README.md` |

## Resolved eval-job decision

**Keep** `claims_adjudication_eval` and document it (it was declared-but-thinly-doc'd,
not silently orphaned). It runs the *same* `evaluate.py` from a parameterized job.
The honest boundary: the interactive/verified path is direct
`uv run python src/evaluate.py` (middle step of the human-gated lifecycle); the
bundle job is a **deliberately unscheduled, attended** `bundle run` for a
parameter-pinned re-run. It is not unattended serverless/CI today because
`evaluate.py` enforces the explicit `fe-bar` CLI profile as a money-safety guard
(`eval/src/evaluate.py:147`) and serverless runtimes carry no local CLI profile.
Making it unattended-CI would require lifting that guard — intentionally not done.

## Q2 doc-only addition (agent README, "Retrieval")

Verified against `agent/src/retrieval.py` + `agent/src/agent_tools.py`:
- Clause citation over `spec_clauses`/`warranty_clauses` is **metadata-resolved**:
  the parent policy is picked deterministically by resolution (spec by
  `(grade, spec_edition, region)`; warranty by `(product_line, coating_class,
  region)` + effective window at ship date), then `retrieve_policy_clauses`
  pre-filters to that parent and orders **BM25-only** (`clause_tsv <@> to_bm25query`);
  `embed_fn` is unused on that path. Not semantic; does not decide which policy applies.
- Genuine dense-vector + BM25 hybrid (RRF) exists **only** in the `prior_claims`
  precedent index (`find_similar_prior_claims`), which is advisory.

## Gates (all green)

- `git diff main -- agent/src/authorities.py` → **empty** (PASS).
- `databricks bundle validate --target prod --profile fe-bar` → **Validation OK!** for
  `pipelines` (`steel-claims`), `lakebase` (`fe-bar-lakebase`), `agent` (`fe-bar-agent`),
  `eval` (`fe-bar-agent-evaluation`). Also confirmed the documented direct
  `bundle validate --strict` and `bundle summary` for pipelines. Read-only; no deploy/run.
- Unit tests: `pipelines` 21 passed, `lakebase` 16 passed, `agent` 91 passed / 3 skipped,
  `eval` 48 passed.
- Ruff: `check` clean for pipelines/scripts/agent/eval/lakebase; `format --check` clean
  for pipelines/scripts/agent/eval.
- Pre-existing (NOT introduced here): `ruff format --check lakebase` flags 3 files
  (`scripts/apply_finalization_columns.py`, `scripts/regrant_synced_table_selects.py`,
  `tests/test_synced_table_regrant.py`) — this drift exists on `main`; this branch's
  only `lakebase` change is `README.md`, so they were left untouched to avoid
  out-of-scope churn.

See `gates.txt` for raw command outputs.
