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

A claim that already carries a human decision (``decision_status`` FINAL or
REVIEWED, e.g. the seeded history replayed by the producer's CDF snapshot) is also
skipped: re-recommending a decided claim would only write a pointless RECOMMENDED
row. Either skip still commits the offset, so the message is handled, not dropped.
"""

from __future__ import annotations

from events import AGENT_PROVENANCE, deserialize

# Human-decided statuses: a claim carrying one needs no agent recommendation.
FINAL_STATUSES = ("FINAL", "REVIEWED")

# One row per claim, always (aggregate, no GROUP BY); both flags are NULL when the
# claim has no adjudication at all.
DEDUP_SQL = (
    "SELECT bool_or(decision_status = ANY(%s)) AS has_final,"
    " bool_or(data_provenance = %s) AS has_agent"
    " FROM adjudications WHERE claim_id = %s"
)

ADJUDICATE = "adjudicate"
SKIP_FINAL = "skip_already_final"
SKIP_AGENT = "skip_already_adjudicated"
CONSUMER_ERROR = "consumer_error"


def dedup_params(claim_id: str) -> tuple[list[str], str, str]:
    """Bound parameters for :data:`DEDUP_SQL` (final statuses, provenance, claim_id)."""
    return (list(FINAL_STATUSES), AGENT_PROVENANCE, claim_id)


def dedup_decision(row: tuple | None) -> str:
    """Map a :data:`DEDUP_SQL` row to ADJUDICATE / SKIP_FINAL / SKIP_AGENT."""
    has_final, has_agent = row if row else (None, None)
    if has_final:
        return SKIP_FINAL
    if has_agent:
        return SKIP_AGENT
    return ADJUDICATE


def handle_submitted(raw: bytes | str, *, lookup, invoke, commit) -> str:
    """Dedup, maybe invoke, then commit one ``claim.submitted`` message.

    ``lookup(claim_id)`` returns the :data:`DEDUP_SQL` row, ``invoke(claim)`` calls the
    governed endpoint, ``commit()`` commits this message's offset. The commit runs only
    after the claim is invoked or deliberately skipped; an exception from ``lookup`` or
    ``invoke`` propagates before it, so the message is re-delivered (at-least-once).
    """
    event = parse_submitted(raw)
    outcome = dedup_decision(lookup(event["claim_id"]))
    if outcome == ADJUDICATE:
        invoke(claim_from_event(event))
    commit()
    return outcome


def parse_submitted(raw: bytes | str) -> dict:
    """Deserialize a ``claim.submitted`` Kafka value into its event dict."""
    return deserialize(raw)


def claim_from_event(event: dict) -> dict:
    """Extract the claim payload the serving endpoint expects from the event."""
    return event["claim"]


def handle_message(msg, *, lookup, invoke, commit) -> str:
    """Handle one polled message; a Kafka error/event message is never committed.

    An error message (transport error, partition EOF, ...) carries no claim, so it was
    neither invoked nor deliberately skipped: committing it could advance the offset
    past an unprocessed claim. It is counted as CONSUMER_ERROR and left uncommitted.
    """
    if msg.error():
        return CONSUMER_ERROR
    return handle_submitted(msg.value(), lookup=lookup, invoke=invoke, commit=commit)
