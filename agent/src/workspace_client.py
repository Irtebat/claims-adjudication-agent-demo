"""Databricks workspace authentication shared by local and served runtimes."""

from __future__ import annotations

import os


def workspace_client(profile: str | None = None):
    """Build an auto-refreshing SDK client for the active runtime boundary."""
    from databricks.sdk import WorkspaceClient

    client_id = os.environ.get("APP_SP_CLIENT_ID")
    client_secret = os.environ.get("APP_SP_CLIENT_SECRET")
    if client_id and client_secret:
        return WorkspaceClient(
            host=os.environ["DATABRICKS_HOST"],
            client_id=client_id,
            client_secret=client_secret,
        )
    return WorkspaceClient(profile=profile or "fe-bar")
