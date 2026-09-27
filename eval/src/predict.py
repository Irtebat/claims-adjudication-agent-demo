"""Side-effect-free packaged ResponsesAgent evaluation adapter."""

from __future__ import annotations

import json
import random
import time

import mlflow
from mlflow.types.responses import ResponsesAgentRequest

PERSISTENT_FORBIDDEN_MARKERS = ("403", "forbidden", "ip acl", "ip_acl")
_MODEL = None
_MODEL_URI = None


def _forbidden(exc: Exception) -> bool:
    return any(marker in str(exc).lower() for marker in PERSISTENT_FORBIDDEN_MARKERS)


def response_request(claim: dict) -> ResponsesAgentRequest:
    return ResponsesAgentRequest(
        input=[{"role": "user", "content": json.dumps(claim, sort_keys=True)}],
        custom_inputs={"claim": claim, "persist": False},
    )


def load_candidate(model_uri: str, loader=mlflow.pyfunc.load_model):
    """Load and cache the packaged candidate pinned by ``evaluate.py``."""
    global _MODEL, _MODEL_URI
    if _MODEL is None or _MODEL_URI != model_uri:
        _MODEL = loader(model_uri)
        _MODEL_URI = model_uri
    return _MODEL


def predict_claim(claim: dict, model=None, attempts: int = 3, sleep=time.sleep) -> dict:
    if model is None:
        if _MODEL is None:
            raise RuntimeError("packaged candidate is not loaded; call load_candidate(model_uri)")
        model = _MODEL
    request = response_request(claim)
    for attempt in range(attempts):
        try:
            response = model.predict(request.model_dump())
            dumped = response.model_dump() if hasattr(response, "model_dump") else response
            if isinstance(dumped, list) and len(dumped) == 1:
                dumped = dumped[0]
            if not isinstance(dumped, dict):
                raise RuntimeError("packaged candidate returned a non-dict response")
            if (
                dumped.get("custom_outputs", {}).get("write_result", {}).get("persisted")
                is not False
            ):
                raise RuntimeError("evaluation persistence invariant failed")
            return dumped
        except Exception as exc:
            if _forbidden(exc) or attempt + 1 == attempts:
                raise
            sleep((2**attempt) + random.random())
    raise AssertionError("unreachable")


def predict_fn(claim: dict) -> dict:
    return predict_claim(claim)
