import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from databricks.sdk.core import ApiClient

import ablation
from ablation import (
    NA,
    CandidateAdapter,
    PersistenceViolation,
    WorkerSpec,
    callable_adapter,
    compare,
    deterministic_adapter,
    endpoint_adapter,
    model_adapter,
    normalize_expectations,
    score,
    standardize_output,
    stratified_sample,
)


def _isolated_explode(claim):
    raise ValueError(f"bad claim {claim['claim_id']}")


def _isolated_persistence(claim):
    raise PersistenceViolation(f"blocked write for {claim['claim_id']}")


def _isolated_slow(claim):
    time.sleep(10)


def _candidate(verdict, disposition, *, citations=None, violations=None, tokens=None):
    def invoke(claim):
        data = {
            "recommendation": {
                "recommended_verdict": verdict,
                "recommended_disposition": disposition,
            },
            "cited_clause_ids": citations,
            "invariant_violations": violations,
        }
        if tokens is not None:
            data["usage"] = {"total_tokens": tokens}
        return {"custom_outputs": data}

    return invoke


def _records():
    return [
        {
            "inputs": {"claim": {"claim_id": "one"}},
            "expectations": {
                "verdict": "APPROVE",
                "disposition": "CREDIT",
                "oracle_clause_ids": ["a"],
            },
        },
        {
            "inputs": {"claim": {"claim_id": "two"}},
            "expectations": {"verdict": "DENY", "disposition": "DUPLICATE"},
        },
    ]


def test_na_is_excluded_and_all_dimensions_are_paired():
    baseline = callable_adapter("baseline", _candidate("APPROVE", "CREDIT"), deterministic=True)
    challenger = callable_adapter(
        "challenger", _candidate("DENY", "DUPLICATE", citations=[], violations=["fixed"])
    )
    report = compare(_records(), [baseline, challenger])
    assert report["summary"]["baseline"]["approval_subchoice"] == 1.0
    assert report["summary"]["baseline"]["citation"] == NA
    assert report["summary"]["baseline"]["judge"] == NA
    assert report["summary"]["baseline"]["tokens_per_claim"] == NA
    assert report["summary"]["baseline"]["invariant_correction_rate"] == NA
    assert set(report["paired_vs_baseline"]["challenger"]) >= {
        "verdict",
        "disposition_class",
        "approval_subchoice",
        "citation",
        "judge",
    }
    assert report["paired_vs_baseline"]["challenger"]["citation"]["comparable_count"] == 0


def test_subchoice_only_applies_to_approvals_and_empty_citations_fail():
    denied = standardize_output(
        _candidate("DENY", "DUPLICATE", citations=[])(None), deterministic=True
    )
    scores = score(denied, {"verdict": "DENY", "disposition": "DUPLICATE"})
    assert scores["disposition_class"] is True
    assert scores["approval_subchoice"] == NA
    assert scores["citation"] == NA
    non_deterministic = standardize_output(_candidate("DENY", "DUPLICATE", citations=[])(None))
    assert score(non_deterministic, {"oracle_clause_ids": ["a"]})["citation"] is False


def test_real_deterministic_shape_always_has_na_citations():
    output = standardize_output(
        {
            "recommended_verdict": "APPROVE",
            "recommended_disposition": "CREDIT",
            "cited_clause_ids": [],
        },
        deterministic=True,
    )
    expected = {"verdict": "APPROVE", "disposition": "CREDIT", "oracle_clause_ids": ["a"]}
    assert score(output, expected)["citation"] == NA


def test_history_expectations_derive_split_disposition():
    duplicate = normalize_expectations({"verdict": "DENY", "disposition": "DUPLICATE"})
    assert duplicate["disposition_class"] == "DENY"
    assert duplicate["approval_subchoice"] is None


def test_model_adapter_reuses_predict_retry_and_persist_guard():
    class Model:
        calls = 0

        def predict(self, request):
            self.calls += 1
            assert request["custom_inputs"]["persist"] is False
            return {"custom_outputs": {"write_result": {"persisted": True}}}

    model = Model()
    adapter = model_adapter("model", "models:/x@prod", loader=lambda _: model)
    with pytest.raises(PersistenceViolation, match="persistence invariant"):
        compare(
            [_records()[0]],
            [adapter, callable_adapter("other", _candidate("APPROVE", "CREDIT"))],
        )
    assert model.calls == 1


