"""Relay outbox SQL + Kafka framing (Lakebase/Kafka-free)."""

import events
import relay_core


def test_select_unpublished_orders_by_created_at_and_limits():
    sql = relay_core.SELECT_UNPUBLISHED_SQL
    assert "FROM outbox" in sql
    assert "published_at IS NULL" in sql
    assert "ORDER BY created_at" in sql
    assert "LIMIT %s" in sql


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
