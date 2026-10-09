"""Worker dedup decision + per-message handling (endpoint/Lakebase-free)."""

import pytest

import events
import worker_core


def _raw(claim_id):
    claim = {f: f"v-{f}" for f in events.CLAIM_FIELDS}
    claim["claim_id"] = claim_id
    return events.serialize(events.build_submitted_event(claim))


class _Adjudications:
    """In-memory adjudications answering DEDUP_SQL with its bound parameters."""

    def __init__(self, rows):
        self.rows = rows  # (claim_id, decision_status, data_provenance)
        self.queries = []

    def lookup(self, claim_id):
        statuses, provenance, cid = worker_core.dedup_params(claim_id)
        self.queries.append((worker_core.DEDUP_SQL, (statuses, provenance, cid)))
        mine = [r for r in self.rows if r[0] == cid]
        if not mine:
            return (None, None)  # aggregate over zero rows
        return (
            any(r[1] in statuses for r in mine),
            any(r[2] == provenance for r in mine),
        )


def _handle(table, claim_id):
    invoked, commits = [], []
    outcome = worker_core.handle_submitted(
        _raw(claim_id),
        lookup=table.lookup,
        invoke=invoked.append,
        commit=lambda: commits.append(claim_id),
    )
    return outcome, invoked, commits


def test_dedup_sql_uses_bound_params_only():
    sql = worker_core.DEDUP_SQL
    assert "FROM adjudications" in sql
    assert "decision_status = ANY(%s)" in sql
    assert "data_provenance = %s" in sql
    assert "claim_id = %s" in sql
    assert sql.count("%s") == 3
    assert "'" not in sql  # no inlined literals


def test_dedup_params_carry_final_statuses_provenance_and_claim():
    assert worker_core.dedup_params("CLM-1") == (
        ["FINAL", "REVIEWED"],
        events.AGENT_PROVENANCE,
        "CLM-1",
    )
    assert events.AGENT_PROVENANCE == "agent_recommendation"


@pytest.mark.parametrize("status", ["FINAL", "REVIEWED"])
def test_claim_with_human_decision_is_skipped_and_committed(status):
    table = _Adjudications([("CLM-1", status, "synthetic_reference_baseline")])
    outcome, invoked, commits = _handle(table, "CLM-1")
    assert outcome == worker_core.SKIP_FINAL
    assert invoked == []  # no LLM call, no write
    assert commits == ["CLM-1"]


def test_human_decision_wins_over_agent_recommendation():
    table = _Adjudications(
        [("CLM-1", "RECOMMENDED", "agent_recommendation"), ("CLM-1", "FINAL", "human")]
    )
    assert _handle(table, "CLM-1")[0] == worker_core.SKIP_FINAL


def test_claim_without_adjudication_is_invoked_then_committed():
    table = _Adjudications([("CLM-OTHER", "FINAL", "human")])
    outcome, invoked, commits = _handle(table, "CLM-1")
    assert outcome == worker_core.ADJUDICATE
    assert [c["claim_id"] for c in invoked] == ["CLM-1"]
    assert set(invoked[0]) == set(events.CLAIM_FIELDS)
    assert commits == ["CLM-1"]


def test_claim_with_only_agent_recommendation_is_still_skipped():
    table = _Adjudications([("CLM-1", "RECOMMENDED", "agent_recommendation")])
    outcome, invoked, commits = _handle(table, "CLM-1")
    assert outcome == worker_core.SKIP_AGENT
    assert invoked == []
    assert commits == ["CLM-1"]


def test_baseline_recommendation_without_human_decision_is_invoked():
    table = _Adjudications([("CLM-1", "RECOMMENDED", "synthetic_reference_baseline")])
    assert _handle(table, "CLM-1")[0] == worker_core.ADJUDICATE


@pytest.mark.parametrize("failing", ["lookup", "invoke"])
def test_failure_before_handling_does_not_commit(failing):
    def boom(*_):
        raise RuntimeError(failing)

    commits = []
    with pytest.raises(RuntimeError):
        worker_core.handle_submitted(
            _raw("CLM-1"),
            lookup=boom if failing == "lookup" else (lambda _: (None, None)),
            invoke=boom,
            commit=lambda: commits.append("CLM-1"),
        )
    assert commits == []  # re-delivered next run (at-least-once)


def test_dedup_decision_handles_missing_row():
    assert worker_core.dedup_decision(None) == worker_core.ADJUDICATE


def test_parse_and_extract_claim_round_trip():
    event = worker_core.parse_submitted(_raw("CLM-1"))
    extracted = worker_core.claim_from_event(event)
    assert extracted["claim_id"] == "CLM-1"
    assert set(extracted) == set(events.CLAIM_FIELDS)


class _Msg:
    def __init__(self, raw=None, error=None):
        self._raw, self._error = raw, error

    def value(self):
        return self._raw

    def error(self):
        return self._error


@pytest.mark.parametrize("error", ["_PARTITION_EOF", "_TRANSPORT"])
def test_error_message_is_not_committed_or_invoked(error):
    lookups, invoked, commits = [], [], []
    outcome = worker_core.handle_message(
        _Msg(error=error),
        lookup=lookups.append,
        invoke=invoked.append,
        commit=lambda: commits.append(error),
    )
    assert outcome == worker_core.CONSUMER_ERROR
    assert (lookups, invoked, commits) == ([], [], [])


def test_valid_message_is_routed_through_dedup():
    table = _Adjudications([])
    invoked, commits = [], []
    outcome = worker_core.handle_message(
        _Msg(raw=_raw("CLM-1")),
        lookup=table.lookup,
        invoke=invoked.append,
        commit=lambda: commits.append("CLM-1"),
    )
    assert outcome == worker_core.ADJUDICATE
    assert [c["claim_id"] for c in invoked] == ["CLM-1"]
    assert commits == ["CLM-1"]
