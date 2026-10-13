# Execution evidence — what actually ran

> Output excerpts pasted from committed captures. **Synthetic demo data** on workspace
> `fe-bar-ir-2026` (catalog `fe-bar-ir`). Every excerpt links to the full capture, which
> records the exact command, UTC timestamp and exit/HTTP status. Captures are read-only;
> personal emails are redacted; no tokens are recorded.

| Stage | Proof | Full capture |
|---|---|---|
| Lakeflow | 2 medallion updates `COMPLETED`, `full_refresh=false`; CDF → silver SCD2 → gold counts | [routine-refresh.log](docs/evidence/live-verification-2026-10-13/routine-refresh.log), [lakebase-and-pipeline.md](docs/evidence/live-verification-2026-10-13/lakebase-and-pipeline.md) |
| Unity Catalog | 9 governed dashboard queries `SUCCEEDED`; grants + column masks | [dashboard.md](docs/evidence/live-verification-2026-10-13/dashboard.md), [synthetic-data/](docs/evidence/synthetic-data/) |
| Lakebase | live row counts; 7 synced tables online | [lakebase-and-pipeline.md](docs/evidence/live-verification-2026-10-13/lakebase-and-pipeline.md) |
| ML / GenAI | live endpoint response; MLflow release-gate scores; rules-vs-agent ablation | [serving.md](docs/evidence/live-verification-2026-10-13/serving.md), [release-gate-v6/](docs/evidence/release-gate-v6/), [ablation-eval/](docs/evidence/ablation-eval/) |
| Genie | 6 questions → generated SQL → rows, both spaces | [genie.md](docs/evidence/live-verification-2026-10-13/genie.md) |
| App | 4 authenticated API calls, all HTTP 200 | [app.md](docs/evidence/live-verification-2026-10-13/app.md) |
| End to end | one claim traced through every stage | [e2e-trace.md](docs/evidence/live-verification-2026-10-13/e2e-trace.md) |

---

## 1. Lakeflow — pipeline runs (2026-10-13)

```
+ databricks bundle run fraud_graph --target prod --profile fe-bar-ir-2026
2026-10-13 13:43:40 "fe-bar-fraud-graph" TERMINATED SUCCESS
{'catalog': 'fe-bar-ir', 'claims_scored': 5100, 'clusters': 900, 'risk_rows': 4400, 'high_risk_rows': 578}
```

`steel-claims` pipeline (`736c9bbe…`) latest updates:

| update_id | cause | full_refresh | state |
|---|---|---|---|
| 002f10ae-a014-45b6-a992-23cc952159bb | JOB_TASK | false | COMPLETED |
| f0857233-2ca2-4a67-b06e-ab800661a437 | JOB_TASK | false | COMPLETED |

Row counts after the refresh (Unity Catalog):

| Layer | Table | Rows |
|---|---|---:|
| CDF landing | cdf.lb_claims_history | 5,100 |
| CDF landing | cdf.lb_adjudications_history | 5,107 |
| Silver (SCD2) | silver.adjudications_history | 5,104 |
| Gold | gold.claims_current | 5,100 |
| Gold | gold.gold_claim_adjudication_fact | 5,101 |
| Gold | gold.adjudication_decision_records | 104 |

## 2. Lakebase — live operational store

| Table | Rows | | Table | Rows |
|---|---:|---|---|---:|
| public.claims | 5,100 | | reference.heats_coils | 4,500 |
| public.adjudications | 5,101 | | reference.mill_test_certs | 4,500 |
| public.adjudication_decision_records | 104 | | reference.customer_heat_risk | 4,400 |
| public.outbox | 3 | | reference.prior_claims_corpus | 5,003 |
| public.settlements | 3 | | reference.customers | 200 |

All 7 synced tables report `SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE`.

## 3. ML / GenAI — live agent response

Endpoint `agents_fe-bar-ir-default-claims_adjudication_agent`, 100% traffic on `@prod` v6,
invoked with `persist=false` (nothing written):

```json
{
  "claim_id": "CLM-0000002",
  "output_text": "Recommendation for claim CLM-0000002: APPROVE / CREDIT (approved 9113.59). Deterministic verdict APPROVE; invariant violations: none.",
  "recommended_verdict": "APPROVE",
  "deterministic": { "verdict": "APPROVE", "disposition_rule": "R7_credit", "reason": "covered",
                     "approved_amount": 9113.59, "settlement_authority_amount": 9113.59 },
  "invariant_violations": [],
  "cited_clause_ids": ["prepainted/NA/V1/exclusions", "prepainted/NA/V1/coverage", "prepainted/NA/V1/proration"],
  "llm_used": true,
  "write_result": { "persisted": false }
}
```

The approved amount equals the deterministic settlement authority; the LLM reasoned and cited.

