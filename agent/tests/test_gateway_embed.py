"""Gateway embedding helper: normalization, batching, and 429 retry — no network."""

import urllib.error
from email.message import Message

import pytest

from gateway_embed import (
    backoff_delay,
    chunk,
    embed_texts,
    l2_normalize,
    parse_retry_after,
)


def test_l2_normalize_unit_length():
    assert l2_normalize([3.0, 4.0]) == pytest.approx([0.6, 0.8])
    assert l2_normalize([0.0, 0.0]) == [0.0, 0.0]


def test_chunk_batches():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_parse_retry_after():
    assert parse_retry_after("2.5") == 2.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("soon") is None


def test_backoff_delay_honors_retry_after():
    delay = backoff_delay(3, retry_after=2.0)
    assert 2.0 <= delay <= 2.5
    assert 0.0 <= backoff_delay(0, base=0.5) <= 0.5


def _token():
    return ("https://host.example", "Bearer test")


def _post_ok(url, authorization, body):
    assert url.endswith("/ai-gateway/mlflow/v1/embeddings")
    assert authorization == "Bearer test"
    assert "dimensions" not in body  # GTE does not accept dimensions
    return {"data": [{"index": i, "embedding": [3.0, 4.0]} for i, _ in enumerate(body["input"])]}


def test_embed_texts_batches_and_normalizes():
    calls = []

    def post(url, auth, body):
        calls.append(len(body["input"]))
        return _post_ok(url, auth, body)

    vectors = embed_texts(
        [f"t{i}" for i in range(20)],
        batch_size=16,
        token_provider=_token,
        post_fn=post,
    )
    assert len(vectors) == 20
    assert calls == [16, 4]  # two bounded batches
    assert all(v == pytest.approx([0.6, 0.8]) for v in vectors)


def test_embed_texts_retries_on_429():
    slept = []
    state = {"n": 0}

    def flaky_post(url, auth, body):
        state["n"] += 1
        if state["n"] == 1:
            headers = Message()
            headers["Retry-After"] = "0"
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", headers, None)
        return _post_ok(url, auth, body)

    vectors = embed_texts(
        ["only"], token_provider=_token, post_fn=flaky_post, sleep_fn=slept.append
    )
    assert len(vectors) == 1
    assert len(slept) == 1  # backed off once before succeeding
