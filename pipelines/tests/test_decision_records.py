"""Decision-record DQ checks and the append-only transformation's shape."""

import py_compile
from pathlib import Path

from src.checks import decision_record_queries

ROOT = Path(__file__).resolve().parents[1]


def test_dq_includes_uniqueness_and_money_invariants():
    queries = decision_record_queries("`fe-bar-ir`")
    # The required assertion: one immutable record per (adjudication_id, record_version).
    assert "duplicate_decision_record" in queries
    assert (
        "GROUP BY adjudication_id, record_version HAVING count(*) > 1"
        in queries["duplicate_decision_record"]
    )
    # Money invariants proving the LLM never overrode an authority.
    assert "duplicate_recommended_for_payment" in queries
    assert "approved_amount_not_deterministic" in queries
    assert "inspec_material_approved" in queries
    assert "uncovered_warranty_approved" in queries
    for query in queries.values():
        assert "gold.adjudication_decision_records" in query


def test_transformation_compiles():
    # py_compile checks syntax without importing pyspark.pipelines.
    py_compile.compile(
        str(ROOT / "src/transformations/gold_adjudication_decision_records.py"), doraise=True
    )
