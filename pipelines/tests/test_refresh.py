"""`run.py refresh` is a normal incremental medallion run with no stale MV migration."""

from pathlib import Path

RUN_PY = (Path(__file__).resolve().parents[1] / "run.py").read_text()


def _refresh_block():
    # Isolate the body of the `refresh` action so assertions target it, not the other
    # actions (which legitimately issue SQL via the shared aitools query surface).
    start = RUN_PY.index('if args.action == "refresh":')
    end = RUN_PY.index('if args.action == "decision-records":')
    return RUN_PY[start:end]


def test_refresh_has_no_drop_materialized_view_migration():
    # silver.*_history are STREAMING_TABLE and gold.*_history / *_current are VIEW, so the
    # old DROP MATERIALIZED VIEW step failed with DROP_COMMAND_TYPE_MISMATCH on go-live.
    assert "DROP MATERIALIZED VIEW" not in RUN_PY


def test_refresh_runs_the_incremental_refresh_medallion_job():
    block = _refresh_block()
    assert 'cli("bundle", "run", "refresh_medallion", "--target", "prod")' in block
    # It deploys the bundle first so the pipeline sees the current CDF table names.
    assert 'cli("bundle", "deploy", "--target", "prod")' in block


def test_refresh_does_not_full_refresh_or_resnapshot():
    block = _refresh_block()
    assert "full-refresh" not in block
    assert "full_refresh" not in block
    # The refresh action no longer issues any ad hoc DDL/query against a warehouse.
    assert "aitools" not in block
