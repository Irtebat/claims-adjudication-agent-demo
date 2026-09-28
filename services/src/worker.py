# Databricks notebook source
# ruff: noqa: F821
"""Adjudication worker: consume claim.submitted -> (dedup) -> invoke governed endpoint.

For each ``claim.submitted`` event the worker checks Lakebase for an existing
*agent-produced* adjudication on that claim id; if none exists it invokes the
governed serving endpoint with ``persist=true`` so the endpoint's writer performs
the atomic Lakebase write (adjudication + decision record + ``claim.adjudicated``
outbox row) behind the deterministic authorities. Kafka offsets are committed
manually only after a message is fully processed, so a crash re-delivers rather
than drops. Re-delivery is safe twice over: the pre-check skips already-adjudicated
claims, and the endpoint's writer is idempotent on a stable ``adjudication_id`` +
outbox ``event_id``.

Default: scheduled serverless, bounded drain (``max_messages``) then exit. See the
CONTINUOUS MODE toggle in the resource yml for a live-demo long-running consumer.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import serving
import worker_core

dbutils.widgets.text("group_id", "fe-bar-worker-adjudication", label="Kafka consumer group")
dbutils.widgets.text("topic", config.TOPIC_CLAIM_SUBMITTED, label="Source topic")
dbutils.widgets.text("max_messages", "6000", label="Max messages to drain this run")
dbutils.widgets.text("poll_timeout_s", "10", label="Idle poll timeout before exit (s)")

group_id = dbutils.widgets.get("group_id")
topic = dbutils.widgets.get("topic")
max_messages = int(dbutils.widgets.get("max_messages"))
poll_timeout_s = float(dbutils.widgets.get("poll_timeout_s"))

# Auth AS the Wave 6 application SP for both the endpoint call and Lakebase.
os.environ.setdefault(
    "DATABRICKS_HOST", "https://" + spark.conf.get("spark.databricks.workspaceUrl")
)
config.AppSpEnv().export_from_secrets(dbutils)

import lakebase  # noqa: E402 (needs SP env set first)

kafka_cfg = config.kafka_config_from_secrets(dbutils)
ws = lakebase.workspace_client()  # authed AS the app SP, for the endpoint call

from kafka_io import build_consumer  # noqa: E402 (lazy: native wheel)

consumer = build_consumer(kafka_cfg, group_id)
consumer.subscribe([topic])
print(f"Worker subscribed group={group_id} topic={topic} max_messages={max_messages}")

processed = adjudicated = skipped = errors = 0
# One read connection reused for the dedup pre-check (autocommit; SELECT only).
with lakebase.connect(autocommit=True) as conn:
    while processed < max_messages:
        msg = consumer.poll(timeout=poll_timeout_s)
        if msg is None:
            print("Idle poll timeout reached; draining complete.")
            break
        if msg.error():
            errors += 1
            print(f"Consumer error (skipping): {msg.error()}")
            consumer.commit(msg, asynchronous=False)
            continue

        processed += 1
        event = worker_core.parse_submitted(msg.value())
        claim = worker_core.claim_from_event(event)
        claim_id = event["claim_id"]

        with conn.cursor() as cur:
            cur.execute(worker_core.ALREADY_ADJUDICATED_SQL, worker_core.dedup_params(claim_id))
            already = cur.fetchone() is not None

        if worker_core.should_adjudicate(already):
            serving.invoke(ws, claim, persist=True)
            adjudicated += 1
        else:
            skipped += 1  # already agent-adjudicated (baseline snapshot or re-delivery)

        # Commit offset only after the message is fully handled (at-least-once).
        consumer.commit(msg, asynchronous=False)

consumer.close()
summary = {
    "processed": processed,
    "adjudicated": adjudicated,
    "skipped_already_adjudicated": skipped,
    "consumer_errors": errors,
}
print(f"Worker summary: {summary}")
dbutils.notebook.exit(__import__("json").dumps(summary, sort_keys=True))
