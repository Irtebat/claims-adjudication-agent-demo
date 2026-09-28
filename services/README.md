# services — Kafka event backbone

The event-driven layer that turns the batch claims-adjudication agent into a
streaming pipeline. New claims flow out as Kafka events, the governed serving
endpoint adjudicates them, and the outcome fans out to four downstream consumers
— all with **exactly-once *business* processing**: every stage deduplicates, so a
re-delivered event is a no-op.

```
 Lakebase CDF (lb_claims_history, inserts)
        │  producer (Spark structured streaming, availableNow)
        ▼
 ┌──────────────────┐     worker (dedup on claim_id, persist=true)
 │ claim.submitted  │ ─────────────────────────────────────────────┐
 └──────────────────┘                                               ▼
                                          governed serving endpoint  →  writer.py
                                          (adjudication + decision record + outbox,
                                           ONE Postgres transaction)
                                                                     │
 ┌──────────────────┐     relay (poll outbox WHERE published_at IS NULL,
 │  public.outbox   │ ──▶  publish, mark published only after broker ack)
 └──────────────────┘                                               │
        ▼                                                           ▼
 ┌──────────────────┐   ┌─ settlement        (APPROVE → settlements STL-<claim_id>, UPSERT)
 │ claim.adjudicated│──▶├─ investigation     (PEND    → investigation_cases INV-<claim_id>)
 └──────────────────┘   ├─ supplier-recovery (attributable → supplier_recovery_cases SRC-<claim_id>)
                        └─ notification       (always  → LOG ONLY)
```

## Topics (existing Aiven service — never provision a new cluster)

Service `fe-bar-kafka` (Aiven project `kafka-dbx-pipeline-001`, plan `free-0`),
transport **SASL_SSL + SCRAM-SHA-256**. `free-0` caps topics at **2 partitions**
and rejects retention overrides (default 7 days).

| Topic | Key | Partitions | RF | Retention |
|-------|-----|-----------:|---:|-----------|
| `claim.submitted`   | `claim_id` | 2 | 2 | 7 d |
| `claim.adjudicated` | `claim_id` (`aggregate_id`) | 2 | 2 | 7 d |

Both topics carry JSON values. The payload contract is the single source of truth
in `src/events.py` (`agent/src/writer.py` mirrors the `claim.adjudicated` shape
because it is bundled with the serving model and cannot import this package).

## Jobs (DABs bundle `fe-bar-services`)

Each job is its own resource file under `resources/` (glob-included). Default
compute is **scheduled serverless** (cheapest); every streaming job documents a
**CONTINUOUS MODE toggle** for a live demo (see each `resources/*.yml`).

| Job | File | What it does |
|-----|------|--------------|
| `fe-bar-services-migrate`   | `src/migrate.py`   | Drops `public.claims_pending`; grants the app SP the outbox/settlements/investigation/supplier-recovery privileges this layer needs. Run once. |
| `fe-bar-services-producer`  | `src/producer.py`  | Spark structured streaming, `availableNow`. CDF inserts on `fe-bar-ir.cdf.lb_claims_history` → `claim.submitted`. Checkpointed; initial snapshot replays the ~5000 seeded claims once, then only new claims stream. |
| `fe-bar-services-worker`    | `src/worker.py`    | Consumes `claim.submitted`; skips claims that already have an `agent_recommendation` adjudication; otherwise invokes the endpoint with `persist=true`. |
| `fe-bar-services-relay`     | `src/relay.py`     | Polls unpublished `outbox` rows, publishes `claim.adjudicated`, sets `published_at` only after the broker acks. |
| `fe-bar-services-consumers` | `src/consumers.py` | Four parallel tasks (settlement / investigation / supplier-recovery / notification), one Kafka consumer group each. |

Pure, unit-tested cores (`events.py`, `config.py`, `producer_core.py`,
`worker_core.py`, `relay_core.py`, `consumer_core.py`) hold the dedup + payload
contract and import without Kafka/Spark/Lakebase; the `*.py` notebooks are thin
Databricks wrappers.

## Dedup / idempotency (the core guarantee)

Exactly-once *business* processing = **at-least-once delivery + idempotent apply**
at every stage, keyed on stable, deterministic ids:

- **Producer → `claim.submitted`**: key `claim_id`, stable `event_id = sub-<claim_id>`.
  Over-emission (e.g. checkpoint replay) is harmless — the worker dedups.
