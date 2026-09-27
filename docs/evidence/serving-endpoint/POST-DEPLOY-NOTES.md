# Post-deploy notes

## Blocking decision required

Lakebase rejected the prescribed role creation before making a role:

```text
Field role_id must match pattern ^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$,
got '47643eb1-dbd5-40a6-a51d-5da6b8e2da7a'.
```

The dedicated SP app/client ID is a UUID beginning with a digit. Approval is needed
to use a separate Lakebase resource ID (for example `claims-agent-sp`) while keeping
`spec.postgres_role` equal to the exact SP client ID. After that decision, the
remaining work is:

1. Create the Lakebase role and apply the enumerated least-privilege SQL grants.
2. Grant Unity Gateway `USE CATALOG`, `USE SCHEMA`, and model-service `EXECUTE`.
3. Add and validate `deploy_agent.py` plus the parameterized DAB job.
4. Deploy version 1 with Small workload and scale-to-zero.
5. Run the persistent end-to-end smoke test and capture the decision record.

No UI-only or Consumer Access entitlement blocker has been encountered yet.
