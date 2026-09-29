"""Execution tests for `run.py refresh`.

Mocks the databricks CLI (``subprocess.run``) and actually calls ``main()`` with the
refresh action, asserting it issues NO DROP/query call and exactly the deploy +
``bundle run refresh_medallion`` sequence — the fix for the stale DROP MATERIALIZED
VIEW migration that failed with DROP_COMMAND_TYPE_MISMATCH on go-live. A companion
test confirms the pure `databricks bundle` passthroughs (validate/deploy/summary)
were collapsed out of the wrapper and are now rejected by the parser.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "run.py"
SPEC = importlib.util.spec_from_file_location("pipelines_run", MODULE_PATH)
run = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run)

# JSON the mocked CLI returns for the two read calls the refresh path makes.
_ONE_CDF_CONFIG = json.dumps([{"name": "cdf/config-1"}])
_CDF_TABLES = json.dumps(
    [
        {"name": "lb_claims_history_abc", "full_name": "fe-bar-ir.cdf.lb_claims_history_abc"},
        {
            "name": "lb_adjudications_history_def",
            "full_name": "fe-bar-ir.cdf.lb_adjudications_history_def",
        },
    ]
)


def _make_fake_run(calls):
    """A subprocess.run stub that records each databricks argv and returns canned JSON.

    ``calls`` collects the parts BETWEEN 'databricks' and the trailing '--profile
    <profile>' so assertions read the logical subcommand, not the wrapper noise.
    """

    def fake_run(cmd, cwd=None, env=None, text=None, capture_output=None, check=False, **kwargs):
        # cmd == ["databricks", *parts, "--profile", <profile>]
        parts = cmd[1:-2] if cmd[-2:-1] == ["--profile"] else cmd[1:]
        calls.append(parts)
        if "list-cdf-configs" in parts:
            stdout = _ONE_CDF_CONFIG
        elif "tables" in parts and "list" in parts:
            stdout = _CDF_TABLES
        else:
            stdout = ""  # bundle deploy / run: stdout is not parsed
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    return fake_run


def _logical_calls(calls):
    """Strip trailing --output json so a call reads as its bare subcommand."""
    trimmed = []
    for parts in calls:
        p = parts[:-2] if parts[-2:] == ["--output", "json"] else parts
        trimmed.append(p)
    return trimmed


def test_refresh_runs_incremental_medallion_with_no_drop_or_query(monkeypatch):
    calls = []
    monkeypatch.setattr(run.subprocess, "run", _make_fake_run(calls))
    monkeypatch.setattr(sys, "argv", ["run.py", "refresh"])

    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 0

    logical = _logical_calls(calls)
    flat = [" ".join(p) for p in logical]

    # No stale migration: never a DROP, never the aitools query surface, never a
    # warehouse lookup (all part of the removed DROP MATERIALIZED VIEW step).
    all_text = " ".join(flat)
    assert "DROP MATERIALIZED VIEW" not in all_text
    assert "aitools" not in all_text
    assert "query" not in all_text
    assert "warehouses" not in all_text

    # Exactly the sanctioned sequence: deploy then run refresh_medallion, deploy first.
    assert ["bundle", "deploy", "--target", "prod"] in logical
    assert ["bundle", "run", "refresh_medallion", "--target", "prod"] in logical
    assert flat.index("bundle deploy --target prod") < flat.index(
        "bundle run refresh_medallion --target prod"
    )
    # It is a normal triggered run — no full-refresh / reset flag anywhere.
    assert "--full-refresh" not in all_text and "--refresh-all" not in all_text


def test_refresh_feeds_current_cdf_table_names_to_the_pipeline(monkeypatch):
    calls = []
    monkeypatch.setattr(run.subprocess, "run", _make_fake_run(calls))
    monkeypatch.setattr(sys, "argv", ["run.py", "refresh"])
    with pytest.raises(SystemExit):
        run.main()
    # The bundle run inherits the resolved CDF table names via BUNDLE_VAR_* env.
    # (Resolution happening at all proves the tables.list read drove the run.)
    assert any("tables" in p and "list" in p for p in calls)


@pytest.mark.parametrize("action", ["validate", "deploy", "summary"])
def test_collapsed_passthrough_actions_are_removed(monkeypatch, action):
    # These were pure `databricks bundle <cmd>` shims. They were collapsed: the README
    # now documents them as direct `databricks bundle ... --target prod --profile
    # fe-bar` commands, and the wrapper's parser rejects them (argparse exits 2 on an
    # invalid choice). The genuine-orchestration actions stay.
    monkeypatch.setattr(run.subprocess, "run", _make_fake_run([]))
    monkeypatch.setattr(sys, "argv", ["run.py", action])
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2
