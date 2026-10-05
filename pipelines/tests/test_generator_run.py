import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "run.py"
SPEC = importlib.util.spec_from_file_location("pipelines_run_generator", MODULE_PATH)
run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run)


@pytest.mark.parametrize(
    ("action", "job"), [("generate", "generate_raw"), ("check-generator", "validate_generator")]
)
def test_generator_jobs_pass_runtime_params_as_csv_not_bundle_vars(monkeypatch, action, job):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run.py", action, "--claim-count", "17"])
    for name in ("catalog", "claim_count", "seed", "warranty_schedule"):
        monkeypatch.delenv(f"BUNDLE_VAR_{name}", raising=False)

    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0

    cmd, kwargs = calls[0]
    assert cmd[:4] == ["databricks", "bundle", "run", job]
    params = dict(
        field.split("=", 1) for field in next(csv.reader([cmd[cmd.index("--params") + 1]]))
    )
    assert params["catalog"] == "fe-bar-ir"
    assert params["claim_count"] == "17"
    assert params["seed"] == "42"

    policy = json.loads((MODULE_PATH.parents[1] / "lakebase/src/policy_source.json").read_text())
    expected_schedule = [
        {
            "version": version["version"],
            "effective_from": version["effective_from"],
            "effective_to": version["effective_to"],
            "duration_months": version["duration_months"],
            "full_coverage_months": version["full_coverage_months"],
        }
        for version in policy["warranties"]["versions"]
    ]
    assert json.loads(params["warranty_schedule"]) == expected_schedule
    assert not any(f"BUNDLE_VAR_{name}" in kwargs["env"] for name in params)
