"""Atomic recommendation writer — adjudications + decision record in one transaction.

One psycopg transaction writes the adjudication recommendation into
``public.adjudications`` AND the full canonical row into
``public.adjudication_decision_records``. The two writes commit together or not at
all. Retries are idempotent: the decision record uses
``ON CONFLICT (adjudication_id, record_version) DO NOTHING`` (append-only,
immutable), and the adjudications recommendation row upserts on ``adjudication_id``.

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
# verdict + outbox row (a separate workstream). verdict is set to the mapped agent
# verdict so the row satisfies the silver business invariant even before finalization.
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


def _upsert_sql(table: str, columns: list[str], pk: str) -> str:
    placeholders = ", ".join(f"%({c})s" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != pk)
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({pk}) DO UPDATE SET {updates}"
    )


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
    """Write the recommendation and decision record atomically in one transaction.

    ``conn`` must be a non-autocommit psycopg connection (``db.connect(...)``).
    Returns a small summary including whether the decision record was newly
    inserted (``inserted``) or already present (idempotent retry).
    """
    adjudication_sql = _upsert_sql("adjudications", ADJUDICATION_COLUMNS, _ADJUDICATION_PK)
    record_sql = _insert_ignore_sql(
        "adjudication_decision_records", DECISION_RECORD_COLUMNS, DECISION_RECORD_PK
    )
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(adjudication_sql, _adjudication_row(record))
            cur.execute(record_sql, _decision_record_row(record))
            inserted = cur.rowcount == 1
    return {
        "adjudication_id": record["adjudication_id"],
        "record_version": record["record_version"],
        "idempotency_key": record["idempotency_key"],
        "decision_record_inserted": inserted,
    }
