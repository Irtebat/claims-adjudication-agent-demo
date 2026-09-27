# AI/BI dashboard and Genie space

This directory contains the deployed Steel Quality Claims Analytics dashboard and its linked, curated Genie space.

## Dashboard

`quality_claims.lvdash.json` has four pages: Executive COPQ, Quality / Metallurgy, Finance / Recovery, and Trust / Ops. Governed quality KPIs come from `gold.quality_claims_metrics`; cycle time and specialized analytics come from the corresponding `gold_*` analytics tables. The four leakage categories remain separate because they may overlap.

Deploy with the authenticated `fe-bar` profile:

```sh
cd dashboards
databricks bundle validate --strict -t prod --profile fe-bar
databricks bundle deploy -t prod --profile fe-bar
databricks bundle summary -t prod --profile fe-bar
```

## Genie

`genie/genie_space.json` is the parsed, version-controlled serialized space. It uses the metric view plus failure-mode, supplier-recovery, fraud-cluster, and agent-human-alignment analytics. It does not attach the underlying wide claim fact.

Create a new space reproducibly:

```sh
SERIALIZED=$(jq -c '.' dashboards/genie/genie_space.json | jq -Rs '.')
jq -n --arg warehouse_id '38e458a09de4a055' \
  --arg title 'Steel Quality Claims Analytics' \
  --arg parent_path '/Workspace/Users/<user>/genie-spaces' \
  --argjson serialized_space "$SERIALIZED" \
  '{warehouse_id:$warehouse_id,title:$title,parent_path:$parent_path,serialized_space:$serialized_space}' \
  > /tmp/create-genie.json
databricks genie create-space --json @/tmp/create-genie.json --profile fe-bar
```

To update the deployed space, build the same `SERIALIZED` value and run:

```sh
jq -n --argjson serialized_space "$SERIALIZED" '{serialized_space:$serialized_space}' > /tmp/update-genie.json
databricks genie update-space <space-id> --json @/tmp/update-genie.json --profile fe-bar
```

The dashboard's `uiSettings.genieSpace.overrideId` links to the deployed space. Change it when importing into another workspace.
