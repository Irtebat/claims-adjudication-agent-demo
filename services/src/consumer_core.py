"""Pure consumer core: map a claim.adjudicated event to an idempotent DB action.

Importable without Kafka or Lakebase so the routing + dedup contract is
unit-testable. Four downstream consumers each own a Kafka consumer group and
react to ``claim.adjudicated``:

- ``settlement``        verdict APPROVE  -> UPSERT ``settlements`` ``STL-<claim_id>``
- ``investigation``     verdict PEND     -> INSERT ``investigation_cases`` ``INV-<claim_id>``
- ``supplier-recovery`` supplier_attributable & recovery_supplier_id
                                         -> INSERT ``supplier_recovery_cases`` ``SRC-<claim_id>``
- ``notification``      always           -> LOG ONLY (no table)

**Dedup.** Every case id is a pure, deterministic function of the claim carried
in the event, and one claim maps 1:1 to one adjudication (the writer's
idempotent ``adjudication_id``), so the case id is 1:1 with the event's
``event_id`` (``adj-<adjudication_id>``). Writing behind ``ON CONFLICT`` on the
case-id primary key therefore deduplicates re-delivered events: processing the
same event twice is a no-op (``DO NOTHING``) or converges to the identical row
(``DO UPDATE`` with the same inputs). The notification consumer holds no state,
so re-delivery is inherently harmless.

Each ``plan_action`` result is one of:
- ``{"kind": "write", "sql": ..., "row": {...}, "table": ..., "case_id": ...}``
- ``{"kind": "log", "message": ...}``
- ``None`` (this event is not actionable for this consumer)
"""

from __future__ import annotations

from events import (
    investigation_case_id,
    settlement_id,
    supplier_recovery_case_id,
)

SETTLEMENT = "settlement"
INVESTIGATION = "investigation"
SUPPLIER_RECOVERY = "supplier-recovery"
NOTIFICATION = "notification"

CONSUMERS = (SETTLEMENT, INVESTIGATION, SUPPLIER_RECOVERY, NOTIFICATION)

# One Kafka consumer group per downstream so each tracks its own offsets.
CONSUMER_GROUPS = {
    SETTLEMENT: "fe-bar-consumer-settlement",
    INVESTIGATION: "fe-bar-consumer-investigation",
    SUPPLIER_RECOVERY: "fe-bar-consumer-supplier-recovery",
    NOTIFICATION: "fe-bar-consumer-notification",
}

# Column order per table (matches lakebase/src/setup_and_seed.py DDL).
_SETTLEMENT_COLUMNS = ["settlement_id", "claim_id", "status", "amount"]
_INVESTIGATION_COLUMNS = ["investigation_case_id", "claim_id", "status", "notes"]
_SUPPLIER_RECOVERY_COLUMNS = [
    "supplier_recovery_case_id",
    "claim_id",
    "supplier_id",
    "status",
    "recovery_amount",
]


def _named_placeholders(columns: list[str]) -> str:
    return ", ".join(f"%({c})s" for c in columns)


def _upsert_sql(table: str, columns: list[str], pk: str) -> str:
    """INSERT ... ON CONFLICT (pk) DO UPDATE — idempotent for identical inputs."""
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != pk)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({_named_placeholders(columns)}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {updates}"
    )


def _insert_ignore_sql(table: str, columns: list[str], pk: str) -> str:
    """INSERT ... ON CONFLICT (pk) DO NOTHING — re-delivery is a no-op."""
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({_named_placeholders(columns)}) "
        f"ON CONFLICT ({pk}) DO NOTHING"
    )


def plan_action(consumer: str, event: dict) -> dict | None:
    """Return the idempotent DB/log action for one consumer + one event (pure).

    Returns ``None`` when the event is not actionable for the given consumer
    (e.g. a DENY verdict reaches the settlement consumer).
    """
    if consumer not in CONSUMER_GROUPS:
        raise ValueError(f"Unknown consumer: {consumer!r}")

    verdict = event.get("verdict")
    claim_id = event["claim_id"]
    event_id = event.get("event_id")

    if consumer == SETTLEMENT:
        if verdict != "APPROVE":
            return None
        case_id = settlement_id(claim_id)
        return {
            "kind": "write",
            "conflict": "update",
            "table": "settlements",
            "case_id": case_id,
            "event_id": event_id,
            "sql": _upsert_sql("settlements", _SETTLEMENT_COLUMNS, "settlement_id"),
            "row": {
                "settlement_id": case_id,
                "claim_id": claim_id,
                "status": "PENDING_PAYMENT",
                "amount": event.get("approved_amount"),
            },
        }

    if consumer == INVESTIGATION:
        if verdict != "PEND":
            return None
        case_id = investigation_case_id(claim_id)
        return {
            "kind": "write",
            "conflict": "nothing",
            "table": "investigation_cases",
            "case_id": case_id,
            "event_id": event_id,
            "sql": _insert_ignore_sql(
                "investigation_cases", _INVESTIGATION_COLUMNS, "investigation_case_id"
            ),
            "row": {
                "investigation_case_id": case_id,
                "claim_id": claim_id,
                "status": "OPEN",
                "notes": f"Opened from {event_id} (verdict PEND).",
            },
        }

    if consumer == SUPPLIER_RECOVERY:
        supplier_id = event.get("recovery_supplier_id")
        if not (event.get("supplier_attributable") and supplier_id):
            return None
        case_id = supplier_recovery_case_id(claim_id)
        return {
            "kind": "write",
            "conflict": "nothing",
            "table": "supplier_recovery_cases",
            "case_id": case_id,
            "event_id": event_id,
            "sql": _insert_ignore_sql(
                "supplier_recovery_cases", _SUPPLIER_RECOVERY_COLUMNS, "supplier_recovery_case_id"
            ),
            "row": {
                "supplier_recovery_case_id": case_id,
                "claim_id": claim_id,
                "supplier_id": supplier_id,
                "status": "OPEN",
                "recovery_amount": event.get("approved_amount"),
            },
        }

    # notification: log only, no persistence; re-delivery is inherently harmless.
    return {
        "kind": "log",
        "event_id": event_id,
        "message": (
            f"notification: claim {claim_id} adjudicated verdict={verdict} "
            f"(event {event_id}); no downstream table."
        ),
    }
