"""Compose the guarded one-time pipelines/Lakebase bootstrap in dependency order.

Two execution mechanisms only (see the top-level README's "Execution model"):

* **DABs bundle** for compute — plain deploys and job runs are issued directly as
  `databricks bundle deploy|run`, not shelled through a per-layer `run.py` shim.
* **Direct `uv run python`** only for genuine orchestration a plain `bundle run`
  cannot express: `pipelines/run.py generate` (injects the authored warranty
  schedule), `lakebase/run.py setup-and-seed` and `create-cdf` (the reseed and
  CDF-exists guards plus the non-bundle `policy_intake.py` step), and
  `pipelines/run.py refresh` (resolves the dynamic native-CDF table names).

Fresh-workspace ordering: the gold fact joins `gold.customer_heat_risk`, which the
fraud-graph job builds from the medallion's own `gold.claims_current`. The first
`fraud_graph` run therefore happens BEFORE the first medallion run: with no
`gold.claims_current` yet it publishes an empty, typed risk table and exits, so the
first medallion run succeeds. Then fraud_graph scores for real, the precedent corpus
is built, and a final medallion run picks up the risk scores. Deterministic, no
retry-until-green.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ("uv", "run", "--with", "pyyaml", "python")


def run(*parts, cwd=ROOT):
    subprocess.run(parts, cwd=cwd, check=True)


def bundle(*parts, cwd):
    run(
        "databricks",
        "bundle",
        *parts,
        "--target",
        "prod",
        "--profile",
        "fe-bar",
        cwd=cwd,
    )


def main():
    # Plain compute deploy: no added logic, so invoke DABs directly (not a shim).
    bundle("deploy", cwd=ROOT / "pipelines")
    # Each wrapper step adds real orchestration/guards.
    run(*WRAPPER, "pipelines/run.py", "generate")
    run(*WRAPPER, "lakebase/run.py", "setup-and-seed")
    run(*WRAPPER, "lakebase/run.py", "create-cdf")
    bundle("deploy", cwd=ROOT / "agent")
    # No gold.claims_current yet -> publishes the empty typed gold.customer_heat_risk.
    bundle("run", "fraud_graph", cwd=ROOT / "agent")
    run(*WRAPPER, "pipelines/run.py", "refresh")
    # Now score for real and build the precedent corpus from FINAL adjudications.
    bundle("run", "fraud_graph", cwd=ROOT / "agent")
    bundle("run", "prior_claims_corpus", cwd=ROOT / "agent")
    # Final medallion run so the gold fact carries the risk scores.
    run(*WRAPPER, "pipelines/run.py", "refresh")


if __name__ == "__main__":
    main()
