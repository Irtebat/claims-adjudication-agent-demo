# services — Kafka event backbone

The event-driven layer that turns the batch claims-adjudication agent into a
streaming pipeline. New claims flow out as Kafka events, the governed serving
endpoint writes a RECOMMENDED adjudication for each, and — only after a human
adjuster finalizes the claim in the App — the final decision fans out to four
downstream consumers, all with **exactly-once *business* processing**: every stage
deduplicates, so a re-delivered event is a no-op.

**Who writes the outbox.** The worker path writes RECOMMENDED adjudications only
(adjudication + decision record, no `public.outbox` row). The `claim.adjudicated`
outbox row is written ONLY by the App's finalization transaction
(`app/server/finalize.ts`), in the same transaction as the FINAL adjudication and the
new decision-record version. Until an adjuster finalizes, the relay finds no
unpublished outbox rows and the consumers receive no events: both jobs run and exit
idle. That is expected, not a failure.

```
 Lakebase CDF (lb_claims_history, inserts)
        │  producer (Spark structured streaming, availableNow)
        ▼
 ┌──────────────────┐     worker (dedup on claim_id, persist=true)
 │ claim.submitted  │ ─────────────────────────────────────────────┐
 └──────────────────┘                                               ▼
                                          governed serving endpoint  →  writer.py
                                          (RECOMMENDED adjudication + decision record,
                                           ONE Postgres transaction, NO outbox row)
                                                                     │
                                          adjuster finalizes in the App (finalize.ts):
                                          FINAL adjudication + record version + outbox,
                                          ONE Postgres transaction
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
| `fe-bar-services-migrate`   | `src/migrate.py`   | Grants the serving SP (`sp_role`) the outbox/settlements/investigation/supplier-recovery privileges this layer needs (and retires a legacy `public.claims_pending` queue if present — a no-op on a fresh setup). Run once. |
| `fe-bar-services-producer`  | `src/producer.py`  | Spark structured streaming, `availableNow`. CDF inserts on `fe-bar-ir.cdf.lb_claims_history` → `claim.submitted`. Checkpointed; initial snapshot replays the ~5000 seeded claims once, then only new claims stream. |
| `fe-bar-services-worker`    | `src/worker.py`    | Consumes `claim.submitted`; skips claims that already have a human decision (`decision_status` FINAL or REVIEWED) or an `agent_recommendation` adjudication; otherwise invokes the endpoint with `persist=true`, which writes a RECOMMENDED adjudication + decision record (no outbox row). |
| `fe-bar-services-relay`     | `src/relay.py`     | Polls unpublished `outbox` rows (written only by App finalization), publishes `claim.adjudicated`, sets `published_at` only after the broker acks. Idle until an adjuster finalizes. |
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
- **Worker**: before invoking the endpoint, one bound-parameter lookup per claim
  (`SELECT bool_or(decision_status = ANY(%s)), bool_or(data_provenance = %s) FROM
  adjudications WHERE claim_id = %s`) decides the outcome:
  - a human decision already exists (`decision_status` FINAL or REVIEWED, e.g. the
    seeded history the producer's CDF snapshot replays): skip, counted as
    `skipped_already_final`;
  - an `agent_recommendation` adjudication already exists (re-delivery): skip,
    counted as `skipped_already_adjudicated`;
  - otherwise invoke the endpoint with `persist=true`.

  A skip makes no endpoint call and no write. Either way the offset is committed
  only after the claim is invoked or deliberately skipped; a failure before that
  leaves it uncommitted for re-delivery. A Kafka error/event message (transport
  error, partition EOF) is counted in `consumer_errors` and never committed; the
  consumers job does the same. The endpoint's
  `writer.py` is the deeper guarantee: first-write-wins on the deterministic
  `adjudication_id`.
- **`writer.py` (one transaction, recommendation only)**: the RECOMMENDED
  adjudication + decision record commit together, both `INSERT ... ON CONFLICT DO
  NOTHING` (first-write-wins) on their stable keys (adjudication on
  `adjudication_id`, record on `(adjudication_id, record_version)`). A same-id retry
  mutates neither, so the adjudication can never drift out of step with the immutable
  record. It writes **no** outbox row.
- **App finalization (`app/server/finalize.ts`, one transaction)**: the FINAL
  adjudication update, the next decision-record version, and the `claim.adjudicated`
  outbox row (`event_id = adj-<adjudication_id>`, payload = the FINAL human decision)
  commit together. Finalizing an already-FINAL adjudication is a no-op, so no double
  outbox row is written. This is the only writer of `public.outbox`.
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

Jobs read Kafka creds from secret scope `fe-bar-aiven-kafka` and the serving
service principal's OAuth creds (`app-sp-client-id` / `app-sp-client-secret`) from
scope `claims-agent`, then authenticate to the endpoint **and** Lakebase
**as that service principal** (mirrors `agent/src/db.py` + `workspace_client.py`).
The SP therefore carries the grants that gate every write (see `src/migrate.py`).
No secret is ever inlined or committed.

## Prerequisites

- The serving endpoint and the `claims-agent` scope (`app-sp-client-id`,
  `app-sp-client-secret`, `lakebase-db-user`) exist — both come from
  `agent/scripts/setup_serving_sp.py` (see `agent/README.md`).
- Scope `fe-bar-aiven-kafka` holds the four keys `src/config.py` reads
  (`KAFKA_KEYS`). Values come from the Aiven console; never commit them. Scope
  creation and secret writes are mutating — run them deliberately:

  ```bash
  databricks secrets create-scope fe-bar-aiven-kafka --profile fe-bar-ir-2026
  databricks secrets put-secret fe-bar-aiven-kafka bootstrap-servers --profile fe-bar-ir-2026  # host:port
  databricks secrets put-secret fe-bar-aiven-kafka sasl-username     --profile fe-bar-ir-2026
  databricks secrets put-secret fe-bar-aiven-kafka sasl-password     --profile fe-bar-ir-2026
  databricks secrets put-secret fe-bar-aiven-kafka ssl-ca-pem        --profile fe-bar-ir-2026 < ca.pem
  ```

  Without `--string-value`, `put-secret` prompts for a single-line value, so it
  stays out of shell history; the multi-line CA certificate is piped on stdin. The non-secret bootstrap address is recorded in
  `config/envs/fe-bar-ir-2026.yaml` (`kafka.bootstrap_servers`).

## Deploy

Working directory: `services/`.

```bash
databricks bundle validate --strict -t prod --profile fe-bar-ir-2026
databricks bundle deploy   -t prod --profile fe-bar-ir-2026
databricks bundle run migrate -t prod --profile fe-bar-ir-2026   # once: grants + retire legacy queue
```

`migrate` grants the serving service principal (bundle variable `sp_role`, its
application UUID) the outbox, settlements, investigation, and supplier-recovery
privileges; override with `--var sp_role=<serving-sp-app-id>` if it is rotated.

**Schedules.** Producer, worker, relay, and consumers are authored with an hourly
`trigger.periodic` and `pause_status: UNPAUSED`. Until all four Kafka keys exist,
every scheduled run fails at Kafka client setup. Pause or unpause a job by updating
its trigger (keep the full periodic block):

```bash
databricks jobs list --name fe-bar-services-worker --profile fe-bar-ir-2026 -o json   # find <job-id>
databricks jobs update <job-id> --profile fe-bar-ir-2026 --json \
  '{"new_settings":{"trigger":{"pause_status":"PAUSED","periodic":{"interval":1,"unit":"HOURS"}}}}'
