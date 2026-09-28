"""Runtime configuration for the event-backbone services (non-secret + secret refs).

Secrets are never inlined. On Databricks the notebooks resolve them with
``dbutils.secrets.get`` and hand the values to :func:`kafka_config_from_values`;
locally they come from environment variables. The dataclasses are plain data so the
config assembly is unit-testable without Databricks.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Aiven fe-bar-kafka transport constants (non-secret).
SASL_MECHANISM = "SCRAM-SHA-256"
SECURITY_PROTOCOL = "SASL_SSL"

# Secret scopes.
KAFKA_SCOPE = "fe-bar-aiven-kafka"
APP_SP_SCOPE = "claims-agent"

# Kafka secret keys (populate with `databricks secrets put-secret ...`).
KAFKA_KEYS = ("bootstrap-servers", "sasl-username", "sasl-password", "ssl-ca-pem")

# Topics.
TOPIC_CLAIM_SUBMITTED = "claim.submitted"
TOPIC_CLAIM_ADJUDICATED = "claim.adjudicated"

# Serving endpoint (Wave 6).
SERVING_ENDPOINT = "agents_fe-bar-ir-default-claims_adjudication_agent"

# Lakebase (Wave 5/6).
LAKEBASE_ENDPOINT = "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
LAKEBASE_DATABASE = "databricks_postgres"


@dataclass
class KafkaConfig:
    bootstrap_servers: str
    sasl_username: str
    sasl_password: str
    ssl_ca_pem: str
    mechanism: str = SASL_MECHANISM
    protocol: str = SECURITY_PROTOCOL

    def confluent_config(self, extra: dict | None = None) -> dict:
        """librdkafka (confluent-kafka) client config; CA passed inline as PEM."""
        conf = {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": self.protocol,
            "sasl.mechanism": self.mechanism,
            "sasl.username": self.sasl_username,
            "sasl.password": self.sasl_password,
            "ssl.ca.pem": self.ssl_ca_pem,
        }
        if extra:
            conf.update(extra)
        return conf

    def spark_kafka_options(self, prefix: str = "kafka.") -> dict:
        """Spark structured-streaming Kafka options; CA passed inline as a PEM truststore."""
        jaas = (
            "org.apache.kafka.common.security.scram.ScramLoginModule required "
            f'username="{self.sasl_username}" password="{self.sasl_password}";'
        )
        return {
            f"{prefix}bootstrap.servers": self.bootstrap_servers,
            f"{prefix}security.protocol": self.protocol,
            f"{prefix}sasl.mechanism": self.mechanism,
            f"{prefix}sasl.jaas.config": jaas,
            f"{prefix}ssl.truststore.type": "PEM",
            f"{prefix}ssl.truststore.certificates": self.ssl_ca_pem,
        }


def kafka_config_from_values(values: dict) -> KafkaConfig:
    """Build a KafkaConfig from a {secret-key: value} mapping (pure/testable)."""
    missing = [k for k in KAFKA_KEYS if not values.get(k)]
    if missing:
        raise ValueError(f"Missing Kafka secret keys: {missing}")
    return KafkaConfig(
        bootstrap_servers=values["bootstrap-servers"],
        sasl_username=values["sasl-username"],
        sasl_password=values["sasl-password"],
        ssl_ca_pem=values["ssl-ca-pem"],
    )


def kafka_config_from_secrets(dbutils) -> KafkaConfig:
    """Read the Kafka secret scope on Databricks and build a KafkaConfig."""
    values = {key: dbutils.secrets.get(scope=KAFKA_SCOPE, key=key) for key in KAFKA_KEYS}
    return kafka_config_from_values(values)


def kafka_config_from_env() -> KafkaConfig:
    """Local/off-platform fallback from environment variables."""
    return kafka_config_from_values(
        {
            "bootstrap-servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", ""),
            "sasl-username": os.environ.get("KAFKA_SASL_USERNAME", ""),
            "sasl-password": os.environ.get("KAFKA_SASL_PASSWORD", ""),
            "ssl-ca-pem": os.environ.get("KAFKA_SSL_CA_PEM", ""),
        }
    )


@dataclass
class AppSpEnv:
    """Env var names the serving model + services use to auth AS the app SP."""

    keys: dict = field(
        default_factory=lambda: {
            "APP_SP_CLIENT_ID": "app-sp-client-id",
            "APP_SP_CLIENT_SECRET": "app-sp-client-secret",
            "LAKEBASE_DB_USER": "lakebase-db-user",
        }
    )

    def export_from_secrets(self, dbutils) -> None:
        """Populate os.environ from the claims-agent scope so db.connect() auths as the SP."""
        for env_name, key in self.keys.items():
            os.environ[env_name] = dbutils.secrets.get(scope=APP_SP_SCOPE, key=key)
