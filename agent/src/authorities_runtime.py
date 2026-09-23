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
    ("spec_id", "STRING"),
]
WARRANTY_FIELDS = [
    ("duration_months", "INT"),
    ("full_coverage_months", "INT"),
    ("excluded_environments", "ARRAY<STRING>"),
    ("excluded_installations", "ARRAY<STRING>"),
    ("min_coast_distance_km", "DOUBLE"),
    ("warranty_id", "STRING"),
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


class AuthorityRuntime:
    """Fetches params + coil MTC over Lakebase psycopg and runs the in-process authorities.

    Takes an open psycopg connection (the same one retrieval/duplicate use, from
    ``db.connect``), so the whole operational adjudication path is a single 5432
    connection with no warehouse involvement.
    """

    def __init__(self, conn: Any):
        self._conn = conn

    def _rows(self, statement: str, parameters: dict | None = None) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(statement, parameters or {})
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def fetch_measured(self, coil_id: str) -> dict:
        """Coil MTC (chemistry/mechanicals/adhesion) + dimensional record, from Lakebase reference."""
        rows = self._rows(
            "SELECT h.spec_id, m.carbon_pct, m.manganese_pct, m.yield_mpa, m.tensile_mpa, "
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
        measured["spec_id"] = row["spec_id"]
        return measured

    def fetch_spec_params(self, spec_id: str) -> dict:
        rows = self._rows(
            "SELECT * FROM public.spec_params WHERE spec_id = %(spec_id)s",
            {"spec_id": spec_id},
        )
        if not rows:
            raise LookupError(f"no spec_params for {spec_id}")
        row = rows[0]
        params = {name: coerce(row.get(name), typ) for name, typ in SPEC_PARAM_FIELDS}
        params["spec_id"] = spec_id
        return params

    def fetch_warranty_terms(self, warranty_id: str) -> dict:
        rows = self._rows(
            "SELECT * FROM public.warranty_terms WHERE warranty_id = %(warranty_id)s",
            {"warranty_id": warranty_id},
        )
        if not rows:
            raise LookupError(f"no warranty_terms for {warranty_id}")
        row = rows[0]
        terms = {name: coerce(row.get(name), typ) for name, typ in WARRANTY_FIELDS}
        terms["warranty_id"] = warranty_id
        return terms

    def compute_conformance(self, coil_id: str, spec_id: str | None = None) -> dict:
        measured = self.fetch_measured(coil_id)
        spec_id = spec_id or measured["spec_id"]
        params = self.fetch_spec_params(spec_id)
        return {
            "coil_id": coil_id,
            "spec_id": spec_id,
            "measured": measured,
            "params": params,
            "verdict": authorities.compute_conformance(params, measured),
        }

    def compute_coverage(self, claim: dict) -> dict:
        terms = self.fetch_warranty_terms(claim["warranty_id"])
        return {"warranty_terms": terms, "verdict": authorities.compute_coverage(terms, claim)}

    def compute_settlement(self, inputs: dict) -> dict:
        # Settlement inputs are caller-provided (from the claim), not fetched, so this
        # is a pure in-process call with no DB read.
        return {"verdict": authorities.compute_settlement(inputs)}
