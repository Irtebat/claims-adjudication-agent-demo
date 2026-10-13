"""Environment templates render cleanly and reproduce the committed fe-bar-ir-2026 artifacts.

Stdlib-only: a minimal `{{ dotted.path }}` renderer stands in for the env manifest renderer.
"""

import json
import re
from pathlib import Path

import pytest

from run import render_governance

REPO = Path(__file__).resolve().parents[2]
TOKEN = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*\}\}")

# Today's values: rendering with these must reproduce the committed artifacts exactly.
CURRENT = {
    "uc.catalog": "fe-bar-ir",
    "uc.schemas.gold": "gold",
    "uc.schemas.silver": "silver",
    "uc.schemas.bronze": "bronze",
    "uc.schemas.cdf": "cdf",
    "state.genie.business_id": "01f1c2698a5418298f81f9e79df576ca",
    "state.dashboard.id": "01f1c269ddc4143aa9bf584f267bd10b",
}
OTHER = {key: f"other_{i}" for i, key in enumerate(CURRENT)}

# Committed rendered artifacts; each has a sibling `<artifact>.tmpl` source.
TEMPLATES = [
    "pipelines/src/metric_views/quality_claims_metrics.sql",
    "dashboards/genie/genie_space.json",
    "dashboards/genie/operational_genie_space.json",
    "dashboards/quality_claims.lvdash.json",
    "app/app.yaml",
]

# Environment literals and known current/legacy ids that must only appear in rendered output.
FORBIDDEN = [
    "fe-bar-ir",
    "fe_bar_operational",
    "01f1c2698a5418298f81f9e79df576ca",
    "01f1c269ca3c1adea7feb9f248ab3445",
    "01f1c269ddc4143aa9bf584f267bd10b",
    "01f1bac20bf6119f84fa99c7ba438ba4",
    "01f1bac220111001a171872b5185e8e6",
    "01f1bb5b9d081378b00a283760825c64",
    "a323b5700ae25d85",
]


def render(text, mapping):
    return TOKEN.sub(lambda m: mapping[m.group(1)], text)


@pytest.mark.parametrize("artifact", TEMPLATES)
def test_template_reproduces_committed_artifact(artifact):
    template = (REPO / f"{artifact}.tmpl").read_text()
    assert TOKEN.search(template), f"{artifact}.tmpl has no tokens"
    rendered = render(template, CURRENT)
    assert not TOKEN.search(rendered) and "{{" not in rendered
    assert rendered == (REPO / artifact).read_text()


@pytest.mark.parametrize("artifact", TEMPLATES)
def test_template_has_no_environment_literals(artifact):
    template = (REPO / f"{artifact}.tmpl").read_text()
    for literal in FORBIDDEN:
        assert literal not in template, f"{literal} hardcoded in {artifact}.tmpl"
    rendered = render(template, OTHER)
    for literal in FORBIDDEN:
        assert literal not in rendered
    if artifact.endswith(".json"):
        json.loads(rendered)


def test_governance_mirror_catalog_is_placeholder():
    sql_text = (REPO / "pipelines/governance.sql").read_text()
    assert "fe_bar_operational" not in sql_text and "fe-bar-ir" not in sql_text
    default, _ = render_governance(sql_text, "fe-bar-ir", agent_principal="agent-sp")
    assert any("CATALOG `fe_bar_operational` TO `agent-sp`" in s for s in default)
    other, skipped = render_governance(
        sql_text, "cat", agent_principal="agent-sp", mirror_catalog="mirror"
    )
    assert any("CATALOG `mirror` TO `agent-sp`" in s for s in other)
    assert not any("fe_bar_operational" in s for s in other + skipped)
