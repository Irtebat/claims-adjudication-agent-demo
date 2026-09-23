# Synthetic data evidence

Captured 2026-09-23 from catalog `fe-bar-ir` after successful run `122092277256700`.

- 5,000 lean claims and 5,000 finalized adjudications were published.
- Claims contain only customer-filed fields; `ground_truth_label` is absent.
- Adjudications are the ground-truth carrier. Verdicts are `APPROVE`, `DENY`, or `PEND`; dispositions include `CREDIT`, `REPLACEMENT`, `REWORK`, `DENY`, terminal `DUPLICATE`, and `PEND_INVESTIGATE`.
- Native Lakebase CDF remains disabled.
