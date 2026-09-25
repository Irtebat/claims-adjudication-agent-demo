from build_dataset import LEAN_CLAIM_COLUMNS, make_record, stable_holdout


def _row(index, category):
    verdict = "APPROVE" if category in ("clean", "over_claim") else "DENY"
    disposition = (
        "DUPLICATE" if category == "duplicate" else ("CREDIT" if verdict == "APPROVE" else "DENY")
    )
    rationale = {
        "clean": "Synthetic adjudication: clean",
        "over_claim": "Synthetic adjudication: over_claim",
        "in_spec": "Synthetic adjudication: in_spec_should_deny",
        "warranty": "Synthetic adjudication: out_of_warranty_or_environment_excluded",
        "duplicate": "Synthetic adjudication: duplicate",
    }[category]
    row = {key: f"v-{index}" for key in LEAN_CLAIM_COLUMNS}
    row.update(
        claim_id=f"claim-{index}",
        coil_id=f"coil-{index}",
        claim_type="coating_warranty"
        if category in ("clean", "warranty", "duplicate")
        else "material_nonconformance",
        gold_verdict=verdict,
        gold_disposition=disposition,
        gold_approved_amount="1.00" if verdict == "APPROVE" else "0.00",
        gold_cited_clause_ids=["a"],
        label_provenance=rationale,
        duplicate_of_claim_id=None,
        finalized_at=f"2026-01-{(index % 28) + 1:02d}",
    )
    return row


def test_stable_holdout_has_rare_strata_and_no_input_labels():
    categories = ["clean", "over_claim", "in_spec", "warranty", "duplicate"]
    selected = stable_holdout([_row(index, categories[index % 5]) for index in range(100)])
    record = make_record(selected[0], {"oracle_clause_ids": ["a"]})
    assert len(selected) == 75
    assert set(record["inputs"]) == {"claim"}
    assert "verdict" not in record["inputs"]["claim"]


def test_duplicate_entity_stays_together():
    categories = ["clean", "over_claim", "in_spec", "warranty", "duplicate"]
    rows = [_row(index, categories[index % 5]) for index in range(100)]
    rows[4]["duplicate_of_claim_id"] = rows[0]["claim_id"]
    ids = {row["claim_id"] for row in stable_holdout(rows)}
    assert (rows[0]["claim_id"] in ids) == (rows[4]["claim_id"] in ids)
