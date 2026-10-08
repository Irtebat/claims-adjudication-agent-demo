from unittest.mock import patch

import pytest

from workspace_client import workspace_client


def test_workspace_client_uses_profile_without_service_principal(monkeypatch):
    monkeypatch.delenv("APP_SP_CLIENT_ID", raising=False)
    monkeypatch.delenv("APP_SP_CLIENT_SECRET", raising=False)
    with patch("databricks.sdk.WorkspaceClient") as constructor:
        workspace_client(None)
    constructor.assert_called_once_with(profile="fe-bar-ir-2026")


def test_workspace_client_uses_service_principal_environment(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("APP_SP_CLIENT_ID", "client-id")
    monkeypatch.setenv("APP_SP_CLIENT_SECRET", "secret")
    with patch("databricks.sdk.WorkspaceClient") as constructor:
        workspace_client("ignored")
    constructor.assert_called_once_with(
        host="https://workspace.example", client_id="client-id", client_secret="secret"
    )


@pytest.mark.parametrize(
    ("client_id", "client_secret"),
    [("client-id", None), (None, "secret")],
)
def test_workspace_client_rejects_partial_service_principal_environment(
    monkeypatch, client_id, client_secret
):
    for name, value in (
        ("APP_SP_CLIENT_ID", client_id),
        ("APP_SP_CLIENT_SECRET", client_secret),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match="must be set together or both unset"):
        workspace_client()


def test_workspace_client_applies_request_timeout_via_public_config(monkeypatch):
    # The Genie advisory tool needs a REAL per-request timeout. Assert it is carried via
    # the SUPPORTED public Config fields (http_timeout_seconds / retry_timeout_seconds)
    # and applied AT CONSTRUCTION — WorkspaceClient(config=Config(...)) — not poked onto
    # an already-built client's private attributes.
    monkeypatch.delenv("APP_SP_CLIENT_ID", raising=False)
    monkeypatch.delenv("APP_SP_CLIENT_SECRET", raising=False)
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured["config_kwargs"] = kwargs

    with (
        patch("databricks.sdk.WorkspaceClient") as constructor,
        patch("databricks.sdk.core.Config", FakeConfig),
    ):
        workspace_client("p", http_timeout_seconds=12.0, retry_timeout_seconds=9.0)

    # Built FROM a Config object (construction-time application of the timeout).
    assert list(constructor.call_args.kwargs) == ["config"]
    assert isinstance(constructor.call_args.kwargs["config"], FakeConfig)
    # The timeout rides on the documented public Config fields, alongside the auth.
    assert captured["config_kwargs"]["http_timeout_seconds"] == 12.0
    assert captured["config_kwargs"]["retry_timeout_seconds"] == 9.0
    assert captured["config_kwargs"]["profile"] == "p"


def test_workspace_client_timeout_config_carries_service_principal_auth(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("APP_SP_CLIENT_ID", "client-id")
    monkeypatch.setenv("APP_SP_CLIENT_SECRET", "secret")
    captured = {}

    class FakeConfig:
        def __init__(self, **kwargs):
            captured["config_kwargs"] = kwargs

    with (
        patch("databricks.sdk.WorkspaceClient") as constructor,
        patch("databricks.sdk.core.Config", FakeConfig),
    ):
        workspace_client("ignored", http_timeout_seconds=30.0)

    assert list(constructor.call_args.kwargs) == ["config"]
    cfg = captured["config_kwargs"]
    assert cfg["http_timeout_seconds"] == 30.0
    assert "retry_timeout_seconds" not in cfg  # only set when explicitly provided
    assert cfg["host"] == "https://workspace.example"
    assert cfg["client_id"] == "client-id"
    assert cfg["client_secret"] == "secret"


def test_workspace_client_without_timeout_builds_plain_client(monkeypatch):
    # Regression: with no timeout params the construction path is byte-for-byte unchanged
    # (no Config object, SDK defaults apply) so every other caller is unaffected.
    monkeypatch.delenv("APP_SP_CLIENT_ID", raising=False)
    monkeypatch.delenv("APP_SP_CLIENT_SECRET", raising=False)
    with (
        patch("databricks.sdk.WorkspaceClient") as constructor,
        patch("databricks.sdk.core.Config") as config_cls,
    ):
        workspace_client("p")
    constructor.assert_called_once_with(profile="p")
    config_cls.assert_not_called()
