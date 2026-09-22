"""Parse a single authored policy source into typed parameters and citable clauses."""

import hashlib
import json
from pathlib import Path


def policy_rows(path=None):
    source_bytes = Path(path or Path(__file__).with_name("policy_source.json")).read_bytes()
    source = json.loads(source_bytes)
    digest = hashlib.sha256(source_bytes).hexdigest()
    standards, warranties = [], []
    for spec in source["standards"]:
        params = {
            k: spec[k]
            for k in (
                "chemistry",
                "mechanical",
                "gauge_tolerance_mm",
                "width_tolerance_mm",
                "min_coating_g_m2",
                "coating_adhesion_required",
            )
        }
        clauses = {
            "chemistry": "; ".join(
                f"{k}: {v['min']}–{v['max']} mass percent" for k, v in params["chemistry"].items()
            ),
            "mechanical": "; ".join(
                f"{k}: {v['min']}–{v['max']}" for k, v in params["mechanical"].items()
            ),
            "dimensions": f"Gauge tolerance ±{params['gauge_tolerance_mm']} mm; width tolerance ±{params['width_tolerance_mm']} mm; minimum coating {params['min_coating_g_m2']} g/m². Coating adhesion test must pass: {params['coating_adhesion_required']}.",
        }
        for region in source["regions"]:
            parent = f"{spec['spec_id']}-{region}-{spec['spec_edition']}"
            for section, text in clauses.items():
                standards.append(
                    dict(
                        spec_id=parent,
                        clause_id=f"{parent}:{section}",
                        parent_clause_id=parent,
                        section_ref=section,
                        grade=spec["grade"],
                        spec_edition=spec["spec_edition"],
                        region=region,
                        structured_params=params,
                        clause_text=f"{source['notice']} {spec['grade']} {spec['spec_edition']}: {text}",
                        source_sha256=digest,
                    )
                )
    for product in source["products"]:
        for version in source["warranty_versions"]:
            params = dict(
                source["coverage"],
                min_coating_g_m2=product["min_coating_g_m2"],
                duration_months=version["duration_months"],
                full_coverage_months=version["full_coverage_months"],
            )
            clauses = {
                "coverage": f"Coverage lasts {params['duration_months']} months from shipment. Minimum coating {params['min_coating_g_m2']} g/m².",
                "exclusions": f"Does not cover environments {', '.join(params['excluded_environments'])}, installations {', '.join(params['excluded_installations'])}, or sites less than {params['min_coast_distance_km']} km from coast.",
                "proration": f"Full coverage through {params['full_coverage_months']} months. Thereafter factor = max(0, (duration_months - completed_months) / (duration_months - full_coverage_months)). Freight is excluded.",
            }
            for region in source["regions"]:
                parent = f"W-{product['product_line']}-{region}-{version['version']}"
                for section, text in clauses.items():
                    warranties.append(
                        dict(
                            warranty_id=parent,
                            clause_id=f"{parent}:{section}",
                            parent_clause_id=parent,
                            section_ref=section,
                            product_line=product["product_line"],
                            coating_class=product["coating_class"],
                            region=region,
                            effective_from=version["effective_from"],
                            effective_to=version["effective_to"],
                            structured_params=params,
                            clause_text=f"{source['notice']} {parent}: {text}",
                            source_sha256=digest,
                        )
                    )
    return standards, warranties
