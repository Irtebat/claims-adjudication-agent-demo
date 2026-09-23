"""The runtime adapter fetches inputs over psycopg and runs the authorities IN-PROCESS.

No warehouse, no Statement Execution API, no UC function call. These offline tests
drive the adapter with a fake psycopg connection (numerics as Decimal, arrays as
lists, booleans as bool — exactly what psycopg returns) and prove the adapter:
  * reads spec_params/warranty_terms from Lakebase ``public`` and the coil MTC from
    the Lakebase ``reference`` schema, with bound/parameterized queries; and
  * returns the SAME verdict as calling the pure ``authorities`` functions directly
    on the fetched inputs.
A live smoke test (skipped without creds) exercises the real Lakebase 5432 path.
"""

import os
from decimal import Decimal

import pytest

import authorities
from authorities_runtime import AuthorityRuntime


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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, routes):
        self.cursor_obj = _FakeCursor(routes)

    def cursor(self):
        return self.cursor_obj


# Fetched inputs shaped like real psycopg rows: numerics come back as Decimal, the
# excluded_* text[] columns as Python lists, booleans as bool.
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
    "grade", "spec_edition", "region",
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
    "ASTM A653 CS Type B", "DEMO-1990", "NA",
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
    "product_line", "region", "version", "freight_cap",
    "duration_months",
    "full_coverage_months",
    "excluded_environments",
    "excluded_installations",
    "min_coast_distance_km",
]
WARRANTY_ROW = (
    "galvanized", "NA", "V2", Decimal("500.0"),
    240,
    60,
    ["marine"],
    ["standing_water"],
    Decimal("1.0"),
)
COIL_COLUMNS = ["coil_id", "grade", "spec_edition", "region", "product_line", "coating_class", "ship_date", "shipped_tonnage", "unit_price"]
COIL_ROW = ("COIL-0000005", "ASTM A653 CS Type B", "DEMO-1990", "NA", "galvanized", "G90", "2018-01-01", Decimal("10.0"), Decimal("1000.0"))


def _runtime():
    routes = {
        "reference.mill_test_certs": (MEASURED_COLUMNS, [MEASURED_ROW]),
        "FROM reference.heats_coils": (COIL_COLUMNS, [COIL_ROW]),
        "public.spec_params": (SPEC_COLUMNS, [SPEC_ROW]),
        "public.warranty_terms": (WARRANTY_COLUMNS, [WARRANTY_ROW]),
    }
    return AuthorityRuntime(_FakeConn(routes)), routes


def test_conformance_fetches_reference_and_matches_in_process_reference():
    rt, _ = _runtime()
    result = rt.compute_conformance("COIL-0000005")

    # The MTC/coil read came from the Lakebase reference schema, parameterized on coil_id.
    sql, params = rt._conn.cursor_obj.calls[0]
    assert "reference.mill_test_certs" in sql and "reference.heats_coils" in sql
    assert "%(coil_id)s" in sql and params == {"coil_id": "COIL-0000005"}
    # spec_params came from Lakebase public, parameterized on spec_id.
    spec_sql, spec_params = rt._conn.cursor_obj.calls[1]
    assert "reference.heats_coils" in spec_sql and spec_params == {"coil_id": "COIL-0000005"}

    # The verdict equals the pure authority called directly on the fetched inputs.
    local = authorities.compute_conformance(result["params"], result["measured"])
    assert result["verdict"] == local
    assert result["verdict"]["conforms"] is False  # tensile 620 is out of [340, 450]
    assert "tensile_mpa" in result["verdict"]["nonconforming_properties"]


def test_coverage_fetches_public_and_matches_in_process_reference():
    rt, _ = _runtime()
    claim = {
        "coil_id": "COIL-0000005",
        "claim_date": "2026-01-01",
        "environment": "marine",  # excluded
        "installation": "ventilated",
        "coast_distance_km": 25.0,
    }
    result = rt.compute_coverage(claim)
    sql, params = rt._conn.cursor_obj.calls[2]
    assert "public.warranty_terms" in sql and params["product_line"] == "galvanized"

    local = authorities.compute_coverage(result["warranty_terms"], {**claim, "ship_date": "2018-01-01"})
    assert result["verdict"] == local
    assert result["verdict"]["covered"] is False
    assert "environment:marine" in result["verdict"]["exclusions_hit"]


def test_settlement_is_pure_in_process_no_fetch():
    rt, _ = _runtime()
    inputs = {
        "coil_id": "COIL-0000005",
        "claim_type": "material_nonconformance",
        "shipped_tonnage": 10.0,
        "unit_price": 1000.0,
        "claimed_tonnage": 14.0,
        "claimed_freight": 1800.0,
        "freight_cap": 500.0,
        "proration_factor": 1.0,
    }
    result = rt.compute_settlement(inputs)
    assert rt._conn.cursor_obj.calls
    assert result["verdict"]["approved_amount"] == 10500.0
    assert result["verdict"]["over_claim_detected"] is True


def test_settlement_zero_proration_pays_zero_via_adapter():
    rt, _ = _runtime()
    inputs = {
        "coil_id": "COIL-0000005",
        "claim_type": "coating_warranty",
        "shipped_tonnage": 10.0,
        "unit_price": 1000.0,
        "claimed_tonnage": 10.0,
        "claimed_freight": 0.0,
        "freight_cap": 500.0,
        "proration_factor": 0.0,  # boundary: covered but zero proration -> pay 0
    }
    result = rt.compute_settlement(inputs)
    assert result["verdict"]["approved_amount"] == 0.0
    assert result["verdict"]["is_partial"] is True


def test_missing_coil_raises():
    routes = {"reference.mill_test_certs": (MEASURED_COLUMNS, [])}
    rt = AuthorityRuntime(_FakeConn(routes))
    with pytest.raises(LookupError):
        rt.compute_conformance("COIL-UNKNOWN")


@pytest.mark.parametrize("kind", ["conformance", "coverage"])
def test_live_lakebase_path_matches_reference(kind):
    """Live smoke over the real Lakebase 5432 psycopg path; skips without creds."""
    if os.getenv("RUN_LIVE_LAKEBASE_TESTS") != "1":
        pytest.skip("set RUN_LIVE_LAKEBASE_TESTS=1 for the live 5432 smoke test")
    pytest.importorskip("psycopg")
    try:
        from db import connect
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"db module unavailable: {exc}")
    try:
        cm = connect(profile="fe-bar")
        conn = cm.__enter__()
    except Exception as exc:  # no creds / Lakebase unreachable -> skip
        pytest.skip(f"fe-bar Lakebase not reachable: {exc}")
    try:
        rt = AuthorityRuntime(conn)
        if kind == "conformance":
            coil = rt._rows("SELECT coil_id FROM reference.heats_coils LIMIT 1")[0]
            result = rt.compute_conformance(coil["coil_id"])
            local = authorities.compute_conformance(result["params"], result["measured"])
            assert result["verdict"] == local
        else:
            row = rt._rows("SELECT coil_id FROM reference.heats_coils LIMIT 1")[0]
            claim = {
                "coil_id": row["coil_id"],
                "claim_date": "2026-01-01",
                "environment": "inland",
                "installation": "ventilated",
                "coast_distance_km": 25.0,
            }
            result = rt.compute_coverage(claim)
            local = authorities.compute_coverage(result["warranty_terms"], claim)
            assert result["verdict"] == local
    finally:
        cm.__exit__(None, None, None)
