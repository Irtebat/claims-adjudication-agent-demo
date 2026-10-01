from heldout import HELDOUT_TABLE, SCENARIO_COUNTS, build_heldout_records, persist


def _reference_rows(count=20):
    return [
        {
            "claim_id": f"CLM-{index}",
            "coil_id": f"COIL-{index}",
            "customer_id": f"CUSTOMER-{index}",
            "claim_type": "coating_warranty",
            "environment": "inland",
            "installation": "ventilated",
            "defect_code": "MECH",
            "defect_narrative": "original",
            "gold_verdict": "APPROVE",
            "gold_disposition": "CREDIT",
        }
        for index in range(count)
    ]


def test_exact_stratification_real_reference_keys_and_no_leakage():
    records, metadata = build_heldout_records(_reference_rows())
    assert len(records) == 100
    assert metadata["stratification"] == SCENARIO_COUNTS
    assert metadata["labels_location"] == "expectations_only"
    assert {record["inputs"]["claim"]["coil_id"] for record in records} <= {
        f"COIL-{index}" for index in range(20)
    }
    for record in records:
        claim = record["inputs"]["claim"]
        assert "gold_verdict" not in claim
        assert "verdict" not in claim
        assert record["expectations"]["policy_basis"]


def test_writer_is_forced_to_isolated_eval_table():
    records, _ = build_heldout_records(_reference_rows())
    calls = []
    persist(records, lambda name, values: calls.append((name, len(values))))
    assert calls == [(HELDOUT_TABLE, 100)]
