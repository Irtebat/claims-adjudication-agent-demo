# Ablation: deterministic rules vs. agent v3 (narrative-escalation) — mixed held-out set

**Date:** 2026-10-04 · **Dataset:** `fe-bar-ir.eval.heldout_claims_mixed` (100 claims: 70 normal + 30 narrative-trap) · **MLflow:** experiment `/Shared/claims-adjudication-ablation`, ablation id `77a71dd4-d3ed-4387-bf71-0bb2b1e4e0dd`, comparison run `48ae74fd89cf49fe9995552f78050490` · **Raw:** `mixed-v3-vs-baseline-2026-10-04.json`.

Two candidates on the identical claims: `deterministic_baseline` (pure Python rules R1–R7, in-process, no LLM) and `models:/fe-bar-ir.default.claims_adjudication_agent/3` (the narrative-escalation agent on `gpt-5-4`). Zero failures/timeouts on either arm (the endpoint-timeout fix in this PR is what let the agent arm complete at all).

## The headline

| | deterministic rules | agent v3 |
|---|---|---|
| **Narrative traps caught (30)** | **0 / 30** (approves all) | **30 / 30** (PEND_INVESTIGATE, each with a `narrative_conflict`) |
| False escalation on normal (70) | 0 | **0** |
| Normal verdict match (70) | 0.857 | 0.857 |
| Amount matches gold (100) | 0.70 | **1.00** |
| Deciding sub-clause cited (narrative) | n/a | **0 / 30** |
| Citations present | n/a | 1.00 |
| Invariant corrections | n/a | 0.0 |
| Latency / claim | ~1.8 s | ~39 s |
| Tokens / claim | 0 (free) | ~5,945 |

## What this shows — does the AI help, and how?

**Yes, in exactly one decisive place the rules structurally cannot reach.** The deterministic rules read only *structured* fields. On the 30 narrative-trap claims the structured fields look clean but the free-text narrative contradicts them — so the rules **approve all 30** (would leak money). The agent reads the prose, spots the contradiction, and **escalates all 30 to a human** (`PEND_INVESTIGATE`, amount 0) with a `narrative_conflict` flag quoting the offending sentence — **with zero false escalations on the 70 normal claims.** This is the human-in-the-loop value: the AI refuses to auto-decide when the document disagrees with the data.

**Its limit:** v3 flags the conflict and cites the *related* exclusion/coverage clauses, but does **not** pinpoint the exact governing sub-clause (`deciding_clause_cited` 0/30 on narrative). It narrows the haystack for the adjuster; it doesn't fully close it.

**On ordinary structured claims the AI is neither better nor worse at the decision** than the rules (both 0.857 on normal verdict; the shared 10/70 miss is the fraud-ring stratum, which *no* written rule covers). The AI's extra value there is traceability — 100% citation coverage. Money safety holds throughout: amount is always correct, verdict never overrides the deterministic money path, and the invariant layer made 0 corrections.

**Honest caveat on the metric names:** the strict `narrative_escalation_rate` summary metric reads 0/30 because it *also* demands the exact deciding sub-clause (which v3 misses). By the behavior that matters operationally — "did it escalate instead of auto-approving?" — v3 scores **30/30**. Likewise, v3's narrative "verdict 0/30" is because the gold label expects a confident `DENY` while v3 conservatively `PEND`s; for a human-in-the-loop system, escalating a genuine narrative conflict is the designed-correct action, so a strict DENY-match understates v3 here.

## Worked example (claim `724112a1…`, stratum `coastal_lt_2km`)

- **Structured field:** `coast_distance_km = 10.0` (looks inland, no coastal exclusion).
- **Narrative:** *"The survey places the building 0.6 km from the shoreline."* (contradicts the structured field).
- **Gold label:** `DENY`, amount `0.00` — coastal exclusion (`warranties.coverage.min_coast_distance_km`, <2 km).
- **Deterministic rules →** `APPROVE / CREDIT`, pays **€6,133.33** (rule R7_credit fired on the clean structured fields; the narrative is invisible to it). **Wrong — a payout that should never happen.**
- **Agent v3 →** `PEND_INVESTIGATE`, amount **0.00**, `narrative_conflict = { clause: galvanized/EU/V1/exclusions, narrative_quote: "the building 0.6 km from the shoreline", structured_field: "coast_distance_km=10.0" }`. **Catches the contradiction and routes to a human** — though it cites the generic exclusions clause, not the precise `min_coast_distance_km` sub-clause.

## Bottom line
On this data the AI's measurable decision value is **narrative/document-contradiction detection with safe escalation** (30/30 vs 0/30, no false alarms) — a blind spot of any purely-structured rules engine — plus citation/traceability. It does not yet pinpoint the exact governing clause, and it adds ~39 s and ~5.9k tokens per claim versus a free, instant rules pass. The deterministic layer still owns every money figure.