- **Worker**: before invoking the endpoint, skip if an agent adjudication already
  exists for the claim (`SELECT 1 FROM adjudications WHERE claim_id=%s AND
  data_provenance='agent_recommendation'`). Scoped to `agent_recommendation` so the
  seeded baseline is adjudicated once and re-delivery is skipped. The endpoint's
  `writer.py` is the deeper guarantee: first-write-wins on the deterministic
  `adjudication_id`.
- **`writer.py` (one transaction)**: adjudication + decision record +
  `claim.adjudicated` outbox row commit together, **all three `INSERT ... ON CONFLICT
  DO NOTHING`** (first-write-wins) on their stable keys (adjudication on
  `adjudication_id`, record on `(adjudication_id, record_version)`, outbox on
  `event_id = adj-<adjudication_id>`). A same-id retry mutates none of them, so the
  adjudication can never drift out of step with the immutable record or the outbox
  payload.
- **Relay**: publishes, then sets `published_at` **only after the broker acks**.
  Crash before the mark → re-published next run → consumers dedup → no double
  *business* processing. `UPDATE ... WHERE published_at IS NULL` guards against
  double-**marking** (a re-run won't re-stamp a published row); it does not by itself
  prevent a concurrent double-**publish** — that is bounded by the job's
  `max_concurrent_runs: 1` plus downstream consumer idempotency, so a rare duplicate
  publish is a no-op.
- **Consumers**: every downstream case id is a deterministic function of the claim
  (`STL-`/`INV-`/`SRC-<claim_id>`), 1:1 with the event's `event_id`. Writing behind
  `ON CONFLICT` on that primary key makes a re-delivered event a no-op (`DO NOTHING`)
  or converges to the identical row (`DO UPDATE`). `notification` holds no state.

## Auth model

Jobs read Kafka creds from secret scope `fe-bar-aiven-kafka` and the Wave 6 app-SP
creds from scope `claims-agent`, then authenticate to the endpoint **and** Lakebase
**as that service principal** (mirrors `agent/src/db.py` + `workspace_client.py`).
The SP therefore carries the grants that gate every write (see `src/migrate.py`).
No secret is ever inlined or committed.

## Deploy & run

**User prerequisites (one-time):**

1. **Populate the Kafka secret scope** — the Aiven MCP connector redacts all secret
   material, so these must be set by a human from the Aiven console:
   ```bash
   databricks secrets put-secret fe-bar-aiven-kafka bootstrap-servers --profile fe-bar \
     --string-value 'fe-bar-kafka-kafka-dbx-pipeline-001.i.aivencloud.com:25358'
   databricks secrets put-secret fe-bar-aiven-kafka sasl-username --profile fe-bar   # Aiven "avnadmin"
   databricks secrets put-secret fe-bar-aiven-kafka sasl-password --profile fe-bar   # Aiven SASL password
   databricks secrets put-secret fe-bar-aiven-kafka ssl-ca-pem   --profile fe-bar    # Aiven CA cert (PEM)
   ```
2. The Wave 6 `claims-agent` scope (`app-sp-client-id`, `app-sp-client-secret`,
   `lakebase-db-user`) and the serving endpoint must already exist (they do).

**Deploy the bundle:**
```bash
cd services
databricks bundle validate --strict -t prod --profile fe-bar
databricks bundle deploy   -t prod --profile fe-bar
```

**Run (order matters the first time):**
```bash
databricks bundle run migrate  -t prod --profile fe-bar   # drop pending queue + grants (once)
databricks bundle run producer -t prod --profile fe-bar   # CDF inserts -> claim.submitted
databricks bundle run worker   -t prod --profile fe-bar   # adjudicate (persist=true)
databricks bundle run relay    -t prod --profile fe-bar   # outbox -> claim.adjudicated
databricks bundle run consumers -t prod --profile fe-bar  # fan-out (4 parallel tasks)
```

**Live-demo (continuous) mode:** flip each streaming job's trigger to `continuous`
and adjust the notebook trigger / poll settings as documented at the bottom of
`resources/producer.yml`, `worker.yml`, `relay.yml`, and `consumers.yml`, then
re-deploy. Scheduled serverless remains the default for cost.

## Tests

```bash
uv run --with pytest pytest services/tests -q
uv run --with ruff ruff check services
uv run --with ruff ruff format --check services
```

The dedup contract is proven without a broker: `tests/test_consumer_core.py`
drives the same `claim.adjudicated` event through a consumer three times against an
in-memory table that honours the planned `ON CONFLICT` semantics and asserts
exactly one downstream row. End-to-end evidence (and the live-run runbook, which is
blocked on the human secret step above) is in `docs/evidence/kafka-events/`.
