# services

Planned layer — not yet implemented. This directory will hold the event-driven
services: a Kafka worker, the outbox relay, and stubbed downstream consumers.

## Intended purpose

- A worker that consumes `claim.submitted` events and invokes the agent.
- An outbox relay that reads the Lakebase `outbox` table and produces
  `claim.adjudicated` events (the transactional outbox pattern).
- Stubbed downstream consumers: settlement, notification, investigation, and
  supplier recovery.

## Intended flow

```
claim.submitted   ->  adjudication worker
Lakebase outbox   ->  claim.adjudicated
claim.adjudicated ->  downstream consumers
```

The operational tables this flow uses (`outbox`, `settlements`,
`investigation_cases`, `supplier_recovery_cases`) are created
by `lakebase/`, not here. No Kafka configuration, worker code, relay, topics, or
jobs are implemented in this directory yet.
The pending/retry queue (`claims_pending`) is deferred to the future services wave.
