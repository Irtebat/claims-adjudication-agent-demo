"""confluent-kafka producer/consumer builders (SASL_SSL + SCRAM, CA via PEM).

confluent-kafka is imported lazily so the pure event/config helpers can be imported
and unit-tested without the native librdkafka wheel installed.
"""

from __future__ import annotations

from typing import Any

from config import KafkaConfig


def build_producer(cfg: KafkaConfig, extra: dict | None = None) -> Any:
    """A confluent-kafka Producer with idempotent, ack-all delivery."""
    from confluent_kafka import Producer

    conf = cfg.confluent_config(
        {
            "enable.idempotence": True,
            "acks": "all",
            "linger.ms": 50,
            "client.id": "fe-bar-services-producer",
        }
    )
    if extra:
        conf.update(extra)
    return Producer(conf)


def build_consumer(cfg: KafkaConfig, group_id: str, extra: dict | None = None) -> Any:
    """A confluent-kafka Consumer with MANUAL offset commit (commit after success)."""
    from confluent_kafka import Consumer

    conf = cfg.confluent_config(
        {
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
            "client.id": f"fe-bar-services-{group_id}",
        }
    )
    if extra:
        conf.update(extra)
    return Consumer(conf)
