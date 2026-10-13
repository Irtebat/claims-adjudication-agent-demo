# Environment manifests

`config/envs/<env>.yaml` is the checked-in source of truth for non-secret, intended
environment configuration. `fe-bar-ir-2026.yaml` records the present environment;
`example.yaml` is a safe starting point for a new one. Secret values remain in secret
scopes and never belong here.

Discovered values are separate from intent. Drivers must read and atomically update
`.deploy/<env>/state.json` through `scripts/envlib`; this ignored file holds Genie and
dashboard IDs, app/serving service-principal IDs, a created warehouse ID, and model version.

Local readiness commands (they make no Databricks calls):

```text
uv run --with pyyaml python scripts/env.py validate --env fe-bar-ir-2026
uv run --with pyyaml python scripts/env.py show --env fe-bar-ir-2026
uv run --with pyyaml python scripts/env.py vars --env fe-bar-ir-2026 --bundle pipelines
uv run --with pyyaml python scripts/env.py plan --env example
```

The present environment's UC storage binding was confirmed with read-only catalog,
external-location, and storage-credential inspection. `release_environment` is the future
release gate; this PR only reports it.
