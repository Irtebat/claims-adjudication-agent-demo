"""Event payload contract + dedup/idempotency keys — the single source of truth.

Both Kafka topics carry JSON values keyed by ``claim_id``. Every record also carries
a stable ``event_id`` so downstream achieves exactly-once BUSINESS processing by
deduplicating on it — re-delivery of the same logical event is a no-op.

- ``claim.submitted`` is built here (producer) and consumed by the worker.
- ``claim.adjudicated`` is produced on HUMAN FINALIZATION by the Databricks App's
  finalize transaction into the Postgres ``outbox`` (the App mirrors
  :func:`build_adjudicated_payload`'s shape); the relay publishes it verbatim and the
  consumers parse it here. The agent's recommendation write (``agent/src/writer.py``)
  no longer emits this event — a claim stays ``RECOMMENDED`` until an adjuster
  finalizes it, so the fan-out fires exactly once, on the final human decision.

This module is pure Python (stdlib only) so the dedup contract is unit-testable
without Kafka, Spark, or Lakebase.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "claim-event/v1"
EVENT_CLAIM_SUBMITTED = "claim.submitted"
EVENT_CLAIM_ADJUDICATED = "claim.adjudicated"

# The 12 operational claim columns the agent needs (mirrors agent/src invocation).
CLAIM_FIELDS = (
    "claim_id",
    "coil_id",
    "customer_id",
    "claim_type",
    "claim_date",
    "install_date",
    "environment",
    "installation",
    "coast_distance_km",
    "defect_code",
    "defect_narrative",
    "claimed_tonnage",
    "claimed_freight",
)

# Provenance tag written by the agent path; the worker dedup is scoped to it so the
# seeded ``synthetic_reference_baseline`` adjudications never look "already agent-adjudicated".
AGENT_PROVENANCE = "agent_recommendation"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Stable ids — deterministic so re-delivery / retry dedups.
# --------------------------------------------------------------------------- #
def submitted_event_id(claim_id: str) -> str:
    """Stable id for a claim's submission event (one logical submission per claim)."""
    return f"sub-{claim_id}"


def adjudicated_event_id(adjudication_id: str) -> str:
    """Stable+unique id for an adjudication outcome event (== outbox event_id)."""
    return f"adj-{adjudication_id}"


def settlement_id(claim_id: str) -> str:
    return f"STL-{claim_id}"


def investigation_case_id(claim_id: str) -> str:
    return f"INV-{claim_id}"


def supplier_recovery_case_id(claim_id: str) -> str:
    return f"SRC-{claim_id}"


# --------------------------------------------------------------------------- #
# claim.submitted (producer -> worker).
# --------------------------------------------------------------------------- #
def _coerce(value: Any) -> Any:
    """JSON-safe scalar coercion for Decimal / date / datetime from Spark rows."""
    from decimal import Decimal

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def build_submitted_event(claim: dict, source: dict | None = None) -> dict:
    """Assemble a claim.submitted event value from a claim row (pure)."""
    claim_id = claim["claim_id"]
    claim_out = {field: _coerce(claim.get(field)) for field in CLAIM_FIELDS}
    return {
        "event_id": submitted_event_id(claim_id),
        "event_type": EVENT_CLAIM_SUBMITTED,
        "schema_version": SCHEMA_VERSION,
        "claim_id": claim_id,
        "claim": claim_out,
        "source": source or {},
        "emitted_at": utc_now_iso(),
    }


# --------------------------------------------------------------------------- #
# claim.adjudicated (writer -> outbox -> relay -> consumers).
# --------------------------------------------------------------------------- #
def build_adjudicated_payload(record: dict, *, verdict: str, event_id: str) -> dict:
    """Build the claim.adjudicated event value from a decision record.

    ``verdict`` is the operational verdict stored on ``adjudications.verdict``
    (APPROVE/DENY/PEND). This is the canonical payload shape; the Databricks App's
    finalize transaction (the authoritative producer at runtime) mirrors it, reflecting
    the FINAL human decision (verdict/disposition/approved_amount).
    """
    flags = record.get("flags") or {}
    return {
        "event_id": event_id,
        "event_type": EVENT_CLAIM_ADJUDICATED,
        "schema_version": SCHEMA_VERSION,
        "claim_id": record["claim_id"],
        "adjudication_id": record["adjudication_id"],
        "verdict": verdict,
        "recommended_verdict": record["recommended_verdict"],
        "disposition": record["recommended_disposition"],
        "approved_amount": _coerce(record.get("approved_amount")),
        "supplier_attributable": bool(flags.get("supplier_attributable")),
        "recovery_supplier_id": record.get("recovery_supplier_id"),
        "duplicate_of_claim_id": (record.get("duplicate") or {}).get("duplicate_of_claim_id"),
        "idempotency_key": record["idempotency_key"],
        "recommended_at": utc_now_iso(),
    }


# --------------------------------------------------------------------------- #
# (De)serialization.
# --------------------------------------------------------------------------- #
def serialize(event: dict) -> bytes:
    return json.dumps(event, sort_keys=True, default=_coerce).encode("utf-8")


def deserialize(raw: bytes | str) -> dict:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)
