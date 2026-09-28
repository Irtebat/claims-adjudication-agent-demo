"""Pure CDF-row -> claim.submitted transformation (Spark/Kafka-free)."""

import producer_core


def _cdf_row():
    row = {f: f"v-{f}" for f in producer_core.CLAIM_FIELDS}
    row["claim_id"] = "CLM-1"
    row.update(
        {
            "_pg_change_type": "insert",
            "_pg_lsn": "0/ABCDEF",
            "_timestamp": "2026-01-02T00:00:00Z",
            "data_provenance": "cdf",
        }
    )
    return row


def test_is_submission_only_for_inserts():
    assert producer_core.is_submission("insert") is True
    assert producer_core.is_submission("update_postimage") is False
    assert producer_core.is_submission("delete") is False


def test_cdf_row_to_event_maps_source_and_claim():
    ev = producer_core.cdf_row_to_event(_cdf_row())
    assert ev["event_id"] == "sub-CLM-1"
    assert ev["event_type"] == "claim.submitted"
    assert ev["claim_id"] == "CLM-1"
    assert set(ev["claim"]) == set(producer_core.CLAIM_FIELDS)
    assert ev["source"]["cdf_change_type"] == "insert"
    assert ev["source"]["cdf_commit_version"] == "0/ABCDEF"
    assert ev["source"]["cdf_commit_timestamp"] == "2026-01-02T00:00:00Z"


def test_cdf_row_to_event_ignores_non_claim_columns():
    ev = producer_core.cdf_row_to_event(_cdf_row())
    assert "data_provenance" not in ev["claim"]
    assert "_pg_change_type" not in ev["claim"]
