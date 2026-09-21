# Steel Quality and Warranty Claims Adjudication Agent

A reference implementation of an agent that adjudicates steel quality and warranty
claims for a coated-flat-steel producer, built end to end on Databricks. A human
adjuster reviews and finalizes each decision. The agent recommends APPROVE, DENY,
or PEND-INVESTIGATE, with a disposition, an approved amount, and a cited rationale.

Design principle: decisions that move money are made by deterministic, auditable
tools (conformance against the Mill Test Certificate, coverage math, and duplicate
detection). Language-model retrieval only finds and cites supporting clauses; it
does not decide outcomes.

## Architecture

The implementation connects these Databricks capabilities as one flow:

| Capability      | Role                                                         |
| --------------- | ------------------------------------------------------------ |
| Lakeflow        | Ingest synthetic reference, master, and history into UC      |
| Unity Catalog   | Govern data and functions; host deterministic UC functions   |
| Lakebase        | Postgres store of record for the live claim (OLTP)           |
| Mosaic AI Agent | ResponsesAgent with tools that produce the recommendation    |
| Genie           | Natural-language query over gold KPIs                        |
| Databricks App  | Adjuster review UI                                           |

A Kafka event backbone (topics `claim.submitted` and `claim.adjudicated`) links the
operational store to downstream services. An MLflow evaluation loop measures and
improves the agent.

## Repository layout

| Path        | Contents                                                      |
| ----------- | ------------------------------------------------------------- |
| config/     | Environment config templates (placeholders only, no secrets)  |
| pipelines/  | Synthetic data generation and Lakeflow pipelines              |
| lakebase/   | Postgres schema, extensions, and synced-table definitions     |
| agent/      | Mosaic AI agent and its tools                                  |
| eval/       | MLflow evaluation harness and scorers                          |
| services/   | Kafka worker, outbox relay, and downstream consumers          |
| app/        | Databricks App (adjuster UI)                                   |
| dashboards/ | AI/BI dashboards and Genie definitions                         |
| docs/       | Documentation and committed execution evidence                 |

## Environment

- Databricks CLI profile: `fe-bar` (always pass `--profile fe-bar`).
- Unity Catalog: `fe-bar-ir`.
- Kafka: Aiven.
- Synthetic data only. Secrets live in Databricks secret scopes, not in source.

Copy `config/config.example.yaml` to `config/config.local.yaml` and fill in values.
`config.local.yaml` is not tracked.

## Development

See `CONVENTIONS.md` for how changes are proposed, reviewed, and committed.
