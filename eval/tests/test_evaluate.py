"""The evaluated model URI must provably pin the candidate version (B1).

Without this guard someone could evaluate CURRENT @prod, tag the run as candidate
vN, and then promote an unevaluated vN using prod's metrics. These tests are
hermetic: alias resolution uses an injected fake client, so no workspace is
touched.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluate
from evaluate import MODEL_NAME, pinned_candidate_model_uri


def _client_returning(version: str):
    return lambda: SimpleNamespace(
        get_model_version_by_alias=lambda name, alias: SimpleNamespace(version=version)
    )


def test_derives_pinned_uri_when_model_uri_omitted():
    assert pinned_candidate_model_uri(None, "4", MODEL_NAME) == f"models:/{MODEL_NAME}/4"


def test_explicit_version_uri_matching_candidate_is_accepted():
    uri = f"models:/{MODEL_NAME}/4"
    assert pinned_candidate_model_uri(uri, "4", MODEL_NAME) == uri


def test_explicit_version_uri_mismatch_is_rejected():
    # This is the loophole: evaluating v3 while claiming candidate v4.
    with pytest.raises(ValueError, match="resolves to version 3, but --candidate-version is 4"):
        pinned_candidate_model_uri(f"models:/{MODEL_NAME}/3", "4", MODEL_NAME)


def test_prod_alias_uri_is_rejected_when_prod_is_not_the_candidate():
    # models:/<name>@prod resolving to v2 while the candidate is v4 must be refused.
    with pytest.raises(ValueError, match="resolves to version 2, but --candidate-version is 4"):
        pinned_candidate_model_uri(
            f"models:/{MODEL_NAME}@prod",
            "4",
            MODEL_NAME,
            client_factory=_client_returning("2"),
        )


def test_prod_alias_uri_accepted_when_prod_equals_candidate():
    uri = f"models:/{MODEL_NAME}@prod"
    assert (
        pinned_candidate_model_uri(uri, "4", MODEL_NAME, client_factory=_client_returning("4"))
        == uri
    )


def test_wrong_model_name_is_rejected():
    with pytest.raises(ValueError, match="not the agent model"):
        pinned_candidate_model_uri("models:/some.other.model/4", "4", MODEL_NAME)


def test_non_uc_model_uri_is_rejected():
    with pytest.raises(ValueError, match="must be a UC model URI"):
        pinned_candidate_model_uri("runs:/abc123/agent", "4", MODEL_NAME)


def test_candidate_version_is_required(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["evaluate.py"])
    with pytest.raises(SystemExit) as exc:
        evaluate.main()
    assert exc.value.code == 2
    error = capsys.readouterr().err
    assert "--candidate-version" in error
    assert "required" in error.lower()


def test_evaluate_never_reads_prior_json_evidence():
    source = Path(evaluate.__file__).read_text()
    assert "_read_evidence" not in source
    assert ".read_text(" not in source
