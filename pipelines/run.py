"""Configuration-driven CLI entry point. Always passes an explicit profile."""

import argparse
import csv
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DECISION_RECORDS = "adjudication_decision_records"

# A service-principal application id, group name, or user email — deliberately excludes
# backticks, quotes, semicolons and whitespace/newlines so a principal can never break
# out of the backticked identifier it is substituted into.
_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]*$")


def _job_params(**values):
    """Encode Databricks job parameter overrides as one CSV argument."""
    output = io.StringIO()
    csv.writer(output, lineterminator="").writerow(
        f"{name}={value}" for name, value in values.items() if value is not None
    )
    return output.getvalue()


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
        # Only genuinely-non-bundle orchestration lives here. Pure `databricks bundle`
        # passthroughs (validate/deploy/summary) were collapsed — run them directly as
        # `databricks bundle <validate|deploy|summary> --target prod --profile fe-bar`
        # (see pipelines/README.md). What remains adds real logic a plain `bundle run`
        # cannot express: warranty-schedule sourcing (generate/check-generator),
        # dynamic native-CDF table resolution (refresh/decision-records), the preview
        # probe, account-group/governance rendering, and SQL evidence capture.
        choices=[
            "generate",
            "refresh",
            "decision-records",
            "preview-status",
            "check-generator",
            "govern",
            "evidence",
        ],
    )
    parser.add_argument("--config", type=Path, default=ROOT / "settings.yaml")
    parser.add_argument(
        "--allow-missing-decision-records",
        action="store_true",
        help=(
            "refresh: proceed when the decision-record CDF landing table does not exist "
            "yet (a fresh workspace before its first decision record). Without this flag "
            "a missing table is an error."
        ),
    )
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
    policy_source = json.loads((ROOT.parent / "lakebase/src/policy_source.json").read_text())
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
    bundle_var_env = dict(
        os.environ,
        BUNDLE_VAR_catalog=catalog,
        BUNDLE_VAR_claim_count=str(args.claim_count or config["synthetic"]["claim_count"]),
        BUNDLE_VAR_seed=str(config["synthetic"]["seed"]),
        BUNDLE_VAR_warranty_schedule=warranty_schedule,
    )

    def cli(*parts, cwd=ROOT, allow_failure=False, env=None):
        result = subprocess.run(
            ["databricks", *parts, "--profile", profile],
            cwd=cwd,
            env=os.environ if env is None else env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode and not allow_failure:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result

    if args.action in {"generate", "check-generator"}:
        # These are `bundle run`s that stay wrapped ONLY because they pass
        # warranty_schedule (sourced from policy_source.json above) as an explicit
        # run-time job-parameter override, so the generator applies the authored
        # warranty schedule and no policy numerics are hardcoded in the bundle. A
        # plain `databricks bundle run` would use the bundle's empty-list default.
        job = "generate_raw" if args.action == "generate" else "validate_generator"
        params = _job_params(
            catalog=catalog,
            claim_count=str(args.claim_count or config["synthetic"]["claim_count"]),
            seed=str(config["synthetic"]["seed"]),
            warranty_schedule=warranty_schedule,
        )
        result = subprocess.run(
            [
                "databricks",
                "bundle",
                "run",
                job,
                "--params",
                params,
                "--target",
                "prod",
                "--profile",
                profile,
            ],
            cwd=ROOT,
            env=os.environ,
            check=False,
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
        result = cli_json("postgres", "list-cdf-configs", database, allow_failure=True)
        return {"enabled": result is not None, "cdf_configs": result}

    def cdf_tables(cdf_schema):
        response = cli_json("tables", "list", catalog, cdf_schema)
        return response if isinstance(response, list) else response.get("tables", [])

    def cdf_table(tables, source, required=True):
        """Resolve the (possibly hash-suffixed) native-CDF landing table for ``source``."""
        prefix = f"lb_{source}_history"
        matches = [
            row.get("full_name") or f"{catalog}.{db['cdf_schema']}.{row['name']}"
            for row in tables
            if row.get("name", "").startswith(prefix)
        ]
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise RuntimeError(f"Expected one CDF table for {source}, found {matches}")
        return matches[0]

    if args.action == "preview-status":
        status = preview_status()
        print(json.dumps(status, sort_keys=True))
        raise SystemExit(0 if status["enabled"] else 2)

    if args.action == "refresh":
        status = preview_status()
        if not status["enabled"]:
            raise RuntimeError(
                "Lakebase Change Data Feed preview is not enabled; a workspace admin "
                "must enable it in the workspace UI"
            )

        cdf_schema = db["cdf_schema"]
        existing = status["cdf_configs"]
        if not isinstance(existing, list) or len(existing) != 1:
            raise RuntimeError(
                f"Refresh requires exactly one existing CDF config; found {existing}"
            )

        created = existing[0]
        tables = cdf_tables(cdf_schema)
        # A normal triggered/incremental medallion run: feed the pipeline the current
        # native-CDF table names for EVERY CDF-fed dataset and run refresh_medallion.
        # Ordinary DML flows through CDF incrementally, so this neither reloads
        # everything nor re-snapshots, and it issues no DDL. The decision-record table
        # is passed too (earlier this path passed only claims/adjudications and left
        # the decision-record flow on the bundle-default name). A missing
        # decision-record table is an error unless the operator explicitly allows it
        # (--allow-missing-decision-records, used by scripts/bootstrap.py): that is only
        # legitimate before the first decision record materializes the CDF landing
        # table, and the pipeline then publishes an empty decision_records_for_fact.
        bundle_var_env["BUNDLE_VAR_cdf_claims_table"] = cdf_table(tables, "claims")
        bundle_var_env["BUNDLE_VAR_cdf_adjudications_table"] = cdf_table(tables, "adjudications")
        decision_records = cdf_table(tables, DECISION_RECORDS, required=False)
        if decision_records:
            bundle_var_env["BUNDLE_VAR_cdf_decision_records_table"] = decision_records
        elif not args.allow_missing_decision_records:
            raise RuntimeError(
                f"No CDF landing table lb_{DECISION_RECORDS}_history* in "
                f"{catalog}.{cdf_schema}. If no decision record has been written yet "
                "(fresh workspace), rerun with --allow-missing-decision-records; "
                "otherwise check the Lakebase CDF status for adjudication_decision_records."
            )
        else:
            print(
                f"WARNING: {DECISION_RECORDS} CDF table not found; proceeding without it "
                "(--allow-missing-decision-records)",
                flush=True,
            )
        # These values configure the deployed pipeline, so bundle variables belong on
        # deploy. They are intentionally not passed to the already-deployed job run.
        cli("bundle", "deploy", "--target", "prod", env=bundle_var_env)
        cli("bundle", "run", "refresh_medallion", "--target", "prod")
        print(
            json.dumps(
                {
                    "cdf_config": created,
                    "claims_table": bundle_var_env["BUNDLE_VAR_cdf_claims_table"],
                    "adjudications_table": bundle_var_env["BUNDLE_VAR_cdf_adjudications_table"],
                    "decision_records_table": decision_records,
                },
                sort_keys=True,
            )
        )
        raise SystemExit(0)

    if args.action == "decision-records":
        # Additive path: the append-only decision-record table is created by the
        # lakebase migration and picked up by the EXISTING schema-scoped native CDF
        # config. This verifies it reached STREAMING, then deploys and runs the
        # pipeline flow that lands it in UC gold. It never reseeds or re-creates CDF.
        status = preview_status()
        if not status["enabled"] or not status["cdf_configs"]:
            raise RuntimeError("No existing Lakebase CDF config; run the base flow first.")
        cdf_schema = db["cdf_schema"]
        cdf_config_name = status["cdf_configs"][0]["name"]
        source = DECISION_RECORDS
        statuses = None
        for _ in range(40):
            statuses = cli_json("postgres", "list-cdf-statuses", cdf_config_name)
            status_rows = statuses if isinstance(statuses, list) else statuses.get("statuses", [])
            row = next(
                (
                    r
                    for r in status_rows
                    if r.get("postgres_table") == source or r.get("table_name") == source
                ),
                None,
            )
            state = (
                str(row.get("status") or row.get("state", "")).upper().removeprefix("CDF_STATE_")
                if row
                else ""
            )
            if state == "STREAMING":
                break
            time.sleep(15)
        else:
            raise RuntimeError(
                f"{source} did not reach CDF STREAMING; last status: {statuses}. "
                "Confirm the lakebase migration created it with REPLICA IDENTITY FULL."
            )

        tables = cdf_tables(cdf_schema)
        bundle_var_env["BUNDLE_VAR_cdf_claims_table"] = cdf_table(tables, "claims")
        bundle_var_env["BUNDLE_VAR_cdf_adjudications_table"] = cdf_table(tables, "adjudications")
        bundle_var_env["BUNDLE_VAR_cdf_decision_records_table"] = cdf_table(tables, source)
        cli("bundle", "deploy", "--target", "prod", env=bundle_var_env)
        cli("bundle", "run", "refresh_medallion", "--target", "prod")
        print(
            json.dumps(
                {
                    "cdf_config": cdf_config_name,
                    "decision_records_state": "STREAMING",
                    "decision_records_table": bundle_var_env[
                        "BUNDLE_VAR_cdf_decision_records_table"
                    ],
                    "gold_table": f"{catalog}.gold.adjudication_decision_records",
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