```

Use `"UNPAUSED"` to resume; the Jobs UI toggle is equivalent. **The next
`bundle deploy` reapplies the bundle definition and resets all four triggers to
`UNPAUSED`** — re-pause after deploying if needed.

**Live-demo (continuous) mode:** flip each streaming job's trigger to `continuous`
and adjust the notebook trigger / poll settings as documented at the bottom of
`resources/producer.yml`, `worker.yml`, `relay.yml`, and `consumers.yml`, then
re-deploy. Scheduled serverless remains the default for cost.

## Run

The schedules drive the backbone once deployed. To drive it by hand, in order
(working directory `services/`):

```bash
databricks bundle run producer  -t prod --profile fe-bar-ir-2026   # CDF inserts -> claim.submitted
databricks bundle run worker    -t prod --profile fe-bar-ir-2026   # RECOMMENDED adjudications (persist=true)
# ... an adjuster finalizes claims in the App, which writes the outbox rows ...
databricks bundle run relay     -t prod --profile fe-bar-ir-2026   # outbox -> claim.adjudicated
databricks bundle run consumers -t prod --profile fe-bar-ir-2026   # fan-out (4 parallel tasks)
```

See `docs/RUNBOOK.md` (event backbone).

## Verify

Read-only:

```bash
databricks secrets list-secrets fe-bar-aiven-kafka --profile fe-bar-ir-2026 -o json   # key names only
databricks secrets list-secrets claims-agent --profile fe-bar-ir-2026 -o json
databricks jobs list --profile fe-bar-ir-2026 -o json \
  | jq -r '.[] | select(.settings.name | startswith("fe-bar-services-")) | [.job_id, .settings.name, (.settings.trigger.pause_status // "-")] | @tsv'
```

Expected: the four Kafka keys and three `claims-agent` keys are listed (values are
never returned); five `fe-bar-services-*` jobs exist, and the four scheduled ones
show the intended pause state. Inspect a run with
`databricks jobs list-runs --job-id <job-id> --limit 5 --profile fe-bar-ir-2026 -o json`.
An idle relay/consumers run (no finalized claims yet) is expected.

## Tests

```bash
uv run --with pytest pytest services/tests -q
uv run --with ruff ruff check services
uv run --with ruff ruff format --check services
```

The dedup contract is proven without a broker: `tests/test_consumer_core.py`
drives the same `claim.adjudicated` event through a consumer three times against an
in-memory table that honours the `ON CONFLICT` semantics and asserts exactly one
downstream row. The producer→worker path has been exercised against the live Aiven
broker; the full relay→consumer fan-out additionally requires the Kafka secrets above
and a human-finalized claim (see `docs/CURRENT-STATE.md`).
