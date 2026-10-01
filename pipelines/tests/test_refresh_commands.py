"""Behavioral tests for scripts/refresh.py (routine and demo refresh).

``subprocess.run`` is mocked; each test runs ``main()`` and asserts the exact command
order. The routine refresh must re-sync the existing synced tables and never take a
create/recreate/re-grant path (a triggered re-sync keeps the tables and grants).
"""

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("refresh", REPO / "scripts" / "refresh.py")
refresh = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refresh)

WRAPPER = ("uv", "run", "--with", "pyyaml", "python")
TARGET = ("--target", "prod", "--profile", "fe-bar")
MEDALLION = ((*WRAPPER, "pipelines/run.py", "refresh"), REPO)
ROUTINE = [
    MEDALLION,
    (("databricks", "bundle", "deploy", *TARGET), REPO / "agent"),
    (("databricks", "bundle", "run", "fraud_graph", *TARGET), REPO / "agent"),
    (("databricks", "bundle", "run", "prior_claims_corpus", *TARGET), REPO / "agent"),
    ((*WRAPPER, "lakebase/run.py", "resync-synced-tables"), REPO),
    MEDALLION,
]


def _run(monkeypatch, argv, fail_on=None):
    calls = []

    def fake_run(args, cwd=None, check=False, **kwargs):
        assert check is True  # every step fails loudly
        calls.append((tuple(args), cwd))
        if fail_on and fail_on in args:
            raise refresh.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(refresh.subprocess, "run", fake_run)
    refresh.main(argv)
    return calls


def test_routine_refresh_order(monkeypatch):
    assert _run(monkeypatch, ["routine"]) == ROUTINE


def test_demo_refresh_is_demo_backlog_then_routine(monkeypatch):
    calls = _run(monkeypatch, ["demo"])
    assert calls == [
        (("databricks", "bundle", "deploy", *TARGET), REPO / "demo"),
        (("databricks", "bundle", "run", "demo_backlog", *TARGET), REPO / "demo"),
        *ROUTINE,
    ]


@pytest.mark.parametrize("mode", ["routine", "demo"])
def test_refresh_resyncs_and_never_recreates_or_regrants(monkeypatch, mode):
    flat = " ".join(" ".join(args) for args, _ in _run(monkeypatch, [mode]))
    assert "resync-synced-tables" in flat
    for forbidden in (
        "recreate-synced-table",
        "lakebase/run.py synced-tables",
        "regrant",
        "--full-refresh",
        "setup-and-seed",
        "create-cdf",
    ):
        assert forbidden not in flat


def test_fraud_graph_and_corpus_run_between_the_two_medallion_runs(monkeypatch):
    calls = [args for args, _ in _run(monkeypatch, ["routine"])]
    medallion_runs = [i for i, a in enumerate(calls) if a[-2:] == ("pipelines/run.py", "refresh")]
    assert len(medallion_runs) == 2
    first, final = medallion_runs
    fraud = calls.index(("databricks", "bundle", "run", "fraud_graph", *TARGET))
    corpus = calls.index(("databricks", "bundle", "run", "prior_claims_corpus", *TARGET))
    resync = calls.index((*WRAPPER, "lakebase/run.py", "resync-synced-tables"))
    assert first < fraud < corpus < resync < final


def test_refresh_stops_at_first_failing_step(monkeypatch):
    with pytest.raises(refresh.subprocess.CalledProcessError):
        _run(monkeypatch, ["routine"], fail_on="prior_claims_corpus")


def test_refresh_stops_before_resync_when_corpus_fails(monkeypatch):
    calls = []

    def fake_run(args, cwd=None, check=False, **kwargs):
        calls.append(tuple(args))
        if "prior_claims_corpus" in args:
            raise refresh.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(refresh.subprocess, "run", fake_run)
    with pytest.raises(refresh.subprocess.CalledProcessError):
        refresh.main(["routine"])
    assert not any("resync-synced-tables" in c for c in calls)


def test_allow_missing_decision_records_is_forwarded_to_both_medallion_runs(monkeypatch):
    calls = [args for args, _ in _run(monkeypatch, ["routine", "--allow-missing-decision-records"])]
    medallion = [a for a in calls if "pipelines/run.py" in a]
    assert len(medallion) == 2
    assert all(a[-1] == "--allow-missing-decision-records" for a in medallion)
    # Default routine refresh does not pass it, so a missing table fails loudly.
    assert "--allow-missing-decision-records" not in " ".join(
        " ".join(a) for a, _ in _run(monkeypatch, ["routine"])
    )


def test_unknown_mode_is_rejected():
    with pytest.raises(SystemExit):
        refresh.main(["full"])
