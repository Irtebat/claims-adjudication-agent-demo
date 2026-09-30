"""Compose the routine and demo refreshes across layers, in dependency order.

Two execution mechanisms only (see the top-level README's "Execution model"): plain
`databricks bundle deploy|run` for compute, issued directly; `uv run python` wrappers
only where a layer adds orchestration a plain `bundle run` cannot express
(`pipelines/run.py refresh` resolves the dynamic native-CDF table names;
`lakebase/run.py resync-synced-tables` resolves each synced table's sync pipeline).

routine — safe to run at any time; incremental end to end, no full refresh:

1. `pipelines/run.py refresh`: incremental medallion run fed EVERY native-CDF table
   name (claims, adjudications, decision records), so new Lakebase DML lands in
   silver/gold SCD2 and gold decision records.
2. `bundle run fraud_graph` (agent/): rescore `gold.customer_heat_risk`.
3. `bundle run prior_claims_corpus` (agent/): rebuild `gold.prior_claims_corpus`
   from the refreshed FINAL adjudications (change-only MERGE, embeds only new text).
4. `lakebase/run.py resync-synced-tables`: triggered re-sync of the EXISTING
   `reference.customer_heat_risk` and `reference.prior_claims_corpus` synced tables
   (plus corpus index check and VACUUM). A re-sync keeps the tables and their grants,
   so no re-grant runs here — re-grant belongs only to the create/recreate paths.
5. `pipelines/run.py refresh` again, so the gold fact picks up the new risk scores.

demo — `bundle run demo_backlog` (demo/: new synthetic claims + RECOMMENDED agent
adjudications in Lakebase), then the routine refresh.

Run: `uv run --with pyyaml python scripts/refresh.py routine|demo`
"""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ("--profile", "fe-bar")
WRAPPER = ("uv", "run", "--with", "pyyaml", "python")


def bundle(*parts):
    return ("databricks", "bundle", *parts, "--target", "prod", *PROFILE)


def routine_steps():
    medallion = ((*WRAPPER, "pipelines/run.py", "refresh"), ROOT)
    return [
        medallion,
        (bundle("deploy"), ROOT / "agent"),
        (bundle("run", "fraud_graph"), ROOT / "agent"),
        (bundle("run", "prior_claims_corpus"), ROOT / "agent"),
        ((*WRAPPER, "lakebase/run.py", "resync-synced-tables"), ROOT),
        medallion,
    ]


def demo_steps():
    return [
        (bundle("deploy"), ROOT / "demo"),
        (bundle("run", "demo_backlog"), ROOT / "demo"),
        *routine_steps(),
    ]


def run(parts, cwd):
    subprocess.run(parts, cwd=cwd, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Routine or demo refresh")
    parser.add_argument("mode", choices=["routine", "demo"])
    args = parser.parse_args(argv)
    for parts, cwd in routine_steps() if args.mode == "routine" else demo_steps():
        print("+", " ".join(parts), flush=True)
        run(parts, cwd)


if __name__ == "__main__":
    main()
