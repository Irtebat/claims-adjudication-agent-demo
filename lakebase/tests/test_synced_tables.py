"""Behavioral tests for scripts/synced_tables.py: create vs resync vs recreate.

The databricks CLI is mocked at ``subprocess.run`` and Postgres at the injected ``pg``
callable. The key contract: a routine re-sync triggers an incremental update of the
EXISTING table's sync pipeline and never re-grants; only the create path (when it
created something) and the recreate (schema-change) path re-grant.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "synced_tables.py"
SPEC = importlib.util.spec_from_file_location("synced_tables", MODULE_PATH)
st = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = st  # dataclasses resolve their module via sys.modules
SPEC.loader.exec_module(st)


class FakeCli:
    """Records every argv; answers get-synced-table / start-update / get-update."""

    def __init__(self, existing=(), update_states=("RUNNING", "COMPLETED")):
        self.calls = []
        self.existing = set(existing)
        self.update_states = list(update_states)

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "uv":  # the regrant subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        parts = cmd[1:]
        out, code = "", 0
        if parts[:2] == ["postgres", "get-synced-table"]:
            name = parts[2].split(".")[-1]
            if name in self.existing:
                out = json.dumps({"status": {"pipeline_id": f"pipe-{name}"}})
            else:
                code = 1
        elif parts[:2] == ["postgres", "create-synced-table"]:
            self.existing.add(parts[2].split(".")[-1])
            out = "{}"
        elif parts[:2] == ["pipelines", "start-update"]:
            out = json.dumps({"update_id": f"upd-{parts[2]}"})
        elif parts[:2] == ["pipelines", "get-update"]:
            out = json.dumps({"update": {"state": self.update_states.pop(0)}})
        else:
            out = "{}"
        return subprocess.CompletedProcess(
            cmd, code, stdout=out, stderr="boom" if code else ""
        )

    def subcommands(self):
        return [
            " ".join(c[1:3]) if c[0] == "databricks" else "REGRANT" for c in self.calls
        ]

    def regranted(self):
        return any(
            c[0] == "uv" and "regrant_synced_table_selects.py" in c[-3]
            for c in self.calls
        )


def _pg_recorder():
    statements = []
    return statements, lambda stmts: statements.extend(stmts)


def _states(n):
    return [s for _ in range(n) for s in ("RUNNING", "COMPLETED")]


def test_resync_runs_incremental_pipeline_update_and_never_regrants():
    cli = FakeCli(existing=st.ROUTINE_RESYNC, update_states=_states(2))
    statements, pg = _pg_recorder()
    result = st.resync(list(st.ROUTINE_RESYNC), runner=cli, pg=pg, sleep=lambda s: None)

    assert result["regranted"] is False
    assert not cli.regranted()
    starts = [c for c in cli.calls if c[1:3] == ["pipelines", "start-update"]]
    assert [c[3] for c in starts] == [
        "pipe-customer_heat_risk",
        "pipe-prior_claims_corpus",
    ]
    # A re-sync is a normal triggered update — never a full refresh — and never deletes,
    # drops, or recreates the synced table.
    flat = " ".join(" ".join(c) for c in cli.calls)
    assert "--full-refresh" not in flat
    assert "delete-synced-table" not in flat and "create-synced-table" not in flat
    assert not any(s.startswith(("DROP", "GRANT")) for s in statements)
    # The corpus gets its indexes ensured and BM25 stats refreshed after its sync.
    assert statements[-1] == "VACUUM (ANALYZE) reference.prior_claims_corpus"
    assert any("USING lakebase_ann" in s for s in statements)
    assert any("USING lakebase_bm25" in s for s in statements)


def test_resync_order_is_sync_then_vacuum_per_table():
    cli = FakeCli(existing=["prior_claims_corpus"], update_states=_states(1))
    events = []
    st.resync(
        ["prior_claims_corpus"],
        runner=lambda cmd, **kw: (events.append(" ".join(cmd[1:3])), cli(cmd, **kw))[1],
        pg=lambda stmts: events.append("PG:" + stmts[-1]),
        sleep=lambda s: None,
    )
    assert events.index("pipelines start-update") < events.index(
        "PG:VACUUM (ANALYZE) reference.prior_claims_corpus"
    )


def test_resync_fails_loudly_when_the_sync_update_fails():
    cli = FakeCli(existing=["customer_heat_risk"], update_states=["FAILED"])
    with pytest.raises(RuntimeError, match="ended FAILED"):
        st.resync(
            ["customer_heat_risk"], runner=cli, pg=lambda s: None, sleep=lambda s: None
        )


def test_create_skips_existing_and_does_not_regrant_when_nothing_created():
    cli = FakeCli(existing=[t.name for t in st.TABLES])
    statements, pg = _pg_recorder()
    result = st.create(runner=cli, pg=pg)
    assert result == {"created": [], "regranted": False}
    assert not cli.regranted() and statements == []


def test_create_new_corpus_builds_indexes_then_regrants():
    existing = [t.name for t in st.TABLES if t.name != "prior_claims_corpus"]
    cli = FakeCli(existing=existing)
    statements, pg = _pg_recorder()
    result = st.create(runner=cli, pg=pg)
    assert result == {"created": ["prior_claims_corpus"], "regranted": True}
    subs = cli.subcommands()
    assert subs.index("postgres create-synced-table") < subs.index("REGRANT")
    create = next(c for c in cli.calls if c[1:3] == ["postgres", "create-synced-table"])
    spec = json.loads(create[create.index("--json") + 1])["spec"]
    assert spec["source_table_full_name"] == "fe-bar-ir.gold.prior_claims_corpus"
    assert spec["primary_key_columns"] == ["claim_id"]
    assert spec["scheduling_policy"] == "TRIGGERED"
    assert statements == st.POST_CREATE_SQL["prior_claims_corpus"]


def test_recreate_deletes_drops_creates_indexes_and_regrants_in_order():
    cli = FakeCli(existing=["prior_claims_corpus"])
    events = []
    st.recreate(
        "prior_claims_corpus",
        runner=lambda cmd, **kw: (
            events.append("REGRANT" if cmd[0] == "uv" else " ".join(cmd[1:3])),
            cli(cmd, **kw),
        )[1],
        pg=lambda stmts: events.append("PG:" + stmts[0].split(" ON ")[0]),
    )
    assert events == [
        "postgres get-synced-table",
        "postgres delete-synced-table",
        "PG:DROP TABLE IF EXISTS reference.prior_claims_corpus",
        "postgres create-synced-table",
        "PG:CREATE INDEX IF NOT EXISTS prior_claims_corpus_lb_ann",
        "REGRANT",
    ]


def test_indexes_cover_the_expressions_retrieval_queries():
    ann, bm25 = st.POST_CREATE_SQL["prior_claims_corpus"]
    assert "(embedding::vector(1024)) vector_cosine_ops" in ann
    assert "(to_tsvector('english', defect_narrative)) tsvector_bm25_ops" in bm25
    assert "prior_claims_corpus_lb_bm25" in bm25


def test_every_cli_call_carries_the_explicit_profile():
    cli = FakeCli(existing=st.ROUTINE_RESYNC, update_states=_states(2))
    st.resync(
        list(st.ROUTINE_RESYNC), runner=cli, pg=lambda s: None, sleep=lambda s: None
    )
    for cmd in cli.calls:
        assert cmd[cmd.index("--profile") + 1] == "fe-bar"
