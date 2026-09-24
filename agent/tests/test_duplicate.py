"""Deterministic duplicate detection matches identity, not text similarity."""

from duplicate import check_duplicate_claim, duplicate_decision

INCOMING = {
    "claim_id": "CLM-0000060",
    "coil_id": "COIL-0000005",
    "defect_code": "MECH_TENSILE",
    "claimed_freight": 500.0,
    "claimed_tonnage": 12.345,
    "claim_date": "2026-01-02",
    "defect_narrative": "Tensile response during forming differs from ordered requirements.",
}
CANDIDATE = {
    "claim_id": "CLM-0000005",
    "coil_id": "COIL-0000005",
    "defect_code": "MECH_TENSILE",
    "claimed_freight": 500.0,
    "claimed_tonnage": 12.345,
    "claim_date": "2026-01-01",
}


def test_true_duplicate_detected():
    decision = duplicate_decision(INCOMING, CANDIDATE, narrative_similarity=1.0)
    assert decision["is_duplicate"] is True
    assert decision["duplicate_of_claim_id"] == "CLM-0000005"
    assert (decision["verdict"], decision["disposition"], decision["decision_status"]) == ("DENY", "DUPLICATE", "FINAL")


def test_low_narrative_similarity_is_not_duplicate():
    decision = duplicate_decision(INCOMING, CANDIDATE, narrative_similarity=0.3)
    assert decision["is_duplicate"] is False
    assert "narrative_below_threshold" in decision["reasons"]


def test_different_coil_is_not_duplicate():
    other = {**CANDIDATE, "coil_id": "COIL-9999999"}
    decision = duplicate_decision(INCOMING, other, narrative_similarity=1.0)
    assert decision["is_duplicate"] is False
    assert "different_coil" in decision["reasons"]


def test_outside_date_window_is_not_duplicate():
    stale = {**CANDIDATE, "claim_date": "2025-11-01"}
    decision = duplicate_decision(INCOMING, stale, narrative_similarity=1.0)
    assert decision["is_duplicate"] is False
    assert "outside_date_window" in decision["reasons"]


class _Col:
    def __init__(self, name):
        self.name = name


class _FakeCursor:
    def __init__(self, rows, columns):
        self._rows = rows
        self.description = [_Col(c) for c in columns]
        self.executed = None

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows, columns):
        self._cursor = _FakeCursor(rows, columns)

    def cursor(self):
        return self._cursor


def test_check_duplicate_claim_applies_rule_over_blocking_candidates():
    columns = [
        "claim_id",
        "coil_id",
        "defect_code",
        "claimed_freight",
        "claimed_tonnage",
        "claim_date",
        "narrative_similarity",
    ]
    rows = [
        ("CLM-0000005", "COIL-0000005", "MECH_TENSILE", 500.0, 12.345, "2026-01-01", 1.0),
    ]
    conn = _FakeConn(rows, columns)
    result = check_duplicate_claim(conn, INCOMING)
    assert result["is_duplicate"] is True
    assert result["candidates_considered"] == 1
    # the blocking query was parameterized on coil + window, never string-formatted
    sql, params = conn.cursor().executed
    assert params["coil_id"] == "COIL-0000005"
    assert "similarity(defect_narrative" in sql
