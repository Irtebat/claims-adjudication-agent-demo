from types import SimpleNamespace

import pytest

from ablation import (
    NA,
    callable_adapter,
    compare,
    deterministic_baseline,
    endpoint_adapter,
    model_adapter,
    standardize_output,
    stratified_sample,
)


def _candidate(verdict, disposition, *, citations=None, violations=None, tokens=0):
    def invoke(claim):
        return {
            "custom_outputs": {
                "recommendation": {
                    "recommended_verdict": verdict,
                    "recommended_disposition": disposition,
                },
                "cited_clause_ids": citations,
                "invariant_violations": violations or [],
                "usage": {"total_tokens": tokens},
            }
        }

    return invoke


def _records():
    return [
        {
            "inputs": {"claim": {"claim_id": "one"}},
            "expectations": {
                "verdict": "APPROVE",
                "disposition_class": "APPROVE",
                "approval_subchoice": "CREDIT",
                "oracle_clause_ids": ["a"],
            },
        },
        {
            "inputs": {"claim": {"claim_id": "two"}},
            "expectations": {
                "verdict": "DENY",
                "disposition_class": "DENY",
                "approval_subchoice": None,
                "oracle_clause_ids": ["a"],
            },
        },
    ]


def test_paired_report_splits_disposition_and_uses_na():
    baseline = callable_adapter("baseline", _candidate("APPROVE", "CREDIT"))
    challenger = callable_adapter(
        "challenger", _candidate("DENY", "DENY", citations=["a"], violations=["corrected"])
    )
    report = compare(_records(), [baseline, challenger])
    assert report["paired"]["verdict"] == {
        "delta": 0.0,
        "both_correct": 0,
        "baseline_only_correct": 1,
        "challenger_only_correct": 1,
        "both_wrong": 0,
    }
    assert report["per_claim"][0]["candidates"]["baseline"]["citation"] == NA
    assert report["per_claim"][0]["candidates"]["baseline"]["judge"] == NA
    assert report["summary"]["challenger"]["invariant_correction_rate"] == 1.0


def test_model_adapter_enforces_persist_false():
    class Model:
        def predict(self, request):
            assert request["custom_inputs"]["persist"] is False
            return {"custom_outputs": {"write_result": {"persisted": True}}}

    adapter = model_adapter("model", "models:/x@prod", loader=lambda _: Model())
    with pytest.raises(RuntimeError, match="persistence invariant"):
        adapter.predict({"claim_id": "c"})


def test_endpoint_adapter_sends_persist_false():
    class Endpoints:
        def query(self, **kwargs):
            assert kwargs["name"] == "claims"
            assert kwargs["custom_inputs"]["persist"] is False
            return {"recommendation": {"recommended_verdict": "DENY"}}

    adapter = endpoint_adapter("endpoint", "claims", SimpleNamespace(serving_endpoints=Endpoints()))
    output, _ = adapter.predict({"claim_id": "c"})
    assert output["verdict"] == "DENY"


def test_standard_output_requires_dict():
    with pytest.raises(TypeError, match="return a dict"):
        standardize_output("bad")


def test_missing_parallel_ruleset_has_actionable_import_error(monkeypatch):
    monkeypatch.setattr("ablation.importlib.import_module", lambda _: SimpleNamespace())
    with pytest.raises(ImportError, match="land branch deterministic-ruleset first"):
        deterministic_baseline({"claim_id": "c"})


def test_history_selection_round_robins_strata():
    records = []
    for verdict, count in (("APPROVE", 8), ("DENY", 2)):
        records.extend(
            {
                "inputs": {"claim": {"claim_id": f"{verdict}-{index}", "claim_type": "x"}},
                "expectations": {"verdict": verdict, "disposition": verdict},
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
