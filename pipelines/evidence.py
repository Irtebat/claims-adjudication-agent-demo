"""Persist real SQL results with the exact query, then fail on invalid fixtures."""

import json
from datetime import datetime, timezone

TABLES = {
    "bronze": [
        "spec_standards",
        "coating_warranty_terms",
        "customers",
        "suppliers",
        "defect_codes",
        "heats_coils",
        "mill_test_certs",
        "claims_history",
        "adjudications_history",
    ],
    "silver": [
        "spec_standards",
        "coating_warranty_terms",
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
        if table.endswith("_history"):
            continue
        assert types[("silver", table)] == "STREAMING_TABLE", types
        properties = save(
            f"cdf-silver-{table}",
            f"SHOW TBLPROPERTIES {c}.silver.{table} ('delta.enableChangeDataFeed')",
        )
        assert len(properties) == 1 and properties[0]["value"] == "true", properties
    save(
        "row-counts",
        " UNION ALL ".join(
            f"SELECT '{schema}.{table}' AS table_name, count(*) AS row_count FROM {c}.{schema}.{table}"
            for schema, tables in TABLES.items()
            for table in tables
        ),
    )
    save(
        "label-distribution",
        f"SELECT ground_truth_label, count(*) row_count, round(100.0*count(*)/sum(count(*)) OVER (),2) percentage FROM {c}.gold.claims_history GROUP BY ground_truth_label ORDER BY ground_truth_label",
    )
    for schema, tables in TABLES.items():
        if schema == "bronze":
            continue
        for table in tables:
            save(
                f"sample-{schema}-{table}", f"SELECT * FROM {c}.{schema}.{table} ORDER BY 1 LIMIT 3"
            )
    save(
        "policy-schemas",
        f"SELECT table_name, column_name, full_data_type FROM {c}.information_schema.columns WHERE table_schema = 'silver' AND table_name IN ('spec_standards','coating_warranty_terms') ORDER BY table_name, ordinal_position",
    )
    coverage = save(
        "clause-metadata-coverage",
        f"""SELECT 'spec_standards' corpus, count(*) clause_rows,
          count(DISTINCT clause_id) unique_clause_ids, count(DISTINCT parent_clause_id) parents,
          count_if(grade IS NULL OR spec_edition IS NULL OR region IS NULL
            OR clause_id IS NULL OR parent_clause_id IS NULL OR section_ref IS NULL
            OR source_sha256 IS NULL OR clause_text IS NULL OR length(trim(clause_text)) = 0
            OR structured_params IS NULL) incomplete_rows
          FROM {c}.silver.spec_standards
          UNION ALL
          SELECT 'coating_warranty_terms', count(*), count(DISTINCT clause_id),
          count(DISTINCT parent_clause_id),
          count_if(product_line IS NULL OR coating_class IS NULL OR region IS NULL
            OR effective_from IS NULL OR effective_to IS NULL OR effective_from >= effective_to
            OR clause_id IS NULL OR parent_clause_id IS NULL OR section_ref IS NULL
            OR source_sha256 IS NULL OR clause_text IS NULL OR length(trim(clause_text)) = 0
            OR structured_params IS NULL)
          FROM {c}.silver.coating_warranty_terms""",
    )
    assert all(
        int(r["incomplete_rows"]) == 0 and int(r["clause_rows"]) == int(r["unique_clause_ids"])
        for r in coverage
    ), coverage
    prohibited = save(
        "deferred-column-check",
        f"""SELECT table_schema, table_name, column_name
          FROM {c}.information_schema.columns
          WHERE table_schema IN ('bronze', 'silver', 'gold')
          AND (lower(column_name) LIKE '%embedding%' OR lower(column_name) LIKE '%vector%'
            OR (table_name = 'spec_standards' AND column_name = 'product_line'))""",
    )
    assert not prohibited, prohibited
    save("show-grants-claims", f"SHOW GRANTS ON TABLE {c}.gold.claims_history")
    save("show-grants-specs", f"SHOW GRANTS ON TABLE {c}.silver.spec_standards")
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
    sensitive = {
        "customers": ["customer_id", "customer_name", "email"],
        "heats_coils": ["customer_id", "unit_price"],
        "claims_history": [
            "customer_id",
            "unit_price",
            "claimed_amount",
            "claimed_freight",
            "freight_cap",
        ],
        "adjudications_history": ["claimed_amount", "approved_amount"],
    }
    expected_masks = {
        (schema, table, column)
        for schema, tables in TABLES.items()
        for table in tables
        for column in sensitive.get(table, [])
    }
    actual_masks = {(r["table_schema"], r["table_name"], r["column_name"]) for r in masks}
    assert expected_masks <= actual_masks, expected_masks - actual_masks
    save(
        "fraud-clusters",
        f"SELECT fraud_cluster_id, count(*) claims, count(DISTINCT customer_id) customers, count(DISTINCT heat_no) heats FROM {c}.gold.claims_history WHERE ground_truth_label='fraud_cluster' GROUP BY fraud_cluster_id ORDER BY fraud_cluster_id",
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