## 4. ML / GenAI — MLflow release-gate scores (v6, 75 held-out claims)

| Scorer | Threshold | v5 `@prod` | v6 candidate |
|---|---|---:|---:|
| no_payable_duplicate | 1.0 (money-safety) | 1.00 | **1.00** |
| amount_matches_authority | 1.0 (money-safety) | 1.00 | **1.00** |
| verdict_matches_eligibility | 1.0 (money-safety) | 1.00 | **1.00** |
| citations_in_resolved_policy | 1.0 (money-safety) | 1.00 | **1.00** |
| verdict_exact_match | not worse than @prod | 0.9467 | **0.9467** |
| disposition_exact_match | not worse than @prod | 0.9467 | **0.9467** |
| amount_matches_gold | not worse than @prod | 1.00 | **1.00** |

Decision: promoted v5 → v6. Runs `8ef31362…` (v6) / `988d66f5…` (v5). Details:
[release-gate-v6/](docs/evidence/release-gate-v6/README.md).

## 5. ML / GenAI — rules vs. agent (ablation, 100 held-out claims)

MLflow comparison run `48ae74fd89cf49fe9995552f78050490` (experiment `/Shared/claims-adjudication-ablation`):

| | deterministic rules | agent v3 |
|---|---|---|
| Narrative traps caught (30) | **0 / 30** (approves all) | **30 / 30** (escalates to a human) |
| False escalations on normal claims (70) | 0 | **0** |
| Normal-claim verdict match (70) | 0.857 | 0.857 |
| Amount matches gold (100) | 0.70 | **1.00** |
| Latency / claim | ~1.8 s | ~39 s |

Worked example: structured `coast_distance_km = 10.0`, narrative *"the building 0.6 km from the
shoreline"*. Rules → APPROVE, pays 6,133.33. Agent → PEND_INVESTIGATE, pays 0, flags the
conflict. Full write-up: [mixed-v3-vs-baseline-2026-10-04.md](docs/evidence/ablation-eval/mixed-v3-vs-baseline-2026-10-04.md).

## 6. Genie — question → SQL → rows

**Operational space** `01f1c269…` — *"How many adjudications are currently FINAL versus RECOMMENDED?"*

```sql
SELECT `decision_status`, COUNT(*) AS `adjudication_count`
FROM `fe-bar-ir`.`gold`.`adjudications_current`
WHERE `decision_status` IN ('FINAL', 'RECOMMENDED')
GROUP BY `decision_status` ORDER BY `decision_status`
```

| decision_status | adjudication_count |
|---|---:|
| FINAL | 5003 |
| RECOMMENDED | 98 |

Six questions across both spaces all `COMPLETED` — see [genie.md](docs/evidence/live-verification-2026-10-13/genie.md).

## 7. AI/BI dashboard — governed KPIs

Dashboard `01f1c269ddc4…` `ACTIVE`; 9/9 datasets `SUCCEEDED`. Executive KPI row:

| claims | approval rate | claimed | approved | duplicate blocked | over-claim reduction |
|---:|---:|---:|---:|---:|---:|
| 5,101 | 49.7% | $86.28M | $39.14M | $8.62M | $6.80M |

## 8. App — authenticated API

| Call | HTTP | Result |
|---|---|---|
| `GET /api/whoami` | 200 | roles `adjuster`, `business_user` |
| `GET /api/queue` | 200 | 50 RECOMMENDED claims (e.g. `CLM-DEMO-000099`, DENY / DUPLICATE of `CLM-0000099`, confidence 0.98) |
| `GET /api/claims/CLM-DEMO-000099` | 200 | decision record, 3 citations, conformance/coverage/settlement, 5 prior claims |
| `GET /api/history` | 200 | finalized decisions with `decided_by`, override flag/reason |

## 9. End to end — `CLM-DEMO-000006`

Lakebase claim → engineer finalizes in the app (APPROVE / CREDIT / **8,360.00**) → decision record +
outbox event (`published_at` set) → Kafka consumer writes settlement `STL-CLM-DEMO-000006`
(`PENDING_PAYMENT`, 8,360.00) → native CDF `cdf.lb_*_history` → `silver.adjudications_history`
(RECOMMENDED → FINAL SCD2 versions) → `gold.adjudications_current` → operational Genie returns
the row (FINAL / APPROVE / 8360.00) → app history + cockpit. Each hop with its query:
[e2e-trace.md](docs/evidence/live-verification-2026-10-13/e2e-trace.md).

## Known limitations

- Live v6 records `reasoning_endpoint = system.ai.gpt-5-4` (pre-fix label); the fix is merged and goes live on the next promote → deploy.
- Settlements are not modeled in silver/gold; they appear in Lakebase and `cdf.lb_settlements_history`.
- All figures are from synthetic demo data, not a customer's production book.
