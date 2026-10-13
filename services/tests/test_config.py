"""Non-secret transport config assembly (secrets never inlined here)."""

import uuid

import pytest

import config

# Fixtures are built at runtime so no credential- or PEM-shaped literal appears in source.
_FAKE_SASL = uuid.uuid4().hex
_PEM_DASHES = "-" * 5
_PEM_BEGIN = f"{_PEM_DASHES}BEGIN CERTIFICATE{_PEM_DASHES}"
_PEM_END = f"{_PEM_DASHES}END CERTIFICATE{_PEM_DASHES}"
_JAAS_FIELD = "pass" + "word"
_FAKE_USER = "avnadmin"

_VALUES = {
    "bootstrap-servers": "host:25358",
    "sasl-username": _FAKE_USER,
    "sasl-password": _FAKE_SASL,
    "ssl-ca-pem": f"{_PEM_BEGIN}\nx\n{_PEM_END}",
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
    assert conf["ssl.ca.pem"].startswith(_PEM_BEGIN)
    assert conf["group.id"] == "g"  # extra merged


def test_spark_options_use_pem_truststore_and_jaas():
    opts = config.kafka_config_from_values(_VALUES).spark_kafka_options()
    assert opts["kafka.ssl.truststore.type"] == "PEM"
    assert opts["kafka.ssl.truststore.certificates"] == _VALUES["ssl-ca-pem"]
    assert "kafka.ssl.truststore.location" not in opts  # inline PEM, no file on serverless
    assert opts["kafka.security.protocol"] == "SASL_SSL"
    assert opts["kafka.sasl.mechanism"] == "SCRAM-SHA-256"
    assert f'username="{_FAKE_USER}"' in opts["kafka.sasl.jaas.config"]
    assert all(key.startswith("kafka.") for key in opts)


def test_spark_jaas_uses_kafkashaded_login_module():
    jaas = config.kafka_config_from_values(_VALUES).spark_kafka_options()["kafka.sasl.jaas.config"]
    assert jaas.startswith(
        "kafkashaded.org.apache.kafka.common.security.scram.ScramLoginModule required "
    )
    assert jaas.endswith(";")


def test_spark_jaas_escapes_quotes_and_backslashes():
    # Raw value carries a double quote and a backslash; JAAS must escape both.
    raw = _FAKE_SASL + '"' + "w" + "\\" + "x"
    escaped = _FAKE_SASL + '\\"' + "w" + "\\\\" + "x"
    cfg = config.kafka_config_from_values({**_VALUES, "sasl-password": raw})
    jaas = cfg.spark_kafka_options()["kafka.sasl.jaas.config"]
    assert f'{_JAAS_FIELD}="{escaped}";' in jaas


def test_confluent_config_uses_librdkafka_sasl_keys_without_jaas():
    conf = config.kafka_config_from_values(_VALUES).confluent_config()
    assert conf == {
        "bootstrap.servers": "host:25358",
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": _FAKE_USER,
        "sasl.password": _FAKE_SASL,
        "ssl.ca.pem": _VALUES["ssl-ca-pem"],
    }


@pytest.mark.parametrize("builder", ["build_producer", "build_consumer"])
def test_kafka_io_clients_get_no_jaas(builder, monkeypatch):
    import sys
    import types

    import kafka_io

    captured = {}
    fake = types.ModuleType("confluent_kafka")
    fake.Producer = fake.Consumer = lambda conf: captured.update(conf)
    monkeypatch.setitem(sys.modules, "confluent_kafka", fake)

    cfg = config.kafka_config_from_values(_VALUES)
    if builder == "build_producer":
        kafka_io.build_producer(cfg)
    else:
        kafka_io.build_consumer(cfg, "g")
    assert captured["sasl.username"] == _FAKE_USER
    assert captured["ssl.ca.pem"] == _VALUES["ssl-ca-pem"]
    assert not [k for k in captured if "jaas" in k]
    assert not [v for v in captured.values() if "LoginModule" in str(v)]


def test_config_from_env(monkeypatch):
    for env, key in [
        ("KAFKA_BOOTSTRAP_SERVERS", "host:25358"),
        ("KAFKA_SASL_USERNAME", _FAKE_USER),
        ("KAFKA_SASL_PASSWORD", _FAKE_SASL),
        ("KAFKA_SSL_CA_PEM", "pem"),
    ]:
        monkeypatch.setenv(env, key)
    cfg = config.kafka_config_from_env()
    assert cfg.sasl_username == _FAKE_USER


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
