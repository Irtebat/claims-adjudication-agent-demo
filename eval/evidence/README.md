# Convenience artifacts — NOT the record of truth

Every file in this directory is a **regenerated convenience snapshot** written by
`eval/src/evaluate.py` for local review and PR evidence. These files are
overwritten on each run and are **not** the authoritative record of an evaluation.

**The authoritative system of record is the MLflow run** and the metrics logged to
it in the experiment (`/Shared/claims-adjudication-offline-evaluation` by default).
Each run carries the release-gate metric means (`<scorer>/mean`) plus the
`candidate_version`, `git_sha`, and `release_gate_passed` tags. The
`run-manifest.json` here records `record_of_truth: "mlflow"`, `convenience_artifact:
true`, and an `mlflow_run_link` back to that run.

Promotion decisions (`eval/src/promote.py`) read release-gate metrics from the
MLflow runs — never from these JSON files. If a snapshot here ever disagrees with
the MLflow run, trust the run.
