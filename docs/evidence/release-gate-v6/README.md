# Evidence — release gate for the current `@prod` agent (v6)

> **Synthetic demo evidence.** MLflow scorer results read live (read-only) from workspace
> `fe-bar-ir-2026`, experiment `/Shared/claims-adjudication-offline-evaluation`
> (id `3711089841553520`), on 2026-10-14. Raw: [`eval-runs.json`](eval-runs.json).

This is the auditable record behind the promotion of
`fe-bar-ir.default.claims_adjudication_agent` **v5 → v6** (`@prod` is v6 today). The gate
logic is `eval/src/promote.py`; thresholds are `RELEASE_THRESHOLDS` in `eval/src/scorers.py`.

## How the gate decides

- **Money-safety invariants** (threshold `1.0`, absolute, strict, checked on candidate *and* `@prod`):
  `no_payable_duplicate`, `amount_matches_authority`, `verdict_matches_eligibility`,
  `citations_in_resolved_policy`. Any value below 1.0, missing, or non-finite fails the gate.
- **Quality** (relative): `verdict_exact_match`, `disposition_exact_match`, `amount_matches_gold`
  must be **not worse than the current `@prod`**.
- Promote only if both pass (`promote.py --promote`).

## Scorer results — exact tier

| Metric | Type | v5 (`@prod` before) | **v6 (candidate)** | Result |
|---|---|---:|---:|---|
| no_payable_duplicate | money-safety ≥ 1.0 | 1.00 | **1.00** | pass |
| amount_matches_authority | money-safety ≥ 1.0 | 1.00 | **1.00** | pass |
| verdict_matches_eligibility | money-safety ≥ 1.0 | 1.00 | **1.00** | pass |
| citations_in_resolved_policy | money-safety ≥ 1.0 | 1.00 | **1.00** | pass |
| verdict_exact_match | quality, not worse | 0.9467 | **0.9467** | not worse |
| disposition_exact_match | quality, not worse | 0.9467 | **0.9467** | not worse |
| amount_matches_gold | quality, not worse | 1.00 | **1.00** | not worse |
| invariant_clean | informational | 1.00 | 0.9733 | some candidate outputs recorded invariant violations; both final-amount metrics remained 1.00 |
| citation_recall | informational | 1.00 | 1.00 | |
| citation_reciprocal_rank | informational | 0.9733 | 0.9733 | |

Runs: v6 exact `8ef313627600444fae6a731545f608db`, v5 exact `988d66f5e6f34ad5b58316fc8e612652`.

**Gate reconstruction:** the captured v5 and v6 metrics satisfy the current `promote.py` gate (money-safety passed on both sides, quality not worse), and the 2026-10-14 capture reports `@prod = version 6`. The capture does not record the historical alias-move command. The live endpoint serves v6 — see [`../live-verification-2026-10-13/serving.md`](../live-verification-2026-10-13/serving.md).

## Other v6 tiers

| Run | Tier | Notes |
|---|---|---|
| `84d2b181…` | pilot | every recorded money-safety metric 1.00; verdict/disposition 1.00 |
| `f8cb1a55…` | LLM judges | `authority_guidelines` 0.47 (diagnostic only); `retrieval_groundedness` 0.0 is a known instrumentation gap — retriever spans carry clause ids, not clause text |
| `446c092c…` | pilot | FAILED (auth token expiry mid-run); superseded by `84d2b181…` |

**Honest reading:** verdict agreement with the human-final decision is 0.9467; the capture does not include per-case counts. The gate is relative by design, so v6 cannot ship worse than v5; it does not claim the 0.95 target.
