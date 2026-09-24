"""Advisory customer/heat risk lookup — returns the row, or a neutral default."""

from heat_risk import get_customer_heat_risk


class _Col:
    def __init__(self, name):
        self.name = name


class _Cursor:
    def __init__(self, columns, rows):
        self.description = [_Col(c) for c in columns]
        self._rows = rows
        self.params = None

    def execute(self, sql, params=None):
        self.params = params

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, columns, rows):
        self._cur = _Cursor(columns, rows)

    def cursor(self):
        return self._cur


COLUMNS = [
    "customer_id",
    "heat_no",
    "cluster_id",
    "cluster_size",
    "distinct_customers_in_cluster",
    "distinct_heats_in_cluster",
    "repeat_customers",
    "risk_score",
    "computed_at",
]


def test_returns_matching_risk_row():
    row = ("CUST-1", "HEAT-1", "cl-1", 5, 3, 2, True, 0.75, "2026-01-01T00:00:00Z")
    conn = _Conn(COLUMNS, [row])
    result = get_customer_heat_risk(conn, "CUST-1", "HEAT-1")
    assert result["found"] is True
    assert result["risk_score"] == 0.75
    assert result["cluster_id"] == "cl-1"
    assert conn._cur.params == {"customer_id": "CUST-1", "heat_no": "HEAT-1"}


def test_missing_row_is_neutral_not_an_error():
    conn = _Conn(COLUMNS, [])
    result = get_customer_heat_risk(conn, "CUST-2", "HEAT-2")
    assert result["found"] is False
    assert result["risk_score"] == 0.0
    assert result["cluster_id"] is None
