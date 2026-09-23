"""govern renders real principal grants: substituted when supplied, skipped only when unset."""

from pathlib import Path

import pytest

from run import render_governance

GOV = (Path(__file__).resolve().parents[1] / "governance.sql").read_text()
CATALOG = "fe-bar-ir"


def test_principal_grants_skipped_only_when_unset():
    executable, skipped = render_governance(GOV, CATALOG)
    # the SP grants carry an unresolved placeholder and are skipped...
    assert skipped and all("${" in s for s in skipped)
    assert any("${agent_principal}" in s for s in skipped)
    # ...but the human-role grants/masks still execute, with no leftover placeholders.
    assert all("${" not in s for s in executable)
    assert any("TO `adjuster`" in s for s in executable)


def test_principal_grants_execute_when_supplied():
    executable, skipped = render_governance(
        GOV, CATALOG, app_principal="app-sp-123", agent_principal="agent-sp-456"
    )
    assert skipped == []  # nothing left unresolved
    # The authorities run in-process now: NO UC-function EXECUTE grants remain, and the
    # agent needs no UC silver grant on the decision path.
    assert not any("EXECUTE ON FUNCTION" in s for s in executable)
    assert not any("`fe-bar-ir`.silver" in s and "agent-sp-456" in s for s in executable)
    # The agent's grant chain matches the psycopg path: everything on `fe_bar_operational`.
    assert any(
        "USE CATALOG ON CATALOG `fe_bar_operational` TO `agent-sp-456`" in s for s in executable
    )
    for schema in ("public", "reference"):
        assert any(
            f"USE SCHEMA ON SCHEMA `fe_bar_operational`.{schema} TO `agent-sp-456`" in s
            for s in executable
        )
    # public policy params (fetch_spec_params / fetch_warranty_terms)
    for tbl in ("spec_params", "warranty_terms"):
        assert any(
            f"SELECT ON TABLE `fe_bar_operational`.public.{tbl} TO `agent-sp-456`" in s
            for s in executable
        )
    # reference-schema synced tables the runtime reads directly (fetch_measured)
    for tbl in ("heats_coils", "mill_test_certs"):
        assert any(
            f"SELECT ON TABLE `fe_bar_operational`.reference.{tbl} TO `agent-sp-456`" in s
            for s in executable
        )
    # app principal grants
    assert any(
        "SELECT ON TABLE `fe-bar-ir`.gold.claims_history TO `app-sp-123`" in s for s in executable
    )


@pytest.mark.parametrize("bad", ["a`b", "a; DROP", "a\nb", "a'b", "a b"])
def test_unsafe_principal_rejected(bad):
    with pytest.raises(ValueError):
        render_governance(GOV, CATALOG, agent_principal=bad)
