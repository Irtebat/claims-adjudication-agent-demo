import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from gateway_chat import UnityGatewayChatModel


def _client():
    client = MagicMock()
    client.config.host = "https://workspace.example/"
    client.config.authenticate.return_value = {"Authorization": "Bearer refreshed"}
    return client


def test_governed_chat_request_and_response():
    calls = []

    def post(url, authorization, body):
        calls.append((url, authorization, body))
        return {
            "model": "system.ai.gpt-5-2",
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        }

    with patch("gateway_chat.workspace_client", return_value=_client()):
        response = UnityGatewayChatModel(profile=None, post_fn=post).invoke(
            [HumanMessage(content="test")]
        )

    assert response.content == "OK"
    url, authorization, body = calls[0]
    assert url.endswith("/ai-gateway/mlflow/v1/chat/completions")
    assert authorization == "Bearer refreshed"
    assert body["model"] == "system.ai.gpt-5-2"
    assert body["temperature"] == 0.0


def test_governed_chat_preserves_response_format_binding():
    bodies = []

    def post(url, authorization, body):
        bodies.append(body)
        return {"choices": [{"message": {"content": '{"ok":true}'}}]}

    schema = {"type": "json_schema", "json_schema": {"name": "result", "schema": {}}}
    with patch("gateway_chat.workspace_client", return_value=_client()):
        UnityGatewayChatModel(post_fn=post).bind(response_format=schema).invoke("test")
    assert bodies[0]["response_format"] == schema


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://workspace.example", status, "error", {}, None)


def test_governed_chat_retries_429():
    post = MagicMock(
        side_effect=[
            _http_error(429),
            {"choices": [{"message": {"content": "OK"}}]},
        ]
    )
    sleep = MagicMock()

    with patch("gateway_chat.workspace_client", return_value=_client()):
        response = UnityGatewayChatModel(post_fn=post, sleep_fn=sleep).invoke("test")

    assert response.content == "OK"
    assert post.call_count == 2
    sleep.assert_called_once()


def test_governed_chat_does_not_retry_403():
    post = MagicMock(side_effect=_http_error(403))
    sleep = MagicMock()

    with (
        patch("gateway_chat.workspace_client", return_value=_client()),
        pytest.raises(urllib.error.HTTPError, match="HTTP Error 403"),
    ):
        UnityGatewayChatModel(post_fn=post, sleep_fn=sleep).invoke("test")

    post.assert_called_once()
    sleep.assert_not_called()
