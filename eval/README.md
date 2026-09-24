# eval

Planned layer — not yet implemented. This directory will hold the MLflow
evaluation harness, scorers, and the CI threshold gate that measure the agent's
recommendations against ground truth.

## Intended purpose

- Compare the agent's recommended verdict, disposition, and amounts against the
  human-final adjudications in `gold.adjudications_history` (the ground truth).
- Score retrieval quality using the deterministic resolver's correct
  spec/warranty binding and the clauses a human actually cited.
- Gate CI on evaluation thresholds so a regression blocks a change.

## Intended inputs

- Labeled history from gold, and agent outputs/traces.

The synthetic label patterns and SCD2 history exist to support this, but no
evaluation code, dataset, scorers, or CI gate are implemented here yet.
