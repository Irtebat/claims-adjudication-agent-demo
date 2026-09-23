"""Configuration-driven CLI entry point. Always passes an explicit profile."""

import argparse
import json
import os
import re
import subprocess
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
        choices=["validate", "deploy", "run", "summary", "check-generator", "govern", "evidence"],
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
            for v in policy_source["warranty_versions"]
        ]
    )
    env = dict(
        os.environ,
        BUNDLE_VAR_catalog=catalog,
        BUNDLE_VAR_claim_count=str(args.claim_count or config["synthetic"]["claim_count"]),
        BUNDLE_VAR_seed=str(config["synthetic"]["seed"]),
        BUNDLE_VAR_warranty_schedule=warranty_schedule,
    )

    def cli(*parts):
        result = subprocess.run(
            ["databricks", *parts, "--profile", profile],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout

    if args.action in {"validate", "deploy", "run", "summary", "check-generator"}:
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
        if args.action == "run":
            parts.append("bootstrap")
        result = subprocess.run(
            ["databricks", *parts, "--profile", profile], cwd=ROOT, env=env, check=False
        )
        raise SystemExit(result.returncode)

    warehouses = json.loads(cli("warehouses", "list", "--output", "json"))
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
        )

    if args.action == "govern":
        # UC roles are account groups, not workspace-local SCIM groups. Do not
        # grant existing broad groups access or automatically enroll any users.
        for name in ("adjuster", "metallurgy_analyst"):
            endpoint = (
                "/api/2.0/account/scim/v2/Groups?filter=displayName%20eq%20%22" + name + "%22"
            )
            groups = json.loads(cli("api", "get", endpoint))
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
                    )
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
