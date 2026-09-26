"""Compose the guarded one-time pipelines/Lakebase bootstrap in dependency order."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*parts):
    subprocess.run(parts, cwd=ROOT, check=True)


def main():
    run("uv", "run", "--with", "pyyaml", "python", "pipelines/run.py", "deploy")
    run("uv", "run", "--with", "pyyaml", "python", "pipelines/run.py", "generate")
    run("uv", "run", "--with", "pyyaml", "python", "lakebase/run.py", "setup-and-seed")
    run("uv", "run", "--with", "pyyaml", "python", "lakebase/run.py", "create-cdf")
    run("uv", "run", "--with", "pyyaml", "python", "pipelines/run.py", "refresh")


if __name__ == "__main__":
    main()
