"""Pure relay core: transactional-outbox SELECT + mark-published SQL and framing.

Importable without Kafka or Lakebase. The relay drains unpublished outbox rows
in creation order, publishes each to Kafka ``claim.adjudicated`` (key =
``aggregate_id`` = ``claim_id``), and sets ``published_at`` ONLY after the
broker acks the publish. If the process crashes after the publish but before the
``UPDATE`` lands, the row is re-published on the next run; consumers dedup on the
carried deterministic case id (derived 1:1 from the event's adjudication), so the
re-publish causes no double business processing (at-least-once publish +
idempotent consume = effectively-once).
"""

from __future__ import annotations

from events import EVENT_CLAIM_ADJUDICATED, serialize

# The event_type filter is applied INSIDE the query, BEFORE the LIMIT, so unsupported
# or foreign outbox rows can never consume the bounded window and starve
# claim.adjudicated events. (An unfiltered SELECT that skipped them client-side would
# let them fill every page and permanently block publishing.)
SELECT_UNPUBLISHED_SQL = (
    "SELECT event_id, aggregate_id, event_type, payload "
    "FROM outbox WHERE published_at IS NULL AND event_type = %s "
    "ORDER BY created_at LIMIT %s"
)

# Sets published_at only after the broker acks a specific record. published_at IS NULL
# guards against double-MARKING (a re-run won't re-stamp an already-published row); it
# does NOT by itself prevent concurrent double-PUBLISHING — that is bounded by the
# job's max_concurrent_runs:1 plus downstream consumer idempotency (dedup on the
# deterministic case id), so a rare duplicate publish is a business no-op.
MARK_PUBLISHED_SQL = (
    "UPDATE outbox SET published_at = now() WHERE event_id = %s AND published_at IS NULL"
)


def select_params(max_events: int) -> tuple[str, int]:
    """Bound params for :data:`SELECT_UNPUBLISHED_SQL`: (event_type filter, row limit)."""
    return (EVENT_CLAIM_ADJUDICATED, max_events)


def kafka_key(aggregate_id: str) -> bytes:
    """Kafka partition key for a claim.adjudicated record (claim_id)."""
    return aggregate_id.encode("utf-8")


def kafka_value(payload: dict) -> bytes:
    """Serialize the outbox payload (already the claim.adjudicated event) to bytes."""
    return serialize(payload)


def is_adjudicated_event(event_type: str) -> bool:
    """Only claim.adjudicated events are relayed from the outbox."""
    return event_type == EVENT_CLAIM_ADJUDICATED
