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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "deploy",
            "setup-and-seed",
            "policy-intake",
            "create-cdf",
            "synced-tables",
        ],
    )
    args = parser.parse_args()

    if args.action == "validate":
        databricks("bundle", "validate", "--strict", "-t", "prod")
    elif args.action == "deploy":
        databricks("bundle", "deploy", "-t", "prod")
    elif args.action == "setup-and-seed":
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
        command("./scripts/create_synced_tables.sh")
    else:
        config = yaml.safe_load((REPO / "pipelines/settings.yaml").read_text())
        db = config["databricks"]
        catalog, schema, database = (
            db["catalog"],
            db["cdf_schema"],
            db["lakebase_database"],
        )
        current = json.loads(
            databricks(
                "postgres",
                "list-cdf-configs",
                database,
                "--output",
                "json",
                capture=True,
            ).stdout
        )
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
