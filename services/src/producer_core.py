"""Pure producer core: CDF row transformation for claim.submitted events.

This module is importable without Spark, Kafka, or Databricks dependencies.
It transforms CDF rows into claim.submitted event payloads and defines the
submission change type. Downstream dedup on event_id makes over-emission harmless.
"""

from __future__ import annotations

from events import CLAIM_FIELDS, build_submitted_event

SUBMISSION_CHANGE_TYPE = "insert"


def is_submission(change_type: str) -> bool:
    """True only for CDF insert changes, which represent new claim submissions.

    Over-emission is safe: the Kafka consumer dedup is keyed on event_id and
    claim_id, so re-delivery of the same logical submission is a no-op.
    """
    return change_type == SUBMISSION_CHANGE_TYPE


def cdf_row_to_event(row: dict) -> dict:
    """Transform a CDF row into a claim.submitted event.

    Args:
        row: A CDF history row dict with claim fields + CDF metadata columns
             (_pg_change_type, _pg_lsn, _timestamp, etc.)

    Returns:
        A claim.submitted event dict (JSON-serializable) with event_id,
        event_type, schema_version, claim_id, claim (12 fields), source
        (CDF provenance), and emitted_at.
    """
    # Extract the 12 claim fields for the event payload.
    claim = {field: row.get(field) for field in CLAIM_FIELDS}

    # Build source metadata from CDF columns.
    source = {
        "cdf_change_type": row.get("_pg_change_type"),
        "cdf_commit_version": row.get("_pg_lsn"),
        "cdf_commit_timestamp": row.get("_timestamp"),
    }

    # Delegate payload assembly to the single source of truth (events.py).
    # build_submitted_event coerces all types to JSON-safe scalars.
    return build_submitted_event(claim, source=source)
