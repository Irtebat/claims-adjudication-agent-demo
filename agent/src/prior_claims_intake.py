"""Backfill governed GTE embeddings for the prior-claims retrieval corpus."""

from __future__ import annotations

import argparse
import json

from db import DEFAULT_DATABASE, DEFAULT_ENDPOINT, connect
from gateway_embed import PROVENANCE, embed_texts


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def run(profile: str, endpoint: str, database: str) -> dict:
    with connect(profile, endpoint, database, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT claim_id, defect_narrative FROM prior_claims "
                "WHERE embedding IS NULL ORDER BY claim_id"
            )
            rows = cur.fetchall()
    vectors = embed_texts([row[1] for row in rows], profile=profile) if rows else []
    with connect(profile, endpoint, database, autocommit=True) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(
                    "UPDATE prior_claims SET embedding = %(embedding)s::vector "
                    "WHERE claim_id = %(claim_id)s",
                    [
                        {"claim_id": row[0], "embedding": _literal(vector)}
                        for row, vector in zip(rows, vectors)
                    ],
                )
        return {"embedded": len(rows), "provenance": PROVENANCE}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="fe-bar")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    args = parser.parse_args()
    print(json.dumps(run(args.profile, args.endpoint, args.database), sort_keys=True))


if __name__ == "__main__":
    main()
