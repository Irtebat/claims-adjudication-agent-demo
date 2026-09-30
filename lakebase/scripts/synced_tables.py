"""Create, re-sync, and recreate the Lakebase ``reference.*`` synced tables.

Three distinct paths, because they have different side effects on Postgres grants:

* ``create`` — creates every synced table that does not exist yet (``databricks
  postgres create-synced-table``, Triggered mode) and skips the ones that do. The
  Postgres table only exists once the initial sync completes, so it then polls
  ``get-synced-table`` until ``status.detailed_state`` is ``SYNCED_TABLE_ONLINE*``
  (bounded; a FAILED state or a timeout raises). A newly created table has no consumer
  grants, so when (and only when) something was created — and only after it is
  ONLINE — this builds the post-create indexes and runs
  ``regrant_synced_table_selects.py``.
* ``resync`` — the routine path. A Triggered synced table is refreshed by running an
  update of its managed sync pipeline (``status.pipeline_id`` from
  ``get-synced-table``), which is what the Catalog "Sync now" button and the Jobs
  "Database Table Sync pipeline" task do. The update applies source Delta CDF changes
  to the EXISTING Postgres table, so ownership, grants, and indexes are untouched:
  this path never re-grants. It never passes ``--full-refresh``. For the precedent
  corpus it then ensures the search indexes and runs ``VACUUM (ANALYZE)`` so the
  ``lakebase_bm25`` corpus statistics reflect the new rows.
* ``recreate`` — the schema-change path only (a non-additive source schema change a
  Triggered sync cannot apply). It deletes the synced table, drops the Postgres table
  (``delete-synced-table`` leaves it behind), creates it again, rebuilds indexes, and
  re-grants, because the recreated table is owned by a different role and has lost
  every prior grant.

Usage (``lakebase/run.py`` wraps these with ``--profile fe-bar``):
    python lakebase/scripts/synced_tables.py create
    python lakebase/scripts/synced_tables.py resync --tables customer_heat_risk prior_claims_corpus
    python lakebase/scripts/synced_tables.py recreate --table prior_claims_corpus
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROFILE = "fe-bar"
PROJECT = "fe-bar-operational-plane"
BRANCH = f"projects/{PROJECT}/branches/production"
ENDPOINT = f"{BRANCH}/endpoints/primary"
DATABASE = "databricks_postgres"
LAKEBASE_CATALOG = "fe_bar_operational"
SOURCE_CATALOG = "fe-bar-ir"
STORAGE_CATALOG = "fe-bar-ir"
STORAGE_SCHEMA = "default"
POSTGRES_SCHEMA = "reference"
SCRIPTS = Path(__file__).resolve().parent


@dataclass(frozen=True)
class SyncedTable:
    name: str
    primary_key: tuple[str, ...]
    source_schema: str = "silver"

    @property
    def synced_name(self) -> str:
        return f"{LAKEBASE_CATALOG}.{POSTGRES_SCHEMA}.{self.name}"

    @property
    def resource(self) -> str:
        return f"synced_tables/{self.synced_name}"

    @property
    def source(self) -> str:
        return f"{SOURCE_CATALOG}.{self.source_schema}.{self.name}"


# spec_standards and coating_warranty_terms are not synced down: the policy corpus is
# authored directly into Lakebase by lakebase/src/policy_intake.py.
TABLES = [
    SyncedTable("heats_coils", ("coil_id",)),
    SyncedTable("mill_test_certs", ("cert_id",)),
    SyncedTable("customers", ("customer_id",)),
    SyncedTable("suppliers", ("supplier_id",)),
    SyncedTable("defect_codes", ("defect_code",)),
    # Advisory customer/heat risk from the offline fraud-graph job (agent/).
    SyncedTable("customer_heat_risk", ("customer_id", "heat_no"), "gold"),
    # Precedent corpus built in the lakehouse by agent's prior_claims_corpus job.
    SyncedTable("prior_claims_corpus", ("claim_id",), "gold"),
]
BY_NAME = {table.name: table for table in TABLES}

# Tables the routine refresh rebuilds in UC and therefore re-syncs.
ROUTINE_RESYNC = ("customer_heat_risk", "prior_claims_corpus")


# Postgres objects built ON a synced table (indexes are allowed on synced tables).
# Synced tables cannot carry vector/tsvector columns, so the corpus indexes are
# expression indexes. The expressions come from agent/src/retrieval.py — the single
# source the retrieval arms ORDER BY — so the index and the query cannot drift apart.
def _load_retrieval():
    path = SCRIPTS.parents[1] / "agent" / "src" / "retrieval.py"
    spec = importlib.util.spec_from_file_location("agent_retrieval_constants", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_retrieval = _load_retrieval()
POST_CREATE_SQL = {
    "prior_claims_corpus": [
        (
            f"CREATE INDEX IF NOT EXISTS {_retrieval.PRIOR_CLAIMS_ANN_INDEX} "
            f"ON {_retrieval.PRIOR_CLAIMS_TABLE} "
            f"USING lakebase_ann (({_retrieval.PRIOR_CLAIMS_EMBEDDING_EXPR}) vector_cosine_ops)"
        ),
        (
            f"CREATE INDEX IF NOT EXISTS {_retrieval.PRIOR_CLAIMS_BM25_INDEX} "
            f"ON {_retrieval.PRIOR_CLAIMS_TABLE} "
            f"USING lakebase_bm25 (({_retrieval.PRIOR_CLAIMS_TSVECTOR_EXPR}) tsvector_bm25_ops)"
        ),
    ],
}
# After new rows land, VACUUM refreshes the lakebase_bm25 corpus statistics.
POST_SYNC_SQL = {
    "prior_claims_corpus": POST_CREATE_SQL["prior_claims_corpus"]
    + ["VACUUM (ANALYZE) reference.prior_claims_corpus"],
}

ONLINE_PREFIX = "SYNCED_TABLE_ONLINE"
# Initial-sync wait bound: ONLINE_ATTEMPTS polls, ONLINE_INTERVAL seconds apart (1 hour).
ONLINE_ATTEMPTS = 240
ONLINE_INTERVAL = 15.0
TERMINAL_OK = {"COMPLETED"}
TERMINAL_FAILED = {"FAILED", "CANCELED"}

Runner = Callable[..., subprocess.CompletedProcess]


def databricks(
    *parts: str, runner: Runner = subprocess.run, check: bool = True
) -> dict | None:
    """Run a databricks CLI call with the explicit profile; return parsed JSON output."""
    result = runner(
        ["databricks", *parts, "--profile", PROFILE, "-o", "json"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        if check:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return None
    return json.loads(result.stdout) if result.stdout.strip() else {}


def create_spec(table: SyncedTable) -> dict:
    return {
        "spec": {
            "source_table_full_name": table.source,
            "primary_key_columns": list(table.primary_key),
            "scheduling_policy": "TRIGGERED",
            "branch": BRANCH,
            "postgres_database": DATABASE,
            "create_database_objects_if_missing": True,
            "new_pipeline_spec": {
                "storage_catalog": STORAGE_CATALOG,
                "storage_schema": STORAGE_SCHEMA,
            },
        }
    }


def exists(table: SyncedTable, runner: Runner = subprocess.run) -> bool:
    return (
        databricks(
            "postgres", "get-synced-table", table.resource, runner=runner, check=False
        )
        is not None
    )


def create_one(table: SyncedTable, runner: Runner = subprocess.run) -> dict:
    return databricks(
        "postgres",
        "create-synced-table",
        table.synced_name,
        "--json",
        json.dumps(create_spec(table)),
        runner=runner,
    )


def pg_execute(statements: list[str]) -> None:
    """Run maintenance/DDL on Lakebase as the invoking superuser (autocommit for VACUUM)."""
    import psycopg
    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient(profile=PROFILE)
    host = client.postgres.get_endpoint(name=ENDPOINT).status.hosts.host
    token = client.postgres.generate_database_credential(endpoint=ENDPOINT).token
    with (
        psycopg.connect(
            host=host,
            dbname=DATABASE,
            user=client.current_user.me().user_name,
            password=token,
            sslmode="require",
            autocommit=True,
        ) as conn,
        conn.cursor() as cur,
    ):
        for statement in statements:
            cur.execute(statement)


def regrant(runner: Runner = subprocess.run) -> None:
    """Re-apply consumer SELECT grants (create / recreate paths only)."""
    runner(
        [
            "uv",
            "run",
            "--with",
            "psycopg[binary]==3.2.10",
            "--with",
            "databricks-sdk>=0.81.0",
            "python",
            str(SCRIPTS / "regrant_synced_table_selects.py"),
            "--profile",
            PROFILE,
        ],
        check=True,
    )


def wait_for_update(
    pipeline_id: str,
    update_id: str,
    runner: Runner = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = 120,
    interval: float = 15.0,
) -> str:
    for _ in range(attempts):
        update = databricks(
            "pipelines", "get-update", pipeline_id, update_id, runner=runner
        )
        state = str((update.get("update") or {}).get("state", "")).upper()
        if state in TERMINAL_OK:
            return state
        if state in TERMINAL_FAILED:
            raise RuntimeError(
                f"Sync pipeline {pipeline_id} update {update_id} ended {state}"
            )
        sleep(interval)
    raise RuntimeError(f"Sync pipeline {pipeline_id} update {update_id} did not finish")


def wait_for_online(
    table: SyncedTable,
    runner: Runner = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    attempts: int | None = None,
    interval: float | None = None,
) -> str:
    """Poll until the synced table is ONLINE, i.e. its Postgres table exists and is loaded.

    Raises on any FAILED detailed state and on timeout, so nothing downstream (index
    DDL, re-grant) ever runs against a table that is not there.
    """
    attempts = ONLINE_ATTEMPTS if attempts is None else attempts
    interval = ONLINE_INTERVAL if interval is None else interval
    state = ""
    for _ in range(attempts):
        synced = databricks(
            "postgres", "get-synced-table", table.resource, runner=runner
        )
        state = str((synced.get("status") or {}).get("detailed_state", "")).upper()
        if state.startswith(ONLINE_PREFIX):
            return state
        if "FAILED" in state:
            raise RuntimeError(f"{table.synced_name} initial sync failed: {state}")
        sleep(interval)
    raise RuntimeError(
        f"{table.synced_name} did not reach {ONLINE_PREFIX} within "
        f"{attempts * interval:.0f}s (last state: {state or 'unknown'})"
    )


def resync_one(
    table: SyncedTable,
    runner: Runner = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Trigger and await one incremental sync of an EXISTING synced table (no regrant)."""
    synced = databricks("postgres", "get-synced-table", table.resource, runner=runner)
    pipeline_id = (synced.get("status") or {}).get("pipeline_id")
    if not pipeline_id:
        raise RuntimeError(f"{table.synced_name} has no sync pipeline_id: {synced}")
    started = databricks("pipelines", "start-update", pipeline_id, runner=runner)
    update_id = started["update_id"]
    state = wait_for_update(pipeline_id, update_id, runner=runner, sleep=sleep)
    return {
        "table": table.synced_name,
        "pipeline_id": pipeline_id,
        "update_id": update_id,
        "state": state,
    }


