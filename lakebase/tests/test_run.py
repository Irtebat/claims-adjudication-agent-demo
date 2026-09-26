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
