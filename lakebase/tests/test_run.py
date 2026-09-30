import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "run.py"
SPEC = importlib.util.spec_from_file_location("lakebase_run", MODULE_PATH)
lakebase_run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lakebase_run)


def test_setup_and_seed_refuses_before_mutation_when_cdf_exists(monkeypatch):
    calls = []

    def fake_databricks(*parts, **kwargs):
        calls.append(parts)
        return subprocess.CompletedProcess(parts, 0, stdout='[{"name":"cdf/config"}]')

    monkeypatch.setattr(lakebase_run, "databricks", fake_databricks)
    monkeypatch.setattr(sys, "argv", ["run.py", "setup-and-seed"])

    with pytest.raises(RuntimeError, match="Refusing to reseed Lakebase"):
        lakebase_run.main()

    assert calls == [
        (
            "postgres",
            "list-cdf-configs",
            "projects/fe-bar-operational-plane/branches/production/databases/databricks-postgres",
            "--output",
            "json",
        )
    ]


@pytest.mark.parametrize("action", ["validate", "deploy"])
def test_plain_bundle_passthroughs_are_not_wrapped(monkeypatch, action):
    calls = []
    monkeypatch.setattr(
        lakebase_run, "databricks", lambda *parts, **kwargs: calls.append(parts)
    )
    monkeypatch.setattr(sys, "argv", ["run.py", action])

    with pytest.raises(SystemExit):
        lakebase_run.main()

    assert calls == []


def _capture_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(
        lakebase_run, "command", lambda *parts, **kwargs: calls.append(parts)
    )
    return calls


@pytest.mark.parametrize(
    "argv, expected_tail",
    [
        (["synced-tables"], ("create",)),
        (["resync-synced-tables"], ("resync",)),
        (
            ["resync-synced-tables", "--tables", "prior_claims_corpus"],
            ("resync", "--tables", "prior_claims_corpus"),
        ),
        (
            ["recreate-synced-table", "--table", "prior_claims_corpus"],
            ("recreate", "--table", "prior_claims_corpus"),
        ),
    ],
)
def test_synced_table_actions_route_to_the_right_path(monkeypatch, argv, expected_tail):
    calls = _capture_commands(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run.py", *argv])
    lakebase_run.main()
    (call,) = calls
    assert "scripts/synced_tables.py" in call
    assert call[call.index("scripts/synced_tables.py") + 1 :] == expected_tail


def test_recreate_requires_a_table(monkeypatch):
    _capture_commands(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run.py", "recreate-synced-table"])
    with pytest.raises(SystemExit):
        lakebase_run.main()
