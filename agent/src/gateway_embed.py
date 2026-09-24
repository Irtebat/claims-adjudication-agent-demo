"""Shared embedding helper — governed GTE embeddings via the Unity Gateway model service.

Used by BOTH the policy intake (embedding clause text at load) and the agent-runtime
retrieval (embedding the query). It calls the governed model service
``system.ai.gte-large-en`` at ``/ai-gateway/mlflow/v1/embeddings`` with an OAuth
bearer token from the Databricks SDK credential provider, refreshed per batch. GTE
output is 1024-dim and NOT normalized, so we L2-normalize both stored and query
vectors and use cosine distance. No ``dimensions`` param is sent (unsupported for
GTE). ``ai_query`` is deliberately not used — it bypasses most gateway governance.

The HTTP transport is injectable (``post_fn``) so batching, normalization and
retry/backoff are unit-testable without the network.
"""

from __future__ import annotations

import http.client
import json
import math
import random
import time
import urllib.error
import urllib.request
from typing import Callable

MODEL_SERVICE = "system.ai.gte-large-en"
EMBEDDING_DIM = 1024
GATEWAY_PATH = "/ai-gateway/mlflow/v1/embeddings"
DEFAULT_BATCH = 16
NORMALIZATION = "l2"
PROVENANCE = f"{MODEL_SERVICE}|normalization={NORMALIZATION}|dim={EMBEDDING_DIM}"


def l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        return list(vector)
    return [x / norm for x in vector]


def chunk(items: list, size: int = DEFAULT_BATCH) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def backoff_delay(attempt: int, retry_after: float | None = None, base: float = 0.5) -> float:
    """Exponential backoff with full jitter, honoring Retry-After when provided."""
    if retry_after is not None:
        return retry_after + random.uniform(0.0, 0.25)
    return random.uniform(0.0, base * (2**attempt))


def _extract_embeddings(payload: dict) -> list[list[float]]:
    if "data" in payload:  # OpenAI-compatible shape
        rows = sorted(payload["data"], key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in rows]
    if "embeddings" in payload:  # MLflow gateway shape
        return list(payload["embeddings"])
    raise ValueError(f"Unrecognized embeddings response shape: {sorted(payload)[:5]}")


def _default_token_provider(profile: str) -> Callable[[], tuple[str, str]]:
    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient(profile=profile)

    def provider() -> tuple[str, str]:
        headers = client.config.authenticate()  # refreshes the OAuth token as needed
        return client.config.host.rstrip("/"), headers["Authorization"]

    return provider


def _default_post(url: str, authorization: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def embed_texts(
    texts: list[str],
    profile: str = "fe-bar",
    batch_size: int = DEFAULT_BATCH,
    max_retries: int = 6,
    normalize: bool = True,
    token_provider: Callable[[], tuple[str, str]] | None = None,
    post_fn: Callable[[str, str, dict], dict] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[list[float]]:
    """Embed ``texts`` in bounded batches; refresh the token per batch; retry 429s.

    Returns one 1024-dim (L2-normalized) vector per input text, in order.
    """
    if not texts:
        return []
    provider = token_provider or _default_token_provider(profile)
    post = post_fn or _default_post
    out: list[list[float]] = []
    for batch in chunk(texts, batch_size):
        host, authorization = provider()
        url = host + GATEWAY_PATH
        body = {"input": batch, "model": MODEL_SERVICE}
        for attempt in range(max_retries + 1):
            try:
                payload = post(url, authorization, body)
                vectors = _extract_embeddings(payload)
                if len(vectors) != len(batch):
                    raise ValueError(f"Expected {len(batch)} embeddings, got {len(vectors)}")
                out.extend(l2_normalize(v) if normalize else list(v) for v in vectors)
                break
            except urllib.error.HTTPError as err:  # pragma: no cover - network path
                if err.code == 429 and attempt < max_retries:
                    retry_after = parse_retry_after(err.headers.get("Retry-After"))
                    sleep_fn(backoff_delay(attempt, retry_after))
                    continue
                raise
            except (
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                urllib.error.URLError,
            ):
                if attempt < max_retries:
                    sleep_fn(backoff_delay(attempt, None))
                    continue
                raise
    return out