def test_endpoint_uses_invocations_rest_and_fails_closed():
    api_client = MagicMock(spec=ApiClient)
    api_client.do.return_value = {
        "custom_outputs": {
            "write_result": {"persisted": False},
            "recommendation": {"recommended_verdict": "DENY"},
        }
    }
    adapter = endpoint_adapter("endpoint", "claims/name", SimpleNamespace(api_client=api_client))
    adapter.worker_spec = None
    output, _ = adapter.predict({"claim_id": "c"})
    assert output["verdict"] == "DENY"
    api_client.do.assert_called_once_with(
        "POST",
        "/api/2.0/serving-endpoints/claims%2Fname/invocations",
        body={
            "input": [{"role": "user", "content": '{"claim_id": "c"}'}],
            "custom_inputs": {"claim": {"claim_id": "c"}, "persist": False},
        },
    )
    api_client.do.return_value = {"custom_outputs": {"write_result": {"persisted": True}}}
    with pytest.raises(RuntimeError, match="persistence invariant"):
        adapter.predict({"claim_id": "c"})


def test_per_claim_failure_is_recorded_and_other_rows_continue():
    calls = 0

    def flaky(claim):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bad row")
        return _candidate("DENY", "DUPLICATE")(claim)

    report = compare(
        _records(),
        [callable_adapter("base", flaky), callable_adapter("other", flaky)],
    )
    assert calls == 4
    assert report["summary"]["base"]["failure_count"] == 1
    assert report["summary"]["base"]["verdict"] == 0.5
    assert report["per_claim"][0]["candidates"]["base"]["verdict"] is False
    assert "bad row" in report["per_claim"][0]["candidates"]["base"]["error"]


def test_every_candidate_is_paired_against_first():
    candidates = [
        callable_adapter("base", _candidate("APPROVE", "CREDIT")),
        callable_adapter("one", _candidate("DENY", "DUPLICATE")),
        callable_adapter("two", _candidate("DENY", "DUPLICATE")),
    ]
    assert set(compare(_records(), candidates)["paired_vs_baseline"]) == {"one", "two"}


def test_standard_output_requires_dict():
    with pytest.raises(TypeError, match="return a dict"):
        standardize_output("bad")


def test_trace_token_usage_maps_claim(monkeypatch):
    rows = [
        {
            "request": {"claim": {"claim_id": "c"}},
            "trace_metadata": {
                "mlflow.trace.tokenUsage": (
                    '{"input_tokens": 30, "output_tokens": 12, "total_tokens": 42}'
                )
            },
        }
    ]
    monkeypatch.setattr(ablation.mlflow, "search_traces", lambda run_id: _Frame(rows))
    assert ablation._trace_token_usage("run") == {"c": 42}


