"""Atomic recommendation writer — adjudications + decision record in one tx.

One psycopg transaction writes the adjudication *recommendation* into
``public.adjudications`` (``decision_status='RECOMMENDED'``) and the full canonical
row into ``public.adjudication_decision_records``. Both writes commit together or
not at all, so an adjudication and its immutable decision record can never diverge.
Retries are idempotent and **first-write-wins**: both writes use ``ON CONFLICT DO
NOTHING`` — the adjudications row on ``adjudication_id`` and the decision record on
``(adjudication_id, record_version)`` (append-only, immutable). Because the
``adjudication_id`` is derived deterministically from the decision, a retry (even one
carrying different decision data under the same id) leaves BOTH rows untouched, so
they can never diverge.

The event fan-out has MOVED to human finalization: the recommendation transaction
emits **NO** ``public.outbox`` row. A claim stays pending (``RECOMMENDED``) until an
adjuster finalizes it in the Databricks App, and only that human-finalization
transaction writes the ``claim.adjudicated`` outbox row (event_id
``adj-<adjudication_id>``, payload reflecting the FINAL human decision). The outbox
relay (services/) then publishes it to Kafka once the broker acks
(transactional-outbox pattern). The canonical event payload shape lives in
``services/src/events.py:build_adjudicated_payload``; the App finalizer mirrors it.

The agent verdict (APPROVE/DENY/PEND_INVESTIGATE) is mapped to the operational
``adjudications.verdict`` value (APPROVE/DENY/PEND) that the silver history
contract constrains; a PEND_INVESTIGATE hold moves no money.
"""

from __future__ import annotations

from typing import Any

from decision_record import (
    DECISION_RECORD_COLUMNS,
    JSONB_COLUMNS,
    adjudication_verdict,
)

# Recommendation-carrying columns written to public.adjudications. The row is a
# RECOMMENDED (not finalized) adjudication: an adjuster later writes the human-final
# verdict (and, at that point, the claim.adjudicated outbox row) in the App. No outbox
# row is written here. The write is insert-ignore (first-write-wins) on
# adjudication_id, matching the decision record, so a same-id retry never mutates it
# out of step with the immutable record. verdict is set to the mapped agent verdict so
# the row satisfies the silver business invariant even before finalization.
# decided_by / override_reason (added by the finalization migration) stay NULL on a
# recommendation and are populated only by the App's human-finalization transaction.
ADJUDICATION_COLUMNS = [
    "adjudication_id",
    "claim_id",
    "verdict",
    "recommended_verdict",
    "disposition",
    "recommended_disposition",
    "claimed_amount",
    "approved_amount",
    "covered_tonnage",
    "freight_covered",
    "supplier_attributable",
    "recovery_supplier_id",
    "defect_failure_mode_code",
    "override_flag",
    "decision_status",
    "rationale",
    "cited_clause_ids",
    "confidence",
    "flags",
    "advisory_risk",
    "precedent",
    "idempotency_key",
    "decision_record_version",
    "recommended_at",
    "finalized_at",
    "duplicate_of_claim_id",
    "fraud_cluster_id",
    "data_provenance",
]
_ADJUDICATION_JSONB = frozenset({"flags", "advisory_risk", "precedent"})
_ADJUDICATION_PK = "adjudication_id"
DECISION_RECORD_PK = ("adjudication_id", "record_version")
DATA_PROVENANCE = "agent_recommendation"


def _json_default(obj: Any):
    """Serialize the Decimal/date values that psycopg returns inside the snapshots."""
    from datetime import date, datetime
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return str(obj)


def _jsonb(value: Any):
    import json

    from psycopg.types.json import Jsonb

    return Jsonb(value, dumps=lambda v: json.dumps(v, default=_json_default))


def _insert_ignore_sql(table: str, columns: list[str], pk: tuple[str, ...]) -> str:
    placeholders = ", ".join(f"%({c})s" for c in columns)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({', '.join(pk)}) DO NOTHING"
    )


def _adjudication_row(record: dict) -> dict:
    """Project the canonical decision record onto the adjudications recommendation row."""
    flags = record.get("flags") or {}
    advisory = record.get("advisory_risk") or {}
    settlement = record.get("settlement") or {}
    values = {
        "adjudication_id": record["adjudication_id"],
        "claim_id": record["claim_id"],
        "verdict": adjudication_verdict(record["recommended_verdict"]),
        "recommended_verdict": adjudication_verdict(record["recommended_verdict"]),
        "disposition": record["recommended_disposition"],
        "recommended_disposition": record["recommended_disposition"],
        "claimed_amount": record["claimed_amount"],
        "approved_amount": record["approved_amount"],
        "covered_tonnage": settlement.get("covered_tonnage"),
        "freight_covered": bool(settlement.get("freight_covered")),
        "supplier_attributable": bool(flags.get("supplier_attributable")),
        "recovery_supplier_id": record.get("recovery_supplier_id"),
        "defect_failure_mode_code": record.get("defect_failure_mode_code"),
        "override_flag": False,
        "decision_status": "RECOMMENDED",
        "rationale": record.get("rationale"),
        "cited_clause_ids": list(record.get("cited_clause_ids") or []),
        "confidence": record.get("confidence"),
        "flags": _jsonb(flags),
        "advisory_risk": _jsonb(advisory),
        "precedent": _jsonb(record.get("precedent") or []),
        "idempotency_key": record["idempotency_key"],
        "decision_record_version": record["record_version"],
        "recommended_at": None,  # DB default now() applies when omitted; explicit NULL is fine
        "finalized_at": None,
        "duplicate_of_claim_id": (record.get("duplicate") or {}).get("duplicate_of_claim_id"),
        "fraud_cluster_id": advisory.get("cluster_id"),
        "data_provenance": DATA_PROVENANCE,
    }
    return values


def _decision_record_row(record: dict) -> dict:
    """Bind JSONB columns; scalars and text[] pass through unchanged."""
    row = {}
    for column in DECISION_RECORD_COLUMNS:
        value = record.get(column)
        row[column] = _jsonb(value) if column in JSONB_COLUMNS else value
    return row


def write_adjudication(conn: Any, record: dict) -> dict:
    """Write the recommendation and its immutable decision record atomically.

    ``conn`` must be a non-autocommit psycopg connection (``db.connect(...)``). One
    transaction writes ``adjudications`` (``decision_status='RECOMMENDED'``) and
    ``adjudication_decision_records`` — both insert-ignore (first-write-wins) on their
    stable keys, so a same-``adjudication_id`` retry mutates neither and they can never
    diverge. NO ``outbox`` row is written: the ``claim.adjudicated`` event fan-out now
    fires only when an adjuster finalizes the claim in the App. Returns a small summary
    including whether each row was newly inserted or already present (idempotent
    retry). On any given call the two ``*_inserted`` flags are identical: either the
    first write inserted both, or a retry inserted neither.
    """
    adjudication_sql = _insert_ignore_sql(
        "adjudications", ADJUDICATION_COLUMNS, (_ADJUDICATION_PK,)
    )
    record_sql = _insert_ignore_sql(
        "adjudication_decision_records", DECISION_RECORD_COLUMNS, DECISION_RECORD_PK
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(adjudication_sql, _adjudication_row(record))
            adjudication_inserted = cur.rowcount == 1
            cur.execute(record_sql, _decision_record_row(record))
            inserted = cur.rowcount == 1
    return {
        "adjudication_id": record["adjudication_id"],
        "record_version": record["record_version"],
        "idempotency_key": record["idempotency_key"],
        "adjudication_inserted": adjudication_inserted,
        "decision_record_inserted": inserted,
    }
