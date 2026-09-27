from unittest.mock import patch

from workspace_client import workspace_client


def test_workspace_client_uses_profile_without_service_principal(monkeypatch):
    monkeypatch.delenv("APP_SP_CLIENT_ID", raising=False)
    monkeypatch.delenv("APP_SP_CLIENT_SECRET", raising=False)
    with patch("databricks.sdk.WorkspaceClient") as constructor:
        workspace_client(None)
    constructor.assert_called_once_with(profile="fe-bar")


def test_workspace_client_uses_service_principal_environment(monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://workspace.example")
    monkeypatch.setenv("APP_SP_CLIENT_ID", "client-id")
    monkeypatch.setenv("APP_SP_CLIENT_SECRET", "secret")
    with patch("databricks.sdk.WorkspaceClient") as constructor:
        workspace_client("ignored")
    constructor.assert_called_once_with(
        host="https://workspace.example", client_id="client-id", client_secret="secret"
    )
