"""Deterministic policy-binding oracle reusing the agent runtime."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

AGENT_SRC = Path(__file__).resolve().parents[2] / "agent" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))

from authorities_runtime import AuthorityRuntime  # noqa: E402
from db import connect  # noqa: E402


class ResolverOracle:
    def __init__(self, profile: str):
        self.profile = profile

    @staticmethod
    def source_sha() -> str:
        digest = hashlib.sha256()
        for name in ("resolution.py", "authorities_runtime.py"):
            digest.update((AGENT_SRC / name).read_bytes())
        return digest.hexdigest()

    def __call__(self, row: dict) -> dict:
        with connect(profile=self.profile, autocommit=True) as conn:
            return self._resolve(AuthorityRuntime(conn), row)

    def resolve_rows(self, rows: list[dict]) -> list[dict]:
        """Freeze each record independently while reusing one authenticated connection."""
        with connect(profile=self.profile, autocommit=True) as conn:
            runtime = AuthorityRuntime(conn)
            return [self._resolve(runtime, row) for row in rows]

    @staticmethod
    def _resolve(runtime: AuthorityRuntime, row: dict) -> dict:
        resolved = runtime.freeze(row["coil_id"]).resolved
        if row["claim_type"] == "coating_warranty":
            provenance = resolved["warranty_provenance"]
            prefix = "/".join(str(provenance[key]) for key in ("product_line", "region", "version"))
            sections = ("coverage", "exclusions", "proration")
        else:
            provenance = resolved["spec_provenance"]
            prefix = "/".join(str(provenance[key]) for key in ("grade", "region", "spec_edition"))
            sections = ("chemistry", "mechanical", "dimensions")
        return {
            "resolved_provenance": provenance,
            "oracle_clause_ids": [f"{prefix}/{section}" for section in sections],
        }
