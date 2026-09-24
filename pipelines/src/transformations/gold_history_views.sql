-- Current-state names are explicit; *_history views retain the complete SCD2 timeline.
CREATE VIEW `${claims.catalog}`.gold.claims_current AS
SELECT *
FROM `${claims.catalog}`.silver.claims_history
WHERE __END_AT IS NULL;

CREATE VIEW `${claims.catalog}`.gold.adjudications_current AS
SELECT *
FROM `${claims.catalog}`.silver.adjudications_history
WHERE __END_AT IS NULL;

CREATE VIEW `${claims.catalog}`.gold.claims_history AS
SELECT * FROM `${claims.catalog}`.silver.claims_history;

CREATE VIEW `${claims.catalog}`.gold.adjudications_history AS
SELECT * FROM `${claims.catalog}`.silver.adjudications_history;
