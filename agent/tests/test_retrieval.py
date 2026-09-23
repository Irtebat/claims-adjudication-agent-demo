"""RRF fusion, metadata pre-filters folded into both arms, parent-clause hybrid retrieval."""

from retrieval import (
    _build_filters,
    find_similar_prior_claims,
    retrieve_policy_clauses,
    rrf_fuse,
)


def test_rrf_fuse_orders_by_reciprocal_rank():
    fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]], k=60)
    ids = [doc for doc, _ in fused]
    assert ids[:2] == ["a", "b"]  # a and b appear in both arms, near the top
    assert set(ids) == {"a", "b", "c", "d"}


def test_spec_filters_folded_in():
    where, params = _build_filters("spec", {"grade": "ASTM A653 CS Type B", "region": "NA"})
    assert "grade = %(grade)s" in where and "region = %(region)s" in where
    assert params == {"grade": "ASTM A653 CS Type B", "region": "NA"}


def test_warranty_filters_include_effective_window():
    where, params = _build_filters(
        "warranty",
        {
            "product_line": "galvanized",
            "coating_class": "G90",
            "region": "NA",
            "ship_date": "2018-01-01",
        },
    )
    assert "effective_from" in where and "effective_to" in where
    assert params["ship_date"] == "2018-01-01"


class _Col:
    def __init__(self, name):
        self.name = name


class _MultiCursor:
    """Returns canned result sets in call order (vector arm, then keyword arm)."""

    def __init__(self, result_sets):
        self._sets = list(result_sets)
        self._current = None
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._current = self._sets.pop(0)

    def fetchall(self):
        return self._current["rows"]

    @property
    def description(self):
        return [_Col(c) for c in self._current["columns"]]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_retrieve_policy_clauses_uses_bm25_only():
    cols = ["clause_id", "parent_clause_id", "section_ref", "clause_text"]
    keyword_rows = [
        ("W-galvanized-NA-V2:exclusions", "W-galvanized-NA-V2", "exclusions", "Does not cover ..."),
        ("W-galvanized-NA-V2:coverage", "W-galvanized-NA-V2", "coverage", "Coverage lasts ..."),
    ]
    cursor = _MultiCursor([{"rows": keyword_rows, "columns": cols}])
    conn = _FakeConn(cursor)
    results = retrieve_policy_clauses(
        conn,
        embed_fn=lambda texts: [[0.1] * 4 for _ in texts],
        query="does the warranty cover marine environments",
        corpus="warranty",
        filters={
            "product_line": "galvanized",
            "coating_class": "G90",
            "region": "NA",
            "ship_date": "2018-01-01",
        },
    )
    assert results[0]["clause_id"] == "W-galvanized-NA-V2:exclusions"
    assert "embedding" not in cursor.calls[0][0]
    assert "lakebase_bm25" not in cursor.calls[0][0] or "to_bm25query" in cursor.calls[0][0]
    for _, params in cursor.calls:
        assert params["product_line"] == "galvanized"
        assert params["ship_date"] == "2018-01-01"


def test_find_similar_prior_claims_rrf_over_arms():
    cols = ["claim_id", "arm", "rnk"]
    rows = [
        ("CLM-A", "trgm", 1),
        ("CLM-B", "trgm", 2),
        ("CLM-A", "fts", 1),
        ("CLM-C", "fts", 2),
    ]
    cursor = _MultiCursor([{"rows": rows, "columns": cols}])
    conn = _FakeConn(cursor)
    results = find_similar_prior_claims(
        conn, lambda texts: [[0.1] * 1024], text="edge failure", coil_id="COIL-1", filters={"grade": "ASTM A653 CS Type B"}
    )
    assert results[0]["claim_id"] == "CLM-A"  # in both arms at rank 1
    _, params = cursor.calls[0]
    assert params["coil_id"] == "COIL-1"
    assert params["grade"] == "ASTM A653 CS Type B"
