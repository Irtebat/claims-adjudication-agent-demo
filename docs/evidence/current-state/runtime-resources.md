# Runtime resources

Captured 2026-10-01. Exact read-only commands:

```bash
databricks jobs list --profile fe-bar -o json
databricks pipelines list-pipelines --profile fe-bar -o json
databricks serving-endpoints get agents_fe-bar-ir-default-claims_adjudication_agent --profile fe-bar -o json
databricks model-versions get-by-alias fe-bar-ir.default.claims_adjudication_agent prod --profile fe-bar -o json
databricks model-versions get-by-alias fe-bar-ir.default.claims_adjudication_agent candidate --profile fe-bar -o json
databricks apps get steel-claims-cockpit --profile fe-bar -o json
databricks lakeview list --profile fe-bar -o json
```

- The medallion pipeline is idle after a completed update. Seven synced-table pipelines are present and idle; the corpus sync last completed 2026-09-30.
- The endpoint is ready, configuration version 1, routing 100% to model version 1. Version 1 still reads legacy `public.prior_claims`; the next promotion must move serving to the live corpus.
- The registered model exists. Both `@prod` and `@candidate` resolve to READY model version 1.
- The app is running with active compute and a successful 2026-09-29 deployment. No user token was available for a fresh authenticated whoami capture.
- Pipeline, Lakebase, agent, and demo jobs exist. No services jobs were returned: the services bundle is not deployed.
- `databricks lakeview list` returned ACTIVE dashboard `01f1bac220111001a171872b5185e8e6`. Both Genie spaces exist and were fetched individually. Raw captured output is in `app-dashboard-genie.json`.
