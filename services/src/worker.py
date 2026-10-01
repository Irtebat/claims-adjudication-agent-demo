# Databricks notebook source
# ruff: noqa: F821
"""Adjudication worker: consume claim.submitted -> (dedup) -> invoke governed endpoint.

For each ``claim.submitted`` event the worker checks Lakebase for an existing
*agent-produced* adjudication on that claim id; if none exists it invokes the
governed serving endpoint with ``persist=true`` so the endpoint's writer performs
the atomic Lakebase write (adjudication + decision record) behind the deterministic
authorities. The recommendation write emits NO ``claim.adjudicated`` outbox row —
that event is produced only when an adjuster finalizes the claim in the App, so the
claim stays ``RECOMMENDED`` until then. Kafka offsets are committed manually only
after a message is fully processed, so a crash re-delivers rather than drops.
Re-delivery is safe twice over: the pre-check skips already-adjudicated claims, and
the endpoint's writer is idempotent on a stable ``adjudication_id``.

Default: scheduled serverless, bounded drain (``max_messages``) then exit. See the
CONTINUOUS MODE toggle in the resource yml for a live-demo long-running consumer.
"""

import os

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

processed = errors = 0
outcomes = {worker_core.ADJUDICATE: 0, worker_core.SKIP_FINAL: 0, worker_core.SKIP_AGENT: 0}
# One read connection reused for the dedup pre-check (autocommit; SELECT only).
with lakebase.connect(autocommit=True) as conn:

    def lookup(claim_id):
        with conn.cursor() as cur:
            cur.execute(worker_core.DEDUP_SQL, worker_core.dedup_params(claim_id))
            return cur.fetchone()

    while processed < max_messages:
        msg = consumer.poll(timeout=poll_timeout_s)
        if msg is None:
            print("Idle poll timeout reached; draining complete.")
            break
        # Skips (already FINAL/REVIEWED, or already agent-recommended) make no endpoint
        # call and no write; the offset commits only after the claim is handled. A Kafka
        # error/event message is counted and never committed.
        outcome = worker_core.handle_message(
            msg,
            lookup=lookup,
            invoke=lambda claim: serving.invoke(ws, claim, persist=True),
            commit=lambda: consumer.commit(msg, asynchronous=False),
        )
        if outcome == worker_core.CONSUMER_ERROR:
            errors += 1
            print(f"Consumer error (not committed): {msg.error()}")
            continue
        processed += 1
        outcomes[outcome] += 1

consumer.close()
summary = {
    "processed": processed,
    "adjudicated": outcomes[worker_core.ADJUDICATE],
    "skipped_already_final": outcomes[worker_core.SKIP_FINAL],
    "skipped_already_adjudicated": outcomes[worker_core.SKIP_AGENT],
    "consumer_errors": errors,
}
print(f"Worker summary: {summary}")
dbutils.notebook.exit(__import__("json").dumps(summary, sort_keys=True))
