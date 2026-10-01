# Databricks notebook source
# ruff: noqa: F821
"""Downstream consumers of claim.adjudicated: settlement / investigation / supplier-recovery / notification.

One parameterized notebook; the ``consumer`` widget selects which downstream this
run embodies (each has its own Kafka consumer group, so offsets are independent).
For every ``claim.adjudicated`` event, :func:`consumer_core.plan_action` returns the
idempotent action for this consumer:

- settlement        (verdict APPROVE)  -> UPSERT settlements STL-<claim_id>
- investigation     (verdict PEND)     -> INSERT investigation_cases INV-<claim_id> (ON CONFLICT DO NOTHING)
- supplier-recovery (supplier_attributable & recovery_supplier_id)
                                        -> INSERT supplier_recovery_cases SRC-<claim_id> (ON CONFLICT DO NOTHING)
- notification      (always)           -> LOG ONLY

Every write lands behind ``ON CONFLICT`` on a deterministic case id derived 1:1
from the claim/adjudication, so a re-delivered event is a no-op. Offsets commit
only after the action is applied (at-least-once delivery + idempotent apply).

Default: scheduled serverless, bounded drain then exit. See the CONTINUOUS MODE
toggle in the resource yml for a live-demo loop.
"""

import json
import os

import config
import consumer_core
from events import deserialize

dbutils.widgets.dropdown(
    "consumer",
    consumer_core.SETTLEMENT,
    list(consumer_core.CONSUMERS),
    label="Which downstream consumer this run embodies",
)
dbutils.widgets.text("topic", config.TOPIC_CLAIM_ADJUDICATED, label="Source topic")
dbutils.widgets.text("max_messages", "6000", label="Max messages to drain this run")
dbutils.widgets.text("poll_timeout_s", "10", label="Idle poll timeout before exit (s)")

consumer_name = dbutils.widgets.get("consumer")
topic = dbutils.widgets.get("topic")
max_messages = int(dbutils.widgets.get("max_messages"))
poll_timeout_s = float(dbutils.widgets.get("poll_timeout_s"))
group_id = consumer_core.CONSUMER_GROUPS[consumer_name]

writes_to_db = consumer_name != consumer_core.NOTIFICATION
if writes_to_db:
    os.environ.setdefault(
        "DATABRICKS_HOST", "https://" + spark.conf.get("spark.databricks.workspaceUrl")
    )
    config.AppSpEnv().export_from_secrets(dbutils)
    import lakebase  # noqa: E402 (needs SP env set first)

kafka_cfg = config.kafka_config_from_secrets(dbutils)

from kafka_io import build_consumer  # noqa: E402 (lazy: native wheel)

consumer = build_consumer(kafka_cfg, group_id)
consumer.subscribe([topic])
print(f"Consumer '{consumer_name}' subscribed group={group_id} topic={topic}")

processed = acted = skipped = errors = 0


def _apply(cur, action):
    """Apply one planned action; returns True when a DB row was written."""
    if action["kind"] == "log":
        print(action["message"])
        return False
    cur.execute(action["sql"], action["row"])
    return True


def _run(cur=None):
    global processed, acted, skipped, errors
    while processed < max_messages:
        msg = consumer.poll(timeout=poll_timeout_s)
        if msg is None:
            print("Idle poll timeout reached; draining complete.")
            break
        if msg.error():
            # Never commit a Kafka error/event message: it carries no event to handle.
            errors += 1
            print(f"Consumer error (not committed): {msg.error()}")
            continue
        processed += 1
        event = deserialize(msg.value())
        action = consumer_core.plan_action(consumer_name, event)
        if action is None:
            skipped += 1  # not actionable for this consumer (e.g. DENY -> settlement)
        elif _apply(cur, action):
            acted += 1
        else:
            acted += 1  # notification log counts as handled
        consumer.commit(msg, asynchronous=False)


if writes_to_db:
    # autocommit=True: each idempotent upsert/insert commits on its own so a mid-run
    # crash leaves committed rows + uncommitted offsets -> safe re-delivery.
    with lakebase.connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            _run(cur)
else:
    _run(None)

consumer.close()
summary = {
    "consumer": consumer_name,
    "processed": processed,
    "acted": acted,
    "skipped_not_actionable": skipped,
    "consumer_errors": errors,
}
print(f"Consumer summary: {summary}")
dbutils.notebook.exit(json.dumps(summary, sort_keys=True))
