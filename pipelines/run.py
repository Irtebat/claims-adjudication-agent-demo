"""Configuration-driven CLI entry point. Always passes an explicit profile."""

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent

# A service-principal application id, group name, or user email — deliberately excludes
# backticks, quotes, semicolons and whitespace/newlines so a principal can never break
# out of the backticked identifier it is substituted into.
_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")


def _validate_principal(value):
    if value is not None and not _PRINCIPAL_RE.match(value):
        raise ValueError(f"unsafe principal identifier: {value!r}")
    return value


def render_governance(sql_text, catalog, app_principal=None, agent_principal=None):
    """Substitute ${catalog} and any supplied principals, then split into statements.

    Returns (executable, skipped): a statement is skipped ONLY when it still contains
    an unresolved ${...} placeholder — i.e. a principal that was genuinely not supplied.
    Supplying a principal substitutes it and the grant becomes executable. Principals are
    validated against a safe identifier pattern before being placed inside backticks.
    """
    _validate_principal(app_principal)
    _validate_principal(agent_principal)
    subs = {"${catalog}": catalog}
    if app_principal:
        subs["${app_principal}"] = app_principal
    if agent_principal:
        subs["${agent_principal}"] = agent_principal
    for key, value in subs.items():
        sql_text = sql_text.replace(key, value)
    body = "\n".join(line for line in sql_text.splitlines() if not line.lstrip().startswith("--"))
    executable, skipped = [], []
    for statement in body.split(";"):
        statement = statement.strip()
        if not statement:
            continue
        (skipped if "${" in statement else executable).append(statement)
    return executable, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=[
            "validate",
            "deploy",
            "run",
            "resume",
            "preview-status",
            "summary",
            "check-generator",
            "govern",
            "evidence",
        ],
    )
    parser.add_argument("--config", type=Path, default=ROOT / "settings.yaml")
    parser.add_argument("--claim-count", type=int)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Apply schema/function/USE grants only; no table grants or masks",
    )
    parser.add_argument(
        "--app-principal", help="App SP application id for the ${app_principal} grants"
    )
    parser.add_argument(
        "--agent-principal", help="Agent SP application id for the ${agent_principal} grants"
    )
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    db = config["databricks"]
    profile, catalog = db["profile"], db["catalog"]
    if not catalog or "`" in catalog or "/" in catalog:
        raise ValueError("Invalid catalog")
    # Source the warranty version schedule from the single authored policy so the
    # generator carries no hardcoded policy numerics (durations, effective windows).
    policy_source = json.loads((ROOT.parent / "agent/src/policy_source.json").read_text())
    warranty_schedule = json.dumps(
        [
            {
                "version": v["version"],
                "effective_from": v["effective_from"],
                "effective_to": v["effective_to"],
                "duration_months": v["duration_months"],
                "full_coverage_months": v["full_coverage_months"],
            }
            for v in policy_source["warranties"]["versions"]
        ]
    )
    env = dict(
        os.environ,
        BUNDLE_VAR_catalog=catalog,
        BUNDLE_VAR_claim_count=str(args.claim_count or config["synthetic"]["claim_count"]),
        BUNDLE_VAR_seed=str(config["synthetic"]["seed"]),
        BUNDLE_VAR_warranty_schedule=warranty_schedule,
    )

    def cli(*parts, cwd=ROOT, allow_failure=False):
        result = subprocess.run(
            ["databricks", *parts, "--profile", profile],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode and not allow_failure:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result

    if args.action in {"validate", "deploy", "summary", "check-generator"}:
        parts = [
            "bundle",
            "run" if args.action == "check-generator" else args.action,
            "--target",
            "prod",
        ]
        if args.action == "validate":
            parts.append("--strict")
        if args.action == "check-generator":
            parts.append("validate_generator")
        result = subprocess.run(
            ["databricks", *parts, "--profile", profile], cwd=ROOT, env=env, check=False
        )
        raise SystemExit(result.returncode)

    def cli_json(*parts, cwd=ROOT, allow_failure=False):
        result = cli(*parts, "--output", "json", cwd=cwd, allow_failure=allow_failure)
        if result.returncode:
            return None
        return json.loads(result.stdout)

    database = db["lakebase_database"]

    def preview_status():
        # This Beta endpoint is feature-gated by the workspace preview. A successful
        # read (including an empty list) verifies enablement without creating state.
        result = cli_json(
            "postgres", "list-cdf-configs", database, allow_failure=True
        )
        return {"enabled": result is not None, "cdf_configs": result}

    if args.action == "preview-status":
        status = preview_status()
        print(json.dumps(status, sort_keys=True))
        raise SystemExit(0 if status["enabled"] else 2)

    if args.action in {"run", "resume"}:
        status = preview_status()
        if not status["enabled"]:
            raise RuntimeError(
                "Lakebase Change Data Feed preview is not enabled; a workspace admin "
                "must enable it in the workspace UI"
            )

        cdf_schema = db["cdf_schema"]
        existing = status["cdf_configs"]
        if args.action == "run" and existing:
            raise RuntimeError(
                "A CDF config already exists. Refusing to reseed Lakebase because fixture "
                "delete/upsert operations would create spurious SCD2 versions."
            )

        if args.action == "run":
            # The baseline is generated and seeded exactly once, before native CDF starts.
            cli("bundle", "deploy", "--target", "prod")
            cli("bundle", "run", "generate_raw", "--target", "prod")
            lakebase_root = ROOT.parent / "lakebase"
            cli("bundle", "deploy", "--target", "prod", cwd=lakebase_root)
            cli("bundle", "run", "setup_and_seed", "--target", "prod", cwd=lakebase_root)
        elif not isinstance(existing, list) or len(existing) != 1:
            raise RuntimeError(
                f"Resume requires exactly one existing CDF config; found {existing}"
            )

        warehouses = cli_json("warehouses", "list")
        warehouse = next(
            item["id"]
            for item in warehouses
            if item["name"] == db["warehouse_name"]
            and item["enable_serverless_compute"]
        )
        if args.action == "run":
            cli(
                "experimental",
                "aitools",
                "tools",
                "query",
                f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{cdf_schema}`",
                "--warehouse",
                warehouse,
            )
            created = cli_json(
                "postgres",
                "create-cdf-config",
                database,
                catalog,
                cdf_schema,
                "public",
            )
        else:
            created = existing[0]
        cdf_config_name = created["name"]

        statuses = None
        for _ in range(40):
            statuses = cli_json("postgres", "list-cdf-statuses", cdf_config_name)
            status_rows = statuses if isinstance(statuses, list) else statuses.get("statuses", [])
            relevant = [
                row
                for row in status_rows
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
            raise RuntimeError(
                "Lakebase CDF did not reach STREAMING for claims and adjudications; "
                f"last status: {statuses}"
            )

        table_response = cli_json("tables", "list", catalog, cdf_schema)
        tables = table_response if isinstance(table_response, list) else table_response.get("tables", [])

        def cdf_table(source):
            prefix = f"lb_{source}_history"
            matches = [
                row.get("full_name") or f"{catalog}.{cdf_schema}.{row['name']}"
                for row in tables
                if row.get("name", "").startswith(prefix)
            ]
            if len(matches) != 1:
                raise RuntimeError(f"Expected one CDF table for {source}, found {matches}")
            return matches[0]

        # Dataset kinds cannot be changed in place. Remove only the six derived,
        # reproducible Parquet-fed MVs that this workstream replaces.
        for schema, table in (
            ("bronze", "claims_history"),
            ("bronze", "adjudications_history"),
            ("silver", "claims_history"),
            ("silver", "adjudications_history"),
            ("gold", "claims_history"),
            ("gold", "adjudications_history"),
        ):
            cli(
                "experimental",
                "aitools",
                "tools",
                "query",
                f"DROP MATERIALIZED VIEW IF EXISTS `{catalog}`.`{schema}`.`{table}`",
                "--warehouse",
                warehouse,
            )

        env["BUNDLE_VAR_cdf_claims_table"] = cdf_table("claims")
        env["BUNDLE_VAR_cdf_adjudications_table"] = cdf_table("adjudications")
        cli("bundle", "deploy", "--target", "prod")
        cli("bundle", "run", "process_cdf", "--target", "prod")
        print(
            json.dumps(
                {
                    "cdf_config": created,
                    "claims_table": env["BUNDLE_VAR_cdf_claims_table"],
                    "adjudications_table": env["BUNDLE_VAR_cdf_adjudications_table"],
                },
                sort_keys=True,
            )
        )
        raise SystemExit(0)

    warehouses = json.loads(cli("warehouses", "list", "--output", "json").stdout)
    warehouse = next(
        w["id"]
        for w in warehouses
        if w["name"] == db["warehouse_name"] and w["enable_serverless_compute"]
    )

    def sql(statement):
        return cli(
            "experimental",
            "aitools",
            "tools",
            "query",
            statement,
            "--warehouse",
            warehouse,
            "--output",
            "json",
        ).stdout

    if args.action == "govern":
        # UC roles are account groups, not workspace-local SCIM groups. Do not
        # grant existing broad groups access or automatically enroll any users.
        for name in ("adjuster", "metallurgy_analyst"):
            endpoint = (
                "/api/2.0/account/scim/v2/Groups?filter=displayName%20eq%20%22" + name + "%22"
            )
            groups = json.loads(cli("api", "get", endpoint).stdout)
            if not groups.get("Resources"):
                print(
                        cli(
                        "api",
                        "post",
                        "/api/2.0/account/scim/v2/Groups",
                        "--json",
                        json.dumps(
                            {
                                "displayName": name,
                                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                            }
                        ),
                        ).stdout
                )
        gov = config.get("governance", {}) or {}
        app_principal = (
            args.app_principal or os.environ.get("APP_PRINCIPAL") or gov.get("app_principal")
        )
        agent_principal = (
            args.agent_principal or os.environ.get("AGENT_PRINCIPAL") or gov.get("agent_principal")
        )
        executable, skipped = render_governance(
            (ROOT / "governance.sql").read_text(), catalog, app_principal, agent_principal
        )
        for statement in skipped:
            # Only reached when a principal was genuinely not supplied; the grant SQL is
            # real and activates as soon as its principal is configured.
            print(f"-- skipped (principal not configured): {statement[:80]}", flush=True)
        for statement in executable:
            if args.metadata_only and not statement.startswith(("CREATE", "GRANT USE")):
                continue
            print(statement, flush=True)
            print(sql(statement), flush=True)
    else:
        from evidence import capture

        capture(sql, catalog, ROOT.parent / "docs/evidence/synthetic-data")


if __name__ == "__main__":
    main()
