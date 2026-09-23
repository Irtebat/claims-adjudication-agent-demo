"""Deterministically bind a claim's coil to policy natural keys."""

from __future__ import annotations

from typing import Any


class PolicyResolver:
    def __init__(self, conn: Any):
        self._conn = conn

    def _one(self, sql: str, params: dict) -> dict:
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [column.name for column in cur.description]
            rows = cur.fetchall()
            row = rows[0] if rows else None
        if row is None:
            raise LookupError("policy resolution found no matching row")
        return dict(zip(columns, row))

    def resolve(self, coil_id: str) -> dict:
        coil = self._one(
            "SELECT coil_id, grade, spec_edition, region, product_line, coating_class, "
            "ship_date, shipped_tonnage, unit_price FROM reference.heats_coils "
            "WHERE coil_id = %(coil_id)s",
            {"coil_id": coil_id},
        )
        spec = self._one(
            "SELECT * FROM public.spec_params WHERE grade = %(grade)s "
            "AND spec_edition = %(spec_edition)s AND region = %(region)s",
            {key: coil[key] for key in ("grade", "spec_edition", "region")},
        )
        warranty = self._one(
            "SELECT * FROM public.warranty_terms WHERE product_line = %(product_line)s "
            "AND region = %(region)s AND %(ship_date)s::date >= effective_from "
            "AND %(ship_date)s::date < effective_to ORDER BY effective_from DESC LIMIT 1",
            {key: coil[key] for key in ("product_line", "region", "ship_date")},
        )
        return {
            "coil": coil,
            "spec_params": spec,
            "warranty_terms": warranty,
            "spec_provenance": {key: spec[key] for key in ("grade", "spec_edition", "region")},
            "warranty_provenance": {
                key: warranty[key] for key in ("product_line", "region", "version")
            },
            "freight_cap": warranty["freight_cap"],
        }
