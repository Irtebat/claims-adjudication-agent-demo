# genie.json — readable rendering

> Generated from [`genie.json`](genie.json) (the raw capture). Synthetic demo data, workspace `https://fe-sandbox-fe-bar-ir-2026.cloud.databricks.com`, captured 2026-10-13T08:31:58+00:00. Personal emails redacted; no tokens recorded.

**Purpose:** Genie: both Genie agents (spaces) — metadata + live Conversation API questions with generated SQL and result rows

## [operational] Show the claim_id, decision_status, verdict, disposition and approved_amount for claim_id exactly 'CLM-DEMO-000006'.

Status: `COMPLETED` · rows: 1 · query state: `SUCCEEDED`

**Generated SQL**
```sql
SELECT `c`.`claim_id`, `a`.`decision_status`, `a`.`verdict`, `a`.`disposition`, `a`.`approved_amount`
FROM `fe-bar-ir`.`gold`.`claims_current` AS `c`
JOIN `fe-bar-ir`.`gold`.`adjudications_current` AS `a`
  ON `c`.`claim_id` = `a`.`claim_id`
WHERE `c`.`claim_id` = 'CLM-DEMO-000006'
```

**Genie answer:** For claim_id **CLM-DEMO-000006**, decision_status is **FINAL**, verdict is **APPROVE**, disposition is **CREDIT**, and approved_amount is **8360.00**. In the single returned record, claim_id **CLM-DEMO-000006** shows a finalized approved credit for **8360.00**.

**Result rows**

| claim_id | decision_status | verdict | disposition | approved_amount |
|---|---|---|---|---|
| CLM-DEMO-000006 | FINAL | APPROVE | CREDIT | 8360.00 |

## [operational] How many adjudications are currently FINAL versus RECOMMENDED?

Status: `COMPLETED` · rows: 2 · query state: `SUCCEEDED`

**Generated SQL**
```sql
SELECT `decision_status`, COUNT(*) AS `adjudication_count`
FROM `fe-bar-ir`.`gold`.`adjudications_current`
WHERE `decision_status` IS NOT NULL
  AND `decision_status` IN ('FINAL', 'RECOMMENDED')
GROUP BY `decision_status`
ORDER BY `decision_status`
```

**Genie answer:** There are **5,003 FINAL** adjudications and **98 RECOMMENDED** adjudications currently. **FINAL** is the dominant status in the visible data, accounting for nearly all adjudications returned by this query.

**Result rows**

| decision_status | adjudication_count |
|---|---|
| FINAL | 5003 |
| RECOMMENDED | 98 |

## [operational] Show finalized claims that were overridden and include the recorded override reason.

Status: `COMPLETED` · rows: 0 · query state: `SUCCEEDED`

**Generated SQL**
```sql
SELECT `claim_id`, `adjudication_id`, `decision_status`, `recommended_verdict`, `verdict`, `recommended_disposition`, `disposition`, `approved_amount`, `override_reason`, `decided_by`, `finalized_at` FROM `fe-bar-ir`.`gold`.`adjudications_current` WHERE `decision_status` = 'FINAL' AND `override_flag` = TRUE AND COALESCE(TRIM(`override_reason`), '') <> '' ORDER BY `finalized_at` DESC LIMIT 100
```

**Genie answer:** No finalized overridden claims were returned by this query, so there are **0** claims with a recorded override reason in the visible data.

**Result rows**

_(no rows)_

## [analytics] What is the denial rate by customer?

Status: `COMPLETED` · rows: 199 · query state: `SUCCEEDED`

**Generated SQL**
```sql
SELECT `Customer`, 100 * (try_divide(MEASURE(`Denied Claims`),NULLIF(MEASURE(`Claim Count`), 0))) AS `denial_rate` FROM `fe-bar-ir`.gold.quality_claims_metrics WHERE `Customer` IS NOT NULL GROUP BY ALL ORDER BY `denial_rate` DESC
```

