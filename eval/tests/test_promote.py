"""Compare + promote decision logic for the @prod alias gate.

These tests are hermetic: ``search_runs`` and the registry client are mocked, so
no live MLflow experiment is queried and — critically — the live @prod alias is
NEVER moved. ``set_registered_model_alias`` is asserted against a mock; a real
call would require a workspace this test never touches.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from promote import (
    EPS,
    MODEL_NAME,
    MONEY_SAFETY_METRICS,
    QUALITY_METRICS,
    evaluate_gate,
    promote_if_beats_prod,
)

# A current @prod baseline: money-safety perfect, verdict/disposition below absolute
# thresholds (mirrors the real v2 evidence) — promotion is therefore relative.
PROD = {
    "verdict_exact_match": 0.94,
    "disposition_exact_match": 0.73,
    "amount_matches_gold": 1.0,
    "no_payable_duplicate": 1.0,
    "amount_matches_authority": 1.0,
    "verdict_matches_eligibility": 1.0,
    "invariant_clean": 1.0,
    "citations_in_resolved_policy": 1.0,
    "citation_recall": 1.0,
    "citation_reciprocal_rank": 0.97,
}
CANDIDATE_WINS = {**PROD, "verdict_exact_match": 0.96, "disposition_exact_match": 0.80}
CANDIDATE_MONEY_FAIL = {**CANDIDATE_WINS, "no_payable_duplicate": 0.99}
CANDIDATE_QUALITY_WORSE = {**PROD, "verdict_exact_match": 0.90}


def _run_df(metrics: dict) -> pd.DataFrame:
    return pd.DataFrame([{f"metrics.{name}/mean": value for name, value in metrics.items()}])


def _search_runs_for(version_metrics: dict[str, dict]):
    """A fake mlflow.search_runs keyed by which version appears in the filter string."""

    def search_runs(experiment_ids, filter_string, order_by, max_results):
        for version, metrics in version_metrics.items():
            if f"'{version}'" in filter_string:
                return _run_df(metrics)
        return pd.DataFrame()

    return search_runs


def _client(prod_version: str) -> MagicMock:
    client = MagicMock()
    client.get_model_version_by_alias.return_value = SimpleNamespace(version=prod_version)
    return client


# --------------------------- pure gate logic --------------------------------- #


def test_money_safety_metrics_are_the_absolute_invariants():
    # Guards the definition: money-safety == the release metrics with a 1.0 threshold.
    assert set(MONEY_SAFETY_METRICS) == {
        "no_payable_duplicate",
        "amount_matches_authority",
        "verdict_matches_eligibility",
        "citations_in_resolved_policy",
    }
    assert QUALITY_METRICS == (
        "verdict_exact_match",
        "disposition_exact_match",
        "amount_matches_gold",
    )


def test_gate_wins_when_safe_and_not_worse():
    gate = evaluate_gate(CANDIDATE_WINS, PROD)
    assert gate["wins"] is True
    assert gate["money_safety_passed"] is True
    assert gate["quality_not_worse"] is True


def test_gate_blocks_when_money_safety_fails():
    gate = evaluate_gate(CANDIDATE_MONEY_FAIL, PROD)
    assert gate["wins"] is False
    assert gate["money_safety_passed"] is False
    assert any("money-safety" in reason for reason in gate["reasons"])


def test_gate_blocks_when_quality_worse_than_prod():
    gate = evaluate_gate(CANDIDATE_QUALITY_WORSE, PROD)
    assert gate["wins"] is False
    assert gate["quality_not_worse"] is False
    assert gate["quality_comparison"]["verdict_exact_match"]["not_worse"] is False


def test_gate_blocks_when_a_compared_metric_is_missing():
    prod_missing = {**PROD}
    del prod_missing["amount_matches_gold"]
    gate = evaluate_gate(CANDIDATE_WINS, prod_missing)
    assert gate["wins"] is False
    assert gate["quality_comparison"]["amount_matches_gold"]["not_worse"] is False


# --------------------------- promotion orchestration ------------------------- #


def test_dry_run_never_moves_the_alias_even_when_candidate_wins():
    client = _client(prod_version="2")
    decision = promote_if_beats_prod(
        "4",
        "exp-1",
        client=client,
        search_runs=_search_runs_for({"2": PROD, "4": CANDIDATE_WINS}),
        dry_run=True,
    )
    assert decision["gate"]["wins"] is True
    assert decision["promoted"] is False
    assert decision["alias_to_version"] is None
    client.set_registered_model_alias.assert_not_called()


def test_promotion_moves_alias_only_when_winning_and_not_dry_run():
    client = _client(prod_version="2")
    decision = promote_if_beats_prod(
        "4",
        "exp-1",
        client=client,
        search_runs=_search_runs_for({"2": PROD, "4": CANDIDATE_WINS}),
        dry_run=False,
    )
    assert decision["promoted"] is True
    assert decision["alias_from_version"] == "2"
    assert decision["alias_to_version"] == "4"
    client.set_registered_model_alias.assert_called_once_with(MODEL_NAME, "prod", "4")


def test_losing_candidate_never_moves_alias_even_without_dry_run():
    client = _client(prod_version="2")
    decision = promote_if_beats_prod(
        "4",
        "exp-1",
        client=client,
        search_runs=_search_runs_for({"2": PROD, "4": CANDIDATE_QUALITY_WORSE}),
        dry_run=False,
    )
    assert decision["promoted"] is False
    assert "did not win" in decision["reason"]
    client.set_registered_model_alias.assert_not_called()


def test_candidate_already_prod_is_a_noop():
    client = _client(prod_version="4")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("search_runs must not run when candidate is already @prod")

    decision = promote_if_beats_prod(
        "4", "exp-1", client=client, search_runs=_fail_if_called, dry_run=False
    )
    assert decision["promoted"] is False
    assert "already @prod" in decision["reason"]
    client.set_registered_model_alias.assert_not_called()


def test_missing_eval_run_raises_actionable_error():
    # Empty search result for the candidate -> refuse (raise), never promote.
    client = _client(prod_version="2")
    with pytest.raises(LookupError, match="no exact-tier eval run"):
        promote_if_beats_prod(
            "4",
            "exp-1",
            client=client,
            search_runs=_search_runs_for({"2": PROD}),  # candidate "4" absent
            dry_run=True,
        )


# ---------------- fail-closed on non-finite / out-of-range metrics ----------- #

INF = float("inf")
NAN = float("nan")


def test_gate_refuses_candidate_nan_money_safety():
    gate = evaluate_gate({**CANDIDATE_WINS, "no_payable_duplicate": NAN}, PROD)
    assert gate["wins"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["candidate_valid"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["passed"] is False


def test_gate_refuses_candidate_inf_money_safety():
    gate = evaluate_gate({**CANDIDATE_WINS, "amount_matches_authority": INF}, PROD)
    assert gate["wins"] is False
    assert gate["money_safety"]["amount_matches_authority"]["passed"] is False


def test_gate_refuses_candidate_out_of_range_above_one_money_safety():
    gate = evaluate_gate({**CANDIDATE_WINS, "no_payable_duplicate": 1.5}, PROD)
    assert gate["wins"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["passed"] is False


def test_money_safety_threshold_is_strict_no_epsilon_slack():
    # A value a hair below the 1.0 threshold must FAIL — money-safety is pass/fail, so
    # the EPS tolerance used for the relative quality compare must not apply here.
    just_below = 1.0 - EPS / 2
    assert just_below < 1.0
    gate = evaluate_gate({**CANDIDATE_WINS, "no_payable_duplicate": just_below}, PROD)
    assert gate["wins"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["candidate_valid"] is True
    assert gate["money_safety"]["no_payable_duplicate"]["candidate_passed"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["passed"] is False


def test_money_safety_exact_threshold_still_passes():
    # Exactly at threshold (1.0) must still pass — strictness rejects only below.
    gate = evaluate_gate(CANDIDATE_WINS, PROD)
    assert gate["money_safety"]["no_payable_duplicate"]["passed"] is True
    assert gate["wins"] is True


def test_gate_refuses_missing_money_safety_metric():
    bad = {**CANDIDATE_WINS}
    del bad["amount_matches_authority"]
    gate = evaluate_gate(bad, PROD)
    assert gate["wins"] is False
    assert gate["money_safety"]["amount_matches_authority"]["passed"] is False


def test_gate_refuses_candidate_inf_quality():
    gate = evaluate_gate({**CANDIDATE_WINS, "verdict_exact_match": INF}, PROD)
    assert gate["wins"] is False
    assert gate["quality_comparison"]["verdict_exact_match"]["not_worse"] is False


def test_gate_refuses_candidate_negative_out_of_range_quality():
    gate = evaluate_gate({**CANDIDATE_WINS, "disposition_exact_match": -0.1}, PROD)
    assert gate["wins"] is False
    assert gate["quality_comparison"]["disposition_exact_match"]["not_worse"] is False


def test_gate_refuses_prod_nan_quality():
    gate = evaluate_gate(CANDIDATE_WINS, {**PROD, "verdict_exact_match": NAN})
    assert gate["wins"] is False
    assert gate["quality_comparison"]["verdict_exact_match"]["prod_valid"] is False
    assert gate["quality_comparison"]["verdict_exact_match"]["not_worse"] is False


def test_gate_refuses_prod_inf_quality():
    gate = evaluate_gate(CANDIDATE_WINS, {**PROD, "amount_matches_gold": INF})
    assert gate["wins"] is False
    assert gate["quality_comparison"]["amount_matches_gold"]["not_worse"] is False


def test_promotion_refuses_to_move_alias_on_nonfinite_metric_even_without_dry_run():
    # A +inf candidate metric read from the run must never move the live alias.
    client = _client(prod_version="2")
    decision = promote_if_beats_prod(
        "4",
        "exp-1",
        client=client,
        search_runs=_search_runs_for(
            {"2": PROD, "4": {**CANDIDATE_WINS, "no_payable_duplicate": INF}}
        ),
        dry_run=False,
    )
    assert decision["promoted"] is False
    assert decision["gate"]["wins"] is False
    client.set_registered_model_alias.assert_not_called()


# ---------- money-safety is enforced on PROD too, not only the candidate ------ #


def test_gate_refuses_when_prod_money_safety_metric_is_invalid():
    # Prod safety metric is NaN — unverifiable @prod safety must block promotion.
    gate = evaluate_gate(CANDIDATE_WINS, {**PROD, "no_payable_duplicate": NAN})
    assert gate["wins"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["prod_valid"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["prod_passed"] is False
    assert gate["money_safety"]["no_payable_duplicate"]["passed"] is False


def test_gate_refuses_when_prod_money_safety_metric_missing():
    prod_missing = {**PROD}
    del prod_missing["amount_matches_authority"]
    gate = evaluate_gate(CANDIDATE_WINS, prod_missing)
    assert gate["wins"] is False
    assert gate["money_safety"]["amount_matches_authority"]["prod_passed"] is False


def test_gate_refuses_when_prod_money_safety_below_threshold():
    gate = evaluate_gate(CANDIDATE_WINS, {**PROD, "amount_matches_authority": 0.9})
    assert gate["wins"] is False
    assert gate["money_safety"]["amount_matches_authority"]["prod_passed"] is False


def test_promotion_refuses_when_prod_money_safety_invalid_even_without_dry_run():
    # Non-dry-run orchestration: an invalid PROD money-safety metric must NOT move the alias.
    client = _client(prod_version="2")
    decision = promote_if_beats_prod(
        "4",
        "exp-1",
        client=client,
        search_runs=_search_runs_for(
            {"2": {**PROD, "amount_matches_authority": INF}, "4": CANDIDATE_WINS}
        ),
        dry_run=False,
    )
    assert decision["promoted"] is False
    assert decision["gate"]["wins"] is False
    client.set_registered_model_alias.assert_not_called()
