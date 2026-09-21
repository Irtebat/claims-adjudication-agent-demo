# Glossary

Shared terminology for contributors. Domain terms describe steel products and claims;
engineering terms describe the platform and data design.

## Steel and claims

- Coated flat steel: steel coil with a protective coating. Types include galvanized
  (zinc, e.g. G90), Galvalume / Aluminum-Zinc (e.g. AZ50), and prepainted. Sold to
  construction, appliance, and automotive customers, often through service centers.
- Grade / spec: a standardized designation with property ranges (e.g. ASTM A653 CS Type
  B). The contractual definition of "conforming" material.
- Mill Test Certificate (MTC): the document certifying a heat or coil's actual chemistry
  and mechanical properties (yield, tensile, elongation) against the grade spec. The
  structured record used to prove conformance.
- Heat (heat number): a single melt of steel; the primary traceability batch.
- Coil ID / lot: the traceability unit for flat products.
- Coating designation / weight: for example G90 or Z275 (zinc coating mass). Drives the
  corrosion warranty.
- Non-conformance (NCR): material that fails the spec. Categories: dimensional (gauge,
  width, flatness, camber), surface (scale, pitting, coating voids, white rust, red rust,
  adhesion, spangle), mechanical (properties out of range), or chemistry deviation.
- Claim: a customer request for credit, replacement, or warranty settlement on defective
  or non-conforming steel, or a field corrosion claim.
- Adjudication: the verdict on a claim. One of APPROVE, DENY, or PEND-INVESTIGATE. An
  APPROVE may be full or partial and carries a disposition.
- Disposition: how an approved claim is settled: credit (credit memo or refund),
  replacement (ship a replacement coil), or rework (pay a rework allowance).
- Partial approval: approve part of a claim and deny the rest. Represented simply by
  approved_amount < claimed_amount, not a separate verdict.
- Coating / corrosion warranty: a multi-year promise (often 20 to 40 years) against
  premature coating failure on coated products. Carries environment and installation
  exclusions.
- Leakage: money settled that should not have been (in-spec per MTC, out of warranty,
  environment excluded, duplicate, over-claimed freight or tonnage, or fraud). The
  primary value driver.
- Supplier recovery: when a supplier's input caused the defect, the producer settles the
  customer and then charges the cost back to that supplier.
- Root cause / failure mode: attributing a defect to a process step (caster, hot mill,
  cold mill, coating line) or supplier input.
- SIU: Special Investigations Unit; investigates suspicious claim patterns.
- Reserve / accrual: money set aside for expected future claim costs (returns allowance
  and long-tail coating-warranty liability). Reserve accuracy is a finance metric.
- COPQ (Cost of Poor Quality): the cost of claims, returns, scrap, and rework. The
  umbrella KPI.
- Auto-adjudication / straight-through: deciding clean claims with no human when
  confidence is high.

## Engineering and Databricks

- OLTP (operational plane): the transactional database (Lakebase, managed Postgres);
  system of record for a live claim.
- Lakehouse (analytical plane): Delta tables in Unity Catalog; system of record for
  history, features, labels, and KPIs.
- Unity Catalog (UC): the governance layer (catalog, schema, table, function, volume)
  with grants, column masks, and lineage.
- Medallion (bronze, silver, gold): raw, then cleaned, then business-ready aggregates.
- Synced Table: copies a UC Delta table into Lakebase for serving (UC to Lakebase).
- Lakehouse Sync: streams Lakebase changes into UC as change history (Lakebase to UC).
- CDC (Change Data Capture): streaming every insert, update, and delete out of a
  database.
- Outbox pattern: writing an event row in the same transaction as the state change so
  the message bus cannot disagree with what was committed. Solves the dual-write problem.
- Record linkage / entity resolution: detecting true duplicates by blocking plus exact
  and fuzzy matching on structured keys. Not semantic similarity.
- Blocking: pre-partitioning records so only plausible pairs are compared.
- Hybrid search: fusing keyword search (BM25) and dense embeddings, combined with
  Reciprocal Rank Fusion (RRF).
- Reranker (cross-encoder): second-stage precision re-scoring of top candidates.
- Mosaic AI Agent Framework: the framework for building a tool-calling agent
  (ResponsesAgent), logged to MLflow and registered in UC.
- Model Serving / Model Service: Model Serving hosts a model; a Model Service registers
  it as a UC securable object.
- Unity Gateway: the UC-built governance layer in front of a serving endpoint (rate
  limits, guardrails, inference logging, audit). It governs an endpoint; it does not host
  the model.
