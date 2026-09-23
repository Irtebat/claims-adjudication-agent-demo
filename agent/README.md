# agent

Policy intake, the deterministic adjudication authorities, hybrid clause
retrieval, duplicate detection, and the fraud-graph risk job. **Deterministic
tools decide money; the LLM only informs and cites** (PLAN §3, §6). The Mosaic AI
`ResponsesAgent` that orchestrates these tools is a later workstream; this package
delivers the tools and the policy data plane they read.

## Single source of truth

`src/policy_source.json` is the sole authored policy source. `src/policy_schema.py`
parses it **once** into two aligned representations (PLAN §6, "two representations,
one source"):

- **structured params** — flat, typed rows the authorities read: `spec_params`
  (grade level: chemistry/mechanical ranges + tolerances + coating threshold) and
  `warranty_terms` (product/coating level: duration, full-coverage window,
  exclusions, proration, effective window);
- **citable clauses** — `spec_clauses` / `warranty_clauses`, one row per parent
  section, carrying the identifiers, numeric thresholds and negation that hybrid
  retrieval needs.

The numbers the authorities decide on always come from the params, never from the
clause text.

## Components

| File | Role |
| --- | --- |
| `src/gateway_embed.py` | Shared GTE embedding helper via the Unity Gateway model service `system.ai.gte-large-en` (`/ai-gateway/mlflow/v1/embeddings`); OAuth token from the SDK refreshed per batch, ~16/batch, 429 retry with backoff + Retry-After, 1024-dim L2-normalized (cosine). No `dimensions` param; `ai_query` deliberately not used. Used by BOTH intake and retrieval. |
| `src/policy_intake.py` | **Sole** creator + populator of the four policy tables in Lakebase: creates `pg_trgm`/`vector`/`lakebase_vector`/`lakebase_text` extensions (raises if any fails — no silent fallback), the param + clause tables (`vector(1024)` + `tsvector`), embeds clauses, upserts + **reconciles** stale rows in a transaction, then builds the real Lakebase Search indexes (`lakebase_ann`, `lakebase_bm25`) **after** backfill and **verifies** the actual access method from the catalog. |
| `src/authorities.py` | Pure `compute_conformance` (incl. gauge + width) / `compute_coverage` / `compute_settlement`. **The single source of the money math**, exercised by the offline test suite and called in-process by the runtime adapter — no longer deployed/registered as UC functions (that added ~5-6 warehouse round-trips per adjudication for math that runs in <1µs in-process). |
| `src/authorities_runtime.py` | Runtime adapter: fetches the structured params (`public.spec_params` / `public.warranty_terms`) and the coil MTC (`reference.heats_coils` / `reference.mill_test_certs`) over the SAME Lakebase psycopg (5432) path as retrieval/duplicate (`db.py`), with parameterized queries, then calls the `authorities.py` functions **in-process**. No warehouse / Statement Execution API on the decision path. |
| `src/duplicate.py` | `check_duplicate_claim` — deterministic record linkage (block by coil + date window, match on defect/amount/tonnage + `pg_trgm` narrative). A leakage gate that can deny money. |
| `src/retrieval.py` | `retrieve_policy_clauses` (RRF hybrid: `lakebase_ann` dense `<=>` + `lakebase_bm25` lexical `<@> to_bm25query`, metadata filters folded into both arms) and `find_similar_prior_claims` (advisory). Runs at agent/app runtime (psycopg + gateway), NOT as a UC Python UDF. |
| `src/fraud_graph.py` + `src/fraud_graph_job.py` | Connected-components cluster risk over shared **heats**, scored by customer concentration → `gold.customer_heat_risk`. |
| `src/db.py` | Lakebase psycopg connection with an SDK OAuth credential. |

## Run

Intake and retrieval run as local runtime Python (psycopg 5432 + gateway HTTPS),
always with an explicit profile:

```bash
uv run --with "psycopg[binary]==3.2.10" --with "databricks-sdk>=0.81.0" \
  python agent/src/policy_intake.py --profile fe-bar          # idempotent; owns the 4 tables
databricks bundle run fraud_graph -t prod --profile fe-bar    # gold.customer_heat_risk
```

## Development checks

```bash
uv run --with pytest pytest agent/tests -q
uv run --with ruff ruff check agent && uv run --with ruff ruff format --check agent
python3 -m compileall -q agent/src
```

Unit tests cover the authorities against the Wave-1 injected label patterns
(in-spec-should-deny, genuine nonconformance, adhesion failure, out-of-warranty /
environment exclusions, over-claim capping + partials, warranty proration), the
duplicate rule, RRF fusion + folded metadata filters, the embedding
helper (normalization/batching/429 retry), and the fraud-graph clustering.
