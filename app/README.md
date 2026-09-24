# app

Planned layer — not yet implemented. This directory will hold the Databricks App
that adjusters use to review a claim, see the agent's recommendation and cited
evidence, and record the final decision.

## Intended purpose

- Present each pending claim alongside the deterministic authority outputs
  (conformance, coverage, settlement), the cited spec/warranty clauses, similar
  prior claims, and any duplicate or fraud-risk flags.
- Let an adjuster confirm or override the recommendation and record the final
  adjudication.
- Write the decision and an outbox event in a single Lakebase transaction, so the
  downstream event flow (see `services/`) stays consistent.

## Intended inputs

- Reference and current claim data from Lakebase (`reference.*`, `public.claims`).
- The agent tools in `agent/` for recommendations and evidence.

Nothing is built here yet: there is no app code, bundle resource, or configuration.
