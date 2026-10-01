"""Hybrid clause retrieval and similar-prior-claims — advisory context, never the authority.

Runs at agent/app runtime (psycopg to Lakebase + gateway embedding for the
query), NOT as a UC Python UDF (those cannot open Postgres 5432). Retrieval only
*finds and cites* candidate clauses; the deterministic ``compute_*`` authorities
decide (PLAN §6.4).

Clause citation uses the real Lakebase Search **BM25** index: it scores rows with
``clause_tsv <@> to_bm25query(to_tsvector('english', query), '<index>'::regclass)``
(a lower/more-negative score is a better match, so ORDER BY ASC), driven by the
``lakebase_bm25`` index built at intake. Metadata filters bind citations to resolved
policy. Dense+BM25 RRF is reserved for the separate prior-claims corpus.
"""

from __future__ import annotations

from typing import Any, Callable

from prior_claims_indexes import (
    PRIOR_CLAIMS_BM25_INDEX,
    PRIOR_CLAIMS_EMBEDDING_EXPR,
    PRIOR_CLAIMS_TABLE,
    PRIOR_CLAIMS_TEXT_SEARCH_CONFIG,
    PRIOR_CLAIMS_TSVECTOR_EXPR,
)

RRF_K = 60

_SPEC_FILTERS = {"grade": "grade", "spec_edition": "spec_edition", "region": "region"}
_WARRANTY_FILTERS = {
    "product_line": "product_line",
    "coating_class": "coating_class",
    "region": "region",
}
# The lakebase_bm25 index name to_bm25query references, per corpus (fixed, not input).
_BM25_INDEX = {"spec": "spec_clauses_lb_bm25", "warranty": "warranty_clauses_lb_bm25"}


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion of several ranked id lists → ids sorted by fused score."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _build_filters(corpus: str, filters: dict) -> tuple[str, dict]:
    mapping = _SPEC_FILTERS if corpus == "spec" else _WARRANTY_FILTERS
    clauses, params = [], {}
    for key, column in mapping.items():
        if filters.get(key) is not None:
            clauses.append(f"{column} = %({key})s")
            params[key] = filters[key]
    if corpus == "warranty" and filters.get("ship_date") is not None:
        # the warranty version in force at sale/ship, not today's
        clauses.append("%(ship_date)s::date >= effective_from")
        clauses.append("%(ship_date)s::date < effective_to")
        params["ship_date"] = str(filters["ship_date"])
    where = (" AND " + " AND ".join(clauses)) if clauses else ""
    return where, params


def retrieve_policy_clauses(
    conn: Any,
    embed_fn: Callable[[list[str]], list[list[float]]] | None,
    query: str,
    corpus: str,
    filters: dict | None = None,
    k: int = 10,
    final_n: int = 5,
) -> list[dict]:
    """Retrieve citable clauses with metadata pre-filtering and BM25 only."""
    if corpus not in ("spec", "warranty"):
        raise ValueError("corpus must be 'spec' or 'warranty'")
    table = "spec_clauses" if corpus == "spec" else "warranty_clauses"
    filters = filters or {}
    where, filter_params = _build_filters(corpus, filters)

    kw_params = {"q": query, "k": k, **filter_params}
    # Lexical arm: real Lakebase Search BM25 over the clause_tsv column. The bm25
    # score (<@>) is lower/more-negative for a better match, so ORDER BY ASC.
    bm25_index = _BM25_INDEX[corpus]
    kw_sql = (
        f"SELECT concat_ws('/', "
        f"{('grade, region, spec_edition' if corpus == 'spec' else 'product_line, region, version')}, section_ref) citation_key, "
        f"section_ref, clause_text "
        f"FROM {table} WHERE clause_tsv IS NOT NULL{where} "
        f"ORDER BY clause_tsv <@> to_bm25query(to_tsvector('english', %(q)s), "
        f"'{bm25_index}'::regclass) ASC LIMIT %(k)s"
    )

    with conn.cursor() as cur:
        cur.execute(kw_sql, kw_params)
        kw_rows = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
    return kw_rows[:final_n]