def test_private_baseline_loader_ignores_preexisting_import_pollution(monkeypatch, tmp_path):
    polluted = tmp_path / "decision_record.py"
    polluted.write_text("raise RuntimeError('polluted module imported')\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(sys.modules, "decision_record", SimpleNamespace(polluted=True))
    monkeypatch.setitem(sys.modules, "disposition_rules", SimpleNamespace(polluted=True))

    _, decision, _, _ = ablation._local_baseline_functions()
    outcome = decision.deterministic_outcome(
        "coating_warranty",
        {"conforms": True},
        {"covered": False},
        {"approved_amount": 10.0},
        {"is_duplicate": False},
    )

    assert outcome["verdict"] == "DENY"
    assert decision.__name__ == "_ablation_decision_record"
    assert deterministic_adapter("baseline").worker_spec.kind == "deterministic"


def test_per_claim_timeout_is_recorded_as_failure():
    adapter = CandidateAdapter(
        "slow",
        lambda claim: None,
        worker_spec=WorkerSpec("callable", "test_ablation._isolated_slow"),
    )
    adapter.timeout_seconds = 0.01
    report = compare(
        _records()[:1], [adapter, callable_adapter("other", _candidate("APPROVE", "CREDIT"))]
    )
    row = report["per_claim"][0]["candidates"]["slow"]
    assert row["verdict"] is False
    assert "ClaimTimeoutError" in row["error"]


def test_isolated_worker_propagates_traceback():
    adapter = CandidateAdapter(
        "broken",
        lambda claim: None,
        worker_spec=WorkerSpec("callable", "test_ablation._isolated_explode"),
    )
    report = compare(
        _records()[:1], [adapter, callable_adapter("other", _candidate("APPROVE", "CREDIT"))]
    )

    error = report["per_claim"][0]["candidates"]["broken"]["error"]
    assert "Traceback (most recent call last)" in error
    assert "ValueError: bad claim one" in error


def test_isolated_persistence_violation_aborts():
    adapter = CandidateAdapter(
        "writer",
        lambda claim: None,
        worker_spec=WorkerSpec("callable", "test_ablation._isolated_persistence"),
    )
    with pytest.raises(PersistenceViolation, match="blocked write for one"):
        compare(
            _records()[:1],
            [adapter, callable_adapter("other", _candidate("APPROVE", "CREDIT"))],
        )


def test_first_five_failures_abort_and_log_diagnostics(monkeypatch):
    records = [
        {"inputs": {"claim": {"claim_id": str(index)}}, "expectations": {"verdict": "DENY"}}
        for index in range(6)
    ]
    logged = []
    monkeypatch.setattr(ablation.mlflow, "active_run", lambda: object())
    monkeypatch.setattr(
        ablation.mlflow, "log_dict", lambda value, path: logged.append((value, path))
    )
    adapter = callable_adapter("broken", lambda claim: (_ for _ in ()).throw(ValueError("boom")))

    with pytest.raises(ablation.CandidateBatchFailure, match="first 5 rows"):
        ablation._observations(records, adapter)

    assert logged[0][1] == "ablation-errors.json"
    assert len(logged[0][0]["per_claim"]) == 5
    assert "ValueError: boom" in logged[0][0]["per_claim"][0]["error"]


def test_four_failures_then_success_does_not_abort():
    calls = 0

    def recover(claim):
        nonlocal calls
        calls += 1
        if calls <= 4:
            raise ValueError("temporary")
        return _candidate("DENY", "DENY")(claim)

    records = [
        {"inputs": {"claim": {"claim_id": str(index)}}, "expectations": {"verdict": "DENY"}}
        for index in range(5)
    ]
    observations = ablation._observations(records, callable_adapter("recover", recover))
    assert len(observations) == 5
    assert observations[-1]["error"] is None


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def iterrows(self):
        return enumerate(self.rows)


def test_history_selection_round_robins_derived_strata():
    records = []
    for verdict, disposition, count in (("APPROVE", "CREDIT", 8), ("DENY", "DUPLICATE", 2)):
        records.extend(
            {
                "inputs": {"claim": {"claim_id": f"{verdict}-{index}", "claim_type": "x"}},
                "expectations": {"verdict": verdict, "disposition": disposition},
            }
            for index in range(count)
        )
    selected = stratified_sample(records, 4)
    assert [row["expectations"]["verdict"] for row in selected] == [
        "APPROVE",
        "DENY",
        "APPROVE",
        "DENY",
    ]


def test_main_selects_experiment_before_starting_runs(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ablation.py",
            "--dataset",
            "heldout",
            "--candidates",
            "deterministic_baseline,agent@prod",
        ],
    )
    monkeypatch.setattr(
        ablation.mlflow, "set_tracking_uri", lambda uri: events.append(("uri", uri))
    )
    monkeypatch.setattr(
        ablation.mlflow, "set_experiment", lambda name: events.append(("experiment", name))
    )
    monkeypatch.setattr(ablation, "load_records", lambda *args: [])
    monkeypatch.setattr(
        ablation, "resolve_candidate", lambda spec: callable_adapter(spec, lambda claim: {})
    )
    monkeypatch.setattr(ablation, "run", lambda records, candidates: {"candidate_runs": []})

    ablation.main()

    assert events == [
        ("uri", "databricks"),
        ("experiment", "/Shared/claims-adjudication-ablation"),
    ]
    assert '"candidate_runs": []' in capsys.readouterr().out
