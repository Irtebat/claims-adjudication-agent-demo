"""Runtime adapter for the deterministic money authorities — in-process, no warehouse.

The authorities in ``authorities.py`` are pure Python and are the SINGLE source of
the conformance/coverage/settlement math. This adapter is the piece that, at
agent/app runtime: fetches the structured params for a grade/product and the coil
MTC over the SAME psycopg (Lakebase 5432) path that retrieval and duplicate
detection use (``db.py``), then calls ``compute_conformance`` / ``compute_coverage``
/ ``compute_settlement`` IN-PROCESS on the fetched inputs. Deterministic; no LLM and
no warehouse round-trip in the decision path.

All four reads use bound/parameterized psycopg queries:
  * ``public.spec_params`` / ``public.warranty_terms`` — native Lakebase policy
    data, authored by ``policy_intake.py``;
  * ``reference.heats_coils`` / ``reference.mill_test_certs`` — UC silver synced
    down to the Lakebase ``reference`` schema.
There is no Statement Execution API and no UC Python function call anywhere here.
"""

from __future__ import annotations

import json
from typing import Any

import authorities
from resolution import PolicyResolver

# Ordered (name, type) field lists: which fetched columns each authority reads, and
# the plain-Python type to normalize the psycopg value to (numerics arrive as Decimal,
# arrays as list, booleans as bool) before handing them to the pure authorities.
MEASURED_FIELDS = [
    ("carbon_pct", "DOUBLE"),
    ("manganese_pct", "DOUBLE"),
    ("yield_mpa", "DOUBLE"),
    ("tensile_mpa", "DOUBLE"),
    ("elongation_pct", "DOUBLE"),
    ("gauge_mm", "DOUBLE"),
    ("ordered_gauge_mm", "DOUBLE"),
    ("width_mm", "DOUBLE"),
    ("ordered_width_mm", "DOUBLE"),
    ("coating_weight_g_m2", "DOUBLE"),
    ("coating_adhesion_pass", "BOOLEAN"),
]
SPEC_PARAM_FIELDS = [
    ("carbon_pct_min", "DOUBLE"),
    ("carbon_pct_max", "DOUBLE"),
    ("manganese_pct_min", "DOUBLE"),
    ("manganese_pct_max", "DOUBLE"),
    ("yield_mpa_min", "DOUBLE"),
    ("yield_mpa_max", "DOUBLE"),
    ("tensile_mpa_min", "DOUBLE"),
    ("tensile_mpa_max", "DOUBLE"),
    ("elongation_pct_min", "DOUBLE"),
    ("elongation_pct_max", "DOUBLE"),
    ("gauge_tolerance_mm", "DOUBLE"),
    ("width_tolerance_mm", "DOUBLE"),
    ("min_coating_g_m2", "DOUBLE"),
    ("coating_adhesion_required", "BOOLEAN"),
]
WARRANTY_FIELDS = [
    ("duration_months", "INT"),
    ("full_coverage_months", "INT"),
    ("excluded_environments", "ARRAY<STRING>"),
    ("excluded_installations", "ARRAY<STRING>"),
    ("min_coast_distance_km", "DOUBLE"),
    ("freight_cap", "DOUBLE"),
]


def coerce(value, typ: str):
    """Normalize a fetched psycopg value to the plain-Python type the authorities expect."""
    if value is None:
        return None
    if typ == "BOOLEAN":
        return value if isinstance(value, bool) else str(value).lower() in ("true", "t", "1")
    if typ == "INT":
        return int(value)
    if typ.startswith("ARRAY"):
        return list(value) if isinstance(value, (list, tuple)) else json.loads(value)
    if typ == "DOUBLE":
        return float(value)
    return value


