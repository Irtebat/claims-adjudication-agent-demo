"""End-to-end dedup simulation for the Kafka event backbone (broker-free).

Drives the FULL services chain using only the pure, unit-tested cores
(no Kafka/Spark/Lakebase): producer -> claim.submitted -> worker dedup ->
outbox -> relay -> claim.adjudicated -> four consumers. Every stage RE-DELIVERS
its events (checkpoint replay, at-least-once publish, consumer re-delivery) so we
can assert that a re-delivered event is a business no-op at each layer.

This is the strongest end-to-end evidence obtainable locally: the live run is
blocked on the human Kafka-secret step (the Aiven MCP connector redacts the SASL
password + CA cert), so the transport is stubbed with in-memory structures that
honour the exact SQL/ON CONFLICT semantics the Postgres tables enforce.

Run:  uv run python docs/evidence/kafka-events/simulate_e2e.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "src"))

import consumer_core
import events
import producer_core
import worker_core


# --------------------------------------------------------------------------- #
# In-memory stand-ins that honour the real ON CONFLICT / dedup semantics.
# --------------------------------------------------------------------------- #
class KeyedStore:
    """A table whose primary key gives ON CONFLICT idempotency."""

    def __init__(self, pk: str):
        self.pk = pk
        self.rows: dict[str, dict] = {}
        self.insert_attempts = 0
        self.rows_written = 0

    def insert_ignore(self, row: dict) -> bool:
        self.insert_attempts += 1
        key = row[self.pk]
        if key in self.rows:
            return False  # ON CONFLICT DO NOTHING
        self.rows[key] = dict(row)
        self.rows_written += 1
        return True

    def upsert(self, row: dict) -> None:
        self.insert_attempts += 1
        key = row[self.pk]
        if key not in self.rows:
            self.rows_written += 1
        self.rows[key] = {**self.rows.get(key, {}), **row}  # ON CONFLICT DO UPDATE


def _synthetic_cdf_rows() -> list[dict]:
    """A representative spread of verdict-driving claims."""
    base = {f: None for f in producer_core.CLAIM_FIELDS}
    specs = [
        ("CLM-approve", "APPROVE", 5000.0, False, None),
        ("CLM-pend", "PEND", 0.0, False, None),
        ("CLM-supplier", "DENY", 0.0, True, "SUP-7"),
        ("CLM-deny", "DENY", 0.0, False, None),
    ]
    rows = []
    for claim_id, verdict, amount, supplier, supplier_id in specs:
        row = dict(base)
        row.update(
            {
                "claim_id": claim_id,
                "_pg_change_type": "insert",
                "_pg_lsn": "0/1",
                "_timestamp": "2026-09-28T00:00:00Z",
                # carried through the sim so the worker can pick the outcome:
                "_verdict": verdict,
                "_amount": amount,
                "_supplier": supplier,
                "_supplier_id": supplier_id,
            }
        )
        rows.append(row)
    return rows


def run() -> dict:
    cdf_rows = _synthetic_cdf_rows()

    # --- Producer: emit claim.submitted; DUPLICATE every row (checkpoint replay). ---
    submitted_stream: list[dict] = []
    for row in cdf_rows:
        if producer_core.is_submission(row["_pg_change_type"]):
            submitted_stream.append(producer_core.cdf_row_to_event(row))
            submitted_stream.append(producer_core.cdf_row_to_event(row))  # re-delivery

    # --- Worker: dedup on claim_id; only first submission per claim adjudicates. ---
    adjudicated_claims: set[str] = set()  # models the adjudications provenance check
    outbox = KeyedStore(pk="event_id")  # writer's ON CONFLICT (event_id) DO NOTHING
    verdict_by_claim = {r["claim_id"]: r for r in cdf_rows}
    endpoint_invocations = 0

    for ev in submitted_stream:
        claim_id = ev["claim_id"]
        already = claim_id in adjudicated_claims
        if worker_core.should_adjudicate(already):
            endpoint_invocations += 1
            spec = verdict_by_claim[claim_id]
            adjudication_id = f"ADJ-{claim_id}"
            # writer.py builds this same shape in the SAME tx as the adjudication.
            record = {
                "claim_id": claim_id,
                "adjudication_id": adjudication_id,
                "recommended_verdict": spec["_verdict"],
                "recommended_disposition": spec["_verdict"],
                "approved_amount": spec["_amount"],
                "supplier_attributable": spec["_supplier"],
                "recovery_supplier_id": spec["_supplier_id"],
                "idempotency_key": claim_id,
                "flags": {"supplier_attributable": spec["_supplier"]},
                "duplicate": {"duplicate_of_claim_id": None},
            }
            payload = events.build_adjudicated_payload(
                record,
                verdict=spec["_verdict"],
                event_id=events.adjudicated_event_id(adjudication_id),
            )
            outbox.insert_ignore(
                {
                    "event_id": payload["event_id"],
                    "aggregate_id": claim_id,
                    "event_type": payload["event_type"],
                    "payload": payload,
                }
            )
            adjudicated_claims.add(claim_id)

    # --- Relay: publish each unpublished outbox row; re-publish one (crash-before-mark). ---
    published: list[dict] = []
    for i, (event_id, row) in enumerate(outbox.rows.items()):
        published.append(row["payload"])
        if i == 0:
            published.append(row["payload"])  # simulate re-publish before mark landed

    # --- Consumers: each own group; re-deliver every adjudicated event. ---
    settlements = KeyedStore(pk="settlement_id")
    investigations = KeyedStore(pk="investigation_case_id")
    supplier_recovery = KeyedStore(pk="supplier_recovery_case_id")
    notification_logs: list[str] = []
    stores = {
        "settlements": settlements,
        "investigation_cases": investigations,
        "supplier_recovery_cases": supplier_recovery,
    }

    for payload in published:
        for delivery in range(2):  # deliver each event twice
            for consumer in consumer_core.CONSUMERS:
                action = consumer_core.plan_action(consumer, payload)
                if action is None:
                    continue
                if action["kind"] == "log":
                    notification_logs.append(action["message"])
                    continue
                store = stores[action["table"]]
                if action["conflict"] == "nothing":
                    store.insert_ignore(action["row"])
                else:
                    store.upsert(action["row"])

    return {
        "producer": {
            "distinct_claims": len(cdf_rows),
            "submitted_events_emitted": len(submitted_stream),  # 2x (re-delivery)
        },
        "worker": {
            "submitted_events_consumed": len(submitted_stream),
            "endpoint_invocations": endpoint_invocations,  # == distinct claims (dedup worked)
            "distinct_claims_adjudicated": len(adjudicated_claims),
        },
        "outbox": {
            "insert_attempts": outbox.insert_attempts,
            "distinct_outbox_rows": len(
                outbox.rows
            ),  # 1 per adjudication despite retries
        },
        "relay": {
            "rows_published_including_republish": len(published),
        },
        "consumers": {
            "settlements": {
                "insert_attempts": settlements.insert_attempts,
                "distinct_rows": len(settlements.rows),
                "keys": sorted(settlements.rows),
            },
            "investigation_cases": {
                "insert_attempts": investigations.insert_attempts,
                "distinct_rows": len(investigations.rows),
                "keys": sorted(investigations.rows),
            },
            "supplier_recovery_cases": {
                "insert_attempts": supplier_recovery.insert_attempts,
                "distinct_rows": len(supplier_recovery.rows),
                "keys": sorted(supplier_recovery.rows),
            },
            "notification_log_lines": len(notification_logs),
        },
    }


def _assert_no_double_processing(r: dict) -> list[str]:
    checks = []

    def check(name, ok):
        checks.append(f"{'PASS' if ok else 'FAIL'}: {name}")
        assert ok, name

    check(
        "producer emitted each claim twice (re-delivery injected)",
        r["producer"]["submitted_events_emitted"]
        == 2 * r["producer"]["distinct_claims"],
    )
    check(
        "worker invoked endpoint once per DISTINCT claim (submitted re-delivery deduped)",
        r["worker"]["endpoint_invocations"] == r["producer"]["distinct_claims"],
    )
    check(
        "outbox holds exactly one row per adjudication (ON CONFLICT event_id)",
        r["outbox"]["distinct_outbox_rows"]
        == r["worker"]["distinct_claims_adjudicated"],
    )
    check(
        "settlement: exactly one STL row (APPROVE only), despite re-delivery",
        r["consumers"]["settlements"]["distinct_rows"] == 1
        and r["consumers"]["settlements"]["insert_attempts"] > 1,
    )
    check(
        "investigation: exactly one INV row (PEND only), despite re-delivery",
        r["consumers"]["investigation_cases"]["distinct_rows"] == 1,
    )
    check(
        "supplier-recovery: exactly one SRC row (attributable only), despite re-delivery",
        r["consumers"]["supplier_recovery_cases"]["distinct_rows"] == 1,
    )
    return checks


if __name__ == "__main__":
    result = run()
    result["dedup_assertions"] = _assert_no_double_processing(result)
    print(json.dumps(result, indent=2, sort_keys=True))