def create(
    runner: Runner = subprocess.run,
    pg: Callable[[list[str]], None] = pg_execute,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    created = []
    for table in TABLES:
        if exists(table, runner=runner):
            print(f"Synced table {table.name} already exists; skipping.", flush=True)
            continue
        create_one(table, runner=runner)
        created.append(table.name)
    # The Postgres table appears only when the initial sync completes: wait for every
    # created table to be ONLINE before any index DDL or re-grant touches it.
    for name in created:
        wait_for_online(BY_NAME[name], runner=runner, sleep=sleep)
    for name in created:
        if name in POST_CREATE_SQL:
            pg(POST_CREATE_SQL[name])
    if created:
        # New tables start with no consumer grants.
        regrant(runner=runner)
    return {"created": created, "regranted": bool(created)}


def resync(
    names: list[str],
    runner: Runner = subprocess.run,
    pg: Callable[[list[str]], None] = pg_execute,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    results = []
    for name in names:
        table = BY_NAME[name]
        results.append(resync_one(table, runner=runner, sleep=sleep))
        if name in POST_SYNC_SQL:
            pg(POST_SYNC_SQL[name])
    # Deliberately no regrant: a triggered sync keeps the existing table and its grants.
    return {"resynced": results, "regranted": False}


def recreate(
    name: str,
    runner: Runner = subprocess.run,
    pg: Callable[[list[str]], None] = pg_execute,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    table = BY_NAME[name]
    if exists(table, runner=runner):
        databricks("postgres", "delete-synced-table", table.resource, runner=runner)
    # delete-synced-table leaves the Postgres table behind; drop it so create can rebuild.
    pg([f"DROP TABLE IF EXISTS {POSTGRES_SCHEMA}.{table.name}"])
    create_one(table, runner=runner)
    wait_for_online(table, runner=runner, sleep=sleep)
    if name in POST_CREATE_SQL:
        pg(POST_CREATE_SQL[name])
    # The recreated table is owned by a different role and has lost every grant.
    regrant(runner=runner)
    return {"recreated": table.synced_name, "regranted": True}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser(
        "create", help="Create missing synced tables (+ indexes, regrant if any)"
    )
    resync_parser = sub.add_parser(
        "resync", help="Trigger an incremental sync (never regrants)"
    )
    resync_parser.add_argument(
        "--tables", nargs="+", choices=sorted(BY_NAME), default=list(ROUTINE_RESYNC)
    )
    recreate_parser = sub.add_parser(
        "recreate", help="Schema change only: delete + create + regrant"
    )
    recreate_parser.add_argument("--table", required=True, choices=sorted(BY_NAME))
    args = parser.parse_args(argv)
    if args.action == "create":
        result = create()
    elif args.action == "resync":
        result = resync(args.tables)
    else:
        result = recreate(args.table)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
