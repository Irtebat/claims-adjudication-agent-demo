# Ablation live run — 2026-10-02

The deterministic baseline defect is fixed, but the final held-out retry failed during isolated `agent@prod` invocation. Per the stop-on-repeat instruction, history was not started and no paired result is claimed.

## Root cause and fix

Loading `agent@prod` prepended its temporary model artifact to `sys.path`. The baseline subsequently imported the artifact's older `decision_record.py`, which did not contain `deterministic_recommendation`; all 100 rows were therefore recorded as failures. The deterministic adapter now captures repository-local authority functions before model loading. Reproducing the same model-load sequence now returns a valid baseline prediction.

Per-row errors and predictions are included in report rows, the first five consecutive failures abort the candidate, and the active MLflow run receives `ablation-errors.json`. Amount, duplicate, PEND-routing, and per-dimension applicable counts were added to the comparison report. Held-out expectations now include approved amount.

## Verification

- `uv run --project eval pytest -q eval/tests`: 70 passed.
- `uv run --project eval ruff check eval/src eval/tests`: passed.
- `uv run --project eval ruff format --check eval/src eval/tests`: passed.
- `PYTHONPATH=agent/src uv run --project eval pytest -q agent/tests`: 175 passed, 3 skipped.

## Final held-out attempt

Command:

```text
uv run --project eval python eval/src/ablation.py --dataset heldout --n 100 --profile fe-bar --candidates deterministic_baseline,agent@prod
```

Log: `.live-ablation-logs/08-heldout-comparison.log` (local and uncommitted).

- Ablation ID: `848fae67-5628-4e34-8bd2-3303f8c9e0c7`
- Baseline run: `c42dca0a6d0e4805acb6eecc9300dc30` (`FINISHED`, zero errors)
- Agent run: `eee45d7a5ae844628f2dd15f37434d30` (`FAILED`)
- Comparison run: none

Exact terminal error:

```text
CandidateBatchFailure: agent@prod failed its first 5 rows: ['EOFError: ', 'EOFError: ', 'EOFError: ', 'EOFError: ', 'EOFError: ']
```

The baseline scored verdict 0.25 (N=100), disposition class 0.25 (N=100), approval sub-choice 1.0 (N=25), amount 0.18 (N=100), duplicate 1.0 (N=100), and PEND routing 1.0 (N=100), with zero errors and mean latency 1335.98 ms/claim. Citation is N/A for the deterministic baseline.

The failed agent run emitted no aggregate metrics. Consequently no truthful disagreement counts, McNemar counts, agent costs/latencies, examples, or complete paired sample can be supplied.

## Safety

All calls used profile `fe-bar` and `persist=false`. No Lakebase public-table writes occurred. `authorities.py` and money logic were not modified.
