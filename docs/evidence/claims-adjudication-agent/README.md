# Evidence — claims-adjudication agent

Proof for the claims-adjudication agent workstream: the deterministic authorities
decide money and the LLM never overrides them, the append-only decision record
reaches UC gold via the reused native CDF, and the agent is registered in Unity
Catalog with an isolated-env validation.

| File | What it shows |
| --- | --- |
| `decision-record-migration.json` | The append-only `public.adjudication_decision_records` created with `REPLICA IDENTITY FULL`, no CDF-unserializable columns, and the immutability grant. |
| `cdf-streaming.json` | The new table reached `CDF_STATE_STREAMING` under the EXISTING schema-scoped `public` CDF config and landed as `cdf.lb_adjudication_decision_records_history` (no new config / Beta). |
| `pipeline-recovery.json` | Version 3/4 were benign native-CDF re-snapshots caused by widening `public.adjudications`; the selective SCD2 refresh and subsequent full incremental pipeline update both completed. |
| `pipeline-gold.json` | The AUTO CDC flow landed the immutable `gold.adjudication_decision_records`, and the decision-record DQ (one row per `(adjudication_id, record_version)`; no authority overridden) passed. |
| `synced-heat-risk.json` | `gold.customer_heat_risk` served down to Lakebase `reference.customer_heat_risk` as a Triggered synced table. |
| `registration.json` | `fe-bar-ir.default.claims_adjudication_agent` logged with pinned deps + resources, validated with `mlflow.models.predict(env_manager="uv")`, alias `@prod` set. |
| `offline-validation.json` | The agent run on a labeled sample spanning every injected pattern: per-claim recommendation vs the deterministic authority outputs (proving no override), the decision records written and read back from Lakebase, and the MLflow traces produced. |
| `gates.txt` | pytest (agent + pipelines) collected counts + results, ruff, ruff format, bundle validate. |

## The core guarantee

`agent/src/authorities.py` (the money math) has an empty diff. The agent's
recommendation is corrected to the deterministic outcome on any invariant
violation, so a duplicate is never payable, `approved_amount` equals the settlement
authority output exactly (or 0 when not APPROVE), and an in-spec / out-of-coverage
claim is never approved. `offline-validation.json` records this per claim
(`authority_never_overridden`).
