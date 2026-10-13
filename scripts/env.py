#!/usr/bin/env python3
"""Inspect and validate deployment environment manifests (no workspace calls)."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path

import yaml
from envlib import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ROOT / "config" / "envs"


def manifest_path(name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ManifestError(f"unsafe environment name: {name!r}")
    path = ENVIRONMENTS / f"{name}.yaml"
    if not path.is_file():
        raise ManifestError(f"environment manifest not found: {path}")
    return path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for command in ("validate", "show", "plan"):
        child = commands.add_parser(command)
        child.add_argument("--env", required=True)
    variables = commands.add_parser("vars")
    variables.add_argument("--env", required=True)
    variables.add_argument(
        "--bundle",
        required=True,
        choices=("pipelines", "lakebase", "agent", "services", "demo", "dashboards", "app"),
    )
    return result


def print_plan(environment: str, release: bool) -> None:
    gate = "enabled" if release else "disabled until workspace.release_environment=true"
    print(f"Environment: {environment} (release gate: {gate})")
    steps = (
        ("HUMAN", "Create the AWS bucket, IAM role, trust policy, and record UC identifiers"),
        ("ADMIN", "Create storage credential, external location, catalog, schemas, and grants"),
        ("AUTO", "Deploy pipelines and seed the medallion data plane"),
        ("AUTO", "Provision/configure Lakebase and its UC mirror"),
        ("HUMAN", "Enable Lakebase Search in the workspace UI"),
        ("AUTO", "Deploy agent support jobs and governed model services"),
        ("HUMAN", "Enter Kafka secret values in the declared secret scope"),
        ("GATED", "Register -> evaluate -> promote -> deploy the agent model"),
        ("AUTO", "Deploy event services and demo job"),
        ("AUTO", "Deploy dashboard and create/update both Genie spaces"),
        ("AUTO", "Deploy the application and bind discovered resource IDs"),
        ("HUMAN", "Add the deployed app domain to dashboard embedding allowed domains"),
        ("ADMIN", "Assign application users/groups and verify least-privilege grants"),
    )
    for index, (tag, text) in enumerate(steps, 1):
        print(f"{index:2}. [{tag}] {text}")


def main() -> int:
    args = parser().parse_args()
    try:
        manifest = load_manifest(manifest_path(args.env))
        if args.command == "validate":
            print(f"valid: {args.env}")
        elif args.command == "show":
            print(yaml.safe_dump(manifest.resolved(), sort_keys=False).rstrip())
        elif args.command == "vars":
            values = manifest.to_bundle_vars(args.bundle)
            print(" ".join(shlex.quote(f"--var={key}={value}") for key, value in values.items()))
        elif args.command == "plan":
            print_plan(args.env, manifest.workspace.release_environment)
    except (ManifestError, OSError, yaml.YAMLError, json.JSONDecodeError) as exc:
        parser().error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
