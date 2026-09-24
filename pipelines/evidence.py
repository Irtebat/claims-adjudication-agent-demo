"""Persist real SQL results with the exact query, then fail on invalid fixtures."""

import json
from datetime import datetime, timezone

# Policy standards and coating-warranty terms are no longer produced by this
# pipeline; they are loaded into Lakebase by the policy intake. This inventory
# covers only the reference/master/history datasets the medallion still owns.
TABLES = {
    "bronze": [
        "customers",
        "suppliers",
        "defect_codes",
        "heats_coils",
        "mill_test_certs",
        "claims_history",
        "adjudications_history",
    ],
    "silver": [
        "customers",
        "suppliers",
        "defect_codes",
        "heats_coils",
        "mill_test_certs",
        "claims_history",
        "adjudications_history",
    ],
    "gold": ["claims_history", "adjudications_history"],
}


def capture(sql, catalog, destination):
    destination.mkdir(parents=True, exist_ok=True)
    c = f"`{catalog}`"
    business_tables = " OR ".join(
        f"(table_schema = '{schema}' AND table_name = '{table}')"
        for schema, tables in TABLES.items()
        for table in tables
    )

    def save(name, query):
        output = sql(query)
        (destination / f"{name}.json").write_text(
            json.dumps(
                {
                    "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                    "query": query,
                    "result": json.loads(output),
                },
                indent=2,
            )
            + "\n"
        )
        return json.loads(output)

    inventory = save(
        "persisted-table-inventory",
        f"SELECT table_schema, table_name, table_type FROM {c}.information_schema.tables WHERE table_schema IN ('bronze','silver','gold') ORDER BY table_schema, table_name",
    )
    types = {(r["table_schema"], r["table_name"]): r["table_type"] for r in inventory}
    for table in TABLES["gold"]:
        assert types[("gold", table)] == "MATERIALIZED_VIEW", types
    for table in TABLES["silver"]:
        if not table.endswith("_history"):
            assert types[("silver", table)] == "STREAMING_TABLE", types
    save(
        "table-schemas",
        f"SELECT table_schema, table_name, column_name, data_type, ordinal_position "
        f"FROM {c}.information_schema.columns WHERE {business_tables} "
        "ORDER BY table_schema, table_name, ordinal_position",
    )
    save(
        "row-counts",
        " UNION ALL ".join(
            f"SELECT '{schema}.{table}' AS table_name, count(*) AS row_count FROM {c}.{schema}.{table}"
            for schema, tables in TABLES.items()
            for table in tables
        ),
    )
    save(
        "outcome-distribution",
        f"SELECT verdict, disposition, count(*) row_count FROM {c}.gold.adjudications_history GROUP BY verdict, disposition ORDER BY verdict, disposition",
    )
    for schema, tables in TABLES.items():
        if schema == "bronze":
            continue
        for table in tables:
            save(
                f"sample-{schema}-{table}", f"SELECT * FROM {c}.{schema}.{table} ORDER BY 1 LIMIT 3"
            )
    save("show-grants-claims", f"SHOW GRANTS ON TABLE {c}.gold.claims_history")
    save("show-grants-heats", f"SHOW GRANTS ON TABLE {c}.silver.heats_coils")
    grants = save(
        "grants",
        f"SELECT * FROM {c}.information_schema.table_privileges WHERE grantee IN ('adjuster','metallurgy_analyst') ORDER BY table_schema, table_name, grantee",
    )
    save(
        "schema-grants",
        f"SELECT * FROM {c}.information_schema.schema_privileges WHERE grantee IN ('adjuster','metallurgy_analyst') ORDER BY schema_name, grantee",
    )
    save(
        "catalog-grants",
        f"SELECT * FROM {c}.information_schema.catalog_privileges WHERE grantee IN ('adjuster','metallurgy_analyst') ORDER BY grantee",
    )
    masks = save(
        "column-masks",
        f"SELECT * FROM {c}.information_schema.column_masks WHERE table_schema IN ('bronze','silver','gold') ORDER BY table_schema, table_name, column_name",
    )
    expected_grants = {
        (schema, table, role)
        for schema in ("silver", "gold")
        for table in TABLES[schema]
        for role in ("adjuster", "metallurgy_analyst")
    }
    actual_grants = {
        (r["table_schema"], r["table_name"], r["grantee"])
        for r in grants
        if r["privilege_type"] == "SELECT"
    }
    assert expected_grants <= actual_grants, expected_grants - actual_grants
    expected_masks = {
        *(('bronze', 'customers', column) for column in ('customer_id', 'customer_name', 'email')),
        *(('bronze', 'heats_coils', column) for column in ('customer_id', 'unit_price')),
        *(('bronze', 'claims_history', column) for column in ('customer_id', 'claimed_freight')),
        *(('bronze', 'adjudications_history', column) for column in ('claimed_amount', 'approved_amount')),
        *(('silver', 'claims_history', column) for column in ('customer_id', 'claimed_freight')),
        *(('silver', 'adjudications_history', column) for column in ('claimed_amount', 'approved_amount')),
        *(('gold', 'claims_history', column) for column in ('customer_id', 'claimed_freight')),
        *(('gold', 'adjudications_history', column) for column in ('claimed_amount', 'approved_amount')),
    }
    actual_masks = {(r["table_schema"], r["table_name"], r["column_name"]) for r in masks}
    assert expected_masks <= actual_masks, expected_masks - actual_masks
    save(
        "fraud-clusters",
        f"SELECT a.fraud_cluster_id, count(*) claims, count(DISTINCT c.customer_id) customers, count(DISTINCT h.heat_no) heats FROM {c}.gold.adjudications_history a JOIN {c}.gold.claims_history c USING(claim_id) JOIN {c}.silver.heats_coils h USING(coil_id) WHERE a.fraud_cluster_id IS NOT NULL GROUP BY a.fraud_cluster_id ORDER BY a.fraud_cluster_id",
    )
    from src.checks import integrity_queries

    checks = integrity_queries(c)
    results = save(
        "integrity-checks",
        " UNION ALL ".join(
            f"SELECT '{name}' check_name, n violations FROM ({query})"
            for name, query in checks.items()
        ),
    )
    assert all(int(row["violations"]) == 0 for row in results), results
    print(f"Saved live evidence to {destination.name}; {len(checks)} integrity checks passed")
