import os
from types import SimpleNamespace

import pytest

from predict import load_candidate, predict_claim, response_request


class FakeModel:
    def __init__(self, failures=0, message="temporary"):
        self.failures, self.message, self.calls = failures, message, 0

    def predict(self, request):
        self.calls += 1
        assert request["custom_inputs"]["persist"] is False
        assert "max_output_tokens" not in request
        assert "temperature" not in request
        if self.calls <= self.failures:
            raise RuntimeError(self.message)
        return SimpleNamespace(
            model_dump=lambda: {"custom_outputs": {"write_result": {"persisted": False}}}
        )


def test_request_shape_and_persist_false():
    request = response_request({"claim_id": "c"})
    assert request.custom_inputs == {"claim": {"claim_id": "c"}, "persist": False}


def test_transient_retry():
    model = FakeModel(failures=1)
    assert predict_claim({"claim_id": "c"}, model=model, sleep=lambda _: None)
    assert model.calls == 2


def test_403_stops_without_retry():
    model = FakeModel(failures=3, message="403 IP ACL forbidden")
    with pytest.raises(RuntimeError, match="403"):
        predict_claim({"claim_id": "c"}, model=model, sleep=lambda _: None)
    assert model.calls == 1


def test_loads_packaged_candidate_with_sdk_request_timeout(monkeypatch):
    loaded = FakeModel()
    calls = []
    uri = "models:/fe-bar-ir.default.claims_adjudication_agent/7"
    monkeypatch.delenv("DATABRICKS_HTTP_TIMEOUT_SECONDS", raising=False)

    assert (
        load_candidate(
            uri, loader=lambda value: calls.append(value) or loaded, request_timeout_seconds=7
        )
        is loaded
    )
    assert calls == [uri]
    assert os.environ["DATABRICKS_HTTP_TIMEOUT_SECONDS"] == "7"