# Table, text-search config, indexed expressions, and index names for the precedent
# corpus come from prior_claims_indexes (shared, import-free), which
# lakebase/scripts/synced_tables.py also uses to build the index DDL. Each arm below
# ORDERs BY exactly those expressions, so the planner can use the expression indexes.
_DENSE_DISTANCE = f"{PRIOR_CLAIMS_EMBEDDING_EXPR} <=> %(qvec)s::vector"
_BM25_SCORE = (
    f"{PRIOR_CLAIMS_TSVECTOR_EXPR} <@> "
    f"to_bm25query(to_tsvector('{PRIOR_CLAIMS_TEXT_SEARCH_CONFIG}', %(text)s), "
    f"'reference.{PRIOR_CLAIMS_BM25_INDEX}'::regclass)"
)

# Each arm reads the table directly with the metadata filter inlined and ORDERs BY
# the indexed expression, so its ordered index scan is not blocked by a materialized
# shared CTE. Both <=> cosine distance and <@> BM25 return smaller scores for better
# matches, so ordering and rank assignment intentionally use ASC ({filters} is filled
# with the same bound-parameter predicates in both arms).
DENSE_ARM_SQL = f"""SELECT claim_id, verdict, approved_amount, {_DENSE_DISTANCE} AS s
  FROM {PRIOR_CLAIMS_TABLE}
  WHERE embedding IS NOT NULL AND coil_id <> %(coil_id)s {{filters}}
  ORDER BY {_DENSE_DISTANCE} ASC
  LIMIT %(k)s"""
FTS_ARM_SQL = f"""SELECT claim_id, verdict, approved_amount, {_BM25_SCORE} AS s
  FROM {PRIOR_CLAIMS_TABLE}
  WHERE coil_id <> %(coil_id)s {{filters}}
  ORDER BY {_BM25_SCORE} ASC
  LIMIT %(k)s"""
SIMILAR_CLAIMS_SQL = f"""
SELECT claim_id, verdict, approved_amount, 'dense' AS arm,
       row_number() OVER (ORDER BY s ASC, claim_id) AS rnk
FROM ({DENSE_ARM_SQL}) dense
UNION ALL
SELECT claim_id, verdict, approved_amount, 'fts' AS arm,
       row_number() OVER (ORDER BY s ASC, claim_id) AS rnk
FROM ({FTS_ARM_SQL}) fts
"""


def find_similar_prior_claims(
    conn: Any,
    embed_fn: Callable[[list[str]], list[list[float]]],
    text: str,
    coil_id: str,
    filters: dict | None = None,
    k: int = 10,
    final_n: int = 5,
) -> list[dict]:
    """Advisory dense + BM25 hybrid over prior claims, RRF-fused.

    Surfaces precedent and copy-paste-narrative fraud signals. Advisory only — it
    never denies money; that is the deterministic duplicate gate's job.
    """
    filters = filters or {}
    extra, params = (
        [],
        {"coil_id": coil_id, "text": text, "k": k, "qvec": _vector_literal(embed_fn([text])[0])},
    )
    for key in ("grade", "coating_class"):
        if filters.get(key) is not None:
            extra.append(f"AND {key} = %({key})s")
            params[key] = filters[key]
    sql = SIMILAR_CLAIMS_SQL.format(filters=" ".join(extra))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    arms: dict[str, list[tuple[int, str]]] = {}
    outcomes = {}
    for row in rows:
        arms.setdefault(row["arm"], []).append((row["rnk"], row["claim_id"]))
        outcomes[row["claim_id"]] = {
            "verdict": row["verdict"],
            "approved_amount": row["approved_amount"],
        }
    rankings = [[cid for _, cid in sorted(v)] for v in arms.values()]
    fused = rrf_fuse(rankings)
    return [
        {"claim_id": cid, "rrf_score": round(score, 6), **outcomes[cid]}
        for cid, score in fused[:final_n]
    ]
