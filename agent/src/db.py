"""Lakebase Postgres connectivity for agent-runtime code (psycopg + OAuth).

The retrieval, duplicate and intake code talk to Lakebase over port 5432 with a
short-lived OAuth credential from the Databricks SDK. This is the connection path
UC Python UDFs cannot use (they only reach 80/443/53), which is exactly why
retrieval and duplicate detection run here at agent/app runtime, not as UDFs.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg

DEFAULT_ENDPOINT = "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
DEFAULT_DATABASE = "databricks_postgres"


def connection_params(
    profile: str = "fe-bar",
    endpoint: str = DEFAULT_ENDPOINT,
    database: str = DEFAULT_DATABASE,
) -> dict:
    """Resolve host + fresh OAuth credential for a Lakebase endpoint via the SDK."""
    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient(profile=profile)
    endpoint_details = client.postgres.get_endpoint(name=endpoint)
    credential = client.postgres.generate_database_credential(endpoint=endpoint)
    return {
        "host": endpoint_details.status.hosts.host,
        "dbname": database,
        "user": client.current_user.me().user_name,
        "password": credential.token,
        "sslmode": "require",
    }


@contextmanager
def connect(
    profile: str = "fe-bar",
    endpoint: str = DEFAULT_ENDPOINT,
    database: str = DEFAULT_DATABASE,
    autocommit: bool = False,
) -> Iterator[psycopg.Connection]:
    params = connection_params(profile, endpoint, database)
    conn = psycopg.connect(autocommit=autocommit, **params)
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
