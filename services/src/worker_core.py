"""Pure worker core: claim.submitted dedup decision + claim extraction.

Importable without Kafka, Spark, Lakebase, or the serving SDK so the dedup
contract is unit-testable in isolation.

The worker deduplicates on the business key (``claim_id``): before invoking the
governed endpoint it checks whether an *agent-produced* adjudication already
exists for the claim. The check is scoped to ``data_provenance='agent_recommendation'``
so the ~5000 seeded ``synthetic_wave_2_baseline`` adjudications never look
"already agent-adjudicated" — the initial snapshot is adjudicated exactly once
and any re-delivery of a ``claim.submitted`` event is a no-op. The endpoint's
``agent/src/writer.py`` adds a second, deterministic guarantee (idempotent
``adjudication_id`` upsert + outbox ``ON CONFLICT (event_id)``), so even a
race between the dedup check and the write cannot double-adjudicate.
"""

from __future__ import annotations

from events import AGENT_PROVENANCE, deserialize

# An agent recommendation already exists for this claim => skip the endpoint call.
ALREADY_ADJUDICATED_SQL = (
    "SELECT 1 FROM adjudications WHERE claim_id = %s AND data_provenance = %s LIMIT 1"
)


def dedup_params(claim_id: str) -> tuple[str, str]:
    """Bound parameters for :data:`ALREADY_ADJUDICATED_SQL` (claim_id, provenance)."""
    return (claim_id, AGENT_PROVENANCE)


def should_adjudicate(already_exists: bool) -> bool:
    """Adjudicate only when no agent recommendation exists yet for the claim."""
    return not already_exists


def parse_submitted(raw: bytes | str) -> dict:
    """Deserialize a ``claim.submitted`` Kafka value into its event dict."""
    return deserialize(raw)


def claim_from_event(event: dict) -> dict:
    """Extract the claim payload the serving endpoint expects from the event."""
    return event["claim"]
