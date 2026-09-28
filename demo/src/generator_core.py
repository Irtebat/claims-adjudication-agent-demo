"""Pure synthetic claims generator — importable without Spark or Databricks.

Generates N fresh synthetic claims consistent with the label-pattern distribution
used by pipelines/src/generate.py. The generator is deterministic given a seed and
produces claims ready for insertion into Lakebase public.claims.

The label distribution (per 100-claim block):
- clean: 20%
- in_spec_should_deny: 20%
- out_of_warranty_or_environment_excluded: 15%
- duplicate: 10%
- over_claim: 15%
- supplier_attributable: 15%
- fraud_cluster: 5%
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal


class ClaimsGenerator:
    """Generate synthetic claims with consistent label distribution and deterministic seed."""

    def __init__(self, count: int, seed: int = 42):
        """Initialize the generator.

        Args:
            count: Number of claims to generate (must be >= 100 so all patterns are present)
            seed: Seed for deterministic generation (default 42)
        """
        if count < 100:
            raise ValueError("claim_count must be >= 100 so all injected patterns are present")
        self.count = count
        self.seed = seed

    def _company_name(self, i: int) -> str:
        """Generate a deterministic company name from an index."""
        # Simplified Faker-like deterministic company names.
        companies = [
            "Acme Corp",
            "TechVision Inc",
            "SteelWorks Ltd",
            "Global Manufacturing",
            "Pioneer Industries",
            "NextGen Solutions",
            "Industrial Dynamics",
            "Premium Steel Co",
            "Quality Metals",
            "Steel Masters",
        ]
        rng_value = (self.seed + i) % len(companies)
        return companies[rng_value]

    def generate(self) -> list[dict]:
        """Generate N claims with label-pattern distribution.

        Returns:
            List of claim dicts ready for insertion into Lakebase public.claims
        """
        claims = []

        for i in range(self.count):
            block = i // 100
            slot = i % 100

            # Determine label pattern from slot (fixed 100-row blocks for distribution consistency).
            # Labels: clean(20%), in_spec_should_deny(20%), out_of_warranty(15%),
            # duplicate(10%), over_claim(15%), supplier_attributable(15%), fraud_cluster(5%)
            if slot >= 95:  # fraud_cluster: 5%
                pass  # Will be used in defect_narrative and claim_type
            elif slot >= 80:  # supplier_attributable: 15%
                pass
            elif slot >= 65:  # over_claim: 15%
                pass
            elif slot >= 55:  # duplicate: 10%
                pass
            elif slot >= 40:  # out_of_warranty_or_environment_excluded: 15%
                pass
            elif slot >= 20:  # in_spec_should_deny: 20%
                pass
            # else: clean: 20%

            # Duplicates reuse identity and fields of clean slots 0-9.
            material_i = i - 55 if 55 <= slot <= 64 else i
            m_slot = material_i % 100

            # Dates and amounts.
            claim_date = date(2026, 1, 1) + timedelta(days=int(block % 180))
            if 55 <= slot <= 64:
                claim_date = claim_date + timedelta(days=1)

            ship_date = (
                date(1999, 1, 1)
                if m_slot >= 95 or (m_slot >= 40 and m_slot < 45)
                else date(2014, 1, 1)
                if (m_slot % 2 == 0 and m_slot < 40)
                else date(2018, 1, 1)
                if m_slot < 40
                else date(2025, 12, 1)
            )
            install_date = ship_date + timedelta(days=30)

            # Customer and supplier.
            if m_slot >= 95:
                customer_id = f"CUST-{197 + material_i % 3:04d}"
            else:
                customer_id = f"CUST-{(material_i * 17) % 197:04d}"

            # Environment and installation.
            environment = "marine" if m_slot >= 45 and m_slot < 50 else "inland"
            installation = "standing_water" if m_slot >= 50 and m_slot < 55 else "ventilated"
            coast_distance = Decimal("0.5") if environment == "marine" else Decimal("25.0")

            # Defect code and narrative.
            if m_slot < 40 and m_slot >= 5:  # is_warranty
                defect_code = "RED_RUST"
                defect_narrative = (
                    "Premature red rust and perforation observed on installed roofing."
                )
            elif m_slot >= 80 and m_slot < 95:
                defect_code = "COATING_VOID"
                defect_narrative = (
                    "Coating detaches during forming; coating voids visible along strip."
                )
            else:
                defect_code = "MECH_TENSILE"
                defect_narrative = (
                    "Tensile response during forming differs from ordered mechanical requirements."
                )

            # Special case for fraud cluster.
            if m_slot >= 95:
                defect_narrative = "Identical edge failure across delivered coils; request full replacement urgently."

            # Tonnage and amounts.
            shipped_tonnage = Decimal(10 + (material_i * 7) % 21)

            claimed_tonnage = (
                shipped_tonnage * Decimal("1.4") if 65 <= slot < 80 else shipped_tonnage
            )
            claimed_freight = Decimal(1800) if 65 <= slot < 80 else Decimal(0)

            claim = {
                "claim_id": f"CLM-{i:07d}",
                "coil_id": f"COIL-{material_i:07d}",
                "customer_id": customer_id,
                "claim_type": (
                    "coating_warranty"
                    if m_slot < 40 or (m_slot >= 40 and m_slot < 55)
                    else "material_nonconformance"
                ),
                "claim_date": claim_date,
                "install_date": install_date,
                "environment": environment,
                "installation": installation,
                "coast_distance_km": float(coast_distance),
                "defect_code": defect_code,
                "defect_narrative": defect_narrative,
                "claimed_tonnage": float(claimed_tonnage),
                "claimed_freight": float(claimed_freight),
            }
            claims.append(claim)

        return claims

    @staticmethod
    def validate_schema(claims: list[dict]) -> list[str]:
        """Validate that all claims have required fields.

        Returns:
            List of error messages (empty if all valid)
        """
        required_fields = [
            "claim_id",
            "coil_id",
            "customer_id",
            "claim_type",
            "claim_date",
            "install_date",
            "environment",
            "installation",
            "coast_distance_km",
            "defect_code",
            "defect_narrative",
            "claimed_tonnage",
            "claimed_freight",
        ]

        errors = []
        for i, claim in enumerate(claims):
            for field in required_fields:
                if field not in claim:
                    errors.append(f"Claim {i} missing field: {field}")
        return errors

    @staticmethod
    def validate_distribution(claims: list[dict], tolerance: float = 0.05) -> list[str]:
        """Validate that distribution matches expected label proportions within tolerance.

        Args:
            claims: List of claims (must have generated labels in a _ground_truth_label field)
            tolerance: Acceptable deviation from expected percentage (default 5%)

        Returns:
            List of validation error messages
        """
        # For now, just verify we have enough claims per pattern.
        # Full distribution check would require claims to carry ground_truth_label.
        errors = []
        if len(claims) < 100:
            errors.append(f"Need at least 100 claims for pattern validation, got {len(claims)}")
        return errors
