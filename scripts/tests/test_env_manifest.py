from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from envlib import (
    ManifestError,
    RuntimeState,
    load_manifest,
    load_state,
    render,
    save_state,
)

ROOT = Path(__file__).resolve().parents[2]
ENVS = ROOT / "config" / "envs"


@pytest.fixture
def manifest():
    return load_manifest(ENVS / "fe-bar-ir-2026.yaml")


@pytest.mark.parametrize("name", ["fe-bar-ir-2026.yaml", "example.yaml"])
def test_manifests_load(name):
    assert load_manifest(ENVS / name).workspace.profile


def test_rejects_bad_identifier(tmp_path):
    data = yaml.safe_load((ENVS / "example.yaml").read_text())
    data["uc"]["catalog"] = "bad; DROP CATALOG"
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError, match="unsafe"):
        load_manifest(path)


@pytest.mark.parametrize("uri", ["https://bucket/path", "s3://Bad_Bucket", "s3://ok/../escape"])
def test_rejects_bad_s3_uri(tmp_path, uri):
    data = yaml.safe_load((ENVS / "example.yaml").read_text())
    data["uc"]["managed_storage_root"] = uri
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ManifestError, match="s3"):
        load_manifest(path)


def test_derived_properties(manifest):
    assert manifest.lakebase.endpoint_path == (
        "projects/fe-bar-operational-plane/branches/production/endpoints/primary"
    )
    assert manifest.lakebase.database_path.endswith("/databases/databricks-postgres")
    assert manifest.registered_model_fqn == "fe-bar-ir.default.claims_adjudication_agent"


def test_render_substitutes_manifest_and_state(manifest):
    state = RuntimeState(dashboard_id="01f1c269ddc4143aa9bf584f267bd10b")
    text = "{{ uc.catalog }}.{{ uc.schemas.gold }} / {{ state.dashboard_id }}"
    assert render(text, manifest, state) == (
        "fe-bar-ir.gold / 01f1c269ddc4143aa9bf584f267bd10b"
    )


def test_render_rejects_missing(manifest):
    with pytest.raises(ManifestError, match="missing"):
        render("{{ state.dashboard_id }}", manifest)
    with pytest.raises(ManifestError, match="missing"):
        render("{{ uc.not_a_field }}", manifest)


def test_state_round_trip_is_complete(manifest, tmp_path):
    path = tmp_path / ".deploy" / "test" / "state.json"
    state = RuntimeState(warehouse_id=manifest.warehouse.id, model_version=7)
    save_state(path, state)
    assert load_state(path) == state
    assert json.loads(path.read_text())["model_version"] == 7


# Variables which tune workload size/behavior rather than identify an environment.
NON_ENVIRONMENT_VARIABLES = {
    "pipelines": {"claim_count", "seed", "warranty_schedule"},
    "services": {"sp_role", "worker_max_messages", "relay_max_events", "consumer_max_messages"},
    "demo": {"count", "seed", "mode"},
    # Discovered IDs live in .deploy/<env>/state.json and are applied by the future driver.
    "app": {"genie_space_id", "business_genie_space_id"},
}


@pytest.mark.parametrize(
    ("bundle", "path"),
    [
        ("pipelines", "pipelines/databricks.yml"),
        ("lakebase", "lakebase/databricks.yml"),
        ("agent", "agent/databricks.yml"),
        ("services", "services/databricks.yml"),
        ("demo", "demo/databricks.yml"),
        ("dashboards", "dashboards/databricks.yml"),
        ("app", "app/databricks.yml"),
    ],
)
def test_bundle_vars_cover_declared_environment_variables(manifest, bundle, path):
    declared = set(yaml.safe_load((ROOT / path).read_text()).get("variables", {}))
    expected = declared - NON_ENVIRONMENT_VARIABLES.get(bundle, set())
    assert set(manifest.to_bundle_vars(bundle)) == expected


def test_optional_warehouse_id_is_omitted_from_overrides():
    manifest = load_manifest(ENVS / "example.yaml")
    assert "warehouse_id" not in manifest.to_bundle_vars("dashboards")


def test_validation_rejects_unsafe_model_name(manifest):
    broken = replace(manifest, models=replace(manifest.models, registered_model="bad.model"))
    with pytest.raises(ManifestError, match="unsafe"):
        broken.validate()
