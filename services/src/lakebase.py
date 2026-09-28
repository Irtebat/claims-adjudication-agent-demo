"""Lakebase Postgres connectivity for the services, authenticating AS the app SP.

Mirrors ``agent/src/db.py`` + ``agent/src/workspace_client.py``: when
``APP_SP_CLIENT_ID`` / ``APP_SP_CLIENT_SECRET`` are set (exported from the
``claims-agent`` scope) the Databricks SDK authenticates as the Wave 6 service
principal, and ``LAKEBASE_DB_USER`` selects its Postgres role. The SP therefore
carries the grants that gate every write in this layer (see migrate.py).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from config import LAKEBASE_DATABASE, LAKEBASE_ENDPOINT


def _workspace_client(profile: str | None):
    from databricks.sdk import WorkspaceClient

    client_id = os.environ.get("APP_SP_CLIENT_ID")
    client_secret = os.environ.get("APP_SP_CLIENT_SECRET")
    if bool(client_id) != bool(client_secret):
        raise RuntimeError(
            "APP_SP_CLIENT_ID and APP_SP_CLIENT_SECRET must be set together or both unset"
        )
    if client_id and client_secret:
        host = os.environ.get("DATABRICKS_HOST")
        return WorkspaceClient(host=host, client_id=client_id, client_secret=client_secret)
    return WorkspaceClient(profile=profile) if profile else WorkspaceClient()


def connection_params(profile: str | None = None) -> dict:
    client = _workspace_client(profile)
    endpoint_details = client.postgres.get_endpoint(name=LAKEBASE_ENDPOINT)
    credential = client.postgres.generate_database_credential(endpoint=LAKEBASE_ENDPOINT)
    return {
        "host": endpoint_details.status.hosts.host,
        "dbname": LAKEBASE_DATABASE,
        "user": os.environ.get("LAKEBASE_DB_USER") or client.current_user.me().user_name,
        "password": credential.token,
        "sslmode": "require",
    }


@contextmanager
def connect(profile: str | None = None, autocommit: bool = False) -> Iterator:
    import psycopg

    conn = psycopg.connect(autocommit=autocommit, **connection_params(profile))
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()
