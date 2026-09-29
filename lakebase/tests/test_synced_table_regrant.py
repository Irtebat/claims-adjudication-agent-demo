"""Recreating synced tables must auto re-grant SELECT to the documented consumers."""

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CREATE_SH = (SCRIPTS / "create_synced_tables.sh").read_text()
REGRANT_PY = (SCRIPTS / "regrant_synced_table_selects.py").read_text()

# Documented consumer ids: app SP -> docs/evidence/app-deploy/grants.sql,
# serving SP -> docs/evidence/serving-endpoint/README.md.
APP_SP = "d5309ee7-a8ea-499f-99d4-4ccbd8369d93"
SERVING_SP = "47643eb1-dbd5-40a6-a51d-5da6b8e2da7a"


def test_create_script_invokes_regrant_after_creating_tables():
    # The re-grant must run after the create_sync calls so a delete+recreate re-sync
    # does not leave the app-SP without SELECT (which 500'd the cockpit live).
    assert "regrant_synced_table_selects.py" in CREATE_SH
    assert CREATE_SH.index("create_sync customer_heat_risk") < CREATE_SH.index(
        "regrant_synced_table_selects.py"
    )


def test_regrant_covers_customer_heat_risk_select():
    assert "GRANT SELECT" in REGRANT_PY
    assert "customer_heat_risk" in REGRANT_PY


def test_regrant_targets_app_and_serving_principals():
    for principal in (APP_SP, SERVING_SP):
        assert principal in REGRANT_PY
        assert principal in CREATE_SH


def test_regrant_is_select_only_never_write_or_ownership():
    # Advisory/read consumers only: no write or ownership grants leak in here.
    for forbidden in ("INSERT", "UPDATE", "DELETE", "OWNER", "ALL PRIVILEGES"):
        assert forbidden not in REGRANT_PY
