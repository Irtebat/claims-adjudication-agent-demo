# Databricks notebook source
# MAGIC %pip install psycopg[binary]==3.2.10 databricks-sdk>=0.81.0

# COMMAND ----------
import json
from datetime import datetime

from databricks.sdk import WorkspaceClient

from generator_core import ClaimsGenerator

# COMMAND ----------
# Get parameters
catalog = dbutils.widgets.get("catalog")
count = int(dbutils.widgets.get("count"))
seed = int(dbutils.widgets.get("seed"))
mode = dbutils.widgets.get("mode", "serving_endpoint")  # serving_endpoint or in_process
endpoint = dbutils.widgets.get(
    "endpoint", "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
)
postgres_database = dbutils.widgets.get("postgres_database", "databricks_postgres")

if count < 100:
    raise ValueError("count must be >= 100")

print(f"Demo Backlog Parameters: count={count}, seed={seed}, mode={mode}")

# COMMAND ----------
# Generate claims
generator = ClaimsGenerator(count, seed=seed)
claims = generator.generate()

# Validate schema
schema_errors = ClaimsGenerator.validate_schema(claims)
if schema_errors:
    raise ValueError(f"Schema validation failed: {schema_errors}")

print(f"Generated {len(claims)} synthetic claims")
print(f"Sample claim: {json.dumps(claims[0], indent=2, default=str)}")

# COMMAND ----------
# Connect to Lakebase Postgres
import psycopg

w = WorkspaceClient()
endpoint_details = w.postgres.get_endpoint(name=endpoint)
credential = w.postgres.generate_database_credential(endpoint=endpoint)
username = w.current_user.me().user_name
host = endpoint_details.status.hosts.host

conn_params = {
    "host": host,
    "dbname": postgres_database,
    "user": username,
    "password": credential.token,
    "sslmode": "require",
}


# COMMAND ----------
# Insert claims into Lakebase
def insert_claims(conn, claims, data_provenance):
    """Insert claims into public.claims with the given data_provenance tag."""
    insert_sql = """
    INSERT INTO claims (
      claim_id, coil_id, customer_id, claim_type, claim_date,
      install_date, environment, installation, coast_distance_km,
      defect_code, defect_narrative, claimed_tonnage, claimed_freight,
      data_provenance
    ) VALUES (
      %(claim_id)s, %(coil_id)s, %(customer_id)s, %(claim_type)s, %(claim_date)s,
      %(install_date)s, %(environment)s, %(installation)s, %(coast_distance_km)s,
      %(defect_code)s, %(defect_narrative)s, %(claimed_tonnage)s, %(claimed_freight)s,
      %(data_provenance)s
    )
    ON CONFLICT (claim_id) DO UPDATE SET
      data_provenance = EXCLUDED.data_provenance
    """

    inserted_count = 0
    with conn.cursor() as cur:
        for claim in claims:
            claim_row = claim.copy()
            claim_row["data_provenance"] = data_provenance
            cur.execute(insert_sql, claim_row)
            if cur.rowcount > 0:
                inserted_count += 1
        conn.commit()

    return inserted_count


data_provenance = "synthetic_demo_backlog"
with psycopg.connect(**conn_params) as conn:
    inserted = insert_claims(conn, claims, data_provenance)
    print(f"Inserted {inserted} claims into public.claims")

# COMMAND ----------
# Run adjudication via serving endpoint or in-process
adjudication_results = {"mode": mode, "total_claims": count, "adjudicated": 0}

if mode == "serving_endpoint":
    print("Using serving endpoint: agents_fe-bar-ir-default-claims_adjudication_agent")

    def invoke_serving_endpoint(claim, persist=True):
        """Invoke the serving endpoint for a single claim."""
        import json

        body = {
            "input": [{"role": "user", "content": json.dumps(claim)}],
            "custom_inputs": {"persist": persist, "claim": claim},
        }

        response = w.api_client.do(
            "POST",
            "/serving-endpoints/agents_fe-bar-ir-default-claims_adjudication_agent/invocations",
            body=body,
        )
        return response

    adjudicated_count = 0
    failed_count = 0

    for claim in claims:
        try:
            result = invoke_serving_endpoint(claim, persist=True)
            adjudicated_count += 1
            if adjudicated_count % 100 == 0:
                print(f"Adjudicated {adjudicated_count}/{count}")
        except Exception as e:
            failed_count += 1
            print(f"Failed to adjudicate {claim['claim_id']}: {e}")
            if failed_count > 10:  # Stop after 10 failures to avoid spam
                print("Stopping due to repeated failures")
                raise

    adjudication_results["adjudicated"] = adjudicated_count
    adjudication_results["failed"] = failed_count

else:  # in_process mode
    raise RuntimeError(
        "in_process mode is not supported in the deployed demo bundle; "
        "use mode=serving_endpoint"
    )

# COMMAND ----------
# Summary
summary = {
    "catalog": catalog,
    "count": count,
    "seed": seed,
    "mode": mode,
    "data_provenance": data_provenance,
    "inserted_claims": inserted,
    "adjudicated": adjudication_results["adjudicated"],
    "failed": adjudication_results.get("failed", 0),
    "timestamp": datetime.utcnow().isoformat(),
}

print(f"\nSummary: {json.dumps(summary, indent=2)}")

# Optional: check queue population
with psycopg.connect(**conn_params) as conn:
    with conn.cursor() as cur:
        # The queue is RECOMMENDED adjudications for THIS demo batch. Adjudications
        # carry data_provenance='agent_recommendation' (set by the writer), so scope
        # by the batch via the claims join (claims carry 'synthetic_demo_backlog').
        cur.execute(
            """
            SELECT COUNT(*) FROM adjudications a
            JOIN claims c ON c.claim_id = a.claim_id
            WHERE c.data_provenance = %s AND a.decision_status = 'RECOMMENDED'
            """,
            (data_provenance,),
        )
        queue_count = cur.fetchone()[0]
        print(f"Adjuster queue (RECOMMENDED adjudications): {queue_count}")
        summary["queue_count"] = queue_count

dbutils.notebook.exit(json.dumps(summary, indent=2))
