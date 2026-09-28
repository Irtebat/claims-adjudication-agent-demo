"""Consumer routing + the idempotency proof: re-delivered events never double-process.

An in-memory table honours the planned ``ON CONFLICT`` semantics so we can drive a
``claim.adjudicated`` event through a consumer twice and assert exactly one row —
the same guarantee the deterministic case id + ``ON CONFLICT`` primary key give in
Postgres, verifiable without Lakebase.
"""

import pytest

import consumer_core


# --------------------------------------------------------------------------- #
# In-memory Postgres-ish table that applies plan_action results.
# --------------------------------------------------------------------------- #
class FakeTable:
    """Models a single table's ON CONFLICT (pk) DO NOTHING / DO UPDATE behaviour."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.write_attempts = 0
        self.actual_mutations = 0

    def apply(self, action: dict) -> None:
        self.write_attempts += 1
        pk_col = _pk_column(action["table"])
        pk = action["row"][pk_col]
        if pk in self.rows:
            if action["conflict"] == "nothing":
                return  # DO NOTHING: re-delivery is a strict no-op
            before = dict(self.rows[pk])
            self.rows[pk].update(action["row"])  # DO UPDATE
            if self.rows[pk] != before:
                self.actual_mutations += 1
            return
        self.rows[pk] = dict(action["row"])
        self.actual_mutations += 1


def _pk_column(table: str) -> str:
    return {
        "settlements": "settlement_id",
        "investigation_cases": "investigation_case_id",
        "supplier_recovery_cases": "supplier_recovery_case_id",
    }[table]


def _event(verdict, *, supplier=False, supplier_id=None, amount=5000.0, claim="CLM-9", adj="ADJ-1"):
    return {
        "event_id": f"adj-{adj}",
        "event_type": "claim.adjudicated",
        "schema_version": "claim-event/v1",
        "claim_id": claim,
        "adjudication_id": adj,
        "verdict": verdict,
        "approved_amount": amount,
        "supplier_attributable": supplier,
        "recovery_supplier_id": supplier_id,
    }


# --------------------------------------------------------------------------- #
# Routing.
# --------------------------------------------------------------------------- #
def test_settlement_only_on_approve():
    approve = consumer_core.plan_action(consumer_core.SETTLEMENT, _event("APPROVE"))
    assert approve["table"] == "settlements"
    assert approve["row"]["settlement_id"] == "STL-CLM-9"
    assert approve["row"]["status"] == "PENDING_PAYMENT"
    assert approve["row"]["amount"] == 5000.0
    assert consumer_core.plan_action(consumer_core.SETTLEMENT, _event("DENY")) is None
    assert consumer_core.plan_action(consumer_core.SETTLEMENT, _event("PEND")) is None


def test_investigation_only_on_pend():
    pend = consumer_core.plan_action(consumer_core.INVESTIGATION, _event("PEND"))
    assert pend["table"] == "investigation_cases"
    assert pend["row"]["investigation_case_id"] == "INV-CLM-9"
    assert pend["row"]["status"] == "OPEN"
    assert pend["conflict"] == "nothing"
    assert consumer_core.plan_action(consumer_core.INVESTIGATION, _event("APPROVE")) is None


def test_supplier_recovery_requires_attribution_and_supplier():
    ok = consumer_core.plan_action(
        consumer_core.SUPPLIER_RECOVERY,
        _event("DENY", supplier=True, supplier_id="SUP-7"),
    )
    assert ok["table"] == "supplier_recovery_cases"
    assert ok["row"]["supplier_recovery_case_id"] == "SRC-CLM-9"
    assert ok["row"]["supplier_id"] == "SUP-7"
    # Attributable but no supplier id, or supplier id but not attributable -> skip.
    assert (
        consumer_core.plan_action(
            consumer_core.SUPPLIER_RECOVERY, _event("DENY", supplier=True, supplier_id=None)
        )
        is None
    )
    assert (
        consumer_core.plan_action(
            consumer_core.SUPPLIER_RECOVERY, _event("APPROVE", supplier=False, supplier_id="SUP-7")
        )
        is None
    )


def test_notification_always_logs_no_table():
    action = consumer_core.plan_action(consumer_core.NOTIFICATION, _event("DENY"))
    assert action["kind"] == "log"
    assert "CLM-9" in action["message"]


def test_unknown_consumer_raises():
    with pytest.raises(ValueError, match="Unknown consumer"):
        consumer_core.plan_action("audit", _event("APPROVE"))


# --------------------------------------------------------------------------- #
# Idempotency / dedup proof: re-delivery is a business no-op.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "consumer,event",
    [
        (consumer_core.SETTLEMENT, _event("APPROVE")),
        (consumer_core.INVESTIGATION, _event("PEND")),
        (consumer_core.SUPPLIER_RECOVERY, _event("DENY", supplier=True, supplier_id="SUP-7")),
    ],
)
def test_redelivery_is_a_noop(consumer, event):
    table = FakeTable()
    # Deliver the SAME event three times (broker at-least-once re-delivery).
    for _ in range(3):
        action = consumer_core.plan_action(consumer, event)
        table.apply(action)
    assert len(table.rows) == 1  # exactly one downstream case despite 3 deliveries
    assert table.write_attempts == 3  # all three were attempted
    assert table.actual_mutations == 1  # only the first actually changed state


def test_distinct_claims_create_distinct_cases():
    table = FakeTable()
    for claim, adj in [("CLM-1", "ADJ-1"), ("CLM-2", "ADJ-2")]:
        action = consumer_core.plan_action(
            consumer_core.SETTLEMENT, _event("APPROVE", claim=claim, adj=adj)
        )
        table.apply(action)
    assert set(table.rows) == {"STL-CLM-1", "STL-CLM-2"}
