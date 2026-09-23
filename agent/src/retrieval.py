"""Hybrid clause retrieval and similar-prior-claims — advisory context, never the authority.

Runs at agent/app runtime (psycopg to Lakebase + gateway embedding for the
query), NOT as a UC Python UDF (those cannot open Postgres 5432). Retrieval only
*finds and cites* candidate clauses; the deterministic ``compute_*`` authorities
decide (PLAN §6.4).

The lexical arm uses the real Lakebase Search **BM25** index: it scores rows with
``clause_tsv <@> to_bm25query(to_tsvector('english', query), '<index>'::regclass)``
(a lower/more-negative score is a better match, so ORDER BY ASC), driven by the
``lakebase_bm25`` index built at intake. The dense arm uses cosine distance
(``<=>``) over the normalized ``embedding`` column, served by the ``lakebase_ann``
index. Metadata filters are folded into BOTH arms, then results are fused with
Reciprocal Rank Fusion. The query is embedded with the same governed GTE model and
L2 normalization used at intake (provenance is stored on each clause row).
"""

from __future__ import annotations

from typing import Any, Callable

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
    embed_fn: Callable[[list[str]], list[list[float]]],
    query: str,
    corpus: str,
    filters: dict | None = None,
    k: int = 10,
    final_n: int = 5,
) -> list[dict]:
    """Hybrid retrieve over spec_clauses / warranty_clauses with metadata pre-filter + RRF."""
    if corpus not in ("spec", "warranty"):
        raise ValueError("corpus must be 'spec' or 'warranty'")
    table = "spec_clauses" if corpus == "spec" else "warranty_clauses"
    filters = filters or {}
    where, filter_params = _build_filters(corpus, filters)

    qvec = embed_fn([query])[0]
    vec_params = {"qvec": _vector_literal(qvec), "k": k, **filter_params}
    vec_sql = (
        f"SELECT clause_id, parent_clause_id, section_ref, clause_text "
        f"FROM {table} WHERE embedding IS NOT NULL{where} "
        f"ORDER BY embedding <=> %(qvec)s::vector LIMIT %(k)s"
    )
    kw_params = {"q": query, "k": k, **filter_params}
    # Lexical arm: real Lakebase Search BM25 over the clause_tsv column. The bm25
    # score (<@>) is lower/more-negative for a better match, so ORDER BY ASC.
    bm25_index = _BM25_INDEX[corpus]
    kw_sql = (
        f"SELECT clause_id, parent_clause_id, section_ref, clause_text "
        f"FROM {table} WHERE clause_tsv IS NOT NULL{where} "
        f"ORDER BY clause_tsv <@> to_bm25query(to_tsvector('english', %(q)s), "
        f"'{bm25_index}'::regclass) ASC LIMIT %(k)s"
    )

    with conn.cursor() as cur:
        cur.execute(vec_sql, vec_params)
        vec_rows = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
        cur.execute(kw_sql, kw_params)
        kw_rows = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    by_id = {r["clause_id"]: r for r in vec_rows + kw_rows}
    vec_ids = {r["clause_id"] for r in vec_rows}
    kw_ids = {r["clause_id"] for r in kw_rows}
    fused = rrf_fuse([[r["clause_id"] for r in vec_rows], [r["clause_id"] for r in kw_rows]])
    results = []
    for clause_id, score in fused[:final_n]:
        row = by_id[clause_id]
        results.append(
            {
                "clause_id": clause_id,
                "parent_clause_id": row["parent_clause_id"],
                "section_ref": row["section_ref"],
                "clause_text": row["clause_text"],
                "rrf_score": round(score, 6),
                "in_vector_arm": clause_id in vec_ids,
                "in_keyword_arm": clause_id in kw_ids,
            }
        )
    return results


SIMILAR_CLAIMS_SQL = """
WITH filtered AS (
  SELECT claim_id, coil_id, grade, coating_class, defect_code, defect_narrative,
         claimed_amount, claim_date
  FROM claims
  WHERE coil_id <> %(coil_id)s {filters}
),
trgm AS (
  SELECT claim_id, similarity(defect_narrative, %(text)s) AS s
  FROM filtered ORDER BY s DESC LIMIT %(k)s
),
fts AS (
  SELECT claim_id,
         ts_rank_cd(to_tsvector('english', defect_narrative),
                    replace(plainto_tsquery('english', %(text)s)::text, ' & ', ' | ')::tsquery) AS s
  FROM filtered
  WHERE to_tsvector('english', defect_narrative)
        @@ replace(plainto_tsquery('english', %(text)s)::text, ' & ', ' | ')::tsquery
  ORDER BY s DESC LIMIT %(k)s
)
SELECT claim_id, arm, rnk FROM (
  SELECT claim_id, 'trgm' arm, row_number() OVER (ORDER BY s DESC) rnk FROM trgm
  UNION ALL
  SELECT claim_id, 'fts' arm, row_number() OVER (ORDER BY s DESC) rnk FROM fts
) ranked
"""


def find_similar_prior_claims(
    conn: Any,
    text: str,
    coil_id: str,
    filters: dict | None = None,
    k: int = 10,
    final_n: int = 5,
) -> list[dict]:
    """Advisory hybrid (trgm + FTS on narrative) over prior claims, RRF-fused.

    Surfaces precedent and copy-paste-narrative fraud signals. Advisory only — it
    never denies money; that is the deterministic duplicate gate's job.
    """
    filters = filters or {}
    extra, params = [], {"coil_id": coil_id, "text": text, "k": k}
    for key in ("grade", "coating_class"):
        if filters.get(key) is not None:
            extra.append(f"AND {key} = %({key})s")
            params[key] = filters[key]
    sql = SIMILAR_CLAIMS_SQL.format(filters=" ".join(extra))
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]

    arms: dict[str, list[tuple[int, str]]] = {}
    for row in rows:
        arms.setdefault(row["arm"], []).append((row["rnk"], row["claim_id"]))
    rankings = [[cid for _, cid in sorted(v)] for v in arms.values()]
    fused = rrf_fuse(rankings)
    return [{"claim_id": cid, "rrf_score": round(score, 6)} for cid, score in fused[:final_n]]
