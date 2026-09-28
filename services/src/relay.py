# Databricks notebook source
# ruff: noqa: F821
"""Outbox relay: poll unpublished outbox rows -> publish claim.adjudicated -> mark published.

The transactional-outbox pattern's publish half. Rows are drained in creation
order; each is produced to Kafka ``claim.adjudicated`` (key = ``aggregate_id`` =
``claim_id``) and its ``published_at`` is set ONLY inside the delivery callback,
i.e. after the broker acknowledges the write. A crash between publish and the
``UPDATE`` re-publishes the row next run; downstream consumers dedup on the
deterministic case id, so re-publish is a business no-op (at-least-once publish +
idempotent consume). ``published_at IS NULL`` guards the UPDATE so a concurrent
run cannot double-mark.

Default: scheduled serverless, bounded drain then exit. See the CONTINUOUS MODE
toggle in the resource yml for a live-demo loop.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import relay_core

dbutils.widgets.text("topic", config.TOPIC_CLAIM_ADJUDICATED, label="Destination topic")
dbutils.widgets.text("max_events", "6000", label="Max unpublished rows to drain this run")

topic = dbutils.widgets.get("topic")
max_events = int(dbutils.widgets.get("max_events"))

os.environ.setdefault(
    "DATABRICKS_HOST", "https://" + spark.conf.get("spark.databricks.workspaceUrl")
)
config.AppSpEnv().export_from_secrets(dbutils)

import lakebase  # noqa: E402 (needs SP env set first)

kafka_cfg = config.kafka_config_from_secrets(dbutils)

from kafka_io import build_producer  # noqa: E402 (lazy: native wheel)

producer = build_producer(kafka_cfg)
print(f"Relay draining up to {max_events} unpublished rows -> topic={topic}")

published = failed = 0

# autocommit=True: each mark-published UPDATE commits independently, right after
# the broker acks that specific record (so we never mark an un-acked publish).
with lakebase.connect(autocommit=True) as conn:
    with conn.cursor() as read_cur:
        read_cur.execute(relay_core.SELECT_UNPUBLISHED_SQL, relay_core.select_params(max_events))
        rows = read_cur.fetchall()
        columns = [d[0] for d in read_cur.description]

    print(f"Fetched {len(rows)} unpublished outbox row(s).")

    def _on_delivery(err, kafka_msg, event_id=None):
        global published, failed
        if err is not None:
            failed += 1
            print(f"Delivery FAILED for event {event_id}: {err}")
            return
        # Broker acked -> now (and only now) mark the row published.
        with conn.cursor() as mark_cur:
            mark_cur.execute(relay_core.MARK_PUBLISHED_SQL, (event_id,))
        published += 1

    for row in rows:
        record = dict(zip(columns, row))
        event_id = record["event_id"]
        # Defensive assertion: SELECT_UNPUBLISHED_SQL already filters event_type in the
        # query (before LIMIT), so every fetched row is a claim.adjudicated event. This
        # guard only trips if that query changes; it cannot cause starvation because
        # foreign rows never enter `rows` in the first place.
        if not relay_core.is_adjudicated_event(record["event_type"]):
            continue
        producer.produce(
            topic=topic,
            key=relay_core.kafka_key(record["aggregate_id"]),
            value=relay_core.kafka_value(record["payload"]),
            on_delivery=lambda err, m, eid=event_id: _on_delivery(err, m, eid),
        )
        producer.poll(0)  # serve delivery callbacks as acks arrive

    producer.flush()  # block until every callback (ack or error) has fired

summary = {"published": published, "delivery_failures": failed, "fetched": len(rows)}
print(f"Relay summary: {summary}")
dbutils.notebook.exit(__import__("json").dumps(summary, sort_keys=True))
