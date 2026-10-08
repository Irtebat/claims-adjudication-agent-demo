"""LangChain chat model backed by a governed Unity Gateway model service."""

from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.output_parsers.openai_tools import parse_tool_calls
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from gateway_embed import backoff_delay, parse_retry_after
from workspace_client import workspace_client

MODEL_SERVICE = "fe-bar-ir.adjudication-agent.adjudication-reasoning"
GATEWAY_PATH = "/ai-gateway/mlflow/v1/chat/completions"


def _default_post(url: str, authorization: str, body: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": authorization, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


class UnityGatewayChatModel(BaseChatModel):
    """OpenAI-compatible governed chat client with refreshed SDK auth per call."""

    profile: str | None = None
    model_service: str = MODEL_SERVICE
    temperature: float = 0.0
    post_fn: Callable[[str, str, dict], dict] = _default_post
    max_retries: int = 6
    sleep_fn: Callable[[float], None] = time.sleep

    @property
    def _llm_type(self) -> str:
        return "databricks-unity-gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model_service, "temperature": self.temperature}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        **kwargs: Any,
    ):
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools], **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        client = workspace_client(self.profile)
        authorization = client.config.authenticate()["Authorization"]
        body: dict[str, Any] = {
            "model": self.model_service,
            "messages": convert_to_openai_messages(messages),
            "temperature": self.temperature,
        }
        if stop:
            body["stop"] = stop
        body.update(kwargs)
        url = client.config.host.rstrip("/") + GATEWAY_PATH
        for attempt in range(self.max_retries + 1):
            try:
                payload = self.post_fn(url, authorization, body)
                break
            except urllib.error.HTTPError as err:  # pragma: no cover - network path
                if err.code in (429, 503) and attempt < self.max_retries:
                    retry_after = parse_retry_after(err.headers.get("Retry-After"))
                    self.sleep_fn(backoff_delay(attempt, retry_after))
                    continue
                raise
            except (
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                urllib.error.URLError,
            ):
                if attempt < self.max_retries:
                    self.sleep_fn(backoff_delay(attempt, None))
                    continue
                raise
        choice = payload["choices"][0]
        message = choice["message"]
        raw_tool_calls = message.get("tool_calls") or []
        ai_message = AIMessage(
            content=message.get("content") or "",
            tool_calls=parse_tool_calls(raw_tool_calls) if raw_tool_calls else [],
            additional_kwargs={"tool_calls": raw_tool_calls} if raw_tool_calls else {},
            response_metadata={
                "finish_reason": choice.get("finish_reason"),
                "model_name": payload.get("model", self.model_service),
                "usage": payload.get("usage"),
            },
        )
        return ChatResult(generations=[ChatGeneration(message=ai_message)])
