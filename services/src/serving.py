"""Invoke the governed claims-adjudication serving endpoint.

The worker calls the endpoint with ``persist=true`` so the governed model performs
the atomic Lakebase write (adjudication + decision record + outbox) via
``agent/src/writer.py`` inside the endpoint — the money-path write stays behind the
deterministic authorities, not in this layer.
"""

from __future__ import annotations

from typing import Any

from config import SERVING_ENDPOINT


def build_request(claim: dict, persist: bool = True) -> dict:
    """The ResponsesAgent request shape (matches agent/src register/deploy)."""
    import json

    return {
        "input": [{"role": "user", "content": json.dumps(claim)}],
        "custom_inputs": {"persist": persist, "claim": claim},
    }


def invoke(workspace_client: Any, claim: dict, persist: bool = True) -> dict:
    """POST one claim to the endpoint's invocations route; return the parsed response."""
    response = workspace_client.api_client.do(
        "POST",
        f"/serving-endpoints/{SERVING_ENDPOINT}/invocations",
        body=build_request(claim, persist=persist),
    )
    return response
