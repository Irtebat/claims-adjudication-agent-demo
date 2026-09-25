"""Side-effect-free in-process ResponsesAgent evaluation adapter."""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

from mlflow.types.responses import ResponsesAgentRequest

AGENT_SRC = Path(__file__).resolve().parents[2] / "agent" / "src"
if str(AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(AGENT_SRC))
PERSISTENT_FORBIDDEN_MARKERS = ("403", "forbidden", "ip acl", "ip_acl")


def _forbidden(exc: Exception) -> bool:
    return any(marker in str(exc).lower() for marker in PERSISTENT_FORBIDDEN_MARKERS)


def response_request(claim: dict) -> ResponsesAgentRequest:
    return ResponsesAgentRequest(
        input=[{"role": "user", "content": json.dumps(claim, sort_keys=True)}],
        custom_inputs={"claim": claim, "persist": False},
    )


def predict_claim(claim: dict, agent=None, attempts: int = 3, sleep=time.sleep) -> dict:
    if agent is None:
        from agent import AGENT

        agent = AGENT
    request = response_request(claim)
    for attempt in range(attempts):
        try:
            response = agent.predict(request)
            dumped = response.model_dump() if hasattr(response, "model_dump") else response
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