class FrozenAdjudicationContext:
    """A RESOLVE-ONCE frozen policy + MTC snapshot for a single adjudication.

    Resolution and the coil MTC read happen once at the start of an adjudication;
    every authority call then reuses this snapshot instead of re-resolving per
    tool call. The three ``compute_*`` methods delegate to the pure authorities in
    ``authorities.py`` — the LLM never participates in the money math.
    """

    def __init__(self, resolved: dict, measured: dict):
        self.resolved = resolved
        self.measured = measured

    def conformance(self) -> dict:
        return authorities.compute_conformance(self.resolved["spec_params"], self.measured)

    def coverage(self, claim: dict) -> dict:
        enriched = {**claim, "ship_date": self.resolved["coil"]["ship_date"]}
        return authorities.compute_coverage(self.resolved["warranty_terms"], enriched)

    def settlement(self, inputs: dict) -> dict:
        enriched = {
            **inputs,
            "shipped_tonnage": self.resolved["coil"]["shipped_tonnage"],
            "unit_price": self.resolved["coil"]["unit_price"],
            "freight_cap": self.resolved["freight_cap"],
        }
        return authorities.compute_settlement(enriched)


class AuthorityRuntime:
    """Fetches params + coil MTC over Lakebase psycopg and runs the in-process authorities.

    Takes an open psycopg connection (the same one retrieval/duplicate use, from
    ``db.connect``), so the whole operational adjudication path is a single 5432
    connection with no warehouse involvement.
    """

    def __init__(self, conn: Any):
        self._conn = conn
        self._resolver = PolicyResolver(conn)

    def freeze(self, coil_id: str) -> FrozenAdjudicationContext:
        """Resolve the coil's policy snapshot and MTC ONCE, for reuse across all tools."""
        measured = self.fetch_measured(coil_id)
        resolved = self._resolver.resolve(coil_id)
        return FrozenAdjudicationContext(resolved, measured)

    def _rows(self, statement: str, parameters: dict | None = None) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(statement, parameters or {})
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def fetch_measured(self, coil_id: str) -> dict:
        """Coil MTC (chemistry/mechanicals/adhesion) + dimensional record, from Lakebase reference."""
        rows = self._rows(
            "SELECT m.carbon_pct, m.manganese_pct, m.yield_mpa, m.tensile_mpa, "
            "m.elongation_pct, m.coating_adhesion_pass, h.gauge_mm, h.ordered_gauge_mm, "
            "h.width_mm, h.ordered_width_mm, h.coating_weight_g_m2 "
            "FROM reference.mill_test_certs m "
            "JOIN reference.heats_coils h USING (coil_id) "
            "WHERE m.coil_id = %(coil_id)s",
            {"coil_id": coil_id},
        )
        if not rows:
            raise LookupError(f"no MTC/coil record for {coil_id}")
        row = rows[0]
        measured = {name: coerce(row.get(name), typ) for name, typ in MEASURED_FIELDS}
        return measured

    def compute_conformance(self, coil_id: str) -> dict:
        measured = self.fetch_measured(coil_id)
        resolved = self._resolver.resolve(coil_id)
        params = resolved["spec_params"]
        return {
            "coil_id": coil_id,
            "spec_provenance": resolved["spec_provenance"],
            "measured": measured,
            "params": params,
            "verdict": authorities.compute_conformance(params, measured),
        }

    def compute_coverage(self, claim: dict) -> dict:
        resolved = self._resolver.resolve(claim["coil_id"])
        terms = resolved["warranty_terms"]
        enriched_claim = {**claim, "ship_date": resolved["coil"]["ship_date"]}
        return {
            "warranty_terms": terms,
            "warranty_provenance": resolved["warranty_provenance"],
            "verdict": authorities.compute_coverage(terms, enriched_claim),
        }

    def compute_settlement(self, inputs: dict) -> dict:
        resolved = self._resolver.resolve(inputs["coil_id"])
        enriched = {
            **inputs,
            "shipped_tonnage": resolved["coil"]["shipped_tonnage"],
            "unit_price": resolved["coil"]["unit_price"],
            "freight_cap": resolved["freight_cap"],
        }
        return {"resolved": resolved, "verdict": authorities.compute_settlement(enriched)}
