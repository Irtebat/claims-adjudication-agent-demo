"""Databricks workspace authentication shared by local and served runtimes."""

from __future__ import annotations

import os


def workspace_client(
    profile: str | None = None,
    *,
    http_timeout_seconds: float | None = None,
    retry_timeout_seconds: float | None = None,
):
    """Build an auto-refreshing SDK client for the active runtime boundary.

    When ``http_timeout_seconds`` and/or ``retry_timeout_seconds`` are given, the client
    is constructed through an explicit ``Config`` carrying those PUBLIC, documented SDK
    fields, so the low-level request client captures them AT CONSTRUCTION and every
    request the client makes inherits a real per-request HTTP/socket timeout (and a
    bounded retry window). The Genie advisory tool relies on this so a stalled Genie
    request cannot hang its worker thread indefinitely — there is no silent fallback to
    "no timeout". With both unset the client is built exactly as before (SDK defaults).
    """
    from databricks.sdk import WorkspaceClient

    client_id = os.environ.get("APP_SP_CLIENT_ID")
    client_secret = os.environ.get("APP_SP_CLIENT_SECRET")
    if bool(client_id) != bool(client_secret):
        raise ValueError(
            "APP_SP_CLIENT_ID and APP_SP_CLIENT_SECRET must be set together or both unset"
        )
    if client_id and client_secret:
        auth: dict = {
            "host": os.environ["DATABRICKS_HOST"],
            "client_id": client_id,
            "client_secret": client_secret,
        }
    else:
        auth = {"profile": profile or "fe-bar-ir-2026"}

    timeouts: dict = {}
    if http_timeout_seconds is not None:
        timeouts["http_timeout_seconds"] = http_timeout_seconds
    if retry_timeout_seconds is not None:
        timeouts["retry_timeout_seconds"] = retry_timeout_seconds
    if not timeouts:
        return WorkspaceClient(**auth)

    # Carry the timeout via the supported public Config fields so it is applied at
    # construction (the SDK's low-level client reads http_timeout_seconds /
    # retry_timeout_seconds when it is built, not per-call afterwards).
    from databricks.sdk.core import Config

    return WorkspaceClient(config=Config(**auth, **timeouts))
