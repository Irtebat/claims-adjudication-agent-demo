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


@pytest.fixture
def full_state(manifest):
    return RuntimeState(
        operational_genie_id="01f1c269ca3c1adea7feb9f248ab3445",
        business_genie_id="01f1c2698a5418298f81f9e79df576ca",
        dashboard_id="01f1c269ddc4143aa9bf584f267bd10b",
        app_service_principal_id="11111111-1111-4111-8111-111111111111",
        serving_service_principal_id="9779e0a0-0dc3-4f60-8746-ae1445b27c6e",
        warehouse_id=manifest.warehouse.id,
        model_version=7,
    )


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
    text = "{{ uc.catalog }}.{{ uc.schemas.gold }} / {{ state.dashboard.id }}"
    assert render(text, manifest, state) == (
        "fe-bar-ir.gold / 01f1c269ddc4143aa9bf584f267bd10b"
    )


def test_render_rejects_missing(manifest):
    with pytest.raises(ManifestError, match="missing"):
        render("{{ state.dashboard.id }}", manifest)
    with pytest.raises(ManifestError, match="missing"):
        render("{{ uc.not_a_field }}", manifest)


def test_render_rejects_malformed_tokens(manifest):
    with pytest.raises(ManifestError, match="malformed"):
        render("{{ uc.catalog }", manifest)
    with pytest.raises(ManifestError, match="malformed"):
        render("uc.catalog }}", manifest)


def test_render_full_template_contract(manifest, full_state):
    tokens = (
        "{{ uc.catalog }}",
        "{{ uc.schemas.bronze }}",
        "{{ uc.schemas.silver }}",
        "{{ uc.schemas.gold }}",
        "{{ uc.schemas.cdf }}",
        "{{ uc.schemas.default }}",
        "{{ lakebase.mirror_catalog }}",
        "{{ lakebase.public_schema }}",
        "{{ lakebase.reference_schema }}",
        "{{ warehouse.id }}",
        "{{ state.genie.operational_id }}",
        "{{ state.genie.business_id }}",
        "{{ state.dashboard.id }}",
    )
    expected = (
        "fe-bar-ir",
        "bronze",
        "silver",
        "gold",
        "cdf",
        "default",
        "fe_bar_operational",
        "public",
        "reference",
        "a323b5700ae25d85",
        "01f1c269ca3c1adea7feb9f248ab3445",
        "01f1c2698a5418298f81f9e79df576ca",
        "01f1c269ddc4143aa9bf584f267bd10b",
    )
    assert render("\n".join(tokens), manifest, full_state) == "\n".join(expected)


def test_all_committed_templates_render_byte_for_byte(manifest, full_state):
    templates = sorted(ROOT.rglob("*.tmpl"))
    if not templates:
        pytest.skip("PR #68 templates are not present on origin/main yet")
    for template in templates:
        sibling = template.with_suffix("")
        assert sibling.is_file(), f"missing rendered sibling for {template.relative_to(ROOT)}"
        rendered = render(template.read_text(), manifest, full_state).encode()
        assert rendered == sibling.read_bytes(), f"render drift for {template.relative_to(ROOT)}"


def test_state_round_trip_is_complete(manifest, tmp_path):
    path = tmp_path / ".deploy" / "test" / "state.json"
    state = RuntimeState(warehouse_id=manifest.warehouse.id, model_version=7)
    save_state(path, state)
    assert load_state(path) == state
    assert json.loads(path.read_text())["model_version"] == 7


# Variables which tune workload size/behavior rather than identify an environment.
NON_ENVIRONMENT_VARIABLES = {
    "pipelines": {"claim_count", "seed", "warranty_schedule"},
    "services": {"worker_max_messages", "relay_max_events", "consumer_max_messages"},
    "demo": {"count", "seed", "mode"},
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
def test_bundle_vars_cover_declared_environment_variables(manifest, full_state, bundle, path):
    declared = set(yaml.safe_load((ROOT / path).read_text()).get("variables", {}))
    expected = declared - NON_ENVIRONMENT_VARIABLES.get(bundle, set())
    assert set(manifest.to_bundle_vars(bundle, full_state)) == expected


def test_optional_warehouse_id_requires_runtime_state():
    manifest = load_manifest(ENVS / "example.yaml")
    with pytest.raises(ManifestError, match="warehouse_id"):
        manifest.to_bundle_vars("dashboards")
    state = RuntimeState(warehouse_id="0123456789abcdef")
    assert manifest.to_bundle_vars("dashboards", state)["warehouse_id"] == state.warehouse_id


def test_services_requires_and_maps_serving_principal(manifest):
    with pytest.raises(ManifestError, match="sp_role"):
        manifest.to_bundle_vars("services")
    state = RuntimeState(serving_service_principal_id="9779e0a0-0dc3-4f60-8746-ae1445b27c6e")
    assert manifest.to_bundle_vars("services", state)["sp_role"] == (
        "9779e0a0-0dc3-4f60-8746-ae1445b27c6e"
    )


def test_validation_rejects_unsafe_model_name(manifest):
    broken = replace(manifest, models=replace(manifest.models, registered_model="bad.model"))
    with pytest.raises(ManifestError, match="unsafe"):
        broken.validate()


def test_validation_rejects_json_unsafe_free_text(manifest):
    broken = replace(manifest, genie=replace(manifest.genie, operational_title='bad " title'))
    with pytest.raises(ManifestError, match="quotes"):
        broken.validate()
