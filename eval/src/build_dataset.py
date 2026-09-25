"""Build the leakage-free held-out MLflow evaluation dataset."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Iterable

import mlflow
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, StatementState

LEAN_CLAIM_COLUMNS = (
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
)
LABEL_KEYS = frozenset(
    {"verdict", "disposition", "approved_amount", "cited_clause_ids", "rationale"}
)
CRITICAL_STRATA = ("clean_approve", "in_spec_deny", "warranty_exclusion", "duplicate", "over_claim")
SPLIT_LOGIC_VERSION = "entity-time-v1"


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    return value.isoformat() if hasattr(value, "isoformat") else value


def source_sql(catalog: str = "fe-bar-ir") -> str:
    claim_cols = ", ".join(f"c.{name}" for name in LEAN_CLAIM_COLUMNS)
    return f"""
    SELECT {claim_cols}, a.verdict AS gold_verdict,
      a.disposition AS gold_disposition, CAST(a.approved_amount AS STRING) AS gold_approved_amount,
      a.cited_clause_ids AS gold_cited_clause_ids, a.duplicate_of_claim_id,
      a.rationale AS label_provenance, a.finalized_at, a.__START_AT AS adjudication_start_at
    FROM `{catalog}`.gold.adjudications_history a
    JOIN `{catalog}`.gold.claims_history c
      ON c.claim_id = a.claim_id AND c.__START_AT <= a.__START_AT
     AND (c.__END_AT IS NULL OR a.__START_AT < c.__END_AT)
    WHERE a.decision_status = 'FINAL' AND a.finalized_at IS NOT NULL
      AND (a.idempotency_key IS NULL OR a.idempotency_key = '')
    QUALIFY row_number() OVER (PARTITION BY a.adjudication_id ORDER BY c.__START_AT DESC) = 1
    """.strip()


def _execute_sql(profile: str, warehouse_id: str, sql: str) -> list[dict]:
    client = WorkspaceClient(profile=profile)
    response = client.statement_execution.execute_statement(
        warehouse_id=warehouse_id, statement=sql, disposition=Disposition.INLINE, wait_timeout="50s"
    )
    if response.status and response.status.state not in (
        StatementState.SUCCEEDED,
        StatementState.CLOSED,
    ):
        raise RuntimeError(f"statement failed: {response.status}")
    columns = [column.name for column in response.manifest.schema.columns]
    chunks = [response.result]
    while chunks[-1] and chunks[-1].next_chunk_index is not None:
        chunks.append(
            client.statement_execution.get_statement_result_chunk_n(
                response.statement_id, chunks[-1].next_chunk_index
            )
        )
    return [
        dict(zip(columns, row))
        for chunk in chunks
        for row in ((chunk.data_array if chunk else None) or [])
    ]


def stratum(row: dict) -> str:
    provenance = (row.get("label_provenance") or "").lower()
    if row.get("gold_disposition") == "DUPLICATE" or row.get("duplicate_of_claim_id"):
        return "duplicate"
    if "in_spec" in provenance:
        return "in_spec_deny"
    if "out_of_warranty" in provenance or "environment_excluded" in provenance:
        return "warranty_exclusion"
    if "over_claim" in provenance:
        return "over_claim"
    if row.get("gold_verdict") == "APPROVE" and "clean" in provenance:
        return "clean_approve"
    return f"claim_type:{row.get('claim_type', 'unknown')}"


def entity_key(row: dict) -> str:
    return str(row.get("duplicate_of_claim_id") or row["claim_id"])


def stable_holdout(rows: Iterable[dict], size: int = 75, minimum_rare: int = 10) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[entity_key(row)].append(row)
    ordered = sorted(
        groups.values(),
        key=lambda group: (
            min(str(r.get("finalized_at") or "") for r in group),
            hashlib.sha256(entity_key(group[0]).encode()).hexdigest(),
        ),
        reverse=True,
    )
    selected, used = [], set()
    counts: dict[str, int] = defaultdict(int)
    for required in CRITICAL_STRATA:
        for group in ordered:
            key = entity_key(group[0])
            matches = [row for row in group if stratum(row) == required]
            if key in used or not matches or len(selected) + len(group) > size:
                continue
            selected.extend(group)
            used.add(key)
            counts[required] += len(matches)
            if counts[required] >= minimum_rare:
                break
        if counts[required] < minimum_rare:
            raise ValueError(f"insufficient held-out records for {required}: {counts[required]}")
    for group in ordered:
        if len(selected) >= size:
            break
        key = entity_key(group[0])
        if key not in used and len(selected) + len(group) <= size:
            selected.extend(group)
            used.add(key)
    if len(selected) != size:
        raise ValueError(
            f"could not select exactly {size} entity-safe records; got {len(selected)}"
        )
    return selected


def make_record(row: dict, oracle: dict, judge: bool = False) -> dict:
    claim = {key: _json_value(row.get(key)) for key in LEAN_CLAIM_COLUMNS}
    leaked = LABEL_KEYS.intersection(claim)
    if leaked:
        raise ValueError(f"label leakage in inputs.claim: {sorted(leaked)}")
    return {
        "inputs": {"claim": claim},
        "expectations": {
            "verdict": row["gold_verdict"],
            "disposition": row["gold_disposition"],
            "approved_amount": str(row["gold_approved_amount"]),
            "gold_cited_clause_ids": _string_array(row.get("gold_cited_clause_ids")),
            "oracle_clause_ids": list(oracle["oracle_clause_ids"]),
            "expected_facts": [
                f"Final verdict is {row['gold_verdict']}",
                f"Final disposition is {row['gold_disposition']}",
                f"Approved amount is {row['gold_approved_amount']}",
            ],
            "judge_subset": judge,
        },
    }


def _string_array(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [str(item) for item in json.loads(value)]
    return [str(item) for item in value]


def fingerprint(rows: list[dict], resolver_sha: str) -> str:
    material = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(f"{material}|{SPLIT_LOGIC_VERSION}|{resolver_sha}".encode()).hexdigest()


def persist_managed_dataset(records: list[dict], experiment_id: str, metadata: dict):
    name = f"fe-bar-ir.default.claims_adjudication_eval_{metadata['source_fingerprint'][:12]}"
    try:
        dataset = mlflow.genai.datasets.get_dataset(name=name)
    except Exception as exc:
        message = str(exc).lower()
        if "does not exist" not in message and "not found" not in message:
            raise
        dataset = mlflow.genai.datasets.create_dataset(
            name=name,
            experiment_id=experiment_id,
        )
    dataset.merge_records(records)
    return dataset


def build(profile: str, warehouse_id: str, experiment_id: str, resolver) -> tuple[Any, dict, list]:
    selected = stable_holdout(_execute_sql(profile, warehouse_id, source_sql()))
    selected.sort(key=lambda row: hashlib.sha256(row["claim_id"].encode()).hexdigest())
    oracles = resolver.resolve_rows(selected)
    records = [
        make_record(row, oracle, judge=index < 30)
        for index, (row, oracle) in enumerate(zip(selected, oracles))
    ]
    resolver_sha = resolver.source_sha()
    metadata = {
        "source_fingerprint": fingerprint(selected, resolver_sha),
        "split_logic": SPLIT_LOGIC_VERSION,
        "resolver_sha": resolver_sha,
        "build_time_utc": datetime.now(UTC).isoformat(),
        "record_count": len(records),
        "judge_record_count": sum(r["expectations"]["judge_subset"] for r in records),
    }
    return persist_managed_dataset(records, experiment_id, metadata), metadata, records
