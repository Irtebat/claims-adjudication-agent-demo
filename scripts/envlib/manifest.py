"""Strict, dependency-light environment manifest and runtime-state helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, TypeVar, get_args, get_origin, get_type_hints

import yaml


class ManifestError(ValueError):
    """A manifest, state file, or template violates its contract."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_HOST = re.compile(r"^https://[A-Za-z0-9.-]+$")
_S3 = re.compile(r"^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](?:/[A-Za-z0-9._/-]+)?/?$")
_ARN = re.compile(r"^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$")
_WAREHOUSE_ID = re.compile(r"^[0-9a-f]{16}$")
_DISCOVERED_ID = re.compile(r"^(?:[0-9a-f]{32}|[0-9a-f-]{36}|[A-Za-z0-9_-]{8,128})$")
_TOKEN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*}}")


def _identifier(value: str, path: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ManifestError(f"{path} contains unsafe characters: {value!r}")


def _nonempty(value: str, path: str) -> None:
    if not value or any(c in value for c in "\n\r\x00"):
        raise ManifestError(f"{path} must be a non-empty single-line string")


@dataclass(frozen=True)
class Workspace:
    profile: str
    host: str
    account_profile: str | None
    release_environment: bool

    def validate(self) -> None:
        _identifier(self.profile, "workspace.profile")
        if self.account_profile is not None:
            _identifier(self.account_profile, "workspace.account_profile")
        if not _HOST.fullmatch(self.host) or ".." in self.host:
            raise ManifestError("workspace.host must be a safe https host with no path")


@dataclass(frozen=True)
class Schemas:
    bronze: str
    silver: str
    gold: str
    cdf: str
    eval: str
    default: str

    def validate(self) -> None:
        for item in fields(self):
            _identifier(getattr(self, item.name), f"uc.schemas.{item.name}")


@dataclass(frozen=True)
class UnityCatalog:
    catalog: str
    managed_storage_root: str
    storage_credential: str
    external_location: str
    iam_role_arn: str
    schemas: Schemas

    def validate(self) -> None:
        _identifier(self.catalog, "uc.catalog")
        _identifier(self.storage_credential, "uc.storage_credential")
        _identifier(self.external_location, "uc.external_location")
        if not _S3.fullmatch(self.managed_storage_root) or ".." in self.managed_storage_root:
            raise ManifestError("uc.managed_storage_root must be a safe s3:// URI")
        if not _ARN.fullmatch(self.iam_role_arn):
            raise ManifestError("uc.iam_role_arn must be an AWS IAM role ARN")
        self.schemas.validate()


@dataclass(frozen=True)
class Warehouse:
    id: str | None
    name: str

    def validate(self) -> None:
        if self.id is not None and not _WAREHOUSE_ID.fullmatch(self.id):
            raise ManifestError("warehouse.id must be 16 lowercase hex characters")
        _nonempty(self.name, "warehouse.name")


@dataclass(frozen=True)
class Lakebase:
    project: str
    branch: str
    endpoint: str
    database: str
    resource_database: str
    mirror_catalog: str
    public_schema: str
    reference_schema: str

    @property
    def project_path(self) -> str:
        return f"projects/{self.project}"

    @property
    def branch_path(self) -> str:
        return f"{self.project_path}/branches/{self.branch}"

    @property
    def endpoint_path(self) -> str:
        return f"{self.branch_path}/endpoints/{self.endpoint}"

    @property
    def database_path(self) -> str:
        return f"{self.branch_path}/databases/{self.resource_database}"

    def validate(self) -> None:
        for item in fields(self):
            _identifier(getattr(self, item.name), f"lakebase.{item.name}")


@dataclass(frozen=True)
class Models:
    registered_model: str
    serving_endpoint: str
    model_service_schema: str
    reasoning_service: str
    reasoning_destination: str
    embedding_service: str
    embedding_destination: str
    experiment: str

    def validate(self) -> None:
        _identifier(self.registered_model, "models.registered_model")
        for key in ("serving_endpoint", "model_service_schema"):
            _identifier(getattr(self, key), f"models.{key}")
        for key in ("reasoning_service", "embedding_service"):
            service = getattr(self, key)
            if len(service.split(".")) != 3:
                raise ManifestError(f"models.{key} must be catalog.schema.service")
            for part in service.split("."):
                _identifier(part, f"models.{key}")
        for key in ("reasoning_destination", "embedding_destination", "experiment"):
            _nonempty(getattr(self, key), f"models.{key}")


@dataclass(frozen=True)
class Genie:
    operational_title: str
    business_title: str

    def validate(self) -> None:
        _nonempty(self.operational_title, "genie.operational_title")
        _nonempty(self.business_title, "genie.business_title")


@dataclass(frozen=True)
class Dashboard:
    display_name: str

    def validate(self) -> None:
        _nonempty(self.display_name, "dashboard.display_name")


@dataclass(frozen=True)
class App:
    name: str
    adjuster_users: list[str]
    adjuster_groups: list[str]
    business_users: list[str]
    business_groups: list[str]

    def validate(self) -> None:
        _identifier(self.name, "app.name")
        for key in ("adjuster_users", "adjuster_groups", "business_users", "business_groups"):
            for value in getattr(self, key):
                _nonempty(value, f"app.{key}")


@dataclass(frozen=True)
class Secrets:
    agent_scope: str
    kafka_scope: str

    def validate(self) -> None:
        _identifier(self.agent_scope, "secrets.agent_scope")
        _identifier(self.kafka_scope, "secrets.kafka_scope")


@dataclass(frozen=True)
class KafkaTopics:
    claim_submitted: str
    claim_adjudicated: str

    def validate(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
                raise ManifestError(f"kafka.topics.{item.name} is unsafe")


@dataclass(frozen=True)
class Kafka:
    bootstrap_servers: str
    topics: KafkaTopics

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9.-]+:[1-9][0-9]{0,4}", self.bootstrap_servers):
            raise ManifestError("kafka.bootstrap_servers must be host:port")
        self.topics.validate()


@dataclass(frozen=True)
class Manifest:
    workspace: Workspace
    uc: UnityCatalog
    warehouse: Warehouse
    lakebase: Lakebase
    models: Models
    genie: Genie
    dashboard: Dashboard
    app: App
    secrets: Secrets
    kafka: Kafka

    def validate(self) -> None:
        for item in fields(self):
            getattr(self, item.name).validate()
        for key in ("reasoning_service", "embedding_service"):
            if getattr(self.models, key).split(".", 1)[0] != self.uc.catalog:
                raise ManifestError(f"models.{key} must use uc.catalog")

    @property
    def registered_model_fqn(self) -> str:
        return (
            f"{self.uc.catalog}.{self.uc.schemas.default}.{self.models.registered_model}"
        )

    def resolved(self) -> dict[str, Any]:
        data = asdict(self)
        data["lakebase"].update(
            project_path=self.lakebase.project_path,
            branch_path=self.lakebase.branch_path,
            endpoint_path=self.lakebase.endpoint_path,
            database_path=self.lakebase.database_path,
        )
        data["models"]["registered_model_fqn"] = self.registered_model_fqn
        return data

    def to_bundle_vars(self, bundle_name: str) -> dict[str, str]:
        common = {"databricks_profile": self.workspace.profile, "catalog": self.uc.catalog}
        cdf_claims = f"{self.uc.catalog}.{self.uc.schemas.cdf}.lb_claims_history"
        mappings: dict[str, dict[str, str | None]] = {
            "pipelines": {
                **common,
                "cdf_claims_table": cdf_claims,
                "cdf_adjudications_table": (
                    f"{self.uc.catalog}.{self.uc.schemas.cdf}.lb_adjudications_history"
                ),
                "cdf_decision_records_table": (
                    f"{self.uc.catalog}.{self.uc.schemas.cdf}.lb_adjudication_decision_records_history"
                ),
                "warehouse_id": self.warehouse.id,
            },
            "lakebase": {
                **common,
                "endpoint": self.lakebase.endpoint_path,
                "postgres_database": self.lakebase.database,
            },
            "agent": common,
            "services": {
                **common,
                "endpoint": self.lakebase.endpoint_path,
                "postgres_database": self.lakebase.database,
                "cdf_claims_table": cdf_claims,
                "producer_checkpoint": (
                    f"/Volumes/{self.uc.catalog}/{self.uc.schemas.bronze}/"
                    "raw_landing/_kafka_checkpoints/claim_submitted"
                ),
            },
            "demo": {
                **common,
                "endpoint": self.lakebase.endpoint_path,
                "postgres_database": self.lakebase.database,
            },
            "dashboards": {
                **common,
                "schema": self.uc.schemas.gold,
                "warehouse_id": self.warehouse.id,
            },
            "app": {
                "warehouse_id": self.warehouse.id,
                "genie_space_name": self.genie.operational_title,
                "business_genie_space_name": self.genie.business_title,
                "postgres_project": self.lakebase.project_path,
                "postgres_branch": self.lakebase.branch_path,
                "postgres_database": self.lakebase.database_path,
            },
        }
        if bundle_name not in mappings:
            raise ManifestError(f"unknown bundle: {bundle_name}")
        return {key: value for key, value in mappings[bundle_name].items() if value is not None}

    def to_env(self) -> dict[str, str]:
        values = {
            "DATABRICKS_CONFIG_PROFILE": self.workspace.profile,
            "DATABRICKS_HOST": self.workspace.host,
            "DATABRICKS_CATALOG": self.uc.catalog,
            "DATABRICKS_WAREHOUSE_ID": self.warehouse.id,
            "LAKEBASE_PROJECT": self.lakebase.project_path,
            "LAKEBASE_BRANCH": self.lakebase.branch_path,
            "LAKEBASE_ENDPOINT": self.lakebase.endpoint_path,
            "LAKEBASE_DATABASE": self.lakebase.database,
            "LAKEBASE_MIRROR_CATALOG": self.lakebase.mirror_catalog,
            "REGISTERED_MODEL": self.registered_model_fqn,
            "SERVING_ENDPOINT": self.models.serving_endpoint,
            "REASONING_MODEL_SERVICE": self.models.reasoning_service,
            "EMBEDDING_MODEL_SERVICE": self.models.embedding_service,
            "MLFLOW_EXPERIMENT": self.models.experiment,
            "AGENT_SECRET_SCOPE": self.secrets.agent_scope,
            "KAFKA_SECRET_SCOPE": self.secrets.kafka_scope,
            "KAFKA_BOOTSTRAP_SERVERS": self.kafka.bootstrap_servers,
            "ADJUSTER_USERS": ",".join(self.app.adjuster_users),
            "ADJUSTER_GROUPS": ",".join(self.app.adjuster_groups),
            "BUSINESS_USERS": ",".join(self.app.business_users),
            "BUSINESS_GROUPS": ",".join(self.app.business_groups),
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class RuntimeState:
    operational_genie_id: str | None = None
    business_genie_id: str | None = None
    dashboard_id: str | None = None
    app_service_principal_id: str | None = None
    serving_service_principal_id: str | None = None
    warehouse_id: str | None = None
    model_version: int | None = None

    def validate(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if value is None:
                continue
            if item.name == "model_version":
                if not isinstance(value, int) or value < 1:
                    raise ManifestError("state.model_version must be a positive integer")
            elif not _DISCOVERED_ID.fullmatch(value):
                raise ManifestError(f"state.{item.name} is not an id")


T = TypeVar("T")


def _construct(cls: type[T], data: Any, path: str) -> T:
    if not isinstance(data, dict):
        raise ManifestError(f"{path or 'manifest'} must be a mapping")
    hints = get_type_hints(cls)
    expected = {item.name for item in fields(cls)}
    unknown = set(data) - expected
    missing = expected - set(data)
    if unknown:
        raise ManifestError(f"{path or 'manifest'} has unknown keys: {sorted(unknown)}")
    if missing:
        raise ManifestError(f"{path or 'manifest'} is missing keys: {sorted(missing)}")
    values: dict[str, Any] = {}
    for item in fields(cls):
        value = data[item.name]
        hint = hints[item.name]
        if is_dataclass(hint):
            value = _construct(hint, value, f"{path}.{item.name}".strip("."))
        else:
            origin = get_origin(hint)
            allowed = get_args(hint) if origin is not None else (hint,)
            if origin is list:
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    raise ManifestError(f"{path}.{item.name} must be a list of strings")
            elif value is None:
                if type(None) not in allowed:
                    raise ManifestError(f"{path}.{item.name} may not be null")
            elif bool in allowed:
                if type(value) is not bool:
                    raise ManifestError(f"{path}.{item.name} must be boolean")
            elif int in allowed:
                if type(value) is not int:
                    raise ManifestError(f"{path}.{item.name} must be integer")
            elif str in allowed and not isinstance(value, str):
                raise ManifestError(f"{path}.{item.name} must be string")
        values[item.name] = value
    return cls(**values)


def load_manifest(path: str | Path) -> Manifest:
    manifest = _construct(Manifest, yaml.safe_load(Path(path).read_text()), "")
    manifest.validate()
    return manifest


def load_state(path: str | Path) -> RuntimeState:
    state_path = Path(path)
    if not state_path.exists():
        return RuntimeState()
    state = _construct(RuntimeState, json.loads(state_path.read_text()), "state")
    state.validate()
    return state


def save_state(path: str | Path, state: RuntimeState) -> None:
    state.validate()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(asdict(state), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def render(text: str, manifest: Manifest, state: RuntimeState | None = None) -> str:
    context = manifest.resolved()
    context["state"] = asdict(state or RuntimeState())

    def replace(match: re.Match[str]) -> str:
        value: Any = context
        path = match.group(1)
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value or value[part] is None:
                raise ManifestError(f"template value is missing: {path}")
            value = value[part]
        if isinstance(value, (dict, list)):
            raise ManifestError(f"template value is not scalar: {path}")
        return str(value).lower() if isinstance(value, bool) else str(value)

    return _TOKEN.sub(replace, text)
