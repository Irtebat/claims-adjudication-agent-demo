"""Non-secret transport config assembly (secrets never inlined here)."""

import pytest

import config

_VALUES = {
    "bootstrap-servers": "host:25358",
    "sasl-username": "avnadmin",
    "sasl-password": "pw",
    "ssl-ca-pem": "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----",
}


def test_kafka_config_from_values_ok():
    cfg = config.kafka_config_from_values(_VALUES)
    assert cfg.bootstrap_servers == "host:25358"
    assert cfg.mechanism == "SCRAM-SHA-256"
    assert cfg.protocol == "SASL_SSL"


def test_missing_keys_raise():
    with pytest.raises(ValueError, match="Missing Kafka secret keys"):
        config.kafka_config_from_values({"bootstrap-servers": "h"})


def test_confluent_config_carries_ca_pem_inline():
    conf = config.kafka_config_from_values(_VALUES).confluent_config({"group.id": "g"})
    assert conf["security.protocol"] == "SASL_SSL"
    assert conf["sasl.mechanism"] == "SCRAM-SHA-256"
    assert conf["ssl.ca.pem"].startswith("-----BEGIN CERTIFICATE-----")
    assert conf["group.id"] == "g"  # extra merged


def test_spark_options_use_pem_truststore_and_jaas():
    opts = config.kafka_config_from_values(_VALUES).spark_kafka_options()
    assert opts["kafka.ssl.truststore.type"] == "PEM"
    assert opts["kafka.ssl.truststore.certificates"].startswith("-----BEGIN")
    assert "ScramLoginModule required" in opts["kafka.sasl.jaas.config"]
    assert 'username="avnadmin"' in opts["kafka.sasl.jaas.config"]


def test_config_from_env(monkeypatch):
    for env, key in [
        ("KAFKA_BOOTSTRAP_SERVERS", "host:25358"),
        ("KAFKA_SASL_USERNAME", "avnadmin"),
        ("KAFKA_SASL_PASSWORD", "pw"),
        ("KAFKA_SSL_CA_PEM", "pem"),
    ]:
        monkeypatch.setenv(env, key)
    cfg = config.kafka_config_from_env()
    assert cfg.sasl_username == "avnadmin"


def test_app_sp_env_exported_from_secrets(monkeypatch):
    class _DbUtils:
        class secrets:
            @staticmethod
            def get(scope, key):
                return f"{scope}:{key}"

    for name in ("APP_SP_CLIENT_ID", "APP_SP_CLIENT_SECRET", "LAKEBASE_DB_USER"):
        monkeypatch.delenv(name, raising=False)
    config.AppSpEnv().export_from_secrets(_DbUtils)
    import os

    assert os.environ["APP_SP_CLIENT_ID"] == "claims-agent:app-sp-client-id"
    assert os.environ["LAKEBASE_DB_USER"] == "claims-agent:lakebase-db-user"
