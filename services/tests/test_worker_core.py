"""Worker dedup decision + event parsing (endpoint/Lakebase-free)."""

import events
import worker_core


def test_dedup_params_scope_to_agent_provenance():
    params = worker_core.dedup_params("CLM-1")
    assert params == ("CLM-1", events.AGENT_PROVENANCE)
    assert events.AGENT_PROVENANCE == "agent_recommendation"


def test_dedup_sql_targets_adjudications_by_claim_and_provenance():
    sql = worker_core.ALREADY_ADJUDICATED_SQL
    assert "FROM adjudications" in sql
    assert "claim_id = %s" in sql
    assert "data_provenance = %s" in sql


def test_should_adjudicate_skips_when_already_adjudicated():
    assert worker_core.should_adjudicate(already_exists=False) is True
    assert worker_core.should_adjudicate(already_exists=True) is False


def test_parse_and_extract_claim_round_trip():
    claim = {f: f"v-{f}" for f in events.CLAIM_FIELDS}
    claim["claim_id"] = "CLM-1"
    raw = events.serialize(events.build_submitted_event(claim))
    event = worker_core.parse_submitted(raw)
    extracted = worker_core.claim_from_event(event)
    assert extracted["claim_id"] == "CLM-1"
    assert set(extracted) == set(events.CLAIM_FIELDS)
