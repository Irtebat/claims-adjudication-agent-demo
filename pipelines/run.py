"""Configuration-driven CLI entry point. Always passes an explicit profile."""

import argparse
import json
import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent


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
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    db = config["databricks"]
    profile, catalog = db["profile"], db["catalog"]
    if not catalog or "`" in catalog or "/" in catalog:
        raise ValueError("Invalid catalog")
    env = dict(
        os.environ,
        BUNDLE_VAR_catalog=catalog,
        BUNDLE_VAR_claim_count=str(args.claim_count or config["synthetic"]["claim_count"]),
        BUNDLE_VAR_seed=str(config["synthetic"]["seed"]),
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
        content = (ROOT / "governance.sql").read_text().replace("${catalog}", catalog)
        content = "\n".join(
            line for line in content.splitlines() if not line.lstrip().startswith("--")
        )
        for statement in content.split(";"):
            if statement.strip():
                if args.metadata_only and not statement.strip().startswith(("CREATE", "GRANT USE")):
                    continue
                print(statement.strip(), flush=True)
                print(sql(statement.strip()), flush=True)
    else:
        from evidence import capture

        capture(sql, catalog, ROOT.parent / "docs/evidence/synthetic-data")


if __name__ == "__main__":
    main()
