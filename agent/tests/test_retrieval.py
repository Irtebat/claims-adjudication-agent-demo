"""RRF fusion, metadata pre-filters folded into both arms, parent-clause hybrid retrieval."""

from pathlib import Path

from retrieval import (
    _build_filters,
    find_similar_prior_claims,
    retrieve_policy_clauses,
    rrf_fuse,
)


def test_similar_claims_rank_assignments_are_distance_ascending():
    import retrieval

    sql = retrieval.SIMILAR_CLAIMS_SQL
    # Smaller cosine distance / BM25 score is better: both arms scan and rank ASC.
    assert sql.count("row_number() OVER (ORDER BY s ASC, claim_id)") == 2
    assert "DESC" not in sql
    assert sql.count(" ASC\n  LIMIT %(k)s") == 2


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
    cols = ["citation_key", "section_ref", "clause_text"]
    keyword_rows = [
        ("galvanized/NA/V2/exclusions", "exclusions", "Does not cover ..."),
        ("galvanized/NA/V2/coverage", "coverage", "Coverage lasts ..."),
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
    assert results[0]["citation_key"] == "galvanized/NA/V2/exclusions"
    assert "embedding" not in cursor.calls[0][0]
    assert "lakebase_bm25" not in cursor.calls[0][0] or "to_bm25query" in cursor.calls[0][0]
    for _, params in cursor.calls:
        assert params["product_line"] == "galvanized"
        assert params["ship_date"] == "2018-01-01"


def test_find_similar_prior_claims_rrf_over_arms():
    cols = ["claim_id", "verdict", "approved_amount", "arm", "rnk"]
    rows = [
        ("CLM-A", "APPROVE", 1250.0, "dense", 1),
        ("CLM-B", "DENY", 0.0, "dense", 2),
        ("CLM-A", "APPROVE", 1250.0, "fts", 1),
        ("CLM-C", "PEND", 0.0, "fts", 2),
    ]
    cursor = _MultiCursor([{"rows": rows, "columns": cols}])
    conn = _FakeConn(cursor)
    results = find_similar_prior_claims(
        conn,
        lambda texts: [[0.1] * 1024],
        text="edge failure",
        coil_id="COIL-1",
        filters={"grade": "ASTM A653 CS Type B"},
    )
    assert results[0] == {
        "claim_id": "CLM-A",
        "rrf_score": 0.032787,
        "verdict": "APPROVE",
        "approved_amount": 1250.0,
    }
    _, params = cursor.calls[0]
    assert params["coil_id"] == "COIL-1"
    assert params["grade"] == "ASTM A653 CS Type B"


def _synced_tables():
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[2] / "lakebase" / "scripts" / "synced_tables.py"
    spec = importlib.util.spec_from_file_location("synced_tables_for_retrieval_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module via sys.modules
    spec.loader.exec_module(module)
    return module


def _arms(sql):
    """Split the statement into its two arm subqueries: (arm label, inner SELECT)."""
    import re

    arms = re.findall(r"FROM \((SELECT .*?LIMIT %\(k\)s)\) (dense|fts)\b", sql, re.S)
    return {label: inner for inner, label in arms}


def _order_by_operand(inner):
    """The left operand of the arm's ORDER BY distance/score (the indexed expression)."""
    import re

    match = re.search(r"ORDER BY (.+?) (<=>|<@>) ", inner)
    return match.group(1), match.group(2)


def _run_similar(filters=None):
    cols = ["claim_id", "verdict", "approved_amount", "arm", "rnk"]
    cursor = _MultiCursor([{"rows": [("CLM-A", "APPROVE", 10.0, "dense", 1)], "columns": cols}])
    find_similar_prior_claims(
        _FakeConn(cursor),
        lambda texts: [[0.1] * 1024],
        text="edge",
        coil_id="COIL-1",
        filters=filters,
    )
    return cursor.calls[0]


def test_each_arm_orders_by_exactly_the_indexed_expression():
    import prior_claims_indexes as shared

    st = _synced_tables()
    ann_ddl, bm25_ddl = st.POST_CREATE_SQL["prior_claims_corpus"]
    sql, _ = _run_similar()
    arms = _arms(sql)
    assert set(arms) == {"dense", "fts"}

    dense_expr, dense_op = _order_by_operand(arms["dense"])
    fts_expr, fts_op = _order_by_operand(arms["fts"])
    # Character-identical to the expression inside each index definition.
    assert dense_op == "<=>" and dense_expr == shared.PRIOR_CLAIMS_EMBEDDING_EXPR
    assert f"(({dense_expr}) vector_cosine_ops)" in ann_ddl
    assert "USING lakebase_ann" in ann_ddl
    assert fts_op == "<@>" and fts_expr == shared.PRIOR_CLAIMS_TSVECTOR_EXPR
    assert f"(({fts_expr}) tsvector_bm25_ops)" in bm25_ddl
    assert "USING lakebase_bm25" in bm25_ddl
    # The BM25 query uses the same text-search config as the indexed tsvector.
    config = shared.PRIOR_CLAIMS_TEXT_SEARCH_CONFIG
    assert f"to_tsvector('{config}', defect_narrative)" == fts_expr
    assert f"to_bm25query(to_tsvector('{config}', %(text)s)" in arms["fts"]
    assert f"'reference.{shared.PRIOR_CLAIMS_BM25_INDEX}'::regclass" in arms["fts"]
    assert shared.PRIOR_CLAIMS_BM25_INDEX in bm25_ddl


def test_synced_tables_builds_its_ddl_from_the_shared_constants_file():
    import prior_claims_indexes as shared

    st = _synced_tables()
    assert st.PRIOR_CLAIMS_INDEXES_PATH.resolve() == Path(shared.__file__).resolve()
    for name in (
        "PRIOR_CLAIMS_TABLE",
        "PRIOR_CLAIMS_EMBEDDING_EXPR",
        "PRIOR_CLAIMS_TSVECTOR_EXPR",
        "PRIOR_CLAIMS_ANN_INDEX",
        "PRIOR_CLAIMS_BM25_INDEX",
    ):
        assert getattr(st._indexes, name) == getattr(shared, name)
    # retrieval re-exports the very same objects, not copies.
    import retrieval

    assert retrieval.PRIOR_CLAIMS_EMBEDDING_EXPR is shared.PRIOR_CLAIMS_EMBEDDING_EXPR
    assert f"'{shared.PRIOR_CLAIMS_TEXT_SEARCH_CONFIG}'" in shared.PRIOR_CLAIMS_TSVECTOR_EXPR


def test_shared_constants_module_has_no_imports_and_only_string_constants():
    import ast

    import prior_claims_indexes as shared

    tree = ast.parse(Path(shared.__file__).read_text())
    body = tree.body[1:] if isinstance(tree.body[0], ast.Expr) else tree.body  # docstring
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(tree))
    for node in body:
        assert isinstance(node, ast.Assign), ast.dump(node)
        assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def test_shared_constants_load_in_a_bare_interpreter_from_the_lakebase_dir():
    # -I -S: isolated mode, no site-packages (so no agent deps), no user paths; run
    # from lakebase/ exactly as lakebase/run.py runs synced_tables.py.
    import subprocess
    import sys

    import prior_claims_indexes as shared

    repo = Path(__file__).resolve().parents[2]
    code = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('m', {str(Path(shared.__file__))!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(json.dumps({k: v for k, v in vars(m).items() if k.startswith('PRIOR_')}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        cwd=repo / "lakebase",
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    import json

    assert json.loads(out)["PRIOR_CLAIMS_EMBEDDING_EXPR"] == shared.PRIOR_CLAIMS_EMBEDDING_EXPR


def test_arms_query_the_table_directly_with_no_shared_cte():
    import re

    sql, params = _run_similar({"grade": "G550", "coating_class": "G90"})
    assert not re.search(r"\bWITH\b", sql, re.I)  # no CTE at all, so none is shared
    assert "public.prior_claims" not in sql
    arms = _arms(sql)
    for label, inner in arms.items():
        # Each arm scans the corpus itself, with the metadata filter inlined.
        assert inner.count("FROM reference.prior_claims_corpus") == 1, label
        assert "coil_id <> %(coil_id)s" in inner
        assert "AND grade = %(grade)s" in inner
        assert "AND coating_class = %(coating_class)s" in inner
        assert "FROM (" not in inner and "filtered" not in inner
    # Bound parameters are unchanged.
    assert set(params) == {"coil_id", "text", "k", "qvec", "grade", "coating_class"}
    assert params["qvec"].startswith("[")


def test_rrf_output_shape_is_unchanged():
    sql, _ = _run_similar()
    # One statement returns both ranked arms for Python-side RRF.
    assert sql.count("UNION ALL") == 1
    assert "'dense' AS arm" in sql and "'fts' AS arm" in sql
