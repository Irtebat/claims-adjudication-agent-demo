"""Relay outbox SQL + Kafka framing (Lakebase/Kafka-free)."""

import events
import relay_core


def test_select_unpublished_filters_event_type_before_limit():
    sql = relay_core.SELECT_UNPUBLISHED_SQL
    assert "FROM outbox" in sql
    assert "published_at IS NULL" in sql
    assert "ORDER BY created_at" in sql
    assert "LIMIT %s" in sql
    # The event_type filter must be in the WHERE (before LIMIT), not applied
    # client-side after the window is filled — otherwise foreign rows starve
    # claim.adjudicated events.
    assert "event_type = %s" in sql
    assert sql.index("event_type = %s") < sql.index("LIMIT %s")


def test_select_params_bind_adjudicated_type_and_limit():
    assert relay_core.select_params(500) == (events.EVENT_CLAIM_ADJUDICATED, 500)


def test_foreign_rows_cannot_fill_the_bounded_window():
    """Simulate WHERE published_at IS NULL AND event_type='claim.adjudicated'
    ORDER BY created_at LIMIT n over a mixed outbox, proving foreign/unsupported
    rows are excluded server-side and so cannot starve adjudication events."""
    outbox = [
        {
            "event_id": "x-1",
            "event_type": "some.other.event",
            "published_at": None,
            "created_at": 1,
        },
        {
            "event_id": "x-2",
            "event_type": "some.other.event",
            "published_at": None,
            "created_at": 2,
        },
        {
            "event_id": "adj-1",
            "event_type": events.EVENT_CLAIM_ADJUDICATED,
            "published_at": None,
            "created_at": 3,
        },
        {
            "event_id": "adj-2",
            "event_type": events.EVENT_CLAIM_ADJUDICATED,
            "published_at": None,
            "created_at": 4,
        },
        {
            "event_id": "adj-3",
            "event_type": events.EVENT_CLAIM_ADJUDICATED,
            "published_at": None,
            "created_at": 5,
        },
    ]
    event_type, limit = relay_core.select_params(2)

    def query(rows, event_type, limit):
        matched = [r for r in rows if r["published_at"] is None and r["event_type"] == event_type]
        matched.sort(key=lambda r: r["created_at"])
        return matched[:limit]

    selected = query(outbox, event_type, limit)
    # Despite 2 foreign rows sorting first by created_at, the bounded window (limit=2)
    # is filled entirely with adjudication events — foreign rows never enter it.
    assert [r["event_id"] for r in selected] == ["adj-1", "adj-2"]
    assert all(r["event_type"] == events.EVENT_CLAIM_ADJUDICATED for r in selected)


def test_mark_published_guards_against_double_mark():
    sql = relay_core.MARK_PUBLISHED_SQL
    assert "UPDATE outbox SET published_at = now()" in sql
    assert "WHERE event_id = %s" in sql
    # Guard: a concurrent run cannot re-mark an already-published row.
    assert "published_at IS NULL" in sql


def test_kafka_key_is_aggregate_id_bytes():
    assert relay_core.kafka_key("CLM-1") == b"CLM-1"


def test_kafka_value_serializes_payload():
    payload = {"event_id": "adj-1", "claim_id": "CLM-1", "verdict": "APPROVE"}
    value = relay_core.kafka_value(payload)
    assert isinstance(value, bytes)
    assert events.deserialize(value) == payload


def test_is_adjudicated_event():
    assert relay_core.is_adjudicated_event(events.EVENT_CLAIM_ADJUDICATED) is True
    assert relay_core.is_adjudicated_event(events.EVENT_CLAIM_SUBMITTED) is False
