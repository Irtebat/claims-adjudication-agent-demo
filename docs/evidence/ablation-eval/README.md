# Ablation live results — 2026-10-02

Both 100-claim comparisons completed with zero errors and `persist=false`. This is a null ablation: `agent@prod` made exactly the same verdict, disposition, and amount decisions as the deterministic baseline on all 200 claims.

## Runs

| Dataset | Baseline | Agent | Comparison |
|---|---|---|---|
| Held-out | `b61e6218ee1c49e48aa46d3f6c81c03a` | `6f8508fad03e4b2daf9175d1d974a849` | `ac3b4c6c38e84dba9de3385c0327c598` |
| History | `c7199561fdb1498c91fcb19c6c832f12` | `18bb010b9b1e408694959c8d5a396a01` | `58d567923c834414a3e272574b2c480b` |

## Metrics

| Dataset/metric | Baseline | Agent | Applicable N | Delta |
|---|---:|---:|---:|---:|
| Held-out verdict | .25 | .25 | 100 | 0 |
| Held-out disposition class | .25 | .25 | 100 | 0 |
| Held-out approval sub-choice | 1.00 | 1.00 | 25 | 0 |
| Held-out amount | .18 | .18 | 100 | 0 |
| Held-out duplicate / PEND | 1.00 / 1.00 | 1.00 / 1.00 | 100 | 0 |
| History verdict / disposition | .95 / .95 | .95 / .95 | 100 | 0 |
| History approval sub-choice | 1.00 | 1.00 | 50 | 0 |
| History amount / duplicate | 1.00 / 1.00 | 1.00 / 1.00 | 100 | 0 |
| History PEND routing | .95 | .95 | 100 | 0 |

Agent citation was 1.00 (N=100) in both runs; baseline citation is N/A. Agent invariant-correction rate was 0.00 held-out and .01 history; baseline is N/A. Both arms had zero errors.

Held-out latency: baseline 1,334.61 ms/claim, agent 35,203.35 ms/claim; agent tokens 4,715.25/claim. History latency: baseline 1,274.38 ms/claim, agent 36,465.15 ms/claim; agent tokens 5,023.22/claim. Monetary cost was not emitted.

Held-out McNemar cells (both-correct/baseline-only/agent-only/both-wrong): verdict and disposition 25/0/0/75; approval 25/0/0/0; amount 18/0/0/82; duplicate and PEND 100/0/0/0. History: verdict, disposition, PEND 95/0/0/5; approval 50/0/0/0; amount and duplicate 100/0/0/0. There are zero discordant pairs and therefore no significant difference.

## Disagreements

There were **zero candidate-output disagreements** in either run, so two disagreement examples do not exist. On held-out treatments both candidates were wrong together; on controls both agreed with gold. The agent adds grounded citations and rationale but does not adjudicate narrative-only exclusions.

## Complete held-out treatment example

Input: claim `c1956233-b628-5bf4-8310-9c7f161b5bf4`; coil `COIL-0003003`; customer `CUST-0028`; type `coating_warranty`; claim/install dates `2026-01-31` / `2018-01-31`; environment `inland`; installation `ventilated`; coast distance `10.0`; defect `RED_RUST`; narrative “The panels face open sea and receive airborne salt during onshore winds.”; tonnage `10.000`; freight `0.00`.

Hidden labels: treatment `narrative_excluded_environment`; `DENY/DENY`; amount `0.00`; fact `marine`; clause `/warranties/coverage/excluded_environments` (`warranties.coverage.excluded_environments`).

Baseline: `APPROVE/CREDIT`, amount `7930.0`, no citations; `R7_credit` fired. Verdict, disposition, and amount scorers: false; citation N/A.

Agent: `APPROVE/CREDIT`, amount `7930.0`; citations `galvanized/NA/V2/coverage`, `galvanized/NA/V2/exclusions`, `galvanized/NA/V2/proration`; no invariant correction. Its rationale says authoritative coverage controls and no exclusion triggered. Verdict, disposition, and amount scorers: false; citation true.

## Read and caveats

Rules are sufficient for history and controls and roughly 27x faster. AI adds explanation and citations but no quality lift. The held-out set shows that both arms ignore narrative-only exclusion facts. Amount exact-match was only 18/25 on controls, but that does not alter the treatment conclusion. This covers one registered version and two stratified 100-claim samples.

The baseline import bug was fixed with private file-path module loading. Model, endpoint, and deterministic candidates now run in one long-lived spawned process; callable adapters run in-process. A 120-second timeout terminates and joins an isolated worker before restart, and exception classes and tracebacks cross the pipe. The published inference numbers came from commit `0672a31`, whose adapters ran in-process; only the amount metric was rescored offline from those stored artifacts after cent quantization. That rescoring left held-out amount at .18 and history at 1.00 for both arms. Current verification: eval `76 passed`; agent `175 passed, 3 skipped`; Ruff passed. No Lakebase public writes occurred; `authorities.py` was untouched.

The realistic mixed held-out rerun is recorded in `mixed-heldout-live-2026-10-02.md` and `.json`. It uses 70 normal claims across R1/R4/R5/R6/R7 plus fraud-ring PEND, and 30 narrative-only exclusions. Both candidates scored 0/30 on narrative decisions. The baseline was correct on all supported structured paths; the registered agent used CREDIT for all 20 R5/R6 rows instead of REPLACEMENT/REWORK. Runs: baseline `5d40e1f8667d4234991ad21089c6656c`, agent `7fef9952af8641399e11a5d19af8aa97`, comparison `af37991c2dbe4b2c91b9946a1bfc054c`.
