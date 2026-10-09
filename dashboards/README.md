# AI/BI dashboard and Genie space

This directory contains the deployed Steel Quality Claims Analytics dashboard and its linked, curated Genie space.

## Dashboard

`quality_claims.lvdash.json` has four pages: Executive COPQ, Quality / Metallurgy, Finance / Recovery, and Trust / Ops. Governed quality KPIs come from `gold.quality_claims_metrics`; cycle time and specialized analytics come from the corresponding `gold_*` analytics tables. The four leakage categories remain separate because they may overlap.

### Data sources

- `gold.gold_claim_adjudication_fact` — cross-filterable claim-grain detail.
- `gold.gold_quality_kpis`, `gold.gold_failure_mode_analytics`, `gold.gold_supplier_recovery_analytics`, `gold.gold_fraud_cluster_analytics`, `gold.gold_agent_human_alignment`, and `gold.gold_retrieval_citation_kpis` — dashboard-ready aggregates.
- `gold.quality_claims_metrics` — governed reusable measures shared by the dashboard and Genie.

Workflow-backlog visuals remain deferred until settlement, recovery, and investigation events exist. The downstream consumers are implemented but are stubs, and the live broker fan-out requires the Aiven secrets (see `docs/CURRENT-STATE.md`), so those events are not populated in the demo.

Deploy with the authenticated `fe-bar-ir-2026` profile:

```sh
cd dashboards
databricks bundle validate --strict -t prod --profile fe-bar-ir-2026
databricks bundle deploy -t prod --profile fe-bar-ir-2026
databricks bundle summary -t prod --profile fe-bar-ir-2026
```

## Genie

`genie/genie_space.json` is the parsed, version-controlled serialized space. It uses the metric view plus failure-mode, supplier-recovery, fraud-cluster, and agent-human-alignment analytics. It does not attach the underlying wide claim fact.

Create a new space reproducibly (replace `<WAREHOUSE_ID>` with a serverless SQL warehouse id from your workspace — `databricks warehouses list`):

```sh
SERIALIZED=$(jq -c '.' dashboards/genie/genie_space.json | jq -Rs '.')
jq -n --arg warehouse_id '<WAREHOUSE_ID>' \
  --arg title 'Steel Quality Claims Analytics' \
  --arg parent_path '/Workspace/Users/<user>/genie-spaces' \
  --argjson serialized_space "$SERIALIZED" \
  '{warehouse_id:$warehouse_id,title:$title,parent_path:$parent_path,serialized_space:$serialized_space}' \
  > /tmp/create-genie.json
databricks genie create-space --json @/tmp/create-genie.json --profile fe-bar-ir-2026
```

To update the deployed space, build the same `SERIALIZED` value and run:

```sh
jq -n --argjson serialized_space "$SERIALIZED" '{serialized_space:$serialized_space}' > /tmp/update-genie.json
databricks genie update-space <space-id> --json @/tmp/update-genie.json --profile fe-bar-ir-2026
```

The dashboard's `uiSettings.genieSpace.overrideId` links to the deployed space. Change it when importing into another workspace.
