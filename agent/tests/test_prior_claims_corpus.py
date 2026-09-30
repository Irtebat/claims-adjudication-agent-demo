"""Lakehouse-built prior-claims corpus: vector encoding and embedding-reuse rule."""

import hashlib

import pytest

from gateway_embed import EMBEDDING_DIM, PROVENANCE
from prior_claims_corpus import narrative_sha256, reuse_embedding, vector_literal


def test_vector_literal_is_a_pgvector_text_literal():
    literal = vector_literal([0.5] + [0.0] * (EMBEDDING_DIM - 1))
    assert literal.startswith("[0.5,0,") and literal.endswith("]")
    assert literal.count(",") == EMBEDDING_DIM - 1
    assert " " not in literal


def test_vector_literal_round_trips_at_float4_precision():
    vec = [(-1) ** i * (i + 1) / 3000.0 for i in range(EMBEDDING_DIM)]
    parsed = [float(x) for x in vector_literal(vec)[1:-1].split(",")]
    # float4 (pgvector storage) has ~6e-8 relative precision; 8 digits stay well inside it.
    assert max(abs(a - b) / abs(a) for a, b in zip(vec, parsed)) < 1e-7


def test_vector_literal_rejects_wrong_dimension():
    with pytest.raises(ValueError):
        vector_literal([0.1] * 3)


def test_narrative_hash_matches_spark_sha2_256_hex():
    # Spark's sha2(col, 256) is the lowercase hex SHA-256 of the UTF-8 bytes.
    assert narrative_sha256("édge crack") == hashlib.sha256("édge crack".encode()).hexdigest()


def _prior(narrative, provenance=PROVENANCE, embedding="[0.1]"):
    return {
        "narrative_sha256": narrative_sha256(narrative),
        "embedding_provenance": provenance,
        "embedding": embedding,
    }


def test_reuse_only_when_narrative_and_provenance_match():
    assert reuse_embedding(_prior("rust at edge"), "rust at edge")
    assert not reuse_embedding(_prior("rust at edge"), "rust at edge, severe")  # edited
    assert not reuse_embedding(_prior("x", provenance="other-model|dim=768"), "x")  # new model
    assert not reuse_embedding(_prior("x", embedding=None), "x")  # never embedded
    assert not reuse_embedding(None, "x")  # new claim


def test_job_reuse_predicate_matches_the_pure_rule():
    # The Spark job must gate reuse on the same three conditions as reuse_embedding.
    from pathlib import Path

    job = (Path(__file__).resolve().parents[1] / "src/prior_claims_corpus_job.py").read_text()
    assert '(F.col("_prior_sha") == F.col("narrative_sha256"))' in job
    assert '(F.col("_prior_provenance") == F.lit(PROVENANCE))' in job
    assert 'F.col("_prior_embedding").isNotNull()' in job
    assert 'F.col("decision_status") == "FINAL"' in job
    assert "WHEN NOT MATCHED BY SOURCE THEN DELETE" in job
    assert "delta.enableChangeDataFeed = true" in job
