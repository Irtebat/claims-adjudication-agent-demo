"""Pure logic for the lakehouse-built prior-claims precedent corpus.

The corpus is built in Unity Catalog (``prior_claims_corpus_job.py``) from gold
``claims_current`` + FINAL ``adjudications_current`` + the coil master, embedded with
the governed gateway helper, and served down to Lakebase as a Triggered synced table
(``reference.prior_claims_corpus``). Everything here is Spark-free so the planning
(which rows need a new embedding) and the vector encoding are unit-testable.

Synced tables map ``ARRAY`` to ``JSONB``, and ``vector``/``tsvector`` are not Delta
types, so the embedding is stored as its pgvector text literal (``[f1,f2,...]``). In
Postgres that column is ``text``; the ``lakebase_ann`` index is built over the
immutable ``embedding::vector(1024)`` cast and the ``lakebase_bm25`` index over
``to_tsvector('english', defect_narrative)``, and retrieval queries those same
expressions.
"""

from __future__ import annotations

import hashlib

from gateway_embed import EMBEDDING_DIM, PROVENANCE

CORPUS_TABLE = "prior_claims_corpus"


def narrative_sha256(narrative: str) -> str:
    return hashlib.sha256(narrative.encode("utf-8")).hexdigest()


def vector_literal(vector: list[float]) -> str:
    """Encode a vector as the pgvector text literal the synced ``text`` column holds.

    Eight significant digits exceed pgvector's float4 storage precision, so nothing is
    lost while the literal stays about half the size of ``repr``.
    """
    if len(vector) != EMBEDDING_DIM:
        raise ValueError(f"Expected a {EMBEDDING_DIM}-dim embedding, got {len(vector)}")
    return "[" + ",".join(format(float(x), ".8g") for x in vector) + "]"


def reuse_embedding(prior: dict | None, narrative: str) -> bool:
    """True when a stored embedding can be reused for ``narrative``.

    Mirrors the Spark ``_reuse`` predicate in ``prior_claims_corpus_job.py``: reuse only
    when the narrative hash AND the embedding provenance (model, normalization, dim)
    both still match, so a model change re-embeds everything and an unchanged narrative
    is never re-sent to the gateway.
    """
    return bool(
        prior
        and prior.get("embedding")
        and prior.get("narrative_sha256") == narrative_sha256(narrative)
        and prior.get("embedding_provenance") == PROVENANCE
    )
