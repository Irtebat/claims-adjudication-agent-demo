"""Lakebase lifecycle entry point. All commands use an explicit profile."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent


def command(*parts, cwd=ROOT, capture=False):
    return subprocess.run(
        [*parts], cwd=cwd, check=True, text=True, capture_output=capture
    )


def databricks(*parts, cwd=ROOT, capture=False):
    return command(
        "databricks", *parts, "--profile", "fe-bar", cwd=cwd, capture=capture
    )


def pipeline_databricks_config():
    return yaml.safe_load((REPO / "pipelines/settings.yaml").read_text())["databricks"]


def cdf_configs(database):
    return json.loads(
        databricks(
            "postgres",
            "list-cdf-configs",
            database,
            "--output",
            "json",
            capture=True,
        ).stdout
    )


def synced_tables(*args):
    command(
        "uv",
        "run",
        "--with",
        "psycopg[binary]==3.2.10",
        "--with",
        "databricks-sdk>=0.81.0",
        "python",
        "scripts/synced_tables.py",
        *args,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "setup-and-seed",
            "policy-intake",
            "create-cdf",
            "synced-tables",
            "resync-synced-tables",
            "recreate-synced-table",
        ],
    )
    parser.add_argument(
        "--tables",
        nargs="+",
        help="resync-synced-tables: tables to re-sync (default: the routine set)",
    )
    parser.add_argument("--table", help="recreate-synced-table: the table to recreate")
    args = parser.parse_args()

    if args.action == "setup-and-seed":
        database = pipeline_databricks_config()["lakebase_database"]
        if cdf_configs(database):
            raise RuntimeError(
                "Refusing to reseed Lakebase because fixture delete/upsert operations "
                "would create spurious SCD2 versions."
            )
        databricks("bundle", "deploy", "-t", "prod")
        databricks("bundle", "run", "setup_and_seed", "-t", "prod")
        command(
            "uv",
            "run",
            "--with",
            "psycopg[binary]==3.2.10",
            "--with",
            "databricks-sdk>=0.81.0",
            "python",
            "src/policy_intake.py",
            "--profile",
            "fe-bar",
        )
    elif args.action == "policy-intake":
        command(
            "uv",
            "run",
            "--with",
            "psycopg[binary]==3.2.10",
            "--with",
            "databricks-sdk>=0.81.0",
            "python",
            "src/policy_intake.py",
            "--profile",
            "fe-bar",
        )
    elif args.action == "synced-tables":
        # Create path: creates missing tables; re-grants only if it created any.
        synced_tables("create")
    elif args.action == "resync-synced-tables":
        # Routine path: incremental triggered sync of EXISTING tables; never re-grants.
        synced_tables("resync", *(["--tables", *args.tables] if args.tables else []))
    elif args.action == "recreate-synced-table":
        # Schema-change path only: delete + create + indexes + re-grant.
        if not args.table:
            parser.error("recreate-synced-table requires --table")
        synced_tables("recreate", "--table", args.table)
    else:
        db = pipeline_databricks_config()
        catalog, schema, database = (
            db["catalog"],
            db["cdf_schema"],
            db["lakebase_database"],
        )
        current = cdf_configs(database)
        if current:
            raise RuntimeError("A CDF config already exists; refusing to recreate it")
        warehouses = json.loads(
            databricks("warehouses", "list", "--output", "json", capture=True).stdout
        )
        warehouse = next(
            w["id"]
            for w in warehouses
            if w["name"] == db["warehouse_name"] and w["enable_serverless_compute"]
        )
        databricks(
            "experimental",
            "aitools",
            "tools",
            "query",
            f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}`",
            "--warehouse",
            warehouse,
        )
        result = databricks(
            "postgres",
            "create-cdf-config",
            database,
            catalog,
            schema,
            "public",
            "--output",
            "json",
            capture=True,
        )
        created = json.loads(result.stdout)
        statuses = None
        for _ in range(40):
            response = databricks(
                "postgres",
                "list-cdf-statuses",
                created["name"],
                "--output",
                "json",
                capture=True,
            )
            statuses = json.loads(response.stdout)
            rows = (
                statuses if isinstance(statuses, list) else statuses.get("statuses", [])
            )
            relevant = [
                row
                for row in rows
                if row.get("postgres_table") in {"claims", "adjudications"}
                or row.get("table_name") in {"claims", "adjudications"}
            ]
            states = {
                str(row.get("status") or row.get("state", ""))
                .upper()
                .removeprefix("CDF_STATE_")
                for row in relevant
            }
            if len(relevant) == 2 and states == {"STREAMING"}:
                break
            time.sleep(15)
        else:
            raise RuntimeError(f"Lakebase CDF did not become ready: {statuses}")
        print(result.stdout)


if __name__ == "__main__":
    main()
