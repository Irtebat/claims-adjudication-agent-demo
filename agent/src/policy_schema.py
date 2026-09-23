"""Parse the single authored policy source into the two representations.

`policy_source.json` is the sole source of policy truth. It is parsed once, here,
into:

* **structured params** — flat, typed rows the deterministic authorities read
  (`spec_params` at grade level, `warranty_terms` at product/coating level); and
* **citable text clauses** — one row per parent section (`spec_clauses`,
  `warranty_clauses`), carrying the numbers, identifiers and negation that hybrid
  retrieval needs, later embedded and indexed in Lakebase.

The numbers the authorities decide on always come from the structured params,
never from the clause text (PLAN §6, "two representations, one source"). This
module is pure Python so the parse is unit-testable without Lakebase.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SOURCE_PATH = Path(__file__).with_name("policy_source.json")

SPEC_SECTIONS = ("chemistry", "mechanical", "dimensions")
WARRANTY_SECTIONS = ("coverage", "exclusions", "proration")


def _load(path: str | Path | None) -> tuple[dict, str]:
    raw = Path(path or SOURCE_PATH).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _range(pair: dict) -> tuple[float, float]:
    return float(pair["min"]), float(pair["max"])


def parse_policies(path: str | Path | None = None) -> dict[str, list[dict]]:
    """Return {spec_params, warranty_terms, spec_clauses, warranty_clauses}.

    Every clause row carries a stable ``clause_id`` and ``parent_clause_id`` and
    is joinable to its param row by ``spec_id`` / ``warranty_id``.
    """
    source, digest = _load(path)
    notice = source["notice"]
    regions = source["regions"]
    coverage = source["coverage"]

    spec_params: list[dict] = []
    spec_clauses: list[dict] = []
    for spec in source["standards"]:
        chem = spec["chemistry"]
        mech = spec["mechanical"]
        carbon = _range(chem["carbon_pct"])
        manganese = _range(chem["manganese_pct"])
        yield_r = _range(mech["yield_mpa"])
        tensile = _range(mech["tensile_mpa"])
        elong = _range(mech["elongation_pct"])
        for region in regions:
            spec_id = f"{spec['spec_id']}-{region}-{spec['spec_edition']}"
            spec_params.append(
                {
                    "spec_id": spec_id,
                    "base_spec_id": spec["spec_id"],
                    "grade": spec["grade"],
                    "spec_edition": spec["spec_edition"],
                    "region": region,
                    "carbon_pct_min": carbon[0],
                    "carbon_pct_max": carbon[1],
                    "manganese_pct_min": manganese[0],
                    "manganese_pct_max": manganese[1],
                    "yield_mpa_min": yield_r[0],
                    "yield_mpa_max": yield_r[1],
                    "tensile_mpa_min": tensile[0],
                    "tensile_mpa_max": tensile[1],
                    "elongation_pct_min": elong[0],
                    "elongation_pct_max": elong[1],
                    "gauge_tolerance_mm": float(spec["gauge_tolerance_mm"]),
                    "width_tolerance_mm": float(spec["width_tolerance_mm"]),
                    "min_coating_g_m2": float(spec["min_coating_g_m2"]),
                    "coating_adhesion_required": bool(spec["coating_adhesion_required"]),
                    "source_sha256": digest,
                }
            )
            texts = {
                "chemistry": "; ".join(
                    f"{k}: {v['min']}–{v['max']} mass percent" for k, v in chem.items()
                ),
                "mechanical": "; ".join(f"{k}: {v['min']}–{v['max']}" for k, v in mech.items()),
                "dimensions": (
                    f"Gauge tolerance ±{spec['gauge_tolerance_mm']} mm; width tolerance "
                    f"±{spec['width_tolerance_mm']} mm; minimum coating "
                    f"{spec['min_coating_g_m2']} g/m². Coating adhesion test must pass: "
                    f"{spec['coating_adhesion_required']}."
                ),
            }
            for section in SPEC_SECTIONS:
                spec_clauses.append(
                    {
                        "clause_id": f"{spec_id}:{section}",
                        "parent_clause_id": spec_id,
                        "spec_id": spec_id,
                        "section_ref": section,
                        "grade": spec["grade"],
                        "spec_edition": spec["spec_edition"],
                        "region": region,
                        "clause_text": f"{notice} {spec['grade']} {spec['spec_edition']}: "
                        f"{texts[section]}",
                        "source_sha256": digest,
                    }
                )

    warranty_terms: list[dict] = []
    warranty_clauses: list[dict] = []
    for product in source["products"]:
        for version in source["warranty_versions"]:
            duration = int(version["duration_months"])
            full = int(version["full_coverage_months"])
            min_coating = float(product["min_coating_g_m2"])
            for region in regions:
                warranty_id = f"W-{product['product_line']}-{region}-{version['version']}"
                warranty_terms.append(
                    {
                        "warranty_id": warranty_id,
                        "product_line": product["product_line"],
                        "coating_class": product["coating_class"],
                        "region": region,
                        "version": version["version"],
                        "effective_from": version["effective_from"],
                        "effective_to": version["effective_to"],
                        "duration_months": duration,
                        "full_coverage_months": full,
                        "min_coating_g_m2": min_coating,
                        "min_coast_distance_km": float(coverage["min_coast_distance_km"]),
                        "excluded_environments": list(coverage["excluded_environments"]),
                        "excluded_installations": list(coverage["excluded_installations"]),
                        "proration_method": coverage["proration_method"],
                        "freight_covered": bool(coverage["freight_covered"]),
                        "source_sha256": digest,
                    }
                )
                texts = {
                    "coverage": f"Coverage lasts {duration} months from shipment. Minimum coating "
                    f"{min_coating} g/m².",
                    "exclusions": "Does not cover environments "
                    f"{', '.join(coverage['excluded_environments'])}, installations "
                    f"{', '.join(coverage['excluded_installations'])}, or sites less than "
                    f"{coverage['min_coast_distance_km']} km from coast.",
                    "proration": f"Full coverage through {full} months. Thereafter factor = "
                    "max(0, (duration_months - completed_months) / (duration_months - "
                    "full_coverage_months)). Freight is excluded.",
                }
                for section in WARRANTY_SECTIONS:
                    warranty_clauses.append(
                        {
                            "clause_id": f"{warranty_id}:{section}",
                            "parent_clause_id": warranty_id,
                            "warranty_id": warranty_id,
                            "section_ref": section,
                            "product_line": product["product_line"],
                            "coating_class": product["coating_class"],
                            "region": region,
                            "effective_from": version["effective_from"],
                            "effective_to": version["effective_to"],
                            "clause_text": f"{notice} {warranty_id}: {texts[section]}",
                            "source_sha256": digest,
                        }
                    )

    return {
        "spec_params": spec_params,
        "warranty_terms": warranty_terms,
        "spec_clauses": spec_clauses,
        "warranty_clauses": warranty_clauses,
    }
