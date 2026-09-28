# Kafka event backbone — evidence

Wave 8 turns the batch claims-adjudication agent into a streaming pipeline over
the **existing** Aiven Kafka service `fe-bar-kafka` (no billed cluster created).
The layer's design and code live in `services/` (see `services/README.md`); this
directory is the evidence.

## Status

- **Code + gates: complete and green.** Producer, adjudication worker, atomic
  outbox write, outbox relay, and four downstream consumers are implemented,
  unit-tested, ruff-clean, and packaged as a validated DABs bundle. See
  `gates.txt`.
- **Live broker E2E: blocked on one human step.** The Aiven MCP connector redacts
  the SASL password and CA cert (`[REDACTED]`), so the secret scope
  `fe-bar-aiven-kafka` cannot be populated programmatically from here. The
  end-to-end **dedup contract is proven in-memory instead** (`end-to-end-dedup.json`),
  and the live-run runbook is in `services/README.md`.

## Files

| File | What it shows |
|------|---------------|
| `gates.txt` | Every gate command + result (tests, ruff, bundle validate, authorities empty diff, sim). |
| `topic-config.json` | Live read-only `aiven_service_get`: service RUNNING, both topics ACTIVE (2 partitions, RF 2, 168h). Secrets `[REDACTED]`. |
| `end-to-end-dedup.json` | Output of the end-to-end dedup simulation — 6 assertions, all PASS. |
| `simulate_e2e.py` | The runnable simulation (`uv run python docs/evidence/kafka-events/simulate_e2e.py`). |

## Dedup design (exactly-once *business* processing)

At-least-once delivery + idempotent apply at every stage, keyed on stable,
deterministic ids so a re-delivered event is a no-op:

| Stage | Re-delivery source | Dedup mechanism |
|-------|--------------------|-----------------|
| Producer → `claim.submitted` | checkpoint replay / over-emission | key + `event_id = sub-<claim_id>`; worker dedups |
| Worker | `claim.submitted` re-delivery | skip if `agent_recommendation` adjudication exists for `claim_id`; endpoint `writer.py` is idempotent on `adjudication_id` |
| `writer.py` outbox (one tx) | endpoint retry | `event_id = adj-<adjudication_id>`, `ON CONFLICT (event_id) DO NOTHING` |
| Relay → `claim.adjudicated` | crash before mark-published | `published_at` set only after broker ack; `WHERE published_at IS NULL` guards double-mark |
| Consumers | `claim.adjudicated` re-delivery | deterministic case id (`STL-`/`INV-`/`SRC-<claim_id>`) + `ON CONFLICT`; notification is log-only |

## What the simulation proves (`end-to-end-dedup.json`)

The simulation drives 4 claims through the whole chain and **deliberately
re-delivers at every stage** (each claim emitted twice by the producer, one outbox
row re-published, every adjudicated event consumed twice). Result:

- Producer emitted **8** `claim.submitted` events for **4** distinct claims.
- Worker invoked the endpoint **4** times — once per distinct claim (submitted
  re-delivery deduped).
- Outbox holds **4** rows — one per adjudication despite retries.
- Downstream: exactly **1** settlement (`STL-CLM-approve`), **1** investigation
  (`INV-CLM-pend`), **1** supplier-recovery (`SRC-CLM-supplier`) — despite every
  event being delivered twice; `insert_attempts > distinct_rows` shows the
  re-deliveries were attempted and absorbed.

All 6 assertions PASS. The same guarantee is unit-tested against the real
`ON CONFLICT` semantics in `services/tests/test_consumer_core.py`.

## Migration

`services/src/migrate.py` (job `fe-bar-services-migrate`) drops the deprecated
`public.claims_pending` queue (`DROP TABLE IF EXISTS`) and grants the Wave 6 app SP
the privileges this layer needs: `INSERT`/`SELECT`/`UPDATE` on `outbox` and
`SELECT,INSERT,UPDATE` on `settlements`, `investigation_cases`,
`supplier_recovery_cases`. Idempotent; verifies the SP can `INSERT` into `outbox`
and reports whether `claims_pending` existed before/after.

## Live run (after the human secret step)

1. Populate `fe-bar-aiven-kafka` with `bootstrap-servers`, `sasl-username`,
   `sasl-password`, `ssl-ca-pem` (exact `databricks secrets put-secret` commands in
   `services/README.md`).
2. `databricks bundle deploy -t prod --profile fe-bar` (from `services/`).
3. Run `migrate` → `producer` → `worker` → `relay` → `consumers`.
4. Re-run `producer` (checkpoint = no new events) and re-run `worker` to observe
   the live dedup: the second worker run adjudicates 0 new claims (all already
   `agent_recommendation`), and consumer re-runs write 0 new rows.
