"""Shared prior-claims corpus search constants. String constants only; no imports.

The precedent corpus is built in Unity Catalog (gold.prior_claims_corpus) and served
down as the Triggered synced table reference.prior_claims_corpus. Synced tables cannot
carry vector/tsvector columns, so the embedding is a pgvector text literal and the
lakebase_ann / lakebase_bm25 indexes are EXPRESSION indexes. This module is the single
source of those expressions and names: agent/src/retrieval.py ORDERs BY them, and
lakebase/scripts/synced_tables.py loads this file by path to build the index DDL, so
the query and the index cannot drift apart. Keep it import-free so it loads from any
layer without the agent's dependencies.
"""

PRIOR_CLAIMS_TABLE = "reference.prior_claims_corpus"
PRIOR_CLAIMS_TEXT_SEARCH_CONFIG = "english"
PRIOR_CLAIMS_EMBEDDING_EXPR = "embedding::vector(1024)"
PRIOR_CLAIMS_TSVECTOR_EXPR = "to_tsvector('english', defect_narrative)"
PRIOR_CLAIMS_ANN_INDEX = "prior_claims_corpus_lb_ann"
PRIOR_CLAIMS_BM25_INDEX = "prior_claims_corpus_lb_bm25"
