"""Compose the guarded one-time pipelines/Lakebase bootstrap in dependency order.

Two execution mechanisms only (see the top-level README's "Execution model"):

* **DABs bundle** for compute — the plain pipelines deploy is issued directly as
  `databricks bundle deploy`, not shelled through a per-layer `run.py` shim.
* **Direct `uv run python`** only for genuine orchestration a plain `bundle run`
  cannot express: `pipelines/run.py generate` (injects the authored warranty
  schedule), `lakebase/run.py setup-and-seed` and `create-cdf` (the reseed and
  CDF-exists guards plus the non-bundle `policy_intake.py` step), and
  `pipelines/run.py refresh` (resolves the dynamic native-CDF table names).
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*parts, cwd=ROOT):
    subprocess.run(parts, cwd=cwd, check=True)


def main():
    # Plain compute deploy: no added logic, so invoke DABs directly (not a shim).
    run(
        "databricks",
        "bundle",
        "deploy",
        "--target",
        "prod",
        "--profile",
        "fe-bar",
        cwd=ROOT / "pipelines",
    )
    # Each remaining step adds real orchestration/guards, so it stays a wrapper.
    run("uv", "run", "--with", "pyyaml", "python", "pipelines/run.py", "generate")
    run("uv", "run", "--with", "pyyaml", "python", "lakebase/run.py", "setup-and-seed")
    run("uv", "run", "--with", "pyyaml", "python", "lakebase/run.py", "create-cdf")
    run("uv", "run", "--with", "pyyaml", "python", "pipelines/run.py", "refresh")


if __name__ == "__main__":
    main()
