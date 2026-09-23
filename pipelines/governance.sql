-- Render ${catalog} from settings.yaml with run.py govern. No secrets.
-- Account groups adjuster / metallurgy_analyst are provisioned by run.py.
-- No users are enrolled automatically. Admins retain engineering visibility.
CREATE SCHEMA IF NOT EXISTS `${catalog}`.bronze;
CREATE SCHEMA IF NOT EXISTS `${catalog}`.silver;
CREATE SCHEMA IF NOT EXISTS `${catalog}`.gold;
CREATE OR REPLACE FUNCTION `${catalog}`.silver.mask_customer(value STRING) RETURNS STRING RETURN CASE WHEN is_account_group_member('adjuster') OR is_member('admins') THEN value ELSE concat('REDACTED-', sha2(value, 256)) END;
CREATE OR REPLACE FUNCTION `${catalog}`.silver.mask_money(value DECIMAL(18,2)) RETURNS DECIMAL(18,2) RETURN CASE WHEN is_account_group_member('adjuster') OR is_member('admins') THEN value ELSE CAST(NULL AS DECIMAL(18,2)) END;
GRANT USE CATALOG ON CATALOG `${catalog}` TO `adjuster`;
GRANT USE SCHEMA ON SCHEMA `${catalog}`.silver TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.customers TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.suppliers TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.defect_codes TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.heats_coils TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.mill_test_certs TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.claims_history TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.silver.adjudications_history TO `adjuster`;
GRANT USE SCHEMA ON SCHEMA `${catalog}`.gold TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.gold.claims_history TO `adjuster`;
GRANT SELECT ON TABLE `${catalog}`.gold.adjudications_history TO `adjuster`;
GRANT USE CATALOG ON CATALOG `${catalog}` TO `metallurgy_analyst`;
GRANT USE SCHEMA ON SCHEMA `${catalog}`.silver TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.customers TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.suppliers TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.defect_codes TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.heats_coils TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.mill_test_certs TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.claims_history TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.silver.adjudications_history TO `metallurgy_analyst`;
GRANT USE SCHEMA ON SCHEMA `${catalog}`.gold TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.gold.claims_history TO `metallurgy_analyst`;
GRANT SELECT ON TABLE `${catalog}`.gold.adjudications_history TO `metallurgy_analyst`;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.customers ALTER COLUMN customer_id SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.customers ALTER COLUMN customer_name SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.customers ALTER COLUMN email SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.heats_coils ALTER COLUMN customer_id SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.heats_coils ALTER COLUMN unit_price SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.claims_history ALTER COLUMN customer_id SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.claims_history ALTER COLUMN claimed_freight SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.adjudications_history ALTER COLUMN claimed_amount SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.bronze.adjudications_history ALTER COLUMN approved_amount SET MASK `${catalog}`.silver.mask_money;
-- silver.customers and silver.heats_coils intentionally remain unmasked for
-- OLTP serve-down; analytical masking is retained on the gold layer.
ALTER MATERIALIZED VIEW `${catalog}`.silver.claims_history ALTER COLUMN customer_id SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.silver.claims_history ALTER COLUMN claimed_freight SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.silver.adjudications_history ALTER COLUMN claimed_amount SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.silver.adjudications_history ALTER COLUMN approved_amount SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.gold.claims_history ALTER COLUMN customer_id SET MASK `${catalog}`.silver.mask_customer;
ALTER MATERIALIZED VIEW `${catalog}`.gold.claims_history ALTER COLUMN claimed_freight SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.gold.adjudications_history ALTER COLUMN claimed_amount SET MASK `${catalog}`.silver.mask_money;
ALTER MATERIALIZED VIEW `${catalog}`.gold.adjudications_history ALTER COLUMN approved_amount SET MASK `${catalog}`.silver.mask_money;
-- App/agent service-principal grants. These are REAL GRANT statements (not comments).
-- run.py govern SUBSTITUTES ${app_principal} / ${agent_principal} from --app-principal /
-- --agent-principal, the APP_PRINCIPAL / AGENT_PRINCIPAL env vars, or a `governance:` config
-- block; once substituted the grant EXECUTES. A statement is skipped ONLY while its principal
-- is genuinely unset, so this never fails against a not-yet-created SP.
--
-- Agent-principal object->grant chain. The authorities now run IN-PROCESS (pure
-- authorities.py, no UC functions, no warehouse), and the runtime adapter reads ALL
-- four inputs over the Lakebase psycopg (5432) path, governed through the
-- `fe_bar_operational` catalog. So the chain is entirely on `fe_bar_operational`:
--   USE CATALOG  `fe_bar_operational`
--   USE SCHEMA   `fe_bar_operational`.public
--   SELECT       `fe_bar_operational`.public.spec_params        (fetch_spec_params)
--   SELECT       `fe_bar_operational`.public.warranty_terms     (fetch_warranty_terms)
--   USE SCHEMA   `fe_bar_operational`.reference
--   SELECT       `fe_bar_operational`.reference.heats_coils     (fetch_measured: dims + spec_id)
--   SELECT       `fe_bar_operational`.reference.mill_test_certs (fetch_measured: MTC)
-- No UC-function EXECUTE grants remain (the functions are retired); no UC silver
-- grant is needed by the agent (it no longer reads UC silver on the decision path).
GRANT USE CATALOG ON CATALOG `${catalog}` TO `${app_principal}`;
GRANT USE SCHEMA ON SCHEMA `${catalog}`.gold TO `${app_principal}`;
GRANT SELECT ON TABLE `${catalog}`.gold.claims_history TO `${app_principal}`;
GRANT USE CATALOG ON CATALOG `fe_bar_operational` TO `${agent_principal}`;
GRANT USE SCHEMA ON SCHEMA `fe_bar_operational`.public TO `${agent_principal}`;
GRANT SELECT ON TABLE `fe_bar_operational`.public.spec_params TO `${agent_principal}`;
GRANT SELECT ON TABLE `fe_bar_operational`.public.warranty_terms TO `${agent_principal}`;
GRANT USE SCHEMA ON SCHEMA `fe_bar_operational`.reference TO `${agent_principal}`;
GRANT SELECT ON TABLE `fe_bar_operational`.reference.heats_coils TO `${agent_principal}`;
GRANT SELECT ON TABLE `fe_bar_operational`.reference.mill_test_certs TO `${agent_principal}`;
-- SP unmasking entitlement is intentionally deferred; do not add SPs to human roles.