**Genie answer:** Examples include:
- **Tate Ltd** — **65.22%**
- **Hardy-Carrillo** — **65.22%**
- **Johnson Inc** — **65.22%**
- **Henry, Martin and Johnson** — **64.00%**
- **Russell LLC** — **64.00%**

**Result rows**

| Customer | denial_rate |
|---|---|
| Tate Ltd | 65.21739130434783 |
| Hardy-Carrillo | 65.21739130434783 |
| Johnson Inc | 65.21739130434783 |
| Henry, Martin and Johnson | 64.0 |
| Russell LLC | 64.0 |
| Spence, Mendoza and Peters | 64.0 |
| Vaughn Ltd | 64.0 |
| Travis and Sons | 64.0 |
| Wilson Group | 64.0 |
| Casey Ltd | 63.63636363636363 |

_5 more rows in the .json_

## [analytics] What are the top defect modes by line, grade, and coating?

Status: `COMPLETED` · rows: 3 · query state: `SUCCEEDED`

**Generated SQL**
```sql
WITH defect_mode_totals AS (
  SELECT
    `line`,
    `grade`,
    `coating`,
    `failure_mode_code`,
    `defect_description`,
    SUM(`claim_count`) AS `claim_count`,
    RANK() OVER (
      PARTITION BY `line`, `grade`, `coating`
      ORDER BY SUM(`claim_count`) DESC, `failure_mode_code` ASC
    ) AS `defect_mode_rank`
  FROM `fe-bar-ir`.`gold`.`gold_failure_mode_analytics`
  WHERE `line` IS NOT NULL
    AND `grade` IS NOT NULL
    AND `coating` IS NOT NULL
    AND `failure_mode_code` IS NOT NULL
    AND `defect_description` IS NOT NULL
  GROUP BY ALL
)
SELECT
  `line`,
  `grade`,
  `coating`,
  `failure_mode_code`,
  `defect_description`,
  `claim_count`
FROM defect_mode_totals
WHERE `defect_mode_rank` <= 1
ORDER BY `line`, `grade`, `coating`, `claim_count` DESC, `failure_mode_code` ASC;
```

**Genie answer:** The top defect mode in the 3 line/grade/coating combinations returned is **MECH_TENSILE** (**Mechanical tensile deviation**) in every case.

- **LINE-1** / **ASTM A653 CS Type B** / **G90**: **1,018** claims
- **LINE-2** / **ASTM A792 AZ50** / **AZ50**: **1,014** claims
- **LINE-3** / **EN 10346 DX51D+Z275** / **Z275**: **1,013** claims

Across all 3 results shown, the same defect mode leads each combination, with claim counts tightly clustered between **1,013** and **1,018**.

**Result rows**

| line | grade | coating | failure_mode_code | defect_description | claim_count |
|---|---|---|---|---|---|
| LINE-1 | ASTM A653 CS Type B | G90 | MECH_TENSILE | Mechanical tensile deviation | 1018 |
| LINE-2 | ASTM A792 AZ50 | AZ50 | MECH_TENSILE | Mechanical tensile deviation | 1014 |
| LINE-3 | EN 10346 DX51D+Z275 | Z275 | MECH_TENSILE | Mechanical tensile deviation | 1013 |

## [analytics] What supplier-attributable value is associated with each supplier?

Status: `COMPLETED` · rows: 2 · query state: `SUCCEEDED`

**Generated SQL**
```sql
SELECT COALESCE(NULLIF(`supplier`, ''), 'Unassigned') AS `supplier`, SUM(`supplier_attributable_amount`) AS `supplier_attributable_amount`
FROM `fe-bar-ir`.`gold`.`gold_supplier_recovery_analytics`
GROUP BY `supplier`
ORDER BY `supplier_attributable_amount` DESC
```

**Genie answer:** There are **2 suppliers** in the results. **Bowman-Bradshaw** has **11,775,285.00** in supplier-attributable value, and **Unassigned** has **362,524.00**; **Bowman-Bradshaw** is much higher than the unassigned amount.

**Result rows**

| supplier | supplier_attributable_amount |
|---|---|
| Bowman-Bradshaw | 11775285.00 |
| Unassigned | 362524.00 |

