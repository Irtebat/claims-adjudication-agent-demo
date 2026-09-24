"""Tool wrappers + the resolve-once deterministic core (authorities decide money)."""

from decimal import Decimal

import agent_tools


class _Col:
    def __init__(self, name):
        self.name = name


class _FakeCursor:
    def __init__(self, routes):
        self._routes = routes
        self._rows = []
        self.description = []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        for needle, (columns, rows) in self._routes.items():
            if needle in sql:
                self.description = [_Col(c) for c in columns]
                self._rows = rows
                return
        raise AssertionError(f"unrouted query: {sql}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, routes):
        self.cur = _FakeCursor(routes)

    def cursor(self):
        return self.cur


MEASURED_COLUMNS = [
    "carbon_pct",
    "manganese_pct",
    "yield_mpa",
    "tensile_mpa",
    "elongation_pct",
    "coating_adhesion_pass",
    "gauge_mm",
    "ordered_gauge_mm",
    "width_mm",
    "ordered_width_mm",
    "coating_weight_g_m2",
]
MEASURED_ROW = (
    Decimal("0.08"),
    Decimal("0.40"),
    Decimal("250.0"),
    Decimal("620.0"),
    Decimal("30.0"),
    True,
    Decimal("1.01"),
    Decimal("1.00"),
    Decimal("1201.0"),
    Decimal("1200.0"),
    Decimal("280.0"),
)
SPEC_COLUMNS = [
    "grade",
    "spec_edition",
    "region",
    "carbon_pct_min",
    "carbon_pct_max",
    "manganese_pct_min",
    "manganese_pct_max",
    "yield_mpa_min",
    "yield_mpa_max",
    "tensile_mpa_min",
    "tensile_mpa_max",
    "elongation_pct_min",
    "elongation_pct_max",
    "gauge_tolerance_mm",
    "width_tolerance_mm",
    "min_coating_g_m2",
    "coating_adhesion_required",
]
SPEC_ROW = (
    "ASTM A653 CS Type B",
    "DEMO-1990",
    "NA",
    Decimal("0.02"),
    Decimal("0.15"),
    Decimal("0.20"),
    Decimal("0.60"),
    Decimal("200.0"),
    Decimal("300.0"),
    Decimal("340.0"),
    Decimal("450.0"),
    Decimal("20.0"),
    Decimal("40.0"),
    Decimal("0.05"),
    Decimal("2.0"),
    Decimal("275.0"),
    True,
)
WARRANTY_COLUMNS = [
    "product_line",
    "region",
    "version",
    "freight_cap",
    "duration_months",
    "full_coverage_months",
    "excluded_environments",
    "excluded_installations",
    "min_coast_distance_km",
]
WARRANTY_ROW = (
    "galvanized",
    "NA",
    "V2",
    Decimal("500.0"),
    240,
    60,
    ["marine"],
    ["standing_water"],
    Decimal("1.0"),
)
COIL_COLUMNS = [
    "coil_id",
    "grade",
    "spec_edition",
    "region",
    "product_line",
    "coating_class",
    "ship_date",
    "shipped_tonnage",
    "unit_price",
]
COIL_ROW = (
    "COIL-5",
    "ASTM A653 CS Type B",
    "DEMO-1990",
    "NA",
    "galvanized",
    "G90",
    "2018-01-01",
    Decimal("10.0"),
    Decimal("1000.0"),
)
DUP_COLUMNS = [
    "claim_id",
    "coil_id",
    "defect_code",
    "claimed_freight",
    "claimed_tonnage",
    "claim_date",
    "narrative_similarity",
]
CLAUSE_COLUMNS = ["citation_key", "section_ref", "clause_text"]
CLAUSE_ROW = (
    "ASTM A653 CS Type B/NA/DEMO-1990/mechanical",
    "mechanical",
    "yield 200-300; tensile 340-450",
)
RISK_COLUMNS = [
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


def _conn():
    # Ordered so the most specific needle matches first.
    routes = {
        "reference.mill_test_certs": (MEASURED_COLUMNS, [MEASURED_ROW]),
        "SELECT heat_no": (["heat_no"], [("HEAT-1",)]),
        "public.spec_params": (SPEC_COLUMNS, [SPEC_ROW]),
        "public.warranty_terms": (WARRANTY_COLUMNS, [WARRANTY_ROW]),
        "FROM reference.heats_coils": (COIL_COLUMNS, [COIL_ROW]),
        "FROM claims": (DUP_COLUMNS, []),
        "spec_clauses": (CLAUSE_COLUMNS, [CLAUSE_ROW]),
        "reference.customer_heat_risk": (RISK_COLUMNS, []),
    }
    return _FakeConn(routes)


CLAIM = {
    "claim_id": "CLM-5",
    "coil_id": "COIL-5",
    "customer_id": "CUST-5",
    "claim_type": "material_nonconformance",
    "claim_date": "2026-01-01",
    "defect_code": "MECH_YIELD",
    "defect_narrative": "yield strength below spec",
    "claimed_tonnage": 14.0,
    "claimed_freight": 1800.0,
}


def test_run_deterministic_core_lets_authorities_decide():
    core = agent_tools.run_deterministic_core(_conn(), CLAIM, embed_fn=None)
    # tensile 620 is out of [340, 450] -> nonconforming -> eligible -> APPROVE.
    assert core["conformance"]["conforms"] is False
    assert "tensile_mpa" in core["conformance"]["nonconforming_properties"]
    assert core["deterministic"]["verdict"] == "APPROVE"
    # over-claim: 14t claimed vs 10t shipped -> capped; freight capped at 500.
    assert core["settlement"]["over_claim_detected"] is True
    assert core["deterministic"]["approved_amount"] == core["settlement"]["approved_amount"]
    assert core["citations"][0]["citation_key"] == CLAUSE_ROW[0]
    assert core["risk"]["found"] is False  # no risk row -> neutral advisory


def test_settlement_inputs_proration_only_for_warranty():
    material = agent_tools.settlement_inputs(
        {"coil_id": "C", "claim_type": "material_nonconformance"}, {"proration_factor": 0.5}
    )
    warranty = agent_tools.settlement_inputs(
        {"coil_id": "C", "claim_type": "coating_warranty"}, {"proration_factor": 0.5}
    )
    assert material["proration_factor"] == 1.0
    assert warranty["proration_factor"] == 0.5


def test_build_citations_hashes_clause_text():
    citations = agent_tools.build_citations(
        [{"citation_key": "k", "section_ref": "s", "clause_text": "text"}]
    )
    assert citations[0]["citation_key"] == "k"
    assert len(citations[0]["clause_text_sha256"]) == 64


def test_default_recommendation_aligns_to_deterministic():
    core = agent_tools.run_deterministic_core(_conn(), CLAIM, embed_fn=None)
    rec = agent_tools.default_recommendation(core)
    assert rec["recommended_verdict"] == core["deterministic"]["verdict"]
    assert rec["settlement_estimate"] == core["deterministic"]["settlement_authority_amount"]
    assert rec["flags"]["over_claim"] is True
