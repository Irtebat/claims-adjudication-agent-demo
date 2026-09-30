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

    ONLINE = "SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE"

    def __init__(
        self, existing=(), update_states=("RUNNING", "COMPLETED"), online_states=None
    ):
        self.calls = []
        self.existing = set(existing)
        self.update_states = list(update_states)
        # Per-table detailed_state sequence reported by get-synced-table (last one sticks).
        self.online_states = {k: list(v) for k, v in (online_states or {}).items()}

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if cmd[0] == "uv":  # the regrant subprocess
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        parts = cmd[1:]
        out, code = "", 0
        if parts[:2] == ["postgres", "get-synced-table"]:
            name = parts[2].split(".")[-1]
            if name in self.existing:
                seq = self.online_states.get(name) or [self.ONLINE]
                state = seq.pop(0) if len(seq) > 1 else seq[0]
                out = json.dumps(
                    {"status": {"pipeline_id": f"pipe-{name}", "detailed_state": state}}
                )
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
    result = st.create(runner=cli, pg=pg, sleep=lambda seconds: None)
    assert result == {"created": [], "regranted": False}
    assert not cli.regranted() and statements == []


def test_create_new_corpus_builds_indexes_then_regrants():
    existing = [t.name for t in st.TABLES if t.name != "prior_claims_corpus"]
    cli = FakeCli(existing=existing)
    statements, pg = _pg_recorder()
    result = st.create(runner=cli, pg=pg, sleep=lambda seconds: None)
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
        sleep=lambda seconds: None,
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
        "postgres get-synced-table",  # waits for ONLINE before any index DDL
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


def _record_events(cli, events):
    def runner(cmd, **kwargs):
        result = cli(cmd, **kwargs)
        if cmd[0] == "uv":
            events.append("REGRANT")
        elif cmd[1:3] == ["postgres", "get-synced-table"] and not result.returncode:
            events.append(
                "STATE:" + json.loads(result.stdout)["status"]["detailed_state"]
            )
        else:
            events.append(" ".join(cmd[1:3]))
        return result

    return runner


def test_create_waits_for_online_before_index_ddl_and_regrant():
    existing = [t.name for t in st.TABLES if t.name != "prior_claims_corpus"]
    cli = FakeCli(
        existing=existing,
        online_states={
            "prior_claims_corpus": [
                "SYNCED_TABLE_PROVISIONING",
                "SYNCED_TABLE_PROVISIONING_INITIAL_SNAPSHOT",
                "SYNCED_TABLE_ONLINE_TRIGGERED_UPDATE",
            ]
        },
    )
    events, sleeps = [], []
    result = st.create(
        runner=_record_events(cli, events),
        pg=lambda stmts: events.append("PG:" + stmts[0].split(" ON ")[0]),
        sleep=sleeps.append,
    )
    assert result == {"created": ["prior_claims_corpus"], "regranted": True}
    after_create = events[events.index("postgres create-synced-table") + 1 :]
    assert after_create == [
        "STATE:SYNCED_TABLE_PROVISIONING",
        "STATE:SYNCED_TABLE_PROVISIONING_INITIAL_SNAPSHOT",
        "STATE:SYNCED_TABLE_ONLINE_TRIGGERED_UPDATE",
        "PG:CREATE INDEX IF NOT EXISTS prior_claims_corpus_lb_ann",
        "REGRANT",
    ]
    assert len(sleeps) == 2  # slept only while not yet ONLINE


@pytest.mark.parametrize(
    "states, match",
    [
        (
            ["SYNCED_TABLE_PROVISIONING", "SYNCED_TABLE_OFFLINE_FAILED"],
            "initial sync failed",
        ),
        (
            ["SYNCED_TABLE_PROVISIONING"],
            "did not reach SYNCED_TABLE_ONLINE",
        ),  # never online
    ],
)
def test_create_failed_or_timeout_raises_and_never_indexes_or_regrants(
    monkeypatch, states, match
):
    monkeypatch.setattr(st, "ONLINE_ATTEMPTS", 3)
    existing = [t.name for t in st.TABLES if t.name != "prior_claims_corpus"]
    cli = FakeCli(existing=existing, online_states={"prior_claims_corpus": states})
    statements, pg = _pg_recorder()
    with pytest.raises(RuntimeError, match=match):
        st.create(runner=cli, pg=pg, sleep=lambda seconds: None)
    assert statements == []
    assert not cli.regranted()


def test_wait_for_online_timeout_is_bounded():
    cli = FakeCli(
        existing=["prior_claims_corpus"],
        online_states={"prior_claims_corpus": ["SYNCED_TABLE_PROVISIONING"]},
    )
    sleeps = []
    with pytest.raises(RuntimeError, match="within 30s"):
        st.wait_for_online(
            st.BY_NAME["prior_claims_corpus"],
            runner=cli,
            sleep=sleeps.append,
            attempts=3,
            interval=10.0,
        )
    assert sleeps == [10.0, 10.0, 10.0]


def test_recreate_failed_initial_sync_never_regrants():
    cli = FakeCli(
        existing=["prior_claims_corpus"],
        online_states={"prior_claims_corpus": ["SYNCED_TABLE_OFFLINE_FAILED"]},
    )
    statements, pg = _pg_recorder()
    with pytest.raises(RuntimeError, match="initial sync failed"):
        st.recreate(
            "prior_claims_corpus", runner=cli, pg=pg, sleep=lambda seconds: None
        )
    # Only the pre-create DROP ran; no index DDL, no regrant.
    assert statements == ["DROP TABLE IF EXISTS reference.prior_claims_corpus"]
    assert not cli.regranted()


def test_script_loads_in_a_bare_interpreter_from_the_lakebase_dir():
    # Isolated, no site-packages: the index constants must come from the import-free
    # agent/src/prior_claims_indexes.py alone, with no agent dependency on the path.
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('st', {str(MODULE_PATH)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['st'] = m\n"
        "spec.loader.exec_module(m)\n"
        "print(m.POST_CREATE_SQL['prior_claims_corpus'][0])\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        cwd=MODULE_PATH.parents[1],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == st.POST_CREATE_SQL["prior_claims_corpus"][0]
    assert "USING lakebase_ann ((embedding::vector(1024)) vector_cosine_ops)" in out
